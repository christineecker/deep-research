# deep-research — usage

`SKILL.md` is the agent-facing router. This file is the short version for a human. Full
docs: [`docs/index.html`](docs/index.html).

## What it does

PubMed-centred literature research, end to end: protocol → search → screen → full-text
acquisition → extraction → appraisal → synthesis → verified report, promotable into a wiki.

**Not**: a meta-analysis (no pooled effects, no I², no forest plots), a paywall bypass (no
credentials, no scraping — unobtainable text is quarantined and handed back to you), or
general web research.

## Install

This repo is a Claude Code plugin.

- **Local dev**: `claude --plugin-dir /path/to/deep-research`
- **From a marketplace**:
  ```
  /plugin marketplace add https://forgejo.sphache.synology.me/sphache/deep-research.git
  /plugin install deep-research@deep-research
  ```
  (this repo is its own marketplace — `.claude-plugin/marketplace.json`)

Either way, `/deep-research:*` slash commands and the `deep-research` skill become
available in every session. See Requirements below for the `python3`/binary
dependencies the scripts themselves need.

## Slash commands — for a single narrow ask

Register some papers, summarize one or a few, check a run's status — these call the
matching script directly, no pipeline negotiation:

| Command | Does |
|---|---|
| `/deep-research:init <path>` | Create a standalone repo |
| `/deep-research:pool-add` | Register papers (bibliographic record only) |
| `/deep-research:summarize` | Read + summarize one paper |
| `/deep-research:summarize-set` | Discover/select + summarize a bounded set |
| `/deep-research:bib-export` | Export BibTeX from the registry |
| `/deep-research:pdf-lookup` | Check whether a paper is already registered |
| `/deep-research:status <run-dir>` | Stage progress + record table for a run |
| `/deep-research:verify` | Consistency checks over a run or a summary |
| `/deep-research:project` | Create/list manuscript projects |
| `/deep-research:watch` | Read-only snapshot of a run's progress |
| `/deep-research:help` | This table, plus the pool-add vs. summarize-set decision tree |

Flags: `commands/*.md`, or `/deep-research:help`. Everything except `status`/`verify`/
`watch` needs a repo from `/deep-research:init` first.

## The full review

Ask for it in the session — "literature review", "systematic review", "what does the
evidence say about X", "PubMed search", "critical appraisal". Stage 0 asks only what it
doesn't already know (question, profile, wiki-or-repo target, filters, outputs), then
runs nine stages (search → screen → retrieve → extract → appraise → synthesize → verify
→ publish) at one of four profiles (`fast`/`standard`/`systematic`/`max`). Details:
[`docs/pipeline.html`](docs/pipeline.html).

## Wiki mode vs. standalone repo

Same pipeline either way — only where the run, PDF store, and shared pool live differs.
Wiki mode needs a wiki-manager wiki; standalone repo mode doesn't, ever — run
`/deep-research:init <path>` once. Layout and full comparison:
[`docs/architecture.html`](docs/architecture.html), `references/pool-architecture.md`.

For a hands-on walkthrough: [`docs/quickstart.html`](docs/quickstart.html). Or run the
guided local tutorial: `python3 skills/deep-research/scripts/tutorial.py quickstart --repo
/tmp/deep-research-tutorial-demo`.

## Appraisal

Each paper gets the framework matching its design (RoB2, ROBINS-I, Newcastle-Ottawa,
AMSTAR-2, QUADAS-2, PROBAST, CASP-qualitative, JBI-prevalence, JBI-cross-sectional), then
per-outcome GRADE certainty. Never one-size-fits-all, never averaged across designs.
Details: [`docs/appraisal.html`](docs/appraisal.html).

## Quarantine and the shared pool

A paper whose full text can't be acquired is quarantined, not skipped — the run asks you
to drop a PDF into `<run-dir>/inbox/` and continues from there. Every paper extracted or
appraised is written to a shared pool (`pool.jsonl` in wiki mode, `registry.jsonl` in
repo mode) so no later run — even on a different question — re-extracts it. Details:
`references/acquisition.md` §5–6, `references/pool-architecture.md`.

## Resuming an interrupted run

Resume is by task, not by stage — say "resume" or rerun the same question; only
incomplete work continues. `/deep-research:status <run-dir> --table` shows what's left.

## Requirements

- **`python3`** only (no `python`). Stdlib + `requests` + `pdfminer` — no pip installs.
- **Binaries**: `pdftotext`, `pdfinfo`, `tesseract` (OCR fallback), `quarto` + `pandoc`
  (export).
- **`DEEP_RESEARCH_EMAIL`** required (Unpaywall + NCBI policy). `NCBI_API_KEY` optional
  (raises the rate limit).

## Limitations, honestly

- No pooled estimates — no meta-analytic effect, no I², no funnel plot.
- The hypothesis-wall check is lexical (flags banned phrasings), not semantic.
- `max` needs connectors (Scholar Gateway, Consensus) that may not be attached.
- Abstract-only evidence is labelled and never appraised as full text.
- Preprints are included at `wide`/`max` and tagged loudly.

## Testing

`python3 -m unittest discover -s skills/deep-research/tests` runs the unit suite, no
network needed. `skills/deep-research/scripts/eval.py` runs fixture-backed evals
(`--live` opts into real PubMed).
