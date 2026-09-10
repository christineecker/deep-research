# schema §12 — `claim span record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 12. `claim span record`

The atom of the kernel. Every material claim in the report, in an extraction, in an appraisal,
and in a promoted OKF concept resolves to one or more of these.

```json
{
  "claim": "The trial reported lower exacerbation rates in the intervention arm.",
  "evidence_id": "pmid:12345678",
  "source_id": "src-3f9a1cb84d02e77a5c1b0f9e2d6a4413c8b7e05f9a2d1c3e4b5a6978d0e1f2a3",
  "start": 10422,
  "end": 10610,
  "access": "full_text"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `claim` | string | yes | The proposition the span supports, in the record author's own words, ≤300 chars, single line. It names what is being asserted; it is **not** a transcription of the source and is never used as evidence text. |
| `evidence_id` | string | yes | The corpus record this claim attaches to (S9). Must exist in `corpus.jsonl`. |
| `source_id` | string | yes | Snapshot the offsets index into. Must exist under `<run>/sources/` and pass integrity (§10). Must be one of the snapshots registered for `evidence_id` (R14). |
| `start` | int | yes | Character offset of the first supporting character, **inclusive**, `>= 0`. |
| `end` | int | yes | Character offset one past the last supporting character, **EXCLUSIVE**. `start < end <= len(text)`. |
| `access` | enum | yes | `full_text` \| `abstract` \| `preprint` \| `guideline` \| `web`. Must equal the snapshot's `access` — it is copied, not chosen. A mismatch is a schema error. |

### Offset semantics (R10)

- `start` and `end` are **character offsets into the snapshot's decoded `text` string** —
  precisely, Python `str` indices, such that the supporting text is exactly `text[start:end]`.
- They are **NOT** byte offsets, **NOT** UTF-8 byte counts, **NOT** UTF-16 code units, and not
  offsets into any rendered, re-wrapped, or markdown-converted view of the source.
- Python `str` indices are Unicode code points. A combining sequence or an emoji ZWJ cluster may
  therefore span several indices; slicing mid-cluster is legal and produces exactly what the
  re-slice produces, so it can never cause a false mismatch.
- `text` is stored and compared without normalization — no NFC/NFD pass, no whitespace collapsing,
  no newline rewriting. Any implementation that normalizes on read breaks every offset in the run.

### Span rules

| # | Rule |
|---|---|
| P1 | `end` is exclusive. `text[start:end]` is the excerpt, always. |
| P2 | `end - start <= 2000` characters. A longer span is a hard failure, never a silent truncation. |
| P3 | Every material report claim resolves to **one or more** span records. Multiple spans are ORed evidence for one claim; they may come from different `source_id`s, may overlap, and are never concatenated or merged by the assembler (R18). Two disjoint 1500-character spans are the correct answer to a claim needing 3000 characters of support. |
| P4 | Literature claims must still trace to a PMID, DOI or PMCID: the span's `evidence_id` and the snapshot's `paper` object must each supply at least one identifier, and they must agree (R14). |
| P5 | Abstract-only claims remain allowed, but only when `access` is `abstract` **and** the claim is labelled abstract-level in the report (existing `C-FULLTEXT` rule, §9). |
| P6 | The excerpt is **never** authored, transcribed, paraphrased, or re-typed by an agent. It is re-sliced from the snapshot at assembly time and again at publish time. An agent-supplied excerpt that differs from the re-slice fails the claim (`EXCERPT_MISMATCH`). |

### Backward compatibility (R16)

An extraction or appraisal record written before this layer — no `spans[]` at all, or `spans: []`
where §7/§8 requires entries — is **not** a schema error and is never deleted. It is marked
`unverified`:

- it is listed in `diagnostics.unresolved[]` of `result.json` with `reason_code: "NO_SPANS"`;
- it can never enter `accepted[]`, so it can never pass the gate;
- with the gate off it still reaches the report, and `C-SPAN` reports `warn` naming it;
- with the gate on it blocks Stage 8 for that artifact;
- it may never back a promoted OKF concept's evidence footnote, gate or no gate.

---
