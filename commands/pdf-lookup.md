Check whether a paper is already registered, before re-fetching it.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required.
- One of `--pmid <id>` | `--doi <id>` | `--pmcid <id>` | `--evidence-id <id>`.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" lookup --repo <path> (--pmid|--doi|--pmcid|--evidence-id) <value>
   ```
2. Print the script's own output verbatim. If nothing is found, say so plainly — that's
   the answer, not an error.

This is a read-only check — it never fetches anything. If the paper isn't registered
yet, or is registered but has no extraction (`extraction_status: not_started`), point at
`/deep-research:pool-add` (register only) or `/deep-research:extract` (fetch full text
and produce a verified extraction) rather than re-implementing the fetch here.
