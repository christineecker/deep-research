Report registered papers with no PDF/full-text asset in the repo, as a table with a link
a human can click to go get the paper manually. Read-only. No run needed.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required. If omitted: use cwd if it contains
  `data/papers/registry.jsonl`, else error and point at `/deep-research:init`.
- `--status registered|screening|included|excluded` — optional, narrows to records at
  that lifecycle status (e.g. `--status included` to only chase papers actually kept).
- `--limit <n>` — optional cap.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/registry.py" missing-fulltext --repo <path> [--status <s>] [--limit <n>]
   ```
2. If `count` is 0, say so plainly — every registered paper already has an asset — and
   stop; don't print an empty table.
3. Otherwise render `results` as a Markdown table, one row per paper:

   | Title | Journal (Year) | Link | Status |
   |---|---|---|---|

   - **Link** — `doi_url` when present (`[10.1001/jama.2023.24567](https://doi.org/...)`
     — show the DOI as the link text, not the bare URL); else `pubmed_url` when present
     (`[PubMed](https://pubmed.ncbi.nlm.nih.gov/.../)`); else literally `no DOI/PMID — see
     evidence_id` and print the raw `evidence_id` next to it, since there is nothing to
     link to.
   - **Status** — the record's `status` field (`registered`/`screening`/`included`/
     `excluded`), so the user can tell a still-in-scope gap from one that no longer
     matters.
   - Sort the table by journal then title, not by `evidence_id` — this is for a human to
     scan, not a machine.
4. Close with the count and a one-line reminder: these are candidates for manual
   retrieval — download the PDF, then `registry.py add-pdf --repo <path> --file <pdf>
   --doi <doi>` (or `--pmid`) attaches it to the *same* record by identifier match, it
   does not create a duplicate.

This is a read-only report over `asset_status` — the field every intake path already
maintains (`add-pdf`/`import-folder`/an acquisition-ladder rung that lands a PDF set it
to `available`; nothing here changes it). It does not attempt any retrieval itself, and
it does not distinguish "never tried" from "every automated rung failed" — that detail
lives per-run in `fulltext.py status`/`/deep-research:status --missing`, not in the
registry. A paper can be `extraction_status: extracted` and still show up here if it was
extracted from an abstract only (`evidence_basis: abstract_only`) with no PDF ever
stored — that is expected, not a bug.
