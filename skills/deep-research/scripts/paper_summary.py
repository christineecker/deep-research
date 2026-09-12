"""paper_summary.py — shared schema/render/verify helpers for the single-paper and
selected-paper summary profiles (`references/single-paper-summary.md`,
`SINGLE_PAPER_SUMMARY_IMPLEMENTATION_PLAN.md`).

Imported as a sibling module by `paper.py` (assembly/rendering) and `verify.py`
(structural checks), the same cross-import pattern `registry.py` uses for `corpus`/`store`/
`render`. Kept deliberately small: this module never touches the network, never writes files
outside what its callers pass a path for, and never fabricates a claim — it only validates and
renders records a subagent (or `--reuse`) already produced.

Schema: `references/schema/14-single-paper-summary.md` (single record),
`references/schema/15-paper-summary-set.md` (set manifest).
"""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA_VERSION = 1

SECTION_NAMES = (
    "bottom_line", "why_summarized", "study_design_and_basis",
    "population_setting_sample", "intervention_exposure_index_test_model",
    "comparator_or_reference_standard", "outcomes_and_results",
    "methods_quality_and_rob", "limitations", "practical_takeaways",
    "what_not_to_conclude", "provenance",
)
PURPOSES = ("clinical", "methods", "journal-club", "peer-review", "background")
AUDIENCES = ("researcher", "clinician", "student", "grant-writer", "general")
SOURCE_BASIS_VALUES = ("fulltext", "abstract_only")
APPRAISAL_SKIP_REASONS = ("no_appraise_flag", "abstract_only", "no_supported_tool")

SPAN_REF_RE = re.compile(
    r"^(?P<record>extraction|appraisal):(?P<field>spans|outcomes|domains):(?P<i>\d+)"
    r"(?::spans:(?P<j>\d+))?$"
)

#: Wording reserved for the full review pipeline (`references/synthesis.md`,
#: `references/appraisal.md` §8 GRADE vocabulary). A single-paper or set-overview claim using
#: any of these is smuggling in cross-study synthesis this profile explicitly excludes
#: (plan "Prompting" / "Verification").
FORBIDDEN_PHRASES = (
    "pooled", "meta-analys", "meta analys", "across studies", "the literature",
    "the evidence base", "consensus", "high certainty", "moderate certainty",
    "low certainty", "very low certainty", "forest plot", "i2", "i²",
)


def evidence_slug(evidence_id: str) -> str:
    """`pmid:12345678` -> `pmid-12345678` (`registry.py extraction_slug`, duplicated here to
    avoid a `paper_summary -> registry` import cycle; both must stay literally identical)."""
    return (evidence_id or "").replace(":", "-", 1).replace("/", "-")


def validate_summary_shape(summary: dict) -> list[str]:
    """Structural-only checks against schema §14. Returns a list of error strings, `[]` if
    the record is well-formed. Never checks that a `span_refs` pointer resolves — that needs
    the extraction/appraisal file too (`resolve_span_ref` below, called by the caller)."""
    errors = []
    if not isinstance(summary, dict):
        return ["summary is not a JSON object"]
    if summary.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1")
    for field in ("summary_id", "evidence_id", "purpose", "audience", "source_basis",
                  "extraction_path", "sections", "limitations", "do_not_conclude",
                  "created_at"):
        if field not in summary:
            errors.append(f"missing required field: {field}")
    if summary.get("purpose") not in (*PURPOSES, None) and "purpose" in summary:
        if summary.get("purpose") not in PURPOSES:
            errors.append(f"purpose must be one of {PURPOSES}")
    if "audience" in summary and summary.get("audience") not in AUDIENCES:
        errors.append(f"audience must be one of {AUDIENCES}")
    if "source_basis" in summary and summary.get("source_basis") not in SOURCE_BASIS_VALUES:
        errors.append(f"source_basis must be one of {SOURCE_BASIS_VALUES}")
    appraisal_path = summary.get("appraisal_path")
    skip_reason = summary.get("appraisal_skipped_reason")
    if appraisal_path is None and skip_reason not in APPRAISAL_SKIP_REASONS:
        errors.append(
            "appraisal_path is null but appraisal_skipped_reason is not one of "
            f"{APPRAISAL_SKIP_REASONS}"
        )
    if appraisal_path is not None and skip_reason is not None:
        errors.append("appraisal_path is set but appraisal_skipped_reason is not null")
    sections = summary.get("sections")
    if not isinstance(sections, list):
        errors.append("sections must be a list")
    else:
        names = [s.get("name") for s in sections if isinstance(s, dict)]
        missing = [n for n in SECTION_NAMES if n not in names]
        if missing:
            errors.append(f"sections missing: {missing}")
        for sec in sections:
            if not isinstance(sec, dict) or "claims" not in sec:
                errors.append(f"section {sec!r} missing claims[]")
                continue
            for claim in sec.get("claims") or []:
                if not isinstance(claim, dict):
                    errors.append(f"{sec.get('name')}: claim is not an object")
                    continue
                for f in ("text", "evidence_id", "span_refs"):
                    if f not in claim:
                        errors.append(f"{sec.get('name')}: claim missing '{f}'")
                if claim.get("evidence_id") not in (None, summary.get("evidence_id")):
                    errors.append(
                        f"{sec.get('name')}: claim evidence_id "
                        f"{claim.get('evidence_id')!r} != summary evidence_id "
                        f"{summary.get('evidence_id')!r}"
                    )
    return errors


