"""Czech adapter: consolidated laws from e-Sbírka, split into § / odst. / písm. / bod.

e-Sbírka (https://e-sbirka.gov.cz, formerly www.e-sbirka.cz) is the official
electronic Collection of Laws of the Czech Republic. Its public web app reads a
JSON API under ``/sbr-cache`` that needs no key or registration:

``GET /sbr-cache/dokumenty-sbirky/{staleUrl}``
    Act metadata. ``staleUrl`` is the URL-encoded act path, e.g.
    ``/sb/2025/264`` (current consolidated version) or ``/sb/2025/264/2025-11-01``
    (version in force from that date; ``0000-00-00`` is the text as promulgated).
    The response names the dated ``staleUrl`` and ``eli`` of the version.

``GET /sbr-cache/dokumenty-sbirky/{staleUrl}/fragmenty?cisloStranky=N``
    The whole text as a flat, document-ordered list of *fragments* (``seznam``),
    1000 per page, pages ``0 .. pocetStranek-1``. Each fragment has a type
    (``kodTypuFragmentu``: ``Paragraf``, ``Odstavec_Dc``, ``Pismeno_Lb``,
    ``Bod_Dd``, ``Clanek``, ``Cast``, ``Hlava``, ``Dil``, ``Nadpis_pod`` ...),
    an XHTML snippet with the printed number in ``<var>``, a depth, an ELI path
    such as ``/eli/cz/sb/2025/264/2025-11-01/dokument/norma/cast_1/hlava_1/par_1/odst_1``
    and a ``staleUrl`` with the web anchor (``/sb/2025/264/2025-11-01#par_1-odst_1``).

The ELI path gives the hierarchy. Numbered segments (``par_12``, ``odst_3``,
``pism_b``, ``bod_1``, ``cl_1``, ``priloha_1``, ``cast_1`` ...) become
provisions; unnumbered ones (``frag_<id>``: headings, a lone unnumbered
paragraph, continuation text after a list, tables, text quoted by an amending
act) are folded into their nearest numbered ancestor as heading or text.

Provision IDs are anchored at the § / článek, so ``§ 12 odst. 3 písm. b)`` of
Act 264/2025 Sb. is ``cz:264/2025:par12.odst3.pism_b`` whatever part or chapter
it sits in. Structural units keep their full path (``cz:264/2025:cast1.hlava2.dil1``).

Bulk alternative: https://opendata.eselpoint.gov.cz/datove-sady-esbirka/ has
nightly dumps of the whole collection (fragments of all versions: ~1.2 GB gzip),
which is the better route for mass processing; this adapter fetches one act.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from ..cellar import USER_AGENT
from ..provisions import Document, Provision, make_id, make_segment, normalize_number

BASE_URL = "https://e-sbirka.gov.cz"
API_URL = BASE_URL + "/sbr-cache"

SOURCE = "cz"
LANGUAGE = "cs"

Fetcher = Callable[[str], dict]

# ELI path segments that only group the document; they never become provisions.
CONTAINERS = {"dokument", "norma", "novela", "prilohy", "poznamkypodcarou"}
# Parts of the document we do not turn into provisions (title block, signatures).
SKIPPED_ROOTS = {"prefix", "postfix"}
# Structural units: IDs keep the full structural path.
STRUCTURAL = {"cast", "hlava", "dil", "oddil", "pododdil"}
# Citable anchors: IDs start here, dropping the structural units above them.
ANCHORS = {"par", "cl", "priloha", "ppc"}

_REF = re.compile(
    r"^(?:č\.\s*)?(?P<num>\d+[a-z]?)\s*/\s*(?P<year>\d{4})(?:\s*Sb\.?)?$", re.IGNORECASE
)
_PATH = re.compile(
    r"(?:^|/)(?P<coll>sb)/(?P<year>\d{4})/(?P<num>\d+[a-z]?)(?:/(?P<ver>\d{4}-\d{2}-\d{2}))?(?:[/#?]|$)"
)
_LABEL = re.compile(r"^\s*(„?)\s*<var>(.*?)</var>", re.DOTALL)
_FOOTNOTE_REF = re.compile(r"<sup>[^<]*</sup>\s*\)")
_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --- references ---------------------------------------------------------------


@dataclass(frozen=True)
class ActRef:
    """An act in the Collection of Laws (Sbírka zákonů), optionally a dated version."""

    year: int
    number: str
    version: str | None = None  # "YYYY-MM-DD" in force from; "0000-00-00" = as promulgated
    collection: str = "sb"

    @property
    def act_id(self) -> str:
        """National reference as used in OpenAcquis IDs and CELLAR: ``"264/2025"``."""
        return f"{self.number}/{self.year}"

    @property
    def stale_url(self) -> str:
        base = f"/{self.collection}/{self.year}/{self.number}"
        return f"{base}/{self.version}" if self.version else base

    @property
    def web_url(self) -> str:
        return BASE_URL + self.stale_url


def parse_reference(ref: str, version: str | None = None) -> ActRef:
    """Parse ``"264/2025"``, ``"č. 264/2025 Sb."``, ``"/sb/2025/264"``, an ELI or an e-Sbírka URL.

    ``version`` (``"YYYY-MM-DD"``) overrides a version date found in ``ref``.
    """
    s = ref.strip()
    m = _REF.match(s)
    if m:
        out = ActRef(year=int(m["year"]), number=m["num"].lower())
    else:
        m = _PATH.search(urllib.parse.unquote(s))
        if not m:
            raise ValueError(
                f"{ref!r} is not a Czech Collection of Laws reference (expected e.g. '264/2025')"
            )
        out = ActRef(year=int(m["year"]), number=m["num"].lower(), version=m["ver"])
    if version is not None:
        if not _VERSION.match(version):
            raise ValueError(f"version must be YYYY-MM-DD, got {version!r}")
        out = ActRef(out.year, out.number, version, out.collection)
    return out


# --- HTTP with cache and rate limit -------------------------------------------


def default_cache_dir() -> Path:
    """``$OPENACQUIS_CACHE/esbirka``, else ``~/.cache/openacquis/esbirka``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    base = Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"
    return base / "esbirka"


