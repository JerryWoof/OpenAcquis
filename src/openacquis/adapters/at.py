"""Austrian adapter: consolidated federal law from RIS, split into § / Abs. / Z / lit.

RIS (Rechtsinformationssystem des Bundes, https://www.ris.bka.gv.at) publishes the
consolidated federal law (*Bundesrecht konsolidiert*, application ``BrKons``) as
open government data. Two documented endpoints, no key or registration:

``GET https://data.bka.gv.at/ris/api/v2.6/Bundesrecht?Applikation=BrKons&Gesetzesnummer=N&Fassung.FassungVom=YYYY-MM-DD``
    OGD search API (JSON). One hit per *norm document*: a consolidated law is
    stored as one document per § / Artikel / Anlage, plus a ``§ 0`` document
    (``Dokumenttyp`` ``Norm``) with the long title, promulgation clause and table
    of contents. ``Fassung.FassungVom`` returns the documents in force on that
    day; without it the search returns every historical version. Hits carry the
    ``Gesetzesnummer`` (RIS's stable number of a consolidated law), the
    Stammfassung (``StammnormPublikationsorgan`` + ``StammnormBgblnummer``,
    e.g. ``BGBl. I Nr.`` + ``94/2025``), ``ArtikelParagraphAnlage`` (``§ 3a``,
    ``Art. 49``, ``Art. 1 § 2``, ``Anl. 1``), ``Inkrafttretensdatum``, an ELI per
    document (``https://ris.bka.gv.at/eli/bgbl/i/2025/94/P3/NOR40273862``) and
    content URLs. 100 hits per page (``DokumenteProSeite=OneHundred``,
    ``Seitennummer=N``). Errors come back as ``{"OgdSearchResult": {"Error": ...}}``.

``GET https://ogd.ris.bka.gv.at/Dokumente/Bundesnormen/{NOR}/{NOR}.xml``
    The text of one norm document in RIS's own XML (namespace
    ``http://www.bka.gv.at``): ``ueberschrift`` (``typ`` ``g1``/``g2`` group
    headings such as ``2. Abschnitt``, ``para`` § heading, ``anlage``),
    ``absatz typ="abs"`` (one Absatz; the first starts with ``<gldsym>§ 7.</gldsym>``,
    numbered ones with ``(2)``), ``liste/aufzaehlung[@ebene]/listelem`` (also
    ``ziffernliste`` instead of ``aufzaehlung``) with the
    printed number in ``symbol`` (``1.`` Ziffer, ``a)`` litera, ``aa)`` sublitera,
    ``–`` dash) and ``schlussteil[@ebene]`` (text closing a list).

A whole act therefore costs one search request per 100 documents plus one XML
request per §; responses are cached under ``$OPENACQUIS_CACHE/ris``.
``www.ris.bka.gv.at/GeltendeFassung.wxe?...`` serves the whole act as one HTML
page, but it is the web application, not the OGD interface, so it is only used
as the human-facing :attr:`Document.url`.

IDs use the Gesetzesnummer as ``act_id``: a Bundesgesetzblatt number names an
issue of the gazette, which may enact or amend several laws (NISG 2026 is
Article 1 of BGBl. I Nr. 94/2025), while the Gesetzesnummer names one
consolidated law and survives amendments. § 24 Abs. 1 Z 1 lit. b of NISG 2026
is ``at:20013065:par24.abs1.z1.lit_b``; a law that nests §§ in Artikel (DSG:
``Art. 2 § 4``) gets ``at:10001597:art2.par4``, with its Hauptstücke and
Abschnitte inside the Artikel (``art2.hauptstueck1.abschnitt1``). ``parse_reference`` accepts the BGBl
citation (``BGBl. I Nr. 94/2025``, resolved to the law whose Stammfassung it is),
the Gesetzesnummer, RIS and ELI URLs, and the official abbreviation
(``NISG 2026``).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..cellar import USER_AGENT
from ..provisions import Document, Provision, make_id, make_segment, normalize_number

BASE_URL = "https://www.ris.bka.gv.at"
API_URL = "https://data.bka.gv.at/ris/api/v2.6"
SEARCH_URL = API_URL + "/Bundesrecht"
OGD_DOC_URL = "https://ogd.ris.bka.gv.at/Dokumente/Bundesnormen/{nor}/{nor}.xml"
PAGE_SIZE = "OneHundred"  # API enum: Ten, Twenty, Fifty, OneHundred
PAGE_HITS = 100

SOURCE = "at"
LANGUAGE = "de"

NS = "{http://www.bka.gv.at}"

Fetcher = Callable[[str], str]

# Group units printed as "2. Abschnitt" / "Abschnitt 2" / "Zweiter Abschnitt", outermost first.
STRUCTURAL = ("teil", "hauptstueck", "abschnitt", "unterabschnitt")
_STRUCT_WORDS = {
    "teil": "teil",
    "hauptstück": "hauptstueck",
    "hauptstueck": "hauptstueck",
    "abschnitt": "abschnitt",
    "unterabschnitt": "unterabschnitt",
}
_ORDINALS = {
    w: i
    for i, w in enumerate(
        "erst zweit dritt viert fünft sechst siebent acht neunt zehnt elft zwölft dreizehnt "
        "vierzehnt fünfzehnt sechzehnt siebzehnt achtzehnt neunzehnt zwanzigst".split(),
        start=1,
    )
}
_UNIT = r"(?P<unit>Teil|Hauptst(?:ü|ue)ck|Unterabschnitt|Abschnitt)"
_STRUCT_NUM_FIRST = re.compile(rf"^(?P<num>\d+[a-z]?|[IVXLC]+)\.\s*{_UNIT}\b", re.IGNORECASE)
_STRUCT_NUM_AFTER = re.compile(rf"^{_UNIT}\s+(?P<num>\d+[a-z]?|[IVXLC]+)\b\.?", re.IGNORECASE)
_STRUCT_ORDINAL = re.compile(rf"^(?P<ord>[A-Za-zäöüÄÖÜ]+?)(?:e|er|es)\s+{_UNIT}\b", re.IGNORECASE)

_BGBL = re.compile(
    r"BGBl\.?\s*(?P<part>III|II|I)?\s*(?:Nr\.?\s*)?(?P<num>\d+[a-z]?)\s*/\s*(?P<year>\d{4})",
    re.IGNORECASE,
)
_ELI = re.compile(r"/eli/bgbl/(?:(?P<part>i{1,3})/)?(?P<year>\d{4})/(?P<num>\d+[a-z]?)(?:[/?#]|$)", re.IGNORECASE)
_GESNR = re.compile(r"(?:^|[?&]Gesetzesnummer=)(?P<nr>\d{8})(?:$|&)")
_ABBREV = re.compile(r"^[A-ZÄÖÜ][\wÄÖÜäöüß.\-–]*(?:\s+\d{4})?$")
_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# "(2)", also after a marker such as "(Verfassungsbestimmung) (1)"; the marker stays in the text
_ABS = re.compile(r"^(?P<pre>\((?:Verfassungsbestimmung|Grundsatzbestimmung)\)\s*)?\((?P<num>\d+[a-z]*)\)\s*")
_Z = re.compile(r"^(?P<num>\d+[a-z]*)\.$")
_LIT = re.compile(r"^\(?(?P<num>[a-z])[.)]$")  # "a)", also "a." and "(a)"
_SUBLIT = re.compile(r"^\(?(?P<num>([a-z])\2{1,2})[.)]$")  # "aa)", "aaa)"
_DASH = {"–", "-", "—", "•", "‑"}
_ITEM_LISTS = {"aufzaehlung", "ziffernliste"}  # list levels holding listelem items
_ARTIKEL_HEADING = re.compile(r"^Art(?:ikel|\.)\s*\d+[a-z]*\.?$", re.IGNORECASE)
_APA_TOKEN = re.compile(r"(Art\.|Artikel|§§?|Anl\.|Anlage)\s*([^\s§]+(?:\s+(?:bis|und)\s+[^\s§]+)?)?")


# --- references ---------------------------------------------------------------


@dataclass(frozen=True)
class BgblRef:
    """A Bundesgesetzblatt number: ``part`` is ``"I"``, ``"II"``, ``"III"`` or ``""`` (before 1997)."""

    part: str
    number: str
    year: int

    @property
    def nummer(self) -> str:
        """``"94/2025"``, as in the API field ``StammnormBgblnummer``."""
        return f"{self.number}/{self.year}"

    @property
    def organ(self) -> str:
        """``"BGBl. I Nr."``, as in the API field ``StammnormPublikationsorgan``."""
        return f"BGBl. {self.part} Nr." if self.part else "BGBl. Nr."

    @property
    def citation(self) -> str:
        return f"{self.organ} {self.nummer}"

    @property
    def eli(self) -> str | None:
        """ELI of the issue on RIS; ``None`` before 2004, when the BGBl was not yet published
        authentically online and RIS has no ELI for the issue (only for single norms)."""
        if self.year < 2004:
            return None
        part = f"{self.part.lower()}/" if self.part else ""
        return f"{BASE_URL}/eli/bgbl/{part}{self.year}/{self.number}"


@dataclass(frozen=True)
class ActRef:
    """A consolidated federal law, named by Gesetzesnummer, Stammfassung or abbreviation.

    At least one of ``gesetzesnummer``, ``bgbl`` and ``abbreviation`` is set;
    :meth:`RisClient.resolve` turns the latter two into a Gesetzesnummer.
    """

    gesetzesnummer: str | None = None
    bgbl: BgblRef | None = None
    abbreviation: str | None = None
    version: str | None = None  # "YYYY-MM-DD": the text in force on that day; None = today

    @property
    def act_id(self) -> str:
        if not self.gesetzesnummer:
            raise ValueError("act_id needs the Gesetzesnummer; resolve the reference first")
        return self.gesetzesnummer

    def __str__(self) -> str:
        return self.gesetzesnummer or (self.bgbl.citation if self.bgbl else self.abbreviation or "?")


def parse_bgbl(text: str) -> BgblRef | None:
    """First ``BGBl. I Nr. 94/2025``-style citation in ``text`` (``None`` if there is none)."""
    m = _BGBL.search(text)
    if not m:
        return None
    return BgblRef(part=(m["part"] or "").upper(), number=m["num"].lower(), year=int(m["year"]))


def parse_reference(ref: str, version: str | None = None) -> ActRef:
    """Parse a reference to an Austrian federal law.

    Accepted: a Gesetzesnummer (``"20013065"``), a BGBl citation of the
    Stammfassung (``"BGBl. I Nr. 94/2025"``, ``"BGBl I 94/2025"``,
    ``"BGBl. Nr. 51/1991"``), an ELI (``https://www.ris.bka.gv.at/eli/bgbl/i/2025/94/...``),
    a RIS URL with ``Gesetzesnummer=``, or an official abbreviation (``"NISG 2026"``,
    ``"AVG"``). ``version`` (``"YYYY-MM-DD"``) selects the text in force on that day.
    """
    if version is not None and not _VERSION.match(version):
        raise ValueError(f"version must be YYYY-MM-DD, got {version!r}")
    s = urllib.parse.unquote(ref.strip())
    m = _GESNR.search(s)
    if m:
        ver = version
        if ver is None:
            vm = re.search(r"FassungVom=(\d{4}-\d{2}-\d{2})", s)
            ver = vm.group(1) if vm else None
        return ActRef(gesetzesnummer=m["nr"], version=ver)
    m = _ELI.search(s)
    if m:
        part = (m["part"] or "").upper()
        return ActRef(bgbl=BgblRef(part, m["num"].lower(), int(m["year"])), version=version)
    if re.match(r"^BGBl", s, re.IGNORECASE):
        b = parse_bgbl(s)
        if b:
            return ActRef(bgbl=b, version=version)
    if _ABBREV.match(s) and not s.isdigit():
        return ActRef(abbreviation=s, version=version)
    raise ValueError(
        f"{ref!r} is not an Austrian federal law reference "
        "(expected e.g. 'BGBl. I Nr. 94/2025', a Gesetzesnummer like '20013065' or 'NISG 2026')"
    )


# --- HTTP with cache and rate limit -------------------------------------------


def default_cache_dir() -> Path:
    """``$OPENACQUIS_CACHE/ris``, else ``~/.cache/openacquis/ris``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    base = Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"
    return base / "ris"


class HttpFetcher:
    """GET text with a descriptive User-Agent, a delay between requests and an on-disk cache.

    Returns the response body as ``str`` (JSON from the search API, XML for
    documents). ``cache_dir=False`` disables the cache; cached responses older
    than ``ttl`` seconds are refetched. HTTP errors raise :class:`LookupError`.
    """

    def __init__(
        self,
        cache_dir: Path | str | None | bool = None,
        ttl: float = 24 * 3600,
        delay: float = 1.0,
        timeout: float = 60.0,
        user_agent: str = USER_AGENT,
        context: ssl.SSLContext | None = None,
    ):
        if cache_dir is False:
            self.cache_dir: Path | None = None
        else:
            self.cache_dir = Path(cache_dir) if cache_dir not in (None, True) else default_cache_dir()
        self.ttl = ttl
        self.delay = delay
        self.timeout = timeout
        self.user_agent = user_agent
        self.context = context
        self._last = 0.0
        self.requests = 0  # network requests actually made

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".txt")

    def _open(self, req: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.context) as resp:
                return resp.read()
        except urllib.error.URLError as e:
            if not isinstance(e.reason, ssl.SSLCertVerificationError) or self.context is not None:
                raise
            # RIS certificates chain to "HARICA TLS RSA Root CA 2021", missing from some OpenSSL
            # bundles (e.g. macOS /etc/ssl/cert.pem); certifi ships it. Verification stays on.
            self.context = _certifi_context()
            if self.context is None:
                raise LookupError(
                    f"RIS: TLS certificate of {urllib.parse.urlsplit(req.full_url).netloc} not trusted by this "
                    "Python (its root, HARICA TLS RSA Root CA 2021, is missing from the CA bundle); "
                    "pip install certifi, or point SSL_CERT_FILE at a bundle that has it"
                ) from e
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.context) as resp:
                return resp.read()

    def __call__(self, url: str) -> str:
        path = self._cache_path(url)
        if path is not None and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return path.read_text(encoding="utf-8")
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            url, headers={"Accept": "application/json, application/xml", "User-Agent": self.user_agent}
        )
        try:
            raw = self._open(req)
        except urllib.error.HTTPError as e:
            raise LookupError(f"RIS {e.code} for {url}") from e
        finally:
            self._last = time.monotonic()
            self.requests += 1
        text = raw.decode("utf-8-sig")
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        return text


