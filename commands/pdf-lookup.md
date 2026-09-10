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
