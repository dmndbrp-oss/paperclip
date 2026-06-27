export const meta = {
  name: 'improve-loop-hybrid',
  description: 'Hybrid bounded code-quality loop: PLAN+CODE on local Qwen3.6 (Ollama, ~zero cloud tokens for generation), REVIEW adversarial gate stays on Claude. Guardrails identical to cloud engine: no merge, worktree isolation, bounded, LES v1.0.',
  phases: [
    { title: 'Preflight',  detail: 'Verify Ollama reachable and local model loaded; fail fast if not' },
    { title: 'Plan',       detail: 'Qwen3.6 (local) identifies highest-value quality improvement via Ollama /api/chat' },
    { title: 'Code',       detail: 'Qwen3.6 (local) generates edit in isolated worktree; Claude bridge applies + verifies' },
    { title: 'Review',     detail: 'Adversarial 3-lens Claude review (unchanged): real improvement, no regression, verify green' },
    { title: 'Synthesize', detail: 'Emit accepted/rejected/skipped; NO merge or PR-create step' },
  ],
}

// ---- Configure via args -------------------------------------------------------
// target:          glob/path/subsystem to improve (e.g. 'pilot-artifacts/validator.py')
// maxIterations:   hard iteration cap (default 8)
// dryStreakToStop: consecutive non-accepted rounds before stopping (default 2)
// budgetFloor:     stop when budget.remaining() <= this many tokens (default 20000)
// ollamaUrl:       Ollama base URL (default 'http://127.0.0.1:11434')
// localModel:      local model tag for PLAN+CODE generation (default 'qwen3.6:latest')
//
// KNOWN CAVEAT — Qwen3.6 serializes on Ollama:
//   qwen3.6 uses the qwen35moe architecture. Ollama force-caps qwen35moe to 1
//   slot regardless of OLLAMA_NUM_PARALLEL (sched.go:423, upstream #14510/#4165).
//   PLAN and CODE calls always serialize (one at a time). This is expected —
//   it is NOT a hang. The REVIEW panel (Claude) still parallelizes. Wall-clock
//   is slower than the cloud engine; token cost is dramatically lower.
// -------------------------------------------------------------------------------
const TARGET          = args?.target          ?? '.'
const MAX_ITER        = args?.maxIterations    ?? 8
const DRY_STREAK_STOP = args?.dryStreakToStop  ?? 2
const BUDGET_FLOOR    = args?.budgetFloor      ?? 20000
const OLLAMA_URL      = args?.ollamaUrl        ?? 'http://127.0.0.1:11434'
const LOCAL_MODEL     = args?.localModel       ?? 'qwen3.6:latest'

// ---- Schemas ------------------------------------------------------------------

const PLAN_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['hasImprovement', 'title', 'rationale', 'files', 'kind', 'verifyCmd'],
  properties: {
    hasImprovement:      { type: 'boolean' },
    title:               { type: 'string' },
    rationale:           { type: 'string' },
    files:               { type: 'array', items: { type: 'string' } },
    kind:                { type: 'string', enum: ['reuse', 'simplify', 'efficiency'] },
    verifyCmd:           { type: 'string' },
    noImprovementReason: { type: 'string' },
    localModelUsed:      { type: 'string' },
  },
}

const CODE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['applied', 'diff', 'branch', 'verifyOutput', 'verifyPassed'],
  properties: {
    applied:        { type: 'boolean' },
    diff:           { type: 'string' },
    branch:         { type: 'string' },
    verifyOutput:   { type: 'string' },
    verifyPassed:   { type: 'boolean' },
    localModelUsed: { type: 'string' },
  },
}

const REVIEW_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['accept', 'confidence', 'reasoning'],
  properties: {
    accept:     { type: 'boolean' },
    confidence: { type: 'number' },
    reasoning:  { type: 'string' },
  },
}

const PREFLIGHT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['ok', 'message', 'modelsLoaded'],
  properties: {
    ok:           { type: 'boolean' },
    message:      { type: 'string' },
    modelsLoaded: { type: 'array', items: { type: 'string' } },
  },
}

// ---- State --------------------------------------------------------------------

const accepted = []
const rejected = []
const seen     = new Set()
let dryStreak  = 0
let iter       = 0

