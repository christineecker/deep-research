#!/usr/bin/env python3
"""assemble.py — the assembler gate (VALIDATION_ARCHITECTURE_PLAN.md Phase 4).

Reads a run's evidence and decides, **per artifact**, whether it is admissible. Writes one
file, `<run>/outputs/result.json`, conforming to `references/schema.md` §13
(`assembler result`). It never modifies an input, never repairs an artifact, and never
silently drops one: an artifact either lands in `accepted[]` or in
`diagnostics.unresolved[]` with a `reason_code`.

Inputs
------
    <run>/workspace/extractions/*.json    §7 extraction records
    <run>/workspace/appraisals/*.json     §8 appraisal records
    <run>/outputs/report.md               report / synthesis claims (footnote citations)
    <run>/sources/src-*.json              immutable snapshots (§10)
    <run>/events.jsonl                    append-only retrieval log (§11)
    <run>/corpus.jsonl                    corpus records (§4), for evidence_id resolution
    <run>/outputs/verification.json       optional, previous run of verify.py (D6)

The derivation rule (schema.md §13, R17) is the point of this script
--------------------------------------------------------------------
Agent records contribute exactly three things: the `claim` string, the `source_id`, and the
`start`/`end` pair. **Everything else is derived from the snapshots** — `url`, `excerpt`,
`access`, `paper`, `section`, `page`, and the extraction record's `quotes[]`, which is
re-sliced here (R17) and written into the accepted artifact; agent-written quote text is
discarded, and where it disagrees with the re-slice the artifact is *rejected*
(`EXCERPT_MISMATCH`), never corrected.

Snapshot hashing, span checking and freshness are **not reimplemented here** (decision D5):
`scripts/store.py` is the single implementation and is imported. One `Store(run_dir)` is
instantiated for the whole run, so each snapshot is verified once rather than once per span.

Checks, one `reason_code` each (schema.md §13)
---------------------------------------------
    UNKNOWN_SOURCE           span names a source_id with no snapshot under sources/
    SNAPSHOT_HASH_MISMATCH   snapshot on disk no longer recomputes to its stored digests
    SPAN_OUT_OF_RANGE        start < 0, start >= end, or end > len(text)
    SPAN_TOO_LONG            end - start > 2000 characters (per span, never summed — R18)
    EXCERPT_MISMATCH         an agent-written excerpt/quote differs from text[start:end]
    NO_SPANS                 a record that §7/§8 requires to carry spans carries none (R16)
    URL_NOT_RETRIEVED        a cited URL has no snapshot retrieved for this artifact this run
    NO_FRESH_FETCH           a source backing the artifact has no fresh retrieval (§11, R15)
    ASSET_HASH_MISMATCH      a user-supplied-pdf asset is missing or no longer hashes right
    UNSUPPORTED_VERDICT      a prior outputs/verification.json calls this claim unsupported
    SOURCE_OUTSIDE_ACCEPTED  a report/synthesis claim rests on evidence that was not accepted
    NO_PAPER_ID              the snapshot's `paper` supplies no identifier, or contradicts
                             the artifact's evidence_id (R14)

Two documented extensions to §13's closed enum, pending a schema amendment:

    ACCESS_MISMATCH          the span record's `access` disagrees with the snapshot's.
                             `store.verify_span` reports this in `warnings[]` rather than as
                             a failure; under the derivation rule the snapshot wins and the
                             artifact is rejected, so the assembler promotes any warning
                             from `verify_span` to a rejection under this code.
    SCHEMA_ERROR             a span record or agent record is structurally malformed
                             (non-integer offsets, missing source_id, evidence_id absent
                             from a non-empty corpus.jsonl).

Legacy records (R16)
--------------------
A pre-kernel extraction/appraisal with no spans is `NO_SPANS` — **unverified, not tampered**.
Its `detail` says so in those words. It is never accepted and never promotable, but it is
reported as an honest gap rather than as an integrity failure.

Verdict-level checks belong to `verify.py`, not here (decision D6). Stage 8 order is
assemble -> verify -> render/html -> `okf promote --check` -> promote (R24).
`UNSUPPORTED_VERDICT` is therefore only meaningful on a re-run where a prior
`verification.json` exists; `verification.json` is never required.

CLI
---
    assemble.py run --run-dir D [--wiki ROOT] [--out outputs/result.json] [--json] [--strict]

`--strict` enables the gate (R20 default is off): it flips `gate.enabled` and turns
`gate.verdict` from `warn` into `fail`. It changes no check and rejects nothing extra —
the same artifacts are accepted either way. `config.json` `gates.evidence_kernel: true`
enables it too.

Exit codes: 0 = gate passed (nothing unresolved), 1 = one or more artifacts unresolved,
2 = fatal error. The only file written is `result.json`, via a temp file and `os.replace`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):                      # sibling import when run as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import store                                        # noqa: E402
from store import Store, StoreError                 # noqa: E402

SCHEMA_VERSION = 1
VERSION = "deep-research/0.1"

#: Reason codes from schema.md §13 plus the two documented extensions above.
REASON_CODES = (
    "UNKNOWN_SOURCE", "SNAPSHOT_HASH_MISMATCH", "SPAN_OUT_OF_RANGE", "SPAN_TOO_LONG",
    "EXCERPT_MISMATCH", "NO_SPANS", "URL_NOT_RETRIEVED", "NO_FRESH_FETCH",
    "UNSUPPORTED_VERDICT", "SOURCE_OUTSIDE_ACCEPTED", "NO_PAPER_ID", "ASSET_HASH_MISMATCH",
    "ACCESS_MISMATCH", "SCHEMA_ERROR",
)

KINDS = ("extraction", "appraisal", "report_claim", "synthesis_claim")

#: Narrative factual fields of an extraction record that owe record-level spans (R19).
NARRATIVE_FIELDS = ("design", "n_total", "n_arms", "population", "intervention",
                    "comparator", "funding", "coi", "limitations")
#: Outcome fields whose presence obliges the outcome entry to carry spans (R19).
OUTCOME_NUMERIC = ("effect", "ci_low", "ci_high", "p_value")

URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"'`,;]+")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:\s*(.*)$")
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
SYNTHESIS_HEADING_RE = re.compile(r"synthes", re.I)


class FatalError(Exception):
    """Something made the run unreadable; exit 2 without writing result.json."""


# ------------------------------------------------------------------------ helpers --


def _short(text, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _norm_url(url: str) -> str:
    """Compare URLs case-insensitively in scheme/host and ignoring one trailing slash.

    Snapshot `url` is stored byte-for-byte (§10) and is never rewritten; this normalization
    is used **only** for the URL_NOT_RETRIEVED comparison, never for identity.
    """
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*)://([^/?#]*)(.*)$", url or "")
    if not m:
        return (url or "").rstrip("/")
    return "%s://%s%s" % (m.group(1).lower(), m.group(2).lower(), m.group(3).rstrip("/"))


def _identifier_urls(paper: dict | None) -> set[str]:
    """Canonical identifier URLs derivable from a snapshot's own `paper` triple.

    `references/reporting.md` §2 mandates PMID/DOI/PMCID links in every footnote. Those are
    identifier renderings of evidence already in the run, not separate retrievals, so they
    are the one thing the URL check accepts without a snapshot of its own — and only when
    built from the artifact's *own* snapshot metadata.
    """
    out: set[str] = set()
    if not paper:
        return out
    pmid, doi, pmcid = paper.get("pmid"), paper.get("doi"), paper.get("pmcid")
    if pmid:
        out.add("https://pubmed.ncbi.nlm.nih.gov/%s" % pmid)
        out.add("https://www.ncbi.nlm.nih.gov/pubmed/%s" % pmid)
    if doi:
        out.add("https://doi.org/%s" % doi)
        out.add("http://dx.doi.org/%s" % doi)
        out.add("https://dx.doi.org/%s" % doi)
    if pmcid:
        out.add("https://pmc.ncbi.nlm.nih.gov/articles/%s" % pmcid)
        out.add("https://www.ncbi.nlm.nih.gov/pmc/articles/%s" % pmcid)
    return {_norm_url(u) for u in out}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _alnum(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _strings(node, out: list[str]) -> list[str]:
    """Every string anywhere in a JSON value (used to find URLs an agent wrote down)."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            _strings(value, out)
    elif isinstance(node, list):
        for value in node:
            _strings(value, out)
    return out


