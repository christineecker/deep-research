# Troubleshooting

## The Same Paper Appears Twice

Look it up by all available identifiers:

```bash
python3 scripts/registry.py lookup --repo /tmp/deep-research-demo --pmid 12345678
python3 scripts/registry.py lookup --repo /tmp/deep-research-demo --doi 10.1000/tutorial-dx
```

The registry normalizes PMID, DOI, and PMCID values. If a manual BibTeX record is
missing identifiers, add the strongest available one and re-import.

## A Paper Is Registered But Not Extracted

This is normal after manual import. Registration does not force extraction.

Check:

```bash
python3 scripts/registry.py list --repo /tmp/deep-research-demo
```

Look for:

```json
{
  "extraction_status": "not_started"
}
```

## Promotion Skips A Record

`registry.py promote` verifies spans by default. A synthetic fixture or manually
written extraction may be skipped if its source snapshot does not exist.

For tutorials only:

```bash
python3 scripts/registry.py promote \
  --repo /tmp/deep-research-demo \
  --run-dir /tmp/deep-research-demo/runs/tutorial-run \
  --no-verify
```

For real research runs, keep verification enabled.

## Appraisal Is Missing From Lookup

Repo-mode appraisals are project-scoped. Include the project when looking up or
reusing appraisals:

```bash
python3 scripts/pool.py lookup \
  --repo /tmp/deep-research-demo \
  --pmid 12345678 \
  --project diagnostic-demo
```

## The Wrong Appraisal Tool Was Selected

The tool is driven by the extracted design:

```text
diagnostic accuracy -> QUADAS-2
prediction model -> PROBAST
qualitative -> CASP-qualitative
prevalence -> JBI-prevalence
analytical cross-sectional -> JBI-cross-sectional
abstract-only -> none
```

Fix the extraction design and rerun or rewrite the appraisal. Do not force a
paper into a checklist just because a preferred tool is available.

## A Checklist Count Looks Like A Risk-Of-Bias Rating

CASP, JBI, and Newcastle-Ottawa produce checklist or star counts. They should be
reported as counts with lost items named. Do not translate them into `low`,
`moderate`, or `high` risk-of-bias labels.

## Abstract-Only Evidence

Abstract-only records should not receive full critical appraisal. Use:

```json
{
  "tool": "none",
  "domains": [],
  "overall_judgement": "unclear",
  "evidence_basis": "abstract_only"
}
```
