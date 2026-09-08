# schema §9 — `verifier result`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 9. `verifier result`

`outputs/verification.json`, written by `scripts/verify.py` (stage 8).

```json
{
  "schema_version": 1,
  "checks": [
    { "check_id": "C-CITE-RESOLVE", "status": "pass", "detail": "48/48 report citations resolve to corpus evidence_ids." },
    { "check_id": "C-FULLTEXT", "status": "warn", "detail": "3 included studies are abstract_only." },
    { "check_id": "C-HYPOTHESIS-WALL", "status": "fail", "detail": "2 hypotheses phrased as established findings." }
  ],
  "unsupported_claims": [
    { "location": "report.md:L142", "claim": "CBT halves relapse risk at two years.", "reason": "No corpus outcome reports 24-month relapse." }
  ],
  "uncited_citations": ["pmid:34567890"],
  "missing_fulltext": ["pmid:45678901", "doi:10-1000-paywalled"],
  "abstract_only_claims": [
    { "location": "report.md:L88", "evidence_id": "pmid:23456789", "labelled": false }
  ],
  "okf_validation": "pass"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `checks` | object[] | yes | Every check run, in execution order. |
| `checks[].check_id` | string | yes | Stable id, `C-` prefix, SCREAMING-KEBAB. e.g. `C-CITE-RESOLVE`, `C-CORPUS-COMPLETE`, `C-SEARCH-LOG`, `C-RETRACTION`, `C-FULLTEXT`, `C-HYPOTHESIS-WALL`, `C-OKF`, plus the evidence-kernel checks below. |
| `checks[].status` | enum | yes | `pass` \| `fail` \| `warn`. Any `fail` makes the report provisional and blocks OKF promotion. |
| `checks[].detail` | string | yes | One line, machine-greppable, with counts. |
| `unsupported_claims` | object[] | yes | Claims in `report.md` with no backing corpus/extraction record. Each `{location, claim, reason}`. `[]` = clean. |
| `uncited_citations` | string[] | yes | `evidence_id`s present in `corpus.jsonl` as included but never cited in the report. |
| `missing_fulltext` | string[] | yes | `evidence_id`s with `fulltext.status == "missing"`; must match `missing.md`. |
| `abstract_only_claims` | object[] | yes | Report claims resting on `abstract_only` evidence. Each `{location, evidence_id, labelled}`; any `labelled: false` is a `C-FULLTEXT` fail (`PLAN.md` §6). |
| `okf_validation` | enum | yes | `pass` \| `fail` \| `skipped`. `skipped` when no wiki promotion was requested. `fail` keeps the report, blocks promotion, and writes `outputs/okf-validation.md` (`PLAN.md` §5). |

### Evidence-kernel checks

`verify.py` runs these in addition to the checks above, reading `sources/`, `events.jsonl` and
`outputs/result.json`. They are **always executed and always reported**; whether a violation is
`fail` or `warn` depends on the gate flag (`--gate` / `config.json` `gates.evidence_kernel`,
default **off** — R20). While the gate is off no evidence-kernel check may block Stage 8 except
the tamper checks, which are never downgraded.

| `check_id` | Asserts | Gate off | Gate on |
|---|---|---|---|
| `C-SNAPSHOT` | Every `source_id` referenced by any span exists at `sources/src-<sha256>.json`, parses, and its recomputed `content_hash` and recomputed `source_id` equal the stored values. | `fail` on a missing, unparseable, or hash-mismatched snapshot (tampering is never a warning). `warn` — detail `no snapshot store` — when the run contains no `sources/` directory at all (pre-kernel run). | `fail`; a run with no snapshot store is also `fail`. |
| `C-SPAN` | For every span: `0 <= start < end <= len(snapshot.text)`, `end - start <= 2000`, and the derived excerpt equals `snapshot.text[start:end]` character-for-character. | `fail` on any span that exists but is out of range, over-length, or whose excerpt mismatches. `warn` when a record that should carry spans carries none (`unverified`, R16). | `fail` in both cases. |
| `C-FRESH-FETCH` | Every `source_id` backing a claim in an artifact the verifier would call `supported` has a fresh retrieval event in `events.jsonl` per §11's freshness rule, or a `local_pdf` event with a matching asset hash. | `warn` — the stale sources are listed in `detail` and the affected claims are downgraded to `unverified`, but the report is not blocked. | `fail`. |
| `C-ASSEMBLER` | `outputs/result.json` exists, is schema-valid, and lists the artifact in `accepted[]` rather than `diagnostics.unresolved[]`. | `warn`, including when the assembler was never run (detail `result.json absent`). | `fail`. |

A `C-SNAPSHOT` or `C-SPAN` `fail` blocks OKF promotion regardless of the gate flag: the publisher
preflight (`okf.py promote --check`, `references/evidence-kernel.md`) repeats both checks and
fails closed. `outputs/report.md` is still written and kept.

---
