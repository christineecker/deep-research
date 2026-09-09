# acquisition.md — the full-text ladder

Stage 4 of the pipeline (`SKILL.md` "Pipeline"). Goal: full text, not abstracts. Every paper walks the
rungs in order until text is in hand; the rung that succeeded is recorded as
`fulltext.source_tier` + `fulltext.access_route` in the corpus record (`references/schema.md` §4).

Implemented by `scripts/fulltext.py` (ladder, resumable) and `scripts/library.py` (rung 0 +
inbox resume loop). Third-party deps: `requests`, `pdfminer` only. Binaries: `pdftotext`,
`pdfinfo`, `pdftoppm`, `tesseract`. Zero pip installs, ever.

---

## 1. Rungs

| # | Rung | Input | Method | Success = | Recorded `access_route` |
|---|---|---|---|---|---|
| 0 | Local library | DOI / PMID / PMCID / title | `library.py lookup` against `<wiki>/assets/papers/index.json`, then `pdftotext -layout` | ≥100 chars extracted | `library` (`inbox_manual` when it arrived via inbox) |
| 1 | PMC full text | PMID / PMCID | PubMed MCP `get_full_text_article` — **coordinator handoff**, see §4 | text file ≥100 chars at `result_path` | `pmc_mcp` |
| 2 | PMC PDF | PMCID | `GET https://pmc.ncbi.nlm.nih.gov/articles/<PMCID>/pdf/`, polite UA + rate limit → `pdftotext -layout` | HTTP 200, `Content-Type: application/pdf`, ≥100 chars | `pmc_pdf` |
| 3 | Europe PMC | DOI / PMID / PMCID | REST `search?query=…&resultType=core` (id conversion) → `{id}/fullTextXML` → JATS→text. Indexes bioRxiv/medRxiv/Research Square (`SRC:PPR`) natively | ≥100 chars of JATS body text | `epmc_xml` |
| 4 | Unpaywall | DOI | `GET https://api.unpaywall.org/v2/<doi>?email=<addr>` → `best_oa_location` | an OA location URL (no text yet — feeds rung 5) | `unpaywall` |
| 5 | OA PDF/HTML | location from rung 3/4 | fetch; PDF → `pdftotext -layout` (+OCR fallback §3); HTML → stdlib parse + **truncation detector** §2 | PDF ≥100 chars, or HTML with any body text | `unpaywall_pdf` / `oa_pdf` / `oa_html` |
| 6 | Preprint twin | DOI / title | Europe PMC `… AND SRC:PPR`, title similarity ≥0.90 unless matched by DOI → `fullTextXML` | ≥100 chars | `preprint_twin` (sets `is_preprint: true`) |
| 7 | Quarantine | anything | append a block to `<run-dir>/missing.md`, alert the user | always succeeds | `quarantine` |

Rung-state vocabulary in `workspace/retrieve/<stem>.json`: `success`, `failed` (attempted, no
text), `skipped` (precondition absent — no PMCID, no DOI, no email, offline), `needs_mcp`
(rung 1 handed off), `unavailable` (coordinator reports the MCP has no full text).

### What each rung writes

| Field | Value |
|---|---|
| `fulltext.status` | `fulltext` \| `abstract_only` (truncation detected, or no text stored) \| `missing` (rung 7) |
| `fulltext.source_tier` | `0`–`7`, the rung that produced the text |
| `fulltext.access_route` | token from the table above |
| `fulltext.local_path` | wiki-root-relative: `assets/papers/<stem>.pdf` for PDF routes, `outputs/deep-research/<slug>/workspace/fulltext/<stem>.txt` for XML/HTML/MCP routes |
| `fulltext.sha256` | hex sha256 of the stored file (library dedupe key) |
| `fulltext.truncation_detected` | `true` only from the HTML route |

Invariants (enforced, `references/schema.md` §4): `status == "missing"` ⇒ `source_tier == 7`
and a `missing.md` block exists; `truncation_detected == true` ⇒ `status == "abstract_only"`.
Extraction of an `abstract_only` record sets `evidence_basis: "abstract_only"` and it is never
appraised as if full (`SKILL.md` "Invariants").

### Politeness

| Host | Min interval | Notes |
|---|---|---|
| `pmc.ncbi.nlm.nih.gov`, `eutils.ncbi.nlm.nih.gov` | 0.34 s (≈3 req/s) | no `NCBI_API_KEY` in env; with a key, 10 req/s is permitted |
| `www.ebi.ac.uk` | 0.25 s | Europe PMC |
| `api.unpaywall.org` | 0.15 s | `email=` is a **required** API parameter, not optional courtesy |
| default | 0.34 s | |