def _certifi_context() -> ssl.SSLContext | None:
    """A verifying SSL context on certifi's CA bundle, if certifi is installed (optional)."""
    try:
        import certifi  # type: ignore[import-not-found]
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


# --- search hits ----------------------------------------------------------------


@dataclass
class Norm:
    """One norm document (§, Artikel, Anlage or the ``§ 0`` title document) from the search API."""

    nor: str  # document number, e.g. "NOR40273862"
    gesetzesnummer: str
    apa: str  # ArtikelParagraphAnlage as printed: "§ 3a", "Art. 49", "Anl. 1"
    doc_type: str  # Dokumenttyp: "Norm" (title document), "Paragraph", "Artikel", "Anlage"
    kurztitel: str | None = None
    titel: str | None = None  # long title, on the "§ 0" document only
    abkuerzung: str | None = None
    typ: str | None = None  # BG, BVG, V, ...
    kundmachungsorgan: str | None = None
    stammfassung: str | None = None  # "BGBl. I Nr. 94/2025"
    in_force: str | None = None  # Inkrafttretensdatum
    out_of_force: str | None = None  # Ausserkrafttretensdatum
    eli: str | None = None
    xml_url: str | None = None
    html_url: str | None = None

    @property
    def is_title(self) -> bool:
        return self.doc_type == "Norm" or self.apa.strip() in ("§ 0", "Art. 0")

    @classmethod
    def from_hit(cls, hit: dict) -> Norm:
        meta = hit["Data"]["Metadaten"]
        br = meta.get("Bundesrecht") or {}
        k = br.get("BrKons") or {}
        urls: dict[str, str] = {}
        content = (hit["Data"].get("Dokumentliste") or {}).get("ContentReference") or {}
        if isinstance(content, list):
            content = next((c for c in content if c.get("ContentType") == "MainDocument"), content[0])
        cu = (content.get("Urls") or {}).get("ContentUrl") or []
        for u in cu if isinstance(cu, list) else [cu]:
            urls[u.get("DataType", "")] = u.get("Url", "")
        organ, nummer = k.get("StammnormPublikationsorgan"), k.get("StammnormBgblnummer")
        return cls(
            nor=meta["Technisch"]["ID"],
            gesetzesnummer=str(k.get("Gesetzesnummer") or ""),
            apa=k.get("ArtikelParagraphAnlage") or "",
            doc_type=k.get("Dokumenttyp") or "",
            kurztitel=br.get("Kurztitel"),
            titel=br.get("Titel"),
            abkuerzung=k.get("Abkuerzung"),
            typ=k.get("Typ"),
            kundmachungsorgan=k.get("Kundmachungsorgan"),
            stammfassung=f"{organ} {nummer}" if organ and nummer else None,
            in_force=k.get("Inkrafttretensdatum"),
            out_of_force=k.get("Ausserkrafttretensdatum"),
            eli=br.get("Eli"),
            xml_url=urls.get("Xml"),
            html_url=urls.get("Html"),
        )


