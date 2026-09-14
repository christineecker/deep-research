Fetch full text (the acquisition ladder) and produce a verified, promoted extraction —
one paper or a bounded set. No appraisal, no summary, no narrative text at all.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

**Boundary**: this is the "just get the structured data into the registry" shortcut —
narrower than `/deep-research:summarize`/`summarize-set`, which also appraise and write a
narrative summary. Use `extract` when the only goal is a paper's `data/papers/extractions/`
record (for `/deep-research:search --q`, `/deep-research:ask`, or a later
`/deep-research:okf-export`), and nothing downstream needs an appraisal or a reader-facing
write-up yet. `/deep-research:pool-add` is narrower still — bibliographic metadata only, no
full text touched at all.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required. If omitted: use cwd if it contains
  `data/papers/registry.jsonl`, else error and point at `/deep-research:init`.
- One paper — any one of `--pmid <id>` | `--doi <id>` | `--pmcid <id>` | `--pdf <path>` |
  `--evidence-id <id>` — OR a bounded set, same selection surface as
  `/deep-research:summarize-set`:
  - discovery: `--question "<text>"` or `--topic "<text>"`, with `--limit <N>`
  - explicit: `--pmid <id>` (repeatable), `--doi <id>`, `--pmcid <id>`,
    `--evidence-id <id>`, `--ids-file <f>`, `--bib <f>`, `--folder <dir> [--recursive]`
- `--reuse` / `--no-reuse` — reuse ON by default (an existing verified extraction is not
  redone).
- `--force` — regenerate even if a reusable extraction exists.
- `--offline` — skip network acquisition; local rungs / reuse only.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/paper.py" summarize-set --repo <path> --extract-only [--question "..." --limit N | --pmid <id> ... | --doi <id> | --pmcid <id> | --evidence-id <id> | --ids-file <f> | --bib <f> | --folder <dir> [--recursive]] [--reuse|--no-reuse] [--force] [--offline]
   ```
   A single paper works the same way — pass exactly one `--pmid`/`--doi`/`--pmcid`/
   `--evidence-id` (or `--pdf` via `paper.py summarize --pdf <file> --extract-only`, since
   `summarize-set` has no `--pdf` selection source).
2. `status` per paper follows the same resumable loop as every other script here:
   `pending_retrieval` (dispatch nothing yet — acquisition hasn't found text; re-run once
   full text is available), `pending_extraction` (dispatch a Stage-5 extraction subagent,
   `references/prompts/extract.md`, output to the path the payload names, then re-run this
   same command), `completed` (the extraction verified and was promoted into
   `data/papers/extractions/` — done, nothing else to dispatch).
3. Print the script's own output verbatim. For a set, report: how many completed, how
   many are still pending a subagent (and which stage), how many failed outright.

This never appraises and never writes a summary — `appraisal_status` stays
`not_appraised` and no `workspace/summaries/` file is produced, even with `--project` set.
Run `/deep-research:summarize`/`summarize-set` afterward (reuse picks the extraction back
up, no rework) if a narrative write-up or appraisal is wanted later.