// ---- Preflight ----------------------------------------------------------------
// Fail fast if Ollama is unreachable or the local model is not loaded.
// Silently falling back to cloud would defeat the cost goal and surprise the user.

phase('Preflight')

const preflight = await agent(
  `You are a preflight check agent for the improve-loop-hybrid skill.\n\n` +
  `Your ONLY task: verify Ollama is reachable at ${OLLAMA_URL} and that the model ` +
  `"${LOCAL_MODEL}" is available (either loaded or pullable). Do NOT call any model or start work.\n\n` +
  `Steps:\n` +
  `1. Run: curl -sf ${OLLAMA_URL}/api/ps\n` +
  `   Parse the JSON and list loaded model names.\n` +
  `2. Run: curl -sf ${OLLAMA_URL}/api/tags\n` +
  `   Parse JSON, check if "${LOCAL_MODEL}" appears in any model name.\n` +
  `3. Set ok=true if Ollama responded to both requests and "${LOCAL_MODEL}" is present ` +
  `(either loaded or available in tags). Set ok=false if Ollama is unreachable OR the model ` +
  `is absent.\n` +
  `4. If ok=false, set message to a clear explanation of what failed so the caller can act.\n\n` +
  `Return: { ok: boolean, message: string, modelsLoaded: string[] }`,
  { label: 'preflight', phase: 'Preflight', schema: PREFLIGHT_SCHEMA },
)

if (!preflight || !preflight.ok) {
  const msg = preflight?.message ?? 'Preflight check returned no result'
  log(`PREFLIGHT FAILED: ${msg}`)
  log(`Local model: ${LOCAL_MODEL} @ ${OLLAMA_URL}`)
  log(`Aborting hybrid run. Fix Ollama/model then retry. Do NOT fall back to cloud — that defeats the cost goal.`)
  return {
    target: TARGET,
    stopReason: `preflight-failed: ${msg}`,
    rounds: 0,
    accepted: [],
    rejected: [],
    noAutoMerge: 'No changes made — preflight failed.',
    hybridRuntime: { ollamaUrl: OLLAMA_URL, localModel: LOCAL_MODEL, preflightOk: false },
  }
}

log(`Preflight OK — ${LOCAL_MODEL} available at ${OLLAMA_URL}. Loaded: [${(preflight.modelsLoaded ?? []).join(', ')}]`)
log(`NOTE: Qwen3.6 (qwen35moe arch) serializes on Ollama — PLAN/CODE calls are sequential (not a hang).`)

// ---- Loop-until-dry -----------------------------------------------------------

