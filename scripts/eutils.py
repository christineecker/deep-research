#!/usr/bin/env python3
"""NCBI E-utilities client for deep-research (`SKILL.md` "Scripts"; stage 2 of the pipeline).

Three operations, usable as a CLI or as an importable module:

  esearch  query (+ filters) -> hit count, NCBI QueryTranslation, PMID pages
           (`search result record`, references/schema.md §3)
  efetch   PMIDs -> PubMed XML -> normalized bibliographic JSON
           (corpus/OKF bibliographic fields, references/schema.md §4 / `references/okf-bundle.md`)
  elink    citation chaining: pubmed_pubmed_citedin | pubmed_pubmed_refs | pubmed_pubmed

Environment
  NCBI_API_KEY           optional; present -> 10 req/s, absent -> 3 req/s
  DEEP_RESEARCH_EMAIL    contact address sent as `email=` per NCBI policy (or --email)
  DEEP_RESEARCH_FIXTURES directory of recorded responses; set -> no network at all
  DEEP_RESEARCH_RECORD   directory to write live responses into (record mode)

Fixture keys are sha256 over endpoint + request params, excluding the volatile
`api_key`, `email`, `tool` and `WebEnv` values, so fixtures are portable across
machines. Filename: `<endpoint>-<sha16>.<json|xml|txt>`.

stdlib + requests only (`SKILL.md` "Scripts": no lxml/bs4/habanero, zero pip installs).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Sequence

import requests

SCHEMA_VERSION = 1
TOOL_NAME = "deep-research"
BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EFETCH_CHUNK = 200          # PMIDs per efetch POST
ESEARCH_ID_CEILING = 9999   # esearch retstart ceiling; beyond it use history + uilist
MAX_PAGE = 10000            # NCBI retmax hard cap
DEFAULT_MAX_RECORDS = 10000
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 4

LINKNAMES = ("pubmed_pubmed_citedin", "pubmed_pubmed_refs", "pubmed_pubmed")

_VOLATILE_PARAMS = frozenset({"api_key", "email", "tool", "WebEnv"})


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #


class EutilsError(Exception):
    """Carries a JSON-serializable error object (honest, no silent fallbacks)."""

    def __init__(self, kind: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "error": self.kind,
            "message": self.message,
            "detail": self.detail,
            "occurred_at": _now(),
        }


# --------------------------------------------------------------------------- #
# time / throttle
# --------------------------------------------------------------------------- #


def _now() -> str:
    """ISO-8601 UTC with literal Z (schema.md rule S2)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Throttle:
    """Token bucket shared by every call type in this process."""

    def __init__(self, rate: float) -> None:
        self.rate = rate
        self.capacity = rate
        self._tokens = rate
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def set_rate(self, rate: float) -> None:
        with self._lock:
            self.rate = rate
            self.capacity = rate
            self._tokens = min(self._tokens, rate)

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._last) * self.rate
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            time.sleep(wait)


def _default_rate() -> float:
    return 10.0 if os.environ.get("NCBI_API_KEY") else 3.0


_THROTTLE = _Throttle(_default_rate())


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #


def _fixture_key(endpoint: str, params: dict[str, Any]) -> str:
    stable = {k: v for k, v in sorted(params.items()) if k not in _VOLATILE_PARAMS}
    blob = endpoint + "?" + json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _fixture_path(directory: str, endpoint: str, params: dict[str, Any]) -> str:
    retmode = str(params.get("retmode", "xml"))
    ext = {"json": "json", "text": "txt"}.get(retmode, "xml")
    return os.path.join(directory, f"{endpoint}-{_fixture_key(endpoint, params)}.{ext}")