def parse_search(data: dict | str) -> tuple[int, list[Norm]]:
    """``(total hits, norms on this page)`` from a search API response; API errors raise ``LookupError``."""
    if isinstance(data, str):
        data = json.loads(data)
    res = data.get("OgdSearchResult") or {}
    if "Error" in res:
        err = res["Error"]
        raise LookupError(f"RIS API: {err.get('Message', err) if isinstance(err, dict) else err}")
    docs = res.get("OgdDocumentResults") or {}
    hits = docs.get("Hits") or {}
    total = int(hits.get("#text", 0) if isinstance(hits, dict) else hits or 0)
    refs = docs.get("OgdDocumentReference") or []
    if isinstance(refs, dict):  # a single hit is not wrapped in a list
        refs = [refs]
    return total, [Norm.from_hit(r) for r in refs]


# --- client -------------------------------------------------------------------


def _today() -> str:
    return _dt.date.today().isoformat()


class RisClient:
    """Fetch consolidated federal law from RIS OGD. Pass ``fetch`` (url -> body text) to stub the network."""

    def __init__(self, fetch: Fetcher | None = None, today: Callable[[], str] = _today):
        self._fetch = fetch or HttpFetcher()
        self._today = today

    # search ------------------------------------------------------------------
    def search_url(self, page: int = 1, **params: str) -> str:
        q = {"Applikation": "BrKons", **params, "DokumenteProSeite": PAGE_SIZE}
        if page > 1:
            q["Seitennummer"] = str(page)
        return SEARCH_URL + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)

    def search(self, max_pages: int = 50, **params: str) -> list[Norm]:
        """All hits of a BrKons search (every page). Keys are API parameters, e.g. ``Gesetzesnummer``."""
        out: list[Norm] = []
        page = 1
        while page <= max_pages:
            total, norms = parse_search(self._fetch(self.search_url(page, **params)))
            out.extend(norms)
            if not norms or len(out) >= total:
                break
            page += 1
        return out

    def norms(self, gesetzesnummer: str, version: str | None = None,
              sections: Sequence[str] | None = None) -> list[Norm]:
        """Norm documents of a law in force on ``version`` (default today), in document order
        (with ``sections``: sorted by Artikel / § / Anlage number).

        ``sections`` (``["§ 45", "Art. 49", "Anl. 1"]``) limits the result to those
        documents, using the API's ``Abschnitt`` filter (one search per section).
        """
        day = version or self._today()
        if sections:
            norms: list[Norm] = []
            for sec in sections:
                for n in self._section(gesetzesnummer, day, sec):
                    if n.nor not in {x.nor for x in norms}:
                        norms.append(n)
        else:
            norms = self.search(Gesetzesnummer=gesetzesnummer, **{"Fassung.FassungVom": day})
        norms = [n for n in norms if n.gesetzesnummer == gesetzesnummer]
        if not norms:
            what = f" ({', '.join(sections)})" if sections else ""
            raise LookupError(
                f"RIS: no text of Gesetzesnummer {gesetzesnummer}{what} in force on {day} "
                "(repealed, not yet in force or unknown; pass a version date)"
            )
        if sections:  # hits come per requested section: put them back in document order
            norms = sorted(norms, key=lambda n: _apa_key(n.apa))
        return sorted(norms, key=lambda n: not n.is_title)  # title document first, else API order

    def _section(self, gesetzesnummer: str, day: str, section: str) -> list[Norm]:
        path = _apa_path(section)
        if not path or path[-1][0] not in _SECTION_TYPES:
            raise ValueError(f"section must look like '§ 45', 'Art. 49' or 'Anl. 1', got {section!r}")
        kind, num = path[-1]
        digits = re.match(r"\d+", num)
        if not digits:
            raise ValueError(f"section number must start with a digit, got {section!r}")
        hits = self.search(Gesetzesnummer=gesetzesnummer, **{
            "Fassung.FassungVom": day, "Abschnitt.Typ": _SECTION_TYPES[kind],
            "Abschnitt.Von": digits.group(), "Abschnitt.Bis": digits.group()})
        want = (kind, normalize_number(num))
        return [n for n in hits if any((k, normalize_number(x)) == want for k, x in _apa_path(n.apa))]

    # references ----------------------------------------------------------------
    def resolve(self, ref: str | ActRef, version: str | None = None) -> ActRef:
        """Fill in the Gesetzesnummer of a BGBl or abbreviation reference.

        A BGBl number names the law whose Stammfassung it is (an issue may hold
        several, e.g. a law and its transitional rules); with both a BGBl number and
        an abbreviation, both must match. Among several matching laws the one in
        force on ``version`` (default today) wins; ``LookupError`` if none or still
        ambiguous.
        """
        r = ref if isinstance(ref, ActRef) else parse_reference(ref, version)
        if r.gesetzesnummer:
            return r
        day = r.version or version or self._today()
        want = _norm_abbrev(r.abbreviation) if r.abbreviation else None
        what = " / ".join(x for x in (r.bgbl.citation if r.bgbl else None, r.abbreviation) if x)

        def keep(n: Norm) -> bool:
            if r.bgbl is not None and not _same_bgbl(n.stammfassung, r.bgbl):
                return False
            return want is None or _norm_abbrev(n.abkuerzung or "") == want

        if r.bgbl is not None:  # matches documents of any law published in an issue with that number
            params = {"Kundmachungsorgannummer": r.bgbl.nummer}
        else:
            params = {"Titel": r.abbreviation or ""}
        # documents in force first (far fewer hits), then every version (repealed laws)
        hits = [n for n in self.search(max_pages=10, **params, **{"Fassung.FassungVom": day}) if keep(n)]
        if not hits:
            hits = [n for n in self.search(max_pages=10, **params) if keep(n)]
        nrs = _unique(n.gesetzesnummer for n in hits)
        if len(nrs) > 1:
            live = _unique(n.gesetzesnummer for n in hits if _in_force(n, day))
            nrs = live or nrs
        if not nrs:
            raise LookupError(f"RIS: no consolidated federal law found for {what}")
        if len(nrs) > 1:
            raise LookupError(f"RIS: {what} matches several laws (Gesetzesnummer {', '.join(nrs)}); pass one")
        return ActRef(gesetzesnummer=nrs[0], bgbl=r.bgbl, abbreviation=r.abbreviation, version=r.version)

    # text ----------------------------------------------------------------------
    def xml(self, norm: Norm) -> str:
        return self._fetch(norm.xml_url or OGD_DOC_URL.format(nor=norm.nor))

    def document(self, ref: str | ActRef, version: str | None = None,
                 sections: Sequence[str] | None = None) -> Document:
        """Fetch and parse a law into a :class:`Document` of provisions (current text by default).

        ``sections`` (``["§ 45", "§ 58"]``) fetches only those §§ / Artikel / Anlagen,
        e.g. the provisions a notified measure cites in a large code; group units
        (Hauptstück, Abschnitt) then appear only where one of them opens a group.
        """
        r = self.resolve(ref, version)
        if version is not None and r.version != version:
            r = ActRef(r.gesetzesnummer, r.bgbl, r.abbreviation, version)
        norms = self.norms(r.act_id, r.version, sections)
        texts = [(n, None if n.is_title else self.xml(n)) for n in norms]
        return parse_norms(texts, act_id=r.act_id, version=r.version)

    def measure_document(self, measure: object, version: str | None = None, whole: bool = False) -> Document:
        """The text a CELLAR measure points to: the cited §§ / Artikel only, or the whole act.

        ``measure`` is a ``cellar.NationalMeasure``, its dict, or a :class:`MeasureRef`.
        """
        m = measure if isinstance(measure, MeasureRef) else measure_reference(measure)
        sections = None if whole else _unique(_section_of(t) for t in m.provisions) or None
        return self.document(m.act_ref(version), version, sections)


