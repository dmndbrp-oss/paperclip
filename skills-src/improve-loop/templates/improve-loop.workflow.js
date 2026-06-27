export const meta = {
  name: 'improve-loop',
  description: 'Bounded code-quality improvement loop (plan→code→review, loop-until-dry). Quality-only: reuse/simplify/efficiency. Worktree-isolated, no merge/PR. Stops on dry-streak, iteration cap, or budget floor.',
  phases: [
    { title: 'Plan',       detail: 'Identify highest-value quality improvement not yet seen/accepted/rejected' },
    { title: 'Code',       detail: 'Apply change in isolated git worktree; run verifyCmd inside worktree' },
    { title: 'Review',     detail: 'Adversarial 3-lens verify: real improvement, no regression, verify green' },
    { title: 'Synthesize', detail: 'Emit accepted/rejected/skipped; NO merge or PR-create step' },
  ],
}

// ---- Configure via args -------------------------------------------------------
// target:          glob/path/subsystem to improve (e.g. 'pilot-artifacts/validator.py')
// maxIterations:   hard iteration cap (default 8)
// dryStreakToStop: consecutive non-accepted rounds before stopping (default 2)
// budgetFloor:     stop when budget.remaining() <= this many tokens (default 20000)
// -------------------------------------------------------------------------------
const TARGET          = args?.target          ?? '.'
const MAX_ITER        = args?.maxIterations    ?? 8
const DRY_STREAK_STOP = args?.dryStreakToStop  ?? 2
const BUDGET_FLOOR    = args?.budgetFloor      ?? 20000

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
  },
}