def request(
    endpoint: str,
    params: dict[str, Any],
    *,
    method: str = "GET",
    email: str | None = None,
    tool: str = TOOL_NAME,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRIES,
) -> str:
    """One throttled, retried E-utilities call. Returns the raw response body.

    Honors DEEP_RESEARCH_FIXTURES (replay, no network) and DEEP_RESEARCH_RECORD.
    """
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    params["tool"] = tool
    email = email or os.environ.get("DEEP_RESEARCH_EMAIL")
    if email:
        params["email"] = email

    fixtures = os.environ.get("DEEP_RESEARCH_FIXTURES")
    if fixtures:
        path = _fixture_path(fixtures, endpoint, params)
        if not os.path.exists(path):
            raise EutilsError(
                "fixture_missing",
                f"no recorded fixture for {endpoint}",
                expected_path=path,
                params={k: v for k, v in params.items() if k not in _VOLATILE_PARAMS},
            )
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    _THROTTLE.set_rate(10.0 if api_key else 3.0)
    url = f"{BASE_URL}/{endpoint}.fcgi"
    last: dict[str, Any] = {}
    for attempt in range(1, max_retries + 1):
        _THROTTLE.acquire()
        try:
            if method.upper() == "POST":
                resp = requests.post(url, data=params, timeout=timeout)
            else:
                resp = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last = {"reason": "transport", "exception": type(exc).__name__, "message": str(exc)}
        else:
            if resp.status_code == 200:
                body = resp.text
                record_dir = os.environ.get("DEEP_RESEARCH_RECORD")
                if record_dir:
                    os.makedirs(record_dir, exist_ok=True)
                    with open(
                        _fixture_path(record_dir, endpoint, params), "w", encoding="utf-8"
                    ) as fh:
                        fh.write(body)
                return body
            last = {
                "reason": "http_status",
                "status_code": resp.status_code,
                "body_head": resp.text[:400],
            }
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
        if attempt < max_retries:
            time.sleep(min(2.0 ** (attempt - 1), 16.0) + random.uniform(0, 0.4))
    raise EutilsError(
        "request_failed",
        f"{endpoint} failed after {max_retries} attempt(s)",
        endpoint=endpoint,
        attempts=max_retries,
        **last,
    )


# --------------------------------------------------------------------------- #
# query building (`references/search-strategy.md` filter -> field tag table)
# --------------------------------------------------------------------------- #

AUTHOR_POSITION_TAGS = {"any": "au", "first": "1au", "last": "lastau"}

SPECIES_TAGS = {
    "human": '"humans"[mh]',
    "humans": '"humans"[mh]',
    "animal": '"animals"[mh]',
    "animals": '"animals"[mh]',
}

# PubMed age filters are MeSH check tags.
AGE_TAGS = {
    "newborn": '"infant, newborn"[mh]',
    "infant": '"infant"[mh]',
    "preschool child": '"child, preschool"[mh]',
    "child": '"child"[mh]',
    "adolescent": '"adolescent"[mh]',
    "young adult": '"young adult"[mh]',
    "adult": '"adult"[mh]',
    "middle aged": '"middle aged"[mh]',
    "aged": '"aged"[mh]',
    "aged 80 and over": '"aged, 80 and over"[mh]',
}


def _tagged(value: str, tag: str) -> str:
    """Field-tag one term, quoting multiword values (author names stay unquoted)."""
    value = value.strip()
    if value.endswith("]"):  # already a fully formed PubMed term
        return value
    quoted = tag not in ("au", "1au", "lastau") and (" " in value or "-" in value)
    if quoted and not (value.startswith('"') and value.endswith('"')):
        value = f'"{value}"'
    return f"{value}[{tag}]"


def _or_group(values: Sequence[str], tag: str) -> str | None:
    terms = [_tagged(v, tag) for v in values if str(v).strip()]
    if not terms:
        return None
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


def _years_clause(years: Any) -> str | None:
    """years: [start, end] | {'start':..,'end':..} | 'YYYY-YYYY' | 'YYYY'."""
    if years is None:
        return None
    if isinstance(years, str):
        parts = [p.strip() for p in years.replace(":", "-").split("-") if p.strip()]
        start, end = (parts + parts)[:2]
    elif isinstance(years, dict):
        start = str(years.get("start") or years.get("from") or "1800")
        end = str(years.get("end") or years.get("to") or "3000")
    elif isinstance(years, (list, tuple)) and years:
        start = str(years[0])
        end = str(years[1]) if len(years) > 1 else str(years[0])
    else:
        raise EutilsError("bad_filter", "years filter must be str, list or dict", years=years)
    return f'("{start}"[dp] : "{end}"[dp])'