def _urls_in(node) -> list[str]:
    urls: list[str] = []
    for text in _strings(node, []):
        for m in URL_RE.finditer(text):
            urls.append(m.group(0).rstrip(".,;:)»\"'"))
    return urls


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FatalError("cannot read %s: %s" % (path, exc)) from exc


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError as exc:
        raise FatalError("cannot read %s: %s" % (path, exc)) from exc
    return out


# ------------------------------------------------------------------------- corpus --


class Corpus:
    """`corpus.jsonl` as an index from evidence_id and from footnote-key shapes."""

    def __init__(self, records: list[dict]):
        self.records = records
        self.by_evidence: dict[str, dict] = {}
        self.by_pmid: dict[str, str] = {}
        self.by_pmcid: dict[str, str] = {}
        self.by_doi: dict[str, str] = {}
        self.by_slug: dict[str, str] = {}
        self.by_alnum: dict[str, str] = {}
        for rec in records:
            eid = rec.get("evidence_id")
            if not isinstance(eid, str) or not eid:
                continue
            self.by_evidence.setdefault(eid, rec)
            self.by_slug.setdefault(_slug(eid), eid)
            self.by_alnum.setdefault(_alnum(eid), eid)          # R6 bibtex key shape
            if rec.get("pmid"):
                self.by_pmid.setdefault(str(rec["pmid"]), eid)
            if rec.get("pmcid"):
                self.by_pmcid.setdefault(str(rec["pmcid"]).upper(), eid)
            if rec.get("doi"):
                self.by_doi.setdefault(_slug(str(rec["doi"])), eid)

    def __bool__(self) -> bool:
        return bool(self.by_evidence)

    def resolve_key(self, key: str) -> str | None:
        """A report footnote key -> evidence_id (`references/reporting.md` §2 shapes)."""
        if key in self.by_evidence:
            return key
        low = key.lower()
        for prefix in ("pubmed-", "fulltext-", "pmid-"):
            if low.startswith(prefix) and key[len(prefix):] in self.by_pmid:
                return self.by_pmid[key[len(prefix):]]
        for prefix in ("pmc-", "pmcid-"):
            if low.startswith(prefix):
                pmcid = key[len(prefix):].upper()
                pmcid = pmcid if pmcid.startswith("PMC") else "PMC" + pmcid
                if pmcid in self.by_pmcid:
                    return self.by_pmcid[pmcid]
        if low.startswith("doi-") and _slug(key[4:]) in self.by_doi:
            return self.by_doi[_slug(key[4:])]
        if _slug(key) in self.by_slug:
            return self.by_slug[_slug(key)]
        if _alnum(key) in self.by_alnum:
            return self.by_alnum[_alnum(key)]
        return None


