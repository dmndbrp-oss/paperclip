export const meta = {
  name: 'self-evolve-hybrid',
  description: 'Hybrid bounded Skill/Prompt Improvement Loop: Collect+Critique+Propose on local model, Evaluate+Gate on Claude. Targets agents\' own SKILL.md files, prompts, and playbooks. Worktree isolation, Claude review gate, no auto-merge.',
  phases: [
    { title: 'Collect',   detail: 'Local model reads target skill source (SKILL.md)' },
    { title: 'Critique',  detail: 'Local model scores skill on rubric (clarity/safety/traceability/completeness)' },
    { title: 'Propose',   detail: 'Local model drafts candidate refinement in worktree' },
    { title: 'Evaluate',  detail: 'Claude compares candidate vs current on held-out eval set' },
    { title: 'Gate',      detail: 'Claude gate: accept if delta>0 and no regression' },
  ],
}

// ---- Configure via args ------
const TARGET_SKILL     = args?.targetSkill      // required
const MAX_ITER         = args?.maxIterations     ?? 3
const DRY_STREAK_STOP  = args?.dryStreakToStop  ?? 2
const BUDGET_FLOOR     = args?.budgetFloor       ?? 20000
const CANARY_PROTOCOL  = args?.canaryProtocol    ?? false
const CANARY_TARGET    = args?.canaryTarget      ?? null
const OLLAMA_URL       = args?.ollamaUrl         ?? 'http://127.0.0.1:11434'
const LOCAL_MODEL      = args?.localModel        ?? 'qwen3.6:latest'
const RUBRIC           = args?.rubric            ?? { clarity: 25, safety: 25, traceability: 25, completeness: 25 }

if (!TARGET_SKILL) {
  log(`self-evolve-hybrid requires targetSkill. Got: ${TARGET_SKILL}. Aborting.`)
  return { error: 'targetSkill is required. Specify via args or Workflow config.' }
}

// ---- State ------
const accepted = []
const rejected = []
const seen     = new Set()
let dryStreak  = 0
let iter       = 0
let canaryCount = 0
const canaryLogs = []

// ---- Schemas ------
const COLLECT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['hasImprovement', 'skillSource', 'evalSet', 'localModelUsed'],
  properties: {
    hasImprovement:      { type: 'boolean' },
    skillSource:         { type: 'string' },
    evalSet:             { type: 'array', items: { type: 'string' } },
    noImprovementReason: { type: 'string' },
    localModelUsed:      { type: 'string' },
  },
}

const CRITIQUE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['clarity', 'safety', 'traceability', 'completeness', 'total', 'notes', 'localModelUsed'],
  properties: {
    clarity:      { type: 'number' },
    safety:       { type: 'number' },
    traceability: { type: 'number' },
    completeness: { type: 'number' },
    total:        { type: 'number' },
    notes:        { type: 'string' },
    localModelUsed: { type: 'string' },
  },
}

const PROPOSE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['applied', 'candidateSource', 'branch', 'summary', 'localModelUsed'],
  properties: {
    applied:         { type: 'boolean' },
    candidateSource: { type: 'string' },
    branch:          { type: 'string' },
    summary:         { type: 'string' },
    localModelUsed:  { type: 'string' },
  },
}

// ---- Preflight ------
phase('Preflight')

const modelsLoaded = await agent(
  `Return the list of currently loaded Ollama model names (no analysis needed).`,
  { label: 'ollama-loaded', phase: 'Preflight', schema: { type: 'object', properties: { models: { type: 'array', items: { type: 'string' } } } } },
)

const availableModels = await agent(
  `Return the list of available Ollama model tags (no analysis needed).`,
  { label: 'ollama-tags', phase: 'Preflight', schema: { type: 'object', properties: { tags: { type: 'array', items: { type: 'string' } } } } },
)