class HttpFetcher:
    """GET JSON with a descriptive User-Agent, a delay between requests and an on-disk cache.

    ``cache_dir=False`` disables the cache. Cached responses older than ``ttl``
    seconds are refetched. Errors reported by e-Sbírka (``{"chyby": [...]}``)
    raise :class:`LookupError`.
    """

    def __init__(
        self,
        cache_dir: Path | str | None | bool = None,
        ttl: float = 24 * 3600,
        delay: float = 1.0,
        timeout: float = 60.0,
        user_agent: str = USER_AGENT,
    ):
        if cache_dir is False:
            self.cache_dir: Path | None = None
        else:
            self.cache_dir = Path(cache_dir) if cache_dir not in (None, True) else default_cache_dir()
        self.ttl = ttl
        self.delay = delay
        self.timeout = timeout
        self.user_agent = user_agent
        self._last = 0.0
        self.requests = 0  # network requests actually made

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")

    def __call__(self, url: str) -> dict:
        path = self._cache_path(url)
        if path is not None and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return json.loads(path.read_text(encoding="utf-8"))
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": self.user_agent}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise LookupError(f"e-Sbírka {e.code} for {url}: {_error_text(body)}") from e
        finally:
            self._last = time.monotonic()
            self.requests += 1
        data = json.loads(raw)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)
        return data


def _error_text(body: str) -> str:
    try:
        errs = json.loads(body).get("chyby") or []
        return "; ".join(f"{e.get('kod')}: {e.get('popis')}" for e in errs) or body[:200]
    except (ValueError, AttributeError):
        return body[:200]


# --- client -------------------------------------------------------------------


