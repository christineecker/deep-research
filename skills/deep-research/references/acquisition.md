# acquisition.md — the full-text ladder

Stage 4 of the pipeline (`SKILL.md` "Pipeline"). Goal: full text, not abstracts. Every paper walks the
rungs in order until text is in hand; the rung that succeeded is recorded as
`fulltext.source_tier` + `fulltext.access_route` in the corpus record (`references/schema.md` §4).

Implemented by `scripts/fulltext.py` (ladder, resumable) and `scripts/library.py` (rung 0 +
inbox resume loop). Third-party deps: `requests`, `pdfminer` only. Binaries: `pdftotext`,
`pdfinfo`, `pdftoppm`, `tesseract`. Zero pip installs, ever. Rung 7 (browser search/fetch) is
the one exception to "no third-party deps": it is driven entirely through the claude-in-chrome
MCP tools, called by the coordinator, never by `fulltext.py` itself — see §4b.

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
| 7 | Browser search/fetch | DOI / PMID / PMCID / title | claude-in-chrome MCP — **coordinator handoff**, OA content only, see §4b | text file ≥100 chars at `result_path` | `browser_fetch` |
| 8 | Quarantine | anything | append a block to `<run-dir>/missing.md`, alert the user | always succeeds | `quarantine` |

Rung-state vocabulary in `workspace/retrieve/<stem>.json`: `success`, `failed` (attempted, no
text), `skipped` (precondition absent — no PMCID, no DOI, no email, offline), `needs_mcp`
(rung 1 handed off), `needs_browser` (rung 7 handed off), `unavailable` (coordinator reports the
MCP/browser search found no full text).

### What each rung writes

| Field | Value |
|---|---|
| `fulltext.status` | `fulltext` \| `abstract_only` (truncation detected, or no text stored) \| `missing` (rung 8) |
| `fulltext.source_tier` | `0`–`8`, the rung that produced the text |
| `fulltext.access_route` | token from the table above |
| `fulltext.local_path` | wiki-root-relative: `assets/papers/<stem>.pdf` for PDF routes, `outputs/deep-research/<slug>/workspace/fulltext/<stem>.txt` for XML/HTML/MCP/browser routes |
| `fulltext.sha256` | hex sha256 of the stored file (library dedupe key) |
| `fulltext.truncation_detected` | `true` from the HTML route (§2) or the browser route (§4b, same rules applied to plain text) |

Invariants (enforced, `references/schema.md` §4): `status == "missing"` ⇒ `source_tier == 8`
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

## 2. Truncation detector

Runs on rung 5's HTML response, and — same rules, applied directly to already-extracted plain
text (`detect_truncation_text`) — on whatever rung 1 (PubMed MCP) and rung 7 (browser) hand
back. Any of the three can return an abstract with a page's worth of teaser text around it, and
`get_full_text_article` / a browser extraction have no tag structure left to strip, so the same
two checks apply to raw text as to stripped HTML. Fires if **either** condition holds:

| Rule | Threshold |
|---|---|
| Body word count (after tag stripping, for the HTML route) | `< 1500` words |
| Paywall marker present in the text (case-insensitive substring) | any of the list below |

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

**A truncated success no longer stops the ladder.** Rungs 1, 5, and 7 are the only three that
can fire the detector; when one does, `acquire_record` keeps the first such result as a
*fallback* and keeps walking every remaining rung looking for genuine full text (§7 "Fallback
walk-through" below) — it does not settle for the first abstract-only hit while five more rungs
remain untried. Only if the whole ladder is exhausted without anything better does the fallback
get finalized, and even then it is written into `missing.md` as an abstract-only block (§5) so
the halt-before-stage-5 gate surfaces it and asks for a real PDF — not silently accepted.

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

`resolve-mcp` does not finalize rung 1 on its own: it persists the coordinator's text (or the
`unavailable` mark) and then calls the same `acquire_record` the ladder itself uses. A response
under 100 chars fails rung 1 and the ladder moves on to rung 2 exactly as it would on a fresh
`acquire` pass; a truncated (abstract-only) response is kept as a fallback while rungs 2-8 are
still tried (§2, §7 "Fallback walk-through") rather than accepted as terminal. Calling
`resolve-mcp` and re-running `acquire` are therefore equivalent, not two different code paths —
use whichever is convenient.

Task record shape:

