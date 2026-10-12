"""German adapter: consolidated federal law from gesetze-im-internet.de, split into § / Abs. / Nr. / Buchst.

Text source: **gesetze-im-internet.de** (GII), the Federal Ministry of Justice's
portal of consolidated federal law (about 6,100 acts). No key or registration:

``GET https://www.gesetze-im-internet.de/gii-toc.xml``
    Table of contents: ``<item><title>…</title><link>…/<slug>/xml.zip</link></item>``
    for every act. ``<slug>`` is the act's path on the portal (``bsig_2025``,
    ``bgb``, ``enwg_2005``), derived from its official abbreviation (*jurabk*,
    ``BSIG 2025``) in lower case with other characters as ``_``.

``GET https://www.gesetze-im-internet.de/<slug>/xml.zip``
    The current consolidated text as one XML file (DTD ``gii-norm.dtd``): a flat
    list of ``<norm>`` elements in document order. The first holds the act's
    metadata (``jurabk``, ``amtabk``, ``langue``, ``fundstelle`` = the BGBl
    citation, ``standangabe`` = last amendment); each later one is either a
    structural heading (``gliederungseinheit`` with a ``gliederungskennzahl``
    such as ``020010`` = Teil 2 › Kapitel 1) or a citable unit (``enbez``: ``§ 30``,
    ``Art 12a``, ``Anlage 1``) with ``<P>`` paragraphs (``(1)`` = Absatz) and
    ``<DL>`` lists (``1.`` = Nummer, ``a)`` = Buchstabe, ``aa)`` = Doppelbuchstabe).
    Only the current version is published; there is no history.

Metadata source: **NeuRIS** (https://testphase.rechtsinformationen.bund.de,
docs at https://docs.rechtsinformationen.bund.de), the federal legal information
system in public testing (due 2028), also without key. It has ELIs, versions and
LegalDocML.de texts, but only part of federal law so far (BSIG and GG yes; BGB,
TKG and EnWG not when checked in October 2026), so it is used only for:

``GET /v1/legislation?risAbbreviation=BSIG%202025``
    Search; the hit gives the expression ELI
    (``eli/bund/bgbl-1/2025/301-2/2026-07-29/1/deu``) and the in-force date.
``GET /v1/legislation/eli/bund/bgbl-1/2025/301-2/2026-07-29/1/deu``
    Expression metadata with the ``eId`` of every § (``art-z30``), used for
    provision ELIs, and ``risAbbreviation`` (= GII *jurabk*), used to map a
    BGBl citation or ELI to a GII slug.
``GET /v1/legislation?eli=eli/bund/bgbl-1/2025/301``
    Works published in a BGBl issue (token match: ``301`` finds ``301-2``).

NeuRIS failures never stop a fetch: the document is then built without ELIs
or date.

German law is cited by abbreviation, not by number, so ``act_id`` is the GII
slug and IDs look like ``de:bsig_2025:par28.abs1.nr3.buchst_a``. IDs are
anchored at the § / Artikel / Anlage whatever Teil or Kapitel it sits in;
structural units keep their path (``de:bsig_2025:teil2.kapitel1``).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..cellar import USER_AGENT
from ..provisions import Document, Provision, make_id, make_segment, normalize_number

BASE_URL = "https://www.gesetze-im-internet.de"
TOC_URL = BASE_URL + "/gii-toc.xml"
NEURIS_URL = "https://testphase.rechtsinformationen.bund.de"
NEURIS_API = NEURIS_URL + "/v1/legislation"

SOURCE = "de"
LANGUAGE = "de"

Fetcher = Callable[[str], bytes]

# Units whose norms we do not turn into provisions.
SKIPPED_NORMS = {"inhaltsübersicht", "inhaltsverzeichnis", "eingangsformel", "schlussformel"}
# Editorial elements dropped from the text (footnote markers and bodies, notes, images).
SKIPPED_TAGS = {"FnR", "Footnotes", "Footnote", "kommentar", "TOC", "IMG", "ABWFORMAT"}
STRUCTURAL = {
    "buch", "teil", "unterteil", "kapitel", "unterkapitel", "abschnitt",
    "unterabschnitt", "titel", "untertitel", "gliederung",
}

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
_GII_URL = re.compile(r"gesetze-im-internet\.de/(?P<slug>[A-Za-z0-9_.\-]+)(?:/|$)")
_ELI = re.compile(
    r"eli/bund/(?P<agent>bgbl-?[12]?|banz-at)/(?P<year>\d{4})/(?P<nat>[A-Za-z0-9\-]+)"
    r"(?:/(?P<pit>\d{4}-\d{2}-\d{2})/(?P<ver>\d+)/(?P<lang>[a-z]{3}))?"
)
# "BGBl. 2025 I Nr. 301", "BGBl. I 2025 Nr. 301", "BGBl. I Nr. 301/2025", "BGBl. I 2009 S. 2821"
_BGBL = re.compile(
    r"^BGBl\.?\s*(?:(?P<p1>I{1,2})\s*)?(?:(?P<y1>\d{4})\s*)?(?:(?P<p2>I{1,2})\b\s*)?,?\s*"
    r"(?:Nr\.?\s*(?P<nr>\d+)(?:\s*/\s*(?P<y2>\d{4}))?)?\s*,?\s*(?:S\.?\s*(?P<page>\d+))?\s*$",
    re.IGNORECASE,
)
# GII stand comments: "G v. 2.12.2025 I Nr. 301", "G v. 14.8.2009 I 2821"
_BGBL_SHORT = re.compile(
    r"v\.\s*(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})\s+(?P<part>I{1,2})\s+(?:Nr\.\s*(?P<nr>\d+)|(?P<page>\d+))"
)
_ABS = re.compile(r"^\s*\((?P<num>\d+[a-z]?)\)\s*")
_WS = re.compile(r"[ \t\r\f\v\xa0]+")


# --- references ---------------------------------------------------------------


@dataclass(frozen=True)
class BgblRef:
    """A citation in the Federal Law Gazette: part I/II, year, issue number (2023+) or page."""

    year: int
    part: int = 1
    number: str | None = None  # "Nr. 301" (electronic BGBl, since 2023)
    page: str | None = None  # "S. 2821" (paper BGBl, until 2022); with ``number``: page in the issue

    @property
    def work_eli(self) -> str:
        """NeuRIS work ELI used to search: ``eli/bund/bgbl-1/2025/301`` or ``eli/bund/bgbl-1/2009/s2821``."""
        if self.number:
            nat = self.number
        elif self.page:
            nat = f"s{int(self.page)}"
        else:
            raise ValueError("a BGBl reference needs an issue number or a page")
        return f"eli/bund/bgbl-{self.part}/{self.year}/{nat}"

    def __str__(self) -> str:
        part = "I" * self.part
        if self.number:
            return f"BGBl. {self.year} {part} Nr. {self.number}"
        return f"BGBl. {part} {self.year} S. {self.page}"


@dataclass(frozen=True)
class ActRef:
    """What a reference names: a GII slug, an abbreviation, a NeuRIS ELI or a BGBl citation.

    Only ``slug`` can be fetched directly; the other forms are resolved by
    :meth:`GiiClient.resolve` (abbreviation: slug guess, then NeuRIS, then the GII
    table of contents for ``<abbreviation>_<year>`` slugs; ELI and BGBl: NeuRIS).
    """

    slug: str | None = None
    abbreviation: str | None = None
    eli: str | None = None
    bgbl: BgblRef | None = None

    @property
    def act_id(self) -> str | None:
        return self.slug

    @property
    def web_url(self) -> str | None:
        return f"{BASE_URL}/{self.slug}/index.html" if self.slug else None

    @property
    def xml_url(self) -> str | None:
        return f"{BASE_URL}/{self.slug}/xml.zip" if self.slug else None


def slugify(abbreviation: str) -> str:
    """GII path for an official abbreviation (*jurabk*): ``"BSIG 2025"`` -> ``"bsig_2025"``.

    Lower case; anything but ``a-z``, digits and ``-`` becomes ``_`` (``"1-DM-GoldMünzG"``
    -> ``"1-dm-goldm_nzg"``, as on the portal).
    """
    return re.sub(r"[^a-z0-9\-]", "_", abbreviation.strip().lower())


def parse_bgbl(ref: str) -> BgblRef:
    """Parse a BGBl citation: ``"BGBl. 2025 I Nr. 301"``, ``"BGBl. I S. 2821"`` needs a year."""
    s = " ".join(ref.split())
    m = _BGBL.match(s)
    if m and (m["nr"] or m["page"]):
        year = m["y1"] or m["y2"]
        if not year:
            raise ValueError(f"{ref!r}: a BGBl citation needs the year (e.g. 'BGBl. I 2009 S. 2821')")
        part = len(m["p1"] or m["p2"] or "I")
        return BgblRef(year=int(year), part=part, number=m["nr"], page=m["page"])
    m = _BGBL_SHORT.search(s)
    if m:
        return BgblRef(year=int(m["y"]), part=len(m["part"]), number=m["nr"], page=m["page"])
    raise ValueError(f"{ref!r} is not a BGBl citation (expected e.g. 'BGBl. 2025 I Nr. 301')")


def parse_reference(ref: str) -> ActRef:
    """Parse a German federal act reference.

    Accepted: a GII slug (``bsig_2025``), a GII URL (``https://www.gesetze-im-internet.de/bsig_2025/__30.html``),
    an abbreviation as cited (``BSIG``, ``BSIG 2025``, ``BSI-KritisV``), a NeuRIS ELI or URL
    (``eli/bund/bgbl-1/2025/301-2``) and a BGBl citation (``BGBl. 2025 I Nr. 301``,
    ``BGBl. I 2009 S. 2821``).
    """
    s = ref.strip()
    if not s:
        raise ValueError("empty reference")
    m = _GII_URL.search(s)
    if m:
        slug = m["slug"].lower()
        if slug.endswith((".html", ".zip", ".xml")):
            raise ValueError(f"{ref!r}: no act path in this gesetze-im-internet.de URL")
        return ActRef(slug=slug)
    m = _ELI.search(s)
    if m:
        return ActRef(eli=f"eli/bund/{m['agent']}/{m['year']}/{m['nat']}")
    if re.match(r"^BGBl\b", s, re.IGNORECASE) or _BGBL_SHORT.search(s):
        return ActRef(bgbl=parse_bgbl(s))
    if _SLUG.match(s):
        return ActRef(slug=s, abbreviation=s)  # may also be an abbreviation typed in lower case
    if re.match(r"^[\wÄÖÜäöüß][\wÄÖÜäöüß.\- ]{0,60}$", s):
        return ActRef(slug=slugify(s), abbreviation=s)
    raise ValueError(
        f"{ref!r} is not a German federal act reference (expected e.g. 'bsig_2025', 'BSIG' or 'BGBl. 2025 I Nr. 301')"
    )


# --- HTTP with cache and rate limit -------------------------------------------


def default_cache_dir() -> Path:
    """``$OPENACQUIS_CACHE``, else ``~/.cache/openacquis``; GII responses go to ``gii/``, NeuRIS to ``neuris/``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    return Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"


def _portal(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    if "rechtsinformationen" in host:
        return "neuris"
    if "gesetze-im-internet" in host:
        return "gii"
    return "de"


class HttpFetcher:
    """GET raw bytes with a descriptive User-Agent, a delay between requests and an on-disk cache.

    Responses are cached under ``<cache_dir>/gii/`` or ``<cache_dir>/neuris/`` for
    ``ttl`` seconds (GII rebuilds its files daily). ``cache_dir=False`` disables
    the cache. HTTP errors raise :class:`LookupError`.
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
        return self.cache_dir / _portal(url) / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".bin")

    def __call__(self, url: str) -> bytes:
        path = self._cache_path(url)
        if path is not None and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return path.read_bytes()
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise LookupError(f"{_portal(url).upper()} {e.code} for {url}") from e
        finally:
            self._last = time.monotonic()
            self.requests += 1
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)
        return raw


# --- NeuRIS metadata ------------------------------------------------------------


@dataclass
class NeurisInfo:
    """What NeuRIS knows about one act (current expression)."""

    expression_eli: str  # "eli/bund/bgbl-1/2025/301-2/2026-07-29/1/deu"
    work_eli: str | None = None  # "eli/bund/bgbl-1/2025/301-2"
    date: str | None = None  # point in time of the expression (version in force from)
    abbreviation: str | None = None  # "BSIG"
    ris_abbreviation: str | None = None  # "BSIG 2025" (= GII jurabk)
    publication: str | None = None  # "BGBl I, 2025 301-2"
    date_published: str | None = None
    legal_force: str | None = None
    eids: dict[str, str] = field(default_factory=dict)  # "§ 30" -> "art-z30"

    @property
    def slug(self) -> str | None:
        return slugify(self.ris_abbreviation) if self.ris_abbreviation else None

    def to_dict(self) -> dict:
        return asdict(self)


def _eids(parts: Iterable[dict], out: dict[str, str]) -> dict[str, str]:
    for p in parts:
        if p.get("partType") == "article" and p.get("name") and p.get("eId"):
            out.setdefault(_key(p["name"]), p["eId"])
        _eids(p.get("hasPart") or [], out)
    return out


def parse_neuris_expression(data: dict) -> NeurisInfo:
    """:class:`NeurisInfo` from a NeuRIS expression (``GET /v1/legislation/eli/…/deu``) or a search hit."""
    ident = data.get("legislationIdentifier") or ""
    m = _ELI.search(ident)
    work = data.get("exampleOfWork") or {}
    return NeurisInfo(
        expression_eli=ident,
        work_eli=work.get("legislationIdentifier"),
        date=m["pit"] if m and m["pit"] else None,
        abbreviation=data.get("abbreviation"),
        ris_abbreviation=data.get("risAbbreviation"),
        publication=(work.get("isPartOf") or {}).get("name"),
        date_published=work.get("datePublished"),
        legal_force=data.get("legislationLegalForce"),
        eids=_eids(data.get("hasPart") or [], {}),
    )


# --- client -------------------------------------------------------------------


class GiiClient:
    """Fetch consolidated federal law from gesetze-im-internet.de, with NeuRIS for ELIs.

    Pass ``fetch`` (url -> bytes) to stub the network; ``neuris=False`` skips
    NeuRIS (no ELIs, no date; abbreviations then resolve only by slug guess and the
    GII table of contents).
    """

    def __init__(self, fetch: Fetcher | None = None, neuris: bool = True):
        self._fetch = fetch or HttpFetcher()
        self.neuris = neuris
        self._last_xml: tuple[str, bytes] | None = None  # resolve() checks a slug, document() reuses it

    # GII ---------------------------------------------------------------------
    def toc(self) -> list[tuple[str, str]]:
        """``(title, slug)`` for every act on the portal (about 6,100)."""
        root = ET.fromstring(self._fetch(TOC_URL))
        out = []
        for item in root.iter("item"):
            link = item.findtext("link") or ""
            m = _GII_URL.search(link)
            if m:
                out.append(((item.findtext("title") or "").strip(), m["slug"]))
        return out

    def xml(self, slug: str) -> bytes:
        """The act's XML, unpacked from ``<slug>/xml.zip``."""
        if self._last_xml and self._last_xml[0] == slug:
            return self._last_xml[1]
        data = self._fetch(f"{BASE_URL}/{slug}/xml.zip")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise LookupError(f"gesetze-im-internet.de: {slug}/xml.zip is not a zip file") from e
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise LookupError(f"gesetze-im-internet.de: no XML in {slug}/xml.zip")
        raw = zf.read(names[0])
        self._last_xml = (slug, raw)
        return raw

    # NeuRIS ------------------------------------------------------------------
    def _neuris_json(self, url: str) -> dict:
        return json.loads(self._fetch(url))

    def neuris_search(self, **params: str) -> list[dict]:
        """``GET /v1/legislation`` with these query parameters; the ``item`` of each hit."""
        q = urllib.parse.urlencode({**params, "size": params.get("size", "50")})
        data = self._neuris_json(f"{NEURIS_API}?{q}")
        return [m.get("item") or {} for m in data.get("member") or []]

    def neuris_expression(self, expression_eli: str) -> NeurisInfo:
        """Expression metadata (with § eIds) for ``eli/bund/…/YYYY-MM-DD/1/deu``."""
        eli = expression_eli.strip().lstrip("/")
        return parse_neuris_expression(self._neuris_json(f"{NEURIS_API}/{eli}"))

    def neuris_info(self, jurabk: str) -> NeurisInfo | None:
        """NeuRIS data for the act with this GII abbreviation (``"BSIG 2025"``); ``None`` if absent."""
        hits = self.neuris_search(risAbbreviation=jurabk)
        if not hits:
            return None
        hit = next((h for h in hits if h.get("legislationLegalForce") == "InForce"), hits[0])
        return self.neuris_expression(hit["legislationIdentifier"])

    def neuris_works(self, work_eli: str, published: str | None = None) -> list[NeurisInfo]:
        """Acts NeuRIS lists under a work ELI (``eli/bund/bgbl-1/2025/301`` also finds ``…/301-2``).

        ``published`` (``YYYY-MM-DD``) keeps only works published in the gazette on that day,
        which guards against an issue number of another year or gazette.
        """
        out: dict[str, NeurisInfo] = {}
        for hit in self.neuris_search(eli=work_eli):
            work = hit.get("exampleOfWork") or {}
            wid = work.get("legislationIdentifier") or hit.get("legislationIdentifier")
            if not _same_work(work_eli, wid):
                continue
            if published and work.get("datePublished") and work["datePublished"] != published:
                continue
            if wid in out:
                continue
            out[wid] = self.neuris_expression(hit["legislationIdentifier"])
        return list(out.values())

    # resolution --------------------------------------------------------------
    def resolve_all(self, ref: str | ActRef) -> list[str]:
        """All GII slugs a reference names (a BGBl issue can contain several acts)."""
        r = ref if isinstance(ref, ActRef) else parse_reference(ref)
        if r.slug and not r.abbreviation:
            return [r.slug]
        if r.abbreviation:
            try:
                self.xml(r.slug)  # kept, so the following document() call does not refetch
                return [r.slug]
            except LookupError:
                pass
            slugs: list[str] = []
            if self.neuris:
                hits: list[dict] = []
                for abk in dict.fromkeys((r.abbreviation, r.abbreviation.upper())):
                    hits = self.neuris_search(abbreviation=abk) or self.neuris_search(risAbbreviation=abk)
                    if hits:
                        break
                for h in hits:
                    info = self.neuris_expression(h["legislationIdentifier"])
                    if info.slug and info.slug not in slugs:
                        slugs.append(info.slug)
            if not slugs:  # NeuRIS lacks much of federal law: "EnWG" is enwg_2005, "TKG" tkg_2021 on GII
                slugs = self._toc_slugs(r.abbreviation)
            if not slugs:
                raise LookupError(f"no federal act with abbreviation {r.abbreviation!r} on gesetze-im-internet.de or NeuRIS")
            return slugs
        if not self.neuris:
            raise LookupError("resolving an ELI or BGBl citation needs NeuRIS (neuris=True)")
        work = r.eli or r.bgbl.work_eli  # type: ignore[union-attr]
        slugs = [i.slug for i in self.neuris_works(work) if i.slug]
        if not slugs:
            raise LookupError(f"NeuRIS lists no act for {r.eli or r.bgbl} (NeuRIS does not have all federal law yet)")
        return list(dict.fromkeys(slugs))

    def _toc_slugs(self, abbreviation: str) -> list[str]:
        """GII slugs of the form ``<abbreviation>_<year>`` (an official abbreviation such as ``EnWG``
        whose GII abbreviation carries the year: ``EnWG 2005``); empty if the TOC is unavailable."""
        try:
            toc = self.toc()
        except (LookupError, ValueError, OSError, ET.ParseError):
            return []
        want = re.compile(rf"{re.escape(slugify(abbreviation))}_\d{{4}}")
        return list(dict.fromkeys(slug for _, slug in toc if want.fullmatch(slug.lower())))

    def resolve(self, ref: str | ActRef) -> str:
        """The one GII slug a reference names; ``LookupError`` if none or several."""
        slugs = self.resolve_all(ref)
        if len(slugs) > 1:
            raise LookupError(f"{ref} names several acts: {', '.join(slugs)}; pass one of them")
        return slugs[0]

    # documents ---------------------------------------------------------------
    def metadata(self, ref: str | ActRef) -> dict:
        """Act metadata from the GII XML (abbreviations, BGBl citation, last amendment) and NeuRIS."""
        slug = self.resolve(ref)
        meta = gii_metadata(self.xml(slug))
        meta["slug"] = slug
        info = self._info(meta.get("jurabk"))
        meta["neuris"] = info.to_dict() if info else None
        return meta

    def _info(self, jurabk: str | None) -> NeurisInfo | None:
        if not self.neuris or not jurabk:
            return None
        try:
            return self.neuris_info(jurabk)
        except (LookupError, ValueError, KeyError, OSError):  # NeuRIS is optional metadata
            return None

    def document(self, ref: str | ActRef, version: str | None = None) -> Document:
        """Fetch and parse the current consolidated text into a :class:`Document`.

        ``version`` must be ``None``: gesetze-im-internet.de publishes only the
        current text.
        """
        if version:
            raise ValueError(
                "gesetze-im-internet.de has only the current consolidated text; dated versions are not supported"
            )
        slug = self.resolve(ref)
        raw = self.xml(slug)
        info = self._info(gii_metadata(raw).get("jurabk"))
        return parse_gii_xml(raw, slug, info)


def fetch_act(ref: str, version: str | None = None, fetch: Fetcher | None = None) -> Document:
    """Convenience wrapper: ``fetch_act("bsig_2025")`` -> current consolidated text."""
    return GiiClient(fetch).document(ref, version)


def _same_work(query: str, work: str | None) -> bool:
    """``eli/bund/bgbl-1/2025/301`` matches ``…/301`` and ``…/301-2`` but not ``…/3010``."""
    if not work:
        return False
    q, w = query.rstrip("/"), work.rstrip("/")
    return w == q or w.startswith(q + "-")


# --- XML to text --------------------------------------------------------------


def _clean(raw: str) -> str:
    lines = (_WS.sub(" ", ln).strip() for ln in raw.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def _collect(el: ET.Element, out: list[str], lists: bool = False) -> None:
    """Text of ``el`` without its ``<DL>`` lists (a list leaves a line break), tables as ``a | b``."""
    if el.text:
        out.append(el.text)
    for c in el:
        if c.tag in SKIPPED_TAGS:
            pass
        elif c.tag == "DL" and not lists:
            out.append("\n")
        elif c.tag == "BR":
            out.append("\n")
        elif c.tag == "row":
            out.append("\n")
            cells = []
            for e in c.iter("entry"):
                parts: list[str] = []
                _collect(e, parts)
                cells.append(" ".join("".join(parts).split()))
            while cells and not cells[-1]:
                cells.pop()
            out.append(" | ".join(cells))
            out.append("\n")
        elif c.tag in ("P", "LA", "DD", "DT", "Title", "Ident", "table", "tgroup", "tbody", "thead", "pre"):
            out.append("\n")
            _collect(c, out, lists)
            out.append("\n")
        else:
            _collect(c, out, lists)
        if c.tail:
            out.append(c.tail)


def element_text(el: ET.Element | None, lists: bool = False) -> str:
    """Plain text of an element; ``<DL>`` lists are left out unless ``lists``."""
    if el is None:
        return ""
    out: list[str] = []
    _collect(el, out, lists)
    return _clean("".join(out))


def _lists(el: ET.Element) -> Iterator[ET.Element]:
    """``<DL>`` elements below ``el`` that are not inside another list."""
    for c in el:
        if c.tag == "DL":
            yield c
        elif c.tag not in SKIPPED_TAGS:
            yield from _lists(c)


def _items(dl: ET.Element) -> Iterator[tuple[str, ET.Element]]:
    """``(label, DD)`` pairs of a list."""
    label: str | None = None
    for c in dl:
        if c.tag == "DT":
            label = element_text(c)
        elif c.tag == "DD":
            yield label or "", c
            label = None


# --- numbering ------------------------------------------------------------------


_UNITS = {"erst": 1, "zweit": 2, "dritt": 3, "viert": 4, "fünft": 5, "sechst": 6, "siebent": 7,
          "siebt": 7, "acht": 8, "neunt": 9, "zehnt": 10, "elft": 11, "zwölft": 12}
_CARD = {"ein": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sech": 6, "sechs": 6, "sieb": 7,
         "sieben": 7, "acht": 8, "neun": 9}
_TENS = {"zwanzigst": 20, "dreißigst": 30, "vierzigst": 40, "fünfzigst": 50, "sechzigst": 60,
         "siebzigst": 70, "achtzigst": 80, "neunzigst": 90}


def german_ordinal(word: str) -> int | None:
    """``"Erster"`` -> 1, ``"Dreizehnter"`` -> 13, ``"Einundzwanzigster"`` -> 21; else ``None``."""
    stem = re.sub(r"(er|es|en|em|e)$", "", word.strip().lower())
    if stem in _UNITS:
        return _UNITS[stem]
    if stem.endswith("zehnt") and stem[:-5] in _CARD:
        return 10 + _CARD[stem[:-5]]
    for t, v in _TENS.items():
        if stem.endswith(t):
            pre = stem[: -len(t)]
            if not pre:
                return v
            if pre.endswith("und") and pre[:-3] in _CARD:
                return v + _CARD[pre[:-3]]
    return None


def _ascii_kind(word: str) -> str:
    w = word.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return re.sub(r"[^a-z]", "", w) or "norm"


def structure_unit(bez: str | None, position: int = 1) -> tuple[str, str]:
    """``(kind, number)`` of a structural heading: ``"Teil 2"`` -> ``("teil", "2")``,
    ``"Dritter Abschnitt"`` -> ``("abschnitt", "3")``, ``"I."`` -> ``("gliederung", "i")``;
    unnumbered ones (``"Allgemeiner Teil"``) get their ``position`` among their siblings.
    """
    pos = str(position)
    words = (bez or "").split()
    if not words:
        return "gliederung", pos
    first = _ascii_kind(words[0])
    if first in STRUCTURAL and len(words) >= 2:
        return first, normalize_number(" ".join(words[1:]))
    last = _ascii_kind(words[-1])
    if last in STRUCTURAL:
        n = german_ordinal(words[0]) if len(words) == 2 else None
        return last, str(n) if n else pos
    m = re.fullmatch(r"([IVXLC]+)\s*([a-z]?)\.?", bez.strip()) if bez else None
    if m:
        return "gliederung", normalize_number(m[1] + m[2])
    return "gliederung", pos


def norm_unit(enbez: str) -> tuple[str, str] | None:
    """``(kind, number)`` of a citable unit, or ``None`` for units we skip.

    ``"§ 30"`` -> ``("par", "30")``, ``"Art 12a"`` -> ``("art", "12a")``,
    ``"(XXXX) §§ 3 bis 6"`` -> ``("par", "3_bis_6")``, ``"Anlage 1"`` -> ``("anlage", "1")``,
    ``"Anlage (zu § 118)"`` -> ``("anlage", "0")``, ``"Präambel"`` -> ``("praeambel", "1")``.
    """
    s = re.sub(r"^\(X+\)\s*", "", enbez.strip()).strip()
    if not s or s.lower() in SKIPPED_NORMS:
        return None
    m = re.match(r"^§§?\s*(.+)$", s)
    if m:
        return "par", normalize_number(m[1])
    m = re.match(r"^Art(?:ikel|\.)?\s+(.+)$", s)
    if m:
        return "art", normalize_number(m[1])
    m = re.match(r"^(Anlage|Anhang)\b\s*(.*)$", s, re.IGNORECASE)
    if m:
        rest = m[2].strip()
        n = re.match(r"^([0-9]+[a-z]?|[IVX]+|[A-Za-z]{1,3})\b", rest) if rest and not rest.startswith("(") else None
        return m[1].lower(), normalize_number(n[1]) if n else "0"
    words = s.split(maxsplit=1)
    kind = _ascii_kind(words[0])
    return kind, normalize_number(words[1]) if len(words) > 1 else "1"


def _key(label: str) -> str:
    unit = norm_unit(label)
    return f"{unit[0]}:{unit[1]}" if unit else label


def item_unit(label: str) -> tuple[str, str | None]:
    """``(kind, number)`` of a list item from its printed label.

    ``"1."`` -> Nummer ``("nr", "1")``, ``"2a."`` -> ``("nr", "2a")``, ``"1.1"`` -> ``("nr", "1_1")``,
    ``"a)"`` -> Buchstabe ``("buchst", "a")``, ``"aa)"`` -> Doppelbuchstabe ``("dbuchst", "aa")``,
    ``"-"`` or no label -> Spiegelstrich ``("strich", None)`` (numbered by position),
    anything else (``"RD"``) -> ``("dt", "rd")``.
    """
    s = label.strip()
    if s in ("", "-", "–", "—", "•", "*", "·"):
        return "strich", None
    m = re.fullmatch(r"\(?(\d+(?:\.\d+)*[a-z]{0,2})\.?\)?", s)
    if m:
        return "nr", normalize_number(m[1])
    m = re.fullmatch(r"\(?([a-zA-Z])[).]", s)
    if m:
        return "buchst", m[1].lower()
    m = re.fullmatch(r"\(?(([a-z])\2{1,2})\)", s)
    if m:
        return "dbuchst", m[1]
    return "dt", normalize_number(s)


# --- XML to provisions --------------------------------------------------------------


def gii_metadata(xml: bytes | str | ET.Element) -> dict:
    """Act metadata from the first ``<norm>`` of a GII XML file."""
    root = xml if isinstance(xml, ET.Element) else ET.fromstring(xml)
    norm = root.find("norm")
    md = norm.find("metadaten") if norm is not None else None
    if md is None:
        raise ValueError("not a gesetze-im-internet.de XML file (no <norm><metadaten>)")
    fund = md.find("fundstelle")
    citation = None
    if fund is not None:
        citation = " ".join(x for x in (fund.findtext("periodikum"), fund.findtext("zitstelle")) if x)
    return {
        "doknr": root.get("doknr") or norm.get("doknr"),
        "builddate": root.get("builddate") or norm.get("builddate"),
        "jurabk": md.findtext("jurabk"),
        "amtabk": md.findtext("amtabk"),
        "kurzue": md.findtext("kurzue"),
        "langue": _clean(element_text(md.find("langue"))) or None,
        "ausfertigung": md.findtext("ausfertigung-datum"),
        "fundstelle": citation,  # "BGBl. I 2025, Nr. 301, 2"
        "stand": [
            {"typ": s.findtext("standtyp"), "kommentar": s.findtext("standkommentar")}
            for s in md.findall("standangabe")
        ],
    }


def _web_url(slug: str, label: str, doknr: str | None, page: str) -> str:
    """Page of one § on the portal (``__30.html``, ``art_12a.html``, ``anlage_1.html``), else an anchor."""
    s = label.strip()
    m = re.fullmatch(r"§\s*(\d+[a-z]?)", s)
    if m:
        return f"{BASE_URL}/{slug}/__{m[1]}.html"
    m = re.fullmatch(r"Art(?:ikel)?\s+(\d+[a-z]?)", s)
    if m:
        return f"{BASE_URL}/{slug}/art_{m[1]}.html"
    m = re.fullmatch(r"Anlage\s+(\d+[a-z]?)", s)
    if m:
        return f"{BASE_URL}/{slug}/anlage_{m[1]}.html"
    return f"{BASE_URL}/{slug}/{page}.html" + (f"#{doknr}" if doknr else "")


class _Builder:
    def __init__(self, doc: Document, slug: str):
        self.doc = doc
        self.slug = slug

    def unique(self, pid: str) -> str:
        if self.doc.get(pid) is None:
            return pid
        n = 2
        while self.doc.get(f"{pid}_{n}") is not None:
            n += 1
        return f"{pid}_{n}"

    def add(self, pid: str, kind: str, label: str, number: str, text: str = "", heading: str | None = None,
            parent: Provision | None = None, eli: str | None = None, url: str | None = None) -> Provision:
        return self.doc.add(Provision(
            id=self.unique(pid), source=SOURCE, act_id=self.slug, language=LANGUAGE, kind=kind,
            label=label, number=number, text=text, heading=heading,
            parent_id=parent.id if parent else None, eli=eli, url=url,
        ))

    def lists(self, block: ET.Element, owner: Provision) -> None:
        """Items of the lists in ``block`` become children of ``owner`` (recursively).

        A later list in the same owner whose numbers clash with an earlier one (Satz 1
        Nr. 1-2, Satz 2 Nr. 1-5) gets one suffix on every item: ``nr1_2`` ... ``nr5_2``.
        """
        for dl in _lists(block):
            items, strich = [], 0
            for label, dd in _items(dl):
                kind, num = item_unit(label)
                if num is None:
                    strich += 1
                    num = str(strich)
                items.append((f"{owner.id}.{make_segment(kind, num)}", kind, label, num, dd))
            suffix, n = "", 1
            while any(self.doc.get(pid + suffix) is not None for pid, *_ in items):
                n += 1
                suffix = f"_{n}"
            for pid, kind, label, num, dd in items:
                p = self.add(pid + suffix, kind, label, num, text=element_text(dd), parent=owner, url=owner.url)
                self.lists(dd, p)


def _append(p: Provision, text: str) -> None:
    if text:
        p.text = f"{p.text}\n{text}" if p.text else text


def parse_gii_xml(xml: bytes | str, slug: str, neuris: NeurisInfo | None = None) -> Document:
    """Turn a gesetze-im-internet.de XML file into a :class:`Document`.

    ``Document.date`` is the date the NeuRIS expression is in force from (``None``
    without NeuRIS: GII does not state it in machine-readable form);
    ``Document.eli`` is the NeuRIS expression ELI. Provision ELIs are set for §§ /
    Artikel NeuRIS lists and their Absätze (``…/deu#art-z30_abs-z1``).
    """
    root = ET.fromstring(xml)
    meta = gii_metadata(root)
    page = meta.get("doknr") or "index"
    title = meta.get("langue")
    abk = meta.get("amtabk") or meta.get("jurabk")
    if title and abk and abk not in title:
        title = f"{title} ({abk})"
    doc = Document(
        source=SOURCE, act_id=slug, language=LANGUAGE, title=title,
        date=neuris.date if neuris else None,
        eli=neuris.expression_eli if neuris else None,
        url=f"{BASE_URL}/{slug}/index.html",
    )
    b = _Builder(doc, slug)
    structs: dict[str, Provision] = {}
    siblings: Counter[str | None] = Counter()
    current: Provision | None = None  # innermost structural unit

    for norm in root.findall("norm")[1:]:
        md = norm.find("metadaten")
        if md is None:
            continue
        doknr = norm.get("doknr")
        ge = md.find("gliederungseinheit")
        if ge is not None:
            kz = (ge.findtext("gliederungskennzahl") or "").strip()
            bez = _clean(ge.findtext("gliederungsbez") or "")
            parent = next((structs[kz[:i]] for i in range(len(kz) - 1, 0, -1) if kz[:i] in structs), None)
            siblings[parent.id if parent else None] += 1
            kind, num = structure_unit(bez, siblings[parent.id if parent else None])
            pid = f"{parent.id}.{make_segment(kind, num)}" if parent else make_id(SOURCE, slug, [(kind, num)])
            current = b.add(pid, kind, bez or kind, num, heading=element_text(ge.find("gliederungstitel")) or None,
                            parent=parent, url=_web_url(slug, "", doknr, page))
            structs[kz] = current
            continue

        enbez = _clean(md.findtext("enbez") or "")
        heading = element_text(md.find("titel")) or None
        if not enbez and heading and re.match(r"^(Anlage|Anhang)\b", heading):
            enbez, heading = heading, None
        unit = norm_unit(enbez) if enbez else None
        content = norm.find("textdaten/text/Content")
        if unit is None:
            continue
        kind, num = unit
        label = re.sub(r"^\(X+\)\s*", "", enbez)
        parent = current if kind in ("par", "art") else None
        pid = make_id(SOURCE, slug, [(kind, num)])
        if doc.get(pid) is not None and parent is not None:  # same § number twice: use the structural path
            pid = f"{parent.id}.{make_segment(kind, num)}"
        eid = neuris.eids.get(_key(label)) if neuris and kind in ("par", "art") else None
        eli = f"{neuris.expression_eli}#{eid}" if eid else None
        url = _web_url(slug, label, doknr, page)
        p = b.add(pid, kind, label, num, heading=heading, parent=parent, eli=eli, url=url)
        if content is None:
            continue

        owner = p
        for block in content:
            if block.tag in SKIPPED_TAGS:
                continue
            text = element_text(block) if block.tag != "DL" else ""
            m = _ABS.match(text) if kind in ("par", "art") else None
            if m:
                n = normalize_number(m["num"])
                owner = b.add(f"{p.id}.{make_segment('abs', n)}", "abs", f"({m['num']})", n,
                              text=text[m.end():].strip(), parent=p,
                              eli=f"{eli}_abs-z{n}" if eli else None, url=url)
            else:
                _append(owner, text)
            b.lists(_wrap(block) if block.tag == "DL" else block, owner)
    return doc


def _wrap(dl: ET.Element) -> ET.Element:
    w = ET.Element("wrap")
    w.append(dl)
    return w


# --- CELLAR national measures -> adapter references ------------------------------------

LAENDER = (
    "Baden-Württemberg", "Bayern", "Bayerisch", "Berlin", "Brandenburg", "Bremen", "Bremisch", "Bremer",
    "Hamburg", "Hessen", "Hessisch", "Mecklenburg-Vorpommern", "Mecklenburg-Vorpommersch",
    "Niedersachsen", "Niedersächsisch", "Nordrhein-Westfalen", "Nordrhein-Westfälisch", "NRW",
    "Rheinland-Pfalz", "Rheinland-Pfälzisch", "Saarland", "Saarländisch", "Sachsen-Anhalt", "Sachsen",
    "Sächsisch", "Schleswig-Holstein", "Thüringen", "Thüringer", "Landesverwaltung",
)


@dataclass
class MeasureMatch:
    """How a measure CELLAR lists for Germany maps to acts this adapter can fetch.

    ``status``: ``mapped`` (``refs`` holds GII slugs), ``land`` (a Land measure:
    gesetze-im-internet.de has federal law only), ``not_found`` (no federal act
    in NeuRIS for that BGBl issue and date), ``no_reference`` (CELLAR has no
    gazette number), ``not_a_law`` (e.g. explanatory documents).
    """

    measure_uri: str
    title: str | None
    status: str
    refs: list[str] = field(default_factory=list)
    bgbl: str | None = None
    elis: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# endings after a Land name: "Hamburgisches", "Berliner", "Bayerischen", "Schleswig-Holsteinische"
_LAND_SUFFIX = r"(?:isch(?:e[nmrs]?)?|er|e[nmrs]?)?"
# titles of administrative measures, which are not published in the BGBl
_ADMINISTRATIVE = re.compile(r"^Amtsblatt\b|\b(?:Runderlass|Ministerialblatt|Verwaltungsvorschrift)\b", re.IGNORECASE)


def land_of(title: str | None) -> str | None:
    """The Land a measure title names (``"… im Land Brandenburg"`` -> ``"Brandenburg"``,
    ``"Hamburgisches …"`` -> ``"Hamburg"``), else ``None``."""
    if not title:
        return None
    for name in LAENDER:
        if re.search(rf"(?<![\w-]){re.escape(name)}{_LAND_SUFFIX}(?![\w-])", title):
            return name
    return None


def journal_numbers(celex: str, country: str = "DEU", cellar=None) -> dict[str, tuple[str | None, str | None]]:
    """``{measure uri: (gazette number, pages)}`` from CELLAR for the measures of one country.

    ``openacquis.cellar.CellarClient.national_measures`` does not return these fields yet.
    """
    from ..cellar import CellarClient, validate_directive_celex
    from ..countries import AUTHORITY, normalize_country

    client = cellar or CellarClient()
    rows = client.query(f"""
SELECT ?m (SAMPLE(?num) AS ?number) (SAMPLE(?page) AS ?pages) WHERE {{
  ?dir cdm:resource_legal_id_celex "{validate_directive_celex(celex)}"^^xsd:string .
  ?m cdm:measure_national_implementing_implements_resource_legal ?dir ;
     cdm:measure_national_implementing_implemented_by_country <{AUTHORITY}{normalize_country(country)}> .
  OPTIONAL {{ ?m cdm:measure_national_implementing_number_official_journal ?num }}
  OPTIONAL {{ ?m cdm:measure_national_implementing_page_official_journal ?page }}
}} GROUP BY ?m""")
    return {r["m"]: (r.get("number"), r.get("pages")) for r in rows}


def measure_bgbl(journal_date: str | None, number: str | None, pages: str | None) -> BgblRef | None:
    """The BGBl I citation CELLAR's fields describe, if they look like one.

    From 2023 the BGBl is numbered per issue (``Nr. 301``); before, by page (``S. 2821``,
    the first page of CELLAR's ``00397-00428``).
    """
    if not journal_date or not number or not re.fullmatch(r"\d{1,4}", number.strip()):
        return None
    year = int(journal_date[:4])
    if year >= 2023:
        return BgblRef(year=year, number=str(int(number)))
    first = re.match(r"0*(\d+)", pages or "")
    return BgblRef(year=year, number=None, page=first[1]) if first else None


def map_measure(
    measure,
    client: GiiClient | None = None,
    number: str | None = None,
    pages: str | None = None,
) -> MeasureMatch:
    """Map one CELLAR measure (:class:`openacquis.cellar.NationalMeasure`) to GII slugs.

    ``number`` / ``pages`` are the gazette fields from :func:`journal_numbers`. A
    federal act is found through NeuRIS by BGBl issue and publication date; the
    act's ``risAbbreviation`` gives the slug. Land measures are not looked up.
    """
    title = " ".join((measure.title or "").split()) or None
    m = MeasureMatch(measure_uri=measure.uri, title=title, status="no_reference")
    if title and re.match(r"^(Explanatory documents|Begründung|Erläuterungen)\b", title, re.IGNORECASE):
        m.status, m.note = "not_a_law", "explanatory document, not a legal act"
        return m
    land = land_of(title)
    if land:
        m.status, m.note = "land", f"Land measure ({land}); gesetze-im-internet.de has federal law only"
        return m
    if title and _ADMINISTRATIVE.search(title):
        m.status = "not_found"
        m.note = "administrative measure (Runderlass, Amtsblatt …), not published in the BGBl; not looked up"
        return m
    ref = measure_bgbl(measure.journal_date, number, pages)
    if ref is None:
        m.note = f"CELLAR gives no usable gazette number ({number!r})"
        return m
    m.bgbl = str(ref)
    works = (client or GiiClient()).neuris_works(ref.work_eli, published=measure.journal_date)
    m.refs = [w.slug for w in works if w.slug]
    m.elis = [w.work_eli for w in works if w.work_eli]
    if m.refs:
        m.status = "mapped"
        m.note = "acts published in this BGBl issue as listed by NeuRIS (re-enacted laws only, not amendments)"
    else:
        m.status = "not_found"
        m.note = "no federal act in NeuRIS for this BGBl issue and date (Land or administrative measure, or not in NeuRIS yet)"
    return m


def map_measures(celex: str, cellar=None, client: GiiClient | None = None) -> list[MeasureMatch]:
    """Map every measure Germany notified for a directive (``"32022L2555"``) to GII slugs."""
    from ..cellar import CellarClient

    cellar = cellar or CellarClient()
    client = client or GiiClient()
    numbers = journal_numbers(celex, "DEU", cellar)
    out = []
    for ms in cellar.national_measures(celex, "DEU"):
        try:
            out.append(map_measure(ms, client, *numbers.get(ms.uri, (None, None))))
        except (LookupError, ValueError, OSError) as exc:  # one failed lookup does not stop the rest
            out.append(MeasureMatch(ms.uri, " ".join((ms.title or "").split()) or None, "error",
                                    note=f"{type(exc).__name__}: {exc}"))
    return out
