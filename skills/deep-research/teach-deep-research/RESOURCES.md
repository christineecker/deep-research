# deep-research (skill) Resources

Repo root is `/Users/sphache/.claude/skills/deep-research` — since the plugin restructure
(2026-09-10) it holds `commands/`, `.claude-plugin/`, and `skills/deep-research/` (the skill
itself, one level down: `SKILL.md`, `scripts/`, `references/`, `docs/`, this teaching
workspace). Paths below reflect that; if a path 404s, the layout has moved again — check
`README.md` at the repo root first.

## Knowledge

- [`skills/deep-research/SKILL.md`](/Users/sphache/.claude/skills/deep-research/skills/deep-research/SKILL.md)
  The agent-facing router — primary source of truth for stage mechanics, profiles, invariants,
  delegation rules. Use for: anything about *how the skill actually runs internally*.
- [`README.md`](/Users/sphache/.claude/skills/deep-research/README.md)
  The human-facing operator manual at the repo root — written for the person running the
  skill, not the agent. Now leads with slash commands vs. the full pipeline. Use for: "how do
  I invoke this / what do I need to answer / what are my modes."
- [`commands/` directory](/Users/sphache/.claude/skills/deep-research/commands/)
  11 slash-command specs (`init`, `pool-add`, `summarize`, `summarize-set`, `bib-export`,
  `pdf-lookup`, `status`, `verify`, `project`, `watch`, `help`) — each calls one script
  directly (`${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/*.py`), bypassing SKILL.md's
  staged Stage 0-8 narrative. Use for: any single narrow ask that isn't a full review. Use
  for: Lesson 7.
- [`references/` directory](/Users/sphache/.claude/skills/deep-research/skills/deep-research/references/)
  Per-topic deep references: `search-strategy.md`, `acquisition.md`, `appraisal.md`,
  `synthesis.md`, `reporting.md`, `pool-architecture.md`, `evidence-kernel.md`,
  `question-frameworks.md`, `schema.md`. Use for: any single stage in depth.
- [`scripts/tutorial.py`](/Users/sphache/.claude/skills/deep-research/skills/deep-research/scripts/tutorial.py)
  Guided local tutorial script. Run `python3 skills/deep-research/scripts/tutorial.py
  quickstart --repo /tmp/deep-research-tutorial-demo` for a hands-on standalone-repo-mode
  practice run without a live wiki or PubMed connector; `build-site --out
  exports/html/tutorials` renders static HTML versions. Use for: first real end-to-end
  practice run.
- [`docs/` directory](/Users/sphache/.claude/skills/deep-research/skills/deep-research/docs/)
  Human-facing static docs site (`index.html`, `quickstart.html`, `pipeline.html`,
  `appraisal.html`, `commands.html`, `architecture.html`, `paper-summaries.html`) —
  `README.md` now links out to these per-topic rather than duplicating them.
- [`references/single-paper-summary.md`](/Users/sphache/.claude/skills/deep-research/skills/deep-research/references/single-paper-summary.md)
  Command reference for the lightweight `scripts/paper.py summarize`/`summarize-set` profiles
  (reuses the standalone-repo pool, skips the full Stage 0-8 pipeline). Use for: Lesson 6.

## Wisdom (Communities)

- No community identified yet — this is a locally-authored Claude Code skill, not a public
  library with a user base. Real-world testing ground is: running it for real, then bringing
  questions/surprises back to this teaching session.

## Gaps

- No external community/forum for this specific skill (expected — it's bespoke to this
  environment). Wisdom here comes from practice runs + reviewing run outputs together, not from
  joining a group.
