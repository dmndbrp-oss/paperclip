export const meta = {
  name: 'self-evolve',
  description: 'Bounded Skill/Prompt Improvement Loop (Collect → Critique → Propose → Evaluate → Gate). Targets agents\' own SKILL.md files, prompts, and playbooks. Worktree-isolated, Claude review gate, no auto-merge. SAG-4787/SAG-4802 Tier A.',
  phases: [
    { title: 'Collect',   detail: 'Assemble target skill source + held-out eval set from traces' },
    { title: 'Critique',  detail: 'Score current skill on clarity/safety/traceability/completeness' },
    { title: 'Propose',   detail: 'Generate candidate refinement to SKILL.md/prompt in worktree' },
    { title: 'Evaluate',  detail: 'A/B comparison: candidate vs current on held-out eval set' },
    { title: 'Gate',      detail: 'Accept if delta>0 and no regression; reject with specific deficit' },
  ],
}

// ---- Configure via args ------
const TARGET_SKILL     = args?.targetSkill      // required: name of skill to evolve
const MAX_ITER         = args?.maxIterations     ?? 3
const DRY_STREAK_STOP  = args?.dryStreakToStop  ?? 2
const BUDGET_FLOOR     = args?.budgetFloor       ?? 20000
const CANARY_PROTOCOL  = args?.canaryProtocol    ?? false
const RUBRIC           = args?.rubric            ?? { clarity: 25, safety: 25, traceability: 25, completeness: 25 }
const CANARY_TARGET    = args?.canaryTarget      ?? null // file path to use as canary subject

if (!TARGET_SKILL) {
  log(`self-evolve requires targetSkill. Got: ${TARGET_SKILL}. Aborting.`)
  return { error: 'targetSkill is required. Specify via args or Workflow config.' }
}

// ---- State ------
const accepted   = []
const rejected   = []
const seen       = new Set()
let dryStreak    = 0
let iter         = 0
let canaryCount  = 0
const canaryLogs = []

// ---- Schemas ------
const COLLECT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['hasImprovement', 'skillSource', 'evalSet', 'canaryTarget'],
  properties: {
    hasImprovement:      { type: 'boolean' },
    skillSource:         { type: 'string' },
    evalSet:             { type: 'array', items: { type: 'string' } },
    canaryTarget:        { type: 'string' },
    noImprovementReason: { type: 'string' },
  },
}

const CRITIQUE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['clarity', 'safety', 'traceability', 'completeness', 'total', 'notes'],
  properties: {
    clarity:      { type: 'number' },
    safety:       { type: 'number' },
    traceability: { type: 'number' },
    completeness: { type: 'number' },
    total:        { type: 'number' },
    notes:        { type: 'string' },
  },
}

const PROPOSE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['applied', 'candidateSource', 'branch', 'rubric', 'summary'],
  properties: {
    applied:         { type: 'boolean' },
    candidateSource: { type: 'string' },
    branch:          { type: 'string' },
    rubric:          { type: 'object' },
    summary:         { type: 'string' },
  },
}

const EVALUATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['currentScore', 'candidateScore', 'delta', 'regressions', 'notes'],
  properties: {
    currentScore:   { type: 'object' },
    candidateScore: { type: 'object' },
    delta:          { type: 'object' },
    regressions:    { type: 'array', items: { type: 'string' } },
    notes:          { type: 'string' },
  },
}

const GATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['accept', 'reasoning', 'delta'],
  properties: {
    accept:     { type: 'boolean' },
    reasoning:  { type: 'string' },
    delta:      { type: 'string' },
  },
}

