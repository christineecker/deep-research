#!/usr/bin/env python3
"""verify.py — final consistency pass over a completed deep-research run (stage 8).

Reads a run directory (`<wiki>/outputs/deep-research/<slug>/`) plus its `outputs/report.md`
and writes `outputs/verification.json` conforming to `references/schema.md` §9
(`verifier result`). Every check has a stable `check_id`; any `status: "fail"` makes the
report provisional and blocks OKF promotion.

**The report is never deleted, rewritten, or edited by this script.** A failing verification
only records the failure and blocks promotion (`SKILL.md` "Failure semantics").

Checks
------
  C-CITE-RESOLVE     every footnote key in the report resolves to a corpus evidence_id /
                     sources[].id, and every reference has a definition
  C-CORPUS-COMPLETE  every included study has screening + extraction + appraisal records on
                     disk; every cited evidence_id exists in corpus.jsonl; included but never
                     cited records are listed in `uncited_citations`
  C-SEARCH-LOG       every executed query has a search result record with query string,
                     NCBI-translated query and hit count, and appears verbatim in the report
  C-RETRACTION       retracted / expression-of-concern records are flagged wherever cited
  C-FULLTEXT         every claim resting on abstract_only evidence is labelled as such;
                     `missing_fulltext` matches `missing.md`
  C-HYPOTHESIS-WALL  hypotheses are not phrased as established evidence and evidence sections
                     do not smuggle in speculation (heuristic — see caveats below)
  C-PRISMA           the report's PRISMA numbers match `corpus.py prisma` exactly, and the
                     reporting.md §1 arithmetic invariants close
  C-PREPRINT         preprints are tagged loudly wherever cited
  C-ATTRIBUTION      per-claim footnotes exist (a body-only citation list fails); PubMed-sourced
                     footnotes carry the PubMed link and the DOI where one exists
  C-PROVISIONAL      if anything is quarantined or any check failed, the report says the
                     synthesis is PROVISIONAL and lists the quarantined records
  C-SECTIONS         the report carries the sections required by references/reporting.md §3
  C-OKF              shells out to `okf.py validate` when --wiki was given

Evidence-kernel checks (schema.md §9 "Evidence-kernel checks", §10-§13, R10-R24)
--------------------------------------------------------------------------------
  C-SNAPSHOT         every snapshot the run references exists under `<run>/sources/`, parses,
                     and its stored `content_hash` / `source_id` recompute from its own
                     `url` + `text`
  C-SPAN             every claim span is in range, at most 2000 characters, and any excerpt
                     carried alongside it equals the re-slice `snapshot.text[start:end]`
  C-FRESH-FETCH      every source backing a claim has a fresh retrieval logged in this run
                     (R15), with the `user-supplied-pdf` exception (a `local_pdf` event plus
                     an asset file that still hashes to `asset.sha256`)
  C-ASSEMBLER        `outputs/result.json` (written by `scripts/assemble.py`, schema §13)
                     exists, is schema-shaped, and carries no unresolved artifacts

All four are **implemented by importing `scripts/store.py`** (decision D5): snapshot hashing,
span slicing and the five-condition freshness rule live in exactly one place, and a single
`store.Store(run_dir)` is instantiated so each snapshot is hashed once per run.

The gate (R20 / D7)
-------------------
The assembler+verifier gate is **off by default** until a live dry run passes. `--gate`
(or `config.json` `gates.evidence_kernel: true`, or a top-level `gate: true`) turns it on.

  * Gate ON  — every kernel violation is a `fail` and blocks stage 8.
  * Gate OFF — the kernel checks still run and are still reported; violations are `warn`,
    and a run with no `sources/` at all (or no `outputs/result.json`) reports `skipped`
    with the reason. Silence is never an option.
  * **Never downgraded, gate or no gate:** a `C-SNAPSHOT` missing/unparseable/hash-mismatched
    snapshot and a `C-SPAN` out-of-range, over-length or excerpt-mismatched span are hard
    `fail`s. Tampered evidence is not a rollout concern (R20, schema §9).

Legacy records with no spans at all are `unverified` (R16), not tampered: they are `warn` with
the gate off and `fail` with it on.

`status` is `pass` | `fail` | `warn` as in schema §9, plus `skipped` for the two gate-off
"nothing to check" states above — consumers (`okf.py`, `html_report.py`) test for `fail` and
`warn`, so `skipped` is inert for them while staying visible to the operator.

Honest limits of C-HYPOTHESIS-WALL
----------------------------------
This check is **lexical and structural, not semantic**. It cannot read meaning. It does three
mechanical things:

  1. Section boundary detection. The hypothesis section is the first heading matching
     /new insight|hypothes/i, running until the next heading of the same or higher level (or a
     References/Appendix heading). Everything before it is treated as evidence-side prose.
     A report that puts hypotheses in an unlabelled section, or that has no such heading,
     defeats this entirely — the check then reports a warn, not a pass.
  2. A closed list of contract-forbidden phrases (`references/synthesis.md` §7 "Forbidden"
     column) — *proves*, *clearly shows*, *studies show*, *data support*, GRADE wordings inside
     the hypothesis section, and so on. A phrase from that list on the wrong side of the wall is
     reported as **fail**, because the contract names it explicitly.
  3. A hedge/assertion heuristic — a declarative hypothesis-section sentence carrying an
     assertion verb and no hedging marker, or an evidence-section sentence carrying speculation
     markers. This is a *phrasing suspicion only* and is always reported as **warn**.
     Block-quoted lines are exempt from the heuristic (the hard-wall notice itself is a
     declarative sentence), though the forbidden-phrase list still applies to them.

What it therefore cannot do: judge whether a properly hedged sentence is actually supported;
recognise a hypothesis expressed without any of the listed vocabulary; understand irony,
quotation, or a sentence quoting someone else's overclaim; or split sentences perfectly
(abbreviations such as "e.g." can end a pseudo-sentence). Tables, code fences, HTML comments,
headings and footnote definitions are excluded from prose scanning to cut false positives, which
also means an overclaim hidden in a table cell is invisible to it. A `pass` here means "no banned
phrasing was found", never "the wall is semantically sound".

Environment: python3 (3.14), stdlib only, no network. There is no `python` on this machine.

CLI
---
  verify.py run --run-dir <dir> [--wiki <root>] [--report <path>] [--json] [--markdown <path>]
                [--gate | --no-gate]

Exit codes: 0 = no check failed (warns do not fail), 1 = at least one check failed,
2 = fatal error (missing run dir, unreadable corpus, missing report).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import read_json, slugify, repo_root_for_run  # noqa: E402  (sibling module, stdlib-only)

SCHEMA_VERSION = 1
HERE = Path(__file__).resolve().parent

PASS, FAIL, WARN = "pass", "fail", "warn"
#: Gate-off "nothing to check" status; see the module docstring. Not part of schema §9's
#: three-value enum, and deliberately inert for `okf.py` / `html_report.py`.
SKIPPED = "skipped"

#: The evidence-kernel check ids, in execution order (schema §9).
KERNEL_CHECKS = ("C-SNAPSHOT", "C-SPAN", "C-FRESH-FETCH", "C-ASSEMBLER")

# D5: store.py is the single implementation of snapshot / span / freshness checking.
try:
    import store as store_mod
except ImportError:                                       # pragma: no cover - path fallback
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    try:
        import store as store_mod
    except ImportError:
        store_mod = None


class FatalError(Exception):
    """Unrecoverable: the run cannot be verified at all."""


# --------------------------------------------------------------------------- utils

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise FatalError(f"{path}:{n}: malformed JSON line ({exc})")
    return out

def doi_slug(doi: str) -> str:
    """Same derivation as okf.py, so footnote keys line up with sources[].id."""
    return slugify(str(doi).replace("/", "-").replace(":", "-").replace(".", "-"))


def norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def short(text: str, limit: int = 220) -> str:
    text = norm_ws(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+")


def sentences(text: str) -> list[tuple[int, str]]:
    """(offset, sentence) pairs. Naive splitter; abbreviations may split early."""
    out: list[tuple[int, str]] = []
    pos = 0
    for piece in SENTENCE_SPLIT.split(text):
        idx = text.find(piece, pos)
        if idx < 0:
            idx = pos
        if piece.strip():
            out.append((idx, piece))
        pos = idx + len(piece)
    return out


# --------------------------------------------------------------------------- report


class Report:
    """Parsed view of report.md: lines, headings/sections, footnotes, prose spans."""

    FOOTNOTE_DEF = re.compile(r"^\[\^([^\]\s]+)\]:\s*(.*)$")
    FOOTNOTE_REF = re.compile(r"\[\^([^\]\s]+)\]")
    HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")

    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.text = path.read_text(encoding="utf-8")
        self.lines = self.text.splitlines()
        self.frontmatter: list[str] = []
        self._parse_frontmatter()
        self._classify_lines()
        self._parse_headings()
        self._parse_footnotes()

    # -- structure ---------------------------------------------------------

    def _parse_frontmatter(self) -> None:
        if self.lines and self.lines[0].strip() == "---":
            for i, line in enumerate(self.lines[1:], 1):
                if line.strip() == "---":
                    self.frontmatter = self.lines[1:i]
                    self.frontmatter_end = i
                    return
        self.frontmatter_end = -1

    def _classify_lines(self) -> None:
        """prose_line[i] is True when line i carries checkable prose."""
        self.prose_line = [True] * len(self.lines)
        in_fence = False
        in_comment = False
        for i, raw in enumerate(self.lines):
            line = raw.strip()
            if i <= self.frontmatter_end:
                self.prose_line[i] = False
                continue
            if in_comment:
                self.prose_line[i] = False
                if "-->" in line:
                    in_comment = False
                continue
            if line.startswith("<!--"):
                self.prose_line[i] = False
                if "-->" not in line:
                    in_comment = True
                continue
            if line.startswith("```") or line.startswith("~~~"):
                in_fence = not in_fence
                self.prose_line[i] = False
                continue
            if in_fence:
                self.prose_line[i] = False
                continue
            if line.startswith("|"):          # table row
                self.prose_line[i] = False
                continue
            if self.HEADING.match(line):
                self.prose_line[i] = False
                continue
            if self.FOOTNOTE_DEF.match(raw):
                self.prose_line[i] = False
                continue
            if not line:
                self.prose_line[i] = False

    def _parse_headings(self) -> None:
        self.headings: list[tuple[int, int, str]] = []   # (line_idx, level, title)
        in_fence = False
        for i, raw in enumerate(self.lines):
            line = raw.strip()
            if line.startswith("```") or line.startswith("~~~"):
                in_fence = not in_fence
                continue
            if in_fence or i <= self.frontmatter_end:
                continue
            m = self.HEADING.match(line)
            if m:
                self.headings.append((i, len(m.group(1)), m.group(2).strip()))

    def _parse_footnotes(self) -> None:
        self.footnote_defs: dict[str, tuple[int, str]] = {}
        self.footnote_refs: list[tuple[str, int]] = []    # (key, line_idx)
        self.def_lines: set[int] = set()
        for i, raw in enumerate(self.lines):
            m = self.FOOTNOTE_DEF.match(raw)
            if m:
                body = [m.group(2)]
                self.def_lines.add(i)
                for j in range(i + 1, len(self.lines)):
                    nxt = self.lines[j]
                    if nxt.startswith(("    ", "\t")) and nxt.strip():
                        body.append(nxt.strip())
                        self.def_lines.add(j)
                    else:
                        break
                self.footnote_defs.setdefault(m.group(1), (i, " ".join(body)))
                continue
            if not self.prose_line[i] and not raw.strip().startswith("|"):
                # headings/comments/fences carry no citations we act on
                if not self.HEADING.match(raw.strip()):
                    continue
            for r in self.FOOTNOTE_REF.finditer(raw):
                if raw[r.end():r.end() + 1] == ":":
                    continue
                self.footnote_refs.append((r.group(1), i))

    # -- lookups -----------------------------------------------------------

    def loc(self, line_idx: int) -> str:
        return f"{self.name}:L{line_idx + 1}"

    def section_of(self, line_idx: int) -> str:
        title = ""
        for i, _level, text in self.headings:
            if i <= line_idx:
                title = text
            else:
                break
        return title

    def find_section(self, pattern: str) -> tuple[int, int] | None:
        """(start_line, end_line_exclusive) of the first heading matching `pattern`."""
        rx = re.compile(pattern, re.I)
        for n, (i, level, title) in enumerate(self.headings):
            if rx.search(title):
                end = len(self.lines)
                for j, lvl, t in self.headings[n + 1:]:
                    if lvl <= level or re.search(r"^\s*(#*\s*)?(references|appendix|bibliography)",
                                                 t, re.I):
                        end = j
                        break
                return (i, end)
        return None

    def paragraph_at(self, line_idx: int) -> tuple[int, int]:
        """Blank-line-delimited paragraph containing `line_idx` (inclusive bounds)."""
        start = line_idx
        while start > 0 and self.lines[start - 1].strip() and not self.HEADING.match(
                self.lines[start - 1].strip()):
            start -= 1
        end = line_idx
        while end + 1 < len(self.lines) and self.lines[end + 1].strip() and not self.HEADING.match(
                self.lines[end + 1].strip()):
            end += 1
        return start, end

    def context(self, line_idx: int) -> tuple[str, str]:
        """(sentence-ish line text, whole paragraph text) around a citation."""
        start, end = self.paragraph_at(line_idx)
        para = " ".join(self.lines[start:end + 1])
        return self.lines[line_idx], para

    def body_text(self) -> str:
        """The report minus its footnote definitions — what the *body* actually says."""
        return "\n".join(l for i, l in enumerate(self.lines) if i not in self.def_lines)

    def title_block(self) -> str:
        """Frontmatter plus everything up to the second heading — where PROVISIONAL lives."""
        end = self.headings[1][0] if len(self.headings) > 1 else min(len(self.lines), 40)
        return "\n".join(self.lines[:end])

    def prose_spans(self, lo: int = 0, hi: int | None = None) -> list[tuple[int, str]]:
        hi = len(self.lines) if hi is None else hi
        return [(i, self.lines[i]) for i in range(lo, min(hi, len(self.lines)))
                if self.prose_line[i] and self.lines[i].strip()]


# --------------------------------------------------------------------------- run data


class RunData:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        if not run_dir.is_dir():
            raise FatalError(f"run directory not found: {run_dir}")
        self.corpus_path = run_dir / "corpus.jsonl"
        if not self.corpus_path.exists():
            raise FatalError(f"corpus.jsonl not found in {run_dir}")
        self.records = read_jsonl(self.corpus_path)
        self.by_id = {r.get("evidence_id"): r for r in self.records if r.get("evidence_id")}
        self.tasks = read_jsonl(run_dir / "taskboard.jsonl")
        self.searches = self._load_searches()
        self.verdicts = self._load_verdicts()
        self.missing_md_ids, self.missing_md_present = self._load_missing_md()
        self.cite_keys = self._build_cite_keys()

    # -- loaders -----------------------------------------------------------

    def _load_searches(self) -> list[tuple[Path, dict]]:
        out = []
        sdir = self.run_dir / "workspace" / "search"
        if sdir.is_dir():
            for f in sorted(sdir.glob("*.json")):
                if f.name.startswith("."):
                    continue
                try:
                    out.append((f, read_json(f)))
                except json.JSONDecodeError as exc:
                    out.append((f, {"_error": str(exc)}))
        return out

    def _load_verdicts(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        sdir = self.run_dir / "workspace" / "screening"
        if not sdir.is_dir():
            return out
        for f in sorted(sdir.rglob("*.json")):
            if f.name.startswith(".") or f.parent.name == "adjudication":
                continue
            try:
                rec = read_json(f)
            except json.JSONDecodeError:
                continue
            pmid = rec.get("pmid")
            eid = rec.get("evidence_id")
            key = eid or (f"pmid:{pmid}" if pmid and str(pmid).isdigit() else pmid)
            if key:
                out.setdefault(str(key), []).append(rec)
        return out

    def _load_missing_md(self) -> tuple[set[str], bool]:
        path = self.run_dir / "missing.md"
        if not path.exists():
            return set(), False
        ids = set(re.findall(r"^\s*-\s*evidence_id\s*:\s*(\S+)\s*$",
                             path.read_text(encoding="utf-8"), re.M | re.I))
        return ids, True

    # -- derived views -----------------------------------------------------

    def _build_cite_keys(self) -> dict[str, str]:
        """Every acceptable footnote key -> evidence_id (lowercased keys)."""
        keys: dict[str, str] = {}

        def put(k: str | None, eid: str) -> None:
            if k:
                keys.setdefault(k.strip().lower(), eid)

        for rec in self.records:
            eid = rec.get("evidence_id")
            if not eid:
                continue
            pmid, doi, pmcid = rec.get("pmid"), rec.get("doi"), rec.get("pmcid")
            put(eid, eid)
            put(eid.replace(":", "-"), eid)
            put(re.sub(r"[^A-Za-z0-9]+", "", eid), eid)          # schema R6 BibTeX key
            put(re.sub(r"[^A-Za-z0-9._~-]+", "-", eid).strip("-"), eid)
            if pmid:
                for k in (f"pubmed-{pmid}", f"pmid-{pmid}", f"pmid{pmid}",
                          f"fulltext-{pmid}", str(pmid)):
                    put(k, eid)
            if doi:
                for k in (f"doi-{doi_slug(doi)}", f"doi-{doi}", f"fulltext-{doi_slug(doi)}",
                          doi_slug(doi)):
                    put(k, eid)
            if pmcid:
                for k in (f"pmc-{pmcid}", f"pmcid-{pmcid}", f"pmc-{slugify(pmcid)}", pmcid):
                    put(k, eid)
            if rec.get("okf_source_ids"):
                for k in rec["okf_source_ids"]:
                    put(k, eid)
        return keys

    def resolve(self, key: str) -> str | None:
        return self.cite_keys.get(key.strip().lower())

    def included(self) -> list[dict]:
        return [r for r in self.records
                if (r.get("screening") or {}).get("decision") == "include"]

    def fulltext(self, rec: dict) -> dict:
        return rec.get("fulltext") or {}

    def quarantined(self) -> list[dict]:
        """Included records whose retrieval was attempted and failed (schema R8)."""
        return [r for r in self.included()
                if self.fulltext(r).get("status") == "missing"
                and self.fulltext(r).get("access_route")]

    def retrieval_not_attempted(self) -> list[dict]:
        return [r for r in self.included()
                if self.fulltext(r).get("status") == "missing"
                and not self.fulltext(r).get("access_route")]

    def abstract_only(self) -> list[dict]:
        return [r for r in self.included()
                if self.fulltext(r).get("status") == "abstract_only"]

    def label_of(self, rec: dict) -> str:
        eid = rec.get("evidence_id") or "?"
        return f"{eid} ({short(rec.get('title') or '', 70)})"


# --------------------------------------------------------------------------- lexicons

ABSTRACT_ONLY_MARKER = re.compile(
    r"abstract[\s\-–—]?only|only the abstract|rests? on the abstract|"
    r"based on the abstract|from the abstract alone|abstract[\s\-–—]?based", re.I)

PREPRINT_MARKER = re.compile(r"preprint|not peer[\s\-]?reviewed|biorxiv|medrxiv|research square",
                             re.I)

RETRACTION_MARKERS = {
    "retracted": re.compile(r"\bretract(ed|ion)\b", re.I),
    "expression_of_concern": re.compile(r"expression of concern|\beoc\b", re.I),
    "corrected": re.compile(r"\bcorrect(ion|ed)\b|\berratum\b", re.I),
}

# Contract-forbidden phrasing (references/synthesis.md §7). Matches here are fails.
EVIDENCE_FORBIDDEN = [
    (re.compile(r"\bprove[sd]?\b|\bproof that\b", re.I), "asserts proof"),
    (re.compile(r"\bconfirms that\b|\bconfirmed that\b", re.I), "asserts confirmation"),
    (re.compile(r"\bestablishe[sd] that\b", re.I), "asserts establishment"),
    (re.compile(r"\bclearly (shows|demonstrates)\b", re.I), "unhedged assertion"),
    (re.compile(r"\bit is well[\s\-]known\b", re.I), "uncited common-knowledge claim"),
    (re.compile(r"\bdefinitively (shows|demonstrates|establishes)\b", re.I), "asserts certainty"),
    (re.compile(r"\bbeyond doubt\b", re.I), "asserts certainty"),
]
HYPOTHESIS_FORBIDDEN = [
    (re.compile(r"\bstudies show\b", re.I), "evidence-side verb form in the hypothesis section"),
    (re.compile(r"\bthe evidence (shows|indicates|demonstrates)\b", re.I),
     "evidence-side verb form in the hypothesis section"),
    (re.compile(r"\bevidence indicates\b", re.I), "evidence-side verb form"),
    (re.compile(r"\bdata (support|show|demonstrate)\b", re.I), "evidence-side verb form"),
    (re.compile(r"\b(demonstrates|proves|confirms|establishes) that\b", re.I),
     "asserted finding inside the hypothesis wall"),
    (re.compile(r"\bprobably (reduces|increases|improves)\b", re.I),
     "GRADE wording inside the hypothesis wall"),
    (re.compile(r"\bthe evidence is very uncertain\b", re.I),
     "GRADE wording inside the hypothesis wall"),
    (re.compile(r"\bis associated with\b", re.I), "asserted association in the hypothesis wall"),
]

HEDGE = re.compile(
    r"\b(may|might|could|would|should|hypothes\w*|speculat\w*|untested|plausib\w*|"
    r"conjectur\w*|propose[ds]?|suspect|possib\w*|potential\w*|if\b|unless|"
    r"consistent with|not (yet )?tested|no (study|trial) (has )?tested|predict\w*|"
    r"falsif\w*|test(able|ed by)?)\b", re.I)
ASSERTION_VERB = re.compile(
    r"\b(shows?|demonstrates?|reduces?|increases?|improves?|causes?|leads to|drives?|"
    r"results in|is|are|was|were|explains?|accounts for)\b", re.I)
SPECULATION_MARKER = re.compile(
    r"\b(we hypothesi[sz]e|presumably|it stands to reason|plausible mechanism|"
    r"likely because|probably because|may be explained by|which likely means|"
    r"mechanistically,)\b", re.I)

# A sentence that reports an effect estimate is a claim and needs attribution.
STATISTIC = re.compile(
    r"\b(95%\s*CI|SMD|\bMD\b|\bRR\b|\bOR\b|\bHR\b|risk ratio|odds ratio|hazard ratio|"
    r"p\s*[=<>]\s*0?\.\d+|effect size)\b")

PRISMA_LABEL_MAP: list[tuple[re.Pattern, tuple[str, ...]]] = [
    (re.compile(r"abstract[\s\-–—]?only", re.I), ("retrieval", "abstract_only")),
    (re.compile(r"preprint", re.I), ("included", "preprints")),
    (re.compile(r"retract|expression of concern", re.I), ("included", "retracted_or_eoc")),
    (re.compile(r"duplicat", re.I), ("identification", "duplicates_removed")),
    (re.compile(r"with extraction", re.I), ("included", "with_extraction")),
    (re.compile(r"with appraisal", re.I), ("included", "with_appraisal")),
    (re.compile(r"records? identified.*(pubmed)", re.I), ("_source", "pubmed")),
    (re.compile(r"records? identified.*(other|website|register|citation)", re.I), ("_skip",)),
    (re.compile(r"records? identified", re.I), ("identification", "records_identified")),
    (re.compile(r"records? screened", re.I), ("screening", "records_screened")),
    (re.compile(r"records? excluded|excluded at screening", re.I), ("screening", "excluded")),
    (re.compile(r"unclear", re.I), ("screening", "unclear")),
    (re.compile(r"sought", re.I), ("retrieval", "fulltext_sought")),
    (re.compile(r"not (obtainable|obtained|retrieved)|quarantin|unobtainable", re.I),
     ("retrieval", "fulltext_unobtainable")),
    (re.compile(r"assessed|obtained", re.I), ("retrieval", "fulltext_obtained")),
    (re.compile(r"studies included|reports included|included in (the )?(synthesis|review)", re.I),
     ("included", "studies_included")),
]


# --------------------------------------------------------------------------- verifier


class Verifier:
    def __init__(self, run: RunData, report: Report, wiki: Path | None,
                 repo: Path | None = None, gate: bool = False, gate_source: str = "default"):
        self.run = run
        self.report = report
        self.wiki = wiki
        self.repo = repo
        self.gate = bool(gate)
        self.gate_source = gate_source
        self.kernel: dict = {}
        self.store = None
        self.checks: list[dict] = []
        self.unsupported_claims: list[dict] = []
        self.uncited_citations: list[str] = []
        self.missing_fulltext: list[str] = []
        self.abstract_only_claims: list[dict] = []
        self.okf_validation = "skipped"
        self.cited_ids: set[str] = set()
        self.refs_by_id: dict[str, list[int]] = {}

    # -- plumbing ----------------------------------------------------------

    def add(self, check_id: str, status: str, detail: str, **extra) -> None:
        check = {"check_id": check_id, "status": status, "detail": short(detail, 400)}
        check.update(extra)
        self.checks.append(check)

    def worst(self, *statuses: str) -> str:
        if FAIL in statuses:
            return FAIL
        if WARN in statuses:
            return WARN
        return PASS

    def any_fail(self) -> bool:
        return any(c["status"] == FAIL for c in self.checks)

    def run_all(self) -> dict:
        self.check_cite_resolve()
        self.check_corpus_complete()
        self.check_search_log()
        self.check_retraction()
        self.check_fulltext()
        self.check_hypothesis_wall()
        self.check_prisma()
        self.check_preprint()
        self.check_attribution()
        self.check_sections()
        self.run_kernel_checks()      # C-SNAPSHOT / C-SPAN / C-FRESH-FETCH / C-ASSEMBLER
        self.check_provisional()      # depends on the checks above
        self.check_okf()
        return {
            "schema_version": SCHEMA_VERSION,
            "checks": self.checks,
            "unsupported_claims": self.unsupported_claims,
            "uncited_citations": sorted(set(self.uncited_citations)),
            "missing_fulltext": sorted(set(self.missing_fulltext)),
            "abstract_only_claims": self.abstract_only_claims,
            "okf_validation": self.okf_validation,
            "evidence_kernel": self.kernel_summary(),
        }

    # -- C-CITE-RESOLVE ----------------------------------------------------

    def check_cite_resolve(self) -> None:
        rep, run = self.report, self.run
        unresolved: list[str] = []
        dangling: list[str] = []
        total = len(rep.footnote_refs)

        for key, line in rep.footnote_refs:
            eid = run.resolve(key)
            if eid:
                self.cited_ids.add(eid)
                self.refs_by_id.setdefault(eid, []).append(line)
            else:
                unresolved.append(f"[^{key}] @{rep.loc(line)}")
                _, para = rep.context(line)
                self.unsupported_claims.append({
                    "location": rep.loc(line),
                    "claim": short(para),
                    "reason": f"footnote key '{key}' resolves to no corpus evidence_id "
                              f"or sources[].id",
                })
            if key not in rep.footnote_defs:
                dangling.append(f"[^{key}] @{rep.loc(line)}")

        orphan_defs = [k for k in rep.footnote_defs
                       if k not in {r[0] for r in rep.footnote_refs}]
        bad_defs = [k for k in rep.footnote_defs if not run.resolve(k)]
        for key in bad_defs:
            line, _body = rep.footnote_defs[key]
            self.unsupported_claims.append({
                "location": rep.loc(line),
                "claim": short(f"[^{key}] footnote definition"),
                "reason": f"footnote definition '{key}' resolves to no corpus evidence_id",
            })

        resolved = total - len(unresolved)
        parts = [f"{resolved}/{total} report citations resolve to corpus evidence_ids"]
        status = PASS
        if unresolved:
            status = FAIL
            parts.append(f"{len(unresolved)} unresolvable: " + ", ".join(unresolved[:5]))
        if dangling:
            status = FAIL
            parts.append(f"{len(dangling)} references without a footnote definition: "
                         + ", ".join(dangling[:5]))
        if bad_defs:
            status = FAIL
            parts.append(f"{len(bad_defs)} footnote definitions do not resolve: "
                         + ", ".join(sorted(bad_defs)[:5]))
        if orphan_defs and status != FAIL:
            status = WARN
            parts.append(f"{len(orphan_defs)} footnote definitions never referenced in the body")
        elif orphan_defs:
            parts.append(f"{len(orphan_defs)} footnote definitions never referenced")
        if total == 0:
            status = FAIL
            parts = ["report contains no footnote citations at all"]
        self.add("C-CITE-RESOLVE", status, "; ".join(parts))

    # -- C-CORPUS-COMPLETE -------------------------------------------------

    def check_corpus_complete(self) -> None:
        run, rep = self.run, self.report
        included = run.included()
        missing_screen: list[str] = []
        missing_extract: list[str] = []
        missing_appraise: list[str] = []
        excused: list[str] = []

        for rec in included:
            eid = rec["evidence_id"]
            if eid not in run.verdicts:
                missing_screen.append(eid)
            quarantined = (run.fulltext(rec).get("status") == "missing")
            for field, bucket in (("extraction_path", missing_extract),
                                  ("appraisal_path", missing_appraise)):
                path = rec.get(field)
                ok = bool(path) and (run.run_dir / path).exists()
                if ok:
                    continue
                if quarantined:
                    excused.append(f"{eid}:{field}")
                else:
                    bucket.append(eid)

        # every cited evidence_id must exist in the corpus (resolve() guarantees it, but
        # a report may cite a raw evidence_id that only exists in the OKF bundle)
        ghost_cites = sorted(e for e in self.cited_ids if e not in run.by_id)

        included_ids = {r["evidence_id"] for r in included}
        uncited = sorted(included_ids - self.cited_ids)
        self.uncited_citations = uncited
        # R4: uncited is acceptable only when the report says so, naming the record.
        unexplained = []
        body = rep.body_text()
        for eid in uncited:
            rec = run.by_id.get(eid) or {}
            tokens = [eid] + [t for t in (rec.get("pmid"), rec.get("doi"), rec.get("pmcid")) if t]
            if not any(str(t) in body for t in tokens):
                unexplained.append(eid)

        parts = [f"{len(included)} included records"]
        status = PASS
        if missing_screen:
            status = FAIL
            parts.append(f"{len(missing_screen)} without a screening verdict on disk: "
                         + ", ".join(missing_screen[:5]))
        if missing_extract:
            status = FAIL
            parts.append(f"{len(missing_extract)} without an extraction record: "
                         + ", ".join(missing_extract[:5]))
        if missing_appraise:
            status = FAIL
            parts.append(f"{len(missing_appraise)} without an appraisal record: "
                         + ", ".join(missing_appraise[:5]))
        if ghost_cites:
            status = FAIL
            parts.append(f"{len(ghost_cites)} cited evidence_ids absent from corpus.jsonl: "
                         + ", ".join(ghost_cites[:5]))
        if unexplained:
            status = FAIL
            parts.append(f"{len(unexplained)} included but never cited and not accounted for in "
                         f"the report: " + ", ".join(unexplained[:5]))
        elif uncited:
            status = self.worst(status, WARN)
            parts.append(f"{len(uncited)} included but never cited (reason stated in the report)")
        if excused:
            # quarantine IS the recorded reason (reporting.md §5) — a note, not a warning
            n = len({e.split(':')[0] + ':' + e.split(':')[1] for e in excused})
            parts.append(f"{n} quarantined record(s) excused from extraction/appraisal")
        self.add("C-CORPUS-COMPLETE", status, "; ".join(parts))

    # -- C-SEARCH-LOG ------------------------------------------------------

    def check_search_log(self) -> None:
        run, rep = self.run, self.report
        report_norm = norm_ws(rep.text)
        problems: list[str] = []
        logged_ids: set[str] = set()

        for path, rec in run.searches:
            qid = rec.get("query_id") or path.stem
            if rec.get("_error"):
                problems.append(f"{path.name}: unparseable ({rec['_error']})")
                continue
            logged_ids.add(qid)
            qs = rec.get("query_string")
            if not qs:
                problems.append(f"{qid}: no query_string")
            elif norm_ws(qs) not in report_norm:
                problems.append(f"{qid}: query_string not reproduced verbatim in the report")
            if rec.get("source") == "pubmed" and not rec.get("translated_query"):
                problems.append(f"{qid}: no NCBI translated_query")
            if rec.get("count") is None:
                problems.append(f"{qid}: no hit count")
            if rec.get("hit_count_logged") is not True:
                problems.append(f"{qid}: hit_count_logged is not true")

        executed = {t.get("task_id", "").split(":")[-1]
                    for t in run.tasks
                    if t.get("stage") == "search" and t.get("status") in ("completed", "active")}
        executed.discard("")
        for qid in sorted(executed - {q.split("-p")[0] for q in logged_ids}):
            problems.append(f"{qid}: executed per taskboard but no search result record")

        for rec in run.records:
            for qid in [rec.get("first_seen_query")] + list(rec.get("seen_in_queries") or []):
                if qid and qid not in logged_ids:
                    problems.append(f"{qid}: referenced by {rec['evidence_id']} but not logged")

        problems = sorted(set(problems))
        if not run.searches and not executed:
            self.add("C-SEARCH-LOG", FAIL, "no search result records found in workspace/search/")
            return
        status = FAIL if problems else PASS
        detail = (f"{len(run.searches)} search result records, {len(logged_ids)} query ids"
                  + ("; " + "; ".join(problems[:6]) if problems else "; all logged with query "
                     "string, translated query and hit count"))
        self.add("C-SEARCH-LOG", status, detail)

    # -- C-RETRACTION ------------------------------------------------------

    def check_retraction(self) -> None:
        run, rep = self.run, self.report
        flagged = [r for r in run.records if (r.get("retraction_status") or "none") != "none"]
        problems: list[str] = []
        for rec in flagged:
            eid = rec["evidence_id"]
            marker = RETRACTION_MARKERS[rec["retraction_status"]]
            for line in self.refs_by_id.get(eid, []):
                _sentence, para = rep.context(line)
                if not marker.search(para):
                    problems.append(f"{eid} cited at {rep.loc(line)} without a "
                                    f"{rec['retraction_status']} marker")
            for key, (line, body) in rep.footnote_defs.items():
                if run.resolve(key) == eid and not marker.search(body):
                    problems.append(f"{eid} footnote at {rep.loc(line)} carries no "
                                    f"{rec['retraction_status']} marker")
            if (rec["retraction_status"] == "retracted"
                    and (rec.get("screening") or {}).get("decision") == "include"):
                problems.append(f"{eid} is retracted but screened in as included "
                                f"(must be excluded from synthesis)")
        if not flagged:
            self.add("C-RETRACTION", PASS, "no retracted / expression-of-concern records")
            return
        status = FAIL if problems else PASS
        self.add("C-RETRACTION", status,
                 f"{len(flagged)} flagged records"
                 + ("; " + "; ".join(problems[:6]) if problems
                    else "; all flagged at every point of use"))

    # -- C-FULLTEXT --------------------------------------------------------

    def check_fulltext(self) -> None:
        run, rep = self.run, self.report
        quarantined = {r["evidence_id"] for r in run.quarantined()}
        self.missing_fulltext = sorted(quarantined)
        not_attempted = [r["evidence_id"] for r in run.retrieval_not_attempted()]

        problems: list[str] = []
        if quarantined and not run.missing_md_present:
            problems.append(f"{len(quarantined)} quarantined records but no missing.md")
        else:
            only_md = run.missing_md_ids - quarantined
            only_corpus = quarantined - run.missing_md_ids
            if only_corpus:
                problems.append("missing.md lacks " + ", ".join(sorted(only_corpus)[:5]))
            if only_md:
                problems.append("missing.md lists non-quarantined " + ", ".join(sorted(only_md)[:5]))

        abstract_ids = {r["evidence_id"] for r in run.abstract_only()}
        unlabelled = 0
        for eid in sorted(abstract_ids):
            for line in self.refs_by_id.get(eid, []):
                sentence, para = rep.context(line)
                labelled = bool(ABSTRACT_ONLY_MARKER.search(sentence)
                                or ABSTRACT_ONLY_MARKER.search(para))
                self.abstract_only_claims.append({
                    "location": rep.loc(line), "evidence_id": eid, "labelled": labelled,
                })
                if not labelled:
                    unlabelled += 1
        for key, (line, body) in rep.footnote_defs.items():
            eid = run.resolve(key)
            if eid in abstract_ids and not ABSTRACT_ONLY_MARKER.search(body):
                problems.append(f"{eid} footnote at {rep.loc(line)} does not state the "
                                f"abstract-only basis")

        status = PASS
        parts = [f"{len(abstract_ids)} included studies are abstract_only; "
                 f"{len(quarantined)} quarantined"]
        if unlabelled:
            status = FAIL
            parts.append(f"{unlabelled} abstract-only claim(s) unlabelled at the point of use")
        if problems:
            status = FAIL
            parts.extend(problems[:5])
        if not_attempted:
            status = self.worst(status, WARN)
            parts.append(f"{len(not_attempted)} included records with retrieval not yet "
                         f"attempted (schema R8)")
        if status == PASS and abstract_ids:
            status = WARN
        self.add("C-FULLTEXT", status, "; ".join(parts))

    # -- C-HYPOTHESIS-WALL -------------------------------------------------

    def check_hypothesis_wall(self) -> None:
        rep = self.report
        span = rep.find_section(r"new insight|hypothes")
        fails: list[str] = []
        warns: list[str] = []

        if span is None:
            self.add("C-HYPOTHESIS-WALL", WARN,
                     "no 'New insights / hypotheses' heading found — the evidence/hypothesis "
                     "wall could not be located, so this check is not meaningful")
            return
        h_start, h_end = span

        # 1. hypothesis side
        hyp_lines = rep.prose_spans(h_start, h_end)
        for idx, line in hyp_lines:
            for _off, sent in sentences(line):
                for rx, why in HYPOTHESIS_FORBIDDEN:
                    if rx.search(sent):
                        fails.append(f"{rep.loc(idx)}: {why} — {short(sent, 90)}")
                        break
                else:
                    if line.lstrip().startswith(">"):
                        continue      # block-quoted boilerplate (the wall notice itself)
                    body = re.sub(r"^[\s>*\-+\d.]+", "", sent)
                    if (len(body.split()) >= 6 and ASSERTION_VERB.search(body)
                            and not HEDGE.search(body)):
                        warns.append(f"{rep.loc(idx)}: unhedged declarative in the hypothesis "
                                     f"section — {short(sent, 90)}")
        if not any(re.search(r"\btest|falsif|predict", line, re.I) for _i, line in hyp_lines):
            warns.append("hypothesis section states no test / falsifier "
                         "(synthesis.md §7 rule 2)")

        # 2. evidence side
        for idx, line in rep.prose_spans(0, h_start):
            for _off, sent in sentences(line):
                for rx, why in EVIDENCE_FORBIDDEN:
                    if rx.search(sent):
                        fails.append(f"{rep.loc(idx)}: {why} in an evidence section — "
                                     f"{short(sent, 90)}")
                        break
                if SPECULATION_MARKER.search(sent):
                    warns.append(f"{rep.loc(idx)}: speculation marker outside the hypothesis "
                                 f"section — {short(sent, 90)}")

        if self.unsupported_claims:
            warns.append(f"{len(self.unsupported_claims)} unsupported_claims recorded "
                         f"(see C-CITE-RESOLVE / C-ATTRIBUTION)")

        if fails:
            status, parts = FAIL, fails[:6]
        elif warns:
            status, parts = WARN, warns[:6]
        else:
            status, parts = PASS, ["no banned phrasing found on either side of the wall "
                                   "(lexical heuristic only)"]
        self.add("C-HYPOTHESIS-WALL", status,
                 f"{len(fails)} forbidden phrasings, {len(warns)} suspicions; " + "; ".join(parts))

    # -- C-PRISMA ----------------------------------------------------------

    def _corpus_prisma(self) -> dict | None:
        script = HERE / "corpus.py"
        if not script.exists():
            return None
        try:
            proc = subprocess.run(
                [sys.executable, str(script), "prisma", "--run-dir", str(self.run.run_dir),
                 "--format", "json"],
                capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"_error": str(exc)}
        if proc.returncode != 0:
            return {"_error": short(proc.stderr or proc.stdout, 200)}
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return {"_error": f"corpus.py prisma emitted non-JSON ({exc})"}

    def check_prisma(self) -> None:
        rep = self.report
        p = self._corpus_prisma()
        if p is None:
            self.add("C-PRISMA", FAIL, "scripts/corpus.py not found; PRISMA numbers unverifiable")
            return
        if "_error" in p:
            self.add("C-PRISMA", FAIL, f"corpus.py prisma failed: {p['_error']}")
            return

        def expected(path: tuple[str, ...]) -> int | None:
            if path[0] == "_skip":
                return None
            if path[0] == "_source":
                return (p["identification"]["records_identified_by_source"] or {}).get(path[1], 0)
            node = p
            for seg in path:
                node = (node or {}).get(seg) if isinstance(node, dict) else None
            return node if isinstance(node, int) else None

        mismatches: list[str] = []
        matched = 0
        seen_keys: set[tuple[str, ...]] = set()
        for i, raw in enumerate(rep.lines):
            line = raw.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            label = re.sub(r"[*_`]", "", cells[0]).strip()
            label = re.sub(r"\([^)]*\)", " ", label)          # drop parenthetical qualifiers
            label = re.sub(r"^[—–\-\s]*(of which)?\s*", "", label, flags=re.I).strip()
            value_cell = re.sub(r"[*_`,]", "", cells[-1]).strip()
            if not re.fullmatch(r"\d+", value_cell):
                continue
            for rx, path in PRISMA_LABEL_MAP:
                if rx.search(label):
                    if path[0] == "_skip":
                        break
                    exp = expected(path)
                    if exp is None:
                        break
                    seen_keys.add(path)
                    matched += 1
                    if int(value_cell) != exp:
                        mismatches.append(f"{rep.loc(i)} '{label}' reports {value_cell}, "
                                          f"corpus.py prisma says {exp}")
                    break

        # reporting.md §1 arithmetic invariants, over the authoritative counters
        ident, scr, ret, inc = (p["identification"], p["screening"], p["retrieval"], p["included"])
        inv: list[str] = []
        if scr["excluded"] + scr["unclear"] + scr["included_after_screening"] != \
                scr["records_screened"]:
            inv.append("records_excluded + unclear + included != records_screened")
        if ret["fulltext_obtained"] != (ret["fulltext_sought"] - ret["fulltext_unobtainable"]
                                        - ret["retrieval_not_yet_attempted"]):
            inv.append("reports_assessed != reports_sought - not_retrieved - not_attempted")
        if inc["studies_included"] > ret["fulltext_obtained"]:
            inv.append("reports_included > reports_assessed")
        if ident["records_identified"] - ident["duplicates_removed"] != \
                ident["records_after_dedupe"]:
            inv.append("records_identified - duplicates != records_after_dedupe")
        if self.run.missing_md_present or ret["fulltext_unobtainable"]:
            if len(self.run.missing_md_ids) != ret["fulltext_unobtainable"]:
                inv.append(f"missing.md lists {len(self.run.missing_md_ids)} records, "
                           f"reports_not_retrieved = {ret['fulltext_unobtainable']}")

        status = PASS
        parts = [f"{matched} PRISMA numbers cross-checked against corpus.py prisma"]
        if matched == 0:
            status = FAIL
            parts.append("no PRISMA counts found in the report")
        if mismatches:
            status = FAIL
            parts.extend(mismatches[:5])
        if inv:
            status = FAIL
            parts.extend(inv[:5])
        self.add("C-PRISMA", status, "; ".join(parts))

    # -- C-PREPRINT --------------------------------------------------------

    def check_preprint(self) -> None:
        run, rep = self.run, self.report
        preprints = [r for r in run.records if r.get("is_preprint")]
        problems: list[str] = []
        for rec in preprints:
            eid = rec["evidence_id"]
            for line in self.refs_by_id.get(eid, []):
                _sentence, para = rep.context(line)
                if not PREPRINT_MARKER.search(para):
                    problems.append(f"{eid} cited at {rep.loc(line)} without a preprint tag")
            for key, (line, body) in rep.footnote_defs.items():
                if run.resolve(key) == eid and not PREPRINT_MARKER.search(body):
                    problems.append(f"{eid} footnote at {rep.loc(line)} carries no preprint tag")
        if not preprints:
            self.add("C-PREPRINT", PASS, "no preprint records in the corpus")
            return
        status = FAIL if problems else PASS
        self.add("C-PREPRINT", status,
                 f"{len(preprints)} preprint records"
                 + ("; " + "; ".join(problems[:6]) if problems
                    else "; tagged at every point of use"))

    # -- C-ATTRIBUTION -----------------------------------------------------

    def check_attribution(self) -> None:
        run, rep = self.run, self.report
        refs_span = rep.find_section(r"^\s*(#*\s*)?(references|bibliography)")
        refs_start = refs_span[0] if refs_span else len(rep.lines)
        body_refs = [(k, i) for k, i in rep.footnote_refs if i < refs_start]

        problems: list[str] = []
        if rep.footnote_defs and not body_refs:
            problems.append("body-only citation list: footnote definitions exist but no claim "
                            "carries a footnote reference")

        # per-claim attribution: a sentence reporting an effect estimate needs a footnote
        for idx, line in rep.prose_spans(0, refs_start):
            if "[^" in line:
                continue
            if not STATISTIC.search(line):
                continue
            start, end = rep.paragraph_at(idx)
            if any("[^" in rep.lines[j] for j in range(start, end + 1)):
                continue
            self.unsupported_claims.append({
                "location": rep.loc(idx),
                "claim": short(line),
                "reason": "reports an effect estimate with no footnote attribution in the "
                          "sentence or its paragraph",
            })
            problems.append(f"{rep.loc(idx)}: unattributed effect estimate")

        # PubMed MCP attribution: PubMed link + DOI where a DOI exists
        for key, (line, body) in rep.footnote_defs.items():
            eid = run.resolve(key)
            rec = run.by_id.get(eid or "")
            if not rec:
                continue
            pmid, doi = rec.get("pmid"), rec.get("doi")
            if pmid and str(pmid) not in body:
                problems.append(f"{rep.loc(line)}: footnote for {eid} carries no PMID/PubMed link")
            if doi and str(doi).lower() not in body.lower():
                problems.append(f"{rep.loc(line)}: footnote for {eid} carries no DOI")
        if not re.search(r"pubmed", rep.text, re.I):
            problems.append("the report never names PubMed (attribution requirement)")

        warns: list[str] = []
        if not re.search(r"e-?utilities|pubmed mcp|ncbi", rep.text, re.I):
            warns.append("methods does not name NCBI E-utilities / the PubMed MCP connector")

        if problems:
            status = FAIL
            parts = problems[:6]
        elif warns:
            status = WARN
            parts = warns
        else:
            status = PASS
            parts = [f"{len(body_refs)} per-claim footnote references; every PubMed footnote "
                     f"carries PubMed and DOI"]
        self.add("C-ATTRIBUTION", status, "; ".join(parts))

    # -- C-SECTIONS --------------------------------------------------------

    REQUIRED_SECTIONS: list[tuple[str, str, bool]] = [
        # (name, heading regex, required-or-warn)
        ("question / protocol", r"question|protocol", True),
        ("methods — search", r"search|quer(y|ies)|selection|methods", True),
        ("PRISMA flow / screening log", r"prisma|selection|screening|flow", True),
        ("characteristics of included studies", r"evidence table|characteristics|included studies",
         True),
        ("results by outcome", r"synthesis|results|outcome|findings", True),
        ("certainty of evidence", r"certainty|grade", True),
        ("conflicts and inconsistencies", r"conflict|inconsisten|agreement|disagree", True),
        ("evidence gaps", r"gap", True),
        ("new insights / hypotheses", r"new insight|hypothes", True),
        ("limitations / unobtainable evidence", r"limitation|missing|quarantin|unobtainable|"
                                                 r"uncertain", True),
        ("references", r"reference|bibliograph", True),
        ("plain-language summary", r"summary|short answer|plain[- ]language|bottom line", False),
        ("risk of bias / quality", r"risk of bias|\brob\b|quality|appraisal", False),
        ("provenance", r"provenance|generated by|run metadata", False),
    ]

    def check_sections(self) -> None:
        rep = self.report
        titles = [t for _i, _l, t in rep.headings]
        missing_req: list[str] = []
        missing_opt: list[str] = []
        empty: list[str] = []

        has_title_block = bool(rep.frontmatter) or any(l == 1 for _i, l, _t in rep.headings)
        if not has_title_block:
            missing_req.append("title block")

        for name, pattern, required in self.REQUIRED_SECTIONS:
            rx = re.compile(pattern, re.I)
            hit = next((t for t in titles if rx.search(t)), None)
            if hit is None:
                (missing_req if required else missing_opt).append(name)
                continue
            span = rep.find_section(pattern)
            if span:
                body = "\n".join(rep.lines[span[0] + 1:span[1]]).strip()
                body = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
                if not body:
                    empty.append(f"{name} (no body, no stated reason)")

        # plain-language summary may live as bolded prose rather than a heading
        if "plain-language summary" in missing_opt and re.search(
                r"\*\*(short answer|plain[- ]language summary|bottom line)", rep.text, re.I):
            missing_opt.remove("plain-language summary")

        status, parts = PASS, [f"{len(rep.headings)} headings; all required sections present"]
        if missing_req:
            status = FAIL
            parts = [f"{len(missing_req)} required sections missing: " + ", ".join(missing_req)]
        if missing_opt or empty:
            status = self.worst(status, WARN)
            if missing_opt:
                parts.append("optional sections absent: " + ", ".join(missing_opt))
            if empty:
                parts.append("empty sections: " + ", ".join(empty[:4]))
        self.add("C-SECTIONS", status, "; ".join(parts))

    # -- C-PROVISIONAL -----------------------------------------------------

    def check_provisional(self) -> None:
        run, rep = self.run, self.report
        triggers: list[str] = []
        quarantined = run.quarantined()
        if quarantined:
            triggers.append(f"{len(quarantined)} quarantined record(s)")
        if run.abstract_only():
            triggers.append(f"{len(run.abstract_only())} abstract_only record(s)")
        stalled = [t for t in run.tasks
                   if t.get("status") in ("failed", "blocked")
                   and t.get("stage") in ("screen", "adjudicate", "retrieve", "extract",
                                          "appraise", "synthesize")]
        if stalled:
            triggers.append(f"{len(stalled)} failed/blocked task(s) feeding synthesis")
        failed_checks = [c["check_id"] for c in self.checks if c["status"] == FAIL]
        if failed_checks:
            triggers.append(f"failed checks: {', '.join(failed_checks)}")

        if not triggers:
            self.add("C-PROVISIONAL", PASS,
                     "no provisional trigger fired; synthesis need not be marked provisional")
            return

        problems: list[str] = []
        if not re.search(r"provisional", rep.title_block(), re.I):
            problems.append("PROVISIONAL does not appear in the title block")
        elif not re.search(r"\bPROVISIONAL\b", rep.title_block()):
            problems.append("'provisional' appears in the title block but not in caps "
                            "(reporting.md §4)")
        body = rep.body_text()
        for rec in quarantined:
            eid = rec["evidence_id"]
            tokens = [eid] + [t for t in (rec.get("pmid"), rec.get("doi")) if t]
            if not any(str(t) in body for t in tokens):
                problems.append(f"quarantined {eid} is not listed in the report")

        status = FAIL if problems else PASS
        self.add("C-PROVISIONAL", status,
                 f"triggers: {'; '.join(triggers)}"
                 + ("; " + "; ".join(problems[:5]) if problems
                    else "; report is marked PROVISIONAL and lists the quarantined records"))

    # -- C-OKF -------------------------------------------------------------

    def check_okf(self) -> None:
        if self.wiki is None:
            self.okf_validation = "skipped"
            self.add("C-OKF", PASS, "skipped: no wiki promotion requested (--wiki not given)")
            return
        script = HERE / "okf.py"
        if not script.exists():
            self.okf_validation = "fail"
            self.add("C-OKF", FAIL, "scripts/okf.py not found; bundle unverifiable")
            return
        report_path = self.run.run_dir / "outputs" / "okf-validation.md"
        cmd = [sys.executable, str(script), "validate", "--wiki", str(self.wiki),
               "--run-dir", str(self.run.run_dir), "--report", str(report_path), "--json"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            self.okf_validation = "fail"
            self.add("C-OKF", FAIL, f"okf.py validate could not be run: {exc}")
            return
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.okf_validation = "fail"
            self.add("C-OKF", FAIL,
                     f"okf.py validate emitted non-JSON (exit {proc.returncode}): "
                     f"{short(proc.stderr or proc.stdout, 160)}")
            return
        self.okf_validation = data.get("okf_validation") or ("fail" if proc.returncode else "pass")
        violations = data.get("violations") or []
        if self.okf_validation == "fail":
            ids = ", ".join(sorted({v.get("rule", "?") for v in violations})[:8])
            self.add("C-OKF", FAIL,
                     f"{len(violations)} bundle violations ({ids}); promotion blocked, details in "
                     f"outputs/okf-validation.md")
        else:
            self.add("C-OKF", PASS,
                     f"bundle validates ({data.get('files_checked', 0)} files checked)")

    # ================================================== evidence kernel (Phase 3) ==
    #
    # Every check below delegates the actual cryptography and slicing to `store.py`
    # (D5). Nothing here recomputes a hash, re-slices a span or re-implements the
    # five-condition freshness rule.

    #: Extraction fields that make a factual claim about the study and therefore need a
    #: record-level span when non-null (R19).
    NARRATIVE_FIELDS = ("design", "n_total", "n_arms", "population", "intervention",
                        "comparator", "funding", "coi", "limitations")
    #: Numeric outcome fields; any non-null one makes the outcome entry a claim (R19).
    OUTCOME_CLAIM_FIELDS = ("effect", "ci_low", "ci_high", "p_value")

    #: Span-level failures that are *never* downgraded by the gate flag (R20).
    SPAN_TAMPER_CODES = ("SPAN_OUT_OF_RANGE", "SPAN_TOO_LONG", "EXCERPT_MISMATCH")
    #: Failures owned by C-SNAPSHOT; C-SPAN reports but does not re-fail on them.
    SNAPSHOT_CODES = ("UNKNOWN_SOURCE", "SNAPSHOT_HASH_MISMATCH")

    # -- plumbing ----------------------------------------------------------

    def gated(self, status_when_bad: str = WARN) -> str:
        """FAIL with the gate on, `status_when_bad` (default WARN) with it off (R20)."""
        return FAIL if self.gate else status_when_bad

    def _kernel_record_files(self) -> list[tuple[str, Path]]:
        """(kind, path) for every extraction / appraisal record on disk, deduplicated."""
        run_dir = self.run.run_dir
        out: list[tuple[str, Path]] = []
        seen: set[Path] = set()

        def put(kind: str, path: Path) -> None:
            try:
                key = path.resolve()
            except OSError:
                key = path
            if key in seen or not path.is_file():
                return
            seen.add(key)
            out.append((kind, path))

        for kind, sub in (("extraction", "extractions"), ("appraisal", "appraisals")):
            d = run_dir / "workspace" / sub
            if d.is_dir():
                for f in sorted(d.rglob("*.json")):
                    if not f.name.startswith("."):
                        put(kind, f)
        for rec in self.run.records:
            for field, kind in (("extraction_path", "extraction"),
                                ("appraisal_path", "appraisal")):
                rel = rec.get(field)
                if rel:
                    put(kind, run_dir / rel)
        return out

    @staticmethod
    def _spans_of(container) -> list[dict]:
        spans = (container or {}).get("spans")
        return [s for s in spans if isinstance(s, dict)] if isinstance(spans, list) else []

    def _collect_kernel_inputs(self) -> None:
        """Gather span records, span gaps (R16) and referenced source_ids from the run."""
        spans: list[dict] = []          # {artifact, kind, field, span, excerpt, path}
        gaps: list[dict] = []           # R16 'unverified' records/fields with no spans
        unreadable: list[str] = []
        referenced: list[str] = []

        def note_source(sid) -> None:
            if isinstance(sid, str) and sid and sid not in referenced:
                referenced.append(sid)

        def take(artifact: str, kind: str, field: str | None, span: dict,
                 excerpt: str | None, path: Path) -> None:
            spans.append({"artifact": artifact, "kind": kind, "field": field,
                          "span": span, "excerpt": excerpt,
                          "path": self._rel(path)})
            note_source(span.get("source_id"))

        for kind, path in self._kernel_record_files():
            try:
                rec = read_json(path)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
                unreadable.append(f"{self._rel(path)}: {exc}")
                continue
            if not isinstance(rec, dict):
                unreadable.append(f"{self._rel(path)}: not a JSON object")
                continue
            eid = rec.get("evidence_id") or rec.get("pmid") or path.stem
            artifact = f"{kind}:{eid}"

            record_spans = self._spans_of(rec)
            for span in record_spans:
                take(artifact, kind, "spans", span, None, path)
            for i, q in enumerate(rec.get("quotes") or []):
                # R17: an agent-written quote is an excerpt claim and is re-sliced, never trusted.
                if isinstance(q, dict) and isinstance(q.get("text"), str) and q.get("source_id"):
                    take(artifact, kind, f"quotes[{i}]",
                         {"source_id": q.get("source_id"), "start": q.get("start"),
                          "end": q.get("end")}, q["text"], path)

            if kind == "extraction":
                if not record_spans and any(rec.get(f) not in (None, [], "")
                                            for f in self.NARRATIVE_FIELDS):
                    gaps.append({"artifact": artifact, "field": "spans",
                                 "detail": "narrative factual fields with no record-level "
                                           "spans (R19)"})
                for i, oc in enumerate(rec.get("outcomes") or []):
                    if not isinstance(oc, dict):
                        continue
                    oc_spans = self._spans_of(oc)
                    for span in oc_spans:
                        take(artifact, kind, f"outcomes[{i}]", span, None, path)
                    if not oc_spans and any(oc.get(f) is not None
                                            for f in self.OUTCOME_CLAIM_FIELDS):
                        gaps.append({"artifact": artifact, "field": f"outcomes[{i}]",
                                     "detail": "effect estimate with no span (R19)"})
            else:
                for i, dom in enumerate(rec.get("domains") or []):
                    if not isinstance(dom, dict):
                        continue
                    dom_spans = self._spans_of(dom)
                    for span in dom_spans:
                        take(artifact, kind, f"domains[{i}]", span, None, path)
                    if not dom_spans and dom.get("judgement") not in (None, "unclear"):
                        gaps.append({"artifact": artifact, "field": f"domains[{i}]",
                                     "detail": f"judgement {dom.get('judgement')!r} with no "
                                               f"span (R19)"})

        for rec in self.run.records:                       # R14 corpus-registered snapshots
            for sid in rec.get("source_ids") or []:
                note_source(sid)

        on_disk = self.store.list_snapshots() if self.store is not None else []
        self.kernel = {
            "spans": spans,
            "gaps": gaps,
            "unreadable_records": unreadable,
            "referenced": referenced,
            "on_disk": on_disk,
            "sources_dir": (self.run.run_dir / "sources").is_dir(),
        }
        # A pre-kernel run: no snapshot store and nothing that points at one.
        self.kernel["pre_kernel"] = (not on_disk and not referenced and not spans)

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.run.run_dir))
        except ValueError:
            return str(path)

    def run_kernel_checks(self) -> None:
        """C-SNAPSHOT, C-SPAN, C-FRESH-FETCH, C-ASSEMBLER — always run, always reported."""
        if store_mod is None:
            for check_id in KERNEL_CHECKS:
                self.add(check_id, FAIL if self.gate else SKIPPED,
                         "skipped: scripts/store.py could not be imported, so the evidence "
                         "kernel cannot be checked" + self._gate_note(), skipped=not self.gate)
            self.kernel = {"spans": [], "gaps": [], "unreadable_records": [], "referenced": [],
                           "on_disk": [], "sources_dir": False, "pre_kernel": True,
                           "store_unavailable": True}
            return
        try:
            self.store = store_mod.Store(self.run.run_dir, wiki_root=self.wiki,
                                         repo_root=self.repo)
            self._collect_kernel_inputs()
        except Exception as exc:  # noqa: BLE001 - a broken store must not abort the other checks
            for check_id in KERNEL_CHECKS:
                self.add(check_id, self.gated(),
                         f"evidence-kernel inputs could not be gathered: "
                         f"{type(exc).__name__}: {exc}")
            self.kernel.setdefault("pre_kernel", False)
            return
        self.check_snapshot()
        self.check_span()
        self.check_fresh_fetch()
        self.check_assembler()

    def _gate_note(self) -> str:
        return "; gate on (--gate)" if self.gate else "; gate off (R20): reported, not blocking"

    # -- C-SNAPSHOT --------------------------------------------------------

    def check_snapshot(self) -> None:
        """Every referenced snapshot exists and rehashes. Tampering is never a warning."""
        k = self.kernel
        if k["pre_kernel"]:
            self.add("C-SNAPSHOT", FAIL if self.gate else SKIPPED,
                     "skipped: no snapshot store — this run has no sources/ directory and no "
                     "record references a source_id (pre-kernel run)" + self._gate_note(),
                     skipped=not self.gate)
            return

        ids = list(dict.fromkeys(list(k["referenced"]) + list(k["on_disk"])))
        bad: list[str] = []
        intact: set[str] = set()
        for sid in ids:
            res = self.store.verify_snapshot(sid)
            if res["ok"]:
                intact.add(sid)
            else:
                bad.append(f"{sid[:20]}…: {res['reason_code']} — {short(res['detail'], 120)}")
        k["snapshot_ok"] = intact
        ok = len(intact)

        parts = [f"{ok}/{len(ids)} snapshots verify (content_hash and source_id recomputed "
                 f"from url+text)"]
        if bad:
            # Never downgraded by the gate flag (R20 / schema §9).
            parts.append(f"{len(bad)} tampered or missing: " + "; ".join(bad[:4]))
            parts.append("hard fail regardless of the gate; OKF promotion blocked")
            self.add("C-SNAPSHOT", FAIL, "; ".join(parts), tamper=True)
            return
        if not ids:
            self.add("C-SNAPSHOT", FAIL if self.gate else SKIPPED,
                     "skipped: sources/ exists but holds no snapshots and nothing references "
                     "one" + self._gate_note(), skipped=not self.gate)
            return
        self.add("C-SNAPSHOT", PASS, "; ".join(parts))

    # -- C-SPAN ------------------------------------------------------------

    def check_span(self) -> None:
        """Range, the 2000-character cap, and excerpt equality against the re-slice."""
        k = self.kernel
        if k["pre_kernel"]:
            self.add("C-SPAN", FAIL if self.gate else SKIPPED,
                     "skipped: no claim spans and no snapshot store (pre-kernel run); legacy "
                     "records are unverified, not tampered (R16)" + self._gate_note(),
                     skipped=not self.gate)
            return

        hard: list[str] = []          # out of range / over-length / excerpt mismatch
        soft: list[str] = []          # gate-controlled (schema errors, access disagreement)
        blocked: list[str] = []       # unresolvable because C-SNAPSHOT already failed
        warns: list[str] = []
        ok = 0
        for entry in k["spans"]:
            res = self.store.verify_span(entry["span"], excerpt=entry["excerpt"])
            where = f"{entry['artifact']}.{entry['field']}"
            if res["ok"]:
                ok += 1
                for w in res["warnings"]:
                    warns.append(f"{where}: {short(w, 110)}")
                continue
            line = f"{where}: {res['reason_code']} — {short(res['detail'], 130)}"
            if res["reason_code"] in self.SPAN_TAMPER_CODES:
                hard.append(line)
            elif res["reason_code"] in self.SNAPSHOT_CODES:
                blocked.append(line)
            else:
                soft.append(line)

        k["spans_checked"] = len(k["spans"])
        k["spans_ok"] = ok
        parts = [f"{ok}/{len(k['spans'])} claim spans re-slice cleanly "
                 f"(0 <= start < end <= len(text), <= {store_mod.MAX_SPAN_CHARS} chars, "
                 f"excerpt == snapshot.text[start:end])"]
        status = PASS
        if hard:
            status = FAIL      # never downgraded by the gate (R20)
            parts.append(f"{len(hard)} span(s) fail hard regardless of the gate: "
                         + "; ".join(hard[:4]))
        if blocked:
            parts.append(f"{len(blocked)} span(s) unresolvable because their snapshot failed "
                         f"C-SNAPSHOT: " + "; ".join(blocked[:2]))
            status = self.worst(status, FAIL)
        if soft or k["unreadable_records"]:
            issues = soft + [f"unreadable record {u}" for u in k["unreadable_records"]]
            status = self.worst(status, self.gated())
            parts.append(f"{len(issues)} malformed span record(s): " + "; ".join(issues[:4]))
        if k["gaps"]:
            status = self.worst(status, self.gated())
            parts.append(f"{len(k['gaps'])} record/field(s) that should carry spans carry none "
                         f"— unverified, not tampered (R16): "
                         + "; ".join(f"{g['artifact']}.{g['field']}" for g in k["gaps"][:4]))
        if warns:
            status = self.worst(status, WARN)
            parts.append("; ".join(warns[:3]))
        if status != PASS:
            parts.append(self._gate_note().lstrip("; "))
        self.add("C-SPAN", status, "; ".join(parts))

    # -- C-FRESH-FETCH -----------------------------------------------------

    def check_fresh_fetch(self) -> None:
        """A 'supported' verdict needs a fresh retrieval for every cited source (R15)."""
        k = self.kernel
        if k["pre_kernel"]:
            self.add("C-FRESH-FETCH", FAIL if self.gate else SKIPPED,
                     "skipped: no events.jsonl / snapshot store to check freshness against "
                     "(pre-kernel run)" + self._gate_note(), skipped=not self.gate)
            return

        cited = [sid for sid in k["referenced"]]
        if not cited:
            cited = list(k["on_disk"])
        stale: list[str] = []
        fresh_ids: list[str] = []
        pdf_exception = 0
        for sid in cited:
            verdict = self.store.freshness(sid)
            if verdict["fresh"]:
                fresh_ids.append(sid)
                try:
                    if self.store.read_snapshot(sid)["origin"] == "user-supplied-pdf":
                        pdf_exception += 1
                except Exception:  # noqa: BLE001 - already reported by C-SNAPSHOT
                    pass
            else:
                stale.append(f"{sid[:20]}…: {verdict['reason_code']} — "
                             f"{short(verdict['detail'], 110)}")

        k["fresh_sources"] = len(fresh_ids)
        k["stale_sources"] = len(stale)
        parts = [f"{len(fresh_ids)}/{len(cited)} cited sources have a fresh retrieval logged in "
                 f"this run (R15)"]
        if pdf_exception:
            parts.append(f"{pdf_exception} via the user-supplied-pdf exception "
                         f"(local_pdf event + matching asset hash)")
        if not stale:
            self.add("C-FRESH-FETCH", PASS, "; ".join(parts))
            return
        if self.store.created_at is None:
            parts.append("config.json carries no created_at, so freshness fails closed (R15)")
        parts.append(f"{len(stale)} without one: " + "; ".join(stale[:4]))
        parts.append("affected claims are downgraded to unverified" if not self.gate
                     else "supported verdicts blocked")
        parts.append(self._gate_note().lstrip("; "))
        self.add("C-FRESH-FETCH", self.gated(), "; ".join(parts))

    # -- C-ASSEMBLER -------------------------------------------------------

    def check_assembler(self) -> None:
        """Reads outputs/result.json (schema §13); absent is skipped/warn with the gate off."""
        path = self.run.run_dir / "outputs" / "result.json"
        if not path.exists():
            self.add("C-ASSEMBLER", FAIL if self.gate else SKIPPED,
                     "skipped: outputs/result.json absent — scripts/assemble.py has not run "
                     "for this run (R20/R24)" + self._gate_note(), skipped=not self.gate)
            return
        try:
            data = read_json(path)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            self.add("C-ASSEMBLER", self.gated(),
                     f"outputs/result.json is unreadable: {exc}{self._gate_note()}")
            return
        if not isinstance(data, dict):
            self.add("C-ASSEMBLER", self.gated(),
                     "outputs/result.json is not a JSON object (schema §13)" + self._gate_note())
            return

        missing = [f for f in ("schema_version", "run_slug", "generated_at", "gate", "counts",
                               "accepted", "diagnostics", "sources") if f not in data]
        diagnostics = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else {}
        unresolved = diagnostics.get("unresolved") or []
        accepted = data.get("accepted") or []
        counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
        self.kernel["assembler"] = {
            "accepted": len(accepted) if isinstance(accepted, list) else None,
            "unresolved": len(unresolved) if isinstance(unresolved, list) else None,
            "gate": data.get("gate"),
        }
        parts = [f"result.json: {len(accepted) if isinstance(accepted, list) else '?'} accepted, "
                 f"{len(unresolved) if isinstance(unresolved, list) else '?'} unresolved, "
                 f"{counts.get('spans_checked', '?')} spans checked by the assembler"]
        status = PASS
        if missing:
            status = self.gated()
            parts.append("result.json is not schema-§13 shaped, missing: " + ", ".join(missing))
        if unresolved:
            status = self.worst(status, self.gated())
            reasons = diagnostics.get("counts_by_reason") or {}
            if isinstance(unresolved, list):
                shown = [f"{u.get('artifact_id')}({u.get('reason_code')})"
                         for u in unresolved[:4] if isinstance(u, dict)]
                parts.append(f"{len(unresolved)} artifact(s) unresolved: " + ", ".join(shown))
            if reasons:
                parts.append("by reason: " + ", ".join(f"{k}={v}" for k, v in
                                                       sorted(reasons.items())))
        if status != PASS:
            parts.append(self._gate_note().lstrip("; "))
        self.add("C-ASSEMBLER", status, "; ".join(parts))

    # -- summary -----------------------------------------------------------

    def kernel_summary(self) -> dict:
        k = self.kernel or {}
        by_id = {c["check_id"]: c["status"] for c in self.checks}
        return {
            "gate": {"enabled": self.gate, "source": self.gate_source, "flag": "--gate"},
            "pre_kernel": bool(k.get("pre_kernel", True)),
            "snapshots": len(k.get("on_disk") or []),
            "sources_referenced": len(k.get("referenced") or []),
            "spans_checked": k.get("spans_checked", len(k.get("spans") or [])),
            "spans_ok": k.get("spans_ok", 0),
            "span_gaps": len(k.get("gaps") or []),
            "fresh_sources": k.get("fresh_sources"),
            "stale_sources": k.get("stale_sources"),
            "assembler": k.get("assembler"),
            "checks": {cid: by_id.get(cid) for cid in KERNEL_CHECKS},
        }


# --------------------------------------------------------------------------- output


def markdown_summary(result: dict, run_dir: Path, report_path: Path) -> str:
    kernel = result.get("evidence_kernel") or {}
    gate = kernel.get("gate") or {}
    lines = ["# Verification report", "",
             f"- Run: `{run_dir}`",
             f"- Report: `{report_path}`",
             f"- OKF validation: **{result['okf_validation']}**"]
    if gate:
        lines.append(f"- Evidence-kernel gate: **{'on' if gate.get('enabled') else 'off'}** "
                     f"({gate.get('source', 'default')})")
    lines += ["", "| Check | Status | Detail |", "|---|---|---|"]
    for c in result["checks"]:
        detail = c["detail"].replace("|", "\\|")
        lines.append(f"| `{c['check_id']}` | {c['status'].upper()} | {detail} |")
    if result["unsupported_claims"]:
        lines += ["", "## Unsupported claims", ""]
        for u in result["unsupported_claims"]:
            lines.append(f"- `{u['location']}` — {u['reason']}: {u['claim']}")
    if result["uncited_citations"]:
        lines += ["", "## Included but never cited", ""]
        lines += [f"- `{e}`" for e in result["uncited_citations"]]
    if result["missing_fulltext"]:
        lines += ["", "## Quarantined (no full text)", ""]
        lines += [f"- `{e}`" for e in result["missing_fulltext"]]
    unlabelled = [a for a in result["abstract_only_claims"] if not a["labelled"]]
    if unlabelled:
        lines += ["", "## Unlabelled abstract-only claims", ""]
        lines += [f"- `{a['location']}` — {a['evidence_id']}" for a in unlabelled]
    return "\n".join(lines) + "\n"


def resolve_gate(run_dir: Path, flag: bool | None) -> tuple[bool, str]:
    """(enabled, source) for the evidence-kernel gate.

    `--gate` / `--no-gate` win; otherwise `config.json` `gates.evidence_kernel`, then a
    top-level `gate`, then the R20 default: **off**.
    """
    if flag is not None:
        return bool(flag), "--gate" if flag else "--no-gate"
    path = run_dir / "config.json"
    try:
        cfg = read_json(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False, "default"
    if isinstance(cfg, dict):
        gates = cfg.get("gates")
        if isinstance(gates, dict) and isinstance(gates.get("evidence_kernel"), bool):
            return gates["evidence_kernel"], "config.json gates.evidence_kernel"
        if isinstance(cfg.get("gate"), bool):
            return cfg["gate"], "config.json gate"
    return False, "default"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- cli


def cmd_run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser()
    if not run_dir.is_dir():
        raise FatalError(f"run directory not found: {run_dir}")
    report_path = (Path(args.report).expanduser() if args.report
                   else run_dir / "outputs" / "report.md")
    if not report_path.exists():
        raise FatalError(f"report not found: {report_path}")

    data = RunData(run_dir)
    report = Report(report_path)
    wiki = Path(args.wiki).expanduser() if args.wiki else None
    repo = Path(args.repo).expanduser() if args.repo else repo_root_for_run(run_dir)
    gate, gate_source = resolve_gate(run_dir, args.gate)
    verifier = Verifier(data, report, wiki, repo=repo, gate=gate, gate_source=gate_source)
    result = verifier.run_all()

    out_path = run_dir / "outputs" / "verification.json"
    atomic_write(out_path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    if args.markdown:
        atomic_write(Path(args.markdown).expanduser(),
                     markdown_summary(result, run_dir, report_path))

    failed = [c for c in result["checks"] if c["status"] == FAIL]
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for c in result["checks"]:
            print(f"{c['status'].upper():5} {c['check_id']:20} {c['detail']}")
        print()
        print(f"verification -> {out_path}")
        print(f"{len(failed)} fail, "
              f"{sum(1 for c in result['checks'] if c['status'] == WARN)} warn, "
              f"{sum(1 for c in result['checks'] if c['status'] == SKIPPED)} skipped, "
              f"{sum(1 for c in result['checks'] if c['status'] == PASS)} pass; "
              f"okf_validation={result['okf_validation']}; "
              f"evidence-kernel gate={'on' if gate else 'off'} ({gate_source})")
        if failed:
            print("FAILED — report.md is preserved and marked provisional; "
                  "OKF promotion is blocked.")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verify.py",
        description=("Final consistency pass over a completed deep-research run. Writes "
                     "outputs/verification.json (schema.md §9). Never modifies report.md."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit 0 = no failures (warns allowed), 1 = a check failed, 2 = fatal error")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run every check over a run directory")
    r.add_argument("--run-dir", required=True,
                   help="run directory <wiki>/outputs/deep-research/<slug>/")
    r.add_argument("--wiki", help="wiki root; enables C-OKF via okf.py validate")
    r.add_argument("--repo", help="standalone repo root for global source fallback "
                        "(default: inferred from --run-dir under <repo>/runs/<slug>)")
    r.add_argument("--report", help="report path (default: <run-dir>/outputs/report.md)")
    r.add_argument("--json", action="store_true", help="print the verification JSON to stdout")
    r.add_argument("--markdown", help="also write a human-readable summary here")
    r.add_argument("--gate", dest="gate", action="store_true", default=None,
                   help="enforce the evidence-kernel checks (C-SNAPSHOT, C-SPAN, "
                        "C-FRESH-FETCH, C-ASSEMBLER) as hard failures blocking stage 8. "
                        "Default off (R20); config.json gates.evidence_kernel also sets it. "
                        "Snapshot/span tamper failures are hard failures either way.")
    r.add_argument("--no-gate", dest="gate", action="store_false",
                   help="force the gate off even if config.json enables it")
    r.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FatalError as exc:
        print(f"verify.py: fatal: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("verify.py: interrupted", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a crash must be exit 2, not a traceback-only exit 1
        print(f"verify.py: fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