def fetch_act(ref: str, version: str | None = None, fetch: Fetcher | None = None) -> Document:
    """Convenience wrapper: ``fetch_act("BGBl. I Nr. 94/2025")`` -> NISG 2026, current consolidated text."""
    return RisClient(fetch).document(ref, version)


_SECTION_TYPES = {"par": "Paragraph", "art": "Artikel", "anl": "Anlage"}


def _section_of(tail: str) -> str:
    """``"par6.abs1.z7"`` -> ``"§ 6"``; ``"art49.abs1"`` -> ``"Art. 49"``."""
    m = re.match(r"(par|art|anl)_?([^.]+)", tail)
    if not m:
        return ""
    return _label(m.group(1), m.group(2))


def _unique(xs: Iterable[str]) -> list[str]:
    out: list[str] = []
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out


def _same_bgbl(stammfassung: str | None, b: BgblRef) -> bool:
    if not stammfassung:
        return False
    s = parse_bgbl(stammfassung)
    return s == b


def _in_force(n: Norm, day: str) -> bool:
    return (not n.in_force or n.in_force <= day) and (not n.out_of_force or n.out_of_force >= day)


def _norm_abbrev(s: str) -> str:
    return re.sub(r"[\s ]+", " ", s.replace("–", "-")).strip().lower()


