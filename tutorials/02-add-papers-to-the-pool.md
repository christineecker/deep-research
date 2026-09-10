# Add Papers To The Pool

Papers can enter the standalone repo in several ways. The most flexible path is
to register metadata first, then attach sources, extractions, and appraisals when
they become available.

## Option A: Import A BibTeX File

Use this when you have a citation manager export or want to add papers by hand.

```bash
python3 scripts/registry.py import-bib \
  --repo /tmp/deep-research-demo \
  --file tutorials/fixtures/sample-refs.bib
```

The importer accepts common fields:

```bibtex
@article{smith2026diagnostic,
  title = {Synthetic diagnostic accuracy study},
  author = {Smith, Jane and Lee, Morgan},
  journal = {Journal of Validation},
  year = {2026},
  doi = {10.1000/tutorial-dx},
  note = {PMID: 12345678}
}
```

## Option B: Add An Identifier

Use this when you know a PMID or DOI and want the registry to fetch metadata.
This may require network access.

```bash
python3 scripts/registry.py add \
  --repo /tmp/deep-research-demo \
  --pmid 12345678
```

or:

```bash
python3 scripts/registry.py add \
  --repo /tmp/deep-research-demo \
  --doi 10.1000/tutorial-dx
```

## Option C: Add A Local PDF

Use this when you have a paper file. The repo stores PDFs by content hash so the
same file is not duplicated.

```bash
python3 scripts/registry.py add-pdf \
  --repo /tmp/deep-research-demo \
  --file /path/to/paper.pdf \
  --title "Paper title" \
  --doi 10.1000/example
```

The PDF is copied to:

```text
data/sources/assets/sha256-<hash>.pdf
```

## Option D: Import A Folder Of PDFs

```bash
python3 scripts/registry.py import-folder \
  --repo /tmp/deep-research-demo \
  --dir /path/to/pdf-folder \
  --recursive
```

## Lookup And Deduplication

Look up by PMID, DOI, PMCID, or evidence id:

```bash
python3 scripts/registry.py lookup \
  --repo /tmp/deep-research-demo \
  --doi 10.1000/tutorial-dx
```

The registry normalizes identifiers and reuses the strongest available identity
in this order:

```text
PMID > DOI > PMCID > URL
```

Re-importing the same paper updates metadata instead of creating a duplicate.

## Manual Adds Are Supported

Manual BibTeX import is the cleanest hand-entry workflow. It registers a paper
without forcing extraction or appraisal. Later runs can reuse the paper, attach a
PDF, or promote completed extraction/appraisal records into the same pool.