while (iter < MAX_ITER && dryStreak < DRY_STREAK_STOP) {

  // Budget guard (LES §1 part 4 — termination path)
  if (budget.total && budget.remaining() <= BUDGET_FLOOR) {
    log(`Budget floor reached (${budget.remaining()} tokens remaining <= floor ${BUDGET_FLOOR}). Stopping.`)
    break
  }

  iter++
  log(`--- Round ${iter}/${MAX_ITER} | dry streak ${dryStreak}/${DRY_STREAK_STOP} | seen: [${[...seen].join(', ') || 'none'}] ---`)

  // ---- PLAN (LOCAL — Qwen3.6 via Ollama) -------------------------------------
  phase('Plan')

  const seenJson = JSON.stringify([...seen])

  const plannerPromptForLocal =
    `You are a code-quality planner. Return ONLY valid JSON. No markdown fences. No explanations. No thinking tags.\\n\\n` +
    `Required JSON schema (all fields required unless noted):\\n` +
    `{\\n` +
    `  "hasImprovement": boolean,\\n` +
    `  "title": string (short unique name for this improvement),\\n` +
    `  "rationale": string (why this is a quality gain),\\n` +
    `  "files": [string] (relative paths of files to change),\\n` +
    `  "kind": "reuse" | "simplify" | "efficiency",\\n` +
    `  "verifyCmd": string (shell command to verify correctness),\\n` +
    `  "noImprovementReason": string (only when hasImprovement=false)\\n` +
    `}\\n\\n` +
    `Quality kinds:\\n` +
    `  reuse: extract duplicated logic into a shared helper\\n` +
    `  simplify: remove unnecessary complexity, dead code, over-engineered constructs\\n` +
    `  efficiency: improve performance of a hot path without changing behavior\\n\\n` +
    `Rules:\\n` +
    `  1. Quality-ONLY. Do NOT fix bugs or change observable behavior.\\n` +
    `  2. The improvement must NOT be in this already-seen set: ${seenJson}\\n` +
    `  3. If no meaningful quality improvement exists, set hasImprovement=false.\\n` +
    `  4. verifyCmd: DISCOVERY RULE — check for a test suite first:\\n` +
    `     - Python: test_*.py / *_test.py in the same dir or sibling test/ dir → use pytest.\\n` +
    `     - JS/TS: *.test.js / *.spec.js or package.json "test" script → use npm test.\\n` +
    `     If a test suite exists, verifyCmd MUST run it:\\n` +
    `       python -m pytest pilot-artifacts/ -x -q\\n` +
    `       python -m unittest discover -s pilot-artifacts -p 'test_*.py' -q\\n` +
    `     Use syntax-only (ast.parse, etc.) ONLY when NO test file exists.\\n` +
    `     NEVER prefer ast.parse over a real test suite — reviewers will reject it.\\n\\n` +
    `Read the source file at "${TARGET}" and identify the single highest-value quality improvement.`

  const plan = await agent(
    `You are a LOCAL-MODEL DISPATCH BRIDGE for the improve-loop-hybrid skill.\n` +
    `Your job: call Qwen3.6 on Ollama at ${OLLAMA_URL} to produce a code-quality PLAN, ` +
    `validate the JSON response, and return the structured plan.\n\n` +
    `== Steps ==\n\n` +
    `1. First, read the source file(s) at "${TARGET}" so you can pass their content to Qwen3.6.\n\n` +
    `2. Build the Ollama request. Call:\n` +
    `   curl -sf -X POST ${OLLAMA_URL}/api/chat \\\n` +
    `     -H 'Content-Type: application/json' \\\n` +
    `     -d '{"model":"${LOCAL_MODEL}","stream":false,"think":false,"options":{"temperature":0.2},' +\n` +
    `         '"messages":[{"role":"user","content":"<PLANNER_PROMPT>"}]}'\n\n` +
    `   Where <PLANNER_PROMPT> is this exact text (file content appended at the end):\n` +
    `   ---BEGIN PLANNER PROMPT---\n` +
    `   ${plannerPromptForLocal}\n` +
    `   ---END PLANNER PROMPT---\n\n` +
    `   Include the FULL file content of "${TARGET}" appended after the rules block.\n\n` +
    `3. Parse the response:\n` +
    `   - Extract the "content" field from the Ollama response JSON.\n` +
    `   - Strip any markdown fences (triple backtick blocks) if present.\n` +
    `   - Strip any <think>...</think> tags if present.\n` +
    `   - Try to parse the content as JSON.\n\n` +
    `4. Validate the parsed JSON has all required fields:\n` +
    `   hasImprovement (boolean), title (string), rationale (string), files (array),\n` +
    `   kind (one of: reuse/simplify/efficiency), verifyCmd (string).\n\n` +
    `5. If JSON is malformed or fields are missing: ONE repair attempt only.\n` +
    `   Call Ollama again with:\n` +
    `   "The following JSON is invalid or incomplete. Fix it and return ONLY valid JSON:\n<original response>"\n` +
    `   If the repair also fails, treat as no improvement (hasImprovement: false).\n\n` +
    `6. Add the field "localModelUsed": "${LOCAL_MODEL}" to the returned object.\n\n` +
    `7. Return the validated plan object via StructuredOutput.\n\n` +
    `IMPORTANT: Never crash or throw on bad Ollama output. Malformed → hasImprovement:false.`,
    { label: `plan-local:r${iter}`, phase: 'Plan', schema: PLAN_SCHEMA },
  )

  if (!plan || !plan.hasImprovement) {
    const reason = plan?.noImprovementReason ?? 'local model returned no improvement (malformed or empty)'
    log(`Round ${iter} PLAN [local:${LOCAL_MODEL}]: no improvement — "${reason}". Dry streak → ${dryStreak + 1}.`)
    dryStreak++
    continue
  }

  seen.add(plan.title)
  log(`Round ${iter} PLAN [local:${LOCAL_MODEL}]: "${plan.title}" (${plan.kind}) — ${plan.rationale.slice(0, 120)}`)

  // ---- CODE (LOCAL generation + Claude apply in worktree) --------------------
  phase('Code')

  const codePromptForLocal =
    `You are a code editor. Return ONLY valid JSON. No markdown fences. No explanations. No thinking tags.\\n\\n` +
    `Required JSON schema:\\n` +
    `{\\n` +
    `  "can_apply": boolean,\\n` +
    `  "edits": [\\n` +
    `    {\\n` +
    `      "file": string (relative file path),\\n` +
    `      "old": string (EXACT substring to replace — must match file content literally),\\n` +
    `      "new": string (exact replacement)\\n` +
    `    }\\n` +
    `  ],\\n` +
    `  "explanation": string (one sentence: what changed and why)\\n` +
    `}\\n\\n` +
    `Apply this SINGLE quality improvement:\\n` +
    `  Title:     ${plan.title}\\n` +
    `  Kind:      ${plan.kind}\\n` +
    `  Rationale: ${plan.rationale}\\n` +
    `  Files:     ${plan.files.length > 0 ? plan.files.join(', ') : TARGET}\\n\\n` +
    `Rules:\\n` +
    `  1. Apply ONLY the described quality change. Do NOT fix bugs or change observable behavior.\\n` +
    `  2. Make the MINIMAL diff — smallest change that delivers the improvement.\\n` +
    `  3. The "old" string MUST appear literally in the file content shown below.\\n` +
    `  4. If you cannot apply cleanly, set can_apply=false and explain in explanation.\\n\\n` +
    `File content is appended below. Output ONLY the JSON object.`

  const code = await agent(
    `You are a LOCAL-MODEL CODE DISPATCH BRIDGE for the improve-loop-hybrid skill.\n` +
    `Your job: call Qwen3.6 on Ollama to generate code edits, apply them in this worktree, ` +
    `run verifyCmd, and return the CODE result.\n\n` +
    `== Steps ==\n\n` +
    `1. Read the target file(s) at: ${plan.files.length > 0 ? plan.files.join(', ') : TARGET}\n` +
    `   Capture the full content of each file.\n\n` +
    `2. Build the Ollama code-editing request. Call:\n` +
    `   curl -sf -X POST ${OLLAMA_URL}/api/chat \\\n` +
    `     -H 'Content-Type: application/json' \\\n` +
    `     -d '{"model":"${LOCAL_MODEL}","stream":false,"think":false,"options":{"temperature":0.1},' +\n` +
    `         '"messages":[{"role":"user","content":"<CODE_PROMPT_WITH_FILE>"}]}'\n\n` +
    `   Where <CODE_PROMPT_WITH_FILE> is this prompt followed by the FULL file content:\n` +
    `   ---BEGIN CODE PROMPT---\n` +
    `   ${codePromptForLocal}\n` +
    `   ---END CODE PROMPT---\n\n` +
    `3. Parse the Ollama response:\n` +
    `   - Extract "content" field.\n` +
    `   - Strip markdown fences and <think> tags.\n` +
    `   - Parse as JSON to get {can_apply, edits, explanation}.\n` +
    `   - If malformed: ONE repair attempt. If still fails → applied:false.\n\n` +
    `4. Apply the edits:\n` +
    `   - If can_apply=false: set applied=false, diff=explanation, verifyPassed=false.\n` +
    `   - If can_apply=true: for each edit in edits[], use your Edit tool to replace\n` +
    `     edit.old with edit.new in edit.file. If any edit.old string is not found in the\n` +
    `     file, set applied=false.\n` +
    `   - If all edits applied: run the verifyCmd: ${plan.verifyCmd}\n` +
    `     Capture stdout+stderr as verifyOutput. verifyPassed = (exit code 0).\n` +
    `   - Run: git diff HEAD\n` +
    `     to capture the diff.\n` +
    `   - Run: git branch --show-current\n` +
    `     to capture the branch name.\n\n` +
    `5. Add "localModelUsed": "${LOCAL_MODEL}" to the returned object.\n\n` +
    `6. Return via StructuredOutput: {applied, diff, branch, verifyOutput, verifyPassed, localModelUsed}.\n\n` +
    `IMPORTANT: Never crash on bad Ollama output. Malformed JSON → applied:false.`,
    { label: `code-local:r${iter}`, phase: 'Code', schema: CODE_SCHEMA, isolation: 'worktree' },
  )

  if (!code || !code.applied) {
    const reason = code?.diff ?? 'CODE bridge returned no result'
    log(`Round ${iter} CODE [local:${LOCAL_MODEL}]: apply failed — "${reason.slice(0, 200)}". Dry streak → ${dryStreak + 1}.`)
    rejected.push({ title: plan.title, kind: plan.kind, reason: `apply-failed: ${reason.slice(0, 300)}`, branch: null, iter })
    dryStreak++
    continue
  }

  if (!code.verifyPassed) {
    log(`Round ${iter} CODE [local:${LOCAL_MODEL}]: verifyCmd FAILED. Discarding. Dry streak → ${dryStreak + 1}.`)
    log(`  Verify output: ${code.verifyOutput.slice(0, 300)}`)
    rejected.push({ title: plan.title, kind: plan.kind, reason: `verify-failed: ${code.verifyOutput.slice(0, 300)}`, branch: code.branch, iter })
    dryStreak++
    continue
  }

  log(`Round ${iter} CODE [local:${LOCAL_MODEL}]: applied on branch "${code.branch}", verify PASSED.`)

  // ---- REVIEW (CLOUD — Claude, UNCHANGED) ------------------------------------
  // Three adversarial Claude skeptics. Default-to-refute (accept:false on uncertainty).
  // This is the non-negotiable safety gate — runs on Claude regardless of local runtime.
  // Each reviewer has a DISTINCT lens to catch different failure modes.
  phase('Review')

  const reviewContext =
    `[Hybrid runtime: PLAN+CODE ran on ${LOCAL_MODEL} (local Ollama). REVIEW on Claude.]\n` +
    `Improvement: "${plan.title}" (${plan.kind})\n` +
    `Rationale: ${plan.rationale}\n` +
    `Files: ${plan.files.join(', ')}\n` +
    `VerifyCmd: ${plan.verifyCmd}\n` +
    `VerifyPassed: ${code.verifyPassed}\n` +
    `VerifyOutput:\n${code.verifyOutput.slice(0, 500)}\n` +
    `Diff:\n${code.diff.slice(0, 3000)}`

  const LENSES = [
    {
      key: 'real-improvement',
      prompt:
        `Lens — REAL IMPROVEMENT: is this a genuine quality gain (${plan.kind}), not churn?\n` +
        `You are a skeptic. Set accept:false by default.\n` +
        `Accept ONLY if you can state a concrete, measurable benefit (less duplication, shorter ` +
        `logic path, faster execution). Cosmetic renames and formatting-only diffs are NOT ` +
        `quality improvements. If in doubt, reject.\n` +
        `NOTE: this change was generated by a local model (${LOCAL_MODEL}). Apply the same ` +
        `rigorous standard as you would for any code change — local origin is not a reason to ` +
        `be more lenient.`,
    },
    {
      key: 'no-regression',
      prompt:
        `Lens — NO REGRESSION: does this change preserve ALL observable behavior?\n` +
        `You are an adversarial tester. Set accept:false by default.\n` +
        `Look for: changed return values, dropped error paths, reordered side effects, ` +
        `removed capabilities, subtle semantic shifts. Even a 1-line change can break behavior. ` +
        `If ANY behavior changed (even "for the better"), set accept:false and explain.\n` +
        `NOTE: this change was generated by a local model (${LOCAL_MODEL}). Local models can ` +
        `produce plausible-looking but subtly incorrect edits — be especially vigilant.`,
    },
    {
      key: 'verify-green',
      prompt:
        `Lens — VERIFY GREEN: did the verification command actually pass meaningfully?\n` +
        `You are a CI auditor. Set accept:false by default.\n` +
        `Check: (1) verifyPassed=true, (2) the verifyCmd is meaningful — NOT "echo ok", ` +
        `NOT a syntax-only check (ast.parse, eslint --parser-options, tsc --noEmit alone), ` +
        `NOT trivially always-succeeds. If a test file (test_*.py, *.test.js, etc.) exists ` +
        `for the target and the planner used ast.parse or similar instead, REJECT — the planner ` +
        `should have run the suite. (3) the verifyOutput must not contain hidden failures or ` +
        `warnings that show the command passed for the wrong reason. A verify that always-passes ` +
        `regardless of code correctness is as bad as a failing verify. Reject if not credible.\n` +
        `NOTE: this change was generated by a local model (${LOCAL_MODEL}). Local models sometimes ` +
        `pick trivial verify commands to avoid test failures — be especially vigilant about weak verifyCmd.`,
    },
  ]

  const votes = await parallel(LENSES.map(lens => () =>
    agent(
      `You are an adversarial code reviewer. Default: accept:false.\n\n` +
      reviewContext + `\n\n` +
      lens.prompt,
      { label: `review:${lens.key}:r${iter}`, phase: 'Review', schema: REVIEW_SCHEMA },
    )
  ))

  const validVotes  = votes.filter(Boolean)
  const acceptCount = validVotes.filter(v => v.accept).length
  const majority    = acceptCount > validVotes.length / 2

  if (majority && code.verifyPassed) {
    log(`Round ${iter} REVIEW [Claude]: ACCEPTED (${acceptCount}/${validVotes.length} votes). Branch: "${code.branch}".`)
    accepted.push({
      title:       plan.title,
      kind:        plan.kind,
      rationale:   plan.rationale,
      files:       plan.files,
      branch:      code.branch,
      iter,
      verifyCmd:   plan.verifyCmd,
      diffSummary: `${code.diff.split('\n').length} diff lines`,
      votes:       validVotes.map(v => ({ accept: v.accept, confidence: v.confidence, reasoning: v.reasoning.slice(0, 120) })),
      localModel:  LOCAL_MODEL,
    })
    dryStreak = 0
  } else {
    const reasons = validVotes.filter(v => !v.accept).map((v, i) => `[${LENSES[i]?.key ?? i}] ${v.reasoning.slice(0, 120)}`).join('; ')
    log(`Round ${iter} REVIEW [Claude]: REJECTED (${acceptCount}/${validVotes.length} votes). Reason: ${reasons.slice(0, 300)}`)
    rejected.push({
      title:  plan.title, kind: plan.kind,
      reason: `review-rejected (${acceptCount}/${validVotes.length}): ${reasons.slice(0, 400)}`,
      branch: code.branch, iter,
    })
    dryStreak++
  }
}

