# schema §6 — `adjudication record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 6. `adjudication record`

`workspace/screening/adjudication/pmid-<pmid>.json`. Written only when dual screening ran
(`systematic`, `max`) and the two screeners disagree.

```json
{
  "schema_version": 1,
  "pmid": "12345678",
  "screener_a_decision": "include",
  "screener_b_decision": "exclude",
  "final_decision": "include",
  "rationale": "B applied E2 to the pilot subsample; full sample age range 8-17 meets I1.",
  "adjudicator_id": "adjudicator-1"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `pmid` | string | yes | PMID as string (or `evidence_id` for non-PMID records). |
| `screener_a_decision` | enum | yes | `include` \| `exclude` \| `unclear` — verbatim from screener-a's verdict. |
| `screener_b_decision` | enum | yes | Same enum, from screener-b. |
| `final_decision` | enum | yes | `include` \| `exclude` \| `unclear`. This is what lands in the corpus record's `screening.decision`. |
| `rationale` | string | yes | ≤400 chars. Must state which criterion resolved the disagreement. |
| `adjudicator_id` | string | yes | Logical adjudicator worker id. |

The disagreement rate (adjudication records ÷ dual-screened records) is logged into the
PRISMA-style screening log (`SKILL.md`).

---