# --- CELLAR measures ---------------------------------------------------------------


@dataclass
class MeasureRef:
    """Where a notified national measure points in RIS.

    ``ref`` is a reference :func:`parse_reference` accepts: the Stammfassung
    citation (``"BGBl. I Nr. 5/2024"``) when the title gives one, else the official
    abbreviation (``"NISG 2026"``); ``None`` when the title names nothing fetchable.
    ``abbreviation`` is kept as well to tell apart laws promulgated in the same
    BGBl issue. ``provisions`` lists the ID tails of the cited provisions
    (``["par6.abs1.z7"]``) when the measure is only part of a law; turn them into
    IDs with :func:`provision_id`, which also finds a § inside an Artikel
    (``par4.abs1`` of the DSG is ``at:10001597:art2.par4.abs1``). ``basis`` says how
    the reference was found.
    """

    ref: str | None
    abbreviation: str | None = None
    provisions: list[str] = field(default_factory=list)
    basis: str = ""
    kind: str | None = None  # CELLAR act type: "Bundesgesetz", "Verordnung", ...

    def act_ref(self, version: str | None = None) -> ActRef:
        """The reference as an :class:`ActRef` for :meth:`RisClient.resolve` / :meth:`RisClient.document`."""
        if not self.ref:
            raise LookupError(f"no RIS reference for this measure ({self.basis})")
        r = parse_reference(self.ref, version)
        if self.abbreviation and r.abbreviation is None:
            r = ActRef(r.gesetzesnummer, r.bgbl, self.abbreviation, r.version)
        return r


_PROV_REF = re.compile(
    r"(?:(?P<art>Art\.?|Artikel)\s*(?P<artn>\d+[a-z]?)|§\s*(?P<par>\d+[a-z]*))"
    r"(?:\s*Abs\.?\s*(?P<abs>\d+[a-z]*))?"
    r"(?:\s*Z\.?\s*(?P<z>\d+[a-z]*))?"
    r"(?:\s*lit\.?\s*(?P<lit>[a-z]{1,3}))?"
)
_ABBR = r"[A-ZÄÖÜ][A-Za-zÄÖÜäöü\-]*[A-ZÄÖÜ](?:\s+\d{4})?"
# "(Langtitel – NISG 2026)", "1991 – AVG;", "(VStG)", "(B-VG)"
_ABBREV_DASH = re.compile(rf"\s[–-]\s*(?P<abbr>{_ABBR})\s*(?:[);,]|$)")
_ABBREV_PAREN = re.compile(rf"\((?P<abbr>{_ABBR})\)")
_STAMM = re.compile(r"Stammfassung[:\s]+(?P<ref>BGBl\.?\s*(?:I{1,3}\s*)?Nr\.?\s*\d+[a-z]?/\d{4})", re.IGNORECASE)


def measure_reference(measure: object) -> MeasureRef:
    """Map a CELLAR national measure (``cellar.NationalMeasure`` or its dict) to an adapter reference.

    CELLAR records Austrian measures with an empty ``reference`` and the
    citation inside the German ``title``, in two shapes:

    * a whole act: ``Bundesgesetz ... (Netz- und Informationssystemsicherheitsgesetz 2026 – NISG 2026)
      erlassen wird ...``; no BGBl number, so the official abbreviation is used;
    * cited provisions of an existing act: ``§ 6 Abs. 1 Z 7 des ... (Informationsfreiheitsgesetz – IFG);
      Stammfassung BGBl. I Nr. 5/2024, zuletzt geändert durch BGBl. I Nr. 52/2025``; the
      Stammfassung (or the first BGBl citation, before ``zuletzt geändert``) identifies the
      law and the provisions become ID tails.

    The result still has to be resolved against RIS: ``client.document(m.act_ref())``.
    """
    get = measure.get if isinstance(measure, dict) else lambda k: getattr(measure, k, None)  # type: ignore[union-attr]
    title = (get("title") or "").replace("\u00a0", " ")
    reference = (get("reference") or "").strip()
    kind = get("act_type")
    if reference:
        try:
            parse_reference(reference)
            return MeasureRef(ref=reference, basis="reference", kind=kind)
        except ValueError:
            title = f"{reference} {title}"
    provisions: list[str] = []
    if re.match(r"^\s*(§|Art)", title):
        head = _expand_pars(re.split(r"\s(?:des|der)\s", title, maxsplit=1)[0])
        provisions = _unique(t for t in (_prov_tail(m) for m in _PROV_REF.finditer(head)) if t)
    am = _ABBREV_DASH.search(title) or _ABBREV_PAREN.search(title)
    abbr = am["abbr"] if am else None
    m = _STAMM.search(title)
    if m:
        return MeasureRef(ref=parse_bgbl(m["ref"]).citation, abbreviation=abbr,  # type: ignore[union-attr]
                          provisions=provisions, basis="Stammfassung in title", kind=kind)
    b = parse_bgbl(title.split("zuletzt geändert")[0])
    if b:
        return MeasureRef(ref=b.citation, abbreviation=abbr, provisions=provisions,
                          basis="BGBl citation in title", kind=kind)
    if abbr:
        return MeasureRef(ref=abbr, abbreviation=abbr, provisions=provisions,
                          basis="abbreviation in title", kind=kind)
    return MeasureRef(ref=None, provisions=provisions, basis="no citation found", kind=kind)