// ---- SYNTHESIZE ---------------------------------------------------------------
// No merge step. Return proposal set for human review.
phase('Synthesize')

const stopReason =
  dryStreak >= DRY_STREAK_STOP ? `dry-streak (${dryStreak} consecutive non-accepted rounds)` :
  iter      >= MAX_ITER        ? `iteration cap (maxIterations=${MAX_ITER})` :
  'budget floor'

log(`Loop stopped after ${iter} round(s): ${stopReason}`)
log(`Result: ${accepted.length} accepted, ${rejected.length} rejected.`)

if (accepted.length === 0) {
  log('No improvements were accepted. Target may already be high-quality, or rounds were too few.')
}

return {
  target:     TARGET,
  stopReason,
  rounds:     iter,
  accepted: accepted.map(a => ({
    title:       a.title,
    kind:        a.kind,
    branch:      a.branch,
    iter:        a.iter,
    diffSummary: a.diffSummary,
    verifyCmd:   a.verifyCmd,
    localModel:  a.localModel,
  })),
  rejected: rejected.map(r => ({
    title:  r.title,
    kind:   r.kind,
    reason: r.reason,
    branch: r.branch,
    iter:   r.iter,
  })),
  noAutoMerge: 'Accepted changes are in worktree branches only. No merge or PR was created. Human/Director approval required before any merge.',
  hybridRuntime: {
    ollamaUrl:  OLLAMA_URL,
    localModel: LOCAL_MODEL,
    note: 'PLAN+CODE generation ran on local Qwen3.6 (Ollama). REVIEW adversarial gate ran on Claude. Qwen3.6 serializes on Ollama (qwen35moe arch, 1-slot limit) — sequential PLAN/CODE is expected behavior.',
  },
}
