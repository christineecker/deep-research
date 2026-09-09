#!/usr/bin/env python3
"""fulltext.py — the full-text acquisition ladder (`SKILL.md` stage 4), resumable.

Rungs (recorded as `fulltext.source_tier` + `fulltext.access_route`, references/schema.md §4):

    0  local library        library.py lookup by DOI / PMID / fuzzy title
    1  PMC full text        PubMed MCP `get_full_text_article`  -- COORDINATOR HANDOFF
    2  PMC PDF              https://pmc.ncbi.nlm.nih.gov/articles/<PMCID>/pdf/
    3  Europe PMC           id conversion -> PMCID/PPRID -> fullTextXML
    4  Unpaywall            DOI -> best_oa_location (email= is a required API param)
    5  OA PDF/HTML          fetch the rung 3-4 location; pdftotext -layout / HTML detector
    6  preprint twin        Europe PMC SRC:PPR match, tagged is_preprint, flagged loudly
    7  browser search/fetch claude-in-chrome MCP -- COORDINATOR HANDOFF, OA content only
    8  quarantine           append to <run-dir>/missing.md and alert the user

Rung 1 handoff: a script cannot call an MCP tool. When a record reaches rung 1 the script
writes a `needs_mcp` task record to <run-dir>/workspace/retrieve/mcp-tasks.jsonl and keeps
walking the ladder. The coordinator calls `get_full_text_article`, saves the returned text to
the task's `result_path`, and calls `fulltext.py resolve-mcp` (or simply re-runs `acquire`,
which picks the file up). See references/acquisition.md.

Rung 7 handoff: same shape, a different MCP surface. When a record still has no text after
rung 6 the script writes a `needs_browser` task to
<run-dir>/workspace/retrieve/browser-tasks.jsonl and keeps walking. The coordinator drives the
claude-in-chrome MCP tools to search for and open a freely-accessible copy (publisher OA page,
institutional repository, preprint mirror not already covered by rung 6), extracts the visible
article text, saves it to the task's `result_path`, and calls `fulltext.py resolve-browser` (or
re-runs `acquire`). This rung exists to reach content a plain HTTP fetch cannot render (JS-gated
OA pages, bot-blocked hosts) -- it is not a paywall bypass. See references/acquisition.md §4b
and the "Forbidden" table below for the hard line between the two.

Evidence kernel: whenever a rung yields usable text the ladder **registers** it into the run's
snapshot store (`scripts/store.py`, references/schema.md §10-§11, `references/evidence-kernel.md`). The resulting `source_id` is appended to the corpus record's `source_ids[]` (R14), so a
claim can trace `evidence_id -> source_id -> start:end`. Registration writes a `register` event,
which is never fresh (R22); a rung that performed a genuine live round-trip in this run
additionally writes a `fetch` event with `fresh: true`. Registration is purely additive and
best-effort: a store failure is logged to `engine.log` and never aborts an acquisition that
already succeeded, so a run with no `sources/` directory acquires text exactly as before.

Forbidden by policy (`SKILL.md` "Invariants"), rung 7 included: no credentials, no logged-in
session, no institutional/VPN/EZproxy/Shibboleth proxy, no cookie jar reuse, no captcha
solving, no sci-hub-class sources, no retrying a 401/402/403 by an alternate route. A browser
rung that hits a login wall, a paywall, or a captcha reports `unavailable` and moves on to
quarantine -- it never signs in, never pays, never guesses past the block. Fail closed.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library  # noqa: E402  (sibling module, stdlib-only)
from library import (  # noqa: E402
    MIN_TEXT_CHARS,
    Library,
    entry_is_preprint,
    evidence_id_of,
    normalize_doi,
    pdf_text_with_ocr,
    read_corpus,
    record_stem,
    sha256_file,
    utcnow,
    wiki_root_for_run,
    write_corpus,
)

try:  # the evidence kernel is additive: acquisition must work without it
    import store  # noqa: E402  (sibling module, stdlib-only)
except Exception:  # pragma: no cover - store.py is a sibling and always present
    store = None

try:
    import requests
except ImportError:  # pragma: no cover - requests is verified present in `SKILL.md` "Scripts"
    requests = None

SCHEMA_VERSION = 1
VERSION = "deep-research/0.1"

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL = "https://api.unpaywall.org/v2"
PMC_PDF = "https://pmc.ncbi.nlm.nih.gov/articles/%s/pdf/"

# --- truncation detector (`references/acquisition.md` rung 5) ------------------------------------
TRUNCATION_MIN_WORDS = 1500
PAYWALL_MARKERS = [
    "Access options",
    "Purchase",
    "Sign in to view",
    "Get access",
    "Subscribe",
]

TIER_ROUTES = {
    0: "library",
    1: "pmc_mcp",
    2: "pmc_pdf",
    3: "epmc_xml",
    4: "unpaywall",
    5: "oa_pdf",
    6: "preprint_twin",
    7: "browser_fetch",
    8: "quarantine",
}

# polite defaults; NCBI hosts get the 3 req/s E-utilities budget (no API key in env)
DEFAULT_INTERVAL = 0.34
HOST_INTERVAL = {
    "pmc.ncbi.nlm.nih.gov": 0.34,
    "eutils.ncbi.nlm.nih.gov": 0.34,
    "www.ebi.ac.uk": 0.25,
    "api.unpaywall.org": 0.15,
}


# ------------------------------------------------------------------ http -------


class Http:
    """Polite HTTP: identifying User-Agent, per-host rate limit, bounded retries."""

    def __init__(self, email: str | None, offline: bool = False, timeout: int = 60):
        self.email = email
        self.offline = offline
        self.timeout = timeout
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        ua = "%s (+https://pubmed.ncbi.nlm.nih.gov/; literature-review agent" % VERSION
        ua += "; mailto:%s)" % email if email else ")"
        self.headers = {"User-Agent": ua, "Accept": "*/*"}
        self.session = requests.Session() if requests else None
        if self.session:
            self.session.headers.update(self.headers)

    def _host_lock(self, host: str) -> "threading.Lock":
        with self._locks_guard:
            lock = self._locks.get(host)
            if lock is None:
                lock = self._locks[host] = threading.Lock()
            return lock

    def _wait(self, url: str) -> None:
        """Sleep until this host's politeness interval has elapsed, then stamp it.

        Per **host**, not global: with `acquire --workers` two threads hitting
        `pmc.ncbi.nlm.nih.gov` still serialise against each other at that host's interval,
        while a third thread hitting `api.unpaywall.org` proceeds immediately. Holding the
        host lock across the sleep is what makes the spacing a real floor rather than a
        suggestion — without it, N threads all read the same `_last` and all wake together.
        """
        host = urlsplit(url).netloc
        interval = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
        with self._host_lock(host):
            last = self._last.get(host)
            if last is not None:
                delta = time.monotonic() - last
                if delta < interval:
                    time.sleep(interval - delta)
            self._last[host] = time.monotonic()

    def get(self, url: str, *, params: dict | None = None, stream: bool = False,
            retries: int = 2):
        if self.offline:
            raise OfflineError("offline mode: refused %s" % url)
        if self.session is None:
            raise RuntimeError("requests is unavailable")
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            self._wait(url)
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout,
                                        stream=stream, allow_redirects=True)
            except Exception as exc:  # network-level
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                last_err = HttpError("HTTP %d" % resp.status_code)
                continue
            return resp
        raise last_err or HttpError("request failed: %s" % url)


class OfflineError(RuntimeError):
    pass


class HttpError(RuntimeError):
    pass


# ------------------------------------------------------------------- parsing ---


class _TextHTML(HTMLParser):
    """stdlib HTML -> text. lxml/bs4 are NOT installed (`SKILL.md` "Scripts")."""

    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "header", "aside",
            "form", "button"}
    BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


def html_to_text(html: str) -> str:
    parser = _TextHTML()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.text()


def detect_truncation(html: str, text: str) -> tuple[bool, list[str]]:
    """Return (truncated, reasons). `references/acquisition.md` rung 5 / schema.md §4."""
    reasons: list[str] = []
    words = len(text.split())
    if words < TRUNCATION_MIN_WORDS:
        reasons.append("body_words=%d<%d" % (words, TRUNCATION_MIN_WORDS))
    haystack = (text + "\n" + html).casefold()
    for marker in PAYWALL_MARKERS:
        if marker.casefold() in haystack:
            reasons.append("paywall_marker=%s" % marker)
    return (bool(reasons), reasons)


def jats_to_text(xml_text: str) -> str:
    """Europe PMC fullTextXML (JATS) -> plain text via stdlib ElementTree."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    drop = {"ref-list", "back", "journal-meta", "front-stub"}
    out: list[str] = []

    def walk(node, skipping=False):
        tag = node.tag.split("}")[-1]
        if tag in drop:
            return
        block = tag in {"title", "p", "sec", "abstract", "caption", "td", "th", "label",
                        "article-title"}
        if node.text and node.text.strip() and not skipping:
            out.append(node.text.strip())
        for child in node:
            walk(child, skipping)
        if block:
            out.append("\n")
        if node.tail and node.tail.strip():
            out.append(node.tail.strip())

    walk(root)
    text = " ".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


