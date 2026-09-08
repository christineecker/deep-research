#!/usr/bin/env python3
"""html_report.py — the self-contained HTML deliverable for a deep-research run.

Stage 8 output path of `PLAN.md` §5 / §8 Phase 5 ("HTML artifact — evidence table,
effect-direction chart, per-study cards, gaps shown honestly").

Reads a run directory and emits ONE offline HTML file:

  corpus.jsonl                      `references/schema.md` §4 corpus record
  workspace/extractions/*.json      §7 extraction record
  workspace/appraisals/*.json       §8 appraisal record
  outputs/verification.json         §9 verifier result
  missing.md                        quarantined / unobtainable evidence
  config.json                       run metadata (title, profile, scope, filters)
  outputs/report.md                 optional; only its "New insights" section is lifted
  outputs/hypotheses.json           optional; structured hypotheses, preferred over report.md
  corpus.py prisma --format json    PRISMA counters (shelled out; never recomputed here)

What this script will NOT do, by contract (`references/synthesis.md` §2, §3):

  * no pooled estimate, no random-effects model, no forest-plot diamond, no I^2.
    The chart is an effect-direction tabulation: direction + weight-proxy + precision
    class + risk of bias, and nothing more. Numbers are printed verbatim as extracted;
    no geometry is used to imply a magnitude comparison across effect measures.
  * no claim that is not present in an extraction, appraisal, corpus or verifier record.
  * no silent completion: quarantined, abstract-only, preprint, retracted and
    not-yet-attempted records are surfaced, and a PROVISIONAL banner fires on any
    trigger of `references/synthesis.md` §8.

Environment: python3 3.14, stdlib only. No third-party imports, no pip, no network —
neither at build time nor when the produced page is opened. Every string that comes
from paper metadata is passed through `html.escape` before it reaches the document,
including inside attributes and inside the embedded JSON blob.

Usage
  html_report.py build --run-dir <dir> [--out outputs/report.html]
                       [--template templates/report.html] [--title TEXT]
                       [--no-external-links] [--stdout] [--quiet]

Exit codes
  0 ok (warnings may still be printed to stderr)
  2 user error (bad arguments, missing run directory)
  3 state error (unreadable corpus, unusable template)
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from string import Template

SCHEMA_VERSION = 1
GENERATOR = "deep-research/html_report.py 0.1"
NOT_STATED = "not stated"
NOT_REPORTED = "not reported"

HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = HERE.parent / "templates" / "report.html"
CORPUS_SCRIPT = HERE / "corpus.py"

# --------------------------------------------------------------------- enums

DIRECTION_ORDER = ("favors_comparator", "null_effect", "favors_intervention", "unclear")
DIRECTION_LABEL = {
    "favors_intervention": "favours intervention",
    "favors_comparator": "favours comparator",
    "null_effect": "null effect",
    "unclear": "unclear",
}
DIRECTION_VAR = {
    "favors_intervention": "--dir-int, #1f4d78",
    "favors_comparator": "--dir-comp, #9a5416",
    "null_effect": "--dir-null, #63635c",
    "unclear": "--dir-unc, #6a4a78",
}

# risk-of-bias vocabulary (schema.md §8 domains[].judgement + overall_judgement)
ROB_RANK = {
    "low": 1, "yes": 1,
    "some_concerns": 2, "moderate": 2, "partial_yes": 2,
    "serious": 3,
    "high": 4, "no": 4,
    "critical": 5,
    "unclear": 6,
}
ROB_CLASS = {
    "low": "rob-low", "yes": "rob-low",
    "some_concerns": "rob-mod", "moderate": "rob-mod", "partial_yes": "rob-mod",
    "serious": "rob-high", "high": "rob-high", "critical": "rob-high", "no": "rob-high",
    "unclear": "rob-unc",
}
ROB_VAR = {
    "rob-low": "--rob-low, #1c5b3c",
    "rob-mod": "--rob-mod, #8a6a08",
    "rob-high": "--rob-high, #8a1c17",
    "rob-unc": "--rob-unc, #63635c",
}
TIER_LABEL = {
    0: "0 — local library",
    1: "1 — PMC MCP full text",
    2: "2 — PMC PDF",
    3: "3 — Europe PMC fullTextXML",
    4: "4 — Unpaywall location",
    5: "5 — OA PDF/HTML fetch",
    6: "6 — preprint twin",
    7: "7 — quarantined",
}
RATIO_MEASURES = {
    "rr", "or", "hr", "irr", "rrr", "pr", "sir", "smr", "risk ratio", "odds ratio",
    "hazard ratio", "rate ratio", "incidence rate ratio", "prevalence ratio",
}

# ----------------------------------------------------- evidence kernel (schema §12/§13)

# schema.md §12 span rule P2. Shown, never enforced here; the assembler owns enforcement.
SPAN_MAX_CHARS = 2000

# schema.md §13 `reason_code` enum, in the same order, with the one-line meaning this page
# prints beside each code so a reader never has to look the code up elsewhere.
REASON_CODE_MEANING = {
    "UNKNOWN_SOURCE":
        "a span named a source_id with no snapshot under the run's sources/ directory.",
    "SNAPSHOT_HASH_MISMATCH":
        "the snapshot exists but its recomputed content_hash or source_id differs from the "
        "stored value — tampering, which is never downgraded to a warning.",
    "SPAN_OUT_OF_RANGE":
        "start < 0, end > len(text), or start >= end.",
    "SPAN_TOO_LONG":
        f"the span exceeds the {SPAN_MAX_CHARS}-character cap; it is failed, never silently "
        "truncated.",
    "EXCERPT_MISMATCH":
        "an agent-written excerpt differs from the re-slice of snapshot text[start:end]. The "
        "snapshot wins and the artifact is rejected, not corrected.",
    "NO_SPANS":
        "the record carries no span at all — the legacy, pre-kernel case (schema R16). It "
        "is unverified: never accepted, never deleted, and not evidence of tampering.",
    "URL_NOT_RETRIEVED":
        "a citation URL in the artifact has no snapshot retrieved for that artifact in this run.",
    "NO_FRESH_FETCH":
        "a source backing the artifact has no fresh fetch or local_pdf event for this run "
        "(schema §11 / R15).",
    "UNSUPPORTED_VERDICT":
        "outputs/verification.json records an unsupported claim for this artifact.",
    "SOURCE_OUTSIDE_ACCEPTED":
        "a synthesis or report claim introduced a source_id outside the accepted evidence for "
        "its branch.",
    "NO_PAPER_ID":
        "the snapshot's paper object supplies no PMID, DOI or PMCID, or contradicts the "
        "claim's evidence_id.",
    "ASSET_HASH_MISMATCH":
        "a user-supplied PDF is missing or no longer hashes to the recorded asset sha256.",
}

# Wording used in three places, so it is written once.
KERNEL_ABSENT_NOTE = (
    "This run has no <code>outputs/result.json</code>, so <code>scripts/assemble.py</code> "
    "never ran over it. Per schema resolution <strong>R20</strong> and decision "
    "<strong>D7</strong> the evidence-kernel gate is <strong>off by default</strong>: an "
    "absent assembler result is a normal state of this pipeline and a run that predates the "
    "kernel, not an error and not a failure. What it does mean is that nothing on this page "
    "has been re-checked against an immutable snapshot — the evidence here is "
    "<em>unverified</em>, which is neither <em>verified</em> nor <em>tampered</em>."
)

# `precise` requires the interval to exclude the null AND its width to be no more than
# this multiple of the point estimate's magnitude (on the log scale for ratio measures).
PRECISE_WIDTH_RATIO = 2.0

PRECISION_NOTE = (
    "Precision classes follow references/synthesis.md §2 and are derived from the reported "
    "interval alone. This pipeline holds no machine-readable minimal important difference "
    "(MID), so the MID-anchored definition of `precise` cannot be applied; the fallback rule "
    "used here is explicit: `null-compatible` when the interval contains the null "
    "(1 for ratio measures, 0 otherwise); `precise` when it excludes the null and its width "
    "is at most twice the magnitude of the point estimate (log scale for ratio measures); "
    "`imprecise` otherwise; `unreported` when no interval is given \u2014 which is a category "
    "of its own and is never read as evidence of no effect."
)

WARNINGS: list[str] = []


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"html_report.py: warning: {msg}", file=sys.stderr)


# --------------------------------------------------------------------- escaping

def E(value) -> str:
    """Escape anything for HTML text or attribute context. Never returns markup."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return html.escape(fmt_num(value), quote=True)
    return html.escape(str(value), quote=True)


def A(value) -> str:
    """Escape for an attribute value (same rules; kept separate for readability)."""
    return E(value)


def T(value, default: str = NOT_STATED) -> str:
    """Escaped text with an explicit placeholder for unknown values (schema rule S3)."""
    if value is None:
        return f'<span class="muted">{E(default)}</span>'
    s = str(value).strip()
    if not s:
        return f'<span class="muted">{E(default)}</span>'
    return E(s)


def plain(value, default: str = NOT_STATED) -> str:
    """Unescaped plain text with a placeholder — only for building search haystacks."""
    if value is None:
        return default
    s = str(value).strip()
    return s or default