def _authors_clause(authors: Any) -> str | None:
    """authors: ['Kaufmann J'] or [{'name':'Kaufmann J','position':'last'}]."""
    if not authors:
        return None
    terms: list[str] = []
    for entry in authors:
        if isinstance(entry, dict):
            name = str(entry.get("name", "")).strip()
            position = str(entry.get("position", "any")).lower()
        else:
            name, position = str(entry).strip(), "any"
        if not name:
            continue
        tag = AUTHOR_POSITION_TAGS.get(position)
        if tag is None:
            raise EutilsError(
                "bad_filter",
                "author position must be any|first|last",
                position=position,
            )
        terms.append(_tagged(name, tag))
    if not terms:
        return None
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


def _mapped_group(values: Sequence[str], table: dict[str, str], kind: str) -> str | None:
    terms: list[str] = []
    for raw in values:
        key = str(raw).strip().lower()
        term = table.get(key)
        if term is None:
            if key.endswith("]"):
                term = str(raw).strip()
            else:
                raise EutilsError(
                    "bad_filter",
                    f"unknown {kind} filter value",
                    value=raw,
                    allowed=sorted(table),
                )
        terms.append(term)
    if not terms:
        return None
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


def build_query(base: str, filters: dict[str, Any] | None = None) -> str:
    """Compose base query + `references/search-strategy.md` filters with explicit boolean nesting.

    filters keys: years, authors, journals, article_types, languages,
    free_full_text (bool), species, ages. Unknown keys are a hard error.
    """
    base = (base or "").strip()
    if not base:
        raise EutilsError("bad_query", "base query is empty")
    filters = dict(filters or {})
    known = {
        "years",
        "authors",
        "journals",
        "article_types",
        "languages",
        "free_full_text",
        "species",
        "ages",
    }
    unknown = set(filters) - known
    if unknown:
        raise EutilsError(
            "bad_filter", "unknown filter key(s)", keys=sorted(unknown), allowed=sorted(known)
        )

    clauses: list[str] = []
    for clause in (
        _authors_clause(filters.get("authors")),
        _or_group(filters.get("journals") or [], "ta"),
        _or_group(filters.get("article_types") or [], "pt"),
        _or_group(filters.get("languages") or [], "la"),
        _mapped_group(filters.get("species") or [], SPECIES_TAGS, "species"),
        _mapped_group(filters.get("ages") or [], AGE_TAGS, "age"),
        _years_clause(filters.get("years")),
    ):
        if clause:
            clauses.append(clause)
    if filters.get("free_full_text"):
        clauses.append('"free full text"[sb]')

    if not clauses:
        return base
    head = base if (base.startswith("(") and base.endswith(")")) else f"({base})"
    return " AND ".join([head] + clauses)


# --------------------------------------------------------------------------- #
# esearch
# --------------------------------------------------------------------------- #


def _search_record(
    query_id: str,
    query_string: str,
    translated: str | None,
    count: int | None,
    ids: list[str],
    retstart: int,
    retmax: int,
) -> dict[str, Any]:
    """`search result record` per references/schema.md §3."""
    return {
        "schema_version": SCHEMA_VERSION,
        "query_id": query_id,
        "query_string": query_string,
        "translated_query": translated,
        "source": "pubmed",
        "count": count,
        "retrieved_ids": ids,
        "retrieved_pmids": list(ids),
        "retstart": retstart,
        "retmax": retmax,
        "executed_at": _now(),
        # true only when this record actually carries what C-SEARCH-LOG requires:
        # a numeric hit count and NCBI's translated query.
        "hit_count_logged": isinstance(count, int) and bool(translated),
    }


def esearch(
    query: str,
    *,
    filters: dict[str, Any] | None = None,
    query_id: str = "q1",
    retstart: int = 0,
    retmax: int = 200,
    db: str = "pubmed",
    sort: str | None = None,
    **http: Any,
) -> dict[str, Any]:
    """Single esearch page -> one `search result record`."""
    return esearch_pages(
        query,
        filters=filters,
        query_id=query_id,
        retstart=retstart,
        retmax=retmax,
        max_records=retmax,
        db=db,
        sort=sort,
        **http,
    )[0]


