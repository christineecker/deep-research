"""Identity primitives for the reference manager.

Pure functions only: no DB access. `new_id` mints the immutable primary
keys used for papers/identifiers/attachments/etc. The `normalize_*`
functions canonicalize external identifiers before they are written to
the `identifiers(scheme, value)` table so its UNIQUE(scheme, value)
constraint actually catches duplicates (see
skills/deep-research/scripts/refmgr/migrations/0001_init.sql and
REFERENCE_MANAGER_V2_PLAN.md, "Data model and invariants").
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlsplit, urlunsplit


class IdentifierError(ValueError):
    """Raised when a raw identifier value cannot be normalized."""


_DOI_PREFIX_RE = re.compile(r"^\s*(https?://(?:dx\.)?doi\.org/|doi\.org/|doi:\s*)", re.IGNORECASE)
_DOI_SHAPE_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_PMID_PREFIX_RE = re.compile(r"^\s*pmid:\s*", re.IGNORECASE)
_PMCID_RE = re.compile(r"^\s*(pmc)?(\d+)\s*$", re.IGNORECASE)


def new_id() -> str:
    """Return a UUID4 hex string (no dashes) for use as an immutable primary key."""
    return uuid.uuid4().hex


def normalize_doi(raw: str) -> str:
    value = _DOI_PREFIX_RE.sub("", raw.strip(), count=1).strip().lower()
    if not _DOI_SHAPE_RE.match(value):
        raise IdentifierError(f"not a valid DOI: {raw!r}")
    return value


def normalize_pmid(raw: str) -> str:
    value = _PMID_PREFIX_RE.sub("", raw.strip(), count=1).strip()
    if not value or not value.isdigit():
        raise IdentifierError(f"not a valid PMID: {raw!r}")
    return value


def normalize_pmcid(raw: str) -> str:
    match = _PMCID_RE.match(raw.strip())
    if not match:
        raise IdentifierError(f"not a valid PMCID: {raw!r}")
    return f"PMC{match.group(2)}"


def normalize_url(raw: str) -> str:
    value = raw.strip()
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        raise IdentifierError(f"not a valid URL (missing scheme or host): {raw!r}")
    path = parts.path
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        path,
        parts.query,
        parts.fragment,
    ))


_NORMALIZERS = {
    "doi": normalize_doi,
    "pmid": normalize_pmid,
    "pmcid": normalize_pmcid,
    "url": normalize_url,
}


def normalize_identifier(scheme: str, raw: str) -> str:
    normalizer = _NORMALIZERS.get(scheme.strip().lower())
    if normalizer is None:
        return raw.strip()
    return normalizer(raw)
