Promote hand-picked registry papers into an OKF wiki bundle. No prior full pipeline run
needed — but each paper must already have an extraction on file.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

**Prerequisite**: every `--evidence-id` must already resolve to a registry record with an
`extraction_path` on disk. Run `/deep-research:summarize` (or the full Stage 0-8 pipeline)
on a paper first if it hasn't been extracted yet; `okf-export` will refuse and list which
evidence_ids are missing an extraction rather than partially writing anything.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required.
- `--evidence-id <id>` — required, repeatable (one per paper to export).
- `--wiki <wiki-root>` — required; must already be `okf.py init`-ed.
- `--project <slug>` — optional; also carries that project's appraisal for each paper.
- `--no-keep-run` — delete the synthetic run directory after promotion (default: kept, as
  a legitimate run record).

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/research.py" okf-export --repo <path> --evidence-id <id> [--evidence-id <id> ...] --wiki <wiki-root> [--project <slug>] [--no-keep-run]
   ```
2. Print the script's own output verbatim.

**Deviations to expect and not treat as failure**: this synthesizes a throwaway run
directory (no real screening/PRISMA history) and runs the existing `verify.py`/`okf.py
promote` over it unmodified. Pipeline-shaped checks like `C-SEARCH-LOG` and `C-PRISMA`
routinely fail for a registry-only export — that's expected, not a bug. `okf-export`
therefore calls `okf.py promote` with `--force --allow-unverified`, and every concept it
promotes lands with `status: provisional`, never `stable`. The concepts themselves still go
through `okf.py promote`'s full V1-V25 validator unmodified — only the pipeline-completeness
gate is bypassed, not concept-level correctness.
