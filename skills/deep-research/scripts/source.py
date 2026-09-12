#!/usr/bin/env python3
"""source.py — the acquisition-facing CLI over the evidence-kernel snapshot store.

`references/evidence-kernel.md`: `fetch`, `read`, `spans`, `local`.
All snapshot and span semantics live in `scripts/store.py`; this file only acquires bytes,
turns them into text, and hands them to the store. Contracts: `references/schema.md`
§10-§13 and R10-R24; narrative `references/evidence-kernel.md`.

    source.py fetch --run-dir D --url U [--fresh]        retrieve a URL and snapshot it
    source.py read  --run-dir D --source-id S [--start N --end N]   bounded, verified window
    source.py spans --run-dir D --source-id S --query T [--max N]   candidate span offsets
    source.py local --run-dir D --pdf P [--wiki R]       ingest a user-supplied PDF
    source.py local --run-dir D --pdf P --repo R --attachment-id A   ingest via refmgr
                                                          (registry.py add-pdf's attachment)

Policy (`SKILL.md` "Invariants" — hard limits, not defaults):

* No paywall circumvention, no credentials, no cookies, no institutional proxies, no
  browser automation, no sci-hub-class sources. `fetch` sends one polite GET with an
  identifying User-Agent and follows ordinary redirects; nothing else. A URL carrying
  embedded credentials is refused. Failures fail closed: no snapshot, no event.
* Schemes: `http`, `https` and `file` (a document already on this machine). Anything else
  is refused.
* `fetch` does not accept PDFs: PDFs belong in the shared library `<wiki>/assets/papers/`
  and are never duplicated per run (R13). File one with `library.py add`, then ingest it
  with `source.py local`, which records the asset hash the fresh-fetch rule needs.

Freshness: `--fresh` is the caller asserting that this GET was a real network round-trip
performed for this run, and it is what writes `fresh: true` (R15/R22). Without it the
retrieval is still logged, as a `fetch` event with `fresh: false`, which does **not**
satisfy the fresh-fetch rule. `source.py local` always writes a fresh `local_pdf` event
because the hash-checked local file *is* the source (the user-supplied-PDF exception).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import emit_json as _emit  # noqa: E402  (sibling module, stdlib-only)
import store  # noqa: E402  (sibling module, stdlib-only)
from store import (  # noqa: E402
    MAX_SPAN_CHARS,
    SchemaError,
    StoreError,
    Store,
    sha256_file,
    wiki_root_for_run,
)

try:
    import requests
except ImportError:  # pragma: no cover - requests is verified present in `SKILL.md` "Scripts"
    requests = None

VERSION = store.VERSION

ALLOWED_SCHEMES = ("http", "https", "file")
DEFAULT_INTERVAL = 1.0          # seconds between requests to the same host
HOST_INTERVAL = {
    "eutils.ncbi.nlm.nih.gov": 0.34,
    "pmc.ncbi.nlm.nih.gov": 1.0,
    "www.ncbi.nlm.nih.gov": 1.0,
    "pubmed.ncbi.nlm.nih.gov": 1.0,
}
MAX_BYTES = 32 * 1024 * 1024    # refuse absurd bodies rather than snapshot them
DEFAULT_READ_WINDOW = 4000      # characters, when `read` is given no explicit range
MAX_READ_WINDOW = 20000         # hard ceiling on one `read` window


class FetchError(StoreError):
    """Acquisition failed. No snapshot and no event are written."""


class PolicyError(StoreError):
    """The request is refused by policy (scheme, credentials, content type)."""


# ------------------------------------------------------------------- http ------


class Http:
    """Polite HTTP: identifying User-Agent, per-host rate limit, bounded retries.

    Deliberately has no hook for authentication, cookies or proxies.
    """

    def __init__(self, email: str | None = None, timeout: int = 60):
        ua = "%s (+https://pubmed.ncbi.nlm.nih.gov/; literature-review agent" % VERSION
        ua += "; mailto:%s)" % email if email else ")"
        self.headers = {"User-Agent": ua, "Accept": "*/*"}
        self.timeout = timeout
        self._last: dict[str, float] = {}
        self.session = requests.Session() if requests else None
        if self.session:
            self.session.headers.update(self.headers)
            self.session.trust_env = False   # ignore ambient proxies and netrc

    def _wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        interval = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
        last = self._last.get(host)
        if last is not None:
            delta = time.monotonic() - last
            if delta < interval:
                time.sleep(interval - delta)
        self._last[host] = time.monotonic()

    def get(self, url: str, *, retries: int = 2):
        if self.session is None:
            raise FetchError("requests is unavailable; cannot fetch %s" % url)
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            self._wait(url)
            try:
                resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            except Exception as exc:                       # network-level
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                last_err = FetchError("HTTP %d" % resp.status_code)
                time.sleep(2.0 * (attempt + 1))
                continue
            return resp
        raise FetchError("request failed: %s (%s)" % (url, last_err))


def check_url_policy(url: str) -> str:
    """Refuse anything the plans' Non-Goals forbid. Returns the URL scheme."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise PolicyError("refused scheme %r; only %s are permitted"
                          % (scheme or url[:40], ", ".join(ALLOWED_SCHEMES)))
    if parts.username or parts.password or "@" in (parts.netloc.split("/")[0] or ""):
        raise PolicyError("refused: the URL carries embedded credentials. No credentials, "
                          "proxies or paywall circumvention (SKILL.md invariant 9).")
    return scheme