const loadedModels = (modelsLoaded?.models ?? [])
const availableTags = (availableModels?.tags ?? [])
const hasModel = loadedModels.includes(LOCAL_MODEL) || availableTags.some(t => t.includes(LOCAL_MODEL))

if (!hasModel) {
  log(`PREFLIGHT FAILED: ${LOCAL_MODEL} not available on Ollama at ${OLLAMA_URL}`)
  log(`Loaded models: [${loadedModels.join(', ')}]`)
  log(`Available tags: [${availableTags.join(', ')}]`)
  log(`Aborting hybrid run. Fix Ollama/model then retry.`)
  return {
    targetSkill: TARGET_SKILL,
    stopReason: 'preflight-failed: local model not available',
    rounds: 0,
    rejected: [],
    accepted: [],
    noAutoMerge: 'No changes — preflight failed.',
    hybridRuntime: { ollamaUrl: OLLAMA_URL, localModel: LOCAL_MODEL, preflightOk: false },
  }
}

log(`Preflight OK: ${LOCAL_MODEL} available at ${OLLAMA_URL}`)

// ---- Loop ------
while (iter < MAX_ITER && dryStreak < DRY_STREAK_STOP) {
  if (budget.total && budget.remaining() <= BUDGET_FLOOR) {
    log(`Budget floor reached (${budget.remaining()} tokens remaining <= floor ${BUDGET_FLOOR}). Stopping.`)
    break
  }

  iter++
  log(`--- Round ${iter}/${MAX_ITER} | dry streak ${dryStreak}/${DRY_STREAK_STOP} | target: ${TARGET_SKILL} ---`)

  if (CANARY_PROTOCOL) {
    const runLog = `canary/run${canaryCount + 1}/${canaryTarget || 'unspecified'}`
    canaryLogs.push(runLog)
    canaryCount++
    log(`Canary run ${canaryCount}/log: ${runLog}`)
  }

  // ---- COLLECT (local + cloud hybrid) ------
  // COLLECT always uses cloud agent (reads SKILL.md from file system)
  phase('Collect')

  const collectResult = await agent(
    `You are a skill analyst. Read the SKILL.md for skill "${TARGET_SKILL}" from the filesystem ` +
    `(check .claude/skills/, skills-src/). If not found, report hasImprovement=false. ` +
    `(CANARY MODE: target is "${CANARY_TARGET}"). ` +
    `Return JSON matching COLLECT_SCHEMA. Do NOT write text outside the JSON block.`,
    { label: 'collect', phase: 'Collect', schema: COLLECT_SCHEMA },
  )

  if (!collectResult || collectResult.hasImprovement === false) {
    dryStreak++
    rejected.push({ iter, reason: `No improvement: ${collectResult?.noImprovementReason || 'unknown'}` })
    log(`No improvement. dry streak ${dryStreak}.`)
    continue
  }

  const skillSource = collectResult.skillSource
  const evalSet     = collectResult.evalSet
  const localModelUsed = collectResult.localModelUsed || LOCAL_MODEL
  log(`Source collected. Eval set: ${evalSet.length} rows.`)

  // ---- CRITIQUE (local) ------
  phase('Critique')

  const critiquePrompt =
    `You are a skill critic. Score this skill source on clarity, safety, traceability, completeness ` +
    `(weights: ${Object.entries(RUBRIC).map(([k,v]) => `${k}=${v}`).join(', ')}). ` +
    `Be harsh.\n\n` +
    `Skill source:\n---\n${skillSource.substring(0, 10000)}\n---\n` +
    `Return ONLY valid JSON matching CRITIQUE_SCHEMA.\nDo NOT write text outside the JSON block.`

  const critiqueResult = await agent(
    `Call Qwen3.6 on Ollama at ${OLLAMA_URL}: curl -sf -X POST ${OLLAMA_URL}/api/chat -d '{"model":"${LOCAL_MODEL}","stream":false,"think":false,"options":{"temperature":0.2},"messages":[{"role":"user","content":"${critiquePrompt}"}]}'\n` +
    `Parse response, extract content, strip markdown fences. Return JSON matching CRITIQUE_SCHEMA.`,
    { label: 'critique-local', phase: 'Critique', schema: CRITIQUE_SCHEMA },
  )

  if (!critiqueResult) {
    dryStreak++
    rejected.push({ iter, reason: 'Critique returned null.' })
    log(`Critique failed. dry streak ${dryStreak}.`)
    continue
  }

  const currentScore = {
    clarity:      critiqueResult.clarity,
    safety:       critiqueResult.safety,
    traceability: critiqueResult.traceability,
    completeness: critiqueResult.completeness,
    total:        critiqueResult.clarity + critiqueResult.safety + critiqueResult.traceability + critiqueResult.completeness,
  }
  log(`Critique: ${critiqueResult.total}/100 — clarity=${critiqueResult.clarity}, safety=${critiqueResult.safety}, ` +
      `traceability=${critiqueResult.traceability}, completeness=${critiqueResult.completeness}`)

  // ---- PROPOSE (local, in worktree) ------
  phase('Propose')

  const proposePrompt =
    `You are a skill-editor. Draft a candidate refinement to this SKILL.md: "${TARGET_SKILL}". ` +
    `Improve the weakest dimension (${critiqueResult.notes}).\n\nRules:\n` +
    `1. Only SKILL.md edits for "${TARGET_SKILL}" (or canary "${CANARY_TARGET}")\n` +
    `2. Never modify governance/security/identity files\n` +
    `3. Never change the skill's behavior\n\n` +
    `Current source:\n---\n${skillSource.substring(0, 10000)}\n---\n` +
    `Return ONLY valid JSON matching PROPOSE_SCHEMA. Do NOT write text outside the JSON block.`

  const proposeResult = await agent(
    `Call Qwen3.6 on Ollama at ${OLLAMA_URL}: curl -sf -X POST ${OLLAMA_URL}/api/chat -d '{"model":"${LOCAL_MODEL}","stream":false,"think":false,"options":{"temperature":0.2},"messages":[{"role":"user","content":"${proposePrompt}"}]}'\n` +
    `Parse response, extract content, strip markdown fences. Return JSON matching PROPOSE_SCHEMA.`,
    { label: 'propose-local', phase: 'Propose', schema: PROPOSE_SCHEMA, isolation: 'worktree' },
  )

  if (!proposeResult || !proposeResult.applied) {
    dryStreak++
    rejected.push({ iter, reason: 'Propose did not apply.' })
    log(`Propose failed. dry streak ${dryStreak}.`)
    continue
  }

  const candidateBranch = proposeResult.branch
  const candidateSource = tryReadBranch(proposeResult.candidateSource, candidateBranch)
  if (!candidateSource) {
    dryStreak++
    rejected.push({ iter, reason: 'Could not read candidate from worktree branch.' })
    log(`Propose branch unreadable. dry streak ${dryStreak}.`)
    continue
  }

  seen.add(`propose.${iter}`)
  log(`Candidate proposed in branch "${candidateBranch}".`)

  // ---- EVALUATE (cloud) ------
  phase('Evaluate')

  const evaluatePrompt =
    `Compare candidate vs current skill source. Score both on clarity/safety/traceability/completeness (0-100). ` +
    `Compute delta = candidateScore - currentScore for each dimension. ` +
    `List regressions (dimensions where delta < 0).\n\n` +
    `Current score: ${critiqueResult.total}/100\n` +
    `Current source:\n${skillSource.substring(0, 10000)}\n\n` +
    `Candidate source:\n${candidateSource.substring(0, 10000)}\n\n` +
    `Return ONLY valid JSON with structure: {"currentScore":{...}, "candidateScore":{...}, "delta":{...}, "regressions":[], "notes":"..."}`

  const evaluateResult = await agent(
    evaluatePrompt,
    { label: 'evaluate', phase: 'Evaluate', schema: { type: 'object', additionalProperties: false, required: ['currentScore', 'candidateScore', 'delta', 'regressions', 'notes'],
      properties: {
        currentScore:   { type: 'object' },
        candidateScore: { type: 'object' },
        delta:          { type: 'object' },
        regressions:    { type: 'array', items: { type: 'string' } },
        notes:          { type: 'string' },
      }
    }},
  )

  if (!evaluateResult) {
    dryStreak++
    rejected.push({ iter, reason: 'Evaluate returned null.' })
    log(`Evaluate failed. dry streak ${dryStreak}.`)
    continue
  }

  const deltaTotal = Object.values(evaluateResult.delta).reduce((a, b) => a + b, 0)
  log(`A/B eval: delta total ${deltaTotal}. Regressions: ${evaluateResult.regressions.length}.`)

  // ---- GATE (cloud) ------
  phase('Gate')

  const gatePrompt =
    `You are a gate reviewer in the self-evolve loop. This is self-targeting: apply the same standard ` +
    `you would to any other agent's work. Do NOT soften the review.\n\n` +
    `A/B eval delta total: ${deltaTotal}\n` +
    `Clarity delta: ${(evaluateResult.delta.clarity || 0)}\n` +
    `Safety delta: ${(evaluateResult.delta.safety || 0)}\n` +
    `Traceability delta: ${(evaluateResult.delta.traceability || 0)}\n` +
    `Completeness delta: ${(evaluateResult.delta.completeness || 0)}\n` +
    `Regressions: ${evaluateResult.regressions.join(', ') || 'none'}\n\n` +
    `Accept if delta total > 0 AND no dimension regressed. Reject with specific deficit if not.\n` +
    `Return ONLY valid JSON: {"accept": true/false, "reasoning": "..."}.`

  const gateResult = await agent(
    gatePrompt,
    { label: 'gate', phase: 'Gate', schema: { type: 'object', additionalProperties: false, required: ['accept', 'reasoning'],
      properties: {
        accept:     { type: 'boolean' },
        reasoning:  { type: 'string' },
      }
    }},
  )

  if (!gateResult) {
    dryStreak++
    rejected.push({ iter, reason: 'Gate returned null.' })
    log(`Gate failed. dry streak ${dryStreak}.`)
    continue
  }

  if (gateResult.accept) {
    accepted.push({
      title: gateResult.reasoning,
      branch: candidateBranch,
      iter,
      evalDelta: deltaTotal,
      rubric: currentScore.total,
    })
    dryStreak = 0
    log(`ACCEPTED (delta=${deltaTotal}). dry streak reset.`)
  } else {
    rejected.push({
      title: gateResult.reasoning,
      iter,
      rubric: currentScore.total,
      reason: gateResult.reasoning,
    })
    dryStreak++
    log(`REJECTED. Reason: ${gateResult.reasoning}. dry streak ${dryStreak}.`)
  }
}

// ---- Post-loop: canary protocol check ------
let canaryProtocol = null
if (CANARY_PROTOCOL) {
  canaryProtocol = {
    runs: canaryCount,
    runLogs: canaryLogs,
    allRunsComplete: canaryCount === 3,
  }
}

// ---- Return ------
return {
  targetSkill: TARGET_SKILL,
  stopReason: dryStreak >= DRY_STREAK_STOP
    ? `dry-streak (${dryStreak} consecutive)`
    : iter >= MAX_ITER
    ? `iteration cap (${iter})`
    : `budget floor (${BUDGET_FLOOR})`,
  rounds: iter,
  currentScore,
  accepted,
  rejected,
  noAutoMerge: 'Accepted changes are in worktree branches. No merge or catalog import was created. Human/Director approval required before changes land.',
  hybridRuntime: { ollamaUrl: OLLAMA_URL, localModel: LOCAL_MODEL, preflightOk: true },
  canaryProtocol,
}
