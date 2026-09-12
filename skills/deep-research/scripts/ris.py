#!/usr/bin/env python3
"""ris.py — pure RIS (Research Information Systems) serializer.

Implements REFERENCE_MANAGER_V2_PLAN.md "New Phase 2 — ReadCube export command" >
"Metadata and PDF handling" > "Bibliographic export": a pure transform from a
registry-shaped record (`registry.py` / `corpus.py` `CORPUS_FIELDS` +
`CORPUS_BIBLIO`) into an RIS text block, for the `/deep-research:export readcube`
command's `references.ris` output.

No file or network I/O. Mirrors (does not import) small pieces of `render.py`'s
BibTeX logic — `entry_type_for`, `_year_month`, `_pages`, `_canonical_url`,
`bib_author_name`/`bib_authors`, `build_note` — adapted for RIS's plain-text,
no-escaping, field-per-line convention.

RIS reference: https://en.wikipedia.org/wiki/RIS_(file_format) (background
knowledge only; not fetched here).

Format notes
------------
One record is a block of ``TAG  - value`` lines terminated by a bare
``ER  - `` line. `render_ris_bundle` joins blocks with a single blank line
between them and no trailing blank line after the last block (a single
trailing newline from the last `ER  - ` line only).

Absent values are omitted, never invented — no fabricated dates, no
today's-date defaults, no partial-date guesses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# --------------------------------------------------------------------- model

@dataclass
class RisRecord:
    evidence_id: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    year: str | None = None          # 4-digit string, or None
    month: str | None = None         # numeric "1".."12", or None
    day: str | None = None           # numeric "1".."31", or None (needed for DA)
    volume: str | None = None
    issue: str | None = None
    start_page: str | None = None
    end_page: str | None = None
    url: str | None = None
    abstract: str | None = None
    notes: list[str] = field(default_factory=list)
    ris_type: str = "JOUR"
    is_preprint: bool = False
    citation_key: str | None = None


# ------------------------------------------------------------------ authors

_INITIALS = re.compile(r"^[A-Z][A-Z-]{0,4}$")


def ris_author_name(raw) -> str | None:
    """One corpus author -> plain "Family, Given" text (or a bare collective name).

    Same dispatch logic as `render.py`'s `bib_author_name`, minus BibTeX escaping
    and brace-protection (RIS needs neither).
    """
    if isinstance(raw, dict):
        collective = raw.get("collective")
        if collective:
            return str(collective).strip()
        family = (raw.get("family") or "").strip()
        given = (raw.get("given") or raw.get("initials") or "").strip()
        if not family and not given:
            return None
        if not family:
            return given
        return f"{family}, {given}".rstrip(", ").rstrip()

    name = str(raw or "").strip()
    if not name:
        return None
    tokens = name.split()
    if len(tokens) >= 2 and _INITIALS.fullmatch(tokens[-1]):
        family = " ".join(tokens[:-1])
        initials = tokens[-1]
        given = " ".join(f"{c}." for c in initials if c.isalpha())
        return f"{family}, {given}"
    # No trailing initials: collective/corporate author or a single-token name.
    return name


def ris_authors(record: dict) -> list[str]:
    authors = record.get("authors_structured") or record.get("authors") or []
    return [n for n in (ris_author_name(a) for a in authors) if n]


# ------------------------------------------------------------------- fields

def _year_month_day(record: dict) -> tuple[str | None, str | None, str | None]:
    pub = record.get("publication_date")
    if not pub:
        return None, None, None
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", str(pub))
    if not m:
        return None, None, None
    year = m.group(1)
    month = str(int(m.group(2))) if m.group(2) else None
    day = str(int(m.group(3))) if (m.group(2) and m.group(3)) else None
    return year, month, day


def _split_pages(record: dict) -> tuple[str | None, str | None]:
    """Split a raw `pages` value into (start, end).

    A single page or an unparsable value passes through whole as `start`, with
    `end` left `None`. A range (hyphen or en/em-dash separated) with two distinct,
    non-empty sides splits into (start, end).
    """
    pages = record.get("pages")
    if not pages:
        return None, None
    pages = str(pages).strip()
    if not pages:
        return None, None
    parts = re.split(r"\s*[-–—]+\s*", pages, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1] and parts[0] != parts[1]:
        return parts[0], parts[1]
    return pages, None


def _canonical_url(record: dict) -> str | None:
    if record.get("url"):
        return str(record["url"])
    if record.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{record['pmid']}/"
    if record.get("doi"):
        return f"https://doi.org/{record['doi']}"
    if record.get("pmcid"):
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{record['pmcid']}/"
    return None


def _has_type(record: dict, *needles: str) -> bool:
    types = " | ".join(str(t) for t in (record.get("article_types") or [])).lower()
    return any(n in types for n in needles)


def ris_type_for(record: dict) -> str:
    """Same decision tree as `render.py`'s `entry_type_for`, mapped to RIS `TY` values."""
    if record.get("is_preprint"):
        return "UNPB"
    if _has_type(record, "book chapter", "chapter"):
        return "CHAP"
    if _has_type(record, "book") or (record.get("source") == "book"):
        return "BOOK"
    if record.get("source") in ("guideline", "web"):
        return "GEN"
    if _has_type(record, "guideline", "practice guideline") and not record.get("journal"):
        return "GEN"
    if not record.get("journal"):
        return "GEN"
    return "JOUR"


