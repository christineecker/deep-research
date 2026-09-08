# schema.md — JSON contracts (index)

Authoritative data contracts for every deep-research script and subagent. Anything written to
`workspace/`, `outputs/`, `corpus.jsonl`, or `taskboard.jsonl` conforms to one of the records
below. Source of truth for design decisions: `SKILL.md`.

**This file is an index.** Each contract lives in its own file under `references/schema/`, so a
reader who needs one record does not load the other thirteen. A reference of the form
`references/schema.md §7` means: look up §7 here, then open the file it names.

| § | Record | File |
|---|---|---|
| §0 | Shared rules S1-S10 | `references/schema/00-shared.md` |
| §1 | `receipt` | `references/schema/01-receipt.md` |
| §2 | `taskboard record` | `references/schema/02-taskboard.md` |
| §3 | `search result record` | `references/schema/03-search.md` |
| §4 | `corpus record` | `references/schema/04-corpus.md` |
| §5 | `screening verdict` | `references/schema/05-screening.md` |
| §6 | `adjudication record` | `references/schema/06-adjudication.md` |
| §7 | `extraction record` | `references/schema/07-extraction.md` |
| §8 | `appraisal record` | `references/schema/08-appraisal.md` |
| §9 | `verifier result` | `references/schema/09-verifier.md` |
| §10 | `snapshot record` | `references/schema/10-snapshot.md` |
| §11 | `event record` | `references/schema/11-event.md` |
| §12 | `claim span record` | `references/schema/12-span.md` |
| §13 | `assembler result` | `references/schema/13-assembler.md` |
| R1-R24 | Resolutions (contract addenda) | `references/schema/99-resolutions.md` |

## Which file do I actually need?

| If you are | Read |
|---|---|
| A screening subagent | Nothing here — `references/prompts/screen.md` carries the whole contract |
| An extraction or appraisal subagent | Nothing here — the prompt block carries the whole contract |
| Writing or debugging `corpus.py` | §4, §2, §0 |
| Working on the evidence kernel | §10, §11, §12, §13, plus `references/evidence-kernel.md` |
| Working on `verify.py` | §9, §13, §0 |
| Promoting an OKF bundle | §13, §99, plus `references/okf-bundle.md` |

Subagent prompt blocks in `references/prompts/` are complete, verified transcriptions of the
record they produce. A subagent that reads this tree is spending context it should be spending
on its paper.

## Resolutions

The R-numbered addenda in `references/schema/99-resolutions.md` are cross-cutting and bind the
sections listed here:

| Resolutions | Bind |
|---|---|
| R9 | §4 `corpus record` |
| R13, R21 | §4, §10 — the PDF library and `access` vs `evidence_basis` |
| R15, R22, R23 | §11 `event record` — freshness, `register`/`read`, ordering |
| R16, R17, R18 | §7, §12 — spans, derived `quotes[]`, the 2000-character cap |
| R20, R24 | §9, §13 — the gate default and Stage-8 ordering |

Shared rules S1-S10 in §0 bind every record without exception.
