# deep-research — usage

Operator's manual. `SKILL.md` is the agent-facing router.
This file is for the person running the skill.

## 1. What it does

PubMed-centred literature research, end to end: writes a protocol, designs and logs several
orthogonal PubMed queries, screens titles/abstracts against numbered criteria, walks an
open-access ladder to get *full text* (not abstracts), extracts study data one paper at a time,
critically appraises each study (RoB2 / ROBINS-I / Newcastle-Ottawa / AMSTAR-2, then GRADE
domains), synthesises direction and conflict, and runs a verifier over the finished report.
Deliverables land in your wiki, with a PRISMA-style count and a citation audit.

What it is **not**:

- It is **not a meta-analysis**. It does not pool effect estimates, does not compute I², does
  not produce forest plots. Synthesis is effect-direction tabulation plus explained conflict.
- It **does not circumvent paywalls**. No credentials, no institutional proxies, no browser
  automation for access, no pirate mirrors. The OA ladder is the whole ladder; anything else is
  quarantined and handed back to you.
- It is not for summarising one paper, and not a general web research tool.

## 2. How to invoke it

Ask in the session. The skill triggers on: "literature review", "systematic review", "evidence
synthesis", "what does the evidence say about X", "deep research on X", "PubMed search",
"critical appraisal", or a request to have papers screened / extracted / appraised / written up
with citations.

At stage 0 it asks only for what it does not already know:

| Asked | Detail |
|---|---|
| Question | Restated back to you as PICO/PECO before anything runs |
| Profile | `fast` / `standard` / `systematic` / `max`, or scope / rigor / gates individually |
| Target wiki | Needed early — the run directory and PDF library live inside it. The wikis directory is enumerated at runtime; a missing wiki is confirmed with you before creation |
| Filters | Years, authors, journals, article types, species/age, language, OA-only |
| Outputs | `report.md` always; optionally HTML artifact, Quarto PDF/docx, OKF bundle promotion |

Values already in the run's `config.json` are never re-asked unless you say to change them.

## 3. Profiles

| Profile | scope | rigor | gates | max_articles | For |
|---|---|---|---|---|---|
| `fast` | narrow | fast | none | 10 | A quick read of the field. PubMed MCP only, report only, no stop-and-confirm |
| **`standard`** (default) | medium | standard | protocol+strategy | 25 | The normal case. Adds E-utilities for exact boolean/MeSH and hit counts; report + OKF bundle when a wiki is chosen |
| `systematic` | wide | systematic | both | 60 | A defensible review. Adds Europe PMC (incl. preprints), dual independent screening + adjudicator, PRISMA log, full verifier pass, optional Quarto export |
| `max` | max | systematic | both | 100 | Widest sweep: adds guidelines, grey literature and web connectors, with source-type tagging and connector authorization checks |

`gates` = where the run stops for your approval: `protocol+strategy`, `screening`, `both`, or
`none`. `max_articles` is the post-screening cap; the report states how the cap was applied.
Profiles are overridable field-by-field in the run's `config.json`.

## 4. Where things live

```
<wiki>/outputs/deep-research/<slug>/   # the run
  config.json  engine.log  taskboard.jsonl  corpus.jsonl
  inputs/      workspace/   outputs/
  missing.md   inbox/
<wiki>/assets/papers/                  # shared PDF library, all runs (rung 0)
  index.json                           # versioned; the PDFs themselves are gitignored
<wiki>/research/                       # the OKF bundle (promoted concepts)
  index.md  log.md  studies/  claims/  appraisals/  gaps/  hypotheses/  …
```

The skill directory itself stays code-only; data lives in the wiki, which is iCloud-synced.

**`<wiki>/wiki/` is never written to.** That bundle belongs to wiki-manager (OKF 0.1).
deep-research owns `<wiki>/research/` (OKF 0.2-style, its own frontmatter convention, spec in
`references/okf-bundle.md`). Cross-links from `wiki/` into `research/` are yours to make.

Run `python3 scripts/library.py init --wiki <root>` once per wiki; it creates
`assets/papers/` and rewrites the `.gitignore` rule so `index.json` is versioned and the PDFs
are not.

## 5. Outputs

| Output | Command | Good for |
|---|---|---|
| `outputs/report.md` | always written by the synthesis stage | The primary deliverable: evidence, conflicts, certainty, gaps, hard-walled hypotheses, footnote citations |
| `outputs/report.html` | `html_report.py build --run-dir <dir>` | A self-contained page to read or share: evidence table, effect-direction chart, per-study cards, gaps shown honestly. `--no-external-links` strips every URL |
| `outputs/prisma.{json,md}` | `corpus.py prisma --run-dir <dir> --out` | The flow numbers: identified → deduped → screened → included, and dual-screen agreement |
| PDF / docx | `render.py all --run-dir <dir> --formats pdf,docx` | A citable document. Generates `refs.bib` + `report.qmd`, then `quarto render` |
| `outputs/verification.json` | `verify.py run --run-dir <dir> [--wiki <root>]` | The audit: citations resolve, records complete, hypotheses not phrased as findings |
| `<wiki>/research/` concepts | `okf.py promote --run-dir <dir> --wiki <root>` | Durable, linkable wiki knowledge that outlives the run directory |