User-Agent: `deep-research/0.1 (+https://pubmed.ncbi.nlm.nih.gov/; literature-review agent; mailto:<email>)`.
Retries: 2, on network errors and HTTP 429/500/502/503/504, with linear backoff. Everything
else fails the rung and moves on. Email comes from `--email` or `$DEEP_RESEARCH_EMAIL`; without
it rung 4 is `skipped`, never guessed.

---

## 2. Truncation detector (HTML route only)

Runs on every rung-5 HTML response. Fires if **either** condition holds:

| Rule | Threshold |
|---|---|
| Body word count after tag stripping | `< 1500` words |
| Paywall marker present in body text or raw HTML (case-insensitive substring) | any of the list below |

Marker list — exactly these five strings:

```text
Access options
Purchase
Sign in to view
Get access
Subscribe
```

On fire: `truncation_detected: true`, `status` downgraded to `abstract_only`, never `fulltext`.
The reasons (`body_words=303<1500`, `paywall_marker=Access options`) are written to the rung
detail and `engine.log`. The text is still stored — it is a legitimate abstract-plus-teaser —
but every downstream claim carries the abstract-only label.

The detector is deliberately trigger-happy: a false `abstract_only` costs a label, a false
`fulltext` corrupts the appraisal.

HTML is parsed with stdlib `html.parser` (`lxml`/`bs4` are not installed); `script`, `style`,
`noscript`, `svg`, `head`, `nav`, `footer`, `header`, `aside`, `form`, `button` are dropped.
JATS XML is parsed with stdlib `xml.etree.ElementTree`; `ref-list`, `back`, `journal-meta` are
dropped. Malformed XML/HTML yields empty text and fails the rung — it never raises.

---

## 3. Text extraction and OCR fallback

| Step | Command | Condition |
|---|---|---|
| 1 | `pdftotext -layout <pdf> -` | always first |
| 2 | `pdfminer.high_level.extract_text` | if `pdftotext` is absent or returns nothing |
| 3 | per-page OCR: `pdftoppm -r 300 -gray -png -f N -l N` → `tesseract <png> - --psm 1` | only if steps 1–2 yield `< 100` chars |

`ocrmypdf` is **not installed**; there is no whole-file OCR path. OCR is capped at 30 pages per
document and can be disabled with `--no-ocr`. OCR use is recorded in the rung detail
(`; OCR used`). If OCR still yields `< 100` chars the rung fails and the ladder continues.

DOI recovery from a PDF (used by the inbox loop): `pdftotext -layout -f 1 -l 1`, then regex
`10\.\d{4,}/\S+` on page-1 text; if page 1 is under 100 chars, OCR page 1 first. `pdfinfo`
metadata is *not* trusted — it rarely carries a DOI.

---

## 4. Rung 1: the PubMed MCP handoff

`get_full_text_article` is an MCP tool. A script cannot call it. The handoff is explicit:

| Step | Actor | Action |
|---|---|---|
| 1 | `fulltext.py` | on reaching rung 1 with a PMID/PMCID, appends a `needs_mcp` task to `workspace/retrieve/mcp-tasks.jsonl` and **continues down the ladder** — the run never blocks on rung 1 |
| 2 | coordinator | reads pending tasks (`fulltext.py status --run-dir <dir>` lists them under `mcp_tasks_pending`) |
| 3 | coordinator | calls `mcp__claude_ai_PubMed__get_full_text_article` with the task's `args` |
| 4 | coordinator | writes the returned text verbatim (UTF-8) to the task's `result_path` (`workspace/fulltext/<stem>.mcp.txt`) |
| 5 | coordinator | `fulltext.py resolve-mcp --run-dir <dir> --evidence-id <id> --text-file <path>` — or simply re-runs `acquire`, which picks the file up on the next pass |
| 6 | coordinator | if the tool reports no full text: `fulltext.py resolve-mcp --evidence-id <id> --status unavailable` so rung 1 is marked terminal and never retried |

Task record shape:

```json
{"schema_version":1,"task_id":"retrieve:pmid:33333333","evidence_id":"pmid:33333333",
 "status":"needs_mcp","tool":"mcp__claude_ai_PubMed__get_full_text_article",
 "args":{"pmid":"33333333","pmcid":null},
 "result_path":"workspace/fulltext/pmid-33333333.mcp.txt","created_at":"2026-09-08T00:00:00Z"}
```

