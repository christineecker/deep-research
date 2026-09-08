# schema §5 — `screening verdict`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 5. `screening verdict`

`workspace/screening/<screener>/pmid-<pmid>.json`, where `<screener>` is `screener-a`,
`screener-b`, or `screener` (single-screen profiles).

```json
{
  "schema_version": 1,
  "pmid": "12345678",
  "decision": "exclude",
  "reason": "Adult sample (mean age 41); protocol requires 6-18y.",
  "criterion_failed": "E2",
  "retraction_flag": "none",
  "screener_id": "screener-a",
  "confidence": 0.92
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `pmid` | string | yes | PMID as string. For non-PMID records use `evidence_id` in this slot and name the file `<evidence_id-slug>.json`. |
| `decision` | enum | yes | `include` \| `exclude` \| `unclear`. `unclear` is legitimate and must not be coerced. |
| `reason` | string | yes | One or two sentences, ≤300 chars, referencing what in the title/abstract drove the call. No paper text quoted beyond a short phrase. |
| `criterion_failed` | string \| null | yes | Id of the protocol criterion that failed (e.g. `I1`, `E2`), drawn from `protocol.md`'s criteria ids. `null` for `include`, and `null` for `unclear` when no single criterion is decisive. Must resolve to a criterion id that exists in the protocol. |
| `retraction_flag` | enum | yes | `none` \| `retracted` \| `expression_of_concern` \| `corrected`. Propagates into the corpus record. |
| `screener_id` | string | yes | `screener-a` \| `screener-b` \| `screener`. Matches the directory segment. |
| `confidence` | float \| null | yes | `0.0`–`1.0` self-reported. `null` permitted; never used to override `unclear`. |

---
