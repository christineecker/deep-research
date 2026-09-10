---
name: full-pipeline-mechanics
description: Stages 1–8 mechanics, interrupts, and output files, from Lessons 3–5
metadata:
  type: learning-record
---

## What this covers

Lessons 3–5 walked the full pipeline body, completing the mission's "name all 8 stages and what
each writes" and "read a run's output" success criteria:

- **Lesson 3** (`0003-stages-1-4-protocol-to-retrieve.html`): protocol → search → screen →
  retrieve, and the one interrupt in this stretch — Stage 4's `missing.md` halt-and-ask, gated
  hard on `systematic`/`max`, provisional on `fast`/`standard`.
- **Lesson 4** (`0004-stages-5-6-extract-appraise.html`): extract → appraise, the pool-reuse
  gate that skips redundant subagent work, `abstract_only` tagging consequences, project-scoped
  appraisal in repo mode, and reading `status.py --table` as the Stage 5/6 checkpoint.
- **Lesson 5** (`0005-stages-7-8-synthesize-publish.html`): synthesize → digest → publish, the
  three invariants that keep a synthesis honest (null findings actively sought, conflicts
  explained not averaged, hypotheses hard-walled from evidence), the fixed
  assemble→verify→render→promote order, and which output file answers which question
  (digest.md = bottom line, report.md = audit trail, verification.json = did it pass its own
  checks).

## Zone of proximal development notes

This completes the "operate a run end to end, on paper" arc from [[scope-and-profile-shorthand]]
and [[stage-0-mechanics]] through to reading final output. The course's stated next step (per
Lesson 5's nav) is a real practice run — likely via `scripts/tutorial.py quickstart` in
standalone repo mode (no live wiki/PubMed connector needed) — rather than another conceptual
lesson. If a practice run surfaces a real quarantine, a real `status.py --table`, or a real
report/digest pair, that belongs in the next learning record as concrete experience, not
restated theory.

## Open question for next session

Nothing yet exercises the appraisal-tool-by-design table (RoB2 vs. ROBINS-I vs. NOS vs. AMSTAR-2
vs. QUADAS-2 vs. PROBAST vs. CASP vs. JBI) as a skill — Lesson 4 named it but a real appraisal
review would be a good interleaved-practice candidate once a run has produced one.