def resolve_span_ref(ref: str, extraction: dict | None, appraisal: dict | None) -> str | None:
    """Resolve one `span_refs[]` pointer against the record it names. Returns `None` when it
    resolves, else a short reason string. Grammar: schema §14 `span_refs`."""
    m = SPAN_REF_RE.match(ref or "")
    if not m:
        return f"malformed span_ref: {ref!r}"
    record_name, field, i, j = m.group("record"), m.group("field"), int(m.group("i")), m.group("j")
    record = extraction if record_name == "extraction" else appraisal
    if record is None:
        return f"{ref}: {record_name} record not available"
    if field == "spans":
        spans = record.get("spans")
        if not isinstance(spans, list) or i >= len(spans):
            return f"{ref}: index {i} out of range for {record_name}.spans"
        return None
    if field == "outcomes":
        outcomes = record.get("outcomes")
        if not isinstance(outcomes, list) or i >= len(outcomes):
            return f"{ref}: index {i} out of range for {record_name}.outcomes"
        if j is None:
            return None
        spans = (outcomes[i] or {}).get("spans")
        if not isinstance(spans, list) or int(j) >= len(spans):
            return f"{ref}: index {j} out of range for {record_name}.outcomes[{i}].spans"
        return None
    if field == "domains":
        domains = record.get("domains")
        if not isinstance(domains, list) or i >= len(domains):
            return f"{ref}: index {i} out of range for {record_name}.domains"
        if j is None:
            return None
        spans = (domains[i] or {}).get("spans")
        if not isinstance(spans, list) or int(j) >= len(spans):
            return f"{ref}: index {j} out of range for {record_name}.domains[{i}].spans"
        return None
    return f"{ref}: unrecognized field {field!r}"


def find_forbidden_phrase(text: str) -> str | None:
    lowered = (text or "").lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            return phrase
    return None


_COMMENT_RE = re.compile(r"<!--.*?-->\n?", re.DOTALL)


def render_template(template_text: str, values: dict) -> str:
    """`{{KEY}}` substitution. Any placeholder left in `values` as `None`/missing renders as
    an explicit marker rather than silently vanishing (schema §0 S3: absence is explicit).
    Author-facing `<!-- ... -->` guidance comments are stripped — they instruct whoever fills
    the template, not the reader of the finished summary (contrast `templates/report.md`,
    which an agent authors fresh from, never runs through literal substitution)."""
    out = template_text
    for key, val in values.items():
        out = out.replace("{{%s}}" % key, "" if val is None else str(val))
    out = re.sub(r"\{\{[A-Z_]+\}\}", "_not applicable_", out)
    out = _COMMENT_RE.sub("", out)
    return out


def render_section_body(summary: dict, name: str) -> str:
    sections = {s.get("name"): s for s in summary.get("sections") or [] if isinstance(s, dict)}
    sec = sections.get(name)
    if not sec or not sec.get("claims"):
        return "_Not applicable to this paper._"
    return "\n\n".join(c.get("text", "") for c in sec["claims"])


def render_bullets(items: list[str]) -> str:
    if not items:
        return "_None recorded._"
    return "\n".join(f"- {item}" for item in items)


def render_personal_notes(annotation: dict | None) -> str:
    """Render `annotations.py`'s per-paper tags/rating/note, or an explicit "none recorded"
    -- never omit the section outright, since absence is explicit (schema §0 S3). Kept as a
    distinct renderer (not `render_section_body`, which reads extraction-derived claims) so a
    personal opinion is never mistaken for a verified, span-traceable claim -- the template
    section this feeds is deliberately labeled "unverified" (plan Phase 5: "distinguish
    personal annotations from verified research claims")."""
    annotation = annotation or {}
    tags = annotation.get("tags") or []
    rating = annotation.get("rating")
    note = annotation.get("note")
    if not tags and rating is None and not note:
        return "_None recorded._"
    lines = []
    if tags:
        lines.append(f"- Tags: {', '.join(tags)}")
    if rating is not None:
        lines.append(f"- Rating: {rating}/5")
    if note:
        lines.append(f"- Note: {note}")
    return "\n".join(lines)


def citation_line(registry_rec: dict) -> str:
    authors = registry_rec.get("authors") or []
    lead = authors[0] if authors else "Unknown author"
    et_al = " et al." if len(authors) > 1 else ""
    year = (registry_rec.get("publication_date") or "")[:4] or "n.d."
    title = registry_rec.get("title") or "Untitled"
    journal = registry_rec.get("journal") or ""
    return f"{lead}{et_al} ({year}). {title}. {journal}.".strip()