def _evidence_identifiers(evidence_id: str) -> tuple[str, str] | None:
    """`pmid:12345678` -> `("pmid", "12345678")`. None for `url:` and unknown shapes."""
    if not isinstance(evidence_id, str) or ":" not in evidence_id:
        return None
    scheme, _, value = evidence_id.partition(":")
    scheme = scheme.lower()
    if scheme in ("pmid", "doi", "pmcid") and value:
        return scheme, value
    return None


def _same_identifier(scheme: str, got: str, want: str) -> bool:
    """Compare one identifier from a snapshot's `paper` with one from an `evidence_id`."""
    a, b = got.strip().lower(), want.strip().lower()
    if scheme == "doi":
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            a = a[len(prefix):] if a.startswith(prefix) else a
            b = b[len(prefix):] if b.startswith(prefix) else b
    return a == b


# ------------------------------------------------------------------------ report --


def parse_report(path: Path) -> dict:
    """Footnote citations on prose lines, plus footnote definitions.

    Returns ``{"refs": [(key, line_no, section_title)], "defs": {key: (line_no, body)}}``.
    Frontmatter, fenced code, HTML comments and footnote definitions are not prose.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    defs: dict[str, tuple[int, str]] = {}
    def_lines: set[int] = set()
    for i, raw in enumerate(lines):
        m = FOOTNOTE_DEF_RE.match(raw)
        if not m:
            continue
        body = [m.group(2)]
        def_lines.add(i)
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if nxt.startswith(("    ", "\t")) and nxt.strip():
                body.append(nxt.strip())
                def_lines.add(j)
            else:
                break
        defs.setdefault(m.group(1), (i + 1, " ".join(body)))

    fm_end = -1
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                fm_end = i
                break

    refs: list[tuple[str, int, str]] = []
    in_fence = in_comment = False
    section = ""
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if i <= fm_end:
            continue
        if in_comment:
            in_comment = "-->" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or i in def_lines:
            continue
        m = HEADING_RE.match(stripped)
        if m:
            section = m.group(2).strip()
            continue
        for r in FOOTNOTE_REF_RE.finditer(raw):
            if raw[r.end():r.end() + 1] == ":":
                continue
            refs.append((r.group(1), i + 1, section))
    return {"refs": refs, "defs": defs, "lines": lines}


# --------------------------------------------------------------------- artifacts --


class Artifact:
    """One thing the gate decides about: a record, or one cited claim in the report."""

    def __init__(self, artifact_id: str, kind: str, evidence_id: str | None, *,
                 record=None, path: str | None = None, spans=None, quotes=None,
                 urls=None, location: str | None = None):
        self.artifact_id = artifact_id
        self.kind = kind
        self.evidence_id = evidence_id
        self.record = record if record is not None else {}
        self.path = path
        self.spans: list[tuple[str | None, dict]] = list(spans or [])
        self.quotes: list[dict] = list(quotes or [])
        self.urls: list[str] = list(urls or [])
        self.location = location
        self.no_spans_detail: str | None = None
        self.owed_field: str | None = None


def _spans_of(node) -> list[dict]:
    spans = node.get("spans") if isinstance(node, dict) else None
    return [s for s in spans if isinstance(s, dict)] if isinstance(spans, list) else []


def extraction_artifact(rec: dict, rel_path: str) -> Artifact:
    """Build the artifact for a §7 extraction record, including its R19 span duties."""
    evidence_id = rec.get("evidence_id") if isinstance(rec.get("evidence_id"), str) else None
    art = Artifact("extraction:%s" % (evidence_id or rel_path), "extraction", evidence_id,
                   record=rec, path=rel_path, urls=_urls_in(rec))
    record_spans = _spans_of(rec)
    for i, span in enumerate(record_spans):
        art.spans.append(("spans[%d]" % i, span))
    outcomes = rec.get("outcomes") if isinstance(rec.get("outcomes"), list) else []
    owed: list[str] = []
    for i, outcome in enumerate(outcomes):
        if not isinstance(outcome, dict):
            continue
        spans = _spans_of(outcome)
        for j, span in enumerate(spans):
            art.spans.append(("outcomes[%d]" % i, span))
        if not spans and any(outcome.get(k) is not None for k in OUTCOME_NUMERIC):
            owed.append("outcomes[%d]" % i)
    narrative = [f for f in NARRATIVE_FIELDS
                 if rec.get(f) not in (None, [], "") and rec.get(f) is not False]
    if narrative and not record_spans:
        owed.insert(0, "spans")
    if owed:
        if not art.spans:
            art.no_spans_detail = (
                "pre-kernel record with no spans anywhere: unverified (R16), NOT tampered; "
                "owes spans for %s" % ", ".join(owed[:6]))
        else:
            art.no_spans_detail = ("record carries spans but owes more (R19): %s"
                                   % ", ".join(owed[:6]))
        art.owed_field = owed[0]
    quotes = rec.get("quotes")
    art.quotes = [q for q in quotes if isinstance(q, dict)] if isinstance(quotes, list) else []
    return art


def appraisal_artifact(rec: dict, rel_path: str) -> Artifact:
    """Build the artifact for a §8 appraisal record, including its R19 span duties."""
    evidence_id = rec.get("evidence_id") if isinstance(rec.get("evidence_id"), str) else None
    art = Artifact("appraisal:%s" % (evidence_id or rel_path), "appraisal", evidence_id,
                   record=rec, path=rel_path, urls=_urls_in(rec))
    domains = rec.get("domains") if isinstance(rec.get("domains"), list) else []
    owed: list[str] = []
    for i, domain in enumerate(domains):
        if not isinstance(domain, dict):
            continue
        spans = _spans_of(domain)
        for span in spans:
            art.spans.append(("domains[%d]" % i, span))
        if not spans and domain.get("judgement") != "unclear":
            owed.append("domains[%d]" % i)
    if owed:
        art.no_spans_detail = (
            "unverified (R16), NOT tampered: %s carr%s a judgement other than 'unclear' with "
            "spans: []" % (", ".join(owed[:6]), "ies" if len(owed) == 1 else "y"))
        art.owed_field = owed[0]
    elif not art.spans:
        art.no_spans_detail = (
            "no spans to check: tool=%r with every domain 'unclear' or no domains — an honest "
            "empty record (R19), unverified rather than accepted, and NOT tampered"
            % rec.get("tool"))
        art.owed_field = None
    return art


# ---------------------------------------------------------------------- assembler --


class Assembler:
    """One pass over one run directory. Instantiates exactly one `store.Store`."""

    def __init__(self, run_dir: Path, *, wiki_root: Path | None = None,
                 strict: bool = False):
        self.run_dir = Path(run_dir).expanduser().resolve()
        if not self.run_dir.is_dir():
            raise FatalError("no such run directory: %s" % self.run_dir)
        self.store = Store(self.run_dir, wiki_root)      # D5: hashed once, not per span
        self.corpus = Corpus(_read_jsonl(self.run_dir / "corpus.jsonl"))
        self.strict = strict
        self.accepted: list[dict] = []
        self.unresolved: list[dict] = []
        self.spans_checked = 0
        self.referenced_sources: list[str] = []
        self._snapshot_urls: dict[str, str] | None = None
        self._evented: set[str] | None = None
        self.gate_enabled = strict or self._config_gate()
        self.unsupported_locations, self.unsupported_evidence = self._prior_unsupported()

    # -- run-level inputs ---------------------------------------------------

    def _config_gate(self) -> bool:
        path = self.run_dir / "config.json"
        if not path.is_file():
            return False
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        gates = cfg.get("gates") if isinstance(cfg, dict) else None
        return bool(isinstance(gates, dict) and gates.get("evidence_kernel"))

    def _prior_unsupported(self) -> tuple[set[int], set[str]]:
        """Locations/evidence ids a *previous* `verify.py` called unsupported (D6).

        `verification.json` is never required: on a first run it does not exist yet, because
        the assembler runs before the verifier (R24).
        """
        path = self.run_dir / "outputs" / "verification.json"
        if not path.is_file():
            return set(), set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set(), set()
        lines: set[int] = set()
        evidence: set[str] = set()
        for item in (data.get("unsupported_claims") or []):
            if not isinstance(item, dict):
                continue
            m = re.search(r":L(\d+)\b", str(item.get("location") or ""))
            if m:
                lines.add(int(m.group(1)))
            for key in ("evidence_id", "artifact_id"):
                if isinstance(item.get(key), str):
                    evidence.add(item[key])
        return lines, evidence

    @property
    def snapshot_urls(self) -> dict[str, str]:
        """source_id -> snapshot url, for every snapshot in the run that still verifies."""
        if self._snapshot_urls is None:
            urls = {}
            for source_id in self.store.list_snapshots():
                try:
                    urls[source_id] = self.store.read_snapshot(source_id)["url"]
                except StoreError:
                    continue
            self._snapshot_urls = urls
        return self._snapshot_urls

    @property
    def evented_sources(self) -> set[str]:
        """source_ids named by at least one line of `events.jsonl` — "was retrieved"."""
        if self._evented is None:
            self._evented = {ev.get("source_id") for ev in self.store.events
                             if not ev.get("_invalid")}
        return self._evented

    # -- bookkeeping --------------------------------------------------------

    def _note_source(self, source_id) -> None:
        if isinstance(source_id, str) and source_id not in self.referenced_sources:
            self.referenced_sources.append(source_id)

    def _reject(self, art: Artifact, reason_code: str, detail: str,
                field: str | None = None, extra: int = 0) -> None:
        assert reason_code in REASON_CODES, reason_code
        if extra:
            detail = "%s (+%d further failure%s in this artifact)" % (
                detail, extra, "" if extra == 1 else "s")
        evidence_id = art.evidence_id
        if evidence_id is not None and self.corpus and evidence_id not in self.corpus.by_evidence:
            evidence_id = None          # §13: null when the id is absent from corpus.jsonl
        self.unresolved.append({
            "artifact_id": art.artifact_id,
            "kind": art.kind,
            "evidence_id": evidence_id,
            "field": field,
            "reason_code": reason_code,
            "detail": _short(detail),
        })

    # -- per-artifact checks ------------------------------------------------

    def _check_spans(self, art: Artifact) -> tuple[list[dict], list[tuple[str, str, str]]]:
        """Resolve every span. Returns (derived claims, failures)."""
        claims: list[dict] = []
        failures: list[tuple[str, str, str]] = []       # (reason_code, detail, field)
        quote_index = {(q.get("source_id"), q.get("start"), q.get("end")): q
                       for q in art.quotes
                       if isinstance(q.get("text"), str)}
        for field, span in art.spans:
            self.spans_checked += 1
            self._note_source(span.get("source_id"))
            if not isinstance(span.get("claim"), str):
                failures.append(("SCHEMA_ERROR",
                                 "span record at %s has no `claim` string" % field, field))
                continue
            res = self.store.verify_span(span)          # excerpt/text keys are picked up
            if not res["ok"]:
                code = res["reason_code"] if res["reason_code"] in REASON_CODES else "SCHEMA_ERROR"
                failures.append((code, "%s: %s" % (field, res["detail"]), field))
                continue
            if res["warnings"]:
                # store.py reports an access disagreement as a warning; under the derivation
                # rule the snapshot wins and the artifact is rejected, not corrected.
                failures.append(("ACCESS_MISMATCH", "%s: %s" % (field, res["warnings"][0]), field))
                continue
            key = (span.get("source_id"), span.get("start"), span.get("end"))
            legacy = quote_index.get(key)
            if legacy is not None and legacy["text"] != res["excerpt"]:
                failures.append((
                    "EXCERPT_MISMATCH",
                    "%s: agent-written quotes[] text (%d chars) differs from the re-slice of "
                    "%s[%s:%s] (%d chars); quotes are derived, never authored (R17)"
                    % (field, len(legacy["text"]), key[0], key[1], key[2], len(res["excerpt"])),
                    field))
                continue
            claims.append({
                "claim": span["claim"],                  # the only agent string that survives
                "field": field,
                "source_id": span["source_id"],
                "start": span["start"],
                "end": span["end"],
                "access": res["access"],                 # DERIVED from the snapshot
                "excerpt": res["excerpt"],               # DERIVED by re-slicing
                "section": None,                         # §10 carries no section map
                "page": None,                            # §10 carries no page map
            })
        return claims, failures

    def _check_paper(self, art: Artifact, source_ids: list[str]) -> list[tuple[str, str, str]]:
        """R14/P4: the snapshot `paper` triple must supply an id and agree with evidence_id."""
        failures: list[tuple[str, str, str]] = []
        ident = _evidence_identifiers(art.evidence_id or "")
        if ident is None:
            return failures                              # url:-keyed / web evidence, not literature
        scheme, value = ident
        for source_id in source_ids:
            try:
                paper = self.store.read_snapshot(source_id).get("paper")
            except StoreError:
                continue                                 # already reported by the span check
            if not paper or not any(paper.get(k) for k in ("pmid", "doi", "pmcid")):
                failures.append(("NO_PAPER_ID",
                                 "snapshot %s carries no PMID, DOI or PMCID; a literature claim "
                                 "must trace to one (P4)" % source_id, None))
                continue
            got = paper.get(scheme)
            if not (isinstance(got, str) and _same_identifier(scheme, got, value)):
                failures.append(("NO_PAPER_ID",
                                 "evidence_id %s disagrees with snapshot %s paper.%s=%r (R14)"
                                 % (art.evidence_id, source_id, scheme, got), None))
        return failures

    def _check_urls(self, art: Artifact, source_ids: list[str]) -> list[tuple[str, str, str]]:
        """URL_NOT_RETRIEVED: a cited URL with no retrieval for this artifact in this run."""
        allowed: set[str] = set()
        for source_id in source_ids:
            try:
                snap = self.store.read_snapshot(source_id)
            except StoreError:
                continue
            allowed.add(_norm_url(snap["url"]))
            allowed |= _identifier_urls(snap.get("paper"))
        # Any other snapshot of the *same study* that was retrieved in this run.
        ident = _evidence_identifiers(art.evidence_id or "")
        if ident:
            scheme, value = ident
            for source_id, url in self.snapshot_urls.items():
                if source_id not in self.evented_sources:
                    continue
                try:
                    paper = self.store.read_snapshot(source_id).get("paper") or {}
                except StoreError:
                    continue
                got = paper.get(scheme)
                if isinstance(got, str) and got.strip().lower() == value.strip().lower():
                    allowed.add(_norm_url(url))
                    allowed |= _identifier_urls(paper)
        failures = []
        for url in art.urls:
            if _norm_url(url) not in allowed:
                failures.append(("URL_NOT_RETRIEVED",
                                 "cited URL %s has no snapshot retrieved for this artifact in "
                                 "this run" % _short(url, 120), None))
        return failures

    def _check_freshness(self, art: Artifact, source_ids: list[str]) -> list[tuple[str, str, str]]:
        """§11/R15. An accepted artifact asserts support, so every source must be fresh."""
        failures = []
        for source_id in source_ids:
            res = self.store.freshness(source_id)
            if res["fresh"]:
                continue
            code = res["reason_code"] if res["reason_code"] in REASON_CODES else "NO_FRESH_FETCH"
            failures.append((code, "%s: %s" % (source_id, res["detail"]), None))
        return failures

    # -- artifact drivers ---------------------------------------------------

    def assess_record(self, art: Artifact) -> dict | None:
        """Run every check for an extraction/appraisal artifact. Returns the accepted entry."""
        if art.evidence_id is None:
            self._reject(art, "SCHEMA_ERROR", "record has no `evidence_id` string (§7/§8)")
            return None
        if self.corpus and art.evidence_id not in self.corpus.by_evidence:
            self._reject(art, "SCHEMA_ERROR",
                         "evidence_id %s is absent from corpus.jsonl" % art.evidence_id)
            return None
        if art.no_spans_detail is not None:
            for _field, span in art.spans:
                self._note_source(span.get("source_id"))
            self._reject(art, "NO_SPANS", art.no_spans_detail,
                         field=getattr(art, "owed_field", None))
            return None
        if not art.spans:
            self._reject(art, "NO_SPANS",
                         "record carries no spans: unverified (R16), NOT tampered")
            return None

        claims, failures = self._check_spans(art)
        source_ids: list[str] = []
        for claim in claims:
            if claim["source_id"] not in source_ids:
                source_ids.append(claim["source_id"])
        if not failures:
            failures += self._check_paper(art, source_ids)
        if not failures:
            failures += self._check_urls(art, source_ids)
        if not failures:
            failures += self._check_freshness(art, source_ids)
        if not failures and art.evidence_id in self.unsupported_evidence:
            failures.append(("UNSUPPORTED_VERDICT",
                             "a previous outputs/verification.json records an unsupported claim "
                             "for %s" % art.evidence_id, None))
        if failures:
            code, detail, field = failures[0]
            self._reject(art, code, detail, field=field, extra=len(failures) - 1)
            return None
        return self._accepted_entry(art, source_ids, claims)

    def _accepted_entry(self, art: Artifact, source_ids: list[str],
                        claims: list[dict]) -> dict:
        first = self.store.read_snapshot(source_ids[0])
        access = min((self.store.read_snapshot(s)["access"] for s in source_ids),
                     key=lambda a: store.ACCESS_STRENGTH.get(a, 0))
        entry = {
            "artifact_id": art.artifact_id,
            "kind": art.kind,
            "evidence_id": art.evidence_id,
            "source_ids": source_ids,
            "paper": first.get("paper"),                 # DERIVED
            "url": first["url"],                         # DERIVED
            "access": access,                            # DERIVED, weakest wins (§13)
            "fresh": True,
            "claims": claims,
        }
        if art.kind == "extraction":
            # R17: `quotes[]` is derived here by re-slicing, in span order. Whatever the agent
            # wrote under `quotes` was discarded above (and rejected where it disagreed).
            entry["quotes"] = [{"text": c["excerpt"], "section": c["section"],
                                "page": c["page"], "source_id": c["source_id"],
                                "start": c["start"], "end": c["end"]} for c in claims]
        return entry

    def assess_report(self, report_path: Path) -> None:
        """Report/synthesis claims: they may rest only on accepted evidence (§13)."""
        parsed = parse_report(report_path)
        accepted_by_evidence: dict[str, list[dict]] = {}
        for entry in self.accepted:
            accepted_by_evidence.setdefault(entry["evidence_id"], []).append(entry)

        by_line: dict[int, list[str]] = {}
        for key, line, _section in parsed["refs"]:
            by_line.setdefault(line, []).append(key)

        for key, line, section in parsed["refs"]:
            kind = "synthesis_claim" if SYNTHESIS_HEADING_RE.search(section or "") else "report_claim"
            artifact_id = ("report:L%d" % line if len(by_line[line]) == 1
                           else "report:L%d#%s" % (line, key))
            evidence_id = self.corpus.resolve_key(key) if self.corpus else None
            def_line, def_body = parsed["defs"].get(key, (None, ""))
            urls = _urls_in([def_body, parsed["lines"][line - 1]])
            art = Artifact(artifact_id, kind, evidence_id, urls=urls,
                           location="report.md:L%d" % line)
            if line in self.unsupported_locations:
                self._reject(art, "UNSUPPORTED_VERDICT",
                             "a previous outputs/verification.json marks report.md:L%d "
                             "unsupported" % line)
                continue
            if evidence_id is None:
                self._reject(art, "SOURCE_OUTSIDE_ACCEPTED",
                             "footnote key [^%s] at report.md:L%d resolves to no corpus record"
                             % (key, line))
                continue
            backing = accepted_by_evidence.get(evidence_id)
            if not backing:
                self._reject(art, "SOURCE_OUTSIDE_ACCEPTED",
                             "report.md:L%d cites %s, which has no accepted evidence in this run"
                             % (line, evidence_id))
                continue
            claims, seen = [], set()
            source_ids: list[str] = []
            for entry in backing:
                for claim in entry["claims"]:
                    key3 = (claim["source_id"], claim["start"], claim["end"])
                    if key3 in seen:
                        continue
                    seen.add(key3)
                    inherited = dict(claim)
                    inherited["field"] = None            # §13: null for a report claim
                    claims.append(inherited)
                    if claim["source_id"] not in source_ids:
                        source_ids.append(claim["source_id"])
            for source_id in source_ids:
                self._note_source(source_id)
            failures = self._check_urls(art, source_ids)
            if failures:
                code, detail, field = failures[0]
                self._reject(art, code, detail, field=field, extra=len(failures) - 1)
                continue
            self.accepted.append(self._accepted_entry(art, source_ids, claims))

    # -- run ----------------------------------------------------------------

    def _load_records(self, subdir: str, kind: str, builder) -> list[Artifact]:
        """Load one workspace directory. A file that is not a JSON object is rejected, not
        skipped — the assembler never silently drops an artifact."""
        directory = self.run_dir / "workspace" / subdir
        out: list[Artifact] = []
        if not directory.is_dir():
            return out
        for path in sorted(directory.glob("*.json")):
            rec = _read_json(path)
            rel = os.path.relpath(path, self.run_dir)
            if not isinstance(rec, dict):
                self._reject(Artifact("%s:%s" % (kind, rel), kind, None, path=rel),
                             "SCHEMA_ERROR", "%s is not a JSON object" % rel)
                continue
            out.append(builder(rec, rel))
        return out

    def run(self) -> dict:
        before_bad = len(self.unresolved)
        artifacts = (self._load_records("extractions", "extraction", extraction_artifact)
                     + self._load_records("appraisals", "appraisal", appraisal_artifact))
        unreadable = len(self.unresolved) - before_bad
        for art in artifacts:
            entry = self.assess_record(art)
            if entry is not None:
                self.accepted.append(entry)
        report_path = self.run_dir / "outputs" / "report.md"
        report_artifacts = 0
        if report_path.is_file():
            before = len(self.accepted) + len(self.unresolved)
            self.assess_report(report_path)
            report_artifacts = len(self.accepted) + len(self.unresolved) - before
        return self._result(len(artifacts) + unreadable + report_artifacts)

    def _sources_block(self) -> list[dict]:
        """Snapshot metadata for every referenced source that still verifies (§13).

        A referenced snapshot that is unknown or tampered is deliberately absent here — it
        cannot be described without trusting it — and is instead named in the `detail` of the
        `UNKNOWN_SOURCE` / `SNAPSHOT_HASH_MISMATCH` entry that rejected its artifact.
        """
        out = []
        for source_id in self.referenced_sources:
            try:
                snap = self.store.read_snapshot(source_id)
            except StoreError:
                continue
            fresh = self.store.freshness(source_id)
            out.append({
                "source_id": snap["source_id"],
                "content_hash": snap["content_hash"],
                "url": snap["url"],
                "title": snap["title"],
                "access": snap["access"],
                "origin": snap["origin"],
                "paper": snap["paper"],
                "asset": snap["asset"],
                "fresh": bool(fresh["fresh"]),
                "fresh_event_id": fresh["fresh_event_id"],
            })
        return out

    def _result(self, artifacts_seen: int) -> dict:
        counts_by_reason: dict[str, int] = {}
        for item in self.unresolved:
            counts_by_reason[item["reason_code"]] = counts_by_reason.get(item["reason_code"], 0) + 1
        sources = self._sources_block()
        verdict = ("pass" if not self.unresolved
                   else ("fail" if self.gate_enabled else "warn"))
        return {
            "schema_version": SCHEMA_VERSION,
            "run_slug": self.run_dir.name,
            "generated_at": store.utcnow(),
            "gate": {"enabled": bool(self.gate_enabled), "verdict": verdict,
                     "flag": "--strict"},
            "counts": {
                "artifacts_seen": artifacts_seen,
                "accepted": len(self.accepted),
                "unresolved": len(self.unresolved),
                "spans_checked": self.spans_checked,
                "sources": len(sources),
            },
            "accepted": self.accepted,
            "diagnostics": {"unresolved": self.unresolved,
                            "counts_by_reason": counts_by_reason},
            "sources": sources,
        }


# ---------------------------------------------------------------------- writing ---


def write_result(result: dict, out_path: Path) -> Path:
    """Atomic write: temp file beside the target, then `os.replace` (§13)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp-%d" % os.getpid())
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, out_path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return out_path