`render.py` can also emit HTML via Quarto (`render.py html`); that is the paper-style export.
`html_report.py` is the richer standalone artifact. Export failure never destroys
`outputs/report.md` — it is preserved and the failure goes to `engine.log`.

## 6. Quarantine → inbox → resume

The workflow you will actually use. When a paper's full text is not obtainable through the OA
ladder, the run does not stall — it quarantines and keeps going.

1. **The run alerts you** with a count and the path to `<run-dir>/missing.md`, and marks the
   synthesis provisional.
2. **You read `missing.md`.** Each block has the title, evidence_id, PMID, DOI, PMCID, journal,
   which rungs were attempted, and direct PubMed / DOI / PMC links to fetch it yourself.
3. **You drop the PDFs into `<run-dir>/inbox/`.** Any filenames; no renaming needed.
4. **You rerun the skill** (or run `library.py ingest-inbox --run-dir <dir>` directly).
5. Matching: page-1 `pdftotext` output → DOI regex `10\.\d{4,}/\S+` → exact DOI match against
   the corpus; failing that, fuzzy title match against quarantined records (normalized title
   slid across page-1 text, ratio ≥ 0.85; titles under 25 normalized characters are never
   fuzzy-matched). Unmatched PDFs stay in `inbox/` and are listed with a reason — never guessed
   onto a record.
6. The PDF is filed into `<wiki>/assets/papers/` (sha256 dedupe), `index.json` and
   `corpus.jsonl` are updated (`source_tier: 0`, `access_route: inbox_manual`), the record's
   block is removed from `missing.md`, and the text is extracted to `workspace/fulltext/`.
7. The run extracts, appraises and **re-synthesises** the newly available studies.

**The report records which studies arrived by manual supply.** Use `--no-apply` on
`ingest-inbox` to see the proposed matches without rewriting `corpus.jsonl`.

## 7. Resuming an interrupted run

Resume is **by task, not by stage**. Every unit of work is a record in `taskboard.jsonl`; a
rerun reads `config.json` + the taskboard and continues only the incomplete, failed and blocked
tasks. It does not restart the last stage, does not redo completed work, and **does not
re-prompt for anything already in `config.json`**. Completed outputs are immutable unless their
`inputs_hash` changes.

Point the skill at the same question/wiki, or just say "resume". Useful probes:

```bash
python3 scripts/corpus.py task stats  --run-dir <dir>
python3 scripts/corpus.py task next   --run-dir <dir> --stage extract
python3 scripts/fulltext.py status    --run-dir <dir>
python3 scripts/corpus.py validate    --run-dir <dir>
```

Never hand-edit `taskboard.jsonl`; `corpus.py task` is its only writer.

## 8. Script reference

All under `scripts/`, all `python3`.

| Script | Purpose | Subcommands |
|---|---|---|
| `eutils.py` | NCBI E-utilities client: hit counts, NCBI-translated query, PMIDs, normalized bibliographic JSON, citation chaining. Throttled and retried | `esearch`, `efetch`, `elink` |
| `fulltext.py` | The acquisition ladder (rungs 0–7), resumable, records `source_tier` + `access_route`, HTML truncation detector, quarantine | `acquire`, `status`, `resolve-mcp` |
| `library.py` | The shared PDF library at `<wiki>/assets/papers/`: index, sha256/DOI/PMID/PMCID/fuzzy-title matching, inbox ingestion | `init`, `lookup`, `add`, `ingest-inbox`, `list` |
| `corpus.py` | `corpus.jsonl` store, dedupe, PRISMA counters, screening ingestion, duplicate-query guard, no-progress guard, and the taskboard CLI | `init`, `add`, `list`, `get`, `export`, `dedupe`, `validate`, `prisma`, `screen-ingest`, `disagreements`, `query-check`, `query-register`, `guard`, `task` |
| `okf.py` | OKF bundle writer and validator for `<wiki>/research/` (enforces V1–V25) | `init`, `write`, `promote`, `validate`, `selftest` |
| `render.py` | `report.md` → `refs.bib` + `report.qmd` → `quarto render` | `bib`, `qmd`, `pdf`, `docx`, `html`, `all` |
| `html_report.py` | Self-contained HTML deliverable from a run directory | `build` |
| `verify.py` | Final consistency pass; writes `outputs/verification.json`, never edits `report.md`. Exit 0 = no failures (warnings allowed), 1 = a check failed, 2 = fatal | `run` |

