"""Slovak adapter: consolidated laws from Slov-Lex, split into § / ods. / písm. / bod.

Slov-Lex (https://www.slov-lex.sk) is the official legal information portal of
the Slovak Republic (Ministry of Justice); it publishes the Collection of Laws
(Zbierka zákonov, ``Z. z.``, and before 1993 ``Zb.``) with every consolidated
version (*časová verzia*) of every act. The current portal is a JavaScript app
under ``/ezbierky/pravne-predpisy/SK/ZZ/{year}/{number}/{version}``; it loads
its texts from the **static version of the portal** on ``static.slov-lex.sk``,
plain server-rendered HTML that needs no key, no JavaScript and no session:

``GET https://static.slov-lex.sk/static/SK/ZZ/{year}/{number}/``
    Version history (*História predpisu*): one row per version with its IRI
    (``data-iri="/SK/ZZ/2018/69/20250101"``), the dates it is in force
    (``data-ucinnostod`` / ``data-ucinnostdo``, the last version open-ended,
    future versions included) and the amending act(s) that produced it
    (*Novela*, e.g. ``366/2024 Z. z.``). ``vyhlasene_znenie`` is the text as
    promulgated. Unknown acts are HTTP 404.

``GET https://static.slov-lex.sk/static/SK/ZZ/{year}/{number}/{version}.html``
    One version's full text. The body (``div#predpis``, then ``div#prilohy``
    and ``div#poznamky``) is generated from Slov-Lex's structured XML: every unit
    is ``<div class="{kind} Skupina" id="...">`` with ``{kind}Oznacenie`` (number
    as printed), ``{kind}Nadpis`` (heading) and ``text`` children. Kinds:
    ``cast`` / ``hlava`` / ``diel`` / ``oddiel`` (structure), ``skupinaParagrafov``
    / ``skupinaElementov`` (unnumbered group headings), ``clanok`` (Čl.),
    ``paragraf`` (§), ``odsek`` (ods.), ``pismeno`` (písm.), ``bod``, ``priloha``,
    ``poznamka`` (footnote) and ``oznacenaPolozka`` (items in annexes). The HTML
    ids double as anchors: ``#paragraf-12.odsek-3.pismeno-b``. Text quoted by an
    amending act sits inside ``div.citat``.

The JSON search API behind the app (``https://api-gateway.slov-lex.sk/vyhladavanie/
predpisZbierky/znenie?zodpovedajucaUcinnost=YYYY-MM-DD&predpis=/SK/ZZ/2018/69``)
answers which version is in force on a date, but the history page above gives the
same and more (all versions, amending acts), so this adapter does not need it.
Bulk alternative: ``https://static.slov-lex.sk/static/exporty/index.portal`` links
ZIP archives of the whole collection in HTML, all versions (large).

Provision IDs are anchored at the § / článok, as Slovak law is cited:
``§ 12 ods. 3 písm. b)`` of Act 69/2018 Z. z. is ``sk:69/2018:par12.ods3.pism_b``
whatever part, chapter or článok it sits in. Structural units keep their own
path (``sk:452/2021:cast2.hlava4``); annexes are ``priloha1`` ... (``priloha0``
when unnumbered) with their text flattened; footnotes are ``pozn10a``.
An unnumbered odsek (a § with a single paragraph) or bod is folded into its
parent, so letters of such a § hang off the §: ``sk:69/2018:par1.pism_a``.

``Document.date`` is the date the version is in force from (``None`` for the text
as promulgated). ``Document.eli`` is the version's ELI-template URI on Slov-Lex
(``https://www.slov-lex.sk/pravne-predpisy/SK/ZZ/2018/69/20260430``); Slov-Lex has
no ELI for subdivisions, so ``Provision.eli`` is empty and ``Provision.url`` links the
anchor in the static text (the app does not scroll to anchors).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from ..cellar import USER_AGENT
from ..provisions import Document, Provision, make_id, make_segment, normalize_number, roman_to_int

PORTAL_URL = "https://www.slov-lex.sk"
STATIC_URL = "https://static.slov-lex.sk"

SOURCE = "sk"
LANGUAGE = "sk"

Fetcher = Callable[[str], str]

#: HTML unit kind -> provision kind used in IDs.
KINDS = {
    "paragraf": "par",
    "clanok": "cl",
    "odsek": "ods",
    "pismeno": "pism",
    "bod": "bod",
    "priloha": "priloha",
    "poznamka": "pozn",
    "cast": "cast",
    "hlava": "hlava",
    "diel": "diel",
    "oddiel": "oddiel",
    "pododdiel": "pododdiel",
}
#: HTML unit kinds read as another kind: the articles of the Constitution (460/1992 Zb.) are
#: ``ustavnyclanok`` units (``#ustavnyclanok-127.odsek-1``), cited as ``Čl. 127 ods. 1``.
ALIASES = {"ustavnyclanok": "clanok"}
STRUCTURAL = {"cast", "hlava", "diel", "oddiel", "pododdiel"}
GROUPS = {"skupinaParagrafov", "skupinaElementov"}  # unnumbered headings over several §§
SUBUNITS = {"odsek", "pismeno", "bod"}

PROMULGATED = "vyhlasene_znenie"

# Ordinal words used in the HTML ids of structural units: cast-druha, hlava-prva, diel-prvy.
_ORDINALS = {
    "prv": 1, "druh": 2, "tret": 3, "stvrt": 4, "piat": 5, "siest": 6, "siedm": 7, "osm": 8,
    "deviat": 9, "desiat": 10, "jedenast": 11, "dvanast": 12, "trinast": 13, "strnast": 14,
    "patnast": 15, "sestnast": 16, "sedemnast": 17, "osemnast": 18, "devatnast": 19, "dvadsiat": 20,
    "tridsiat": 30, "styridsiat": 40, "patdesiat": 50,
}

_REF = re.compile(
    r"^(?:(?:zákon|zákona|nariadenie vlády|vyhláška|vyhlášky)\s+)?(?:č\.\s*)?"
    r"(?P<num>\d+[a-z]?)\s*/\s*(?P<year>\d{4})"
    r"(?:\s*-\s*(?P<anum>\d+[a-z]?)\s*/\s*(?P<ayear>\d{4}))?"
    r"(?:\s*(?:Z\.\s*z\.|Zb\.)?)?\s*$",
    re.IGNORECASE,
)
_PATH = re.compile(
    r"(?:^|/)SK/ZZ/(?P<year>\d{4})/(?P<num>\d+[a-z]?)"
    r"(?:/(?P<ver>\d{8}|vyhlasene_znenie)(?:\.html|\.portal)?)?(?:[/#?]|$)",
    re.IGNORECASE,
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ANNEX_NUM = re.compile(r"^príloha\s+(?:č\.\s*)?(\d+[a-z]?)\b", re.IGNORECASE)


# --- references ---------------------------------------------------------------


@dataclass(frozen=True)
class ActRef:
    """An act in the Zbierka zákonov, optionally with a version.

    ``version`` is ``None`` (in force today), ``"YYYY-MM-DD"`` (in force on that day),
    ``"YYYYMMDD"`` (a Slov-Lex version id, exact) or ``"vyhlasene_znenie"`` (as promulgated).
    ``amended_by`` (``"366/2024"``) selects the version produced by that amending act,
    as in CELLAR's ``"69/2018-366/2024"``.
    """

    year: int
    number: str
    version: str | None = None
    amended_by: str | None = None

    @property
    def act_id(self) -> str:
        """National reference as used in OpenAcquis IDs and CELLAR: ``"69/2018"``."""
        return f"{self.number}/{self.year}"

    @property
    def iri(self) -> str:
        """Slov-Lex IRI of the act: ``/SK/ZZ/2018/69``."""
        return f"/SK/ZZ/{self.year}/{self.number}"

    @property
    def citation(self) -> str:
        """``"69/2018 Z. z."`` (``Zb.`` before 1993)."""
        return f"{self.act_id} {'Zb.' if self.year < 1993 else 'Z. z.'}"

    @property
    def history_url(self) -> str:
        return f"{STATIC_URL}/static{self.iri}/"

    @property
    def web_url(self) -> str:
        return f"{PORTAL_URL}/ezbierky/pravne-predpisy{self.iri}/"


def _normalize_version(version: str) -> str:
    v = version.strip()
    if v in ("0000-00-00", PROMULGATED, "vyhlasene"):
        return PROMULGATED
    if _DATE.match(v):
        _dt.date.fromisoformat(v)  # raises ValueError on 2025-13-01
        return v
    if re.fullmatch(r"\d{8}", v):
        return v
    raise ValueError(f"version must be YYYY-MM-DD, a Slov-Lex version id (YYYYMMDD) or 0000-00-00, got {version!r}")


def parse_reference(ref: str, version: str | None = None) -> ActRef:
    """Parse ``"69/2018"``, ``"č. 69/2018 Z. z."``, ``"40/1964 Zb."``, ``"69/2018-366/2024"``
    (CELLAR: as amended by 366/2024), ``"/SK/ZZ/2018/69/20250101"`` or a Slov-Lex URL.

    ``version`` overrides a version found in ``ref``: ``"YYYY-MM-DD"`` (in force on that
    day), ``"YYYYMMDD"`` (Slov-Lex version id) or ``"0000-00-00"`` (as promulgated).
    """
    s = " ".join(ref.split())
    m = _REF.match(s)
    if m:
        amended = f"{m['anum'].lower()}/{m['ayear']}" if m["anum"] else None
        out = ActRef(year=int(m["year"]), number=m["num"].lower(), amended_by=amended)
    else:
        m = _PATH.search(urllib.parse.unquote(s))
        if not m:
            raise ValueError(
                f"{ref!r} is not a Slovak Collection of Laws reference (expected e.g. '69/2018' or '69/2018 Z. z.')"
            )
        ver = m["ver"].lower() if m["ver"] else None
        out = ActRef(year=int(m["year"]), number=m["num"].lower(), version=ver)
    if version is not None:
        out = ActRef(out.year, out.number, _normalize_version(version))
    return out


# --- CELLAR national measures -----------------------------------------------------


@dataclass(frozen=True)
class MeasureRef:
    """A CELLAR national measure mapped to the adapter: the act to fetch and the version.

    ``act_id`` is the act whose text is fetched (``"69/2018"``); ``amended_by`` the
    amending act whose consolidated version was notified (``"366/2024"``, or ``None``
    when the measure is an act on its own); ``amends`` the acts an amending act
    changes, read from its title (hints only).
    """

    act_id: str
    amended_by: str | None = None
    amends: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        """Reference for :func:`parse_reference` / ``openacquis law sk``."""
        return f"{self.act_id}-{self.amended_by}" if self.amended_by else self.act_id


_MEASURE_REF = re.compile(r"^\s*(\d+[a-z]?)\s*/\s*(\d{4})\s*(?:-\s*(\d+[a-z]?)\s*/\s*(\d{4}))?\s*$", re.I)
_TITLE_ACT = re.compile(
    r"^\s*(?:zákon|nariadenie vlády|vyhláška|výnos|oznámenie|opatrenie|ústavný zákon)\b[^č\d]{0,120}?"
    r"(?:č\.\s*)?(\d+[a-z]?)\s*/\s*(\d{4})\s*(?:Z\.\s*z\.|Zb\.)?",
    re.IGNORECASE,
)
_AMENDS = re.compile(
    r"(?:ktor(?:ým|ou)\s+sa\s+(?:mení|menia|dopĺňa|dopĺňajú|mení\s+a\s+dopĺňa|menia\s+a\s+dopĺňajú)"
    r"(?:\s+(?:zákon|nariadenie vlády|vyhláška)[^č]{0,80}?)?)\s+č\.\s*(\d+[a-z]?)\s*/\s*(\d{4})",
    re.IGNORECASE,
)
_AS_AMENDED = re.compile(r"as amended by\s+(?:zákon(?:a|om)?\s+)?(?:č\.\s*)?(\d+[a-z]?)\s*/\s*(\d{4})", re.I)


def measure_reference(reference: str | None, title: str | None = None) -> MeasureRef | None:
    """Map a CELLAR national measure (reference, title) to an adapter reference.

    CELLAR's Slovak references are ``"69/2018"`` (the act itself) or
    ``"69/2018-366/2024"`` (act 69/2018 consolidated as amended by 366/2024).
    When the reference is something else (``"MF/22164/2014-74"``: a ministry
    measure outside the Collection of Laws) the title is tried
    (``"Zákon č. 69/2018 Z. z. ..."``). ``None`` if no Collection of Laws act is found.

    >>> measure_reference("69/2018-366/2024").ref
    '69/2018-366/2024'
    """
    act = amended = None
    m = _MEASURE_REF.match(reference or "")
    if m:
        act = f"{m[1].lower()}/{m[2]}"
        if m[3]:
            amended = f"{m[3].lower()}/{m[4]}"
    elif title:
        t = _TITLE_ACT.match(title)
        if t:
            act = f"{t[1].lower()}/{t[2]}"
            a = _AS_AMENDED.search(title)
            if a and f"{a[1]}/{a[2]}" != act:
                amended = f"{a[1].lower()}/{a[2]}"
    if act is None:
        return None
    amends: list[str] = []
    if title and amended is None:
        for a in _AMENDS.finditer(title):
            x = f"{a[1].lower()}/{a[2]}"
            if x != act and x not in amends:
                amends.append(x)
    return MeasureRef(act, amended, tuple(amends))


# --- HTTP with cache and rate limit -------------------------------------------


def default_cache_dir() -> Path:
    """``$OPENACQUIS_CACHE/slovlex``, else ``~/.cache/openacquis/slovlex``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    base = Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"
    return base / "slovlex"