# -------------------------------------------------------------------------- cli ---


def _summary(result: dict, out_path: Path) -> str:
    counts = result["counts"]
    lines = [
        "assembler gate: %s (gate %s)" % (result["gate"]["verdict"],
                                          "on" if result["gate"]["enabled"] else "off"),
        "  artifacts %d  accepted %d  unresolved %d  spans %d  sources %d"
        % (counts["artifacts_seen"], counts["accepted"], counts["unresolved"],
           counts["spans_checked"], counts["sources"]),
    ]
    by_reason = result["diagnostics"]["counts_by_reason"]
    for code in sorted(by_reason):
        lines.append("  %-24s %d" % (code, by_reason[code]))
    for item in result["diagnostics"]["unresolved"][:20]:
        lines.append("  - %s [%s] %s" % (item["artifact_id"], item["reason_code"],
                                         item["detail"]))
    extra = len(result["diagnostics"]["unresolved"]) - 20
    if extra > 0:
        lines.append("  ... and %d more in %s" % (extra, out_path))
    lines.append("wrote %s" % out_path)
    return "\n".join(lines)


def cmd_run(args) -> int:
    run_dir = Path(args.run_dir).expanduser()
    asm = Assembler(run_dir, wiki_root=Path(args.wiki).expanduser() if args.wiki else None,
                    strict=bool(args.strict))
    result = asm.run()
    out = Path(args.out).expanduser()
    if not out.is_absolute():
        out = asm.run_dir / out
    write_result(result, out)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_summary(result, out))
    return 0 if not result["diagnostics"]["unresolved"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="assemble.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run", help="assemble a run and write outputs/result.json")
    s.add_argument("--run-dir", required=True, dest="run_dir",
                   help="<wiki>/outputs/deep-research/<slug>/")
    s.add_argument("--wiki", help="wiki root for asset paths (default: inferred from --run-dir)")
    s.add_argument("--out", default="outputs/result.json",
                   help="output path, absolute or run-relative (default: outputs/result.json)")
    s.add_argument("--json", action="store_true", help="print the full result to stdout")
    s.add_argument("--strict", action="store_true",
                   help="enable the gate (R20 default off): gate.verdict becomes 'fail' "
                        "instead of 'warn'. Accepts and rejects exactly the same artifacts.")
    s.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FatalError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "error_type": "FatalError"},
                         indent=2), file=sys.stderr)
        return 2
    except StoreError as exc:
        print(json.dumps(exc.to_json(), indent=2), file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "error_type": type(exc).__name__}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
