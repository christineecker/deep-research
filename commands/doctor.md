Check a standalone repo's stored PDFs and indexes for damage. Read-only. No run needed.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required.
- `--deep` — re-hash every stored asset instead of trusting a matching file size. Slower
  and exact; without it, a file that was modified without changing size is not detected.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" doctor --repo <path> [--deep]
   ```
2. Print the script's own output verbatim, then say plainly whether the library is
   healthy. The command exits non-zero only when something is actually wrong, so it is
   safe to run from cron.
3. Distinguish the two kinds of finding, because they need different responses:
   - **Data loss** — `missing_files`, `corrupt_assets`, `orphan_attachments`,
     `attachments_without_paper`. These make the report unhealthy. A missing or corrupt
     PDF cannot be rebuilt from the registry: it comes back from a backup or a re-import,
     and nothing else will fix it. Say so rather than offering `reindex`.
   - **Stale index** — anything under `indexes` (`papers_fts_missing`, `orphan_chunks`,
     `orphan_terms`, `orphan_fts_rows`). No data is at risk; `/deep-research:reindex` or
     `registry.py reindex --repo <path>` rebuilds it. Offer that.
4. `orphan_assets` are stored bytes that no live attachment references. They are reported
   for visibility, are not a problem, and are not deleted by this command. Do not offer to
   delete them unless the user asks — the bytes are immutable originals by design.
5. If the run was not `--deep`, say so when reporting a clean result: a shallow pass
   trusts file sizes, so "healthy" means "nothing obviously wrong", not "every byte
   verified". Offer `--deep` for the stronger check.

This command never repairs anything. Everything it reports is either rebuildable with
`reindex` or a restore-from-backup decision that belongs to the user.
