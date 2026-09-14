Delete `runs/<slug>/` directories left over from `/deep-research:summarize`/
`summarize-set`/`extract` once their paper data is safely promoted into the registry —
the repo's "common knowledge pool." Dry-run by default; deletion is a separate,
explicitly-confirmed step.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

**Scope, and why it stops there**: only `single-paper-summary`/`summarize-set` run
directories are ever considered. A full Stage 0-8 review run's `runs/<slug>/` holds the
report, PRISMA counts, and screening decisions — none of that has a registry equivalent,
so this command never touches a review run, with no flag that overrides that. Clean up a
finished review run by hand if it's genuinely done with.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required. If omitted: use cwd if it contains
  `data/papers/registry.jsonl`, else error and point at `/deep-research:init`.
- `--apply` — actually delete. Without it, only the classification report is produced and
  nothing is touched.
- `--include-unsaved` — also delete runs whose only issue is a write-up or appraisal that
  was never copied to a project (the paper data itself is already safe in the registry
  either way). Off by default.
- `--limit <n>` — cap how many run directories are deleted in one call.

Steps:
1. Print, then run — **always without `--apply` first**, even if the user asked to clean
   up directly:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/research.py" gc-runs --repo <path> [--include-unsaved]
   ```
2. Every entry in `candidates` carries a `tier`:
   - **`safe`** — nothing in this run isn't already durable elsewhere. Deletable with
     `--apply` alone.
   - **`unsaved`** — the paper data (extraction, and appraisal if any) is promoted and
     safe, but a rendered write-up or an unprojected appraisal exists only in this run
     and would be lost. Deletable only with `--apply --include-unsaved`.
   - **`blocking`** — an unfinished taskboard task, or an extraction that exists in this
     run's workspace but is *not* (yet) promoted into the registry. Never deletable, no
     flag overrides this.

   Summarize the report in plain text: counts per tier, total bytes the `safe` (and, if
   asked about, `unsaved`) candidates would free, and list `blocking` run dirs with their
   `blocking_reasons` so the user knows what's actually still in flight.
3. This is a destructive, hard-to-reverse action (`rm -rf` per directory). Before running
   with `--apply`, show the dry-run report and get explicit confirmation — which tier(s)
   to delete, and whether `--include-unsaved` is wanted — even if the user's original
   request sounded unconditional ("clean up finished runs"). Do not chain straight from
   step 1 into `--apply` in the same turn.
4. Once confirmed, run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/research.py" gc-runs --repo <path> --apply [--include-unsaved] [--limit <n>]
   ```
   Print the script's own output verbatim — `deleted` (the run dirs actually removed) and
   `freed_bytes`.

An `unsaved` write-up is not gone for good — re-running `/deep-research:summarize` for
that evidence_id regenerates it from the still-promoted extraction/appraisal (one
subagent call, not a re-fetch). An unprojected appraisal with no `--project` on the
original run has no promotable home and would need a fresh appraisal subagent if it's
ever wanted again — say so plainly if a candidate's `unsaved_reasons` names one.