A record that reached rung 7 while its rung-1 task was still pending is quarantined *and* still
resolvable: delivering the MCP text later upgrades it to `source_tier: 1` and removes its block
from `missing.md`.

---

## 5. Quarantine and the inbox resume loop

Quarantine (`rung 7`) appends to `<run-dir>/missing.md`:

```markdown
## <title>

- evidence_id: pmid:33333333
- PMID: 33333333
- DOI: 10.1000/paywalled
- PMCID: null
- Journal: Closed Access J
- Rungs attempted: t0=failed; t1=needs_mcp; t2=skipped; …
- PubMed: https://pubmed.ncbi.nlm.nih.gov/33333333/
- DOI link: https://doi.org/10.1000/paywalled
- PMC: https://pmc.ncbi.nlm.nih.gov/articles/PMC…/

Action: place the PDF in `inbox/` (any filename) and re-run the skill.
```

The run never stalls: it continues, marks the synthesis provisional, and lists the gap
(`SKILL.md` "Pipeline"). Never ask the user whether to keep going, wait, or supply a PDF for
an individual record — quarantine and continue. Once acquisition has been attempted for every
selected record, alert the user once with a consolidated table of all quarantined records:

| Title | PMID | DOI | PMCID | Rung reached | Links |
|---|---|---|---|---|---|
| ... | 33333333 | 10.1000/paywalled | — | t7 | [PubMed](https://pubmed.ncbi.nlm.nih.gov/33333333/) · [DOI](https://doi.org/10.1000/paywalled) |

Tell the user exactly where to put PDFs they find manually: `<run-dir>/inbox/`.

Resume loop:

| Step | Command / actor | Detail |
|---|---|---|
| 1 | user | drops PDFs into `<run-dir>/inbox/` (any filenames) |
| 2 | `library.py ingest-inbox --run-dir <dir> [--wiki <root>]` | per PDF: page-1 `pdftotext` → DOI regex → exact DOI match against corpus records; else fuzzy title (§6) against quarantined records and `missing.md` titles |
| 3 | `library.py` | files the matched PDF into `<wiki>/assets/papers/<stem>.pdf` (move, sha256 dedupe), updates `index.json`, extracts text to `workspace/fulltext/<stem>.txt` |
| 4 | `library.py` | writes the corpus update (`source_tier: 0`, `access_route: inbox_manual`, `local_path`, `sha256`) into `corpus.jsonl` and emits `workspace/retrieve/inbox-updates.json`; removes the record's block from `missing.md` |
| 5 | coordinator | re-runs extract → appraise → synthesize for the un-quarantined records; the report notes which studies arrived by manual supply |

Unmatched PDFs stay in `inbox/` and are listed under `unmatched` with the reason — never
guessed onto a record.

---

## 6. Matching and thresholds (`library.py`)

| Order | Key | Rule |
|---|---|---|
| 1 | `sha256` | content dedupe on `add`; identical bytes never stored twice, identifiers are merged into the existing entry |
| 2 | DOI | exact, after normalization: lowercase, strip `https://doi.org/`, `doi:`, trailing `).,;]>'"` |
| 3 | PMID | exact string compare (PMIDs are strings, never ints) |
| 4 | PMCID | exact, case-insensitive |
| 5 | fuzzy title | normalized title vs. `index.json` `title_norm`, `difflib.SequenceMatcher(autojunk=False).ratio() >= 0.90` |

Inbox ingestion uses a looser second threshold: the normalized title is slid across the
normalized page-1 text (window `len(title)+step`, `step = len(title)//4`) and the best ratio
must be `>= 0.85`. Layout and OCR noise inflate the denominator, so the page threshold is
lower than the title↔title threshold. Titles normalizing to fewer than 25 characters are never
fuzzy-matched — too collision-prone.

Title normalization: NFKD, combining marks dropped, case-folded, every non-`[a-z0-9]` run
collapsed to a single space, trimmed.

`index.json` entry:

```json
{"sha256":"…","path":"assets/papers/pmid-12345678.pdf","pmid":"12345678",
 "doi":"10.1000/example","pmcid":"PMC1234567","title":"…","title_norm":"…",
 "journal":"J Example Med","year":"2024","pages":12,"bytes":481203,
 "added_at":"2026-09-08T00:00:00Z","source_tier":2,"access_route":"pmc_pdf",
 "is_preprint":false}
```

`is_preprint` is additive and defaults to `false`; an entry written before the field
existed simply lacks the key and reads back as `false`. A rung-0 hit on an entry with
`is_preprint: true` registers its snapshot as `access: "preprint"`, never `"full_text"`.

The library is shared across runs — a PDF fetched for one review is rung 0 for the next.

### git policy

`library.py init --wiki <root>` creates `assets/papers/` + `index.json` and idempotently
installs exactly this block in the wiki's `.gitignore` (PDFs local, manifest versioned;
a negation inside an ignored directory does not work, hence the four lines):

