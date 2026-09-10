# deep-research (skill) Resources

## Knowledge

- [`SKILL.md`](/Users/sphache/.claude/skills/deep-research/SKILL.md)
  The agent-facing router — primary source of truth for stage mechanics, profiles, invariants,
  delegation rules. Use for: anything about *how the skill actually runs internally*.
- [`README.md`](/Users/sphache/.claude/skills/deep-research/README.md)
  The human-facing operator manual — written for the person running the skill, not the agent.
  Use for: "how do I invoke this / what do I need to answer / what are my two modes."
- [`references/` directory](/Users/sphache/.claude/skills/deep-research/references/)
  Per-topic deep references: `search-strategy.md`, `acquisition.md`, `appraisal.md`,
  `synthesis.md`, `reporting.md`, `pool-architecture.md`, `evidence-kernel.md`,
  `question-frameworks.md`, `schema.md`. Use for: any single stage in depth.
- [`scripts/tutorial.py`](/Users/sphache/.claude/skills/deep-research/scripts/tutorial.py)
  Guided local tutorial script (replaced the old static `tutorials/` markdown walkthrough,
  which the repo has since deleted). Run `python3 scripts/tutorial.py quickstart --repo
  /tmp/deep-research-tutorial-demo` for a hands-on standalone-repo-mode practice run without a
  live wiki or PubMed connector; `build-site --out exports/html/tutorials` renders static HTML
  versions. Use for: first real end-to-end practice run.
- [`docs/` directory](/Users/sphache/.claude/skills/deep-research/docs/)
  Newer human-facing static docs site (`index.html`, `quickstart.html`, `pipeline.html`,
  `appraisal.html`, `commands.html`, `architecture.html`, `paper-summaries.html`) — appears to
  be replacing/supplementing `README.md` as the operator-facing entry point. Worth checking each
  session whether `README.md` still tracks it or has been superseded.
- [`references/single-paper-summary.md`](/Users/sphache/.claude/skills/deep-research/references/single-paper-summary.md)
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