_TIER_LABELS = {
    0: "local library",
    1: "PMC full text",
    2: "PMC PDF",
    3: "Europe PMC full-text XML",
    4: "Unpaywall location",
    5: "open-access fetch",
    6: "preprint twin",
    7: "browser search/fetch",
    8: "quarantined; full text not obtained",
}


def build_notes(record: dict) -> list[str]:
    """Notes list mirroring `render.py`'s `build_note` (retraction/preprint/fulltext
    status), reused for the RIS `N1` field(s) — one list entry per `N1` line."""
    bits: list[str] = []
    status = record.get("retraction_status") or "none"
    if status == "retracted":
        bits.append("RETRACTED.")
    elif status == "expression_of_concern":
        bits.append("Expression of Concern.")
    elif status == "corrected":
        bits.append("A correction to this article exists.")
    if record.get("is_preprint"):
        bits.append("Preprint. Not peer reviewed.")
    ft = record.get("fulltext") or {}
    ft_status = ft.get("status")
    if ft_status == "abstract_only":
        bits.append("Abstract only; full text not obtained.")
    elif ft_status == "missing":
        bits.append("Full text not obtained (quarantined).")
    tier = ft.get("source_tier")
    if ft_status == "fulltext" and isinstance(tier, int) and tier in _TIER_LABELS:
        bits.append(f"Full text via {_TIER_LABELS[tier]} (source tier {tier}).")
    if record.get("note"):
        bits.append(str(record["note"]))
    return bits


# --------------------------------------------------------------- transform

def build_ris_record(record: dict, citation_key: str | None = None) -> RisRecord:
    """Pure transform: registry-shaped record dict -> `RisRecord`.

    Absent fields become `None`/empty list, never a fabricated default. No
    publication date is invented when none exists.
    """
    ris_type = ris_type_for(record)
    year, month, day = _year_month_day(record)
    start_page, end_page = _split_pages(record)

    notes = build_notes(record)
    if record.get("pmcid"):
        notes.append(f"PMCID: {record['pmcid']}")

    return RisRecord(
        evidence_id=record.get("evidence_id"),
        doi=record.get("doi"),
        pmid=record.get("pmid"),
        pmcid=record.get("pmcid"),
        title=record.get("title"),
        authors=ris_authors(record),
        journal=record.get("journal"),
        year=year,
        month=month,
        day=day,
        volume=record.get("volume"),
        issue=record.get("issue"),
        start_page=start_page,
        end_page=end_page,
        url=_canonical_url(record),
        abstract=record.get("abstract"),
        notes=notes,
        ris_type=ris_type,
        is_preprint=bool(record.get("is_preprint")),
        citation_key=citation_key,
    )


# ------------------------------------------------------------------ escaping

_NEWLINES = re.compile(r"\r\n|\r|\n")


def escape_ris_value(text) -> str:
    """RIS has no character-escaping mechanism, but a value must be one physical
    line. Collapse embedded newlines to a single space and trim edges."""
    if text is None:
        return ""
    return _NEWLINES.sub(" ", str(text)).strip()


# ------------------------------------------------------------------ rendering

def render_ris_record(rec: RisRecord) -> str:
    """Serialize one `RisRecord` to an RIS text block, `ER  - ` terminated.

    No trailing blank line — `render_ris_bundle` owns block separation.
    """
    lines: list[str] = []

    def put(tag: str, value) -> None:
        if value is None:
            return
        text = escape_ris_value(value)
        if not text:
            return
        lines.append(f"{tag}  - {text}")

    put("TY", rec.ris_type)
    for author in rec.authors:
        put("AU", author)
    put("TI", rec.title)
    put("T2", rec.journal)
    put("PY", rec.year)
    if rec.year and rec.month and rec.day:
        put("DA", f"{rec.year}/{int(rec.month):02d}/{int(rec.day):02d}")
    elif rec.year:
        put("DA", rec.year)
    put("VL", rec.volume)
    put("IS", rec.issue)
    put("SP", rec.start_page)
    put("EP", rec.end_page)
    put("DO", rec.doi)
    put("UR", rec.url)
    put("AB", rec.abstract)
    put("AN", rec.pmid)
    for note in rec.notes:
        put("N1", note)
    lines.append("ER  - ")
    return "\n".join(lines)


def render_ris_bundle(records: list[RisRecord]) -> str:
    """Join multiple records' RIS blocks with one blank line between them.

    No blank line after the final block. This is `references.ris`'s full
    contents (a single trailing newline, from the terminal `ER  - ` line)."""
    return "\n\n".join(render_ris_record(rec) for rec in records)
