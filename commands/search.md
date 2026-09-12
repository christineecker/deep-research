Search the registry: facet filters, keyword search, or similarity search. No run needed.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required.
- `--journal <substr>` — case-insensitive substring match against journal.
- `--year <YYYY>|<YYYY-YYYY>` — matched against `publication_date`.
- `--status registered|screening|included|excluded`
- `--extraction-status not_started|in_progress|extracted`
- `--appraisal-status not_appraised|in_progress|appraised`
- `--tag <tag>` — requires this annotation tag (`data/papers/annotations.jsonl`).
- `--min-rating N` — requires an annotation star rating >= N.
- `--project <slug>` — scopes `--appraisal-status appraised` and `--q`'s appraisal-rationale
  search to this project's own appraisal entry.
- `--q "<text>"` — keyword search: lowercase AND-of-terms over title/abstract/journal/
  extraction narrative/appraisal rationale/full text.
- `--similar-to <evidence-id>` — rank surviving results by cosine similarity to this
  evidence_id. **Requires `embeddings.py index` to have already been run** for this repo
  (`/deep-research:embed index`) — if no embeddings file exists yet, the script will say so;
  point the user at that command rather than treating it as an error.
- `--limit N` — cap the number of results (default: all).

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" search --repo <path> [--journal <substr>] [--year <spec>] [--status <s>] [--extraction-status <s>] [--appraisal-status <s>] [--tag <tag>] [--min-rating N] [--project <slug>] [--q "<text>"] [--similar-to <evidence-id>] [--limit N]
   ```
2. Print the script's own output verbatim. If nothing matches, say so plainly — that's the
   answer, not an error.
