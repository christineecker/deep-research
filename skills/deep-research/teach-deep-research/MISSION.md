# Mission: deep-research skill (functionality and usage)

## Why
Christine wants to be able to competently invoke and operate the `deep-research` Claude Code
skill — a PubMed-centred literature research pipeline (protocol → search → screen → retrieve →
extract → appraise → synthesize → verified report). The goal is operator fluency: knowing when
to trigger it, what it asks, what each stage does and produces, and how to read/steer its
output — so that when a real literature question comes up, the tool is not a black box.

## Success looks like
- Can state what `deep-research` does and does not do (not a meta-analysis tool, not a paywall
  bypass) — and, since the skill added `scripts/paper.py summarize`/`summarize-set`, can also
  say when a single-paper or small-set ask should route to that lightweight profile instead of
  the full Stage 0-8 pipeline.
- Since the repo became a plugin with 11 `/deep-research:*` slash commands (2026-09-10), can
  pick the narrow command for a single ask (`init`, `pool-add`, `summarize`, `summarize-set`,
  `bib-export`, `pdf-lookup`, `status`, `verify`, `project`, `watch`) instead of invoking the
  full pipeline for it — and knows the full pipeline is still what to ask for when the request
  is a genuine multi-paper review, not a command gap.
- Can correctly answer Stage 0's setup questions (question framework, profile, wiki vs.
  standalone repo target, filters) for a real question.
- Can name all 8 pipeline stages in order and what each one writes.
- Understands profile tradeoffs (`fast`/`standard`/`systematic`/`max`) well enough to pick the
  right one for a given need.
- Can read a run's output (`report.md`, `status.py --table`, quarantine/`missing.md`) and know
  what to do next.

## Constraints
- Session-based learning (multi-session), self-paced.
- Learner already works inside Claude Code / this skill ecosystem daily — comfortable with
  CLI, JSON, git. No need to over-explain tooling basics.
- Caveman-mode is active in this Claude Code session for chat, but lessons/reference docs are
  written in normal prose (per session convention: code/docs stay normal).

## Out of scope (for now)
- Deep dives into RoB2/ROBINS-I/GRADE appraisal methodology itself (that's epidemiology
  knowledge, not skill-usage — revisit only if the mission shifts toward "run defensible
  systematic reviews," not just "operate the tool").
- wiki-manager internals beyond what's needed to answer "wiki or repo target?".
- Modifying/extending the skill's own code (`scripts/*.py`) — this is a user-of-the-skill
  mission, not a maintainer-of-the-skill mission.