# --------------------------------------------------------------------- state ---


def state_path(run_dir: Path, rec: dict) -> Path:
    return run_dir / "workspace" / "retrieve" / ("%s.json" % record_stem(rec))


def load_state(run_dir: Path, rec: dict) -> dict:
    path = state_path(run_dir, rec)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": evidence_id_of(rec),
        "pmid": rec.get("pmid"),
        "doi": normalize_doi(rec.get("doi")),
        "pmcid": rec.get("pmcid"),
        "rungs": {},
        "oa_location": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "fulltext": None,
    }


def save_state(run_dir: Path, rec: dict, state: dict) -> None:
    state["updated_at"] = utcnow()
    path = state_path(run_dir, rec)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def mark(state: dict, tier: int, status: str, detail: str = "", **extra) -> None:
    entry = {"status": status, "detail": detail, "at": utcnow()}
    entry.update(extra)
    state["rungs"][str(tier)] = entry


#: Serialises `engine.log` appends so pooled workers cannot interleave a partial line.
_LOG_LOCK = threading.Lock()


def log(run_dir: Path, msg: str) -> None:
    path = run_dir / "engine.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_LOCK:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s fulltext.py %s\n" % (utcnow(), msg))


# ----------------------------------------------------------------- mcp tasks ---


class HandoffQueue:
    """Coordinator-handoff task queue: a JSONL file of task records under `run_dir`.

    Shared mechanics for the rung-1 (MCP) and rung-7 (browser) handoff queues, which were
    previously two copy-pasted implementations differing only in `relpath` and
    `pending_status`.
    """

    def __init__(self, relpath: str, pending_status: str) -> None:
        self.relpath = relpath
        self.pending_status = pending_status

    def path(self, run_dir: Path) -> Path:
        return run_dir / self.relpath

    def read(self, run_dir: Path) -> list[dict]:
        path = self.path(run_dir)
        if not path.exists():
            return []
        tasks = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    tasks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return tasks

    def upsert(self, run_dir: Path, task: dict) -> None:
        tasks = [t for t in self.read(run_dir) if t.get("task_id") != task["task_id"]]
        tasks.append(task)
        path = self.path(run_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for t in tasks:
                fh.write(json.dumps(t, ensure_ascii=False, separators=(",", ":")) + "\n")
        tmp.replace(path)

    def pending(self, run_dir: Path, records: list[dict]) -> list[dict]:
        """Tasks of this queue's status still requiring coordinator action.

        A later rung can acquire full text after this queue's rung emitted its task; those
        tasks are stale and must not keep surfacing as blockers.
        """
        fulltext_by_eid = {
            evidence_id_of(rec): (rec.get("fulltext") or {}).get("status")
            for rec in records
        }
        pending = []
        for task in self.read(run_dir):
            if task.get("status") != self.pending_status:
                continue
            if (run_dir / task["result_path"]).exists():
                continue
            if fulltext_by_eid.get(task.get("evidence_id")) == "fulltext":
                continue
            pending.append(task)
        return pending


MCP_TASK_QUEUE = HandoffQueue("workspace/retrieve/mcp-tasks.jsonl", "needs_mcp")
BROWSER_TASK_QUEUE = HandoffQueue("workspace/retrieve/browser-tasks.jsonl", "needs_browser")


def mcp_tasks_path(run_dir: Path) -> Path:
    return MCP_TASK_QUEUE.path(run_dir)


def read_mcp_tasks(run_dir: Path) -> list[dict]:
    return MCP_TASK_QUEUE.read(run_dir)


def upsert_mcp_task(run_dir: Path, task: dict) -> None:
    MCP_TASK_QUEUE.upsert(run_dir, task)


def pending_mcp_tasks(run_dir: Path, records: list[dict]) -> list[dict]:
    """Rung-1 tasks still requiring coordinator action. See `HandoffQueue.pending`."""
    return MCP_TASK_QUEUE.pending(run_dir, records)


def browser_tasks_path(run_dir: Path) -> Path:
    return BROWSER_TASK_QUEUE.path(run_dir)


def read_browser_tasks(run_dir: Path) -> list[dict]:
    return BROWSER_TASK_QUEUE.read(run_dir)


def upsert_browser_task(run_dir: Path, task: dict) -> None:
    BROWSER_TASK_QUEUE.upsert(run_dir, task)


def pending_browser_tasks(run_dir: Path, records: list[dict]) -> list[dict]:
    """Rung-7 tasks still requiring coordinator browser action (mirrors `pending_mcp_tasks`)."""
    return BROWSER_TASK_QUEUE.pending(run_dir, records)


TRUNCATION_MIN_WORDS_TEXT = TRUNCATION_MIN_WORDS


def detect_truncation_text(text: str) -> tuple[bool, list[str]]:
    """Same detector as rung 5 (`detect_truncation`), applied to already-plain text.

    The browser rung hands back extracted body text, not raw HTML, so there is nothing to
    tag-strip -- the two checks (word count, paywall marker substring) apply directly.
    """
    reasons = []
    words = len(text.split())
    if words < TRUNCATION_MIN_WORDS_TEXT:
        reasons.append("body_words=%d<%d" % (words, TRUNCATION_MIN_WORDS_TEXT))
    lowered = text.lower()
    for marker in PAYWALL_MARKERS:
        if marker.lower() in lowered:
            reasons.append("paywall_marker=%s" % marker)
    return bool(reasons), reasons


# ----------------------------------------------------------- kernel mapping ----
#
# Rung -> snapshot (`access`, `origin`), schema.md §10 enums. `access` describes the
# snapshot, `fulltext.status` describes the corpus record; R21 maps between them
# (`full_text` -> `fulltext`; `abstract`/`web` -> `abstract_only`).
#
#   rung  route            access                       origin
#   ----  ---------------  ---------------------------  -----------------------------
#   0     library          full_text (preprint when the  from the library entry's own
#                          index entry or the corpus     access_route, else
#                          record says so)               user-supplied-pdf
#   1     pmc_mcp          full_text                    pmc
#   2     pmc_pdf          full_text                    pmc
#   3     epmc_xml         full_text / preprint         europepmc
#   4     unpaywall        -- no text, nothing registered --
#   5     unpaywall_pdf    full_text                    unpaywall
#   5     oa_pdf           full_text                    oa-pdf
#   5     oa_html          abstract when the truncation detector fired, else full_text
#                                                       unpaywall / oa-pdf
#   6     preprint_twin    preprint                     europepmc
#   7     browser_fetch    abstract when the truncation  web
#                          detector fires, else full_text
#   8     quarantine       -- no text, nothing registered --
#
ROUTE_ORIGIN = {
    "pmc_mcp": "pmc",
    "pmc_pdf": "pmc",
    "epmc_xml": "europepmc",
    "unpaywall": "unpaywall",
    "unpaywall_pdf": "unpaywall",
    "oa_pdf": "oa-pdf",
    "oa_html": "oa-pdf",
    "preprint_twin": "europepmc",
    "browser_fetch": "web",
    "browser_fetch_institutional": "web",
    "inbox_manual": "user-supplied-pdf",
    "library": "user-supplied-pdf",
}


def snapshot_access(res: dict, truncated: bool) -> str:
    """`access` for a rung result (schema.md §10 enum, R21).

    A truncation-detected HTML body is `abstract`, never `full_text` — the detector's
    verdict carries into the snapshot exactly as it carries into `fulltext.status`.
    """
    if truncated:
        return "abstract"
    if res.get("is_preprint"):
        return "preprint"
    return "full_text"


def snapshot_origin(res: dict, route: str | None) -> str:
    """`origin` for a rung result (schema.md §10 enum).

    A rung-0 library hit inherits the origin recorded on the library entry when the PDF
    was filed; a PDF with no recorded provenance is a local file the user supplied. A rung
    may state its own `origin` when the route token is ambiguous (an `oa_html` body reached
    through an Unpaywall location is `unpaywall`, one reached through Europe PMC is not).
    """
    stated = res.get("origin")
    if stated in (store.ORIGIN_VALUES if store else ()):
        return stated
    if route == "library":
        entry = res.get("library_entry") or {}
        return ROUTE_ORIGIN.get(entry.get("access_route") or "", "user-supplied-pdf")
    return ROUTE_ORIGIN.get(route or "", "oa-pdf")


def canonical_url(rec: dict, state: dict) -> str | None:
    """Stable identifier URL for a snapshot whose rung recorded no retrieval URL."""
    pmcid = rec.get("pmcid") or state.get("pmcid")
    if pmcid:
        return "https://pmc.ncbi.nlm.nih.gov/articles/%s/" % pmcid
    if rec.get("pmid"):
        return "https://pubmed.ncbi.nlm.nih.gov/%s/" % rec["pmid"]
    doi = normalize_doi(rec.get("doi"))
    if doi:
        return "https://doi.org/%s" % doi
    return None


def register_acquisition(rec: dict, ctx: Ctx, state: dict, tier: int, res: dict,
                         fulltext: dict, asset: dict | None) -> str | None:
    """Fold acquired text into the run's snapshot store. Returns the `source_id` or None.

    Best-effort by contract (the evidence kernel is additive): every
    failure is logged to `engine.log` and swallowed, because the acquisition itself has
    already succeeded and must not be lost to a kernel problem. `<run>/sources/` is
    created lazily here, on the first registration of the run.

    Writes a `register` event, never a fresh one (R22). A rung that performed a real
    network round-trip in *this* invocation (`res["live"]`) additionally gets a `fetch`
    event with `fresh: true`; nothing else does, so a coordinator-supplied MCP file, a
    library cache hit and a resumed run never claim freshness they do not have.
    """
    if store is None or not getattr(ctx, "register", True):
        return None
    text = res.get("text") or ""
    if not text.strip():
        return None
    eid = evidence_id_of(rec)
    route = fulltext.get("access_route") or TIER_ROUTES.get(tier)
    access = snapshot_access(res, bool(fulltext.get("truncation_detected")))
    origin = snapshot_origin(res, route)
    url = res.get("url")
    if not url and asset:
        url = "file:///" + asset["path"]
    if not url:
        url = canonical_url(rec, state)
    if not url:
        log(ctx.run_dir, "%s rung %d: no URL to register a snapshot against" % (eid, tier))
        return None
    paper = {
        "pmid": rec.get("pmid") or None,
        "doi": normalize_doi(rec.get("doi")),
        "pmcid": rec.get("pmcid") or state.get("pmcid") or None,
    }
    try:
        snap = store.register_text(
            ctx.run_dir, url=url, text=text, title=rec.get("title"), access=access,
            origin=origin, paper=paper, asset=asset, actor="main",
            detail="fulltext.py rung %d (%s): %d chars, access=%s, not re-retrieved"
                   % (tier, route, len(text), access),
        )
        source_id = snap["source_id"]
        if res.get("live"):
            store.append_event(ctx.run_dir, {
                "type": "fetch",
                "source_id": source_id,
                "url": url,
                "fresh": True,
                "sha256": store.sha256_text(text),
                "actor": "main",
                "detail": "live retrieval in this run: rung %d (%s), %d chars"
                          % (tier, route, len(text)),
            })
    except Exception as exc:  # a kernel problem must never lose a successful acquisition
        log(ctx.run_dir, "%s rung %d: snapshot registration failed: %r" % (eid, tier, exc))
        return None
    ids = rec.setdefault("source_ids", [])
    if source_id not in ids:
        ids.append(source_id)
    state["source_ids"] = list(ids)
    log(ctx.run_dir, "%s rung %d registered %s (access=%s, origin=%s%s)"
        % (eid, tier, source_id, access, origin, ", fresh fetch" if res.get("live") else ""))
    return source_id


# ------------------------------------------------------------------- context ---


class Ctx:
    def __init__(self, run_dir: Path, wiki_root: Path, email: str | None, offline: bool,
                 allow_ocr: bool = True, register: bool = True, max_ocr_pages: int = 30):
        self.run_dir = run_dir
        self.wiki_root = wiki_root
        self.email = email
        self.offline = offline
        self.allow_ocr = allow_ocr
        self.register = register
        self.max_ocr_pages = max_ocr_pages
        self.http = Http(email, offline=offline)
        self.lib = Library(wiki_root)

    def text_path(self, rec: dict) -> Path:
        d = self.run_dir / "workspace" / "fulltext"
        d.mkdir(parents=True, exist_ok=True)
        return d / ("%s.txt" % record_stem(rec))

    def tmp_path(self, rec: dict, suffix: str) -> Path:
        d = self.run_dir / "workspace" / "retrieve" / "tmp"
        d.mkdir(parents=True, exist_ok=True)
        return d / ("%s%s" % (record_stem(rec), suffix))

    def wiki_rel(self, path: Path) -> str | None:
        try:
            return str(Path(path).resolve().relative_to(self.wiki_root))
        except ValueError:
            return None


# --------------------------------------------------------------------- rungs ---
# Each rung returns a dict:
#   {"status": "success|failed|skipped|needs_mcp", "detail": str,
#    optional: "text", "pdf", "access_route", "truncation_detected", "reasons"}


def rung0_library(rec: dict, ctx: Ctx, state: dict) -> dict:
    entry, kind, score = ctx.lib.lookup(
        doi=rec.get("doi"), pmid=rec.get("pmid"), pmcid=rec.get("pmcid"), title=rec.get("title")
    )
    if entry is None:
        return {"status": "failed", "detail": "no library match"}
    pdf = ctx.lib.entry_path(entry)
    if not pdf.exists():
        return {"status": "failed", "detail": "index entry points at a missing file: %s"
                % entry.get("path")}
    text, used_ocr = pdf_text_with_ocr(pdf, ctx.max_ocr_pages) if ctx.allow_ocr \
        else (library.pdftotext(pdf), False)
    if len(text.strip()) < MIN_TEXT_CHARS:
        return {"status": "failed", "detail": "library PDF yielded <%d chars" % MIN_TEXT_CHARS}
    return {
        "status": "success",
        "detail": "library match by %s (score %.3f)%s" % (kind, score,
                                                          "; OCR used" if used_ocr else ""),
        "text": text,
        "pdf": pdf,
        "access_route": "library",
        "library_entry": entry,
        # Preprint precedence: an explicit true from *either* source wins, the index
        # entry first, then the corpus record; anything else (a false, a missing key on
        # a pre-`is_preprint` entry) is false. `snapshot_access` turns a true into
        # `access: "preprint"`, so a cached preprint never registers as `full_text`.
        "is_preprint": entry_is_preprint(entry) or bool(rec.get("is_preprint")),
    }


def rung1_pmc_mcp(rec: dict, ctx: Ctx, state: dict) -> dict:
    """Coordinator handoff. A script cannot call the PubMed MCP tool."""
    if not (rec.get("pmid") or rec.get("pmcid")):
        return {"status": "skipped", "detail": "no PMID/PMCID for get_full_text_article"}
    stem = record_stem(rec)
    result_rel = "workspace/fulltext/%s.mcp.txt" % stem
    result_abs = ctx.run_dir / result_rel
    if result_abs.exists():
        text = result_abs.read_text(encoding="utf-8", errors="replace")
        if len(text.strip()) < MIN_TEXT_CHARS:
            return {"status": "failed", "detail": "coordinator MCP file <%d chars" % MIN_TEXT_CHARS}
        truncated, reasons = detect_truncation_text(text)
        detail = "MCP full text supplied by coordinator"
        if truncated:
            detail += "; truncated (%s)" % "; ".join(reasons)
        return {"status": "success", "detail": detail, "text": text,
                "access_route": "pmc_mcp", "truncation_detected": truncated}
    task = {
        "schema_version": SCHEMA_VERSION,
        "task_id": "retrieve:%s" % evidence_id_of(rec).replace(":", ":", 1),
        "evidence_id": evidence_id_of(rec),
        "status": "needs_mcp",
        "tool": "mcp__claude_ai_PubMed__get_full_text_article",
        "args": {"pmid": rec.get("pmid"), "pmcid": rec.get("pmcid")},
        "result_path": result_rel,
        "instructions": (
            "Call get_full_text_article with these args; write the returned full text verbatim "
            "to result_path (UTF-8), then re-run `fulltext.py acquire` or "
            "`fulltext.py resolve-mcp`. If the tool reports no full text, run "
            "`fulltext.py resolve-mcp --status unavailable` so the ladder skips rung 1."
        ),
        "created_at": utcnow(),
    }
    upsert_mcp_task(ctx.run_dir, task)
    return {"status": "needs_mcp", "detail": "emitted MCP task -> %s" % result_rel}


def rung2_pmc_pdf(rec: dict, ctx: Ctx, state: dict) -> dict:
    pmcid = (rec.get("pmcid") or state.get("pmcid") or "").strip()
    if not pmcid:
        return {"status": "skipped", "detail": "no PMCID"}
    url = PMC_PDF % pmcid
    try:
        resp = ctx.http.get(url, stream=True)
    except OfflineError as exc:
        return {"status": "skipped", "detail": str(exc)}
    except Exception as exc:
        return {"status": "failed", "detail": "PMC PDF fetch error: %s" % exc}
    if resp.status_code != 200:
        return {"status": "failed", "detail": "PMC PDF HTTP %d (not in the OA subset?)"
                % resp.status_code}
    ctype = resp.headers.get("Content-Type", "")
    if "pdf" not in ctype.lower():
        return {"status": "failed", "detail": "PMC PDF returned %s, not a PDF" % ctype}
    pdf = ctx.tmp_path(rec, ".pdf")
    with open(pdf, "wb") as fh:
        for chunk in resp.iter_content(1 << 16):
            fh.write(chunk)
    text, used_ocr = pdf_text_with_ocr(pdf, ctx.max_ocr_pages) if ctx.allow_ocr \
        else (library.pdftotext(pdf), False)
    if len(text.strip()) < MIN_TEXT_CHARS:
        return {"status": "failed", "detail": "PMC PDF text <%d chars (OCR too)" % MIN_TEXT_CHARS}
    return {"status": "success", "detail": "PMC OA PDF%s" % ("; OCR used" if used_ocr else ""),
            "text": text, "pdf": pdf, "access_route": "pmc_pdf", "url": url, "live": True}


def _epmc_search(ctx: Ctx, query: str) -> list[dict]:
    resp = ctx.http.get("%s/search" % EPMC,
                        params={"query": query, "format": "json", "resultType": "core",
                                "pageSize": "5"})
    if resp.status_code != 200:
        raise HttpError("Europe PMC search HTTP %d" % resp.status_code)
    data = resp.json()
    return (data.get("resultList") or {}).get("result") or []


def _epmc_fulltext(ctx: Ctx, ident: str) -> str:
    resp = ctx.http.get("%s/%s/fullTextXML" % (EPMC, ident))
    if resp.status_code != 200:
        raise HttpError("fullTextXML HTTP %d for %s" % (resp.status_code, ident))
    return jats_to_text(resp.text)


def rung3_europepmc(rec: dict, ctx: Ctx, state: dict) -> dict:
    """Id conversion -> PMCID/PPRID -> fullTextXML. Covers bioRxiv/medRxiv/Research Square."""
    doi = normalize_doi(rec.get("doi"))
    pmid = rec.get("pmid")
    pmcid = rec.get("pmcid")
    queries = []
    if pmcid:
        queries.append("PMCID:%s" % pmcid)
    if doi:
        queries.append('DOI:"%s"' % doi)
    if pmid:
        queries.append("EXT_ID:%s AND SRC:MED" % pmid)
    if not queries:
        return {"status": "skipped", "detail": "no DOI/PMID/PMCID for Europe PMC"}
    hits: list[dict] = []
    for q in queries:
        try:
            hits = _epmc_search(ctx, q)
        except OfflineError as exc:
            return {"status": "skipped", "detail": str(exc)}
        except Exception as exc:
            return {"status": "failed", "detail": "Europe PMC error: %s" % exc}
        if hits:
            break
    if not hits:
        return {"status": "failed", "detail": "no Europe PMC record"}
    hit = hits[0]
    found_pmcid = hit.get("pmcid")
    if found_pmcid and not rec.get("pmcid"):
        state["pmcid"] = found_pmcid
    src, ext_id = hit.get("source"), hit.get("id")
    idents = [i for i in (found_pmcid, ext_id if src == "PPR" else None) if i]
    if not idents:
        return {"status": "failed", "detail": "Europe PMC record has no PMCID/PPR id"}
    for ident in idents:
        try:
            text = _epmc_fulltext(ctx, ident)
        except OfflineError as exc:
            return {"status": "skipped", "detail": str(exc)}
        except Exception as exc:
            state.setdefault("notes", []).append("epmc %s: %s" % (ident, exc))
            continue
        if len(text.strip()) >= MIN_TEXT_CHARS:
            is_ppr = ident.startswith("PPR")
            return {
                "status": "success",
                "detail": "Europe PMC fullTextXML %s%s" % (ident, " (preprint)" if is_ppr else ""),
                "text": text,
                "access_route": "epmc_xml",
                "is_preprint": is_ppr,
                "url": "%s/%s/fullTextXML" % (EPMC, ident),
                "live": True,
            }
    # no XML, but remember any OA link for rung 5
    for link in (hit.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
        if (link.get("availability") or "").lower().startswith("open") or \
                link.get("availabilityCode") == "OA":
            state["oa_location"] = {"url": link.get("url"),
                                    "type": link.get("documentStyle"),
                                    "from": "europepmc"}
            break
    return {"status": "failed", "detail": "no full text XML available at Europe PMC"}


def rung4_unpaywall(rec: dict, ctx: Ctx, state: dict) -> dict:
    doi = normalize_doi(rec.get("doi"))
    if not doi:
        return {"status": "skipped", "detail": "no DOI"}
    if not ctx.email:
        return {"status": "skipped",
                "detail": "Unpaywall requires email=; set DEEP_RESEARCH_EMAIL or --email"}
    try:
        resp = ctx.http.get("%s/%s" % (UNPAYWALL, doi), params={"email": ctx.email})
    except OfflineError as exc:
        return {"status": "skipped", "detail": str(exc)}
    except Exception as exc:
        return {"status": "failed", "detail": "Unpaywall error: %s" % exc}
    if resp.status_code != 200:
        return {"status": "failed", "detail": "Unpaywall HTTP %d" % resp.status_code}
    try:
        data = resp.json()
    except ValueError:
        return {"status": "failed", "detail": "Unpaywall returned non-JSON"}
    loc = data.get("best_oa_location") or None
    if not loc:
        for cand in data.get("oa_locations") or []:
            loc = cand
            break
    if not loc:
        return {"status": "failed", "detail": "no OA location (is_oa=%s)" % data.get("is_oa")}
    url = loc.get("url_for_pdf") or loc.get("url") or loc.get("url_for_landing_page")
    if not url:
        return {"status": "failed", "detail": "OA location without a URL"}
    state["oa_location"] = {
        "url": url,
        "is_pdf": bool(loc.get("url_for_pdf")),
        "host_type": loc.get("host_type"),
        "version": loc.get("version"),
        "license": loc.get("license"),
        "from": "unpaywall",
    }
    return {"status": "success", "detail": "OA location %s (%s)" % (url, loc.get("host_type")),
            "no_text": True, "access_route": "unpaywall"}


def rung5_oa_fetch(rec: dict, ctx: Ctx, state: dict) -> dict:
    loc = state.get("oa_location")
    if not loc or not loc.get("url"):
        return {"status": "skipped", "detail": "no OA location from rungs 3-4"}
    url = loc["url"]
    try:
        resp = ctx.http.get(url, stream=True)
    except OfflineError as exc:
        return {"status": "skipped", "detail": str(exc)}
    except Exception as exc:
        return {"status": "failed", "detail": "OA fetch error: %s" % exc}
    if resp.status_code != 200:
        return {"status": "failed", "detail": "OA location HTTP %d" % resp.status_code}
    ctype = (resp.headers.get("Content-Type") or "").lower()
    body = resp.content
    if "pdf" in ctype or body[:5] == b"%PDF-":
        pdf = ctx.tmp_path(rec, ".pdf")
        pdf.write_bytes(body)
        text, used_ocr = pdf_text_with_ocr(pdf, ctx.max_ocr_pages) if ctx.allow_ocr \
            else (library.pdftotext(pdf), False)
        if len(text.strip()) < MIN_TEXT_CHARS:
            return {"status": "failed",
                    "detail": "OA PDF text <%d chars (OCR too)" % MIN_TEXT_CHARS}
        return {"status": "success",
                "detail": "OA PDF %s%s" % (url, "; OCR used" if used_ocr else ""),
                "text": text, "pdf": pdf, "access_route": "unpaywall_pdf"
                if loc.get("from") == "unpaywall" else "oa_pdf",
                "url": url, "live": True}

    html = body.decode(resp.encoding or "utf-8", "replace")
    text = html_to_text(html)
    truncated, reasons = detect_truncation(html, text)
    if not text.strip():
        return {"status": "failed", "detail": "OA HTML yielded no text"}
    return {
        "status": "success",
        "detail": "OA HTML %s%s" % (url, "; TRUNCATED: %s" % ",".join(reasons) if truncated else ""),
        "text": text,
        "access_route": "oa_html",
        "truncation_detected": truncated,
        "reasons": reasons,
        "url": url,
        "live": True,
        "origin": "unpaywall" if loc.get("from") == "unpaywall" else "oa-pdf",
    }


def rung6_preprint_twin(rec: dict, ctx: Ctx, state: dict) -> dict:
    title = (rec.get("title") or "").replace('"', " ").strip()
    doi = normalize_doi(rec.get("doi"))
    queries = []
    if doi:
        queries.append('DOI:"%s" AND SRC:PPR' % doi)
    if len(title) >= 25:
        queries.append('TITLE:"%s" AND SRC:PPR' % title)
    if not queries:
        return {"status": "skipped", "detail": "no title/DOI for preprint search"}
    for q in queries:
        try:
            hits = _epmc_search(ctx, q)
        except OfflineError as exc:
            return {"status": "skipped", "detail": str(exc)}
        except Exception as exc:
            return {"status": "failed", "detail": "Europe PMC preprint search error: %s" % exc}
        for hit in hits:
            ident = hit.get("id")
            if not ident:
                continue
            ratio = library.title_ratio(library.normalize_title(title),
                                        library.normalize_title(hit.get("title")))
            if title and ratio < library.FUZZY_TITLE_THRESHOLD and not doi:
                continue
            try:
                text = _epmc_fulltext(ctx, ident)
            except Exception:
                continue
            if len(text.strip()) >= MIN_TEXT_CHARS:
                return {
                    "status": "success",
                    "detail": ("PREPRINT TWIN %s (%s) — content differs from the published "
                               "version; flag loudly" % (ident, hit.get("doi") or "no doi")),
                    "text": text,
                    "access_route": "preprint_twin",
                    "is_preprint": True,
                    "preprint_id": ident,
                    "preprint_doi": hit.get("doi"),
                    "url": "%s/%s/fullTextXML" % (EPMC, ident),
                    "live": True,
                }
    return {"status": "failed", "detail": "no preprint twin with full text"}


def load_institutional_access(run_dir: Path) -> dict | None:
    """`config.json` `institutional_access` object, or None if absent/disabled/unreadable.

    Opt-in only (Stage 0 asks once per run, `SKILL.md` "Institutional access"). Shape:
    ``{"enabled": true, "name": "King's College London",
       "search_url": "https://librarysearch.kcl.ac.uk/discovery/search?vid=44KCL_INST:44KCL_INST"}``.
    Absent or ``enabled: false`` means rung 7 stays OA-only, exactly as it was before this
    setting existed.
    """
    try:
        cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    inst = cfg.get("institutional_access") or {}
    if not inst.get("enabled") or not inst.get("search_url"):
        return None
    return inst


def institutional_marker_path(browser_text_path: Path) -> Path:
    """Sidecar marker: `resolve-browser --institutional` touches this next to the text file
    so a later plain `acquire` pass (which just reads the text file) still records
    `access_route: browser_fetch_institutional` instead of losing the disclosure."""
    return browser_text_path.with_name(browser_text_path.name + ".institutional")


def rung7_browser_fetch(rec: dict, ctx: Ctx, state: dict) -> dict:
    """Coordinator handoff. A script cannot drive the claude-in-chrome MCP tools.

    OA-only by default, last resort before quarantine: for content a plain HTTP GET cannot
    render (JS-gated open-access pages, bot-blocked hosts) but that carries no login wall,
    paywall, or captcha. A record that reaches here already failed rungs 0-6, so every scripted
    OA route (library, PMC, Europe PMC, Unpaywall, direct OA fetch, preprint twin) is exhausted.

    If `config.json` `institutional_access.enabled` is set (opt-in, Stage 0 asks once per run —
    see `SKILL.md` "Institutional access" and `references/acquisition.md` §4b), the task also
    names that library's discovery search as a candidate. The coordinator may open it and search
    by title/DOI, but if it reaches a login/SSO wall it must **stop and hand the tab to the
    human** to authenticate themselves -- never type, store, or transmit the credential itself.
    """
    if ctx.offline:
        return {"status": "skipped", "detail": "offline: browser rung needs a live browser"}
    stem = record_stem(rec)
    result_rel = "workspace/fulltext/%s.browser.txt" % stem
    result_abs = ctx.run_dir / result_rel
    if result_abs.exists():
        text = result_abs.read_text(encoding="utf-8", errors="replace")
        if len(text.strip()) < MIN_TEXT_CHARS:
            return {"status": "failed", "detail": "coordinator browser file <%d chars"
                    % MIN_TEXT_CHARS}
        truncated, reasons = detect_truncation_text(text)
        institutional = institutional_marker_path(result_abs).exists()
        route = "browser_fetch_institutional" if institutional else "browser_fetch"
        detail = "browser full text supplied by coordinator"
        if institutional:
            detail += " via opt-in institutional access"
        if truncated:
            detail += "; truncated (%s)" % "; ".join(reasons)
        return {"status": "success", "detail": detail, "text": text,
                "access_route": route, "truncation_detected": truncated,
                "url": canonical_url(rec, state)}
    doi = normalize_doi(rec.get("doi"))
    inst = load_institutional_access(ctx.run_dir)
    instructions = (
        "Search for and open a freely-accessible copy of this article (publisher OA page, "
        "institutional/author repository, or a preprint mirror rung 6 did not already find) "
        "using the claude-in-chrome MCP tools. OA content only by default: never sign in, "
        "never use a saved session or institutional/VPN/EZproxy/Shibboleth access, never solve "
        "a captcha, never pay, never retry a 401/402/403 by another route. If every candidate "
        "is a login wall, paywall, or captcha, stop -- do not proceed -- and run `fulltext.py "
        "resolve-browser --status unavailable` instead. Otherwise extract the visible article "
        "body text (read_page / get_page_text) and write it verbatim (UTF-8) to result_path, "
        "then re-run `fulltext.py acquire` or `fulltext.py resolve-browser`."
    )
    if inst:
        instructions += (
            " INSTITUTIONAL ACCESS (opt-in, this run only): also try %s -- use its on-page "
            "search box with the title or DOI above, do not construct or guess a query-string "
            "API for it. If it reaches an SSO/login page, STOP: tell the human this record "
            "needs their %s login, hand them the open tab, and wait -- you must never type, "
            "store, or transmit the credential yourself. Only resume extraction once the human "
            "confirms the tab shows the authenticated article. If the text came from this "
            "institutional path, you MUST record it with `fulltext.py resolve-browser "
            "--institutional --text-file <path>` -- do not just drop the file and re-run "
            "`acquire`, which would silently record it as plain OA browser_fetch and lose the "
            "institutional-access disclosure the report methods section needs."
            % (inst["search_url"], inst.get("name") or "institutional")
        )
    task = {
        "schema_version": SCHEMA_VERSION,
        "task_id": "retrieve:%s" % evidence_id_of(rec),
        "evidence_id": evidence_id_of(rec),
        "status": "needs_browser",
        "tool": "mcp__claude-in-chrome__* (navigate / read_page / get_page_text)",
        "args": {
            "pmid": rec.get("pmid"), "doi": doi, "pmcid": rec.get("pmcid"),
            "title": rec.get("title"), "journal": rec.get("journal"),
            "candidate_urls": [u for u in [
                ("https://doi.org/%s" % doi) if doi else None,
                ("https://pubmed.ncbi.nlm.nih.gov/%s/" % rec["pmid"]) if rec.get("pmid") else None,
            ] if u],
            "institutional_search_url": inst["search_url"] if inst else None,
        },
        "result_path": result_rel,
        "instructions": instructions,
        "created_at": utcnow(),
    }
    upsert_browser_task(ctx.run_dir, task)
    return {"status": "needs_browser", "detail": "emitted browser task -> %s" % result_rel}


MISSING_MD_HEADER = (
    "# missing.md — records the OA ladder could not fully resolve\n\n"
    "Two kinds of block appear here, both needing the same action: drop a PDF into `inbox/` "
    "and re-run; `library.py ingest-inbox` will match and remove the block.\n\n"
    "- **Quarantined (no text at all)** — every rung failed outright.\n"
    "- **Abstract-only (degraded text obtained)** — some rung returned text, but the "
    "truncation detector flagged it as an abstract/teaser, not the full article; the ladder "
    "kept walking every remaining rung looking for real full text and found none. The stored "
    "text is real and is used as-is unless a better PDF arrives.\n\n"
    "No paywall circumvention is attempted for either kind.\n\n"
)


def append_missing_block(ctx: Ctx, rec: dict, state: dict, *, status_line: str) -> bool:
    """Append (once) an evidence_id's block to `<run-dir>/missing.md`. Returns False if
    already present -- the caller's rung result is still reported as `success` either way."""
    eid = evidence_id_of(rec)
    path = ctx.run_dir / "missing.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing:
        existing = MISSING_MD_HEADER
    if "- evidence_id: %s" % eid in existing:
        return False
    pmid, doi, pmcid = rec.get("pmid"), normalize_doi(rec.get("doi")), rec.get("pmcid")
    attempts = "; ".join(
        "t%s=%s" % (t, state["rungs"][t].get("status")) for t in sorted(state["rungs"], key=int)
    )
    block = ["## %s" % (rec.get("title") or eid), ""]
    block += [
        "- Status: %s" % status_line,
        "- evidence_id: %s" % eid,
        "- PMID: %s" % (pmid or "null"),
        "- DOI: %s" % (doi or "null"),
        "- PMCID: %s" % (pmcid or "null"),
        "- Journal: %s" % (rec.get("journal") or "null"),
        "- Rungs attempted: %s" % (attempts or "none"),
    ]
    if pmid:
        block.append("- PubMed: https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid)
    if doi:
        block.append("- DOI link: https://doi.org/%s" % doi)
    if pmcid:
        block.append("- PMC: https://pmc.ncbi.nlm.nih.gov/articles/%s/" % pmcid)
    block += ["", "Action: place the PDF in `inbox/` (any filename) and re-run the skill.", ""]
    path.write_text(existing.rstrip("\n") + "\n\n" + "\n".join(block), encoding="utf-8")
    return True


def rung8_quarantine(rec: dict, ctx: Ctx, state: dict) -> dict:
    append_missing_block(ctx, rec, state, status_line="quarantined (no text obtained)")
    return {"status": "success", "detail": "quarantined in missing.md", "no_text": True,
            "access_route": "quarantine"}


RUNGS = [
    (0, rung0_library),
    (1, rung1_pmc_mcp),
    (2, rung2_pmc_pdf),
    (3, rung3_europepmc),
    (4, rung4_unpaywall),
    (5, rung5_oa_fetch),
    (6, rung6_preprint_twin),
    (7, rung7_browser_fetch),
    (8, rung8_quarantine),
]

TERMINAL_RUNG_STATES = {"success", "failed", "skipped", "unavailable"}


# ------------------------------------------------------------------ finalize ---


def finalize(rec: dict, ctx: Ctx, state: dict, tier: int, res: dict) -> dict:
    """Store text/PDF, build the schema.md §4 `fulltext` sub-object."""
    route = res.get("access_route") or TIER_ROUTES[tier]
    truncated = bool(res.get("truncation_detected"))
    text = res.get("text") or ""
    local_path, digest = None, None
    asset = None

    if res.get("pdf"):
        pdf = Path(res["pdf"])
        entry, _ = ctx.lib.add(
            pdf,
            pmid=rec.get("pmid"), doi=rec.get("doi"), pmcid=rec.get("pmcid"),
            title=rec.get("title"), journal=rec.get("journal"),
            year=(rec.get("publication_date") or "")[:4] or None,
            source_tier=tier, access_route=route, stem=record_stem(rec),
            # carry the flag into the index so the next run's rung-0 hit is correct
            is_preprint=bool(res.get("is_preprint") or rec.get("is_preprint")),
            move=str(pdf.parent).endswith("retrieve/tmp"),
        )
        local_path, digest = entry["path"], entry["sha256"]
        # R13: the PDF stays in the shared library; the snapshot records the
        # wiki-root-relative path plus its hash. Nothing is copied into the run.
        try:
            nbytes = entry.get("bytes")
            if not isinstance(nbytes, int):
                nbytes = ctx.lib.entry_path(entry).stat().st_size
            asset = {"path": Path(local_path).as_posix(), "sha256": digest, "bytes": nbytes}
        except OSError:
            asset = None

    if text:
        tpath = ctx.text_path(rec)
        tpath.write_text(text, encoding="utf-8")
        if local_path is None:
            local_path = ctx.wiki_rel(tpath)
            digest = sha256_file(tpath)

    if tier == 8 or (not text and res.get("no_text")):
        status = "missing" if tier == 8 else "abstract_only"
    elif truncated:
        status = "abstract_only"
    else:
        status = "fulltext"

    fulltext = {
        "status": status,
        "source_tier": tier,
        "access_route": route,
        "local_path": local_path,
        "sha256": digest,
        "truncation_detected": truncated,
    }
    state["fulltext"] = fulltext
    if tier != 8 and status != "missing":
        library.unquarantine(ctx.run_dir, evidence_id_of(rec))
    if res.get("is_preprint"):
        state["is_preprint"] = True
        rec["is_preprint"] = True
    register_acquisition(rec, ctx, state, tier, res, fulltext, asset)
    return fulltext


def already_satisfied(rec: dict, from_tier: int | None) -> bool:
    if from_tier is not None:
        return False
    ft = rec.get("fulltext") or {}
    return ft.get("status") == "fulltext"


def acquire_record(rec: dict, ctx: Ctx, from_tier: int | None = None) -> dict:
    state = load_state(ctx.run_dir, rec)
    eid = evidence_id_of(rec)
    if already_satisfied(rec, from_tier):
        return {"evidence_id": eid, "action": "skipped", "reason": "already fulltext",
                "fulltext": rec.get("fulltext")}

    start = from_tier if from_tier is not None else 0
    if from_tier is not None:
        for t in list(state["rungs"]):
            if int(t) >= from_tier:
                state["rungs"].pop(t)

    # A truncated success (abstract-only text) is kept here as a fallback rather than
    # finalized immediately: the ladder keeps walking every remaining rung looking for real
    # full text before settling. This is what makes a degraded rung-1/5/7 hit surface at the
    # missing.md gate instead of silently passing as `fulltext` with only rungs 0-1 tried.
    fallback: tuple[int, dict] | None = None
    for tier, handler in RUNGS:
        if tier < start:
            continue
        prev = state["rungs"].get(str(tier))
        if prev and prev.get("status") in TERMINAL_RUNG_STATES and prev.get("status") != "success":
            continue  # never redo a completed rung
        if prev and prev.get("status") == "success" and state.get("fulltext"):
            break
        if tier == 8 and fallback is not None:
            break  # don't let quarantine's zero-text write clobber a real (degraded) hit
        try:
            res = handler(rec, ctx, state)
        except Exception as exc:  # a rung must never kill the run
            res = {"status": "failed", "detail": "unhandled error: %r" % exc}
        mark(state, tier, res["status"], res.get("detail", ""))
        log(ctx.run_dir, "%s rung %d -> %s (%s)" % (eid, tier, res["status"],
                                                    res.get("detail", "")))
        if res["status"] == "success":
            if res.get("truncation_detected"):
                if fallback is None:
                    fallback = (tier, res)
                save_state(ctx.run_dir, rec, state)
                continue  # abstract-only text in hand; keep walking for real full text
            fulltext = finalize(rec, ctx, state, tier, res)
            rec["fulltext"] = fulltext
            save_state(ctx.run_dir, rec, state)
            return {"evidence_id": eid, "action": "acquired", "tier": tier,
                    "fulltext": fulltext, "source_ids": list(rec.get("source_ids") or []),
                    "detail": res.get("detail", "")}
        # needs_mcp / needs_browser: task emitted, keep walking the ladder
    if fallback is not None:
        tier, res = fallback
        fulltext = finalize(rec, ctx, state, tier, res)
        rec["fulltext"] = fulltext
        append_missing_block(
            ctx, rec, state,
            status_line="abstract-only (tier %d, %s) — the ladder tried every remaining rung "
                        "and found nothing better; a full PDF would upgrade this record"
                        % (tier, res.get("access_route") or TIER_ROUTES.get(tier)))
        save_state(ctx.run_dir, rec, state)
        return {"evidence_id": eid, "action": "acquired_abstract_only", "tier": tier,
                "fulltext": fulltext, "source_ids": list(rec.get("source_ids") or []),
                "detail": res.get("detail", "")}
    save_state(ctx.run_dir, rec, state)
    rec["fulltext"] = rec.get("fulltext") or {
        "status": "missing", "source_tier": 8, "access_route": "quarantine",
        "local_path": None, "sha256": None, "truncation_detected": False,
    }
    quarantined = (state["rungs"].get("8") or {}).get("status") == "success"
    return {"evidence_id": eid,
            "action": "quarantined" if quarantined else "exhausted",
            "fulltext": rec["fulltext"]}


# ----------------------------------------------------------------- selection ---


def selectable(rec: dict) -> bool:
    screening = rec.get("screening") or {}
    return screening.get("decision") != "exclude"


# ----------------------------------------------------------------------- CLI ---


def resolve_paths(args) -> tuple[Path, Path]:
    run_dir = Path(args.run_dir).expanduser().resolve()
    wiki = Path(args.wiki).expanduser().resolve() if getattr(args, "wiki", None) \
        else wiki_root_for_run(run_dir)
    return run_dir, wiki


#: Pool size when neither `--workers` nor `budgets.max_parallel` says otherwise. Deliberately
#: modest: the ladder's hosts are rate-limited per host, so more threads mostly means more
#: threads sleeping on the same host lock.
DEFAULT_WORKERS = 4
MAX_WORKERS = 16


def resolve_workers(args, run_dir: Path) -> int:
    """`--workers`, else `config.json` `budgets.max_parallel`, else `DEFAULT_WORKERS`.

    Forced to 1 under `--offline`, which has no network work to overlap and whose determinism
    the fixture tests depend on.
    """
    if getattr(args, "offline", False):
        return 1
    n = getattr(args, "workers", None)
    if n is None:
        try:
            cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
            n = (cfg.get("budgets") or {}).get("max_parallel")
        except (OSError, ValueError):
            n = None
    try:
        n = int(n) if n is not None else DEFAULT_WORKERS
    except (TypeError, ValueError):
        n = DEFAULT_WORKERS
    return max(1, min(n, MAX_WORKERS))


def resolve_max_ocr_pages(args, run_dir: Path) -> int:
    n = getattr(args, "max_ocr_pages", None)
    if n is None:
        try:
            cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
            n = (cfg.get("budgets") or {}).get("max_ocr_pages")
        except (OSError, ValueError):
            n = None
    try:
        n = int(n) if n is not None else 30
    except (TypeError, ValueError):
        n = 30
    return max(1, min(n, 200))


def cmd_acquire(args) -> int:
    run_dir, wiki = resolve_paths(args)
    corpus_path = Path(args.corpus).expanduser().resolve()
    email = args.email or os.environ.get("DEEP_RESEARCH_EMAIL")
    records = read_corpus(corpus_path)
    if not records:
        print(json.dumps({"error": "no records in %s" % corpus_path}, indent=2))
        return 1
    ctx = Ctx(run_dir, wiki, email, offline=args.offline, allow_ocr=not args.no_ocr,
              register=not args.no_register, max_ocr_pages=resolve_max_ocr_pages(args, run_dir))

    # Selection happens up front, so `--limit` means "the first N selectable records" with a
    # pool exactly as it did with a serial loop. Breaking out of a pool mid-flight would make
    # the set depend on which worker finished first.
    selected = []
    for rec in records:
        if args.only_pmid and str(rec.get("pmid") or "") != str(args.only_pmid):
            continue
        if args.only_evidence_id and evidence_id_of(rec) != args.only_evidence_id:
            continue
        if not selectable(rec):
            continue
        selected.append(rec)
        if args.limit and len(selected) >= args.limit:
            break

    workers = resolve_workers(args, run_dir)
    if workers > 1 and len(selected) > 1:
        # Each record owns its own dict and its own output paths, so the only shared state is
        # the HTTP throttle (per-host locked), engine.log (locked) and the snapshot store
        # (O_EXCL snapshots, lock-serialised event append — see store._EVENT_LOCK).
        with cf.ThreadPoolExecutor(max_workers=workers,
                                   thread_name_prefix="acquire") as pool:
            futures = [pool.submit(acquire_record, rec, ctx, from_tier=args.from_tier)
                       for rec in selected]
            results = [f.result() for f in futures]      # submission order, not completion
    else:
        results = [acquire_record(rec, ctx, from_tier=args.from_tier) for rec in selected]

    write_corpus(corpus_path, records)
    pending = pending_mcp_tasks(run_dir, records)
    pending_browser = pending_browser_tasks(run_dir, records)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "wiki_root": str(wiki),
        "processed": len(results),
        "acquired": sum(1 for r in results if r["action"] == "acquired"),
        "quarantined": sum(1 for r in results
                           if (r.get("fulltext") or {}).get("status") == "missing"),
        "unpaywall_email": bool(email),
        "needs_mcp": len(pending),
        "needs_browser": len(pending_browser),
        "registered_sources": sum(len(r.get("source_ids") or []) for r in results),
        "sources_dir": str(run_dir / "sources") if not args.no_register else None,
        "mcp_tasks_path": str(mcp_tasks_path(run_dir)) if pending else None,
        "browser_tasks_path": str(browser_tasks_path(run_dir)) if pending_browser else None,
        "results": results,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    alert_acquisition_health(summary, email=email)
    return 0


def alert_acquisition_health(summary: dict, *, email: str | None) -> None:
    """Loud stderr banner for acquisition problems a coordinator must surface.

    The JSON summary already carries these numbers, but a degraded run reads as a
    successful one unless someone counts them. Anything here is meant to reach the
    user verbatim, not to be summarised away.
    """
    processed = summary.get("processed") or 0
    if not processed:
        return
    results = summary.get("results") or []
    statuses = [(r.get("fulltext") or {}).get("status") for r in results]
    abstract_only = sum(1 for s in statuses if s == "abstract_only")
    quarantined = summary.get("quarantined") or 0
    pending_mcp = summary.get("needs_mcp") or 0
    pending_browser = summary.get("needs_browser") or 0
    no_text = quarantined + abstract_only

    alerts: list[str] = []
    if pending_mcp:
        alerts.append(
            f"{pending_mcp}/{processed} record(s) are waiting on ladder rung 1 "
            f"(PubMed MCP get_full_text_article), which a script cannot call. "
            f"If no PubMed MCP server is connected, these will NEVER resolve on their "
            f"own: call the tool yourself, or run `fulltext.py resolve-mcp "
            f"--status unavailable` per record so the ladder stops waiting. "
            f"Tasks: {summary.get('mcp_tasks_path')}")
    if pending_browser:
        alerts.append(
            f"{pending_browser}/{processed} record(s) are waiting on ladder rung 7 "
            f"(browser search/fetch via claude-in-chrome), which a script cannot drive. "
            f"If no claude-in-chrome MCP server is connected, these will NEVER resolve on "
            f"their own: drive the browser yourself (OA content only -- see task "
            f"instructions), or run `fulltext.py resolve-browser --status unavailable` per "
            f"record so the ladder stops waiting. Tasks: {summary.get('browser_tasks_path')}")
    if quarantined:
        alerts.append(
            f"{quarantined}/{processed} record(s) quarantined with no text at all. "
            f"They are listed in missing.md; the synthesis must be marked provisional.")
    if abstract_only:
        alerts.append(
            f"{abstract_only}/{processed} record(s) are abstract-only. They must be "
            f"tagged evidence_basis=abstract_only and never appraised as full text.")
    if not summary.get("unpaywall_email"):
        alerts.append(
            "No Unpaywall contact email set ($DEEP_RESEARCH_EMAIL or --email): "
            "ladder rung 4 is degraded and OA copies will be missed.")
    if processed and no_text * 2 > processed:
        alerts.append(
            f"MAJORITY WITHOUT FULL TEXT: {no_text}/{processed}. Extraction quality is "
            f"materially limited. Tell the user before extracting, not after.")
    if not alerts:
        return
    print("", file=sys.stderr)
    print("!" * 72, file=sys.stderr)
    print("ACQUISITION ALERT — surface these to the user verbatim:", file=sys.stderr)
    for a in alerts:
        print(f"  * {a}", file=sys.stderr)
    print("!" * 72, file=sys.stderr)


def cmd_status(args) -> int:
    run_dir, wiki = resolve_paths(args)
    corpus_path = Path(args.corpus) if args.corpus else run_dir / "corpus.jsonl"
    records = read_corpus(corpus_path)
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    missing = []
    truncated = []
    for rec in records:
        ft = rec.get("fulltext") or {}
        st = ft.get("status") or "unattempted"
        by_status[st] = by_status.get(st, 0) + 1
        by_tier[str(ft.get("source_tier"))] = by_tier.get(str(ft.get("source_tier")), 0) + 1
        if st == "missing":
            missing.append(evidence_id_of(rec))
        if ft.get("truncation_detected"):
            truncated.append(evidence_id_of(rec))
    pending = pending_mcp_tasks(run_dir, records)
    pending_browser = pending_browser_tasks(run_dir, records)
    out = {
        "schema_version": SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "corpus": str(corpus_path),
        "records": len(records),
        "by_status": by_status,
        "by_source_tier": by_tier,
        "truncation_detected": truncated,
        "missing": missing,
        "missing_md": str(run_dir / "missing.md"),
        "inbox_pdfs": len(list((run_dir / "inbox").glob("*.pdf"))) if (run_dir / "inbox").exists()
        else 0,
        "mcp_tasks_pending": [
            {"evidence_id": t["evidence_id"], "args": t["args"], "result_path": t["result_path"]}
            for t in pending
        ],
        "browser_tasks_pending": [
            {"evidence_id": t["evidence_id"], "args": t["args"], "result_path": t["result_path"]}
            for t in pending_browser
        ],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_resolve_mcp(args) -> int:
    """Record the outcome of the rung-1 coordinator handoff, then let the ladder keep walking.

    Deliberately thin: this used to finalize rung 1 directly and stop, which meant a short or
    truncated MCP response (a PubMed abstract, not the article) was recorded as terminal
    `fulltext` and rungs 2-8 were never tried, and the record never surfaced at the missing.md
    gate for a PDF ask. Now it only persists the coordinator's answer (or marks the rung
    unavailable) and calls `acquire_record`, the same ladder `acquire` uses: truncated text is
    kept as a fallback while the ladder keeps trying for real full text, and a genuinely short
    response fails rung 1 and moves on to rung 2 -- exactly as if `acquire` had picked up the
    file itself.
    """
    run_dir, wiki = resolve_paths(args)
    corpus_path = Path(args.corpus) if args.corpus else run_dir / "corpus.jsonl"
    records = read_corpus(corpus_path)
    target = None
    for rec in records:
        if evidence_id_of(rec) == args.evidence_id or (
            args.evidence_id.isdigit() and str(rec.get("pmid")) == args.evidence_id
        ):
            target = rec
            break
    if target is None:
        print(json.dumps({"error": "no corpus record for %s" % args.evidence_id}, indent=2))
        return 1
    ctx = Ctx(run_dir, wiki, args.email or os.environ.get("DEEP_RESEARCH_EMAIL"), offline=False,
              register=not args.no_register)
    if args.status == "unavailable":
        state = load_state(run_dir, target)
        mark(state, 1, "unavailable", "coordinator: MCP reports no full text")
        save_state(run_dir, target, state)
        upsert_mcp_task(run_dir, {"task_id": "retrieve:%s" % args.evidence_id,
                                  "schema_version": SCHEMA_VERSION,
                                  "evidence_id": args.evidence_id, "status": "unavailable",
                                  "result_path": "workspace/fulltext/%s.mcp.txt"
                                  % record_stem(target), "args": {}, "tool":
                                  "mcp__claude_ai_PubMed__get_full_text_article",
                                  "created_at": utcnow()})
    else:
        src = Path(args.text_file).expanduser().resolve()
        text = src.read_text(encoding="utf-8", errors="replace")
        # The coordinator, not this process, called the MCP tool: whatever text results from
        # this is registered (`register`, never fresh — R22), never claimed as a live `fetch`.
        dest = run_dir / "workspace" / "fulltext" / ("%s.mcp.txt" % record_stem(target))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src != dest:
            dest.write_text(text, encoding="utf-8")
    result = acquire_record(target, ctx)
    write_corpus(corpus_path, records)
    print(json.dumps({"evidence_id": args.evidence_id, **result}, indent=2))
    return 0


def cmd_resolve_browser(args) -> int:
    """Record the outcome of the rung-7 coordinator browser handoff, then let the ladder keep
    walking. Same reasoning as `cmd_resolve_mcp`: this only persists the coordinator's answer
    (or marks the rung unavailable) and calls `acquire_record` -- a truncated browser result no
    longer finalizes as terminal `fulltext`/`abstract_only` on the spot, and rung 8 (quarantine)
    is skipped in favour of a fallback finalize plus a missing.md block once the whole ladder is
    confirmed exhausted.
    """
    run_dir, wiki = resolve_paths(args)
    corpus_path = Path(args.corpus) if args.corpus else run_dir / "corpus.jsonl"
    records = read_corpus(corpus_path)
    target = None
    for rec in records:
        if evidence_id_of(rec) == args.evidence_id or (
            args.evidence_id.isdigit() and str(rec.get("pmid")) == args.evidence_id
        ):
            target = rec
            break
    if target is None:
        print(json.dumps({"error": "no corpus record for %s" % args.evidence_id}, indent=2))
        return 1
    ctx = Ctx(run_dir, wiki, args.email or os.environ.get("DEEP_RESEARCH_EMAIL"), offline=False,
              register=not args.no_register)
    if args.status == "unavailable":
        state = load_state(run_dir, target)
        mark(state, 7, "unavailable", "coordinator: no OA copy reachable without a login/"
             "paywall/captcha")
        save_state(run_dir, target, state)
        upsert_browser_task(run_dir, {"task_id": "retrieve:%s" % args.evidence_id,
                                      "schema_version": SCHEMA_VERSION,
                                      "evidence_id": args.evidence_id, "status": "unavailable",
                                      "result_path": "workspace/fulltext/%s.browser.txt"
                                      % record_stem(target), "args": {}, "tool":
                                      "mcp__claude-in-chrome__*", "created_at": utcnow()})
    else:
        src = Path(args.text_file).expanduser().resolve()
        text = src.read_text(encoding="utf-8", errors="replace")
        # The coordinator, not this process, drove the browser: whatever text results from
        # this is registered (`register`, never fresh — R22), never claimed as a live `fetch`.
        dest = run_dir / "workspace" / "fulltext" / ("%s.browser.txt" % record_stem(target))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src != dest:
            dest.write_text(text, encoding="utf-8")
        marker = institutional_marker_path(dest)
        if args.institutional:
            marker.touch()
        elif marker.exists():
            marker.unlink()
    result = acquire_record(target, ctx)
    write_corpus(corpus_path, records)
    print(json.dumps({"evidence_id": args.evidence_id, **result}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fulltext.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("acquire", help="walk the acquisition ladder for every corpus record")
    s.add_argument("--corpus", required=True, help="path to corpus.jsonl")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--wiki", help="wiki root (default: inferred from --run-dir)")
    s.add_argument("--only-pmid", dest="only_pmid", help="restrict to one PMID")
    s.add_argument("--only-evidence-id", dest="only_evidence_id", help="restrict to one record")
    s.add_argument("--from-tier", type=int, dest="from_tier", choices=range(0, 9),
                   help="re-run the ladder starting at this rung (discards later rung state)")
    s.add_argument("--email", help="Unpaywall contact email (else $DEEP_RESEARCH_EMAIL)")
    s.add_argument("--limit", type=int, help="stop after N records")
    s.add_argument("--offline", action="store_true",
                   help="local rungs only; network rungs are skipped, not failed")
    s.add_argument("--no-ocr", action="store_true", dest="no_ocr",
                   help="disable the tesseract OCR fallback")
    s.add_argument("--max-ocr-pages", type=int, dest="max_ocr_pages",
                   help="max pages to OCR per PDF (default: config.json budgets.max_ocr_pages, "
                        "else 30)")
    s.add_argument("--no-register", action="store_true", dest="no_register",
                   help="do not register acquired text into <run-dir>/sources/ (kernel off)")
    s.add_argument("--workers", type=int, default=None,
                   help="acquire N records concurrently (default: config.json "
                        "budgets.max_parallel, else %d; 1 disables the pool). Per-host "
                        "rate limits are enforced regardless." % DEFAULT_WORKERS)
    s.set_defaults(func=cmd_acquire)

    s = sub.add_parser("status", help="acquisition state for a run")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--wiki")
    s.add_argument("--corpus", help="default: <run-dir>/corpus.jsonl")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("resolve-mcp", help="record a rung-1 PubMed MCP result (coordinator)")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--wiki")
    s.add_argument("--corpus")
    s.add_argument("--evidence-id", required=True, dest="evidence_id",
                   help="evidence_id or bare PMID")
    s.add_argument("--text-file", dest="text_file",
                   help="file holding the text returned by get_full_text_article")
    s.add_argument("--status", choices=["ok", "unavailable"], default="ok")
    s.add_argument("--email")
    s.add_argument("--no-register", action="store_true", dest="no_register",
                   help="do not register the supplied text into <run-dir>/sources/")
    s.set_defaults(func=cmd_resolve_mcp)

    s = sub.add_parser("resolve-browser",
                       help="record a rung-7 browser search/fetch result (coordinator)")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--wiki")
    s.add_argument("--corpus")
    s.add_argument("--evidence-id", required=True, dest="evidence_id",
                   help="evidence_id or bare PMID")
    s.add_argument("--text-file", dest="text_file",
                   help="file holding the extracted article body text")
    s.add_argument("--status", choices=["ok", "unavailable"], default="ok")
    s.add_argument("--institutional", action="store_true",
                   help="text was retrieved via config.json institutional_access (opt-in) "
                        "after the human completed SSO themselves; recorded as "
                        "access_route=browser_fetch_institutional for report transparency")
    s.add_argument("--email")
    s.add_argument("--no-register", action="store_true", dest="no_register",
                   help="do not register the supplied text into <run-dir>/sources/")
    s.set_defaults(func=cmd_resolve_browser)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "resolve-mcp" and args.status == "ok" and not args.text_file:
        print("resolve-mcp --status ok requires --text-file", file=sys.stderr)
        return 2
    if args.cmd == "resolve-browser" and args.status == "ok" and not args.text_file:
        print("resolve-browser --status ok requires --text-file", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
