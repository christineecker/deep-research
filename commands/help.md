Print this cheat sheet directly. Do not read SKILL.md or run any script.

## `/deep-research:*` commands

| Command | Does | Needs a run? |
|---|---|---|
| `init` | Create a standalone repo (or import from a wiki) | no — this is the prerequisite |
| `pool-add` | Register papers (bibliographic record only, no reading) | no |
| `summarize-set` | Discover/select + read + summarize a bounded set of papers | no |
| `summarize` | Read + summarize exactly one paper | no |
| `bib-export` | Export BibTeX from the registry | no |
| `pdf-lookup` | Check whether a paper is already registered | no |
| `status` | Stage progress + record table for an existing run | yes |
| `verify` | Consistency checks over a run, or one summary/summary-set | yes* |
| `project` | Create/list manuscript projects (appraisals are project-scoped) | no |
| `watch` | Read-only snapshot of a run's progress (`--once`, non-interactive) | yes |
| `search` | Facet/keyword/similarity search over the registry | no |
| `ask` | Answer a question from papers the repo already holds, citing verified spans | no |
| `annotate` | Personal tags/rating/note on a paper (tag/rate/note/show/list) | no |
| `embed` | Build/query the semantic-similarity index (`index`/`similar`/`query`) | no |
| `okf-export` | Promote hand-picked registry papers into an OKF wiki bundle | no* |
| `export` | Publish a ReadCube-importable bundle (RIS + PDFs) from the registry | no |

For the full Stage 0–8 review pipeline (search → screen → extract → appraise →
synthesize → report), use the `deep-research` skill directly (ask for "deep research on
X" or "a literature review") rather than these commands — they're deliberately narrow.

## `pool-add` vs `summarize-set` — which do I want?

- Want a shelf of papers to search/filter/export later, with no reading done yet? →
  `pool-add`.
- Want actual content — a summary, optionally an appraisal, of a bounded set? →
  `summarize-set`.
- Not sure which papers would even be picked, and don't want to pay for summaries yet? →
  `summarize-set --select-only` first, then re-run without it once the set looks right.
- Already know it's exactly one paper? → `summarize` (same flags as `summarize-set`,
  minus the discovery/selection machinery).
- Have a *question* rather than a paper set, and the repo has already read the relevant
  papers? → `ask`. It answers from what is on the shelf, with verified citations; it never
  goes to PubMed, so if the shelf is empty the answer is "nothing here", and a full
  `deep-research` run is what goes looking.

*`verify`'s `single-paper-summary`/`paper-summary-set` modes still require `--run-dir`
even though they check a summary output, not a full pipeline run. `okf-export` needs no
prior full pipeline run either, but each `--evidence-id` must already have an extraction
on file (it synthesizes its own throwaway run directory internally). `export` distinct
from `okf-export`: it publishes a ReadCube import bundle to a local folder, not an OKF
wiki; it never touches wiki state and needs no extraction on file — metadata-only
records export too (with a warning, or blocked if `--require-pdfs`).

## Everything needs `--repo`

Every command above except `status`, `verify`, and `watch` takes `--repo <path>` and
fails without a `data/papers/registry.jsonl` already in place. Run
`/deep-research:init <path>` once first. `status`/`verify`/`watch` key off `--run-dir`
instead, which only exists once a full Stage 0–8 pipeline run has been started.