class ESbirkaClient:
    """Fetch an act's consolidated text from e-Sbírka. Pass ``fetch`` (url -> JSON) to stub the network."""

    def __init__(self, fetch: Fetcher | None = None):
        self._fetch = fetch or HttpFetcher()

    def _url(self, stale_url: str, suffix: str = "") -> str:
        return f"{API_URL}/dokumenty-sbirky/{urllib.parse.quote(stale_url, safe='')}{suffix}"

    def metadata(self, ref: str | ActRef, version: str | None = None) -> dict:
        """Metadata of the act version (current consolidated version by default)."""
        r = ref if isinstance(ref, ActRef) else parse_reference(ref, version)
        meta = self._fetch(self._url(r.stale_url))
        if "chyby" in meta:
            raise LookupError(f"e-Sbírka: {_error_text(json.dumps(meta))}")
        return meta

    def fragments(self, stale_url: str) -> list[dict]:
        """All fragments of a dated version (``/sb/2025/264/2025-11-01``), every page."""
        out: list[dict] = []
        page, pages = 0, 1
        while page < pages:
            data = self._fetch(self._url(stale_url, f"/fragmenty?cisloStranky={page}"))
            if "chyby" in data:
                raise LookupError(f"e-Sbírka: {_error_text(json.dumps(data))}")
            out.extend(data.get("seznam", []))
            pages = int(data.get("pocetStranek") or 1)
            page += 1
        return out

    def document(self, ref: str | ActRef, version: str | None = None) -> Document:
        """Fetch and parse an act into a :class:`Document` of provisions."""
        r = ref if isinstance(ref, ActRef) else parse_reference(ref, version)
        meta = self.metadata(r)
        stale = meta.get("staleUrl") or r.stale_url
        return parse_fragments(self.fragments(stale), meta, act_id=r.act_id)


def fetch_act(ref: str, version: str | None = None, fetch: Fetcher | None = None) -> Document:
    """Convenience wrapper: ``fetch_act("264/2025")`` -> current consolidated text."""
    return ESbirkaClient(fetch).document(ref, version)


# --- XHTML to text --------------------------------------------------------------


