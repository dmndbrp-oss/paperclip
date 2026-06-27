# SAG-4533 Next Heartbeat Instructions

Written by run 2c136932 (blocked by stale run a92b794f checkout on SAG-4533).

## Situation
- SAG-4533 skill files: COMPLETE and synced to ~/.claude/skills/improve-loop/
  - skills-src/improve-loop/SKILL.md ✓
  - skills-src/improve-loop/templates/improve-loop.workflow.js ✓
- Demo workflow wf_071e323b-724 running on pilot-artifacts/validator.py maxIterations=2
- Demo artifact NOT yet saved to skills-src/improve-loop/demo/

## On next wake (demo workflow complete notification)

1. Checkout SAG-4533 (625cf704-01da-4274-8afc-9c4c5b9e386f) — stale run should be expired
2. Save demo artifact from workflow result to:
   - ~/.claude/skills/improve-loop/demo/run-001-validator-demo.json
   - /home/gus-pinsoneault/.paperclip/instances/default/companies/1dc911ed-ff05-4072-b2ae-a3e3177e3873/skills-src/improve-loop/demo/run-001-validator-demo.json
3. Post completion comment on SAG-4533 with:
   - All acceptance criteria checklist (with ✓ marks)
   - Link to demo artifact
   - Collision rationale flagged for CEO/board
   - Confidence 8/10
4. PATCH SAG-4533 to in_review, assigneeAgentId = f3c48afc-c339-4e43-b47b-a42a0891229d (CTO)
5. @mention [@CTO (Opus 4.8)](agent://f3c48afc-c339-4e43-b47b-a42a0891229d) in comment

## Skills location
- Source: /home/gus-pinsoneault/.paperclip/instances/default/companies/1dc911ed-ff05-4072-b2ae-a3e3177e3873/skills-src/improve-loop/
- Synced: ~/.claude/skills/improve-loop/