```json
{"schema_version":1,"task_id":"retrieve:pmid:33333333","evidence_id":"pmid:33333333",
 "status":"needs_mcp","tool":"mcp__claude_ai_PubMed__get_full_text_article",
 "args":{"pmid":"33333333","pmcid":null},
 "result_path":"workspace/fulltext/pmid-33333333.mcp.txt","created_at":"2026-09-08T00:00:00Z"}
```

A record that reached rung 8 while its rung-1 task was still pending is quarantined *and* still
resolvable: delivering the MCP text later upgrades it to `source_tier: 1` and removes its block
from `missing.md`.

---

## 4b. Rung 7: the browser search/fetch handoff

Same shape as §4, a different MCP surface, and the last rung before quarantine. By the time a
record reaches rung 7, every scripted OA route has already failed: no local copy, no PMC MCP
text, no PMC PDF, no Europe PMC JATS, no Unpaywall location, no direct OA fetch, no preprint
twin. What's left is content a plain `requests.get` cannot reach — a JS-rendered OA landing
page, a host that blocks scripted clients, a repository whose download link only appears after
client-side rendering — but that is still, in principle, free to read.

| Step | Actor | Action |
|---|---|---|
| 1 | `fulltext.py` | on reaching rung 7 appends a `needs_browser` task to `workspace/retrieve/browser-tasks.jsonl` (candidate URLs built from DOI/PMID) and **continues down the ladder** — the run never blocks on rung 7 |
| 2 | coordinator | reads pending tasks (`fulltext.py status --run-dir <dir>` lists them under `browser_tasks_pending`) |
| 3 | coordinator | drives the claude-in-chrome MCP tools (`navigate`, `read_page` / `get_page_text`, `tabs_create_mcp`) to search for and open a freely-accessible copy |
| 4 | coordinator | extracts the visible article body text and writes it verbatim (UTF-8) to the task's `result_path` (`workspace/fulltext/<stem>.browser.txt`) |
| 5 | coordinator | `fulltext.py resolve-browser --run-dir <dir> --evidence-id <id> --text-file <path>` — or simply re-runs `acquire`, which picks the file up on the next pass |
| 6 | coordinator | if nothing but a login wall, paywall, or captcha turns up: `fulltext.py resolve-browser --evidence-id <id> --status unavailable` so rung 7 is marked terminal and never retried |

Like `resolve-mcp` (§4), `resolve-browser` does not finalize rung 7 by itself — it persists the
text (or the `unavailable` mark, or the `--institutional` marker, §"Institutional access" below)
and calls `acquire_record`. A truncated result becomes a fallback, not a terminal `abstract_only`
(§2); rung 8 is the only rung this can preempt — reaching rung 7 with a fallback already in hand
from an earlier rung means quarantine's zero-text write is skipped in favour of finalizing that
fallback (§7 "Fallback walk-through").

**Hard boundary, not a suggestion.** This rung exists solely to reach the *rendering* problem —
never to reach past an access control. The same invariant §9 states for every other rung applies
here with zero exception:

- No sign-in, no saved session, no cookie jar reuse, no institutional/VPN/EZproxy/Shibboleth
  proxy, no captcha solving, no payment.
- A 401/402/403, a login redirect, or any of the five paywall markers (§2) means: stop, do not
  try another route for this candidate, report `unavailable`.
- Sci-Hub, LibGen, Anna's Archive, ResearchGate scraping, mirror sites — never a candidate URL,
  regardless of what a search turns up.
- The text handed to `resolve-browser` runs through the same truncation detector as the HTML
  route (§2, word count + paywall-marker substrings) — a thin teaser page still gets tagged
  `abstract_only`, never `fulltext`.

### Institutional access (opt-in exception to the boundary above)

The one deliberate carve-out: if `config.json` `institutional_access.enabled` is set (Stage 0
asks once per run, off by default — `SKILL.md` "Institutional access"), the task also names that
library's discovery search (e.g. King's College London: `https://librarysearch.kcl.ac.uk/discovery/search?vid=44KCL_INST:44KCL_INST`)
as a candidate. This is still not a bypass:

- The coordinator opens the discovery search and searches by title/DOI — it never constructs or
  guesses a query-string API for a site whose search syntax was not verified.
- If the record needs SSO, the coordinator **stops and hands the tab to the human**. It never
  types, stores, requests, or transmits the credential itself — only the human authenticates,
  in their own already-open browser tab, at their own discretion.
