Summarize a bounded set of papers — explicit IDs, or bounded discovery from a
question/topic. This is the "just look at N papers" shortcut.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

**Boundary**: this is the expensive path — it reads and summarizes (optionally
appraises) each paper. For a shelf of bibliographic records only, with no reading, use
`/deep-research:pool-add`. To preview which papers would be picked before paying for
summaries, run first with `--select-only`.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required. If omitted: use cwd if it contains
  `data/papers/registry.jsonl`, else error and point at `/deep-research:init`.
- Set selection — EITHER:
  - discovery: `--question "<text>"` or `--topic "<text>"`, with `--limit <N>`
  - explicit: `--pmid <id>` (repeatable), `--doi <id>`, `--pmcid <id>`,
    `--evidence-id <id>`, `--ids-file <f>`, `--bib <f>`, `--folder <dir> [--recursive]`
- `--select-only` — pick the set, don't summarize (cheap preview).
- `--overview` / `--no-overview` — §15 set manifest, default OFF.
- `--strict` — one bad identifier fails the whole set; default is best-effort.
- Any of the common per-paper flags: `--project <slug>`, `--purpose
  {clinical,methods,journal-club,peer-review,background}` (default `background`),
  `--audience {researcher,clinician,student,grant-writer,general}` (default
  `researcher`), `--appraise`/`--no-appraise` (appraise ON by default), `--reuse`/
  `--no-reuse` (reuse ON by default — pass `--no-reuse` to force fresh work),
  `--force`, `--format md|html|both` (default `md`), `--out <path>`, `--offline`.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/paper.py" summarize-set --repo <path> [--question "..." --limit N | --pmid <id> ...] [other flags]
   ```
2. Print the script's own output verbatim — don't re-narrate it unless the user asks for
   interpretation.
3. Report: N papers summarized, where outputs landed (`--out` or default location).
