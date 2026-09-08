# schema §10 — `snapshot record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 10. `snapshot record`

`<run>/sources/src-<sha256>.json`. One file per immutable source snapshot, written by
`scripts/source.py`. The evidence-proof kernel's ground truth: **the only text in the run that
counts as evidence.** Narrative reference: `references/evidence-kernel.md`.

```json
{
  "schema_version": 1,
  "source_id": "src-3f9a1cb84d02e77a5c1b0f9e2d6a4413c8b7e05f9a2d1c3e4b5a6978d0e1f2a3",
  "content_hash": "sha256:b7e05f9a2d1c3e4b5a6978d0e1f2a33f9a1cb84d02e77a5c1b0f9e2d6a4413c8",
  "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/",
  "title": "Cognitive behavioral therapy for adolescent depression: a randomized trial",
  "retrieved_at": "2026-09-08T12:07:44Z",
  "access": "full_text",
  "paper": { "pmid": "12345678", "doi": "10.1000/example", "pmcid": "PMC1234567" },
  "origin": "pmc",
  "asset": { "path": "assets/papers/pmid-12345678.pdf", "sha256": "9ab3c1...", "bytes": 1842991 },
  "text": "Cognitive behavioral therapy for adolescent depression\n\nAbstract\nBackground. ..."
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `source_id` | string | yes | `"src-"` + sha256 over the UTF-8 URL, one NUL byte, and the UTF-8 text; lowercase hex, 64 chars after the prefix. See "Source identity" below. Equals the file's basename without `.json`. |
| `content_hash` | string | yes | `"sha256:"` + sha256 of `text` **alone**, UTF-8 encoded, lowercase hex. Detects tampering with the body independently of the URL. |
| `url` | string | yes | The exact URL retrieved, byte-for-byte as requested — no normalization, no trailing-slash fixing. A redirect that changed the URL produces a snapshot for the final URL. For `user-supplied-pdf` this is the `file://` URL of the library path at ingest time. It participates in `source_id`, so it can never be edited. |
| `title` | string \| null | yes | Title as stated by the source (PubMed `ArticleTitle`, PMC title, HTML `<title>`, PDF metadata). `null` when the source states none. Never inferred. |
| `retrieved_at` | string | yes | ISO-8601 UTC Z (S2) at which the bytes were obtained. |
| `access` | enum | yes | `full_text` \| `abstract` \| `preprint` \| `guideline` \| `web`. What kind of evidence this text can support. Closed enum (S6). Mapping to `fulltext.status` in R21. |
| `paper` | object \| null | yes | `{pmid, doi, pmcid}`, each `string \| null`. The whole object is `null` only for sources with no bibliographic identity at all, i.e. `origin: web`. A literature claim must resolve to at least one non-null member (§13 `NO_PAPER_ID`). |
| `origin` | enum | yes | `pubmed` \| `pmc` \| `europepmc` \| `unpaywall` \| `oa-pdf` \| `user-supplied-pdf` \| `web`. The concrete acquisition channel. Closed enum. |
| `asset` | object \| null | yes | `{path, sha256, bytes}` for a snapshot derived from a stored file, else `null`. `path` is **wiki-root-relative** (`assets/papers/pmid-12345678.pdf`) — an explicit exception to S7, because the PDF library is shared across runs and PDFs are never duplicated per run (R13). `sha256` is lowercase hex of the file's bytes (no `sha256:` prefix, matching `corpus.fulltext.sha256`). `bytes` is the file size as an integer. |
| `text` | string | yes | The extracted plain text, decoded UTF-8. This is what spans (§12) index into. Never truncated to fit; never re-flowed, re-wrapped, normalized, or edited after writing. May be `""` only if the source genuinely yielded no text, in which case no span can reference it. |

### Source identity

```text
source_id = "src-" + sha256_hex( utf8(url) || NUL || utf8(text) )
```

- Both `url` and `text` are encoded as **UTF-8**; the separator is a **single literal NUL byte**
  (`0x00`, Python `b"\x00"`), not the two-character sequence backslash-zero.
- The digest is lowercase hexadecimal, 64 characters. `source_id` is the whole 68-character
  string including the `src-` prefix; the filename is `src-<64 hex>.json` (R11).
- `content_hash` covers `text` only and carries the `sha256:` prefix, matching `inputs_hash` in §2.

### Immutability and integrity

- A snapshot file is written **once**, with exclusive creation (`O_EXCL`). Rewriting an existing
  `src-*.json` is an error, not an overwrite — identical content produces the identical path, so
  a re-fetch of unchanged text is a no-op.
- **Every read** of a snapshot (by `source.py read`, `source.py spans`, `assemble.py`,
  `verify.py`, `okf.py promote`) recomputes `content_hash` and `source_id` from the file's own
  `url` and `text` and compares them to the stored values. A mismatch is a hard error: the
  snapshot is treated as tampered, no span resolves against it, and `C-SNAPSHOT` fails.
- Because `text` participates in `source_id`, corrected or re-fetched text is a **new** source,
  never an amendment. Offsets therefore never drift under a claim (R12).

---