# ----------------------------------------------------------------- html/text ---


class _TextHTML(HTMLParser):
    """Minimal, dependency-free HTML-to-text: drops script/style, keeps block breaks."""

    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "table", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title and self.title is None:
            t = data.strip()
            if t:
                self.title = t
        if self._skip:
            return
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> tuple[str, str | None]:
    parser = _TextHTML()
    try:
        parser.feed(html)
        parser.close()
    except Exception:                                    # malformed markup
        pass
    return parser.text(), parser.title


def _decode(body: bytes, content_type: str) -> str:
    charset = None
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    if not charset:
        m = re.search(rb'charset=["\']?([\w\-]+)', body[:4096], re.I)
        if m:
            charset = m.group(1).decode("ascii", "replace")
    for enc in [charset, "utf-8", "latin-1"]:
        if not enc:
            continue
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def pdf_to_text(pdf: Path) -> tuple[str, str]:
    """`pdftotext -layout`, falling back to pdfminer. Returns (text, tool)."""
    try:
        proc = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                              capture_output=True, timeout=300)
        if proc.returncode == 0:
            text = proc.stdout.decode("utf-8", "replace")
            if text.strip():
                return text, "pdftotext -layout"
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from pdfminer.high_level import extract_text     # type: ignore
    except ImportError:
        return "", "none"
    try:
        return extract_text(str(pdf)) or "", "pdfminer"
    except Exception as exc:                             # pragma: no cover
        raise FetchError("pdfminer failed on %s: %s" % (pdf, exc)) from exc


# ------------------------------------------------------------------- spans -----