```gitignore
assets/*
!assets/papers/
assets/papers/*
!assets/papers/index.json
```

Unrelated rules are preserved verbatim. Only a previous managed block and lines that would
shadow it (`assets/`, `assets/*`, `/assets/…`, the four rules themselves) are removed.

---

## 7. Resumability

| Guarantee | Mechanism |
|---|---|
| Never redo a completed rung | per-record state in `workspace/retrieve/<stem>.json`; a rung with a terminal status (`failed`, `skipped`, `unavailable`) is not re-attempted |
| Never redo a satisfied record | `fulltext.status == "fulltext"` ⇒ the record is skipped entirely |
| Retry deliberately | `--from-tier N` discards rung state ≥ N and walks again from rung N |
| Retry one record | `--only-pmid <pmid>` / `--only-evidence-id <id>` |
| Pending MCP work is visible | `needs_mcp` rung state is re-checked on every pass; the file's presence flips it to `success` |
| Corpus is rewritten atomically | temp file + `replace()`; `index.json` likewise |
| Crash-safe | state, corpus, `missing.md`, `index.json` are all on disk after each record |

`--offline` runs local rungs only: network rungs report `skipped`, not `failed`, so a later
online pass still attempts them.

---

## 8. CLI

```bash
fulltext.py acquire --corpus <corpus.jsonl> --run-dir <dir>
                    [--wiki <root>] [--only-pmid X] [--only-evidence-id X]
                    [--from-tier N] [--email addr] [--limit N] [--offline] [--no-ocr]
                    [--workers N]
fulltext.py status  --run-dir <dir> [--corpus <path>]
fulltext.py resolve-mcp --run-dir <dir> --evidence-id <id>
                    (--text-file <path> | --status unavailable)

library.py init         --wiki <root>
library.py lookup       --wiki <root> [--doi|--pmid|--pmcid|--title|--sha256]
library.py add          --wiki <root> --pdf <path> [--pmid --doi --pmcid --title
                                                    --journal --year
                                                    --source-tier --access-route --move]
library.py ingest-inbox --run-dir <dir> [--wiki <root>] [--corpus <path>] [--no-apply]
library.py list         --wiki <root> [--limit N]
```

Records whose `screening.decision == "exclude"` are never acquired. The wiki root defaults to
`<run-dir>/../../..` (`<wiki>/outputs/deep-research/<slug>/` → `<wiki>`).

---

## 9. Forbidden approaches

Hard policy (`SKILL.md` "Invariants"). These are not "not yet implemented" — they are never
implemented, and no rung may be added that does any of them. Fail closed.

| Forbidden | Why |
|---|---|
| Browser automation (chrome-devtools MCP, Playwright, headless Chrome) to reach an article | Dropped at design review; a browser rung exists only to defeat access controls |
| Institutional / VPN / EZproxy / Shibboleth / library credentials | Credentialed access is the user's, not the agent's, and licences are per-person |
| Any user account, cookie jar, session token, or captcha solving | Same |
| Sci-Hub, LibGen, Anna's Archive, Nexus, ResearchGate scraping, `#icanhazpdf`, mirror sites | Infringing sources |
| Requesting a PDF from an author by automated email | Not authorized; the human may do this themselves |
| Bypassing rate limits, rotating User-Agents, cloaking the client identity | Breaks the terms we operate under |
| Retrying a 401/402/403 by any alternate route | A refusal is a refusal |
| Guessing an Unpaywall email, or omitting `email=` | The parameter is required and identifies the caller honestly |

When the OA ladder is exhausted the answer is rung 7: quarantine, alert the user, mark the
synthesis provisional, and let the human decide whether to supply the PDF via `inbox/`.
A missing paper is an honest gap in the report; a circumvented paywall is a policy breach.
