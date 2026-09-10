Summarize exactly one paper (§14 record + export).

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required. If omitted: use cwd if it contains
  `data/papers/registry.jsonl`, else error and point at `/deep-research:init`.
- One identifier: `--pmid <id>` | `--doi <id>` | `--pmcid <id>` | `--pdf <path>` |
  `--evidence-id <id>`.
- Optional: `--title <text>` (fallback title, used with `--pdf`/`--doi` when metadata
  can't be resolved), `--project <slug>`, `--purpose
  {clinical,methods,journal-club,peer-review,background}` (default `background`),
  `--audience {researcher,clinician,student,grant-writer,general}` (default
  `researcher`), `--appraise`/`--no-appraise` (appraise ON by default), `--reuse`/
  `--no-reuse` (reuse ON by default — pass `--no-reuse` to force fresh work),
  `--force` (regenerate even if reusable artifacts exist), `--format md|html|both`
  (default `md`), `--out <path>`, `--offline`.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/paper.py" summarize --repo <path> --pmid <id> [other flags]
   ```
2. Print the script's own output verbatim — don't re-narrate it unless asked to
   interpret.

For more than one paper, use `/deep-research:summarize-set` instead — same flags, plus
bounded discovery.