_WS_RE = re.compile(r"\s")


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Case-folded, whitespace-collapsed view of `text`, plus norm-index -> orig-index.

    Only two transformations, both reversible through the map: runs of whitespace become a
    single space, and each character is lowercased. The snapshot itself is never modified —
    R10 forbids normalizing stored text, so this view exists purely to find candidates, and
    every offset returned is an index into the original `text`.
    """
    out: list[str] = []
    idx: list[int] = []
    prev_ws = False
    for i, ch in enumerate(text):
        if _WS_RE.match(ch):
            if not prev_ws:
                out.append(" ")
                idx.append(i)
            prev_ws = True
            continue
        prev_ws = False
        low = ch.lower()
        for c in low:
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def _expand(text: str, start: int, end: int, context: int) -> tuple[int, int]:
    """Widen a match to nearby sentence/paragraph edges, capped at MAX_SPAN_CHARS."""
    budget = min(MAX_SPAN_CHARS, (end - start) + 2 * context)
    lo = max(0, start - context)
    hi = min(len(text), end + context)
    left = text.rfind("\n", lo, start)
    if left == -1:
        left = max((text.rfind(p, lo, start) for p in (". ", "? ", "! ")), default=-1)
        left = left + 2 if left != -1 else lo
    else:
        left += 1
    right = text.find("\n", end, hi)
    if right == -1:
        cands = [text.find(p, end, hi) for p in (". ", "? ", "! ")]
        cands = [c + 1 for c in cands if c != -1]
        right = min(cands) if cands else hi
    left = min(left, start)
    right = max(right, end)
    if right - left > budget:
        right = min(right, left + budget)
        if right < end:
            left, right = max(0, end - budget), end
    return left, right


def find_spans(text: str, query: str, *, max_results: int = 5,
               context: int = 200) -> list[dict]:
    """Deterministic candidate spans for `query` inside `text`.

    Method, in order:

    1. **Exact match on a normalized view.** The query and the text are compared with runs
       of whitespace collapsed to one space and characters lowercased; every occurrence is
       returned, mapped back to offsets in the original text. `method: "exact"`, score 1.0.
    2. **Window scoring**, only when step 1 finds nothing. The text is cut into overlapping
       windows; each is scored by the fraction of the query's distinct ≥3-character tokens
       it contains, plus half its `difflib` similarity ratio to the query. The best
       non-overlapping windows are returned. `method: "fuzzy"`, score < 1.0.

    Honest limits: this is lexical, not semantic. No stemming, no synonyms, no
    transliteration, no Unicode normalization (R10 forbids normalizing the stored text, so
    a query typed in NFC will not match text stored in NFD). Hyphenation across a line
    break, ligatures and PDF column interleaving all defeat step 1. Numbers and symbols are
    matched literally. Step 2 is a ranking heuristic and can return windows that do not
    support the claim at all — a returned span is a *place to read*, never evidence that
    the claim is true. The caller must read the window and choose the real offsets. Results
    are deterministic: identical inputs give identical output, ordering included.
    """
    if not text or not query or not query.strip():
        return []
    norm, idx = _normalize_with_map(text)
    qnorm, _ = _normalize_with_map(query)
    qnorm = qnorm.strip()
    out: list[dict] = []
    if qnorm:
        pos = norm.find(qnorm)
        while pos != -1 and len(out) < max_results:
            start = idx[pos]
            end = idx[pos + len(qnorm) - 1] + 1
            lo, hi = _expand(text, start, end, context)
            out.append({"start": lo, "end": hi, "match_start": start, "match_end": end,
                        "score": 1.0, "method": "exact",
                        "excerpt": text[lo:hi]})
            pos = norm.find(qnorm, pos + max(1, len(qnorm)))
    if out:
        return out

    tokens = {t for t in re.findall(r"[0-9a-z]+", qnorm) if len(t) >= 3}
    if not tokens:
        return []
    size = max(240, min(MAX_SPAN_CHARS, 3 * len(qnorm)))
    step = max(60, size // 2)
    scored: list[tuple[float, int, int]] = []
    for pos in range(0, max(1, len(norm) - 1), step):
        window = norm[pos:pos + size]
        if not window:
            break
        hits = sum(1 for t in tokens if t in window)
        if not hits:
            continue
        ratio = SequenceMatcher(None, qnorm, window, autojunk=False).ratio()
        scored.append((round(hits / len(tokens) + 0.5 * ratio, 6), pos, min(pos + size, len(norm))))
    scored.sort(key=lambda t: (-t[0], t[1]))
    taken: list[tuple[int, int]] = []
    for score, npos, nend in scored:
        if len(out) >= max_results:
            break
        start = idx[npos]
        end = idx[min(nend, len(idx)) - 1] + 1
        if any(start < b and a < end for a, b in taken):
            continue
        lo, hi = _expand(text, start, min(end, start + MAX_SPAN_CHARS), 0)
        taken.append((lo, hi))
        out.append({"start": lo, "end": hi, "match_start": start, "match_end": end,
                    "score": score, "method": "fuzzy", "excerpt": text[lo:hi]})
    return out


# --------------------------------------------------------------------- fetch ---


def _read_file_url(url: str) -> tuple[bytes, str, Path]:
    parts = urlsplit(url)
    if parts.netloc and parts.netloc not in ("", "localhost"):
        raise PolicyError("refused remote file:// host %r" % parts.netloc)
    path = Path(unquote(parts.path)).expanduser()
    if not path.is_file():
        raise FetchError("no such file: %s" % path)
    body = path.read_bytes()
    ext = path.suffix.lower()
    ctype = {".html": "text/html", ".htm": "text/html", ".xml": "application/xml",
             ".json": "application/json", ".pdf": "application/pdf"}.get(ext, "text/plain")
    return body, ctype, path


def acquire(url: str, *, http: Http | None = None) -> dict:
    """One retrieval. Returns `{text, title, content_type, status, bytes, final_url}`.

    Refuses PDFs: they belong in `<wiki>/assets/papers/` via `library.py add`, then
    `source.py local` (R13).
    """
    scheme = check_url_policy(url)
    if scheme == "file":
        body, ctype, path = _read_file_url(url)
        status, final_url = 200, url
    else:
        resp = (http or Http()).get(url)
        status = resp.status_code
        if status >= 400:
            raise FetchError("HTTP %d for %s" % (status, url))
        body = resp.content
        ctype = (resp.headers.get("Content-Type") or "").lower()
        final_url = resp.url
    if len(body) > MAX_BYTES:
        raise FetchError("body is %d bytes, over the %d-byte ceiling" % (len(body), MAX_BYTES))
    if "pdf" in ctype or body[:5] == b"%PDF-":
        raise PolicyError(
            "refused: %s is a PDF. PDFs are never copied per run (R13). File it with "
            "`library.py add --wiki <wiki> --pdf <file>`, then ingest it with "
            "`source.py local --run-dir <run> --pdf <library path>`." % url)

    text_body = _decode(body, ctype)
    title = None
    if "html" in ctype or re.match(r"\s*<(!doctype html|html)", text_body[:200], re.I):
        text, title = html_to_text(text_body)
    else:
        text = text_body
    return {"text": text, "title": title, "content_type": ctype or None,
            "status": status, "bytes": len(body), "final_url": final_url}


# ----------------------------------------------------------------- commands ----

def _paper(args) -> dict | None:
    if getattr(args, "no_paper", False):
        return None
    trio = {"pmid": getattr(args, "pmid", None), "doi": getattr(args, "doi", None),
            "pmcid": getattr(args, "pmcid", None)}
    return trio


def cmd_fetch(args) -> int:
    got = acquire(args.url, http=Http(email=args.email, timeout=args.timeout))
    if not got["text"].strip() and not args.allow_empty:
        raise FetchError("%s yielded no text; refusing to snapshot an empty body "
                         "(pass --allow-empty to override)" % args.url)
    url = args.url if args.keep_requested_url else got["final_url"]
    out = store.write_snapshot_result(
        args.run_dir, url=url, text=got["text"],
        title=args.title if args.title is not None else got["title"],
        access=args.access, origin=args.origin, paper=_paper(args), asset=None,
        event_type="fetch", fresh=bool(args.fresh), actor=args.actor,
        detail=args.detail or ("%s, %d chars, http %s"
                               % (got["content_type"] or "unknown type",
                                  len(got["text"]), got["status"])))
    snap = out["snapshot"]
    return _emit({"ok": True, "source_id": snap["source_id"], "created": out["created"],
                  "url": snap["url"], "title": snap["title"], "access": snap["access"],
                  "origin": snap["origin"], "chars": len(snap["text"]),
                  "content_hash": snap["content_hash"],
                  "fresh": bool(out["event"] and out["event"]["fresh"]),
                  "event_id": out["event"]["event_id"] if out["event"] else None,
                  "status": got["status"], "content_type": got["content_type"]})


def cmd_read(args) -> int:
    st = Store(args.run_dir, wiki_root=args.wiki)
    snap = st.read_snapshot(args.source_id)
    length = len(snap["text"])
    start = args.start or 0
    if args.end is not None:
        end = args.end
    else:
        end = min(length, start + max(1, args.window))
    if end - start > MAX_READ_WINDOW:
        raise SchemaError("window [%d, %d) is %d characters, over the %d-character read "
                          "ceiling; read it in pieces"
                          % (start, end, end - start, MAX_READ_WINDOW))
    if start < 0 or start >= end or end > length:
        raise store.SpanRangeError(
            "window [%d, %d) out of range for a %d-character snapshot" % (start, end, length))
    text = snap["text"][start:end]
    st.append_event({"type": "read", "source_id": snap["source_id"], "url": snap["url"],
                     "fresh": False, "sha256": store.sha256_text(snap["text"]),
                     "actor": args.actor, "detail": "window %d-%d" % (start, end)})
    return _emit({"ok": True, "source_id": snap["source_id"], "access": snap["access"],
                  "origin": snap["origin"], "title": snap["title"], "url": snap["url"],
                  "paper": snap["paper"], "chars": length, "start": start, "end": end,
                  "eof": end >= length, "next_start": end if end < length else None,
                  "text": text,
                  "note": "offsets are character offsets into the snapshot text (R10); "
                          "cite start/end, never retyped prose"})


def cmd_spans(args) -> int:
    st = Store(args.run_dir, wiki_root=args.wiki)
    snap = st.read_snapshot(args.source_id)
    cands = find_spans(snap["text"], args.query, max_results=args.max,
                       context=args.context)
    st.append_event({"type": "read", "source_id": snap["source_id"], "url": snap["url"],
                     "fresh": False, "sha256": store.sha256_text(snap["text"]),
                     "actor": args.actor,
                     "detail": "spans query %r, %d candidates" % (args.query[:80], len(cands))})
    return _emit({"ok": True, "source_id": snap["source_id"], "access": snap["access"],
                  "chars": len(snap["text"]), "query": args.query,
                  "count": len(cands), "candidates": cands,
                  "method": "normalized exact match, else window scoring; lexical only, "
                            "no stemming or semantics — read the window before citing it",
                  "note": "end is exclusive; a cited span must be <= %d characters"
                          % MAX_SPAN_CHARS})


def _cmd_local_refmgr(args, pdf: Path) -> int:
    """`source.py local --repo ... --attachment-id ...`: ingest a PDF already staged
    into the shared refmgr attachment pool (`registry.py add-pdf`/`import-folder`),
    recording `asset.refmgr_paper_id`/`refmgr_attachment_id` instead of a
    wiki-relative path (schema §10 Phase 5: "connect extractions to exact
    attachment/snapshot versions"). The given `--pdf` must be the exact file already
    registered as that attachment -- this never stages new bytes into refmgr itself,
    only ingests text from bytes it already holds."""
    import refmgr.service as _refmgr_service_mod
    repo_root = Path(args.repo).expanduser().resolve()
    service = _refmgr_service_mod.ReferenceManagerService(repo_root / "data" / "refmgr")
    try:
        row = service.conn.execute(
            "SELECT * FROM attachments WHERE id = ? AND deleted_at IS NULL",
            (args.attachment_id,)).fetchone()
        if row is None:
            raise FetchError(
                "no refmgr attachment %r in %s -- register it first with "
                "`registry.py add-pdf`" % (args.attachment_id, repo_root))
        paper_id, asset_sha256 = row["paper_id"], row["asset_sha256"]
        digest = sha256_file(pdf)
        if digest != asset_sha256:
            raise FetchError(
                "%s (sha256 %s) does not match refmgr attachment %s's recorded asset "
                "%s -- pass the exact file registered via `registry.py add-pdf`"
                % (pdf, digest[:16], args.attachment_id, asset_sha256[:16]))
        asset_row = service.assets.get(asset_sha256)
        stored_path = repo_root / "data" / "refmgr" / asset_row["storage_path"]
        nbytes = pdf.stat().st_size
        text, tool = pdf_to_text(pdf)
        if not text.strip() and not args.allow_empty:
            raise FetchError(
                "no text extracted from %s (tried pdftotext -layout, then pdfminer); a "
                "scanned PDF needs OCR, or pass --allow-empty" % pdf)
        url = args.url or ("file://" + stored_path.as_posix())
        out = store.write_snapshot_result(
            args.run_dir, url=url, text=text, title=args.title, access=args.access,
            origin="user-supplied-pdf", paper=_paper(args),
            asset={"path": None, "sha256": digest, "bytes": nbytes,
                  "refmgr_paper_id": paper_id, "refmgr_attachment_id": args.attachment_id},
            event_type="local_pdf", fresh=True, actor=args.actor,
            detail=args.detail or ("user-supplied pdf via %s; %d chars, %d bytes, refmgr "
                                   "attachment %s" % (tool, len(text), nbytes,
                                                       args.attachment_id)))
    finally:
        service.close()
    snap = out["snapshot"]
    st = Store(args.run_dir, repo_root=repo_root)
    fresh = st.freshness(snap["source_id"])
    return _emit({"ok": True, "source_id": snap["source_id"], "created": out["created"],
                  "url": snap["url"], "asset": snap["asset"], "chars": len(snap["text"]),
                  "extractor": tool, "access": snap["access"], "origin": snap["origin"],
                  "content_hash": snap["content_hash"], "fresh": fresh["fresh"],
                  "fresh_reason_code": fresh["reason_code"]})


def cmd_local(args) -> int:
    pdf = Path(args.pdf).expanduser().resolve()
    if not pdf.is_file():
        raise FetchError("no such PDF: %s" % pdf)
    if args.repo:
        if not args.attachment_id:
            raise FetchError("--attachment-id is required with --repo (the refmgr "
                             "attachment this PDF was staged as via `registry.py add-pdf`)")
        return _cmd_local_refmgr(args, pdf)
    wiki = Path(args.wiki).expanduser().resolve() if args.wiki else wiki_root_for_run(args.run_dir)
    try:
        rel = pdf.relative_to(wiki)
    except ValueError:
        raise PolicyError(
            "%s is outside the wiki root %s. PDFs live in the shared library and are never "
            "copied per run (R13): file it first with `library.py add --wiki %s --pdf %s`, "
            "then re-run this command against the library path."
            % (pdf, wiki, wiki, pdf)) from None
    digest = sha256_file(pdf)
    nbytes = pdf.stat().st_size
    text, tool = pdf_to_text(pdf)
    if not text.strip() and not args.allow_empty:
        raise FetchError("no text extracted from %s (tried pdftotext -layout, then pdfminer); "
                         "a scanned PDF needs OCR via `library.py`, or pass --allow-empty"
                         % rel.as_posix())
    url = args.url or ("file:///" + rel.as_posix())
    out = store.write_snapshot_result(
        args.run_dir, url=url, text=text, title=args.title, access=args.access,
        origin="user-supplied-pdf", paper=_paper(args),
        asset={"path": rel.as_posix(), "sha256": digest, "bytes": nbytes},
        event_type="local_pdf", fresh=True, actor=args.actor,
        detail=args.detail or ("user-supplied pdf via %s; %d chars, %d bytes, asset hashed"
                               % (tool, len(text), nbytes)))
    snap = out["snapshot"]
    st = Store(args.run_dir, wiki_root=wiki)
    fresh = st.freshness(snap["source_id"])
    return _emit({"ok": True, "source_id": snap["source_id"], "created": out["created"],
                  "url": snap["url"], "asset": snap["asset"], "chars": len(snap["text"]),
                  "extractor": tool, "access": snap["access"], "origin": snap["origin"],
                  "content_hash": snap["content_hash"],
                  "event_id": out["event"]["event_id"] if out["event"] else None,
                  "fresh": fresh["fresh"], "fresh_detail": fresh["detail"]})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="source.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch", help="retrieve a URL and write an immutable snapshot",
                       description="One polite GET (http/https/file), text extraction, "
                                   "snapshot, fetch event. No credentials, no proxies, "
                                   "no browser automation, no PDFs (use `local`).")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--url", required=True)
    s.add_argument("--fresh", action="store_true",
                   help="log fresh: true — a real retrieval performed for this run (R15)")
    s.add_argument("--access", default="web", choices=list(store.ACCESS_VALUES))
    s.add_argument("--origin", default="web", choices=list(store.ORIGIN_VALUES))
    s.add_argument("--title", default=None, help="override the title read from the document")
    s.add_argument("--pmid")
    s.add_argument("--doi")
    s.add_argument("--pmcid")
    s.add_argument("--no-paper", action="store_true", dest="no_paper",
                   help="record paper: null (a source with no bibliographic identity)")
    s.add_argument("--keep-requested-url", action="store_true", dest="keep_requested_url",
                   help="snapshot the requested URL rather than the post-redirect URL")
    s.add_argument("--allow-empty", action="store_true", dest="allow_empty")
    s.add_argument("--email", help="contact address for the User-Agent")
    s.add_argument("--timeout", type=int, default=60)
    s.add_argument("--actor", default="main")
    s.add_argument("--detail", default=None)
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("read", help="a bounded, verified text window from a snapshot",
                       description="Verifies the snapshot on the way out and logs a `read` "
                                   "event. Offsets are character offsets (R10).")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--source-id", required=True, dest="source_id")
    s.add_argument("--start", type=int)
    s.add_argument("--end", type=int, help="exclusive")
    s.add_argument("--window", type=int, default=DEFAULT_READ_WINDOW,
                   help="characters to return when --end is omitted (default %d, max %d)"
                        % (DEFAULT_READ_WINDOW, MAX_READ_WINDOW))
    s.add_argument("--wiki")
    s.add_argument("--actor", default="main")
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("spans", help="locate candidate spans for a claim",
                       description="Deterministic lexical search: normalized exact match, "
                                   "else window scoring. Returns {start, end, excerpt} "
                                   "candidates so extractors cite offsets instead of "
                                   "transcribing text. Not semantic — read before citing.")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--source-id", required=True, dest="source_id")
    s.add_argument("--query", required=True)
    s.add_argument("--max", type=int, default=5, help="maximum candidates (default 5)")
    s.add_argument("--context", type=int, default=200,
                   help="characters of context around an exact match (default 200)")
    s.add_argument("--wiki")
    s.add_argument("--actor", default="main")
    s.set_defaults(func=cmd_spans)

    s = sub.add_parser("local", help="ingest a user-supplied PDF from the shared library",
                       description="Hashes the bytes, extracts text with `pdftotext "
                                   "-layout` (pdfminer fallback), snapshots it as "
                                   "origin: user-supplied-pdf, and logs a fresh `local_pdf` "
                                   "event with the asset hash (R13, R15 exception).")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--pdf", required=True,
                   help="path inside <wiki>/assets/papers/, or (with --repo) the exact "
                        "file already registered as --attachment-id")
    root_group = s.add_mutually_exclusive_group()
    root_group.add_argument("--wiki", help="wiki root (default: inferred from --run-dir)")
    root_group.add_argument("--repo", help="repo root: ingest via the refmgr attachment "
                                           "pool instead of the wiki-relative library "
                                           "(requires --attachment-id)")
    s.add_argument("--attachment-id", dest="attachment_id",
                   help="refmgr attachment id from `registry.py add-pdf`'s output "
                        "(required with --repo)")
    s.add_argument("--title", default=None)
    s.add_argument("--access", default="full_text", choices=list(store.ACCESS_VALUES))
    s.add_argument("--pmid")
    s.add_argument("--doi")
    s.add_argument("--pmcid")
    s.add_argument("--no-paper", action="store_true", dest="no_paper")
    s.add_argument("--url", help="override the recorded file:// URL")
    s.add_argument("--allow-empty", action="store_true", dest="allow_empty")
    s.add_argument("--actor", default="main")
    s.add_argument("--detail", default=None)
    s.set_defaults(func=cmd_local)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except StoreError as exc:
        return _emit(exc.to_json(), code=2)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__,
                      "reason_code": None}, code=2)


if __name__ == "__main__":
    sys.exit(main())
