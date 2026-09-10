---
name: lightweight-paper-summary-profile
description: New paper.py summarize/summarize-set profiles, and the mission-scope correction they triggered
metadata:
  type: learning-record
---

## What this covers

The skill added `scripts/paper.py summarize`/`summarize-set` (references/single-paper-summary.md,
schema §14/§15) — lightweight profiles for one paper or a small set that reuse the standalone-repo
pool instead of running Stage 0-8. This directly contradicted Lesson 1's original claim ("not for
a single paper") and [[scope-and-profile-shorthand]]'s framing of the mission's scope boundary.

Corrected in this session:
- **Lesson 1** (`0001-what-is-deep-research.html`): the "what it is not" list and its first quiz
  now say "not the full pipeline for a single paper," pointing to the new Lesson 6, instead of
  ruling out single-paper summaries entirely.
- **Lesson 6** (new, `0006-paper-summary-profile.html`): covers `summarize` vs. `summarize-set`,
  pool reuse in both directions (full review → summary and summary → full review), the
  explicit/question/topic entry modes for sets, `--select-only`/`--overview`, and the
  verify.py hard-fail on cross-study/pooled/consensus wording leaking into a summary.
- **MISSION.md**: "Success looks like" no longer claims single-paper summaries are categorically
  out of scope — now states the learner should know *when* to route to the lightweight profile
  vs. the full pipeline. User confirmed this mission edit before it was made.
- **RESOURCES.md**: added `docs/` (a newer static docs site — `index.html`,
  `paper-summaries.html`, etc. — possibly superseding `README.md` as the operator entry point,
  unconfirmed) and `references/single-paper-summary.md`.

## Zone of proximal development notes

This was a correction pass, not new depth — Lesson 6 sits after Lesson 5 in the course but is a
sibling to Lesson 1's scope framing, not a continuation of pipeline mechanics. The course's actual
next step is still what [[full-pipeline-mechanics]] flagged: a real practice run. Lesson 6 gives a
second candidate for that practice run (a `paper.py summarize` call is a much smaller first rep
than a full `tutorial.py quickstart`) — worth offering both options next session rather than
assuming the full pipeline is the only "first practical rep."

## Open question for next session

`docs/` (static HTML site) appeared alongside `tutorials/`'s final deletion and an untracked
`scripts/tutorial.py` change (git status: `tutorials/*.md` all deleted, `docs/` untracked,
`scripts/tutorial.py` modified). Unclear whether `docs/` is the new canonical operator manual
(replacing `README.md`) or a parallel export target. Re-check `README.md` vs. `docs/index.html`
next session before trusting either as *the* primary source citation.