class HttpFetcher:
    """GET HTML with a descriptive User-Agent, a delay between requests and an on-disk cache.

    ``cache_dir=False`` disables the cache. Cached pages older than ``ttl`` seconds are
    refetched; dated versions never change, but the history page gains new versions.
    HTTP 404 (unknown act or version) raises :class:`LookupError`.
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
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".html")

    def __call__(self, url: str) -> str:
        path = self._cache_path(url)
        if path is not None and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return path.read_text(encoding="utf-8")
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            url, headers={"Accept": "text/html", "User-Agent": self.user_agent}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise LookupError(f"Slov-Lex {e.code} for {url}") from e
        finally:
            self._last = time.monotonic()
            self.requests += 1
        text = raw.decode("utf-8", "replace")
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)
        return text


# --- a small DOM ----------------------------------------------------------------

_VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr", "source"}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)  # Node | str

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    @property
    def id(self) -> str:
        return self.attrs.get("id") or ""

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    b = _TreeBuilder()
    b.feed(html)
    b.close()
    return b.root


# --- text -----------------------------------------------------------------------


def _is_footnote_ref(children: list, i: int) -> bool:
    """``<sup>12a</sup>)``: a footnote marker (dropped from text)."""
    c = children[i]
    if not (isinstance(c, Node) and c.tag == "sup"):
        return False
    nxt = children[i + 1] if i + 1 < len(children) else None
    return isinstance(nxt, str) and nxt.lstrip().startswith(")")


def _collect(node: Node, out: list[str], block: bool) -> None:
    skip_paren = False
    kids = node.children
    for i, c in enumerate(kids):
        if isinstance(c, str):
            if skip_paren:
                c = c.lstrip()[1:]
                skip_paren = False
            out.append(re.sub(r"\s+", " ", c))  # source line breaks are not breaks
            continue
        if not block and _is_footnote_ref(kids, i):  # quoted text keeps its markers (they are content)
            skip_paren = True
            continue
        if c.tag in ("script", "style"):
            continue
        if c.tag == "br":
            out.append("\n")
            continue
        if c.tag == "tr":
            out.append("\n")
        elif c.tag in ("td", "th") and i and any(isinstance(k, Node) and k.tag in ("td", "th") for k in kids[:i]):
            out.append(" | ")
        elif c.tag in ("p", "li", "table"):
            out.append("\n")
        elif c.tag == "div" and block:
            cls = c.classes
            if "Skupina" in cls or "citat" in cls or "blokTextu" in cls:
                out.append("\n")
            else:
                out.append(" ")
        _collect(c, out, block)


def _norm(raw: str) -> str:
    raw = raw.replace("\xa0", " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def node_text(node: Node | None, block: bool = False) -> str:
    """Plain text of a node; footnote markers dropped, whitespace normalised.

    ``block=True`` puts each nested unit on its own line, with its number and text
    on one line, and keeps footnote markers (used for quoted text and annexes).
    """
    if node is None:
        return ""
    out: list[str] = []
    _collect(node, out, block)
    return _norm("".join(out))


def html_text(html: str | None) -> str:
    """Plain text of an HTML snippet (footnote markers ``<sup>2</sup>)`` dropped)."""
    if not html:
        return ""
    return node_text(parse_html(html))


# --- versions -------------------------------------------------------------------


@dataclass(frozen=True)
class Version:
    """One version of an act on Slov-Lex (a row of the history table)."""

    iri: str  # /SK/ZZ/2018/69/20250101 or /SK/ZZ/2018/69/vyhlasene_znenie
    valid_from: str | None = None  # YYYY-MM-DD, None for the promulgated text
    valid_to: str | None = None  # YYYY-MM-DD, None if open-ended
    amended_by: tuple[str, ...] = ()  # amending acts that produced this version ("366/2024")

    @property
    def promulgated(self) -> bool:
        return self.iri.endswith("/" + PROMULGATED)

    @property
    def key(self) -> str:
        """``"20250101"`` or ``"vyhlasene_znenie"``."""
        return self.iri.rsplit("/", 1)[-1]

    def in_force_on(self, day: str) -> bool:
        return (not self.promulgated and self.valid_from is not None and self.valid_from <= day
                and (self.valid_to is None or day <= self.valid_to))


def parse_history(html: str) -> list[Version]:
    """Versions listed in a Slov-Lex history table (index page or a version page)."""
    root = parse_html(html)
    out: list[Version] = []
    seen: set[str] = set()
    for tr in root.iter():
        if tr.tag != "tr" or "effectivenessHistoryItem" not in tr.classes:
            continue
        iri = tr.attrs.get("data-iri") or ""
        if not iri or iri in seen:
            continue
        seen.add(iri)
        amended: list[str] = []
        cells = [c for c in tr.children if isinstance(c, Node) and c.tag == "td"]
        if len(cells) >= 3:
            for a in cells[2].iter():
                if a.tag == "a":
                    m = re.search(r"(\d+[a-z]?)\s*/\s*(\d{4})", node_text(a))
                    if m:
                        amended.append(f"{m[1]}/{m[2]}")
        out.append(Version(
            iri=iri,
            valid_from=tr.attrs.get("data-ucinnostod") or None,
            valid_to=tr.attrs.get("data-ucinnostdo") or None,
            amended_by=tuple(amended),
        ))
    return out


def select_version(versions: list[Version], ref: ActRef, today: str) -> Version:
    """The version ``ref`` asks for (see :class:`ActRef`); in force on ``today`` by default.

    With ``amended_by``, the latest version that act produced and that has started by
    ``today``: an act amended in stages (201/2024: 2024-09-01, 2024-10-01, 2025-01-01)
    is "as amended by" all stages in force, not only the first.
    """
    if not versions:
        raise LookupError(f"Slov-Lex lists no versions of {ref.citation}")
    dated = [v for v in versions if not v.promulgated]
    have = ", ".join(v.key for v in versions)
    if ref.amended_by:
        hits = [v for v in dated if ref.amended_by in v.amended_by]
        if not hits:
            raise LookupError(f"no version of {ref.citation} produced by {ref.amended_by} (versions: {have})")
        # an amending act that comes into force in stages produces several versions; the text
        # "as amended by" it is the latest of them already in force (the first if none has started)
        started = [v for v in hits if v.valid_from and v.valid_from <= today]
        return started[-1] if started else hits[0]
    if ref.version == PROMULGATED:
        for v in versions:
            if v.promulgated:
                return v
        raise LookupError(f"no promulgated text of {ref.citation} on Slov-Lex")
    if ref.version and not _DATE.match(ref.version):  # exact version id
        for v in versions:
            if v.key == ref.version:
                return v
        raise LookupError(f"{ref.citation} has no version {ref.version} (versions: {have})")
    day = ref.version or today
    for v in dated:
        if v.in_force_on(day):
            return v
    if ref.version:
        raise LookupError(f"no version of {ref.citation} in force on {day} (versions: {have})")
    started = [v for v in dated if v.valid_from and v.valid_from <= day]
    if started:
        return started[-1]
    return next((v for v in versions if v.promulgated), versions[0])


# --- client -------------------------------------------------------------------


class SlovLexClient:
    """Fetch an act's consolidated text from Slov-Lex. Pass ``fetch`` (url -> HTML) to stub the network.

    ``today`` (``"YYYY-MM-DD"``) fixes the date used to pick the current version (tests).
    """

    def __init__(self, fetch: Fetcher | None = None, today: str | None = None):
        self._fetch = fetch or HttpFetcher()
        self.today = today

    def _today(self) -> str:
        return self.today or _dt.date.today().isoformat()

    def versions(self, ref: str | ActRef) -> list[Version]:
        """All versions of the act, oldest first (promulgated text first)."""
        r = ref if isinstance(ref, ActRef) else parse_reference(ref)
        versions = parse_history(self._fetch(r.history_url))
        if not versions:
            raise LookupError(f"Slov-Lex has no version history for {r.citation} ({r.history_url})")
        return versions

    def version(self, ref: str | ActRef, version: str | None = None) -> Version:
        """The version ``ref`` / ``version`` selects (in force today by default)."""
        r = ref if isinstance(ref, ActRef) else parse_reference(ref, version)
        if r.version and not _DATE.match(r.version) and not r.amended_by and r.version != PROMULGATED:
            return Version(iri=f"{r.iri}/{r.version}")  # exact version id: no history needed
        return select_version(self.versions(r), r, self._today())

    def text_url(self, v: Version) -> str:
        return f"{STATIC_URL}/static{v.iri}.html"

    def document(self, ref: str | ActRef, version: str | None = None) -> Document:
        """Fetch and parse an act into a :class:`Document` of provisions."""
        r = ref if isinstance(ref, ActRef) else parse_reference(ref, version)
        v = self.version(r)
        return parse_act(self._fetch(self.text_url(v)), act_id=r.act_id, iri=v.iri)


def fetch_act(ref: str, version: str | None = None, fetch: Fetcher | None = None) -> Document:
    """Convenience wrapper: ``fetch_act("69/2018")`` -> text in force today."""
    return SlovLexClient(fetch).document(ref, version)


# --- HTML to provisions --------------------------------------------------------------


def _unit_kind(node: Node) -> str | None:
    cls = node.classes
    if node.tag == "div" and "Skupina" in cls and cls:
        return cls[0]
    return None


def _part(node: Node, suffix: str) -> Node | None:
    """Direct child div whose first class is ``{kind}{suffix}`` (Oznacenie / Nadpis)."""
    for c in node.children:
        if isinstance(c, Node) and c.tag == "div" and c.classes and c.classes[0].endswith(suffix):
            return c
    return None


def _id_number(html_id: str, kind: str) -> str | None:
    """Number from the last HTML id segment: ``paragraf-12.odsek-3`` -> ``3``; ``bod~1`` -> None."""
    last = html_id.rsplit(".", 1)[-1]
    m = re.fullmatch(rf"{kind}-([^~]+)(?:~\d+)?", last)
    return m.group(1) if m else None


def _ordinal(word: str) -> int | None:
    """Slovak ordinal (or roman numeral) to int: ``"druha"`` -> 2, ``"dvadsiata_prva"`` -> 21, ``"IV"`` -> 4."""
    w = word.lower().translate(str.maketrans("áäčďéíĺľňóôŕšťúýž", "aacdeillnoorstuyz"))
    parts = [x for x in re.split(r"[_\s-]+", w) if x]
    if len(parts) == 1 and (r := roman_to_int(parts[0])) is not None:
        return r
    total = 0
    for part in parts:
        for stem in sorted(_ORDINALS, key=len, reverse=True):
            if part.startswith(stem) and len(part) - len(stem) <= 2:
                total += _ORDINALS[stem]
                break
        else:
            return None
    return total or None


def _structural_number(node: Node, kind: str, label: str) -> str:
    raw = _id_number(node.id, kind) or ""
    if re.fullmatch(r"\d+[a-z]?", raw):
        return raw
    n = _ordinal(raw) if raw else None
    if n is None and label:
        words = [w for w in label.split() if w.lower().rstrip(".") not in (
            "časť", "hlava", "diel", "oddiel", "pododdiel")]
        n = _ordinal(" ".join(words)) if words else None
        if n is None:
            m = re.search(r"\b(\d+[a-z]?)\b", label)
            if m:
                return m.group(1)
    if n is not None:
        return str(n)
    return raw or "0"


def _title_case_type(typ: str) -> str:
    words = [w.lower() if w.isupper() else w for w in typ.split()]
    s = " ".join(words)
    return s[:1].upper() + s[1:]


@dataclass
class _Ctx:
    parent: Provision | None = None  # provision that owns text and children
    struct: Provision | None = None  # nearest structural unit (část, hlava ...)
    struct_path: tuple = ()  # (kind, number) of structural ancestors


class _Parser:
    def __init__(self, doc: Document, page_url: str | None):
        self.doc = doc
        self.page_url = page_url
        self.pending_heading: str | None = None
        # Slov-Lex sometimes closes "Čl. I" early and puts later §§ (in group headings) beside it;
        # such §§ still belong to the článok that holds the act's §§
        self.open_clanok: Provision | None = None

    def _url(self, html_id: str) -> str | None:
        if not self.page_url or not html_id:
            return self.page_url
        return f"{self.page_url}#{urllib.parse.quote(html_id, safe='.-_~')}"

    def _add(self, pid: str, fallback: list[str], **kw) -> Provision:
        if self.doc.get(pid) is not None:
            for alt in fallback:
                if self.doc.get(alt) is None:
                    pid = alt
                    break
            else:
                base, i = pid, 2
                while self.doc.get(f"{base}_{i}") is not None:
                    i += 1
                pid = f"{base}_{i}"
        p = Provision(id=pid, source=SOURCE, act_id=self.doc.act_id, language=LANGUAGE, **kw)
        return self.doc.add(p)

    @staticmethod
    def _append(p: Provision | None, text: str) -> None:
        if p is not None and text:
            p.text = f"{p.text}\n{text}" if p.text else text

    def walk_children(self, node: Node, ctx: _Ctx) -> None:
        for c in node.children:
            if not isinstance(c, Node):
                continue
            kind = _unit_kind(c)
            if kind:
                self.unit(c, kind, ctx)
                continue
            cls = c.classes
            if "citat" in cls:  # text quoted by an amending provision: text of its owner
                self._append(ctx.parent, node_text(c, block=True))
            elif cls and cls[0] == "text":
                self._append(ctx.parent, node_text(c))
            elif cls and (cls[0].endswith("Oznacenie") or cls[0].endswith("Nadpis")):
                continue
            elif c.id == "predpis.text2":  # signatures
                continue
            elif c.tag == "div":
                self.walk_children(c, ctx)

    def unit(self, node: Node, kind: str, ctx: _Ctx) -> None:
        html_kind = kind
        if kind in ALIASES:  # the Constitution's "ustavnyclanok" is a článok like any other
            kind = ALIASES[kind]
        label = " ".join(node_text(_part(node, "Oznacenie")).split())
        heading = " ".join(node_text(_part(node, "Nadpis")).split()) or None

        if kind in GROUPS:
            # the heading applies to every § in the group without a heading of its own;
            # an enclosing group's heading applies again after a nested group ends
            outer = self.pending_heading
            self.pending_heading = label or heading
            self.walk_children(node, ctx)
            self.pending_heading = outer
            return
        if kind not in KINDS:  # oznacenaPolozka and unknown units: text of the owner
            self._append(ctx.parent, node_text(node, block=True))
            return
        pkind = KINDS[kind]

        if kind == "poznamka" and ctx.parent is not None:
            # a footnote quoted by an amending point without a div.citat wrapper
            # ("Poznámka pod čiarou k odkazu 27g znie: „27g) ...“"): text of the point
            self._append(ctx.parent, node_text(node, block=True))
            return

        if kind in SUBUNITS and not label:  # unnumbered ods. / bod: fold into the parent
            self.walk_children(node, ctx)
            return

        if kind in STRUCTURAL:
            num = _structural_number(node, kind, label)
            path = ctx.struct_path + ((pkind, num),)
            pid = make_id(SOURCE, self.doc.act_id, list(path))
            pref = [ctx.parent.id + "." + make_segment(pkind, num)] if ctx.parent else []
            self.pending_heading = None
            p = self._add(pid, pref, kind=pkind, label=label, number=normalize_number(num), heading=heading,
                          parent_id=ctx.parent.id if ctx.parent else None, url=self._url(node.id))
            self.walk_children(node, _Ctx(parent=p, struct=p, struct_path=path))
            return

        if kind == "priloha":
            m = _ANNEX_NUM.search(label)
            raw = _id_number(node.id, kind) or ""
            num = m.group(1) if m else (raw if re.fullmatch(r"\d+[a-z]?", raw) else "0")
            body = [x for x in node.children if not (isinstance(x, Node) and x.classes and (
                x.classes[0].endswith("Oznacenie") or x.classes[0].endswith("Nadpis")))]
            text = node_text(Node("div", {}, body), block=True)
            pdfs = [a.attrs["href"] for a in node.iter() if a.tag == "a" and "predpis-pdf-priloha" in a.classes
                    and a.attrs.get("href")]
            if pdfs and self.page_url:  # annex published only as PDF: keep the link
                text = "\n".join([text] + [f"PDF: {urllib.parse.urljoin(self.page_url, h)}" for h in pdfs]).strip()
            pid = make_id(SOURCE, self.doc.act_id, [(pkind, num)])
            self._add(pid, [], kind=pkind, label=label or "Príloha", number=normalize_number(num), text=text,
                      heading=heading, parent_id=None, url=self._url(node.id))
            return

        if kind in ("paragraf", "clanok", "poznamka"):
            num = _id_number(node.id, html_kind)
            if not num or not re.fullmatch(r"\w+", num):
                num = label
            try:
                number = normalize_number(num)
            except ValueError:
                number = "0"
            parent = ctx.struct if kind == "clanok" else ctx.parent
            if kind == "paragraf" and parent is None:
                parent = self.open_clanok
            if kind == "clanok":
                self.open_clanok = None
            pid = make_id(SOURCE, self.doc.act_id, [(pkind, number)])
            alt = (make_id(SOURCE, self.doc.act_id, list(ctx.struct_path) + [(pkind, number)])
                   if ctx.struct_path else None)
            if kind == "paragraf" and heading is None:
                heading = self.pending_heading
            p = self._add(pid, [a for a in (alt,) if a], kind=pkind, label=label or num, number=number,
                          heading=heading, parent_id=parent.id if parent else None, url=self._url(node.id))
            start = len(self.doc)
            self.walk_children(node, _Ctx(parent=p, struct=ctx.struct, struct_path=ctx.struct_path))
            if kind == "clanok" and any(c.kind == "par" and c.parent_id == p.id for c in self.doc.provisions[start:]):
                self.open_clanok = p
            return

        # odsek / pismeno / bod with a printed number
        try:
            number = normalize_number(label)
        except ValueError:
            number = normalize_number(_id_number(node.id, kind) or "0")
        if ctx.parent is not None:
            pid = f"{ctx.parent.id}.{make_segment(pkind, number)}"
        else:
            pid = make_id(SOURCE, self.doc.act_id, [(pkind, number)])
        p = self._add(pid, [], kind=pkind, label=label, number=number, heading=heading,
                      parent_id=ctx.parent.id if ctx.parent else None, url=self._url(node.id))
        self.walk_children(node, _Ctx(parent=p, struct=ctx.struct, struct_path=ctx.struct_path))


def _find(root: Node, pred) -> Node | None:
    for n in root.iter():
        if pred(n):
            return n
    return None


def parse_act(html: str, act_id: str | None = None, iri: str | None = None) -> Document:
    """Turn a Slov-Lex version page (static HTML) into a :class:`Document`.

    ``act_id`` (``"69/2018"``) is read from the page heading when not given; ``iri``
    (``/SK/ZZ/2018/69/20260430``) gives the document and provision URLs.
    """
    root = parse_html(html)
    h1 = _find(root, lambda n: n.tag == "h1")
    citation = node_text(h1) if h1 else ""
    if act_id is None:
        m = re.match(r"(\d+[a-z]?/\d{4})", citation)
        if not m:
            raise ValueError("act_id not given and not found in the page heading")
        act_id = m.group(1)
    if not citation:
        citation = f"{act_id} {'Zb.' if int(act_id.split('/')[1]) < 1993 else 'Z. z.'}"
    header = _find(root, lambda n: "ucinnost_header" in n.classes)
    date = header.attrs.get("data-from") or None if header else None
    predpis = _find(root, lambda n: n.id == "predpis")
    if predpis is None:
        raise LookupError(f"no text (div#predpis) in the Slov-Lex page for {act_id}")
    typ = node_text(_find(predpis, lambda n: n.id == "predpis.typ"))
    nadpis = " ".join(node_text(_find(predpis, lambda n: n.id == "predpis.nadpis")).split())
    title = f"{_title_case_type(typ)} č. {citation} {nadpis}".strip() if (typ or nadpis) else None
    if iri:
        portal = f"{PORTAL_URL}/ezbierky/pravne-predpisy{iri}"
        eli = f"{PORTAL_URL}/pravne-predpisy{iri}" if not iri.endswith(PROMULGATED) else None
        page = f"{STATIC_URL}/static{iri}.html"
    else:
        portal = eli = page = None
    doc = Document(source=SOURCE, act_id=act_id, language=LANGUAGE, title=title, date=date, eli=eli, url=portal)
    parser = _Parser(doc, page)
    walked: set[int] = set()
    for region in root.iter():
        if region.tag == "div" and region.id in ("predpis", "prilohy", "poznamky") and id(region) not in walked:
            walked.update(id(n) for n in region.iter())
            parser.walk_children(region, _Ctx())
    return doc