class _TextExtractor(HTMLParser):
    """Plain text from e-Sbírka XHTML snippets; tables become ``a | b`` rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._cells = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "br":
            self.parts.append("\n")
        elif tag == "tr":
            self.parts.append("\n")
            self._cells = 0
        elif tag in ("td", "th"):
            if self._cells:
                self.parts.append(" | ")
            self._cells += 1
        elif tag in ("p", "div", "li"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts).replace("\xa0", " ")
        lines = (re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.split("\n"))
        return "\n".join(ln for ln in lines if ln)


def html_text(xhtml: str | None) -> str:
    """Plain text of an XHTML snippet (entities decoded, whitespace normalised)."""
    if not xhtml:
        return ""
    p = _TextExtractor()
    p.feed(xhtml)
    p.close()
    return p.text()


def split_label(xhtml: str | None) -> tuple[str | None, str]:
    """Split a fragment into its printed number (leading ``<var>``) and body text.

    Footnote reference markers (``<sup>2</sup>)``) are dropped from the body.

    >>> split_label('<var>(1)</var> Tento zákon upravuje')
    ('(1)', 'Tento zákon upravuje')
    """
    if not xhtml:
        return None, ""
    m = _LABEL.match(xhtml)
    if not m:
        return None, html_text(_FOOTNOTE_REF.sub("", xhtml))
    label = (m.group(1) + html_text(m.group(2))).strip() or None
    return label, html_text(_FOOTNOTE_REF.sub("", xhtml[m.end():]))


# --- fragments to provisions ------------------------------------------------------


def _segments(eli: str) -> list[str] | None:
    """ELI path segments below ``/dokument``, without grouping containers.

    ``None`` for fragments outside the normative text (title block, signatures).
    """
    _, sep, rest = eli.partition("/dokument")
    if not sep:
        return None
    segs = [s for s in rest.split("/") if s]
    if segs and segs[0] in SKIPPED_ROOTS:
        return None
    return [s for s in segs if s not in CONTAINERS]


def _split_segment(seg: str) -> tuple[str, str]:
    kind, _, num = seg.partition("_")
    return kind, num


def _append(p: Provision, text: str) -> None:
    if text:
        p.text = f"{p.text}\n{text}" if p.text else text


def parse_fragments(
    fragments: Iterable[dict], meta: dict | None = None, act_id: str | None = None
) -> Document:
    """Turn e-Sbírka fragments (document order) into a :class:`Document`.

    ``meta`` is the act metadata response; ``act_id`` (``"264/2025"``) is taken
    from it when not given. ``Document.date`` is the date the version is in
    force from; ``Document.eli`` is the version's ELI path.
    """
    meta = meta or {}
    if act_id is None:
        code = meta.get("kodDokumentuSbirky") or ""  # "264/2025 Sb."
        m = re.match(r"(\d+[a-z]?/\d{4})", code)
        if not m:
            raise ValueError("act_id not given and not found in metadata")
        act_id = m.group(1)
    stale = meta.get("staleUrl")
    doc = Document(
        source=SOURCE,
        act_id=act_id,
        language=LANGUAGE,
        title=meta.get("uplnaCitace") or meta.get("nazev"),
        date=meta.get("datumUcinnostiZneniOd") or meta.get("datumUcinnostiOd"),
        eli=meta.get("eli"),
        url=BASE_URL + stale if stale else None,
    )

    by_key: dict[tuple[str, ...], Provision] = {}
    pending_heading: str | None = None
    own_heading: set[str] = set()
    quote_depth: int | None = None  # depth of an open Block_Citace (text quoted by an amendment)

    def owner(key: tuple[str, ...]) -> Provision | None:
        for i in range(len(key), 0, -1):
            p = by_key.get(key[:i])
            if p is not None:
                return p
        return None

    for f in fragments:
        ftype = f.get("kodTypuFragmentu") or ""
        depth = f.get("hloubka") or 0
        segs = _segments(f.get("eli") or "")
        if quote_depth is not None and depth <= quote_depth:
            quote_depth = None
        if segs is None or not segs or ftype.startswith("Virtual_"):
            continue
        numbered = tuple(s for s in segs if not s.startswith("frag_"))

        if quote_depth is not None:  # quoted text of another act: keep it as text, labels included
            p = owner(numbered)
            if p is not None:
                _append(p, html_text(_FOOTNOTE_REF.sub("", f.get("xhtml") or "")))
            continue
        if ftype == "Block_Citace":
            quote_depth = depth
            continue

        if segs[-1].startswith("frag_"):
            text = html_text(_FOOTNOTE_REF.sub("", f.get("xhtml") or ""))
            if not text:
                continue  # Block_* wrappers
            if ftype == "Nadpis_nad":  # heading placed above the next § / článek
                pending_heading = text
                continue
            p = owner(numbered)
            if p is None:
                continue
            if ftype == "Nadpis_pod":
                # own heading replaces one inherited from Nadpis_nad; a second line is appended
                p.heading = f"{p.heading} {text}" if p.id in own_heading else text
                own_heading.add(p.id)
            else:
                _append(p, text)
            continue

        # numbered fragment -> provision
        kind, num = _split_segment(segs[-1])
        anchor = next((i for i, s in enumerate(numbered) if _split_segment(s)[0] in ANCHORS), None)
        parent = owner(numbered[:-1])
        direct_parent = by_key.get(numbered[:-1])
        if anchor is not None and anchor < len(numbered) - 1 and direct_parent is not None:
            # below a § / článek: extend the parent's ID, so the children of a § that had
            # to fall back to its full path (a repeated number) follow it
            pid = f"{direct_parent.id}.{make_segment(kind, num)}"
        else:
            id_segs = numbered[anchor:] if anchor is not None and kind not in STRUCTURAL else numbered
            pid = make_id(SOURCE, act_id, [_split_segment(s) for s in id_segs])
        if doc.get(pid) is not None:  # same § number twice (e.g. in separate parts): use full path
            pid = make_id(SOURCE, act_id, [_split_segment(s) for s in numbered])
        label, text = split_label(f.get("xhtml"))
        heading = None
        if kind in STRUCTURAL:
            pending_heading = None  # a group heading never crosses into the next part/chapter
        elif kind in ("par", "cl") and pending_heading:
            heading, pending_heading = pending_heading, None
        p = Provision(
            id=pid,
            source=SOURCE,
            act_id=act_id,
            language=LANGUAGE,
            kind=kind,
            label=label or (text if ftype == "Hlavicka_priloha" else num),
            number=normalize_number(num),
            text="" if ftype == "Hlavicka_priloha" else text,
            heading=heading,
            parent_id=parent.id if parent else None,
            eli=f.get("eli"),
            url=BASE_URL + f["staleUrl"] if f.get("staleUrl") else None,
        )
        doc.add(p)
        by_key[numbered] = p
    return doc