`corpus.py task` sub-subcommands: `create`, `claim`, `complete`, `fail`, `block`, `cancel`,
`reopen`, `list`, `next`, `show`, `stats`.

Verifier checks: `C-CITE-RESOLVE`, `C-CORPUS-COMPLETE`, `C-SEARCH-LOG`, `C-RETRACTION`,
`C-FULLTEXT`, `C-HYPOTHESIS-WALL`, `C-PRISMA`, `C-PREPRINT`, `C-ATTRIBUTION`, `C-SECTIONS`,
`C-PROVISIONAL`, `C-OKF` (skipped unless `--wiki` is given).

## 9. Requirements

- **`python3`** — there is no `python` on this machine; every script and shebang uses `python3`.
- **Python packages**: stdlib + `requests` + `pdfminer` only. **No pip installs, ever.**
  `lxml`, `bs4`, `PyYAML` etc. are absent by design — XML is parsed with `xml.etree`, YAML
  frontmatter with `okf.py`'s own serializer (`okf.py selftest` round-trips it).
- **Binaries**: `pdftotext`, `pdfinfo` (text extraction and PDF metadata), `tesseract` (OCR
  fallback when a PDF yields under ~100 characters), `quarto` + `pandoc` (PDF/docx export).
  `ocrmypdf` is not installed and not used.
- **`NCBI_API_KEY`** (optional) — 3 requests/s without it, 10 with.
- **`DEEP_RESEARCH_EMAIL`** (required by the Unpaywall rung; also sent as `email=` to NCBI per
  their policy). Overridable per call with `--email`.
- `DEEP_RESEARCH_FIXTURES` / `DEEP_RESEARCH_RECORD` — offline replay and recording for
  `eutils.py`.

## 10. Limitations, honestly

- **`max` scope depends on connectors that may not be attached.** Scholar Gateway and Consensus
  are the extra sources `max` adds; everything below it (`narrow`/`medium`/`wide`) runs on the
  PubMed MCP, `eutils.py` and Europe PMC, which need no connector. The skill checks its own tool
  list at Stage 0, records the result in `config.json` under `connectors`, and if one is missing
  it names the server, tells you to authorize it in claude.ai → Settings → Connectors, and asks
  whether to continue at reduced scope or stop. Two consequences worth knowing: a connector you
  authorize mid-run is not picked up until a **new** session, because MCP servers attach at
  session start; and a `max` run that could not reach both connectors searched a `wide` set, so
  the report's Methods section says exactly that. It will not silently skip them or pretend the
  coverage happened.
- **The hypothesis-wall check is lexical and structural, not semantic.** `C-HYPOTHESIS-WALL`
  matches banned phrasings on each side of the evidence/hypothesis boundary and flags unhedged
  declaratives; it warns, it does not prove. A `PASS` means "no banned phrasing found", not
  "the wall holds". Same register applies to the other verifier checks: they catch
  inconsistency, not wrongness.
- **No pooled estimates.** No meta-analytic summary effect, no I², no funnel plot statistics.
  Publication-bias discussion is qualitative.
- **Abstract-only evidence is labelled and never appraised as full text.** Records with
  `evidence_basis: abstract_only` carry that label through extraction, appraisal and the report.
- **Rung 1 of the ladder needs a coordinator MCP call.** A script cannot call the PubMed MCP
  `get_full_text_article` tool. `fulltext.py` writes a `needs_mcp` task to
  `workspace/retrieve/mcp-tasks.jsonl` and keeps walking the ladder, so the run never blocks;
  the agent makes the call and hands the text back via `fulltext.py resolve-mcp` (or a re-run of
  `acquire`, which picks up the saved file).
- **HTML full-text routes can be truncated.** The truncation detector (body under 1500 words,
  or paywall markers such as "Access options" / "Purchase" / "Sign in to view") demotes those to
  abstract-only rather than treating a teaser as a paper.
- **Preprints are included at `wide`/`max` and tagged loudly** — their content differs from the
  published version.

## 11. Testing

`scripts/eval.py` runs fixture-backed evals offline by default, with `--live` opting in to
real PubMed. `python3 -m unittest discover -s tests` runs the unit suite: store/source/assemble
contracts, the validation pipeline, and the concurrency invariants for `acquire --workers`.
Neither needs credentials or the network.

What works today for offline testing:

```bash
DEEP_RESEARCH_FIXTURES=<dir> python3 scripts/eutils.py esearch --query '...'   # replay
DEEP_RESEARCH_RECORD=<dir>   python3 scripts/eutils.py esearch --query '...'   # record
python3 scripts/fulltext.py acquire --run-dir <dir> --corpus <path> --offline  # local rungs only
python3 scripts/okf.py selftest
python3 scripts/okf.py validate --wiki <root> --run-dir <dir>
python3 scripts/corpus.py validate --run-dir <dir>
python3 scripts/verify.py run --run-dir <dir> --wiki <root> --json
```