const CODE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['applied', 'diff', 'branch', 'verifyOutput', 'verifyPassed'],
  properties: {
    applied:      { type: 'boolean' },
    diff:         { type: 'string' },
    branch:       { type: 'string' },
    verifyOutput: { type: 'string' },
    verifyPassed: { type: 'boolean' },
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

// ---- State --------------------------------------------------------------------

const accepted = []
const rejected = []
const seen     = new Set()
let dryStreak  = 0
let iter       = 0

// ---- Loop-until-dry -----------------------------------------------------------

while (iter < MAX_ITER && dryStreak < DRY_STREAK_STOP) {

  // Budget guard (LES §1 part 4 — termination path)
  if (budget.total && budget.remaining() <= BUDGET_FLOOR) {
    log(`Budget floor reached (${budget.remaining()} tokens remaining <= floor ${BUDGET_FLOOR}). Stopping.`)
    break
  }

  iter++
  log(`--- Round ${iter}/${MAX_ITER} | dry streak ${dryStreak}/${DRY_STREAK_STOP} | seen: [${[...seen].join(', ') || 'none'}] ---`)

  // ---- PLAN -------------------------------------------------------------------
  phase('Plan')

  const seenJson = JSON.stringify([...seen])

  const plan = await agent(
    `You are a code-quality planner. Read the source file(s) at "${TARGET}" and identify the ` +
    `SINGLE highest-value quality improvement that meets ALL of:\n` +
    `  1. Quality-ONLY: kind must be one of {reuse, simplify, efficiency}.\n` +
    `     - reuse: extract duplicated logic into a shared helper\n` +
    `     - simplify: remove unnecessary complexity, dead code, over-engineered constructs\n` +
    `     - efficiency: improve performance of a hot path without changing behavior\n` +
    `  2. NOT in this already-seen set: ${seenJson}\n` +
    `  3. Does NOT fix bugs or change observable behavior.\n` +
    `  4. Actionable in a single focused, minimal commit.\n\n` +
    `If no meaningful quality improvement exists (all obvious ones done, or none warranted), ` +
    `set hasImprovement:false and fill noImprovementReason.\n\n` +
    `verifyCmd: the shell command that PROVES the changed code is correct — not just parses.\n` +
    `  DISCOVERY RULE (mandatory): Before choosing verifyCmd, check whether a test suite exists:\n` +
    `    - Python: look for test_*.py / *_test.py in the same directory or a sibling test/ dir.\n` +
    `    - JS/TS: look for *.test.js / *.spec.js / *.test.ts, or a package.json "test" script.\n` +
    `    - Other: look for Makefile "test" target, CMakeLists tests, etc.\n` +
    `  If a test suite IS found → verifyCmd MUST run it. Examples:\n` +
    `    "python -m pytest pilot-artifacts/ -x -q"\n` +
    `    "python -m unittest discover -s pilot-artifacts -p 'test_*.py' -q"\n` +
    `    "npm test --prefix <dir>"\n` +
    `  If NO test suite exists → use the next-best runtime check (import, smoke-run, lint).\n` +
    `  NEVER use a syntax-only check (ast.parse, eslint --parser-options, etc.) when a test\n` +
    `  suite is present. Syntax-only checks miss behavioral regressions and will be REJECTED\n` +
    `  by the verify-green reviewer.`,
    { label: `plan:r${iter}`, phase: 'Plan', schema: PLAN_SCHEMA },
  )

  if (!plan || !plan.hasImprovement) {
    const reason = plan?.noImprovementReason ?? 'agent returned no improvement'
    log(`Round ${iter} PLAN: no improvement found — "${reason}". Dry streak → ${dryStreak + 1}.`)
    dryStreak++
    continue
  }

  seen.add(plan.title)
  log(`Round ${iter} PLAN: "${plan.title}" (${plan.kind}) — ${plan.rationale.slice(0, 120)}`)

  // ---- CODE -------------------------------------------------------------------
  phase('Code')

  const code = await agent(
    `Apply this quality improvement to "${TARGET}":\n` +
    `  Title:     ${plan.title}\n` +
    `  Kind:      ${plan.kind}\n` +
    `  Rationale: ${plan.rationale}\n` +
    `  Files:     ${plan.files.length > 0 ? plan.files.join(', ') : TARGET}\n\n` +
    `Rules (HARD):\n` +
    `  1. Apply ONLY the described quality change. Do NOT fix bugs, add features, or change behavior.\n` +
    `  2. Make the minimal diff needed. Prefer editing the existing file over rewrites.\n` +
    `  3. After editing, run: ${plan.verifyCmd}\n` +
    `     Capture stdout+stderr as verifyOutput.\n` +
    `     verifyPassed = (exit code was 0).\n` +
    `  4. Run "git diff HEAD" (or "git diff --cached" after staging) to capture the diff.\n` +
    `  5. Run "git branch --show-current" to capture the branch name.\n` +
    `  6. If you cannot apply the change cleanly (conflicts, scope unclear), ` +
    `set applied:false, explain in diff, and set verifyPassed:false.\n\n` +
    `Return applied, diff (full patch text), branch (current branch name), verifyOutput, verifyPassed.`,
    { label: `code:r${iter}`, phase: 'Code', schema: CODE_SCHEMA, isolation: 'worktree' },
  )

  if (!code || !code.applied) {
    const reason = code?.diff ?? 'CODE agent returned no result'
    log(`Round ${iter} CODE: apply failed — "${reason.slice(0, 200)}". Dry streak → ${dryStreak + 1}.`)
    rejected.push({ title: plan.title, kind: plan.kind, reason: `apply-failed: ${reason.slice(0, 300)}`, branch: null, iter })
    dryStreak++
    continue
  }

  if (!code.verifyPassed) {
    log(`Round ${iter} CODE: verifyCmd FAILED. Discarding. Dry streak → ${dryStreak + 1}.`)
    log(`  Verify output: ${code.verifyOutput.slice(0, 300)}`)
    rejected.push({ title: plan.title, kind: plan.kind, reason: `verify-failed: ${code.verifyOutput.slice(0, 300)}`, branch: code.branch, iter })
    dryStreak++
    continue
  }

  log(`Round ${iter} CODE: applied on branch "${code.branch}", verify PASSED.`)

  // ---- REVIEW -----------------------------------------------------------------
  // Three adversarial skeptics. Default-to-refute (accept:false on uncertainty).
  // Each has a DISTINCT lens so they catch different failure modes.
  phase('Review')

  const reviewContext =
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
        `quality improvements. If in doubt, reject.`,
    },
    {
      key: 'no-regression',
      prompt:
        `Lens — NO REGRESSION: does this change preserve ALL observable behavior?\n` +
        `You are an adversarial tester. Set accept:false by default.\n` +
        `Look for: changed return values, dropped error paths, reordered side effects, ` +
        `removed capabilities, subtle semantic shifts. Even a 1-line change can break behavior. ` +
        `If ANY behavior changed (even "for the better"), set accept:false and explain.`,
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
        `regardless of code correctness is as bad as a failing verify. Reject if not credible.`,
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
    log(`Round ${iter} REVIEW: ACCEPTED (${acceptCount}/${validVotes.length} votes). Branch: "${code.branch}".`)
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
    })
    dryStreak = 0
  } else {
    const reasons = validVotes.filter(v => !v.accept).map((v, i) => `[${LENSES[i]?.key ?? i}] ${v.reasoning.slice(0, 120)}`).join('; ')
    log(`Round ${iter} REVIEW: REJECTED (${acceptCount}/${validVotes.length} votes). Reason: ${reasons.slice(0, 300)}`)
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
  })),
  rejected: rejected.map(r => ({
    title:  r.title,
    kind:   r.kind,
    reason: r.reason,
    branch: r.branch,
    iter:   r.iter,
  })),
  noAutoMerge: 'Accepted changes are in worktree branches only. No merge or PR was created. Human/Director approval required before any merge.',
}