_PARS = re.compile(r"§§\s*(?P<list>[^§]*?)(?=\s*(?:Art\.?\s*\d|Artikel\s|§|$))")
_PAR_RANGE = re.compile(r"\b(?P<a>\d+)\s*(?:bis|-|–)\s*(?P<b>\d+)\b")
_PAR_NEXT = re.compile(r"(?P<sep>^|,\s*|\bund\s+|\bsowie\s+)(?P<num>\d+[a-z]*)\b")


def _expand_pars(head: str) -> str:
    """``"§§ 45 und 58"`` -> ``"§ 45 und § 58"``; ``"§§ 3 bis 5"`` -> ``"§ 3, § 4, § 5"``."""

    def one(m: re.Match) -> str:
        lst = _PAR_RANGE.sub(
            lambda r: ", ".join(str(n) for n in range(int(r["a"]), int(r["b"]) + 1))
            if 0 < int(r["b"]) - int(r["a"]) <= 50 else f"{r['a']}, {r['b']}",
            m["list"])
        return _PAR_NEXT.sub(lambda x: f"{x['sep']}§ {x['num']}", lst)

    return _PARS.sub(one, head)


def provision_id(doc: Document, tail: str) -> str | None:
    """The ID of the provision an ID tail names in ``doc`` (``None`` if absent).

    ``"par4.abs1"`` is ``at:<Gesetzesnummer>:par4.abs1``, or, in a law whose §§ sit
    in Artikel (DSG: ``Art. 2 § 4``), ``at:<Gesetzesnummer>:art2.par4.abs1``; a
    § number found in several Artikel is ambiguous and gives ``None``.
    """
    prefix = f"{SOURCE}:{doc.act_id}:"
    if doc.get(prefix + tail) is not None:
        return prefix + tail
    if not tail.startswith("par"):
        return None
    hits = [p.id for p in doc if p.id.startswith(prefix) and p.id.endswith("." + tail)
            and re.fullmatch(r"(?:art[^.]+\.)+", p.id[len(prefix):-len(tail)])]
    return hits[0] if len(hits) == 1 else None


def _prov_tail(m: re.Match) -> str:
    path: list[tuple[str, str]] = []
    if m["art"]:
        path.append(("art", m["artn"]))
    if m["par"]:
        path.append(("par", m["par"]))
    for kind in ("abs", "z", "lit"):
        if m[kind]:
            path.append((kind, m[kind]))
    return ".".join(make_segment(k, n) for k, n in path)


# --- XML to text -----------------------------------------------------------------


def _local(tag: str) -> str:
    return tag[len(NS):] if tag.startswith(NS) else tag


_SKIP_TEXT = {"kzinhalt", "fzinhalt", "layoutdaten", "feld"}