def fmt_num(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f or f in (float("inf"), float("-inf")):
        return str(v)
    s = f"{f:.6g}"
    return s


def trunc(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------- io

def read_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                warn(f"{path}:{lineno}: malformed JSON line skipped ({exc})")
                continue
            if isinstance(obj, dict):
                out.append(obj)
            else:
                warn(f"{path}:{lineno}: not a JSON object, skipped")
    return out


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        warn(f"{path}: unreadable ({exc})")
        return None


def load_dir_records(d: Path, kind: str) -> dict[str, dict]:
    """Index workspace result files by evidence_id, falling back to pmid."""
    out: dict[str, dict] = {}
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        if f.name.startswith("."):
            continue
        try:
            rec = read_json(f)
        except (json.JSONDecodeError, OSError) as exc:
            warn(f"{f}: malformed {kind} record skipped ({exc})")
            continue
        if not isinstance(rec, dict):
            warn(f"{f}: {kind} record is not an object, skipped")
            continue
        rec["_path"] = str(f)
        eid = rec.get("evidence_id")
        if eid:
            out[str(eid)] = rec
        pmid = rec.get("pmid")
        if pmid:
            out.setdefault(f"pmid:{pmid}", rec)
    return out


def run_prisma(run_dir: Path, corpus_script: Path) -> tuple[dict | None, str | None]:
    """PRISMA counters come from corpus.py, never from a second implementation here."""
    if corpus_script.is_file():
        try:
            proc = subprocess.run(
                [sys.executable, str(corpus_script), "prisma",
                 "--run-dir", str(run_dir), "--format", "json"],
                capture_output=True, text=True, timeout=120, check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return json.loads(proc.stdout), None
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            note = (f"corpus.py prisma exited {proc.returncode}: "
                    + (detail[-1] if detail else "no output"))
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            note = f"corpus.py prisma could not be run ({exc})"
    else:
        note = f"corpus.py not found at {corpus_script}"
    cached = run_dir / "outputs" / "prisma.json"
    if cached.is_file():
        try:
            return read_json(cached), note + "; used the cached outputs/prisma.json instead"
        except (json.JSONDecodeError, OSError) as exc:
            note += f"; cached outputs/prisma.json unreadable ({exc})"
    return None, note


# --------------------------------------------------------------------- model

class Study:
    """A corpus record joined to its extraction and appraisal records."""

    def __init__(self, rec: dict, ext: dict | None, app: dict | None):
        self.rec = rec
        self.ext = ext or {}
        self.app = app or {}
        self.eid = str(rec.get("evidence_id") or "")
        ft = rec.get("fulltext") or {}
        self.ft = ft if isinstance(ft, dict) else {}
        scr = rec.get("screening") or {}
        self.screening = scr if isinstance(scr, dict) else {}

    # -- identity ---------------------------------------------------------
    @property
    def year(self) -> str | None:
        d = self.rec.get("publication_date")
        if not d:
            return None
        m = re.match(r"(\d{4})", str(d))
        return m.group(1) if m else None

    @property
    def first_author(self) -> str | None:
        authors = self.rec.get("authors") or []
        if not authors:
            return None
        first = str(authors[0]).strip()
        return first.split(" ")[0] if first else None

    @property
    def label(self) -> str:
        fa, yr = self.first_author, self.year
        if fa and yr:
            return f"{fa} {yr}"
        if fa:
            return f"{fa} (year {NOT_STATED})"
        title = self.rec.get("title")
        if title:
            return trunc(title, 48)
        return self.eid or "unidentified record"

    # -- status -----------------------------------------------------------
    @property
    def decision(self) -> str | None:
        return self.screening.get("decision")

    @property
    def status(self) -> str:
        return str(self.ft.get("status") or "missing")

    @property
    def tier(self):
        return self.ft.get("source_tier")

    @property
    def retracted(self) -> str:
        return str(self.rec.get("retraction_status") or "none")

    @property
    def is_preprint(self) -> bool:
        return bool(self.rec.get("is_preprint"))

    @property
    def basis(self) -> str:
        """evidence_basis, preferring the extraction record, else the corpus status."""
        b = self.ext.get("evidence_basis")
        if b in ("fulltext", "abstract_only"):
            return b
        return "abstract_only" if self.status == "abstract_only" else self.status

    @property
    def retrieval_attempted(self) -> bool:
        """Resolution R8: `missing` before any attempt is not `unobtainable`."""
        return bool(self.ft.get("access_route"))

    @property
    def quarantined(self) -> bool:
        return self.status == "missing" and self.retrieval_attempted

    @property
    def not_yet_attempted(self) -> bool:
        return self.status == "missing" and not self.retrieval_attempted

    @property
    def overall_judgement(self) -> str | None:
        return self.app.get("overall_judgement")

    @property
    def tool(self) -> str | None:
        return self.app.get("tool")

    @property
    def flags(self) -> list[str]:
        f: list[str] = []
        if self.retracted == "retracted":
            f.append("retracted")
        elif self.retracted == "expression_of_concern":
            f.append("expression_of_concern")
        elif self.retracted == "corrected":
            f.append("corrected")
        if self.is_preprint:
            f.append("preprint")
        if self.basis == "abstract_only":
            f.append("abstract_only")
        if self.quarantined:
            f.append("quarantined")
        return f

    @property
    def primary_flag(self) -> str:
        for f in ("retracted", "expression_of_concern", "quarantined", "preprint",
                  "abstract_only", "corrected"):
            if f in self.flags:
                return f
        return "none"

    def badges_html(self) -> str:
        out = []
        if self.retracted == "retracted":
            out.append('<span class="tag retracted">retracted</span>')
        elif self.retracted == "expression_of_concern":
            out.append('<span class="tag eoc">expression of concern</span>')
        elif self.retracted == "corrected":
            out.append('<span class="tag corrected">corrected</span>')
        if self.is_preprint:
            out.append('<span class="tag preprint">preprint &middot; not peer reviewed</span>')
        if self.basis == "abstract_only":
            out.append('<span class="tag abstract">abstract only</span>')
        if self.quarantined:
            out.append('<span class="tag quarantined">quarantined</span>')
        return " ".join(out)


# --------------------------------------------------------------------- kernel

def is_span_backed(q: dict) -> bool:
    """True when a `quotes[]` entry carries the §7 DERIVED triple (source_id, start, end).

    Entries without it are pre-kernel, agent-transcribed records: legal (R16), never
    accepted by the assembler, and never presented here as verified.
    """
    if not isinstance(q, dict):
        return False
    sid, start, end = q.get("source_id"), q.get("start"), q.get("end")
    if not sid or not isinstance(sid, str):
        return False
    if isinstance(start, bool) or isinstance(end, bool):
        return False
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    return 0 <= start < end


def split_quotes(ext: dict) -> tuple[list[dict], list[dict]]:
    """Partition a record's `quotes[]` into (span-backed, span-less legacy) entries."""
    derived: list[dict] = []
    legacy: list[dict] = []
    for q in (ext.get("quotes") or []) if isinstance(ext, dict) else []:
        if not isinstance(q, dict):
            continue
        (derived if is_span_backed(q) else legacy).append(q)
    return derived, legacy


def quotes_from_accepted(entry: dict) -> list[dict]:
    """The derived `quotes[]` of one `accepted[]` artifact (schema §13).

    `scripts/assemble.py` re-slices the excerpts into the accepted artifact, not back into
    `workspace/extractions/*.json`, so `result.json` is the primary source here. Where an
    older assembler wrote only `claims[]`, the same entries are reconstructed from it —
    both carry `excerpt`/`text` plus `source_id`/`start`/`end`, all snapshot-derived.
    """
    out: list[dict] = []
    for q in entry.get("quotes") or []:
        if is_span_backed(q):
            out.append(q)
    if out:
        return out
    for c in entry.get("claims") or []:
        if not isinstance(c, dict) or not is_span_backed(c):
            continue
        out.append({"text": c.get("excerpt"), "section": c.get("section"),
                    "page": c.get("page"), "source_id": c.get("source_id"),
                    "start": c.get("start"), "end": c.get("end"),
                    "claim": c.get("claim"), "field": c.get("field")})
    return out


def derived_quotes(st: "Study", kernel: "Kernel") -> tuple[list[dict], str]:
    """(span-backed excerpts, where they came from: `result` | `record` | `none`)."""
    if kernel.present:
        acc: list[dict] = []
        for entry in kernel.accepted_for(st.eid, "extraction"):
            acc.extend(quotes_from_accepted(entry))
        if acc:
            return acc, "result"
    rec_derived, _ = split_quotes(st.ext)
    if rec_derived:
        return rec_derived, "record"
    return [], "none"


class Kernel:
    """Read-only index over `<run>/outputs/result.json` — the assembler result, schema §13.

    `present` is False for a run that predates the assembler. That is a first-class state,
    not an error: R20 / decision D7 keep the evidence-kernel gate off by default.
    """

    def __init__(self, result=None, has_sources: bool = False):
        self.result = result if isinstance(result, dict) else None
        self.has_sources = bool(has_sources)
        self.accepted_by_eid: dict[str, list[dict]] = {}
        self.unresolved: list[dict] = []
        self.unresolved_by_eid: dict[str, list[dict]] = {}
        self.sources: dict[str, dict] = {}
        if self.result is None:
            return
        for a in self.result.get("accepted") or []:
            if isinstance(a, dict):
                self.accepted_by_eid.setdefault(str(a.get("evidence_id") or ""), []).append(a)
        diag = self.result.get("diagnostics")
        diag = diag if isinstance(diag, dict) else {}
        for u in diag.get("unresolved") or []:
            if isinstance(u, dict):
                self.unresolved.append(u)
                self.unresolved_by_eid.setdefault(str(u.get("evidence_id") or ""), []).append(u)
        for s in self.result.get("sources") or []:
            if isinstance(s, dict) and s.get("source_id"):
                self.sources[str(s["source_id"])] = s

    @property
    def present(self) -> bool:
        return self.result is not None

    @property
    def gate(self) -> dict:
        g = (self.result or {}).get("gate")
        return g if isinstance(g, dict) else {}

    @property
    def counts(self) -> dict:
        c = (self.result or {}).get("counts")
        return c if isinstance(c, dict) else {}

    @property
    def counts_by_reason(self) -> dict:
        diag = (self.result or {}).get("diagnostics")
        diag = diag if isinstance(diag, dict) else {}
        c = diag.get("counts_by_reason")
        return c if isinstance(c, dict) else {}

    def accepted_for(self, eid: str, kind: str | None = None) -> list[dict]:
        items = self.accepted_by_eid.get(str(eid), [])
        return [a for a in items if kind is None or a.get("kind") == kind]

    def unresolved_for(self, eid: str, kind: str | None = None) -> list[dict]:
        items = self.unresolved_by_eid.get(str(eid), [])
        return [u for u in items if kind is None or u.get("kind") == kind]

    def source_label(self, source_id: str) -> str:
        """Escaped one-line description of a snapshot, or an honest 'not listed'."""
        s = self.sources.get(str(source_id))
        if not s:
            return ('<span class="muted">not listed in the assembler result</span>'
                    if self.present else
                    '<span class="muted">no assembler result to describe it</span>')
        bits = [E(s.get("access") or NOT_STATED), E(s.get("origin") or NOT_STATED)]
        if s.get("fresh") is True:
            bits.append('<span class="pill pass">fresh this run</span>')
        elif s.get("fresh") is False:
            bits.append('<span class="pill warn">not freshly retrieved this run</span>')
        return " &middot; ".join(bits)


# --------------------------------------------------------------------- precision

def null_value_for(measure: str | None) -> float:
    if not measure:
        return 0.0
    m = str(measure).strip().lower()
    return 1.0 if m in RATIO_MEASURES else 0.0


def precision_class(outcome: dict) -> str:
    """`precise` | `imprecise` | `null-compatible` | `unreported` (synthesis.md §2)."""
    lo, hi = outcome.get("ci_low"), outcome.get("ci_high")
    if lo is None or hi is None:
        return "unreported"
    try:
        lo, hi = float(lo), float(hi)
    except (TypeError, ValueError):
        return "unreported"
    if lo > hi:
        lo, hi = hi, lo
    null = null_value_for(outcome.get("effect_measure"))
    if lo <= null <= hi:
        return "null-compatible"
    eff = outcome.get("effect")
    try:
        eff = float(eff)
    except (TypeError, ValueError):
        return "imprecise"
    if null == 1.0:
        if lo <= 0 or hi <= 0 or eff <= 0:
            return "imprecise"
        width = math.log(hi) - math.log(lo)
        scale = abs(math.log(eff))
    else:
        width = hi - lo
        scale = abs(eff)
    if scale <= 0:
        return "imprecise"
    return "precise" if (width / scale) <= PRECISE_WIDTH_RATIO else "imprecise"


def effect_text(o: dict) -> str:
    measure = o.get("effect_measure")
    eff, lo, hi = o.get("effect"), o.get("ci_low"), o.get("ci_high")
    if eff is None:
        head = NOT_REPORTED
    else:
        head = f"{measure} {fmt_num(eff)}" if measure else fmt_num(eff)
    if lo is not None and hi is not None:
        return f"{head} [{fmt_num(lo)}, {fmt_num(hi)}]"
    if eff is None:
        return NOT_REPORTED
    return f"{head} (interval {NOT_REPORTED})"


def p_text(o: dict) -> str:
    p = o.get("p_value")
    return f"p = {fmt_num(p)}" if p is not None else NOT_REPORTED


# --------------------------------------------------------------------- rows

def build_rows(studies: list[Study]) -> list[dict]:
    """One row per study x outcome. Studies with no extractable outcome keep one row."""
    rows: list[dict] = []
    for st in studies:
        outcomes = st.ext.get("outcomes")
        if not isinstance(outcomes, list):
            outcomes = []
        if not outcomes:
            rows.append(make_row(st, None))
            continue
        for o in outcomes:
            rows.append(make_row(st, o if isinstance(o, dict) else None))
    for i, r in enumerate(rows):
        r["i"] = i
    return rows


def make_row(st: Study, o: dict | None) -> dict:
    ext = st.ext
    n_total = ext.get("n_total")
    n_arms = ext.get("n_arms") or []
    if n_total is None:
        n_disp = NOT_STATED
    elif n_arms:
        n_disp = f"{n_total} ({', '.join(str(a) for a in n_arms)})"
    else:
        n_disp = str(n_total)
    inter = plain(ext.get("intervention"), NOT_STATED)
    comp = plain(ext.get("comparator"), NOT_STATED)
    direction = (o or {}).get("direction") or "unclear"
    if direction not in DIRECTION_LABEL:
        warn(f"{st.eid}: unrecognized direction {direction!r}; shown as unclear")
        direction = "unclear"
    outcome_name = plain((o or {}).get("name"), "no extractable outcome reported")
    timepoint = plain((o or {}).get("timepoint"), NOT_STATED)
    rob = st.overall_judgement
    rob_key = str(rob).strip().lower().replace(" ", "_") if rob else ""
    return {
        "study": st,
        "outcome": o,
        "label": st.label,
        "n_total": n_total,
        "n_disp": n_disp,
        "design": plain(ext.get("design"), NOT_STATED),
        "population": plain(ext.get("population"), NOT_STATED),
        "ic": f"{inter} vs {comp}",
        "outcome_name": outcome_name,
        "timepoint": timepoint,
        "effect": effect_text(o) if o else NOT_REPORTED,
        "effect_num": (o or {}).get("effect"),
        "p": p_text(o) if o else NOT_REPORTED,
        "direction": direction,
        "precision": precision_class(o) if o else "unreported",
        "rob": plain(rob, "not appraised"),
        "rob_key": rob_key,
        "rob_rank": ROB_RANK.get(rob_key, 7),
        "rob_class": ROB_CLASS.get(rob_key, "rob-unc"),
        "tool": plain(st.tool, "none recorded"),
        "basis": st.basis,
        "tier": st.tier,
        "flag": st.primary_flag,
    }


# --------------------------------------------------------------------- sections

def h_banner(prov: dict) -> str:
    if not prov["provisional"]:
        return (
            '<section class="banner ok" role="status">'
            "<h2>Status: final</h2>"
            "<p>No provisional trigger fired: nothing is quarantined, no included record "
            "rests on an abstract alone, and no verifier check failed. The synthesis "
            "remains limited to what the retrieved corpus reports.</p>"
            "</section>"
        )
    items = "".join(f"<li>{t}</li>" for t in prov["triggers"])
    return (
        '<section class="banner" role="alert">'
        "<h2>PROVISIONAL synthesis</h2>"
        "<p>This report is <strong>provisional</strong>. One or more triggers in "
        "<code>references/synthesis.md</code> §8 fired. It is delivered as-is, never "
        "withheld and never silently completed.</p>"
        f"<ul>{items}</ul>"
        "<p class=\"small\">What would lift it: obtain the records listed under "
        "<a href=\"#gaps\">Gaps, quarantine and honesty panel</a> — drop PDFs into the run's "
        "<code>inbox/</code> and rerun — and clear every failing check under "
        "<a href=\"#verification\">Verification</a>.</p>"
        "</section>"
    )


def kernel_state_html(kernel: Kernel) -> str:
    """One line naming which of the three evidence states this whole run is in."""
    if not kernel.present:
        return ('<span class="pill unknown">no assembler result</span> '
                "this run predates the evidence-kernel gate; the gate is off by default "
                "(R20 / decision D7), so this is a normal state, not an error. Excerpts on "
                "this page are <strong>unverified</strong>, never <em>tampered</em>."
                + ("" if kernel.has_sources else
                   " The run has no <code>sources/</code> snapshot store either."))
    verdict = str(kernel.gate.get("verdict") or "unknown")
    n_unres = len(kernel.unresolved)
    return (f'<span class="pill {A(verdict)}">gate {E(verdict)}</span> '
            f"{E(num(kernel.counts.get('accepted')))} artifact(s) accepted, "
            f"{n_unres} unresolved &mdash; see "
            '<a href="#gaps">the honesty panel</a>. Enforcing: '
            + ("yes" if kernel.gate.get("enabled") else "no (off by default, R20/D7)") + ".")


def h_run_section(cfg: dict, run_dir: Path, prisma_note: str | None,
                  counts: dict, generated_at: str, kernel: Kernel) -> str:
    def row(k, v):
        # k is always a literal owned by this module, never paper metadata
        return f'<tr><th scope="row">{k}</th><td>{v}</td></tr>'

    filters = cfg.get("filters")
    if isinstance(filters, dict) and filters:
        fil = ", ".join(f"{E(k)}: {E(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)}"
                        for k, v in sorted(filters.items()))
    else:
        fil = T(None, "none recorded")
    rows = [
        row("Research question", T(cfg.get("question") or cfg.get("title"), "not recorded in config.json")),
        row("Run slug", f"<code>{E(cfg.get('slug') or run_dir.name)}</code>"),
        row("Profile / scope / rigor",
            f"{T(cfg.get('profile'), 'not recorded')} / {T(cfg.get('scope'), 'not recorded')} "
            f"/ {T(cfg.get('rigor'), 'not recorded')}"),
        row("Date of last search", T(cfg.get("last_search_date") or cfg.get("search_date"),
                                     "not recorded in config.json")),
        row("Filters applied", fil),
        row("Corpus records", f"{counts['corpus']}"),
        row("Included studies (screened in, text obtained)", f"{counts['included']}"),
        row("&mdash; on full text", f"{counts['fulltext']}"),
        row("&mdash; on abstract only", f"{counts['abstract_only']}"),
        row("Quarantined (attempted, unobtainable)", f"{counts['quarantined']}"),
        row("Retrieval not yet attempted", f"{counts['not_attempted']}"),
        row("Preprints among included", f"{counts['preprints']}"),
        row("Retracted / expression of concern", f"{counts['retracted']}"),
        row("Evidence kernel", kernel_state_html(kernel)),
        row("Generated", f"<code>{E(generated_at)}</code> by <code>{E(GENERATOR)}</code>"),
    ]
    note = ""
    if prisma_note:
        note = (f'<p class="small muted">PRISMA counters note: {E(prisma_note)}</p>')
    return (
        '<section id="run"><h2>1. Run and protocol</h2>'
        '<div class="tablewrap"><table class="meta"><tbody>'
        + "".join(rows) +
        "</tbody></table></div>"
        '<p class="small muted">Values are read from <code>config.json</code> and '
        "<code>corpus.jsonl</code>. A field absent from the run is shown as "
        "<em>not recorded</em>; nothing here is inferred.</p>"
        + note +
        "</section>"
    )


# ---------------------------------------------------------------- PRISMA

def prisma_pick(prisma: dict) -> dict:
    """Flatten corpus.py's nested prisma object into the numbers this page shows."""
    ident = prisma.get("identification") or {}
    scr = prisma.get("screening") or {}
    ret = prisma.get("retrieval") or {}
    inc = prisma.get("included") or {}
    return {
        "identified": ident.get("records_identified"),
        "by_source": ident.get("records_identified_by_source") or {},
        "duplicates": ident.get("duplicates_removed"),
        "after_dedupe": ident.get("records_after_dedupe"),
        "queries": ident.get("queries_logged"),
        "hit_counts": ident.get("hit_counts") or {},
        "screened": scr.get("records_screened"),
        "not_screened": scr.get("not_yet_screened"),
        "excluded": scr.get("excluded"),
        "excluded_by_reason": scr.get("excluded_by_reason") or {},
        "unclear": scr.get("unclear"),
        "included_after_screening": scr.get("included_after_screening"),
        "sought": ret.get("fulltext_sought"),
        "obtained": ret.get("fulltext_obtained"),
        "unobtainable": ret.get("fulltext_unobtainable"),
        "unobtainable_ids": ret.get("unobtainable_ids") or [],
        "not_attempted": ret.get("retrieval_not_yet_attempted"),
        "abstract_only": ret.get("abstract_only"),
        "studies_included": inc.get("studies_included"),
        "with_extraction": inc.get("with_extraction"),
        "with_appraisal": inc.get("with_appraisal"),
        "preprints": inc.get("preprints"),
        "retracted": inc.get("retracted_or_eoc"),
        "dual": prisma.get("dual_screening"),
    }


def num(v) -> str:
    return NOT_STATED if v is None else str(v)


def svg_box(x, y, w, h, lines, emphasis=False) -> str:
    stroke = "var(--accent, #1f4d78)" if emphasis else "var(--rule, #d9d6cd)"
    fill = "var(--accent-bg, #eaf0f6)" if emphasis else "var(--surface, #ffffff)"
    sw = 2 if emphasis else 1
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" '
           f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>']
    ty = y + 17
    for i, (text, bold) in enumerate(lines):
        weight = "600" if bold else "400"
        size = 12.5 if bold else 11.5
        out.append(f'<text x="{x + 10}" y="{ty}" font-size="{size}" font-weight="{weight}" '
                   f'fill="var(--ink, #1b1b19)">{E(trunc(text, 52))}</text>')
        ty += 15 if i == 0 else 14
    return "".join(out)


def svg_arrow(x1, y1, x2, y2) -> str:
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="var(--muted, #6a6a63)" stroke-width="1.5" '
            f'marker-end="url(#dr-arrow)"/>')


def h_prisma_section(prisma: dict | None, note: str | None, counts: dict) -> str:
    head = '<section id="prisma"><h2>2. Search and selection (PRISMA 2020 flow)</h2>'
    if prisma is None:
        return (head +
                '<div class="banner"><h3>PRISMA counters unavailable</h3>'
                f"<p>{E(note or 'corpus.py prisma could not be run.')} "
                "The flow is therefore not shown. It is not reconstructed from a second "
                "implementation, because a counter that disagrees with "
                "<code>corpus.py</code> would be worse than an absent one.</p></div></section>")

    p = prisma_pick(prisma)

    # ---- inline SVG flow
    W, BW, SW_ = 1000, 360, 330
    left_x, right_x = 30, 470
    main = [
        ([("Records identified", True),
          (f"n = {num(p['identified'])} from {len(p['by_source'])} source(s)", False)], True),
        ([("Records screened (title/abstract)", True),
          (f"n = {num(p['screened'])}; unclear {num(p['unclear'])}", False)], False),
        ([("Reports sought for retrieval", True),
          (f"n = {num(p['sought'])}", False)], False),
        ([("Reports assessed (text obtained)", True),
          (f"n = {num(p['obtained'])}; abstract-only {num(p['abstract_only'])}", False)], False),
        ([("Studies included in synthesis", True),
          (f"n = {num(p['studies_included'])}; extracted {num(p['with_extraction'])}, "
           f"appraised {num(p['with_appraisal'])}", False)], True),
    ]
    side = [
        [("Duplicate records removed", True), (f"n = {num(p['duplicates'])}", False)],
        [("Records excluded at screening", True),
         (f"n = {num(p['excluded'])} across {len(p['excluded_by_reason'])} criteria", False)],
        [("Reports not retrieved (quarantined)", True),
         (f"n = {num(p['unobtainable'])}; not yet attempted {num(p['not_attempted'])}", False)],
        None,
    ]
    box_h, gap = 52, 34
    parts: list[str] = []
    y = 10
    for idx, (lines, emph) in enumerate(main):
        parts.append(svg_box(left_x, y, BW, box_h, lines, emph))
        if idx < len(side) and side[idx]:
            parts.append(svg_box(right_x, y, SW_, box_h, side[idx]))
            parts.append(svg_arrow(left_x + BW + 4, y + box_h / 2, right_x - 6, y + box_h / 2))
        if idx < len(main) - 1:
            parts.append(svg_arrow(left_x + BW / 2, y + box_h + 2,
                                   left_x + BW / 2, y + box_h + gap - 4))
        y += box_h + gap
    height = y - gap + 20
    desc = (f"PRISMA flow: {num(p['identified'])} records identified, "
            f"{num(p['duplicates'])} duplicates removed, {num(p['screened'])} screened, "
            f"{num(p['excluded'])} excluded, {num(p['sought'])} sought for retrieval, "
            f"{num(p['unobtainable'])} not retrieved, {num(p['obtained'])} assessed, "
            f"{num(p['studies_included'])} included.")
    svg = (
        f'<svg viewBox="0 0 {W} {height}" role="img" '
        'aria-labelledby="prisma-t prisma-d" '
        'font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif">'
        '<title id="prisma-t">PRISMA 2020 flow of records through this run</title>'
        f'<desc id="prisma-d">{E(desc)}</desc>'
        '<defs><marker id="dr-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted, #6a6a63)"/></marker></defs>'
        + "".join(parts) + "</svg>"
    )

    # ---- counter table (the accessible, printable, authoritative rendering)
    def r(label, value, indent=False):
        pad = ' style="padding-left:1.6rem"' if indent else ""
        return f'<tr><th scope="row"{pad}>{label}</th><td class="num">{E(num(value))}</td></tr>'

    trs = [
        r("Records identified (all sources)", p["identified"]),
        r("Duplicate records removed", p["duplicates"]),
        r("Records after de-duplication", p["after_dedupe"]),
        r("Records screened (title/abstract)", p["screened"]),
        r("Records not yet screened", p["not_screened"], True),
        r("Records excluded at screening", p["excluded"]),
        r("Records unclear at screening", p["unclear"], True),
        r("Reports sought for retrieval", p["sought"]),
        r("Reports not retrieved &mdash; quarantined (rung 7)", p["unobtainable"]),
        r("Retrieval not yet attempted (schema R8: not counted as unobtainable)",
          p["not_attempted"], True),
        r("Reports assessed (full text or abstract)", p["obtained"]),
        r("&mdash; of which abstract-only", p["abstract_only"], True),
        r("Studies included in synthesis", p["studies_included"]),
        r("&mdash; with an extraction record", p["with_extraction"], True),
        r("&mdash; with an appraisal record", p["with_appraisal"], True),
        r("&mdash; preprints (not peer reviewed)", p["preprints"], True),
        r("&mdash; retracted / expression of concern", p["retracted"], True),
    ]

    # exclusion reasons
    if p["excluded_by_reason"]:
        excl = "".join(
            f'<tr><th scope="row"><code>{E(k)}</code></th><td class="num">{E(v)}</td></tr>'
            for k, v in sorted(p["excluded_by_reason"].items()))
        excl_block = ('<h3>Exclusions by protocol criterion</h3>'
                      '<div class="tablewrap"><table class="meta"><tbody>'
                      + excl + "</tbody></table></div>")
    else:
        excl_block = ('<h3>Exclusions by protocol criterion</h3>'
                      '<p class="muted">No screening exclusions are recorded with a '
                      "criterion id.</p>")

    # query hit counts
    if p["hit_counts"]:
        q = "".join(
            f'<tr><th scope="row"><code>{E(k)}</code></th>'
            f'<td class="num">{E(num(v))}</td></tr>'
            for k, v in sorted(p["hit_counts"].items()))
        q_block = ('<h3>Queries executed, with hit counts</h3>'
                   '<div class="tablewrap"><table class="meta"><tbody>' + q +
                   "</tbody></table></div>"
                   '<p class="small muted">Verbatim query strings and NCBI translations live in '
                   "<code>workspace/search/*.json</code>; they are the reproducible record.</p>")
    else:
        q_block = ('<h3>Queries executed, with hit counts</h3>'
                   '<p class="muted">No search result records were found in '
                   "<code>workspace/search/</code>.</p>")

    # dual screening
    d = p["dual"]
    if not d:
        dual_block = ('<h3>Dual screening agreement</h3>'
                      '<p class="muted">Single-screen profile: no dual-screening agreement '
                      "statistics were produced.</p>")
    else:
        kappa = d.get("cohens_kappa")
        kappa_txt = "undefined" if kappa is None else fmt_num(kappa)
        dual_block = (
            '<h3>Dual screening agreement</h3>'
            '<div class="tablewrap"><table class="meta"><tbody>'
            + r("Records dual-screened", d.get("dual_screened"))
            + r("Disagreements (adjudications required)", d.get("disagreements"))
            + r("Raw agreement", d.get("raw_agreement"))
            + f'<tr><th scope="row">Cohen&rsquo;s kappa</th>'
              f'<td class="num">{E(kappa_txt)}</td></tr>'
            + r("Adjudication records written", d.get("adjudications_recorded"))
            + r("Awaiting adjudication", len(d.get("awaiting_adjudication") or []))
            + "</tbody></table></div>"
            '<p class="small muted">Raw agreement is reported as raw agreement. It is never '
            "presented as a kappa; the kappa above is only shown because "
            "<code>corpus.py</code> computed it from the full contingency table.</p>")

    note_html = f'<p class="small muted">{E(note)}</p>' if note else ""
    return (
        head + note_html +
        f'<figure><div class="svgwrap">{svg}</div>'
        '<figcaption>PRISMA 2020 flow. The figure is a rendering of the counter table '
        "below, which is the authoritative version; both come from "
        "<code>corpus.py prisma</code>.</figcaption></figure>"
        '<div class="tablewrap"><table class="meta"><caption>PRISMA counters, from '
        "<code>corpus.py prisma --format json</code>.</caption><tbody>"
        + "".join(trs) + "</tbody></table></div>"
        + excl_block + q_block + dual_block +
        "</section>"
    )


# ---------------------------------------------------------------- evidence table

def h_evidence_section(rows: list[dict], counts: dict) -> str:
    if not rows:
        return ('<section id="evidence"><h2>3. Evidence table</h2>'
                '<p class="muted">No included study reached extraction, so there is no '
                "evidence table. This is an absence in the run, not a finding about the "
                "literature.</p></section>")

    def facet(name, label, values, labeller=lambda v: v):
        opts = "".join(f'<option value="{A(v)}">{E(labeller(v))}</option>'
                       for v in values)
        return (f'<div class="field"><label for="f-{name}">{E(label)}</label>'
                f'<select id="f-{name}" data-facet="{A(name)}">'
                f'<option value="">All</option>{opts}</select></div>')

    outcomes = sorted({r["outcome_name"] for r in rows})
    designs = sorted({r["design"] for r in rows})
    robs = sorted({r["rob"] for r in rows})
    tiers = sorted({r["tier"] for r in rows if r["tier"] is not None})
    controls = (
        '<div class="controls noprint" role="group" aria-label="Evidence table filters">'
        '<div class="field"><label for="f-search">Search</label>'
        '<input type="search" id="f-search" placeholder="study, population, outcome, id"'
        ' autocomplete="off"></div>'
        + facet("outcome", "Outcome", outcomes, lambda v: trunc(v, 40))
        + facet("direction", "Direction", DIRECTION_ORDER, lambda v: DIRECTION_LABEL[v])
        + facet("basis", "Evidence basis", sorted({r["basis"] for r in rows}))
        + facet("rob", "Appraisal judgement", robs)
        + facet("tier", "Source tier", [str(t) for t in tiers],
                lambda v: TIER_LABEL.get(int(v), v))
        + facet("design", "Design", designs, lambda v: trunc(v, 40))
        + facet("flags", "Flag", ["none", "abstract_only", "preprint", "retracted",
                                  "expression_of_concern", "corrected"],
                lambda v: v.replace("_", " "))
        + '<button type="button" class="btn" id="f-reset">Reset filters</button>'
        '<p class="status" id="filter-status" role="status" aria-live="polite"></p>'
        "</div>"
    )

    cols = [
        ("study", "Study"), ("design", "Design"), ("n", "N (arms)"),
        ("population", "Population"), ("ic", "Intervention / comparator"),
        ("outcome", "Outcome (timepoint)"), ("effect", "Effect [95% CI]"),
        ("p", "p"), ("direction", "Direction"), ("precision", "Precision"),
        ("robrank", "Appraisal (tool)"), ("basis", "Evidence basis"), ("tier", "Tier"),
    ]
    head = "".join(
        f'<th scope="col" data-key="{A(k)}">'
        f'<button type="button" class="sortbtn" aria-sort="none">{E(lab)}</button></th>'
        for k, lab in cols)

    body: list[str] = []
    for r in rows:
        st: Study = r["study"]
        attrs = (
            f' data-i="{r["i"]}"'
            f' data-study="{A(r["label"])}"'
            f' data-design="{A(r["design"])}"'
            f' data-n="{A("" if r["n_total"] is None else r["n_total"])}"'
            f' data-population="{A(r["population"])}"'
            f' data-ic="{A(r["ic"])}"'
            f' data-outcome="{A(r["outcome_name"])}"'
            f' data-effect="{A("" if r["effect_num"] is None else r["effect_num"])}"'
            f' data-p="{A(r["p"])}"'
            f' data-direction="{A(r["direction"])}"'
            f' data-precision="{A(r["precision"])}"'
            f' data-rob="{A(r["rob"])}"'
            f' data-robrank="{r["rob_rank"]}"'
            f' data-basis="{A(r["basis"])}"'
            f' data-tier="{A("" if r["tier"] is None else r["tier"])}"'
            f' data-flags="{A(r["flag"])}"'
        )
        badges = st.badges_html()
        study_cell = (f'<a href="#card-{A(slug(st.eid))}">{E(r["label"])}</a>'
                      f'<br><code class="small">{E(st.eid)}</code>'
                      + (f"<br>{badges}" if badges else ""))
        basis_cell = ('<span class="tag abstract">abstract only</span>'
                      if r["basis"] == "abstract_only"
                      else ('<span class="tag fulltext">full text</span>'
                            if r["basis"] == "fulltext" else T(r["basis"])))
        tier_cell = (T(None, NOT_STATED) if r["tier"] is None
                     else E(TIER_LABEL.get(r["tier"], str(r["tier"]))))
        body.append(
            f"<tr{attrs}>"
            f"<td>{study_cell}</td>"
            f"<td>{T(r['design'])}</td>"
            f'<td class="num">{T(r["n_disp"])}</td>'
            f"<td>{T(trunc(r['population'], 140))}</td>"
            f"<td>{T(trunc(r['ic'], 160))}</td>"
            f"<td>{T(trunc(r['outcome_name'], 90))}<br>"
            f'<span class="muted small">{T(r["timepoint"])}</span></td>'
            f"<td>{T(r['effect'], NOT_REPORTED)}</td>"
            f"<td>{T(r['p'], NOT_REPORTED)}</td>"
            f'<td class="dir-{A(r["direction"])}">{E(DIRECTION_LABEL[r["direction"]])}</td>'
            f"<td>{E(r['precision'])}</td>"
            f'<td class="rob {A(r["rob_class"])}">{T(r["rob"], "not appraised")}<br>'
            f'<span class="muted small">{T(r["tool"], "no tool recorded")}</span></td>'
            f"<td>{basis_cell}</td>"
            f'<td class="num">{tier_cell}</td>'
            "</tr>")

    caption = (
        f"One row per study &times; outcome-timepoint pair; {len(rows)} rows over "
        f"{counts['included']} included studies. Null and negative rows are first-class and "
        "are never omitted. Unknown values read <em>not stated</em> and are never estimated. "
        "Sorting places missing values last in both directions, so an absent N is never "
        "treated as zero."
    )
    return (
        '<section id="evidence"><h2>3. Evidence table</h2>'
        "<p>Every value below is copied from an extraction or appraisal record produced this "
        "run. Nothing is derived except the precision class, which is defined in the caption "
        "of the next section.</p>"
        + controls +
        '<div class="tablewrap"><table class="data" id="evidence-table">'
        f"<caption>{caption}</caption>"
        f"<thead><tr>{head}</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
        "</section>"
    )


# ---------------------------------------------------------------- direction chart

def glyph(cx: float, cy: float, r: float, direction: str, fill_var: str,
          precision: str, title: str) -> str:
    dash = {"precise": "none", "imprecise": "none",
            "null-compatible": "4 2.5", "unreported": "1.5 2.5"}[precision]
    width = {"precise": 3.0, "imprecise": 1.4,
             "null-compatible": 1.6, "unreported": 2.0}[precision]
    opacity = 0.12 if precision == "unreported" else 0.85
    stroke = f"var({fill_var})"
    fill = f"var({fill_var})"
    common = (f'fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" '
              f'stroke-width="{width}" stroke-dasharray="{dash}"')
    if direction == "favors_intervention":
        pts = f"{cx - r},{cy - r} {cx - r},{cy + r} {cx + r * 1.15},{cy}"
        shape = f'<polygon points="{pts}" {common}/>'
    elif direction == "favors_comparator":
        pts = f"{cx + r},{cy - r} {cx + r},{cy + r} {cx - r * 1.15},{cy}"
        shape = f'<polygon points="{pts}" {common}/>'
    elif direction == "null_effect":
        shape = f'<circle cx="{cx}" cy="{cy}" r="{r}" {common}/>'
    else:
        pts = f"{cx},{cy - r * 1.15} {cx + r * 1.15},{cy} {cx},{cy + r * 1.15} {cx - r * 1.15},{cy}"
        shape = f'<polygon points="{pts}" {common}/>'
    return f"<g><title>{E(title)}</title>{shape}</g>"


def h_chart_section(rows: list[dict]) -> str:
    head = '<section id="directions"><h2>4. Effect-direction tabulation</h2>'
    data_rows = [r for r in rows if r["outcome"]]
    if not data_rows:
        return (head + '<p class="muted">No extracted outcome carries a direction, so no '
                "effect-direction chart is drawn.</p></section>")

    groups: dict[str, list[dict]] = {}
    for r in data_rows:
        groups.setdefault(r["outcome_name"], []).append(r)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    ns = [r["n_total"] for r in data_rows
          if isinstance(r["n_total"], (int, float)) and r["n_total"] > 0]
    nmax = max(ns) if ns else 0

    W = 1180
    LANE_X = {"favors_comparator": 372, "null_effect": 470, "favors_intervention": 568,
              "unclear": 672}
    LANE_EDGES = (323, 421, 519, 620, 724)
    ROW_H, PANEL_HEAD, PANEL_GAP, TOP = 30, 30, 18, 46

    parts: list[str] = []
    # axis header
    parts.append(f'<text x="8" y="20" font-size="11.5" font-weight="600" '
                 f'fill="var(--muted, #6a6a63)">Study (design, n)</text>')
    for key, x in LANE_X.items():
        parts.append(f'<text x="{x}" y="20" font-size="11" text-anchor="middle" '
                     f'font-weight="600" fill="var({DIRECTION_VAR[key]})">'
                     f'{E(DIRECTION_LABEL[key])}</text>')
    parts.append(f'<text x="712" y="20" font-size="11.5" font-weight="600" '
                 f'fill="var(--muted, #6a6a63)">As reported (measure, interval, '
                 f'timepoint, appraisal, basis)</text>')
    parts.append(f'<line x1="8" y1="27" x2="{W - 8}" y2="27" '
                 f'stroke="var(--rule, #d9d6cd)" stroke-width="1"/>')
    # lane separators
    lane_top = TOP - 8

    y = TOP
    summaries: list[str] = []
    for name, grp in ordered:
        parts.append(f'<text x="8" y="{y + 12}" font-size="12.5" font-weight="700" '
                     f'fill="var(--ink, #1b1b19)">{E(trunc(name, 92))}</text>')
        y += PANEL_HEAD
        gtop = y - 6
        for r in sorted(grp, key=lambda r: (DIRECTION_ORDER.index(r["direction"]),
                                            -(r["n_total"] or 0))):
            st: Study = r["study"]
            n = r["n_total"] if isinstance(r["n_total"], (int, float)) else None
            if n and nmax:
                rad = 5.0 + 8.0 * math.sqrt(n / nmax)
            else:
                rad = 6.5
            cy = y + ROW_H / 2
            left = f"{r['label']} — {trunc(r['design'], 26)}, " + (
                f"n={n}" if n else "n not stated")
            badge = ""
            if st.basis == "abstract_only":
                badge += " [abstract]"
            if st.is_preprint:
                badge += " [preprint]"
            if st.retracted == "retracted":
                badge += " [RETRACTED]"
            elif st.retracted == "expression_of_concern":
                badge += " [EoC]"
            parts.append(
                f'<text x="8" y="{cy + 4}" font-size="11" '
                f'fill="var(--ink-2, #3d3d39)">{E(trunc(left, 44))}'
                f'<tspan fill="var(--danger, #8a1c17)" font-weight="600">{E(badge)}</tspan>'
                "</text>")
            title = (f"{r['label']}: {DIRECTION_LABEL[r['direction']]}, "
                     f"{r['precision']}, {r['effect']}, "
                     f"appraisal {r['rob']} ({r['tool']}), basis {st.basis}")
            parts.append(glyph(LANE_X[r["direction"]], cy, rad, r["direction"],
                               ROB_VAR[r["rob_class"]], r["precision"], title))
            right = (f"{r['effect']} · {trunc(r['timepoint'], 18)} · "
                     f"{r['precision']} · {trunc(r['rob'], 16)} · "
                     f"{'abstract' if st.basis == 'abstract_only' else 'full text'}")
            parts.append(f'<text x="712" y="{cy + 4}" font-size="10.5" '
                         f'fill="var(--muted, #6a6a63)">{E(trunc(right, 62))}</text>')
            y += ROW_H
        parts.append(f'<rect x="4" y="{gtop}" width="{W - 8}" height="{y - gtop + 2}" '
                     f'fill="none" stroke="var(--rule-2, #ebe8e0)" stroke-width="1" rx="2"/>')
        y += PANEL_GAP
        summaries.append(group_summary(name, grp))

    for x in LANE_EDGES:
        parts.insert(0, f'<line x1="{x}" y1="{lane_top}" x2="{x}" y2="{y - PANEL_GAP}" '
                        f'stroke="var(--rule-2, #ebe8e0)" stroke-width="1"/>')

    height = y - PANEL_GAP + 12
    desc = ("Effect-direction tabulation. Each study-outcome row places one glyph in the "
            "lane of its reported direction. Glyph shape encodes direction, glyph area "
            "encodes analysed N as a weight proxy, glyph colour encodes the risk-of-bias "
            "judgement, and the outline style encodes the precision class. No pooled "
            "estimate, diamond or heterogeneity statistic is drawn. The same information, "
            "with exact numbers, is in the evidence table above.")
    svg = (f'<svg viewBox="0 0 {W} {height}" role="img" '
           'aria-labelledby="efd-t efd-d" '
           'font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif">'
           '<title id="efd-t">Effect-direction tabulation by outcome</title>'
           f'<desc id="efd-d">{E(desc)}</desc>' + "".join(parts) + "</svg>")

    legend = (
        '<ul class="legend">'
        '<li><strong>Shape = direction:</strong></li>'
        '<li>&#9654; favours intervention</li>'
        '<li>&#9664; favours comparator</li>'
        '<li>&#11044; null effect</li>'
        '<li>&#9670; unclear</li>'
        "</ul>"
        '<ul class="legend">'
        '<li><strong>Colour = risk of bias:</strong></li>'
        '<li><span class="swatch" style="background:var(--rob-low)"></span>low</li>'
        '<li><span class="swatch" style="background:var(--rob-mod)"></span>'
        "some concerns / moderate</li>"
        '<li><span class="swatch" style="background:var(--rob-high)"></span>'
        "serious / high / critical</li>"
        '<li><span class="swatch" style="background:var(--rob-unc)"></span>'
        "unclear or not appraised</li>"
        "</ul>"
        '<ul class="legend">'
        '<li><strong>Outline = precision:</strong></li>'
        "<li>thick solid = precise</li>"
        "<li>thin solid = imprecise</li>"
        "<li>dashed = null-compatible interval</li>"
        "<li>dotted, faded = no interval reported</li>"
        "</ul>"
        '<ul class="legend">'
        '<li><strong>Size = analysed N</strong> (area &prop; N; smallest fixed glyph = '
        "N not stated, not N=0)</li></ul>"
    )

    return (
        head +
        "<p>This is an effect-direction tabulation, the default synthesis method for this "
        "pipeline (<code>references/synthesis.md</code> §2). It is deliberately "
        "<strong>not</strong> a forest plot: there is no pooled estimate, no diamond, no "
        "I&sup2;, and no shared numeric axis &mdash; the studies do not share an effect "
        "measure, and a common axis would assert a comparability that does not exist. "
        "Horizontal position is a categorical lane, nothing more; the numbers are printed "
        "verbatim beside each row.</p>"
        f'<figure><div class="svgwrap">{svg}</div>{legend}'
        f"<figcaption>{E(PRECISION_NOTE)} Glyph area is a weight <em>proxy</em> only: it "
        "encodes analysed N, not study precision or inverse variance."
        "</figcaption></figure>"
        "<h3>Direction tallies, with the qualifiers that make them readable</h3>"
        "<p class=\"small\">A bare count of positive studies is not a synthesis "
        "(<code>references/synthesis.md</code> §2 rule 5), so each tally below is reported "
        "with the size and risk of bias of the studies behind it.</p>"
        '<ul class="plain">' + "".join(summaries) + "</ul>"
        "</section>"
    )


def group_summary(name: str, grp: list[dict]) -> str:
    tally: dict[str, int] = {}
    for r in grp:
        tally[r["direction"]] = tally.get(r["direction"], 0) + 1
    counted = ", ".join(
        f"{tally[d]} {DIRECTION_LABEL[d]}" for d in DIRECTION_ORDER if d in tally)
    sized = [r for r in grp if isinstance(r["n_total"], (int, float))]
    if sized:
        big = max(sized, key=lambda r: r["n_total"])
        big_txt = (f"The largest contributing study is {E(big['label'])} "
                   f"(n = {E(big['n_total'])}, appraisal {E(big['rob'])}), which is "
                   f"{E(DIRECTION_LABEL[big['direction']])} and "
                   f"{E(big['precision'])}.")
    else:
        big_txt = ("No study in this unit states an analysed N, so no study can be "
                   "weighted against another.")
    lowest = [r for r in grp if r["rob_rank"] <= 1]
    if lowest:
        low_txt = (" Lowest-risk-of-bias rows: "
                   + "; ".join(f"{E(r['label'])} ({E(DIRECTION_LABEL[r['direction']])}, "
                               f"{E(r['precision'])})" for r in lowest) + ".")
    else:
        low_txt = (" No row in this unit is judged low risk of bias, so the tally is not "
                   "anchored by a low-risk study.")
    n_missing = sum(1 for r in grp if r["precision"] == "unreported")
    miss_txt = ("" if not n_missing else
                f" {n_missing} of {len(grp)} rows report no interval; those are "
                "categorised as <em>unreported</em> and are not read as evidence of no "
                "effect.")
    abstract = sum(1 for r in grp if r["basis"] == "abstract_only")
    abs_txt = ("" if not abstract else
               f" {abstract} row(s) rest on an abstract alone and were not appraised as if "
               "full text.")
    return (f"<li><strong>{E(trunc(name, 110))}</strong> &mdash; {len(grp)} row(s): "
            f"{E(counted)}. {big_txt}{low_txt}{miss_txt}{abs_txt}</li>")


# ---------------------------------------------------------------- study cards

def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(s)).strip("-").lower() or "record"


def ids_html(st: Study, links: bool) -> str:
    bits: list[str] = []
    pmid, doi, pmcid = st.rec.get("pmid"), st.rec.get("doi"), st.rec.get("pmcid")
    if pmid:
        if links:
            bits.append(f'PMID <a href="https://pubmed.ncbi.nlm.nih.gov/{A(pmid)}/" '
                        f'rel="noopener noreferrer" target="_blank">{E(pmid)}</a>')
        else:
            bits.append(f"PMID {E(pmid)}")
    if doi:
        if links:
            bits.append(f'DOI <a href="https://doi.org/{A(doi)}" rel="noopener noreferrer" '
                        f'target="_blank">{E(doi)}</a>')
        else:
            bits.append(f"DOI {E(doi)}")
    if pmcid:
        if links:
            bits.append(f'PMCID <a href="https://pmc.ncbi.nlm.nih.gov/articles/{A(pmcid)}/" '
                        f'rel="noopener noreferrer" target="_blank">{E(pmcid)}</a>')
        else:
            bits.append(f"PMCID {E(pmcid)}")
    if not bits:
        return f'<span class="muted">no persistent identifier recorded</span>'
    return " &middot; ".join(bits)


def h_cards_section(studies: list[Study], links: bool, kernel: Kernel) -> str:
    head = ('<section id="studies"><h2>5. Included studies, in detail</h2>'
            "<p>One card per included study: the extraction record, the appraisal domains, "
            "the evidence-integrity state, the source excerpts and the acquisition route that "
            "produced the text. Cards are collapsed by default and open in print.</p>"
            "<p class=\"small\">Excerpts are <strong>not</strong> transcribed by an agent. "
            "Under decision <strong>D4</strong> and resolution <strong>R17</strong>, "
            "<code>extraction.quotes[]</code> is a <em>derived</em> field: "
            "<code>scripts/assemble.py</code> re-slices <code>snapshot.text[start:end]</code> "
            "from an immutable snapshot and writes the result. Where a record carries no span, "
            "no excerpt can be derived and the record is shown as <em>unverified</em> "
            "(schema R16) &mdash; which is not the same as tampered, and not the same as "
            "absent.</p>"
            '<p class="noprint"><button type="button" class="btn" id="expand-all" '
            'aria-pressed="false">Expand all studies</button></p>')
    if not studies:
        return head + '<p class="muted">No included study to describe.</p></section>'

    cards: list[str] = []
    for st in sorted(studies, key=lambda s: (s.first_author or "zz", s.year or "")):
        cards.append(h_card(st, links, kernel))
    return head + "".join(cards) + "</section>"


def h_integrity(st: Study, kernel: Kernel) -> str:
    """Per-study evidence-integrity block, read from `outputs/result.json` (schema §13)."""
    head = "<h4>Evidence integrity</h4>"
    if not kernel.present:
        return (head + f'<p class="muted">{KERNEL_ABSENT_NOTE}</p>')

    rows: list[str] = []
    for kind, label in (("extraction", "Extraction artifact"),
                        ("appraisal", "Appraisal artifact")):
        acc = kernel.accepted_for(st.eid, kind)
        unr = kernel.unresolved_for(st.eid, kind)
        cells: list[str] = []
        for a in acc:
            claims = a.get("claims") or []
            n_claims = len(claims) if isinstance(claims, list) else 0
            sids = [str(s) for s in (a.get("source_ids") or []) if s]
            cells.append(
                '<div><span class="pill pass">accepted by the assembler</span> '
                f'<code class="small">{E(a.get("artifact_id"))}</code><br>'
                f'<span class="small muted">{n_claims} resolved claim span(s) &middot; '
                f"access {E(a.get('access') or NOT_STATED)} &middot; "
                + ('<span class="pill pass">fresh</span>' if a.get("fresh") is True
                   else '<span class="pill warn">not fresh this run</span>')
                + (" &middot; snapshots " + ", ".join(f"<code>{E(trunc(s, 20))}</code>"
                                                      for s in sids) if sids else "")
                + "</span></div>")
        for u in unr:
            code = str(u.get("reason_code") or "unknown")
            cells.append(
                '<div><span class="pill fail">not accepted &mdash; unresolved</span> '
                f'<code class="small">{E(u.get("artifact_id"))}</code><br>'
                f'<span class="small">reason <code>{E(code)}</code>'
                + (f" at <code>{E(u.get('field'))}</code>" if u.get("field") else "")
                + f" &mdash; {E(REASON_CODE_MEANING.get(code, 'reason code not in the §13 enum.'))}"
                + f"<br><span class=\"muted\">{T(u.get('detail'), 'no detail recorded')}</span>"
                "</span></div>")
        if not cells:
            cells.append('<span class="muted">not listed in the assembler result &mdash; '
                         "neither accepted nor unresolved</span>")
        rows.append(f'<tr><th scope="row">{label}</th><td>{"".join(cells)}</td></tr>')

    gate = kernel.gate
    gate_txt = (f'gate <span class="pill {A(str(gate.get("verdict") or "unknown"))}">'
                f'{E(gate.get("verdict") or "not recorded")}</span>, '
                f"{'enforcing' if gate.get('enabled') else 'not enforcing (off by default, R20/D7)'}")
    return (head +
            '<div class="tablewrap"><table class="meta"><tbody>' + "".join(rows) +
            f'<tr><th scope="row">Run-level gate</th><td>{gate_txt}</td></tr>'
            "</tbody></table></div>")


def h_quotes(st: Study, kernel: Kernel) -> str:
    """The three evidence states: verified excerpt, unverified legacy record, or absent."""
    derived, origin = derived_quotes(st, kernel)
    _, legacy = split_quotes(st.ext)
    ext_accepted = bool(kernel.accepted_for(st.eid, "extraction"))
    parts: list[str] = []

    if derived:
        if origin == "result":
            pill = '<span class="pill pass">verified excerpt</span>'
            standing = ("The assembler accepted this study's extraction artifact, so each "
                        "excerpt below was re-sliced from the named snapshot and matched. It "
                        "is read from <code>outputs/result.json</code>, not from the agent's "
                        "extraction file.")
        elif kernel.present and not ext_accepted:
            pill = '<span class="pill warn">span-backed, artifact not accepted</span>'
            standing = ("These entries carry snapshot offsets, but the assembler did not "
                        "accept this study's extraction artifact &mdash; see <em>Evidence "
                        "integrity</em> above for the reason code. Treat the excerpts as "
                        "located, not as cleared.")
        elif kernel.present:
            pill = '<span class="pill warn">span-backed, not re-sliced by this assembler run</span>'
            standing = ("These entries come from the extraction record rather than from the "
                        "assembler's accepted artifact, so the text below was not re-sliced "
                        "by the run that produced <code>outputs/result.json</code>.")
        else:
            pill = '<span class="pill warn">span-backed, acceptance unconfirmed</span>'
            standing = ("These entries carry snapshot offsets, but this run has no "
                        "<code>outputs/result.json</code>, so no assembler result confirms "
                        "that the text still matches the snapshot at those offsets.")
        parts.append("<h4>Source excerpts &mdash; re-sliced from immutable snapshots</h4>"
                     f'<p class="small">{standing} The text is not typed by an agent: it is '
                     "<code>snapshot.text[start:end]</code>, with <code>end</code> exclusive "
                     "(schema §12, R10/R17).</p>")
        for q in derived:
            start, end = q.get("start"), q.get("end")
            length = end - start
            meta = [f"snapshot <code>{E(q.get('source_id'))}</code>",
                    f"characters {E(start)}&ndash;{E(end)} "
                    f"(<code>end</code> exclusive, {E(length)} chars)"]
            if length > SPAN_MAX_CHARS:
                meta.append('<span class="pill fail">over the '
                            f"{SPAN_MAX_CHARS}-character span cap</span>")
            src = kernel.source_label(str(q.get("source_id")))
            sect = q.get("section")
            meta.append(E(sect) if sect else '<span class="muted">no section recorded</span>')
            if q.get("page") is not None:
                meta.append(f"p. {E(q.get('page'))}")
            parts.append(
                '<blockquote class="quote">'
                f'<div style="white-space:pre-wrap">{T(q.get("text"), "empty re-slice")}</div>'
                f"<footer>{pill} " + " &middot; ".join(meta) + f"<br>{src}</footer>"
                "</blockquote>")

    if legacy:
        parts.append(
            "<h4>Unverified transcribed text &mdash; no span</h4>"
            '<div class="banner"><p><strong>Unverified.</strong> '
            + (f"{len(legacy)} entry below carries no "
               if len(legacy) == 1 else f"{len(legacy)} entries below carry no ")
            + "<code>source_id</code>/<code>start</code>/<code>end</code>, so nothing was "
            "re-sliced from a snapshot and the text cannot be checked against one. Under "
            "schema resolution <strong>R16</strong> such a record is legal, is never deleted, "
            "and can never enter <code>accepted[]</code>; it appears in "
            "<code>diagnostics.unresolved[]</code> with <code>NO_SPANS</code>. "
            "<em>Unverified is not tampered</em> &mdash; there is simply nothing here to "
            "verify against.</p></div>")
        for q in legacy:
            foot = [f'<span class="pill warn">unverified &mdash; no span recorded</span>',
                    T(q.get("section"), "anchor not stated")]
            if q.get("page") is not None:
                foot.append(f"p. {E(q.get('page'))}")
            parts.append('<blockquote class="quote">'
                         f'<div style="white-space:pre-wrap">{T(q.get("text"))}</div>'
                         f"<footer>{' &middot; '.join(foot)}</footer></blockquote>")

    if parts:
        return "".join(parts)

    # ---- third state: genuinely no excerpt at all
    head = "<h4>Source excerpts</h4>"
    if not kernel.present and not kernel.has_sources:
        return (head + '<p class="muted">None. This run predates the evidence kernel: it has '
                "no snapshot store (<code>&lt;run&gt;/sources/</code>) and no "
                "<code>outputs/result.json</code>, so no excerpt could be derived for any "
                "record. This study&rsquo;s evidence is therefore <strong>unverified</strong> "
                "&mdash; not verified, and not tampered. Nothing is missing that this run ever "
                "claimed to produce.</p>")
    if st.basis == "abstract_only":
        return (head + '<p class="muted">None derived. This record rests on an abstract '
                "alone; where no span was recorded against the abstract snapshot, there is "
                "nothing to re-slice. The record is <strong>unverified</strong> (schema "
                "R16), which is stated rather than hidden.</p>")
    return (head + '<p class="muted">None derived. This full-text record carries no claim '
            "span, so <code>scripts/assemble.py</code> had nothing to re-slice and wrote "
            "<code>quotes: []</code>. Under schema resolution <strong>R16</strong> the record "
            "is <strong>unverified</strong>: legal, never deleted, never accepted, reported "
            "as <code>NO_SPANS</code>. It is not tampered and it is not silently missing.</p>")


def h_card(st: Study, links: bool, kernel: Kernel) -> str:
    ext, app = st.ext, st.app
    ft = st.ft
    badges = st.badges_html()
    authors = st.rec.get("authors") or []
    author_txt = ", ".join(str(a) for a in authors[:6]) + (
        ", et al." if len(authors) > 6 else "")

    summary = (
        f'<summary><span class="studyname">{E(st.label)}</span> '
        f'<span class="cardline">{E(trunc(st.rec.get("title") or NOT_STATED, 110))}</span> '
        f"{badges}</summary>")

    # -- bibliographic
    bib = (
        '<dl class="kv">'
        f"<dt>Title</dt><dd>{T(st.rec.get('title'))}</dd>"
        f"<dt>Authors</dt><dd>{T(author_txt)}</dd>"
        f"<dt>Journal / date</dt><dd>{T(st.rec.get('journal'))}, "
        f"{T(st.rec.get('publication_date'))}</dd>"
        f"<dt>Identifiers</dt><dd>{ids_html(st, links)}</dd>"
        f"<dt>evidence_id</dt><dd><code>{E(st.eid)}</code></dd>"
        f"<dt>Article types</dt><dd>{T(', '.join(st.rec.get('article_types') or []) or None, 'none recorded')}</dd>"
        f"<dt>Source</dt><dd>{T(st.rec.get('source'))}"
        + (" &mdash; <strong>preprint, not peer reviewed</strong>" if st.is_preprint else "")
        + "</dd>"
        f"<dt>Retraction status</dt><dd>{T(st.retracted)}</dd>"
        f"<dt>Screening decision</dt><dd>{T(st.decision, 'not screened')}"
        + (f' &mdash; {T(st.screening.get("reason"))}' if st.screening.get("reason") else "")
        + "</dd>"
        "</dl>")

    # -- acquisition route
    acq = (
        "<h4>Acquisition route</h4>"
        '<dl class="kv">'
        f"<dt>Text status</dt><dd>{T(ft.get('status'))}</dd>"
        f"<dt>Ladder rung</dt><dd>"
        + (T(None, "not attempted") if ft.get("source_tier") is None
           else E(TIER_LABEL.get(ft.get("source_tier"), str(ft.get("source_tier")))))
        + "</dd>"
        f"<dt>Access route</dt><dd><code>{T(ft.get('access_route'), 'not attempted')}</code></dd>"
        f"<dt>Stored file</dt><dd>{('<code>' + E(ft.get('local_path')) + '</code>') if ft.get('local_path') else T(None, 'nothing stored')}</dd>"
        f"<dt>sha256</dt><dd>{('<code>' + E(trunc(ft.get('sha256'), 24)) + '</code>') if ft.get('sha256') else T(None, 'not recorded')}</dd>"
        f"<dt>Truncation detected</dt><dd>{E(bool(ft.get('truncation_detected')))}"
        + (" &mdash; the HTML route was truncated, so this record is treated as "
           "<strong>abstract-only</strong>" if ft.get("truncation_detected") else "")
        + "</dd>"
        f"<dt>First seen via query</dt><dd>{T(st.rec.get('first_seen_query'), 'not recorded')}</dd>"
        "</dl>")

    # -- extraction
    if not ext:
        extraction = ('<h4>Extraction</h4><p class="muted">No extraction record was written '
                      "for this study. It therefore contributes no row to the evidence "
                      "table.</p>")
    else:
        n_arms = ext.get("n_arms") or []
        outs = ext.get("outcomes") or []
        if outs:
            orows = "".join(
                "<tr>"
                f"<td>{T(o.get('name'))}</td>"
                f"<td>{T(o.get('timepoint'))}</td>"
                f"<td>{T(effect_text(o), NOT_REPORTED)}</td>"
                f"<td>{T(p_text(o), NOT_REPORTED)}</td>"
                f'<td class="dir-{A(o.get("direction") or "unclear")}">'
                f"{E(DIRECTION_LABEL.get(o.get('direction') or 'unclear', 'unclear'))}</td>"
                f"<td>{E(precision_class(o))}</td>"
                "</tr>" for o in outs if isinstance(o, dict))
            otable = ('<div class="tablewrap"><table class="meta"><thead><tr>'
                      "<th>Outcome</th><th>Timepoint</th><th>Effect [CI]</th><th>p</th>"
                      "<th>Direction</th><th>Precision</th></tr></thead><tbody>"
                      + orows + "</tbody></table></div>")
        else:
            otable = ('<p class="muted">The extraction record reports no extractable '
                      "outcome.</p>")
        extraction = (
            "<h4>Extraction</h4>"
            '<dl class="kv">'
            f"<dt>Design</dt><dd>{T(ext.get('design'))}</dd>"
            f"<dt>N analysed</dt><dd>{T(ext.get('n_total'))}"
            + (f" (arms: {E(', '.join(str(a) for a in n_arms))})" if n_arms else "")
            + "</dd>"
            f"<dt>Population</dt><dd>{T(ext.get('population'))}</dd>"
            f"<dt>Intervention</dt><dd>{T(ext.get('intervention'))}</dd>"
            f"<dt>Comparator</dt><dd>{T(ext.get('comparator'))}</dd>"
            f"<dt>Funding</dt><dd>{T(ext.get('funding'), 'not stated (distinct from &ldquo;none&rdquo;)')}</dd>"
            f"<dt>Conflicts of interest</dt><dd>{T(ext.get('coi'), 'not stated')}</dd>"
            f"<dt>Limitations</dt><dd>{T(ext.get('limitations'))}</dd>"
            f"<dt>Extractor notes</dt><dd>{T(ext.get('extractor_notes'), 'none')}</dd>"
            f"<dt>Evidence basis</dt><dd>{T(ext.get('evidence_basis'))}</dd>"
            "</dl>" + otable)

    # -- appraisal
    if not app:
        appraisal = ('<h4>Appraisal</h4><p class="muted">No appraisal record was written for '
                     "this study. Its rows carry <em>not appraised</em>, which is not the "
                     "same as a favourable judgement.</p>")
    else:
        domains = app.get("domains") or []
        if domains:
            drows = "".join(
                "<tr>"
                f"<td>{T(d.get('domain'))}</td>"
                f'<td class="rob {A(ROB_CLASS.get(str(d.get("judgement")).lower(), "rob-unc"))}">'
                f"{T(d.get('judgement'))}</td>"
                f"<td>{T(d.get('rationale'))}</td></tr>"
                for d in domains if isinstance(d, dict))
            dtable = ('<div class="tablewrap"><table class="meta"><thead><tr>'
                      "<th>Domain</th><th>Judgement</th><th>Rationale</th>"
                      "</tr></thead><tbody>" + drows + "</tbody></table></div>")
        elif str(app.get("tool")) == "none":
            dtable = ('<p class="muted">No in-scope appraisal instrument applies to this '
                      "record, so no domains were rated. This is a pipeline limitation, not a "
                      "quality verdict.</p>")
        else:
            dtable = '<p class="muted">No domains were recorded.</p>'
        grade = app.get("grade")
        if isinstance(grade, dict) and grade:
            grows = "".join(
                f'<tr><th scope="row">{E(k.replace("_", " "))}</th>'
                f"<td>{T(v)}</td></tr>" for k, v in grade.items())
            gtable = ('<h4>GRADE (single-study view)</h4>'
                      '<div class="tablewrap"><table class="meta"><tbody>' + grows +
                      "</tbody></table></div>"
                      '<p class="small muted">Resolution R4: <code>inconsistency</code> and '
                      "<code>publication_bias</code> are body-level domains. The values here "
                      "are the single-study view; the stage-7 synthesis judgement overrides "
                      "them wherever the two differ.</p>")
        else:
            gtable = ""
        appraisal = ("<h4>Appraisal</h4>"
                     '<dl class="kv">'
                     f"<dt>Tool</dt><dd>{T(app.get('tool'))}</dd>"
                     f"<dt>Overall judgement</dt><dd class=\"rob {A(ROB_CLASS.get(str(app.get('overall_judgement')).lower(), 'rob-unc'))}\">"
                     f"{T(app.get('overall_judgement'))}</dd>"
                     f"<dt>Evidence basis</dt><dd>{T(app.get('evidence_basis'))}</dd>"
                     "</dl>" + dtable + gtable)

    # -- evidence integrity and source excerpts (schema §12/§13, decisions D4/D7/D9)
    integrity = h_integrity(st, kernel)
    qblock = h_quotes(st, kernel)

    focus = (f'<p class="noprint small"><a href="#evidence" data-focus="{A(st.eid)}">'
             "Filter the evidence table to this study &rarr;</a></p>")

    return (f'<details class="card" id="card-{A(slug(st.eid))}">' + summary +
            '<div class="cardbody">' + bib + acq + extraction + appraisal + integrity +
            qblock + focus + "</div></details>")


# ---------------------------------------------------------------- gaps panel

def study_line(st: Study, links: bool) -> str:
    return (f"<li><strong>{E(st.label)}</strong> {st.badges_html()}<br>"
            f"{T(trunc(st.rec.get('title') or NOT_STATED, 150))}<br>"
            f'<span class="small muted">{T(st.rec.get("journal"))} &middot; '
            f"{ids_html(st, links)} &middot; rung "
            + (E(TIER_LABEL.get(st.tier, str(st.tier))) if st.tier is not None
               else "not attempted")
            + f" &middot; route <code>{T(st.ft.get('access_route'), 'none')}</code>"
              "</span></li>")


def unverified_studies(included: list[Study], kernel: Kernel) -> list[Study]:
    """Included studies with no span-backed excerpt at all (schema R16, `unverified`)."""
    out: list[Study] = []
    for s in included:
        if not s.ext:
            continue
        derived, _origin = derived_quotes(s, kernel)
        if not derived:
            out.append(s)
    return out


def h_kernel_block(kernel: Kernel, included: list[Study]) -> str:
    """Evidence-kernel surfacing inside the honesty panel (schema §13, decisions D7/D9)."""
    unver = unverified_studies(included, kernel)
    if unver:
        unver_block = (
            f"<h3>Unverified evidence &mdash; no snapshot-backed span ({len(unver)})</h3>"
            '<div class="banner"><p>These included records carry no derived, span-backed '
            "excerpt. Under schema resolution <strong>R16</strong> that is legal and is never "
            "treated as tampering, but such a record can never enter "
            "<code>accepted[]</code> and can never back a promoted OKF concept&rsquo;s "
            "evidence footnote, gate or no gate.</p></div>"
            '<ul class="plain">'
            + "".join(f"<li><strong>{E(s.label)}</strong> "
                      f'<code class="small">{E(s.eid)}</code></li>' for s in unver)
            + "</ul>")
    else:
        unver_block = ("<h3>Unverified evidence &mdash; no snapshot-backed span</h3>"
                       '<p class="muted">None: every included record with an extraction '
                       "carries at least one excerpt re-sliced from a snapshot.</p>")

    if not kernel.present:
        return ('<h3>Evidence-integrity gate (assembler result)</h3>'
                f'<div class="banner"><p>{KERNEL_ABSENT_NOTE}</p>'
                "<p>Nothing here is reported as failing, because nothing was checked. The "
                "unresolved-artifact register below is empty for the same reason &mdash; it "
                "is unwritten, not clean.</p></div>"
                + unver_block)

    gate = kernel.gate
    counts = kernel.counts
    verdict = str(gate.get("verdict") or "unknown")
    meta_rows = "".join(
        f'<tr><th scope="row">{lab}</th><td>{val}</td></tr>' for lab, val in (
            ("Gate verdict", f'<span class="pill {A(verdict)}">{E(verdict)}</span>'),
            ("Gate enforcing?",
             "yes &mdash; unresolved artifacts block stage 8" if gate.get("enabled")
             else "no &mdash; off by default (R20 / decision D7); unresolved artifacts are "
                  "reported and do not block"),
            ("Artifacts seen", E(num(counts.get("artifacts_seen")))),
            ("Accepted", E(num(counts.get("accepted")))),
            ("Unresolved", E(num(counts.get("unresolved")))),
            ("Spans checked", E(num(counts.get("spans_checked")))),
            ("Snapshots referenced", E(num(counts.get("sources")))),
            ("Assembler run at", f'<code>{E(kernel.result.get("generated_at") or NOT_STATED)}</code>'),
        ))

    if kernel.unresolved:
        urows = "".join(
            "<tr>"
            f'<td><code class="small">{E(u.get("artifact_id"))}</code></td>'
            f"<td>{T(u.get('kind'))}</td>"
            f"<td><code class=\"small\">{T(u.get('evidence_id'), 'not in corpus.jsonl')}</code></td>"
            f"<td>{T(u.get('field'), 'whole artifact')}</td>"
            f'<td><code>{E(u.get("reason_code") or "unknown")}</code></td>'
            f"<td>{T(u.get('detail'), 'no detail recorded')}</td>"
            "</tr>"
            for u in kernel.unresolved)
        utable = ('<div class="tablewrap"><table class="meta"><caption>'
                  "<code>diagnostics.unresolved[]</code> from "
                  "<code>outputs/result.json</code>, verbatim.</caption><thead><tr>"
                  "<th>Artifact</th><th>Kind</th><th>evidence_id</th><th>Field</th>"
                  "<th>Reason code</th><th>Detail</th></tr></thead><tbody>"
                  + urows + "</tbody></table></div>")
        seen = []
        for u in kernel.unresolved:
            c = str(u.get("reason_code") or "")
            if c and c not in seen:
                seen.append(c)
        by_reason = kernel.counts_by_reason
        gloss = "".join(
            f'<li><code>{E(c)}</code> &mdash; '
            + (f"{E(by_reason[c])} artifact(s). " if c in by_reason else "")
            + E(REASON_CODE_MEANING.get(c, "not a code in the schema §13 enum."))
            + "</li>" for c in seen)
        gloss_block = (f"<h4>Reason codes seen ({len(seen)})</h4>"
                       f'<ul class="plain">{gloss}</ul>')
        unresolved_block = (
            f"<h3>Unresolved artifacts ({len(kernel.unresolved)})</h3>"
            '<div class="banner"><p>The assembler could not accept these artifacts. They are '
            "reported here at full detail rather than dropped."
            + ("" if gate.get("enabled") else
               " The gate is off, so they did not block this run &mdash; they still mark the "
               "synthesis provisional.")
            + "</p></div>" + utable + gloss_block)
    else:
        unresolved_block = ('<h3>Unresolved artifacts</h3><p class="muted">None: the '
                            "assembler accepted every artifact it saw.</p>")

    return ('<h3>Evidence-integrity gate (assembler result)</h3>'
            "<p>Read from <code>outputs/result.json</code> (schema &sect;13), written by "
            "<code>scripts/assemble.py</code> before the verifier (R24). Every excerpt on this "
            "page is derived by that assembler from an immutable snapshot; no agent-written "
            "quote is trusted (R17).</p>"
            '<div class="tablewrap"><table class="meta"><tbody>' + meta_rows +
            "</tbody></table></div>" + unresolved_block + unver_block)


def h_gaps_section(all_studies: list[Study], included: list[Study],
                   missing_md: str | None, links: bool, kernel: Kernel) -> str:
    quarantined = [s for s in all_studies if s.decision == "include" and s.quarantined]
    not_attempted = [s for s in all_studies if s.decision == "include" and s.not_yet_attempted]
    abstract_only = [s for s in included if s.basis == "abstract_only"]
    preprints = [s for s in included if s.is_preprint]
    retracted = [s for s in all_studies if s.retracted == "retracted"]
    eoc = [s for s in all_studies if s.retracted == "expression_of_concern"]
    corrected = [s for s in all_studies if s.retracted == "corrected"]
    no_ext = [s for s in included if not s.ext]
    no_app = [s for s in included if not s.app]
    no_outcome = [s for s in included if s.ext and not (s.ext.get("outcomes") or [])]
    unclear_screen = [s for s in all_studies if s.decision == "unclear"]

    def block(title, items, empty, note) -> str:
        if not items:
            return f'<h3>{title}</h3><p class="muted">{empty}</p>'
        lis = "".join(study_line(s, links) for s in items)
        return (f"<h3>{title} ({len(items)})</h3>"
                + (f'<div class="banner"><p>{note}</p></div>' if note else "")
                + f'<ul class="plain">{lis}</ul>')

    parts = [
        '<section id="gaps"><h2>6. Gaps, quarantine and honesty panel</h2>',
        "<p>What follows is what this run could <em>not</em> establish. It is reported at the "
        "same prominence as what it could, because a review that hides its shortfalls is a "
        "more confident review, not a better one.</p>",
        block("Quarantined &mdash; full text sought and not obtained", quarantined,
              "Nothing is quarantined: every included record's text was obtained.",
              "These records were <strong>not assessed</strong>. Their abstracts are not "
              "paraphrased as if they were the study. The synthesis is provisional while they "
              "are outstanding; drop PDFs into the run&rsquo;s <code>inbox/</code> and rerun "
              "to ingest them."),
        block("Retrieval not yet attempted", not_attempted,
              "None: retrieval was attempted for every included record.",
              "Schema resolution <strong>R8</strong>: these are <code>status: missing</code> "
              "with no <code>access_route</code> yet, so they are not counted as "
              "unobtainable. They are simply not done."),
        block("Abstract-only evidence", abstract_only,
              "No included study rests on an abstract alone.",
              "Conduct could not be appraised from an abstract. Every domain not assessable "
              "from an abstract is rated <code>unclear</code>, never favourably, and every "
              "claim resting on these records must be labelled at the point of use."),
        block("Preprints &mdash; not peer reviewed", preprints,
              "No included record is a preprint.",
              "<strong>Preprint (not peer reviewed).</strong> Content may differ from any "
              "later published version; where a preprint is a rung-6 twin of a published "
              "article, the version actually read is the preprint."),
        block("Retracted", retracted,
              "No record in the corpus is retracted.",
              "<strong>RETRACTED.</strong> Retracted records are excluded from the synthesis. "
              "Where one is mentioned at all, the retraction is stated in the same sentence."),
        block("Expression of concern", eoc,
              "No record carries an expression of concern.",
              "<strong>Expression of Concern.</strong> These may be included, carry the marker "
              "at every point of use, and contribute to the GRADE risk-of-bias judgement."),
        block("Corrected", corrected,
              "No record carries a published correction.", ""),
    ]

    def plainblock(title, items, empty, note) -> str:
        if not items:
            return f'<h3>{title}</h3><p class="muted">{empty}</p>'
        lis = "".join(f"<li><strong>{E(s.label)}</strong> "
                      f"<code class=\"small\">{E(s.eid)}</code></li>" for s in items)
        return (f"<h3>{title} ({len(items)})</h3><p>{note}</p>"
                f'<ul class="plain">{lis}</ul>')

    parts += [
        plainblock("Included but not extracted", no_ext,
                   "Every included study has an extraction record.",
                   "These studies contribute no row to the evidence table. Their absence is a "
                   "gap in this run, not a property of the literature."),
        plainblock("Included but not appraised", no_app,
                   "Every included study has an appraisal record.",
                   "These studies carry <em>not appraised</em> in the evidence table. That is "
                   "an absence of a judgement, not a favourable one."),
        plainblock("Extracted, but reporting no extractable outcome", no_outcome,
                   "Every extracted study reports at least one outcome.",
                   "The paper reports no outcome this extraction could take a direction, "
                   "estimate or interval from."),
        plainblock("Left <code>unclear</code> at screening", unclear_screen,
                   "No record was left unclear at screening.",
                   "<code>unclear</code> is a legitimate screening decision and was not "
                   "coerced into include or exclude."),
    ]

    parts.append(h_kernel_block(kernel, included))

    if missing_md is not None:
        parts.append(
            '<h3><code>missing.md</code>, as written by the run</h3>'
            "<details class=\"card\"><summary>Show the raw quarantine register</summary>"
            f'<div class="cardbody"><pre style="white-space:pre-wrap;overflow-x:auto">'
            f"{E(missing_md)}</pre></div></details>")
    else:
        parts.append('<h3><code>missing.md</code></h3>'
                     '<p class="muted">No <code>missing.md</code> is present in the run '
                     "directory.</p>")

    parts.append("</section>")
    return "".join(parts)


# ---------------------------------------------------------------- verification

def h_verify_section(ver: dict | None) -> str:
    head = '<section id="verification"><h2>7. Verification</h2>'
    if ver is None:
        return (head + '<div class="banner"><h3>Not verified</h3>'
                "<p>No <code>outputs/verification.json</code> was found. "
                "<code>scripts/verify.py</code> did not run, or did not complete. An "
                "unverified report is treated as provisional.</p></div></section>")
    checks = ver.get("checks") or []
    rows = "".join(
        f'<tr><th scope="row"><code>{E(c.get("check_id"))}</code></th>'
        f'<td><span class="pill {A(str(c.get("status") or "unknown"))}">'
        f'{E(c.get("status") or "unknown")}</span></td>'
        f"<td>{T(c.get('detail'))}</td></tr>"
        for c in checks if isinstance(c, dict))
    table = ('<div class="tablewrap"><table class="meta"><thead><tr><th>Check</th>'
             "<th>Status</th><th>Detail</th></tr></thead><tbody>"
             + (rows or '<tr><td colspan="3" class="muted">No checks recorded.</td></tr>')
             + "</tbody></table></div>")

    def listing(title, items, render, empty):
        if not items:
            return f'<h3>{title}</h3><p class="muted">{empty}</p>'
        return (f"<h3>{title} ({len(items)})</h3>"
                f'<ul class="plain">{"".join(render(i) for i in items)}</ul>')

    unsupported = listing(
        "Unsupported claims", ver.get("unsupported_claims") or [],
        lambda c: (f"<li><code>{T(c.get('location'))}</code> &mdash; "
                   f"&ldquo;{T(c.get('claim'))}&rdquo;<br>"
                   f'<span class="small muted">{T(c.get("reason"))}</span></li>'),
        "None: every claim in the report resolves to a record retrieved this run.")
    uncited = listing(
        "Included but never cited", ver.get("uncited_citations") or [],
        lambda i: f"<li><code>{E(i)}</code></li>",
        "None: every included study is cited at least once.")
    missing_ft = listing(
        "Full text missing", ver.get("missing_fulltext") or [],
        lambda i: f"<li><code>{E(i)}</code></li>",
        "None recorded by the verifier.")
    aoc = ver.get("abstract_only_claims") or []
    unlabelled = [c for c in aoc if isinstance(c, dict) and not c.get("labelled")]
    aoc_block = listing(
        "Claims resting on abstract-only evidence", aoc,
        lambda c: (f"<li><code>{T(c.get('location'))}</code> &rarr; "
                   f"<code>{T(c.get('evidence_id'))}</code> &mdash; "
                   + ('<span class="pill pass">labelled</span>' if c.get("labelled")
                      else '<span class="pill fail">NOT labelled at the point of use</span>')
                   + "</li>"),
        "None recorded.")
    if unlabelled:
        aoc_block = ('<div class="banner"><p><strong>' + str(len(unlabelled)) +
                     " claim(s) rest on abstract-only evidence without a label at the point "
                     "of use.</strong> That is a <code>C-FULLTEXT</code> failure.</p></div>"
                     + aoc_block)
    okf = ver.get("okf_validation")
    okf_block = (f'<h3>OKF bundle validation</h3><p><span class="pill '
                 f'{A(str(okf or "unknown"))}">{E(okf or "not recorded")}</span>'
                 + (" &mdash; promotion to the wiki bundle is blocked while this fails."
                    if okf == "fail" else "") + "</p>")
    return head + table + unsupported + uncited + missing_ft + aoc_block + okf_block + "</section>"


# ---------------------------------------------------------------- hypotheses

HYP_HEADING = re.compile(
    r"^#{1,3}\s*\d*\.?\s*(new insights?|hypothes[ie]s)[^\n]*$", re.IGNORECASE | re.MULTILINE)


def extract_report_hypotheses(report_md: str) -> str | None:
    m = HYP_HEADING.search(report_md)
    if not m:
        return None
    start = m.start()
    level = len(report_md[start:].split(" ", 1)[0])
    rest = report_md[m.end():]
    nxt = re.search(r"^#{1,%d}\s" % max(level, 1), rest, re.MULTILINE)
    body = rest[: nxt.start()] if nxt else rest
    body = body.strip()
    return body or None


def h_insights_section(hyps: list[dict] | None, report_body: str | None,
                       provisional: bool) -> str:
    notice = (
        '<div class="notice"><p><strong>Hard wall.</strong> Everything in this section is '
        "generated inference, <strong>not</strong> a finding of the literature. Nothing here "
        "is supported by the studies above. Do not cite this section as evidence, do not "
        "quote it as a conclusion, and do not carry any sentence from it back into sections "
        "1&ndash;7. Citations inside this section point at what <em>prompted</em> an idea; "
        "they never make the idea itself evidenced "
        "(<code>references/synthesis.md</code> §7).</p>"
        + ("<p>The corpus behind these hypotheses is <strong>provisional</strong>, so the "
           "hypotheses are too.</p>" if provisional else "")
        + "</div>")

    if hyps:
        items = []
        for i, h in enumerate(hyps, 1):
            if not isinstance(h, dict):
                continue
            items.append(
                f'<div class="hyp"><h3>H{i} &mdash; {T(h.get("title"), "untitled")}</h3>'
                '<dl class="kv">'
                f"<dt>Idea (speculative)</dt><dd>{T(h.get('statement') or h.get('idea'))}</dd>"
                f"<dt>What prompted it</dt><dd>{T(h.get('prompted_by') or h.get('trigger'))}</dd>"
                f"<dt>Testable prediction</dt><dd>{T(h.get('prediction'))}</dd>"
                f"<dt>How to test it</dt><dd>{T(h.get('test'))}</dd>"
                f"<dt>What would falsify it</dt><dd>{T(h.get('falsifier'))}</dd>"
                "</dl></div>")
        body = "".join(items) or '<p class="muted">The hypotheses file contained no usable entry.</p>'
    elif report_body:
        body = ('<p class="small muted">Lifted verbatim from the &ldquo;New insights&rdquo; '
                "section of <code>outputs/report.md</code> and rendered as plain text. It is "
                "not re-interpreted here.</p>"
                f"<pre>{E(report_body)}</pre>")
    else:
        body = ('<p class="muted">No hypotheses were recorded for this run. Neither '
                "<code>outputs/hypotheses.json</code> nor a &ldquo;New insights&rdquo; section "
                "in <code>outputs/report.md</code> was found. This section is empty on "
                "purpose rather than filled speculatively.</p>")

    return ('<section id="hypotheses" class="hardwall">'
            "<h2>8. New insights &amp; hypotheses &mdash; NOT EVIDENCE</h2>"
            + notice + body + "</section>")


# --------------------------------------------------------------------- build


def escape_json_for_script(obj) -> str:
    """Serialise for embedding inside a `<script type="application/json">` element.

    HTML entities are NOT decoded inside a script element, so `html.escape` would
    corrupt the payload there. The correct escape for this context is the JSON one:
    `ensure_ascii` renders every non-ASCII character as a \\uXXXX sequence, and `<`,
    `>` and `&` are then rewritten to their own \\uXXXX escapes. JSON.parse decodes
    them back to the exact original characters, while the serialised text can no
    longer contain `<`, `>` or `&` at all — so `</script>`, a tag, an entity or a
    comment opener cannot be produced by any paper title, abstract or quote.
    """
    text = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return (text.replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026"))


def compute_provisional(all_studies: list[Study], included: list[Study],
                        ver: dict | None, kernel: Kernel) -> dict:
    triggers: list[str] = []
    q = [s for s in all_studies if s.decision == "include" and s.quarantined]
    if q:
        triggers.append(f"{len(q)} included record(s) could not be obtained in full text and "
                        "are quarantined at rung 7.")
    na = [s for s in all_studies if s.decision == "include" and s.not_yet_attempted]
    if na:
        triggers.append(f"{len(na)} included record(s) have had no retrieval attempt yet "
                        "(schema R8): the acquisition ladder has not finished.")
    ao = [s for s in included if s.basis == "abstract_only"]
    if ao:
        triggers.append(f"{len(ao)} included record(s) rest on an abstract alone; their "
                        "conduct was not appraised.")
    ne = [s for s in included if not s.ext]
    if ne:
        triggers.append(f"{len(ne)} included record(s) have no extraction record.")
    napp = [s for s in included if not s.app]
    if napp:
        triggers.append(f"{len(napp)} included record(s) have no appraisal record.")
    if ver is None:
        triggers.append("No <code>outputs/verification.json</code>: the run was not verified.")
    else:
        fails = [c for c in (ver.get("checks") or [])
                 if isinstance(c, dict) and c.get("status") == "fail"]
        for c in fails:
            triggers.append(f"Verifier check <code>{E(c.get('check_id'))}</code> failed: "
                            f"{E(c.get('detail'))}")
        if ver.get("unsupported_claims"):
            triggers.append(f"{len(ver['unsupported_claims'])} unsupported claim(s) recorded "
                            "by the verifier.")
        unlabelled = [c for c in (ver.get("abstract_only_claims") or [])
                      if isinstance(c, dict) and not c.get("labelled")]
        if unlabelled:
            triggers.append(f"{len(unlabelled)} abstract-only claim(s) are unlabelled at the "
                            "point of use.")
    retracted = [s for s in included if s.retracted == "retracted"]
    if retracted:
        triggers.append(f"{len(retracted)} retracted record(s) are present in the included "
                        "set and must be excluded from the synthesis.")

    # ---- evidence-kernel triggers (schema §12/§13, decisions D4/D7/D9)
    unver = unverified_studies(included, kernel)
    if unver:
        if not kernel.present and not kernel.has_sources:
            triggers.append(
                f"{len(unver)} included record(s) carry no snapshot-backed span, because this "
                "run predates the evidence kernel (no <code>sources/</code>, no "
                "<code>outputs/result.json</code>). Their evidence is "
                "<strong>unverified</strong> under schema R16 &mdash; not verified, and not "
                "tampered.")
        else:
            triggers.append(
                f"{len(unver)} included record(s) carry no snapshot-backed span, so their "
                "evidence is <strong>unverified</strong> (schema R16, "
                "<code>NO_SPANS</code>): it can never be accepted by the assembler.")
    if kernel.present:
        if kernel.unresolved:
            codes = sorted({str(u.get("reason_code") or "unknown")
                            for u in kernel.unresolved})
            triggers.append(
                f"{len(kernel.unresolved)} artifact(s) were not accepted by the assembler "
                f"(<code>diagnostics.unresolved[]</code>: {E(', '.join(codes))}).")
        if str(kernel.gate.get("verdict") or "") == "fail":
            triggers.append("The evidence-kernel gate verdict in "
                            "<code>outputs/result.json</code> is <code>fail</code>.")
    return {"provisional": bool(triggers), "triggers": triggers}


def build(args) -> int:
    run_dir = Path(args.run_dir).expanduser()
    if not run_dir.is_dir():
        print(f"html_report.py: error: run directory not found: {run_dir}", file=sys.stderr)
        return 2

    corpus_path = run_dir / "corpus.jsonl"
    if not corpus_path.is_file():
        print(f"html_report.py: error: no corpus.jsonl in {run_dir}", file=sys.stderr)
        return 3
    records = read_jsonl(corpus_path)
    if not records:
        warn("corpus.jsonl contains no usable record; the report will be nearly empty")

    extractions = load_dir_records(run_dir / "workspace" / "extractions", "extraction")
    appraisals = load_dir_records(run_dir / "workspace" / "appraisals", "appraisal")

    studies: list[Study] = []
    for rec in records:
        eid = str(rec.get("evidence_id") or "")
        if not eid:
            warn("corpus record without evidence_id skipped")
            continue
        ext = extractions.get(eid)
        app = appraisals.get(eid)
        # honour explicit paths from the corpus record when the index missed
        if ext is None and rec.get("extraction_path"):
            p = run_dir / str(rec["extraction_path"])
            if p.is_file():
                try:
                    ext = read_json(p)
                except (json.JSONDecodeError, OSError) as exc:
                    warn(f"{p}: unreadable extraction record ({exc})")
        if app is None and rec.get("appraisal_path"):
            p = run_dir / str(rec["appraisal_path"])
            if p.is_file():
                try:
                    app = read_json(p)
                except (json.JSONDecodeError, OSError) as exc:
                    warn(f"{p}: unreadable appraisal record ({exc})")
        studies.append(Study(rec, ext if isinstance(ext, dict) else None,
                             app if isinstance(app, dict) else None))

    included = [s for s in studies
                if (s.decision == "include" or (s.decision is None and s.ext))
                and s.status in ("fulltext", "abstract_only")]
    rows = build_rows(included)

    counts = {
        "corpus": len(studies),
        "included": len(included),
        "fulltext": sum(1 for s in included if s.basis == "fulltext"),
        "abstract_only": sum(1 for s in included if s.basis == "abstract_only"),
        "quarantined": sum(1 for s in studies if s.decision == "include" and s.quarantined),
        "not_attempted": sum(1 for s in studies
                             if s.decision == "include" and s.not_yet_attempted),
        "preprints": sum(1 for s in included if s.is_preprint),
        "retracted": sum(1 for s in studies if s.retracted != "none"),
    }

    # ancillary inputs
    cfg = {}
    cfg_path = run_dir / "config.json"
    if cfg_path.is_file():
        try:
            loaded = read_json(cfg_path)
            cfg = loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            warn(f"config.json unreadable ({exc}); run metadata will read 'not recorded'")
    else:
        warn("no config.json in the run directory; run metadata will read 'not recorded'")

    ver = None
    ver_path = run_dir / "outputs" / "verification.json"
    if ver_path.is_file():
        try:
            loaded = read_json(ver_path)
            ver = loaded if isinstance(loaded, dict) else None
            if ver is None:
                warn("verification.json is not a JSON object; treated as absent")
        except (json.JSONDecodeError, OSError) as exc:
            warn(f"verification.json unreadable ({exc}); treated as absent")

    # assembler result (schema §13). Absent for a pre-kernel run; per R20 / decision D7 the
    # gate is off by default, so that is a normal state and is reported, never warned about.
    result = None
    res_path = run_dir / "outputs" / "result.json"
    if res_path.is_file():
        try:
            loaded = read_json(res_path)
            result = loaded if isinstance(loaded, dict) else None
            if result is None:
                warn("outputs/result.json is not a JSON object; treated as absent")
        except (json.JSONDecodeError, OSError) as exc:
            warn(f"outputs/result.json unreadable ({exc}); treated as absent")
    kernel = Kernel(result, has_sources=(run_dir / "sources").is_dir())

    missing_md = None
    mm = run_dir / "missing.md"
    if mm.is_file():
        missing_md = read_text(mm)

    hyps = None
    hyp_path = Path(args.hypotheses) if args.hypotheses else run_dir / "outputs" / "hypotheses.json"
    if hyp_path.is_file():
        try:
            loaded = read_json(hyp_path)
            if isinstance(loaded, list):
                hyps = loaded
            elif isinstance(loaded, dict) and isinstance(loaded.get("hypotheses"), list):
                hyps = loaded["hypotheses"]
            else:
                warn(f"{hyp_path}: not a list of hypotheses; ignored")
        except (json.JSONDecodeError, OSError) as exc:
            warn(f"{hyp_path}: unreadable ({exc})")

    report_body = None
    rp = run_dir / "outputs" / "report.md"
    if hyps is None and rp.is_file():
        txt = read_text(rp)
        if txt:
            report_body = extract_report_hypotheses(txt)

    prisma, prisma_note = run_prisma(run_dir, Path(args.corpus_script))
    if prisma_note:
        warn(prisma_note)

    prov = compute_provisional(studies, included, ver, kernel)
    links = not args.no_external_links

    # ---- title block
    question = args.title or cfg.get("question") or cfg.get("title")
    if not question:
        question = f"Deep-research evidence synthesis: {run_dir.name}"
        warn("no question in config.json and no --title given; using the run slug")
    status_word = "PROVISIONAL" if prov["provisional"] else "final"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    subtitle = (
        f"{counts['included']} included studies &middot; {len(rows)} study&times;outcome rows "
        f"&middot; {counts['fulltext']} on full text, {counts['abstract_only']} on abstract "
        f"only &middot; {counts['quarantined']} quarantined &middot; status "
        f"<strong>{E(status_word)}</strong>.")

    # ---- embedded search index (HTML-escaped JSON; see the note in the template)
    blob = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "rows": [
            {"i": r["i"],
             "text": " | ".join([
                 r["label"], r["study"].eid, r["design"], r["population"], r["ic"],
                 r["outcome_name"], r["timepoint"], r["effect"], r["p"],
                 DIRECTION_LABEL[r["direction"]], r["precision"], r["rob"], r["tool"],
                 r["basis"], "" if r["tier"] is None else f"tier {r['tier']}",
                 " ".join(r["study"].flags),
                 str(r["study"].rec.get("title") or ""),
                 str(r["study"].rec.get("journal") or ""),
             ])}
            for r in rows
        ],
    }
    data_json = escape_json_for_script(blob)

    # ---- assemble
    template_path = Path(args.template)
    if not template_path.is_file():
        print(f"html_report.py: error: template not found: {template_path}", file=sys.stderr)
        return 3
    tpl = Template(template_path.read_text(encoding="utf-8"))

    footer = (
        f"Generated {E(generated_at)} by <code>{E(GENERATOR)}</code> from run "
        f"<code>{E(cfg.get('slug') or run_dir.name)}</code>. Literature identified via PubMed "
        "and NCBI E-utilities; full text via PMC, Europe PMC and Unpaywall as recorded "
        "per study in <code>corpus.jsonl</code>. Per-claim attribution, footnotes and the "
        "narrative synthesis live in <code>outputs/report.md</code>; this page is a "
        "navigable rendering of the same records and adds no claim of its own."
    )
    if WARNINGS:
        footer += (' <span class="muted">Build warnings: '
                   + E("; ".join(WARNINGS[:12]))
                   + (" &hellip;" if len(WARNINGS) > 12 else "") + "</span>")

    out_html = tpl.safe_substitute(
        TITLE_TEXT=html.escape(
            f"{trunc(question, 90)} — deep-research report ({status_word})", quote=False),
        TITLE_HTML=E(question),
        SUBTITLE_HTML=subtitle,
        RUN_SLUG=E(cfg.get("slug") or run_dir.name),
        BANNER=h_banner(prov),
        RUN_SECTION=h_run_section(cfg, run_dir, prisma_note, counts, generated_at,
                                  kernel),
        PRISMA_SECTION=h_prisma_section(prisma, prisma_note, counts),
        EVIDENCE_SECTION=h_evidence_section(rows, counts),
        CHART_SECTION=h_chart_section(rows),
        CARDS_SECTION=h_cards_section(included, links, kernel),
        GAPS_SECTION=h_gaps_section(studies, included, missing_md, links, kernel),
        VERIFY_SECTION=h_verify_section(ver),
        INSIGHTS_SECTION=h_insights_section(hyps, report_body, prov["provisional"]),
        DATA_JSON=data_json,
        FOOTER_HTML=footer,
        GENERATED_AT=E(generated_at),
    )

    if args.stdout:
        sys.stdout.write(out_html)
        return 0

    out_path = Path(args.out) if args.out else run_dir / "outputs" / "report.html"
    if not out_path.is_absolute():
        out_path = run_dir / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(out_html, encoding="utf-8")
    tmp.replace(out_path)
    if not args.quiet:
        print(json.dumps({
            "output_path": str(out_path),
            "bytes": len(out_html.encode("utf-8")),
            "included_studies": counts["included"],
            "rows": len(rows),
            "status": status_word,
            "provisional_triggers": len(prov["triggers"]),
            "evidence_kernel": ("result.json present" if kernel.present
                                else "no result.json (pre-kernel run; gate off by default)"),
            "unresolved_artifacts": len(kernel.unresolved),
            "unverified_records": len(unverified_studies(included, kernel)),
            "warnings": len(WARNINGS),
        }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="html_report.py",
        description="Build the self-contained HTML deliverable for a deep-research run "
                    "(evidence table, effect-direction chart, per-study cards, honest gaps).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build", help="render outputs/report.html from a run directory")
    p.add_argument("--run-dir", required=True,
                   help="run directory <wiki>/outputs/deep-research/<slug>/")
    p.add_argument("--out", default=None,
                   help="output path (default: <run-dir>/outputs/report.html); a relative "
                        "path is resolved against the run directory")
    p.add_argument("--template", default=str(DEFAULT_TEMPLATE),
                   help=f"HTML template (default: {DEFAULT_TEMPLATE})")
    p.add_argument("--corpus-script", default=str(CORPUS_SCRIPT),
                   help="path to corpus.py, used for the PRISMA counters")
    p.add_argument("--hypotheses", default=None,
                   help="JSON file of hypotheses (default: <run-dir>/outputs/hypotheses.json, "
                        "then the 'New insights' section of outputs/report.md)")
    p.add_argument("--title", default=None,
                   help="override the report title / research question")
    p.add_argument("--no-external-links", action="store_true",
                   help="render PMID/DOI/PMCID as plain text instead of hyperlinks, so the "
                        "file contains no http(s) URL at all")
    p.add_argument("--stdout", action="store_true", help="write the HTML to stdout")
    p.add_argument("--quiet", action="store_true", help="suppress the JSON build receipt")
    p.set_defaults(func=build)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
