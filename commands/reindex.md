Rebuild the standalone repo's derived search indexes. Safe to re-run. No run needed.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required.
- `--papers-only` — mirror papers/identifiers/terms into refmgr, skip the full-text
  chunk index.
- `--chunks-only` — rebuild the full-text chunk index only.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" reindex --repo <path> [--papers-only|--chunks-only]
   ```
2. Print the script's own output verbatim, then summarize: how many records were
   mirrored, how many snapshots were indexed (vs already current), and the resulting
   coverage numbers.

When this is the right command:
- The repo has papers registered from before the refmgr mirror existed — `papers.mirrored`
  will be non-zero and search facets start working.
- `/deep-research:doctor` reported stale or orphaned index rows.
- The refmgr database was deleted or corrupted. Everything here is derived from
  `registry.jsonl` and the snapshot store, so it is rebuilt, not restored.
- Full-text search is missing papers you know have text: `coverage.chunks` shows how many
  papers actually have indexed text.

Nothing is at risk: the registry and the snapshots are the source of truth, an unchanged
snapshot is skipped rather than re-split, and search falls back to a full scan whenever
the index is absent. If `papers.errors` is non-empty, name the records that failed and
what the error was — those are the ones that will still be missing from search.