def xml_text(el: ET.Element, skip: Sequence[str] = ()) -> str:
    """Plain text of a RIS XML element: ``gdash`` -> ``-``, tabs -> space, tables -> ``a | b`` rows.

    Child elements named in ``skip`` (local names) are left out with their text, but
    the text that follows them (their ``tail``) is kept.
    """
    parts: list[str] = []

    def rec(e: ET.Element) -> None:
        tag = _local(e.tag)
        if tag in skip or tag in _SKIP_TEXT:
            return
        if tag == "gdash":
            parts.append("-")
        elif tag in ("tab", "abstand"):
            parts.append(" ")
        elif tag == "br":
            parts.append("\n")
        elif tag == "tr":
            parts.append("\n")
        elif tag == "td" and parts and not parts[-1].endswith("\n"):
            parts.append(" | ")
        if e.text:
            parts.append(e.text)
        for c in e:
            rec(c)
            if c.tail:
                parts.append(c.tail)
        if tag in ("absatz", "listelem", "schlussteil", "ueberschrift"):
            parts.append("\n")

    if el.text and _local(el.tag) not in skip:
        parts.append(el.text)
    for c in el:
        rec(c)
        if c.tail:
            parts.append(c.tail)
    raw = "".join(parts).replace("\xa0", " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def _body(root: ET.Element) -> list[ET.Element]:
    """Top-level elements of the norm text: everything after the ``Text`` / ``Präambel`` title
    heading up to the next metadata title heading (``Schlagworte``, ``Zuletzt aktualisiert am`` ...)."""
    nd = root.find(f"{NS}nutzdaten")
    if nd is None:
        return []
    out: list[ET.Element] = []
    for ab in nd.iter(f"{NS}abschnitt"):
        on = False
        for e in ab:
            tag = _local(e.tag)
            if tag in ("kzinhalt", "fzinhalt"):
                continue
            if tag == "ueberschrift" and e.get("typ") == "titel":
                on = xml_text(e) == "Text"
                continue
            if on:
                out.append(e)
    return out


# --- norms to provisions ------------------------------------------------------------


def _apa_path(apa: str) -> list[tuple[str, str]]:
    """``"Art. 1 § 2"`` -> ``[("art", "1"), ("par", "2")]``; ``"Anl. 1"`` -> ``[("anl", "1")]``."""
    path: list[tuple[str, str]] = []
    for m in _APA_TOKEN.finditer(apa):
        word, num = m.group(1), (m.group(2) or "0").rstrip(".")
        kind = {"Art.": "art", "Artikel": "art", "§": "par", "§§": "par", "Anl.": "anl", "Anlage": "anl"}[word]
        path.append((kind, num))
    if not path and apa.strip():
        path.append(("doc", apa))
    return path


def _apa_key(apa: str) -> tuple:
    """Sort key in document order: ``"Art. 1 § 1"`` < ``"Art. 2 § 4"`` < ``"Art. 2 § 35b"`` < ``"Anl. 1"``."""
    out = []
    for kind, num in _apa_path(apa):
        m = re.match(r"(\d*)(.*)", num)
        out.append((1 if kind == "anl" else 2 if kind == "doc" else 0,
                    int(m[1]) if m and m[1] else 0, (m[2] if m else num).lower()))
    return tuple(out)


def _structure(text: str) -> tuple[str, str] | None:
    """``"2. Abschnitt"`` -> ``("abschnitt", "2")``; ``"Zweiter Teil"`` -> ``("teil", "2")``."""
    t = text.strip()
    for rx in (_STRUCT_NUM_FIRST, _STRUCT_NUM_AFTER):
        m = rx.match(t)
        if m:
            return _STRUCT_WORDS[m["unit"].lower()], m["num"]
    m = _STRUCT_ORDINAL.match(t)
    if m and m["ord"].lower() in _ORDINALS:
        return _STRUCT_WORDS[m["unit"].lower()], str(_ORDINALS[m["ord"].lower()])
    return None


def _item_kind(symbol: str) -> tuple[str, str | None]:
    """Kind and number of a list item from its printed symbol: ``1.`` Z, ``a)`` lit, ``aa)`` sublit, dash."""
    s = symbol.strip()
    if m := _Z.match(s):
        return "z", m["num"]
    if m := _LIT.match(s):
        return "lit", m["num"]
    if m := _SUBLIT.match(s):
        return "sublit", m["num"]
    if s in _DASH or not s:
        return "ind", None
    return "pt", s


def _append(p: Provision, text: str) -> None:
    if text:
        p.text = f"{p.text}\n{text}" if p.text else text


class _Builder:
    """Turns norm documents (in order) into provisions of one :class:`Document`."""

    def __init__(self, doc: Document):
        self.doc = doc
        self.act_id = doc.act_id
        self.struct: list[tuple[int, list[tuple[str, str]], Provision]] = []  # (rank, path, provision)
        self.pending: str | None = None  # group heading for the next §
        self.fresh_struct: Provision | None = None  # structural unit still waiting for its heading
        # Artikel wrapping §§ ("Art. 2 § 4"): its path and provision; Hauptstück / Abschnitt nest inside it
        self.wrapper: tuple[list[tuple[str, str]], Provision] | None = None

    def _id(self, path: Sequence[tuple[str, str]], parent: Provision | None = None, suffix: str = "") -> str:
        base = (f"{parent.id}.{make_segment(*path[-1])}" if parent is not None
                else make_id(SOURCE, self.act_id, list(path))) + suffix
        pid, n = base, 1
        while self.doc.get(pid) is not None:
            n += 1
            pid = f"{base}_{n}"
        return pid

    def _new(self, kind: str, number: str, label: str, parent: Provision | None, *,
             path: Sequence[tuple[str, str]] | None = None, text: str = "", heading: str | None = None,
             eli: str | None = None, url: str | None = None, suffix: str = "") -> Provision:
        pid = self._id(path, None) if path is not None else self._id([(kind, number)], parent, suffix)
        p = Provision(id=pid, source=SOURCE, act_id=self.act_id, language=LANGUAGE, kind=kind, label=label,
                      number=normalize_number(number), text=text, heading=heading,
                      parent_id=parent.id if parent else None, eli=eli, url=url)
        return self.doc.add(p)

    # group headings (g1, g2 ...) ------------------------------------------------
    def group_heading(self, text: str, url: str | None = None) -> None:
        st = _structure(text)
        if st is None:
            if self.fresh_struct is not None and not self.fresh_struct.heading:
                self.fresh_struct.heading = text
            else:
                self.pending = f"{self.pending} {text}" if self.pending else text
            return
        kind, num = st
        rank = STRUCTURAL.index(kind)
        while self.struct and self.struct[-1][0] >= rank:
            self.struct.pop()
        if self.struct:
            parent, base = self.struct[-1][2], self.struct[-1][1]
        elif self.wrapper is not None:
            base, parent = self.wrapper
        else:
            parent, base = None, []
        path = list(base) + [(kind, num)]
        p = self._new(kind, num, text, parent, path=path, url=url)
        self.struct.append((rank, path, p))
        self.fresh_struct = p
        self.pending = None

    # one norm document ----------------------------------------------------------------
    def norm(self, norm: Norm, xml: str) -> None:
        root = ET.fromstring(xml)
        body = _body(root)
        path = _apa_path(norm.apa)
        if not path:
            return
        url = norm.eli or norm.html_url
        # Artikel wrapping §§ ("Art. 2 § 4"): the Artikel holds the Hauptstücke / Abschnitte that
        # its §§ sit in, so it is opened before the leading headings are read
        wrap = list(path[:-1])
        if wrap:
            if self.wrapper is None or self.wrapper[0] != wrap:
                parent = None
                for depth in range(len(wrap)):
                    sub = wrap[: depth + 1]
                    existing = self.doc.get(make_id(SOURCE, self.act_id, sub))
                    if existing is None:
                        k, n = sub[-1]
                        existing = self._new(k, n, _label(k, n), parent, path=sub, url=url)
                    parent = existing
                self.wrapper = (wrap, parent)
                self.struct = []
                self.fresh_struct = parent if not parent.heading else None
        elif self.wrapper is not None and path[0][0] != "anl":  # a plain § / Artikel after them
            self.wrapper, self.struct = None, []
        # leading headings belong before the anchor is created
        i, para_heading, anl_label = 0, None, None
        while i < len(body) and _local(body[i].tag) == "ueberschrift":
            typ, t = body[i].get("typ") or "", xml_text(body[i])
            if typ == "para":
                para_heading = f"{para_heading} {t}" if para_heading else t
            elif typ == "anlage":
                anl_label = t
            elif t and wrap and _ARTIKEL_HEADING.match(t):
                pass  # "Artikel 2" opening the wrapping Artikel: its label
            elif t:
                if path[-1][0] == "anl" and anl_label is not None:
                    para_heading = f"{para_heading} {t}" if para_heading else t
                else:
                    self.group_heading(t, url)
            i += 1
        # annexes stand outside the Hauptstück / Abschnitt structure
        if path[0][0] == "anl":
            parent = None
        elif self.struct:
            parent = self.struct[-1][2]
        else:
            parent = self.wrapper[1] if self.wrapper is not None else None
        kind, num = path[-1]
        heading = para_heading or self.pending
        self.pending, self.fresh_struct = None, None
        anchor = self._new(kind, num, anl_label or _label(kind, num), parent, path=path,
                           heading=heading, eli=norm.eli, url=url)
        rest = body[i:]
        if kind == "anl":  # annexes stay one provision with their flattened text
            for e in rest:
                _append(anchor, xml_text(e))
            return
        _Section(self, anchor, url).run(rest)


def _label(kind: str, num: str) -> str:
    return {"par": f"§ {num}", "art": f"Art. {num}", "anl": f"Anlage {num}"}.get(kind, num)


class _Section:
    """Absätze and lists inside one § / Artikel document."""

    def __init__(self, b: _Builder, anchor: Provision, url: str | None):
        self.b, self.anchor, self.url = b, anchor, url
        self.abs: Provision | None = None
        self.stack: list[tuple[int, Provision]] = []  # open list items by ebene
        self.dash_count: dict[str, int] = {}
        # a later list in the same owner whose numbers clash with an earlier one gets one suffix
        # on every item (Z 1_2, Z 2_2 ...), chosen at its first item: (parent id, list) -> suffix
        self.list_no = 0
        self.suffix: dict[tuple[str, int], str] = {}

    def owner(self) -> Provision:
        return self.abs or self.anchor

    def deepest(self) -> Provision:
        return self.stack[-1][1] if self.stack else self.owner()

    def run(self, elements: Iterable[ET.Element]) -> None:
        for e in elements:
            tag = _local(e.tag)
            if tag == "absatz":
                self.absatz(e)
            elif tag == "liste":
                self.list_no += 1
                self.liste(e)
            elif tag == "ueberschrift":
                t = xml_text(e)
                if e.get("typ") == "para" and not self.anchor.heading and not self.anchor.text and self.abs is None:
                    self.anchor.heading = t
                else:  # a heading inside the § (rare): keep it as text
                    _append(self.deepest(), t)
            else:
                _append(self.deepest(), xml_text(e))

    def absatz(self, e: ET.Element) -> None:
        text = xml_text(e, skip=("gldsym",))
        m = _ABS.match(text)
        if m and e.get("typ") in ("abs", None, ""):
            self.stack.clear()
            own = (m["pre"] or "").strip()
            rest = text[m.end():]
            self.abs = self.b._new("abs", m["num"], f"({m['num']})", self.anchor,
                                   text=f"{own} {rest}".strip() if own else rest, url=self.url)
            return
        if self.stack:  # unnumbered text after a list: closes the list
            self.stack.clear()
        _append(self.owner(), text)

    def liste(self, e: ET.Element) -> None:
        for child in e:
            tag = _local(child.tag)
            if tag in _ITEM_LISTS:
                level = int(float(child.get("ebene") or 1))  # RIS sometimes writes ebene="0.5"
                for item in child:
                    if _local(item.tag) == "listelem":
                        self.item(item, level)
                    elif _local(item.tag) == "liste":
                        self.liste(item)
            elif tag == "schlussteil":
                level = int(float(child.get("ebene") or 0))
                while self.stack and self.stack[-1][0] > level:
                    self.stack.pop()
                target = self.stack[-1][1] if (self.stack and level > 0) else self.owner()
                if level == 0:
                    self.stack.clear()
                _append(target, xml_text(child))
            elif tag == "liste":
                self.liste(child)
            else:
                _append(self.deepest(), xml_text(child))

    def item(self, el: ET.Element, level: int) -> None:
        sym_el = el.find(f"{NS}symbol")
        symbol = xml_text(sym_el) if sym_el is not None else ""
        text = xml_text(el, skip=("symbol", "liste", *_ITEM_LISTS))
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        parent = self.stack[-1][1] if self.stack else self.owner()
        kind, num = _item_kind(symbol)
        if num is None:
            self.dash_count[parent.id] = self.dash_count.get(parent.id, 0) + 1
            num = str(self.dash_count[parent.id])
        key = (parent.id, self.list_no)
        if key not in self.suffix:
            base, suffix, n = f"{parent.id}.{make_segment(kind, num)}", "", 1
            while self.b.doc.get(base + suffix) is not None:
                n += 1
                suffix = f"_{n}"
            self.suffix[key] = suffix
        p = self.b._new(kind, num, symbol or "–", parent, text=text, url=self.url, suffix=self.suffix[key])
        self.stack.append((level, p))
        for nested in el:
            if _local(nested.tag) == "liste":
                self.liste(nested)


def _title(norms: Sequence[Norm]) -> tuple[str | None, str | None]:
    """Long title (``§ 0`` document, without the StF / CELEX lines) and the Stammfassung citation."""
    title_doc = next((n for n in norms if n.is_title and n.titel), None)
    any_norm = norms[0] if norms else None
    stf = next((n.stammfassung for n in norms if n.stammfassung), None)
    if title_doc is not None:
        t = re.split(r"<br\s*/?>", title_doc.titel or "")[0]
        t = re.sub(r"<[^>]+>", "", t).strip()
    else:
        t = (any_norm.kurztitel if any_norm else None) or None
        if t and any_norm and any_norm.abkuerzung:
            t = f"{t} ({any_norm.abkuerzung})"
    if t and stf:
        t = f"{t}, StF: {stf}"
    return t or None, stf


def parse_norms(
    items: Iterable[tuple[Norm, str | None]], act_id: str | None = None, version: str | None = None
) -> Document:
    """Turn ``(norm, xml)`` pairs in document order into a :class:`Document`.

    ``xml`` may be ``None`` for the ``§ 0`` title document (only its metadata is
    used). ``Document.date`` is the date the returned consolidated version took
    effect: the latest ``Inkrafttretensdatum`` among the documents (all of them
    are in force on the requested day). ``Document.eli`` is the ELI of the
    Stammfassung as promulgated in the BGBl (``None`` for laws promulgated before
    2004: RIS has no ELI for those issues); ``Document.url`` is the RIS page of
    the consolidated law (``GeltendeFassung``) for the requested day. Provision
    ``eli``/``url`` are set on §, Artikel and Anlage (RIS has one ELI per norm
    document); Absätze and list items link to their § document.
    """
    items = list(items)
    norms = [n for n, _ in items]
    act_id = act_id or next((n.gesetzesnummer for n in norms if n.gesetzesnummer), None)
    if not act_id:
        raise ValueError("act_id not given and no Gesetzesnummer in the norms")
    title, stf = _title(norms)
    dates = sorted(n.in_force for n in norms if n.in_force)
    bgbl = parse_bgbl(stf) if stf else None
    q = {"Abfrage": "Bundesnormen", "Gesetzesnummer": act_id}
    if version:
        q["FassungVom"] = version
    doc = Document(
        source=SOURCE,
        act_id=act_id,
        language=LANGUAGE,
        title=title,
        date=dates[-1] if dates else None,
        eli=bgbl.eli if bgbl else None,
        url=f"{BASE_URL}/GeltendeFassung.wxe?{urllib.parse.urlencode(q)}",
    )
    b = _Builder(doc)
    for norm, xml in items:
        if norm.is_title or xml is None:
            continue
        b.norm(norm, xml)
    return doc