def esearch_pages(
    query: str,
    *,
    filters: dict[str, Any] | None = None,
    query_id: str = "q1",
    retstart: int = 0,
    retmax: int = 200,
    max_records: int = DEFAULT_MAX_RECORDS,
    db: str = "pubmed",
    sort: str | None = None,
    **http: Any,
) -> list[dict[str, Any]]:
    """Paginated esearch. Returns one `search result record` per page.

    Pages past the esearch retstart ceiling (9999) are served from the search
    history via `efetch rettype=uilist`, which is how NCBI documents retrieving
    more than 10k UIDs. `max_records` caps the total collected.
    """
    term = build_query(query, filters)
    page = max(1, min(int(retmax), MAX_PAGE))
    budget = max(0, int(max_records))

    body = request(
        "esearch",
        {
            "db": db,
            "term": term,
            "retmode": "json",
            "retstart": retstart,
            "retmax": min(page, budget) if budget else page,
            "sort": sort,
            "usehistory": "y",
        },
        **http,
    )
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise EutilsError("bad_response", f"esearch returned non-JSON: {exc}", body_head=body[:400])
    if "esearchresult" not in payload:
        raise EutilsError("bad_response", "esearch response missing esearchresult", body_head=body[:400])
    result = payload["esearchresult"]
    if result.get("ERROR"):
        raise EutilsError("ncbi_error", str(result["ERROR"]), term=term)

    count = int(result["count"]) if str(result.get("count", "")).isdigit() else None
    translated = result.get("querytranslation") or None
    webenv = result.get("webenv")
    query_key = result.get("querykey") or result.get("query_key")
    ids = [str(i) for i in result.get("idlist", [])]

    pages = [_search_record(query_id, term, translated, count, ids, retstart, page)]
    collected = len(ids)
    offset = retstart + len(ids)
    total = count if count is not None else collected

    while collected < budget and offset < total and ids:
        want = min(page, budget - collected, MAX_PAGE)
        if offset <= ESEARCH_ID_CEILING and offset + want <= ESEARCH_ID_CEILING + 1:
            body = request(
                "esearch",
                {
                    "db": db,
                    "term": term,
                    "retmode": "json",
                    "retstart": offset,
                    "retmax": want,
                    "sort": sort,
                    "usehistory": "y",
                },
                **http,
            )
            ids = [str(i) for i in json.loads(body).get("esearchresult", {}).get("idlist", [])]
        elif webenv and query_key:
            text = request(
                "efetch",
                {
                    "db": db,
                    "WebEnv": webenv,
                    "query_key": query_key,
                    "rettype": "uilist",
                    "retmode": "text",
                    "retstart": offset,
                    "retmax": want,
                },
                **http,
            )
            ids = [line.strip() for line in text.splitlines() if line.strip().isdigit()]
        else:
            raise EutilsError(
                "pagination_unavailable",
                "past the esearch retstart ceiling and no search history was returned",
                offset=offset,
            )
        if not ids:
            break
        pages.append(_search_record(query_id, term, translated, count, ids, offset, want))
        collected += len(ids)
        offset += len(ids)
    return pages


# --------------------------------------------------------------------------- #
# efetch: PubMed XML -> normalized bibliographic JSON
# --------------------------------------------------------------------------- #

_MONTHS = {
    m: f"{i:02d}"
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}

_RETRACTION_PUB_TYPES = {
    "retracted publication": "retracted",
    "retraction of publication": "retracted",
    "expression of concern": "expression_of_concern",
    "corrected and republished article": "corrected",
}

_RETRACTION_REF_TYPES = {
    "retractionin": "retracted",
    "retractionof": "retracted",
    "expressionofconcernin": "expression_of_concern",
    "expressionofconcernfor": "expression_of_concern",
    "erratumin": "corrected",
    "correctionin": "corrected",
    "republishedin": "corrected",
}

_RETRACTION_RANK = {"none": 0, "corrected": 1, "expression_of_concern": 2, "retracted": 3}


