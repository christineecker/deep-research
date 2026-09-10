# Export A Manuscript Report

Standalone repo mode keeps manuscript files inside the project directory.

```text
projects/<project_slug>/
  protocol.md
  synthesis.md
  manuscript.qmd
  refs.bib
  figures/
  tables/
```

## Export BibTeX

Export all registered papers:

```bash
python3 scripts/registry.py bib \
  --repo /tmp/deep-research-demo \
  --out /tmp/deep-research-demo/projects/diagnostic-demo/refs.bib \
  --select all
```

Export only extracted papers:

```bash
python3 scripts/registry.py bib \
  --repo /tmp/deep-research-demo \
  --out /tmp/deep-research-demo/projects/diagnostic-demo/refs.bib \
  --select extracted
```

Export only papers appraised for a project:

```bash
python3 scripts/registry.py bib \
  --repo /tmp/deep-research-demo \
  --out /tmp/deep-research-demo/projects/diagnostic-demo/refs.bib \
  --select appraised \
  --project diagnostic-demo
```

## Build The Manuscript Files

The core project files can be edited directly:

- `protocol.md`: protocol and planned methods.
- `synthesis.md`: evidence synthesis.
- `manuscript.qmd`: manuscript body.
- `refs.bib`: bibliography generated from the registry.

The standalone repo can optionally export a plain bundle to a legacy wiki:

```bash
python3 scripts/research.py export wiki \
  --repo /tmp/deep-research-demo \
  --wiki /path/to/wiki \
  --project diagnostic-demo
```

This is only an adapter for compatibility. It is not required for standalone
manuscript work.

## HTML Report Preview

The HTML report renderer reads extraction and appraisal records and displays
study cards. With the new appraisal frameworks, cards can include:

- diagnostic accuracy tables
- prediction model tables
- qualitative evidence blocks
- cross-sectional evidence blocks
- grouped QUADAS-2/PROBAST risk-of-bias and applicability domains
- checklist-count appraisals for CASP, JBI, and Newcastle-Ottawa

QUADAS-2 and PROBAST applicability concerns should stay separate from risk of
bias. CASP and JBI checklist counts should not be described as low/high risk of
bias.