- Once the human confirms the tab shows the authenticated article, extraction resumes normally.
- The result is recorded with `fulltext.py resolve-browser --institutional --text-file <path>`,
  never picked up silently by a plain `acquire` re-run — this sets `access_route:
  browser_fetch_institutional` (vs. plain `browser_fetch` for OA) so the report's Methods
  section can disclose, per record, which full texts came through the user's own institutional
  licence rather than open access. `references/schema/04-corpus.md` §4 documents both tokens.

Everything else in this rung's boundary — no automated credential entry, no institutional
access without this explicit per-run opt-in, no retrying a block by another route — is
unchanged.

Task record shape:

```json
{"schema_version":1,"task_id":"retrieve:pmid:33333333","evidence_id":"pmid:33333333",
 "status":"needs_browser","tool":"mcp__claude-in-chrome__* (navigate / read_page / get_page_text)",
 "args":{"pmid":"33333333","doi":null,"pmcid":null,"title":"…","journal":"…",
         "candidate_urls":["https://pubmed.ncbi.nlm.nih.gov/33333333/"]},
 "result_path":"workspace/fulltext/pmid-33333333.browser.txt","created_at":"2026-09-08T00:00:00Z"}
```

A record that reached rung 8 while its rung-7 task was still pending is quarantined *and* still
resolvable: delivering the browser text later upgrades it to `source_tier: 7` and removes its
block from `missing.md`.

---

## 5. Quarantine and the inbox resume loop

`<run-dir>/missing.md` carries two kinds of block, both written by `append_missing_block` and
both meaning "the user might be able to supply something better":

- **Quarantined** (`rung 8`, zero text at all) — every rung failed outright.
- **Abstract-only** (`§7 "Fallback walk-through"` below) — some earlier rung *did* return text,
  but the truncation detector (§2) flagged it as degraded and the ladder tried every remaining
  rung looking for real full text before giving up. `fulltext.status` stays `abstract_only`
  with the real (degraded) text stored — this block is never a `status: missing` record, only a
  flag that a full PDF would upgrade it.