// ---- Loop ------
while (iter < MAX_ITER && dryStreak < DRY_STREAK_STOP) {
  if (budget.total && budget.remaining() <= BUDGET_FLOOR) {
    log(`Budget floor reached (${budget.remaining()} tokens remaining <= floor ${BUDGET_FLOOR}). Stopping.`)
    break
  }

  iter++
  log(`--- Round ${iter}/${MAX_ITER} | dry streak ${dryStreak}/${DRY_STREAK_STOP} | target: ${TARGET_SKILL} ---`)

  // Check canary protocol
  if (CANARY_PROTOCOL) {
    const runLog = `canary/run${canaryCount + 1}/${canaryTarget || 'unspecified'}`
    canaryLogs.push(runLog)
    canaryCount++
    log(`Canary run ${canaryCount}/log: ${runLog}`)
  }

  // ---- COLLECT ------
  phase('Collect')

  const collectPrompt =
    `You are a skill analyst for the self-evolve loop. Your ONLY task: read and assemble the source ` +
    `of the target skill "${TARGET_SKILL}", and identify a held-out eval set from past agent outputs.\n\n` +
    (CANARY_TARGET ? `CANARY MODE: The target skill to evolve is "${CANARY_TARGET}" (SAG-4804 canary).\n\n` : '') +
    `Steps:\n` +
    `1. Read the SKILL.md for skill "${TARGET_SKILL}". If not found, search skills/src/ and .claude/skills/\n` +
    `2. Read the full source text and return it as skillSource.\n` +
    `3. Identify a held-out eval set: collect [0, 1, 2] from past outputs for this skill\n` +
    `4. Set hasImprovement: true only if you can identify a concrete quality gap in the SKILL.md\n\n` +
    `Output ONLY valid JSON matching COLLECT_SCHEMA. Do NOT write any text outside the JSON block.`

  const collectAgent = Workflow({
    name: 'self-evolve-collect',
    role: 'Analyze skill source and identify improvement target.',
    prompt: collectPrompt,
    schemas: [COLLECT_SCHEMA],
  })

  const collectResult = JSON.parse(collectAgent)
  if (collectResult.hasImprovement === false) {
    dryStreak++
    rejected.push({ iter, reason: `No improvement identified: ${collectResult.noImprovementReason}` })
    log(`No improvement identified: ${collectResult.noImprovementReason}. dry streak ${dryStreak}.`)
    continue
  }

  const skillSource = collectResult.skillSource
  const evalSet     = collectResult.evalSet
  seen.add(`collect.${iter}`)
  log(`Source collected. Eval set: ${evalSet.length} rows.`)

  // ---- CRITIQUE ------
  phase('Critique')

  const critiquePrompt =
    `Score the following skill source on clarity, safety, traceability, completeness. ` +
    `Weights: clarity=${RUBRIC.clarity}, safety=${RUBRIC.safety}, traceability=${RUBRIC.traceability}, completeness=${RUBRIC.completeness}.\n\n` +
    `Skill source:\n---\n${skillSource.substring(0, 10000)}\n---\n\n` +
    `Score each dimension on 0-100 scale (0 = terrible, 100 = excellent). ` +
    `Be harsh — this is self-targeting. Use the rubric weights to compute total.\n\n` +
    `Output ONLY valid JSON matching CRITIQUE_SCHEMA. Do NOT write any text outside the JSON block.`

  const critiqueAgent = Workflow({
    name: 'self-evolve-critique',
    role: `Score skill source on clarity (${RUBRIC.clarity}/25), safety (${RUBRIC.safety}/25), traceability (${RUBRIC.traceability}/25), completeness (${RUBRIC.completeness}/25).`,
    prompt: critiquePrompt,
    schemas: [CRITIQUE_SCHEMA],
  })

  let currentCritique
  try {
    currentCritique = JSON.parse(critiqueAgent)
  } catch (e) {
    dryStreak++
    rejected.push({ iter, reason: `Critique JSON parse error: ${e.message}` })
    log(`Critique parse error. dry streak ${dryStreak}.`)
    continue
  }

  const currentScore = {
    clarity:      currentCritique.clarity,
    safety:       currentCritique.safety,
    traceability: currentCritique.traceability,
    completeness: currentCritique.completeness,
    total:        currentCritique.clarity + currentCritique.safety + currentCritique.traceability + currentCritique.completeness,
  }

  log(`Current score: clarity=${currentCritique.clarity}, safety=${currentCritique.safety}, ` +
      `traceability=${currentCritique.traceability}, completeness=${currentCritique.completeness}, ` +
      `total=${currentCritique.total}`)

  // ---- PROPOSE ------
  phase('Propose')

  const proposePrompt =
    `You are a skill-editor in the self-evolve loop. Your task: draft a candidate refinement ` +
    `that improves the current skill source. Only change the documentation — never change ` +
    `behavioral contracts, guardrails, or scope limits.\n\n` +
    `Current critique (score ${currentCritique.total}/100):\n` +
    `${currentCritique.notes}\n\n` +
    `Current skill source:\n---\n${skillSource.substring(0, 10000)}\n---\n\n` +
    `Rules:\n` +
    `1. Only SKILL.md edits for skill "${TARGET_SKILL}"\n` +
    `2. Never modify governance/security/identity files\n` +
    `3. Never change the skill's behavioral contract\n` +
    `4. Improve the weakest rubric dimension(s) from the critique above\n\n` +
    `Output ONLY valid JSON matching PROPOSE_SCHEMA. Do NOT write any text outside the JSON block.`

  const proposeAgent = Workflow({
    name: 'self-evolve-propose',
    role: 'Generate candidate SKILL.md refinement in worktree.',
    prompt: proposePrompt,
    schemas: [PROPOSE_SCHEMA],
    isolation: 'worktree',
  })

  let proposeResult
  try {
    proposeResult = JSON.parse(proposeAgent)
  } catch (e) {
    dryStreak++
    rejected.push({ iter, reason: `Propose JSON parse error: ${e.message}` })
    log(`Propose parse error. dry streak ${dryStreak}.`)
    continue
  }

  if (!proposeResult.applied) {
    dryStreak++
    rejected.push({ iter, reason: 'Propose did not apply (no change needed or schema mismatch).' })
    log(`Propose did not apply. dry streak ${dryStreak}.`)
    continue
  }

  const candidateBranch = proposeResult.branch
  seen.add(`propose.${iter}`)
  log(`Candidate proposed in branch "${candidateBranch}".`)

  // ---- EVALUATE ------
  phase('Evaluate')

  // Read the candidate SKILL.md from the worktree branch
  const candidateSource = tryReadBranch(proposeResult.candidateSource, candidateBranch)

  const evaluatePrompt =
    `Perform A/B evaluation of the candidate skill source vs the current skill source.\n\n` +
    `Current score: clarity=${currentCritique.clarity}, safety=${currentCritique.safety}, ` +
    `traceability=${currentCritique.traceability}, completeness=${currentCritique.completeness}\n\n` +
    `Current source (first 10000 chars):\n---\n${skillSource.substring(0, 10000)}\n---\n\n` +
    `Candidate source (first 10000 chars):\n---\n${candidateSource.substring(0, 10000)}\n---\n\n` +
    `Score both on 0-100 scale for each dimension. ` +
    `Compute delta = candidateScore - currentScore for each dimension. ` +
    `List regressions (any dimension where delta < 0).\n\n` +
    `Output ONLY valid JSON matching EVALUATE_SCHEMA. Do NOT write any text outside the JSON block.`

  const evaluateAgent = Workflow({
    name: 'self-evolve-evaluate',
    role: `Compare candidate vs current. Score each on clarity/safety/traceability/completeness. Compute delta and regressions.`,
    prompt: evaluatePrompt,
    schemas: [EVALUATE_SCHEMA],
  })

  let evalResult
  try {
    evalResult = JSON.parse(evaluateAgent)
  } catch (e) {
    dryStreak++
    rejected.push({ iter, reason: `Evaluate JSON parse error: ${e.message}` })
    log(`Evaluate parse error. dry streak ${dryStreak}.`)
    continue
  }

  const deltaTotal = Object.values(evalResult.delta).reduce((a, b) => a + b, 0)
  log(`A/B eval: delta total ${deltaTotal}. Regressions: ${evalResult.regressions.length}.`)

  // ---- GATE ------
  phase('Gate')

  const gatePrompt =
    `You are a gate reviewer in the self-evolve loop. ` +
    `Evaluate the candidate refinement against the held-out quality standards. ` +
    `The proposal MUST be held to the same standard you would apply to any other agent's work. ` +
    `This is self-targeting: you are reviewing your own team's documentation. ` +
    `Do NOT soften the review because it is "our own" or "self-targeted." ` +
    `Apply the exact same standard you would apply to an external contribution.\n\n` +
    `A/B eval results:\n` +
    `  Delta total: ${deltaTotal}\n` +
    `  Clarity delta: ${(evalResult.delta.clarity || 0)}\n` +
    `  Safety delta:  ${(evalResult.delta.safety || 0)}\n` +
    `  Traceability delta: ${(evalResult.delta.traceability || 0)}\n` +
    `  Completeness delta: ${(evalResult.delta.completeness || 0)}\n` +
    `  Regressions: ${evalResult.regressions.join(', ') || 'none'}\n\n` +
    `Current score: ${currentScore.total}/100\n` +
    `Candidate score: ${Object.values(evalResult.candidateScore).reduce((a, b) => a + b, 0)}/100\n\n` +
    `Gate criteria:\n` +
    `(a) delta total > 0\n` +
    `(b) no dimension regressed\n\n` +
    `Output ONLY valid JSON matching GATE_SCHEMA. Do NOT write any text outside the JSON block.`

  const gateAgent = Workflow({
    name: 'self-evolve-gate',
    role: `Gate candidate: accept if delta>0 and no regression, reject with specific deficit.`,
    prompt: gatePrompt,
    schemas: [
      { type: 'object', additionalProperties: false,
        required: ['accept', 'reasoning', 'delta'],
        properties: {
          accept:     { type: 'boolean' },
          reasoning:  { type: 'string' },
          delta:      { type: 'string' },
        },
      },
    ],
  })

  let gateResult
  try {
    gateResult = JSON.parse(gateAgent)
  } catch (e) {
    dryStreak++
    rejected.push({ iter, reason: `Gate JSON parse error: ${e.message}` })
    log(`Gate parse error. dry streak ${dryStreak}.`)
    continue
  }

  if (gateResult.accept) {
    accepted.push({
      title: gateResult.reasoning,
      branch: candidateBranch,
      iter,
      evalDelta: deltaTotal,
      rubric: currentCritique.total,
    })
    dryStreak = 0
    log(`ACCEPTED (delta=${deltaTotal}). dry streak reset.`)
  } else {
    rejected.push({
      title: gateResult.reasoning,
      iter,
      rubric: currentCritique.total,
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
  noAutoMerge: 'Accepted changes are in worktree branches. No merge or catalog import was created. Human/Director approval required before any changes land.',
  canaryProtocol,
}