def _text(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    value = "".join(node.itertext()).strip()
    return value or None


def _find_text(parent: ET.Element, path: str) -> str | None:
    return _text(parent.find(path))


def _date_from(node: ET.Element | None) -> str | None:
    """PubMed date element -> YYYY-MM-DD, degrading to YYYY-MM / YYYY (rule S2)."""
    if node is None:
        return None
    medline = _find_text(node, "MedlineDate")
    year = _find_text(node, "Year")
    if not year and medline:
        head = medline.split()[0][:4] if medline.split() else ""
        return head if head.isdigit() else None
    if not year:
        return None
    month = (_find_text(node, "Month") or "").strip().lower()
    if month:
        month = _MONTHS.get(month[:3], month.zfill(2) if month.isdigit() else "")
    if not month or not month.isdigit() or not 1 <= int(month) <= 12:
        return year
    day = (_find_text(node, "Day") or "").strip()
    if day.isdigit():
        return f"{year}-{month}-{int(day):02d}"
    return f"{year}-{month}"


def _abstract(article: ET.Element) -> str | None:
    """Structured abstract labels preserved as `LABEL: text` blocks."""
    parts: list[str] = []
    for node in article.findall("./Abstract/AbstractText"):
        body = _text(node)
        if not body:
            continue
        label = node.get("Label") or node.get("NlmCategory")
        parts.append(f"{label.strip()}: {body}" if label else body)
    return "\n\n".join(parts) if parts else None


def _authors(article: ET.Element) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in article.findall("./AuthorList/Author"):
        affiliations = [
            _text(a) for a in node.findall("./AffiliationInfo/Affiliation") if _text(a)
        ]
        out.append(
            {
                "family": _find_text(node, "LastName"),
                "given": _find_text(node, "ForeName"),
                "initials": _find_text(node, "Initials"),
                "affiliation": affiliations[0] if affiliations else None,
                "collective": _find_text(node, "CollectiveName"),
            }
        )
    return out


def _retraction_status(article: ET.Element, citation: ET.Element) -> str:
    status = "none"
    for node in article.findall("./PublicationTypeList/PublicationType"):
        mapped = _RETRACTION_PUB_TYPES.get((_text(node) or "").lower())
        if mapped and _RETRACTION_RANK[mapped] > _RETRACTION_RANK[status]:
            status = mapped
    for node in citation.findall(".//CommentsCorrections"):
        mapped = _RETRACTION_REF_TYPES.get((node.get("RefType") or "").lower())
        if mapped and _RETRACTION_RANK[mapped] > _RETRACTION_RANK[status]:
            status = mapped
    return status


def parse_pubmed_article(entry: ET.Element) -> dict[str, Any]:
    """One <PubmedArticle> -> normalized record (schema.md §4 / `references/okf-bundle.md` fields)."""
    citation = entry.find("./MedlineCitation")
    if citation is None:
        raise EutilsError("bad_response", "PubmedArticle without MedlineCitation")
    article = citation.find("./Article")
    if article is None:
        raise EutilsError("bad_response", "MedlineCitation without Article")

    ids: dict[str, str] = {}
    for node in entry.findall("./PubmedData/ArticleIdList/ArticleId"):
        value = _text(node)
        if value:
            ids[(node.get("IdType") or "").lower()] = value
    doi = ids.get("doi")
    if not doi:
        for node in article.findall("./ELocationID"):
            if (node.get("EIdType") or "").lower() == "doi":
                doi = _text(node)
                break

    journal = article.find("./Journal")
    pagination = article.find("./Pagination")
    pages = None
    if pagination is not None:
        pages = _find_text(pagination, "MedlinePgn")
        if not pages:
            start, end = _find_text(pagination, "StartPage"), _find_text(pagination, "EndPage")
            pages = f"{start}-{end}" if start and end else start

    epub = None
    for node in article.findall("./ArticleDate"):
        if (node.get("DateType") or "Electronic") == "Electronic":
            epub = _date_from(node)
            break

    return {
        "schema_version": SCHEMA_VERSION,
        "pmid": _find_text(citation, "PMID"),
        "doi": doi.lower() if doi else None,
        "pmcid": ids.get("pmc"),
        "title": _find_text(article, "ArticleTitle"),
        "journal": {
            "title": _find_text(journal, "Title") if journal is not None else None,
            "iso_abbrev": _find_text(journal, "ISOAbbreviation") if journal is not None else None,
            "issn": _find_text(journal, "ISSN") if journal is not None else None,
        },
        "publication_date": _date_from(article.find("./Journal/JournalIssue/PubDate")),
        "epub_date": epub,
        "volume": _find_text(article, "./Journal/JournalIssue/Volume"),
        "issue": _find_text(article, "./Journal/JournalIssue/Issue"),
        "pages": pages,
        "abstract": _abstract(article),
        "authors": _authors(article),
        "article_types": [
            t for t in (_text(n) for n in article.findall("./PublicationTypeList/PublicationType")) if t
        ],
        "mesh_terms": [
            t
            for t in (
                _text(n) for n in citation.findall("./MeshHeadingList/MeshHeading/DescriptorName")
            )
            if t
        ],
        "keywords": [t for t in (_text(n) for n in citation.findall("./KeywordList/Keyword")) if t],
        "grants": [
            {"id": _find_text(g, "GrantID"), "agency": _find_text(g, "Agency")}
            for g in article.findall("./GrantList/Grant")
        ],
        "publication_status": _find_text(entry, "./PubmedData/PublicationStatus"),
        "retraction_status": _retraction_status(article, citation),
    }


def parse_efetch_xml(xml_text: str) -> list[dict[str, Any]]:
    """PubMed efetch XML -> list of normalized records."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EutilsError("bad_response", f"efetch XML parse error: {exc}", body_head=xml_text[:400])
    return [parse_pubmed_article(entry) for entry in root.iter("PubmedArticle")]


def _chunks(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def efetch(
    pmids: Iterable[str],
    *,
    db: str = "pubmed",
    chunk_size: int = EFETCH_CHUNK,
    **http: Any,
) -> dict[str, Any]:
    """PMIDs -> normalized bibliographic JSON, batched over POSTed chunks."""
    wanted = [str(p).strip() for p in pmids if str(p).strip()]
    seen: set[str] = set()
    ordered = [p for p in wanted if not (p in seen or seen.add(p))]
    size = max(1, min(int(chunk_size), EFETCH_CHUNK))

    records: list[dict[str, Any]] = []
    for chunk in _chunks(ordered, size):
        xml_text = request(
            "efetch",
            {"db": db, "id": ",".join(chunk), "retmode": "xml", "rettype": "abstract"},
            method="POST",
            **http,
        )
        records.extend(parse_efetch_xml(xml_text))

    returned = {r["pmid"] for r in records if r.get("pmid")}
    return {
        "schema_version": SCHEMA_VERSION,
        "requested": len(ordered),
        "returned": len(records),
        "missing_pmids": [p for p in ordered if p not in returned],
        "records": records,
        "fetched_at": _now(),
    }


# --------------------------------------------------------------------------- #
# elink
# --------------------------------------------------------------------------- #


def elink(
    pmids: Iterable[str],
    *,
    linkname: str = "pubmed_pubmed_citedin",
    db: str = "pubmed",
    dbfrom: str = "pubmed",
    **http: Any,
) -> dict[str, Any]:
    """Citation chaining. linkname: citedin (forward), refs (backward), related."""
    source = [str(p).strip() for p in pmids if str(p).strip()]
    if not source:
        raise EutilsError("bad_request", "elink needs at least one PMID")
    body = request(
        "elink",
        {
            "dbfrom": dbfrom,
            "db": db,
            "linkname": linkname,
            "id": ",".join(source),
            "retmode": "json",
        },
        method="POST",
        **http,
    )
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise EutilsError("bad_response", f"elink returned non-JSON: {exc}", body_head=body[:400])

    linked: list[str] = []
    seen: set[str] = set()
    for linkset in payload.get("linksets", []):
        for db_block in linkset.get("linksetdbs", []) or []:
            if db_block.get("linkname") != linkname:
                continue
            for uid in db_block.get("links", []) or []:
                uid = str(uid)
                if uid not in seen:
                    seen.add(uid)
                    linked.append(uid)
    return {
        "schema_version": SCHEMA_VERSION,
        "linkname": linkname,
        "source_pmids": source,
        "linked_ids": linked,
        "count": len(linked),
        "executed_at": _now(),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _emit(payload: Any, out: str | None) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(out)
    else:
        print(text)


def _page_path(out: str, index: int) -> str:
    if index == 0:
        return out
    stem, ext = os.path.splitext(out)
    return f"{stem}-p{index}{ext or '.json'}"


def _load_filters(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    if raw.startswith("@"):
        with open(raw[1:], "r", encoding="utf-8") as fh:
            raw = fh.read()
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise EutilsError("bad_filter", f"--filters-json is not valid JSON: {exc}")
    if not isinstance(value, dict):
        raise EutilsError("bad_filter", "--filters-json must be a JSON object")
    return value


def _read_pmids(args: argparse.Namespace) -> list[str]:
    raw: list[str] = []
    for chunk in args.pmids or []:
        raw.extend(chunk.replace(",", " ").split())
    if args.pmids_file:
        source = sys.stdin.read() if args.pmids_file == "-" else open(
            args.pmids_file, "r", encoding="utf-8"
        ).read()
        raw.extend(source.replace(",", " ").split())
    if not raw:
        raise EutilsError("bad_request", "no PMIDs given (--pmids or --pmids-file)")
    return raw


def _common(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--email",
        default=None,
        help="contact email sent as email= per NCBI policy (default $DEEP_RESEARCH_EMAIL)",
    )
    parser.add_argument("--tool", default=TOOL_NAME, help="tool= identifier (default deep-research)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout (s)")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_RETRIES, help="retry attempts")
    parser.add_argument("--out", default=None, help="write JSON here instead of stdout")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eutils.py",
        description="NCBI E-utilities client for deep-research (esearch/efetch/elink).",
        epilog=(
            "env: NCBI_API_KEY (10 req/s, else 3), DEEP_RESEARCH_EMAIL, "
            "DEEP_RESEARCH_FIXTURES (offline replay), DEEP_RESEARCH_RECORD (record)"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    s = _common(sub.add_parser("esearch", help="query -> count, translated query, PMIDs"))
    s.add_argument("--query", required=True, help="base query (MeSH/free-text boolean)")
    s.add_argument("--filters-json", default=None, help="filter object as JSON, or @file")
    s.add_argument("--query-id", default="q1", help="stable query id for the search record")
    s.add_argument("--retstart", type=int, default=0, help="zero-based offset of the first page")
    s.add_argument("--retmax", type=int, default=200, help="page size (max 10000)")
    s.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="cap on total PMIDs collected across pages (default: one page)",
    )
    s.add_argument("--all", action="store_true", help="page through the whole result set")
    s.add_argument("--sort", default=None, help="esearch sort, e.g. pub_date, relevance")
    s.add_argument("--db", default="pubmed")

    f = _common(sub.add_parser("efetch", help="PMIDs -> normalized bibliographic JSON"))
    f.add_argument("--pmids", action="append", default=[], help="comma/space separated PMIDs")
    f.add_argument("--pmids-file", default=None, help="file of PMIDs ('-' for stdin)")
    f.add_argument("--chunk-size", type=int, default=EFETCH_CHUNK, help="PMIDs per POST (<=200)")
    f.add_argument("--db", default="pubmed")

    e = _common(sub.add_parser("elink", help="citation chaining"))
    e.add_argument("--pmid", action="append", default=[], required=True, help="source PMID(s)")
    e.add_argument(
        "--linkname",
        default="pubmed_pubmed_citedin",
        help=f"one of {', '.join(LINKNAMES)} (others passed through)",
    )
    e.add_argument("--db", default="pubmed")
    e.add_argument("--dbfrom", default="pubmed")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    http = {"email": args.email, "tool": args.tool, "timeout": args.timeout,
            "max_retries": args.max_retries}
    try:
        if args.command == "esearch":
            max_records = args.max_records
            if args.all:
                max_records = 10 ** 9
            elif max_records is None:
                max_records = args.retmax
            pages = esearch_pages(
                args.query,
                filters=_load_filters(args.filters_json),
                query_id=args.query_id,
                retstart=args.retstart,
                retmax=args.retmax,
                max_records=max_records,
                db=args.db,
                sort=args.sort,
                **http,
            )
            if args.out:
                for i, page in enumerate(pages):
                    _emit(page, _page_path(args.out, i))
            else:
                _emit(pages[0] if len(pages) == 1 else pages, None)
        elif args.command == "efetch":
            _emit(
                efetch(_read_pmids(args), db=args.db, chunk_size=args.chunk_size, **http),
                args.out,
            )
        elif args.command == "elink":
            pmids: list[str] = []
            for chunk in args.pmid:
                pmids.extend(chunk.replace(",", " ").split())
            _emit(elink(pmids, linkname=args.linkname, db=args.db, dbfrom=args.dbfrom, **http),
                  args.out)
    except EutilsError as exc:
        print(json.dumps(exc.to_dict(), indent=2, ensure_ascii=False), file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            json.dumps(
                EutilsError("io_error", str(exc)).to_dict(), indent=2, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
