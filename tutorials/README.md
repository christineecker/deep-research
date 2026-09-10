# Deep Research Tutorial Suite

These tutorials show the standalone manuscript-repo workflow. They are task-based
and use local fixtures so a user can learn the tool without depending on external
APIs, PubMed availability, or a generated wiki.

Start here:

1. [Standalone Repo Quickstart](01-standalone-repo-quickstart.md)
2. [Add Papers To The Pool](02-add-papers-to-the-pool.md)
3. [Run A Review Project](03-run-a-review-project.md)
4. [Appraise With Frameworks](04-appraise-with-frameworks.md)
5. [Export A Manuscript Report](05-export-a-manuscript-report.md)
6. [Troubleshooting](06-troubleshooting.md)

## What The Tutorial Covers

- Creating a standalone research repo with `scripts/research.py init`.
- Creating a manuscript project.
- Adding papers by hand through BibTeX or identifiers.
- Promoting extraction and appraisal records into the general paper pool.
- Understanding where sources, extracted papers, and project appraisals live.
- Choosing appraisal frameworks such as QUADAS-2, PROBAST, CASP qualitative, and
  JBI prevalence/cross-sectional.
- Exporting repo-mode manuscript assets.

## Fixture Files

The `fixtures/` directory contains small synthetic examples:

- `sample-refs.bib`: two paper records for registry import.
- `sample-corpus.jsonl`: a tiny included corpus.
- `sample-extraction-quadas2.json`: diagnostic-accuracy extraction.
- `sample-appraisal-quadas2.json`: QUADAS-2 appraisal.
- `sample-appraisal-casp.json`: CASP qualitative appraisal example.
- `sample-protocol.md`: framework-aware protocol example.

The fixtures are synthetic and are meant for workflow practice, not scientific
interpretation.

## Guided Local Tutorial

Run the whole local quickstart automatically:

```bash
python3 scripts/tutorial.py quickstart --repo /tmp/deep-research-tutorial-demo
```

If the demo repo already exists and you want to reuse it:

```bash
python3 scripts/tutorial.py quickstart --repo /tmp/deep-research-tutorial-demo --force
```

The command creates a standalone repo, imports the fixture BibTeX file, stages a
run-local extraction and appraisal, promotes both into the canonical pool, and
exports project-scoped `refs.bib`.

## Static Online Tutorial

Build a static HTML version of these tutorials:

```bash
python3 scripts/tutorial.py build-site --out exports/html/tutorials
```

Open or publish:

```text
exports/html/tutorials/index.html
```

The generated site is plain HTML/CSS and includes the fixture files under
`exports/html/tutorials/fixtures/`, so it can be hosted by GitHub Pages, any
static file server, or a documentation site.