```markdown
## <title>

- Status: quarantined (no text obtained)
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

An abstract-only block is identical except its `- Status:` line reads e.g. `abstract-only (tier
1, pmc_mcp) — the ladder tried every remaining rung and found nothing better; a full PDF would
upgrade this record`.

Stage 4 (retrieve) itself never stalls: it continues walking the ladder for every remaining
record regardless of any single failure. Never ask the user whether to keep going, wait, or
supply a PDF for an individual record mid-ladder — quarantine (or accept the abstract-only
fallback) and continue to the next record.

What *does* stall, deliberately, is the pipeline as a whole: once acquisition has been
attempted for every selected record, if `missing.md` is non-empty the run halts before stage 5
(extraction) — **regardless of which kind of block it holds**. An abstract-only block is not
a lesser case that can be waved through; the whole point of writing it is that the user gets
asked, exactly as for a true quarantine. Alert the user once with a consolidated table of all
blocked records:

| Title | PMID | DOI | PMCID | Rung reached | Status | Links |
|---|---|---|---|---|---|---|
| ... | 33333333 | 10.1000/paywalled | — | t8 | quarantined | [PubMed](https://pubmed.ncbi.nlm.nih.gov/33333333/) · [DOI](https://doi.org/10.1000/paywalled) |
| ... | 42119772 | — | PMC1234567 | t1 (fallback) | abstract-only | [PubMed](https://pubmed.ncbi.nlm.nih.gov/42119772/) |

Tell the user exactly where to put PDFs they find manually — `<run-dir>/inbox/` — and ask
explicitly whether they can supply any of the blocked records, quarantined or abstract-only
alike. For `systematic` and `max` this is a hard gate: extraction does not start, and no stage
after it runs, until `missing.md` is empty. For `fast` and `standard`, the user may instead
answer to continue without them; extraction then proceeds with quarantined records tagged
`missing` and abstract-only records left exactly as they are (their real, degraded text used
as-is), and the synthesis marked PROVISIONAL either way. Run `library.py ingest-inbox` then
re-run `fulltext.py acquire` to clear resolved records before continuing.

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

### Fallback walk-through

When rung 1, 5, or 7 returns a truncated (abstract-only) success, `acquire_record` does not
finalize it on the spot. It keeps the **first** such result as `fallback = (tier, res)`, marks
that rung's state as `success` (so it is never redone), and keeps walking every remaining rung:

- A later rung returning genuine full text wins outright — finalized immediately, the fallback
  is discarded, and `library.unquarantine` (already unconditional on any non-`missing` finalize)
  removes any `missing.md` block for the record, abstract-only or quarantined.
- A later rung also returning truncated text does not replace the fallback — first found, kept.
- Rung 8 is preempted: if a fallback exists by the time the loop would reach it, quarantine's
  unconditional zero-text write is skipped entirely (it would otherwise silently overwrite the
  degraded-but-real text with `status: missing`).
- If the ladder reaches its end with a fallback in hand and nothing better, *that* fallback is
  finalized (`status: abstract_only`, its own tier and `access_route`, real text stored) and
  `append_missing_block` writes the abstract-only block (§5) — the record surfaces at the
  halt-before-stage-5 gate exactly like a true quarantine, just labeled differently.
- If the ladder reaches its end with **no** fallback at all, rung 8 runs as before: a genuine
  zero-text quarantine.

`resolve-mcp` and `resolve-browser` (§4, §4b) do not duplicate any of this — they persist the
coordinator's input and call the same `acquire_record`, so this walk-through applies identically
whether text arrives via those commands or via a plain `acquire` re-run picking up a dropped
file.

| Guarantee | Mechanism |
|---|---|
| Never redo a completed rung | per-record state in `workspace/retrieve/<stem>.json`; a rung with a terminal status (`failed`, `skipped`, `unavailable`) is not re-attempted |
| Never redo a satisfied record | `fulltext.status == "fulltext"` ⇒ the record is skipped entirely |
| Retry deliberately | `--from-tier N` discards rung state ≥ N and walks again from rung N |
| Retry one record | `--only-pmid <pmid>` / `--only-evidence-id <id>` |
| Pending MCP/browser work is visible | `needs_mcp` and `needs_browser` rung state is re-checked on every pass; the result file's presence flips it to `success` |
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
fulltext.py resolve-browser --run-dir <dir> --evidence-id <id>
                    (--text-file <path> [--institutional] | --status unavailable)

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

Rung 7 (§4b) is a controlled, narrow exception to the old blanket "no browser automation" rule:
it may use the claude-in-chrome MCP tools, but *only* to reach content that requires JS
rendering and carries no access control at all. Every row below still applies to rung 7 exactly
as it applies to every other rung — a browser is not a loophole around any of them.

The institutional-access opt-in (§4b "Institutional access") is a second, narrower exception,
scoped even tighter: off by default, on only when the user explicitly opts in for that run, and
even then the agent never handles the credential — the human completes SSO themselves. It does
not relax "Any user account, cookie jar, session token" below for anyone but that human, acting
on their own licence, in their own browser session.

A third, unrelated exception lives entirely outside this ladder: `scripts/embeddings.py`
(semantic similarity search, `references/reference-manager.md`) has one optional third-party
dependency, `sentence-transformers`, imported lazily inside that single script. It is a narrow,
deliberate carve-out to the project's blanket "zero pip installs, ever" policy, scoped to that
one script only — it does not relax the policy for full-text acquisition, extraction, appraisal,
or anything else in this skill, all of which remain stdlib-plus-`requests`/`pdfminer` as before.

| Forbidden | Why |
|---|---|
| Browser automation to defeat, bypass, or route around any access control (login, paywall, metering, captcha) | A browser rung exists to render JS pages, never to reach past a control a script was correctly refused by |
| Institutional / VPN / EZproxy / Shibboleth / library credentials | Credentialed access is the user's, not the agent's, and licences are per-person |
| Any user account, cookie jar, session token, or captcha solving | Same — applies to the browser rung's own cookie/session state too, not just scripted requests |
| Sci-Hub, LibGen, Anna's Archive, Nexus, ResearchGate scraping, `#icanhazpdf`, mirror sites | Infringing sources — never a candidate URL for the browser rung either |
| Requesting a PDF from an author by automated email | Not authorized; the human may do this themselves |
| Bypassing rate limits, rotating User-Agents, cloaking the client identity | Breaks the terms we operate under |
| Retrying a 401/402/403 by any alternate route, browser included | A refusal is a refusal |
| Guessing an Unpaywall email, or omitting `email=` | The parameter is required and identifies the caller honestly |

When the OA ladder — including rung 7's browser search — is exhausted the answer is rung 8:
quarantine, alert the user, mark the synthesis provisional, and let the human decide whether to
supply the PDF via `inbox/`. A missing paper is an honest gap in the report; a circumvented
paywall is a policy breach.
