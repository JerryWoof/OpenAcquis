"""Change alerts: cheap per-source change signals for watched directives and national acts.

Each watched item gets a small *fingerprint* of what its source says about it; a run compares the
fingerprints with the previous state file (``data/alerts/state.json``) and reports what changed.
Signals, one or two small requests per item (see docs/alerts.md):

=========  ==============================================================  ===========================
source     signal                                                          item key
=========  ==============================================================  ===========================
cellar     amendments, corrigenda, consolidated versions of the directive  ``eu:32022L2555``
cellar     notified national measures (id + notification date), per state  ``nim:32022L2555:DEU``
cz         e-Sbírka ``/historie`` (versions, future ones included) and      ``cz:264/2025``
           ``/souvislosti`` (amended / implemented / repealed by)
sk         Slov-Lex ``HEAD`` ``content-md5``; on change, the version table  ``sk:69/2018``
at         RIS OGD: every norm version (ID, Geändert, in/out of force)     ``at:20013065``
de         gesetze-im-internet.de ``xml.zip`` (conditional GET, ETag):      ``de:bsig_2025``
           ``builddate`` and the ``Stand`` notes
=========  ==============================================================  ===========================

Rules: stdlib only; fetched content is parsed as data (JSON, regular expressions on HTML/XML) and never
executed; a source that fails keeps its previous fingerprint and is reported as a warning, never as a change;
an item seen for the first time is a silent baseline. Alerts describe changes in the sources: they are
signals for a reviewer, not findings about any country's transposition.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from .cellar import ENDPOINT as SPARQL_ENDPOINT
from .cellar import USER_AGENT
from .countries import MEMBER_STATES

SCHEMA = 1
#: Change events kept in the state file (newest first); the Atom feed is rendered from them.
MAX_EVENTS = 200
#: Consecutive failed runs of one source before the workflow opens the single health issue.
HEALTH_AFTER = 3
# A record that vanished from CELLAR (one state's measures, one directive's amendments/corrigenda/consolidations)
# keeps its previous fingerprint for this many runs, then the removal is reported as a change.
VANISH_AFTER = 3
#: Directives per SPARQL ``VALUES`` block.
SPARQL_CHUNK = 40
#: Largest XML file read from a gesetze-im-internet.de zip (bytes, uncompressed).
MAX_XML = 30_000_000
SITE_URL = "https://jerrywoof.github.io/OpenAcquis/"
FEED_ID = "tag:jerrywoof.github.io,2026:openacquis/alerts"
SOURCES = ("cellar", "cz", "sk", "at", "de")
#: Fields that only steer the next request (not content): ignored by the fingerprint.
TRANSPORT = ("md5", "etag", "last_modified")

ESBIRKA_API = "https://e-sbirka.gov.cz/sbr-cache/dokumenty-sbirky/"
SLOVLEX_STATIC = "https://static.slov-lex.sk/static/SK/ZZ/{year}/{num}/"
RIS_SEARCH = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
GII_ZIP = "https://www.gesetze-im-internet.de/{slug}/xml.zip"

_CELEX = re.compile(r"^3\d{4}L\d{4}$")
_NUM_YEAR = re.compile(r"^\s*(\d+[a-z]?)\s*/\s*(\d{4})\b")
_GESNR = re.compile(r"^\d{8}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
_KEY_SAFE = re.compile(r"[^A-Za-z0-9:/_.\-]")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")
_BGBL_AT = re.compile(r"BGBl\.\s*(?:I{1,3}\s*)?Nr\.\s*\d+/\d{4}")


# --- HTTP -------------------------------------------------------------------------


@dataclass
class Response:
    status: int
    headers: dict[str, str]  # lower-case names
    body: bytes = b""

    def json(self) -> Any:
        return json.loads(self.body)

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class SourceError(Exception):
    """A source did not answer usefully (HTTP error, outage, unexpected shape)."""


#: ``fetch(method, url, headers, data) -> Response``; HTTP errors are returned as responses, network
#: failures raise ``OSError``. Tests pass a fake.
Fetcher = Callable[[str, str, dict, "bytes | None"], Response]


def _certifi_context() -> ssl.SSLContext | None:
    try:
        import certifi  # type: ignore[import-not-found]
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def urllib_fetch(method: str, url: str, headers: dict, data: bytes | None = None, timeout: float = 90.0) -> Response:
    """The real fetcher: urllib, verified TLS (certifi as fallback for RIS's root), no redirects to scripts."""
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
        except urllib.error.HTTPError as e:
            return Response(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b"")
        except urllib.error.URLError as e:
            if attempt == 0 and isinstance(e.reason, ssl.SSLCertVerificationError):
                context = _certifi_context()  # verification stays on, only the CA bundle changes
                if context is not None:
                    continue
            raise
    raise AssertionError("unreachable")


def default_cache_dir() -> Path:
    """``$OPENACQUIS_CACHE/alerts``, else ``~/.cache/openacquis/alerts``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    base = Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"
    return base / "alerts"


class Http:
    """Polite HTTP: fixed User-Agent, ``delay`` seconds between requests to one host, retries with backoff
    on 429 / 5xx / network errors, and an optional short-lived cache of plain GET/POST answers (for local
    reruns; conditional and HEAD requests are never cached). ``cache_ttl=0`` disables the cache."""

    def __init__(
        self,
        fetch: Fetcher | None = None,
        *,
        delay: float = 1.0,
        retries: int = 2,
        backoff: float = 5.0,
        cache_dir: Path | None = None,
        cache_ttl: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
        user_agent: str = USER_AGENT,
    ):
        self._fetch = fetch or urllib_fetch
        self.delay = delay
        self.retries = retries
        self.backoff = backoff
        self.cache_dir = cache_dir if cache_ttl > 0 else None
        self.cache_ttl = cache_ttl
        self.sleep = sleep
        self.user_agent = user_agent
        self._last: dict[str, float] = {}
        self.requests = 0  # network requests actually made

    def _cache_path(self, method: str, url: str, data: bytes | None) -> Path | None:
        if self.cache_dir is None:
            return None
        h = hashlib.sha256(f"{method} {url}\n".encode() + (data or b"")).hexdigest()[:32]
        return self.cache_dir / f"{h}.bin"

    def request(
        self, url: str, *, method: str = "GET", headers: dict | None = None, data: bytes | None = None,
        cacheable: bool = False,
    ) -> Response:
        """``Response`` for 2xx and 304; ``SourceError`` for anything else after the retries."""
        path = self._cache_path(method, url, data) if cacheable else None
        if path is not None and path.exists() and time.time() - path.stat().st_mtime < self.cache_ttl:
            return Response(200, {}, path.read_bytes())
        h = {"User-Agent": self.user_agent, **(headers or {})}
        host = urllib.parse.urlsplit(url).netloc
        problem = ""
        for attempt in range(self.retries + 1):
            wait = self._last.get(host, -1e9) + self.delay - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            try:
                resp = self._fetch(method, url, h, data)
            except (OSError, ValueError) as e:  # URLError, timeouts, connection resets
                resp, problem = None, f"{type(e).__name__}: {e}"
            finally:
                self._last[host] = time.monotonic()
                self.requests += 1
            if resp is not None:
                if 200 <= resp.status < 300 or resp.status == 304:
                    if path is not None and resp.status == 200:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        tmp = path.with_suffix(".tmp")
                        tmp.write_bytes(resp.body)
                        tmp.replace(path)
                    return resp
                problem = f"HTTP {resp.status}"
                if resp.status not in (408, 425, 429) and resp.status < 500:
                    break  # 4xx: retrying will not help
            if attempt < self.retries:
                self.sleep(self.backoff * 2**attempt)
        raise SourceError(f"{problem} for {_short_url(url)}")


def _short_url(url: str) -> str:
    s = urllib.parse.urlsplit(url)
    return f"{s.netloc}{s.path}"[:120]


# --- watch list -------------------------------------------------------------------


@dataclass(frozen=True)
class WatchAct:
    country: str  # adapter code: cz, sk, at, de
    ref: str  # as the adapter takes it: 264/2025, 69/2018, 20013065, bsig_2025
    label: str = ""
    celex: str = ""  # the directive it transposes (for the alert text), if known


@dataclass
class WatchList:
    directives: dict[str, str] = field(default_factory=dict)  # CELEX -> short name
    acts: list[WatchAct] = field(default_factory=list)
    origin: str = ""


#: Fallback when ``data/directives.json`` does not exist: NIS2 and its main act in each adapter country.
NIS2_WATCH = WatchList(
    directives={"32022L2555": "NIS2"},
    acts=[
        WatchAct("cz", "264/2025", "CZ 264/2025 Sb. (Cybersecurity Act)", "32022L2555"),
        WatchAct("sk", "69/2018", "SK 69/2018 Z. z. (Cybersecurity Act)", "32022L2555"),
        WatchAct("at", "20013065", "AT NISG 2026", "32022L2555"),
        WatchAct("de", "bsig_2025", "DE BSIG", "32022L2555"),
    ],
    origin="built-in NIS2 list",
)

_COUNTRY = {"cz": "cz", "cze": "cz", "sk": "sk", "svk": "sk", "at": "at", "aut": "at", "de": "de", "deu": "de"}
_ACT_KEYS = ("act_id", "ref", "act", "reference", "id", "slug", "gesetzesnummer")


def _country(code: Any) -> str | None:
    return _COUNTRY.get(str(code or "").strip().lower())


def _act_ref(v: Any) -> tuple[str, str]:
    """(ref, label) from ``"264/2025"`` or ``{"act_id": ..., "label": ...}``."""
    if isinstance(v, dict):
        for k in _ACT_KEYS:
            if v.get(k):
                return str(v[k]).strip(), str(v.get("label") or v.get("act_label") or v.get("title") or "").strip()
        return "", ""
    return str(v or "").strip(), ""


def parse_watchlist(data: Any, origin: str = "") -> WatchList:
    """Read a directives file leniently. Accepted shapes::

        ["32022L2555", ...]
        [{"celex": "32022L2555", "short": "NIS2",
          "acts": {"cz": "264/2025", "sk": ["69/2018"], "at": {"act_id": "20013065", "label": "NISG 2026"}}}]
        [{"celex": "32022L2555", "short": "NIS2",       # the data/directives.json registry
          "countries": {"cz": {"status": "ok", "acts": ["264/2025"], "label": "264/2025 Sb."}}}]
        {"directives": [...]}          # same entries

    National acts may also be a list (``"national"``, ``"main_acts"``, ``"acts"``) of
    ``{"country": "cz"|"CZE", "act_id"|"ref"|"act": ..., "label": ...}``. Countries without an adapter
    are ignored (CELLAR still watches their notified measures).
    """
    entries = data.get("directives", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("directives file: expected a list or {\"directives\": [...]}")
    w = WatchList(origin=origin)
    acts: dict[tuple[str, str], tuple[str, list[str]]] = {}  # (country, ref) -> (label, CELEX numbers)
    for e in entries:
        if isinstance(e, str):
            celex = e.strip().upper()
        elif isinstance(e, dict):
            celex = str(e.get("celex") or "").strip().upper()
        else:
            continue
        if not _CELEX.match(celex):
            continue
        short = str(e.get("short") or e.get("name") or e.get("title") or celex) if isinstance(e, dict) else celex
        w.directives.setdefault(celex, short.strip() or celex)
        if not isinstance(e, dict):
            continue
        for key in ("acts", "main_acts", "national", "countries"):
            block = e.get(key)
            pairs: list[tuple[Any, Any]] = []
            if isinstance(block, dict):
                for cc, v in block.items():
                    if isinstance(v, dict) and isinstance(v.get("acts"), list):
                        # registry shape: {"cz": {"acts": ["264/2025", ...], "label": "264/2025 Sb."}}
                        first = str(v.get("label") or "").strip()
                        v = [{"ref": a, "label": f"{str(cc).upper()} {first}" if i == 0 and first else ""}
                             for i, a in enumerate(v["acts"]) if isinstance(a, str)]
                    pairs += [(cc, x) for x in (v if isinstance(v, list) else [v])]
            elif isinstance(block, list):
                for x in block:
                    if isinstance(x, dict):
                        pairs.append((x.get("country") or x.get("country_code") or x.get("cc"), x))
            for cc, v in pairs:
                code = _country(cc)
                ref, label = _act_ref(v)
                if not code or not ref:
                    continue
                prev_label, celexes = acts.setdefault((code, ref), (label, []))
                if not prev_label and label:
                    acts[(code, ref)] = (label, celexes)
                if celex not in celexes:
                    celexes.append(celex)
    for (code, ref), (label, celexes) in acts.items():
        shorts = ", ".join(w.directives.get(c, c) for c in celexes)
        base = _act_label(WatchAct(code, ref, label))
        w.acts.append(WatchAct(code, ref, f"{base} ({shorts})" if shorts else base, celexes[0] if celexes else ""))
    return w


def load_watchlist(path: Path | None) -> WatchList:
    """``path`` (``data/directives.json``) if it exists and names at least one directive, else :data:`NIS2_WATCH`."""
    if path is not None and path.exists():
        w = parse_watchlist(json.loads(path.read_text(encoding="utf-8")), origin=str(path))
        if w.directives:
            return w
    return WatchList(dict(NIS2_WATCH.directives), list(NIS2_WATCH.acts), NIS2_WATCH.origin)


def alignment_index(align_dir: Path | None) -> dict[str, list[dict]]:
    """Item key (``cz:264/2025``) -> alignment pages built on that act: ``[{"id", "date", "url"}]``."""
    out: dict[str, list[dict]] = {}
    if align_dir is None or not align_dir.is_dir():
        return out
    for p in sorted(align_dir.glob("*.json")):
        if p.name.endswith((".review.json", ".headings.json")):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cc = _country(d.get("country_code"))
        for n in d.get("national") or []:
            if cc and n.get("act_id"):
                key = act_key(WatchAct(cc, str(n["act_id"])))
                if key:
                    out.setdefault(key, []).append(
                        {"id": str(d.get("id") or p.stem), "date": str(n.get("date") or ""),
                         "url": f"{SITE_URL}alignment.html?id={urllib.parse.quote(str(d.get('id') or p.stem))}"})
    return out


# --- items and fingerprints -------------------------------------------------------


def fingerprint(data: dict | None) -> str:
    if data is None:
        return ""
    core = {k: v for k, v in data.items() if k not in TRANSPORT}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


@dataclass
class Item:
    key: str
    source: str
    label: str
    url: str
    data: dict

    def to_state(self) -> dict:
        return {"source": self.source, "label": self.label, "url": self.url,
                "fingerprint": fingerprint(self.data), "data": self.data}


def _num_year(ref: str) -> tuple[str, str] | None:
    m = _NUM_YEAR.match(ref)
    return (m.group(1), m.group(2)) if m else None


def act_key(act: WatchAct) -> str | None:
    """Stable item key of a national act, or ``None`` if the reference is not usable without a lookup."""
    if act.country in ("cz", "sk"):
        ny = _num_year(act.ref)
        return f"{act.country}:{ny[0]}/{ny[1]}" if ny else None
    if act.country == "at":
        return f"at:{act.ref}" if _GESNR.match(act.ref) else None
    if act.country == "de":
        return f"de:{act.ref}" if _SLUG.match(act.ref) else None
    return None


def _act_label(act: WatchAct) -> str:
    if act.label:
        return act.label
    suffix = {"cz": " Sb.", "sk": " Z. z."}.get(act.country, "")
    return f"{act.country.upper()} {act.ref}{suffix}"


# --- CELLAR -----------------------------------------------------------------------

_PREFIX = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
           "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> ")


def _values(celexes: Iterable[str]) -> str:
    return "VALUES ?celex { " + " ".join(f'"{c}"^^xsd:string' for c in celexes) + " }"


def eu_query(celexes: list[str]) -> str:
    return _PREFIX + f"""SELECT ?celex ?rel ?xc WHERE {{ {_values(celexes)}
 ?w cdm:resource_legal_id_celex ?celex .
 {{ ?x cdm:resource_legal_amends_resource_legal ?w . BIND('amended_by' AS ?rel) }} UNION
 {{ ?x cdm:resource_legal_corrects_resource_legal ?w . BIND('corrected_by' AS ?rel) }} UNION
 {{ ?x cdm:act_consolidated_based_on_resource_legal ?w . BIND('consolidated' AS ?rel) }}
 ?x cdm:resource_legal_id_celex ?xc }}"""


def nim_query(celexes: list[str]) -> str:
    # No GROUP BY / SAMPLE / MAX: aggregates over this data are not deterministic between runs.
    return _PREFIX + f"""SELECT ?celex ?mc ?notif WHERE {{ {_values(celexes)}
 ?w cdm:resource_legal_id_celex ?celex .
 ?m cdm:measure_national_implementing_implements_resource_legal ?w ; cdm:resource_legal_id_celex ?mc .
 FILTER(STRSTARTS(?mc, CONCAT('7', SUBSTR(?celex, 2))))
 OPTIONAL {{ ?m cdm:measure_national_implementing_date_notification ?notif }} }}"""


def _sparql(http: Http, query: str) -> list[dict[str, str]]:
    resp = http.request(
        SPARQL_ENDPOINT, method="POST", data=urllib.parse.urlencode({"query": query}).encode(),
        headers={"Accept": "application/sparql-results+json", "Content-Type": "application/x-www-form-urlencoded"},
        cacheable=True,
    )
    try:
        bindings = resp.json()["results"]["bindings"]
        return [{k: str(v.get("value", "")) for k, v in b.items()} for b in bindings]
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise SourceError(f"CELLAR: unexpected answer ({type(e).__name__})") from e


def check_cellar(http: Http, directives: dict[str, str]) -> dict[str, Item]:
    celexes = sorted(c for c in directives if _CELEX.match(c))
    eu_rows: list[dict] = []
    nim_rows: list[dict] = []
    for i in range(0, len(celexes), SPARQL_CHUNK):
        chunk = celexes[i : i + SPARQL_CHUNK]
        eu_rows += _sparql(http, eu_query(chunk))
        nim_rows += _sparql(http, nim_query(chunk))
    if celexes and not eu_rows and not nim_rows:
        raise SourceError("CELLAR returned no rows at all for the watched directives")
    items: dict[str, Item] = {}
    for c in celexes:
        short = directives.get(c) or c
        related = sorted({(r.get("rel", ""), r.get("xc", "")) for r in eu_rows if r.get("celex") == c})
        items[f"eu:{c}"] = Item(f"eu:{c}", "cellar", f"{short} ({c})",
                                f"https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX:{c}",
                                {"related": [list(x) for x in related]})
        per: dict[str, set[tuple[str, str]]] = {}
        for r in nim_rows:
            mc = r.get("mc", "")
            if r.get("celex") == c and len(mc) >= 13 and mc[10:13] in MEMBER_STATES:
                per.setdefault(mc[10:13], set()).add((mc, r.get("notif", "")[:10]))
        for cc, ms in sorted(per.items()):
            key = f"nim:{c}:{cc}"
            items[key] = Item(key, "cellar", f"{short} × {MEMBER_STATES[cc]}",
                              f"https://eur-lex.europa.eu/legal-content/EN/NIM/?uri=CELEX:{c}",
                              {"measures": [list(x) for x in sorted(ms)]})
    return items


# --- national sources ---------------------------------------------------------------


def check_cz(http: Http, act: WatchAct) -> Item:
    num, year = _num_year(act.ref) or ("", "")
    path = f"/sb/{year}/{num}"
    base = ESBIRKA_API + urllib.parse.quote(path, safe="")
    hdr = {"Accept": "application/json"}
    try:
        hist = http.request(base + "/historie", headers=hdr, cacheable=True).json()["historie"]
        souv = http.request(base + "/souvislosti", headers=hdr, cacheable=True).json().get("souvislosti") or []
        versions = [[str(v["staleUrl"]), str(v.get("typZneni") or ""),
                     sorted(str(n.get("kodDokumentuSbirky") or "") for n in v.get("novely") or [])] for v in hist]
        related = {str(x["typ"]): sorted(str(d.get("kodDokumentuSbirky") or "") for d in x.get("dokumentySbirky") or [])
                   for x in souv if x.get("typ") in ("JE_MENEN", "JE_PROVADEN", "JE_ZRUSEN")}
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise SourceError(f"e-Sbírka {path}: unexpected answer ({type(e).__name__})") from e
    if not versions:
        raise SourceError(f"e-Sbírka {path}: empty version history")
    return Item(f"cz:{num}/{year}", "cz", _act_label(act), f"https://e-sbirka.gov.cz{path}",
                {"versions": versions, "related": related})


_SK_ROW = re.compile(
    r'<tr class="effectivenessHistoryItem"\s+data-iri="([^"]+)"[^>]*?data-ucinnostod="([^"]*)"\s+'
    r'data-ucinnostdo="([^"]*)"[^>]*>(.*?)</tr>', re.S)
_SK_NOVELA = re.compile(r">\s*(\d+[a-z]?/\d{4})(?:&nbsp;|\s)*Z\.")


def parse_slovlex_history(page: str) -> list[list]:
    """``[[iri, from, to, [amending acts]]]`` from a Slov-Lex act index page (regular expressions only)."""
    return [[html.unescape(i), f, t, _SK_NOVELA.findall(body)] for i, f, t, body in _SK_ROW.findall(page)]


def check_sk(http: Http, act: WatchAct, prev: dict | None) -> Item:
    num, year = _num_year(act.ref) or ("", "")
    url = SLOVLEX_STATIC.format(year=year, num=num)
    key = f"sk:{num}/{year}"
    web = f"https://www.slov-lex.sk/ezbierky/pravne-predpisy/SK/ZZ/{year}/{num}/"
    md5 = http.request(url, method="HEAD").headers.get("content-md5")
    if md5 and prev and prev.get("md5") == md5 and prev.get("versions"):
        return Item(key, "sk", _act_label(act), web, prev)
    versions = parse_slovlex_history(http.request(url).text())
    if not versions:
        raise SourceError(f"Slov-Lex {year}/{num}: no version table on the page")
    return Item(key, "sk", _act_label(act), web, {"md5": md5 or "", "versions": versions})


def check_at(http: Http, act: WatchAct) -> Item:
    docs: list[dict] = []
    page = 1
    try:
        while True:
            q = urllib.parse.urlencode({"Applikation": "BrKons", "Gesetzesnummer": act.ref,
                                        "DokumenteProSeite": "OneHundred", "Seitennummer": page})
            r = http.request(f"{RIS_SEARCH}?{q}", headers={"Accept": "application/json"},
                             cacheable=True).json()["OgdSearchResult"]["OgdDocumentResults"]
            d = r.get("OgdDocumentReference") or []
            d = d if isinstance(d, list) else [d]
            docs += d
            hits = int(r["Hits"]["#text"])
            if len(docs) >= hits or not d or page >= 50:
                break
            page += 1
        rows, amendments = [], set()
        for x in docs:
            meta = x["Data"]["Metadaten"]
            br = meta["Bundesrecht"]["BrKons"]
            rows.append([str(meta["Technisch"]["ID"]), str(br.get("ArtikelParagraphAnlage") or ""),
                         str(meta["Allgemein"].get("Geaendert") or ""), str(br.get("Inkrafttretensdatum") or ""),
                         str(br.get("Ausserkrafttretensdatum") or "")])
            if str(br.get("Paragraphnummer")) == "0":
                amendments.update(re.sub(r"\s+", " ", m) for m in _BGBL_AT.findall(str(br.get("Aenderung") or "")))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise SourceError(f"RIS {act.ref}: unexpected answer ({type(e).__name__})") from e
    if not rows:
        raise SourceError(f"RIS {act.ref}: no norms returned")
    return Item(f"at:{act.ref}", "at", _act_label(act),
                f"https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer={act.ref}",
                {"norms": sorted(rows), "amendments": sorted(amendments)})


_BUILDDATE = re.compile(r'<dokumente\b[^>]*\bbuilddate="(\d{8,14})"')
_STAND = re.compile(r"<standkommentar>(.*?)</standkommentar>", re.S)
_TAG = re.compile(r"<[^>]+>")


def parse_gii_header(xml: str) -> dict:
    """``builddate`` and the ``Stand`` notes of a gesetze-im-internet.de XML (regular expressions only)."""
    m = _BUILDDATE.search(xml)
    notes = []
    for raw in _STAND.findall(xml):
        t = re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", raw))).strip()
        if t and t not in notes:
            notes.append(t)
    return {"builddate": m.group(1) if m else "", "stand": notes}


def check_de(http: Http, act: WatchAct, prev: dict | None) -> Item:
    url = GII_ZIP.format(slug=act.ref)
    key = f"de:{act.ref}"
    web = f"https://www.gesetze-im-internet.de/{act.ref}/"
    hdr = {}
    if prev and prev.get("builddate"):
        if prev.get("etag"):
            hdr["If-None-Match"] = prev["etag"]
        if prev.get("last_modified"):
            hdr["If-Modified-Since"] = prev["last_modified"]
    resp = http.request(url, headers=hdr)
    if resp.status == 304 and prev:
        return Item(key, "de", _act_label(act), web, prev)
    try:
        z = zipfile.ZipFile(io.BytesIO(resp.body))
        infos = [i for i in z.infolist() if i.filename.lower().endswith(".xml") and i.file_size <= MAX_XML]
        if not infos:
            raise SourceError(f"gesetze-im-internet.de {act.ref}: no XML in the zip")
        xml = z.read(infos[0]).decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError) as e:
        raise SourceError(f"gesetze-im-internet.de {act.ref}: not a zip ({type(e).__name__})") from e
    head = parse_gii_header(xml)
    if not head["builddate"]:
        raise SourceError(f"gesetze-im-internet.de {act.ref}: no builddate")
    return Item(key, "de", _act_label(act), web,
                {"etag": resp.headers.get("etag", ""), "last_modified": resp.headers.get("last-modified", ""),
                 **head})


# --- describing changes -------------------------------------------------------------


def _date_of(stale_url: str) -> str:
    return stale_url.rstrip("/").rsplit("/", 1)[-1]


def _fmt_list(xs: list[str], n: int = 8) -> str:
    xs = [x for x in xs if x]
    more = f" and {len(xs) - n} more" if len(xs) > n else ""
    return ", ".join(xs[:n]) + more


_EU_KIND = {"amended_by": ("eu_amended", "amended by {x}"),
            "corrected_by": ("eu_corrigendum", "corrigendum {x}"),
            "consolidated": ("eu_consolidated", "new consolidated version {x}")}


def _describe_eu(old: dict, new: dict) -> list[tuple[str, str]]:
    o = {tuple(x) for x in old.get("related", [])}
    n = {tuple(x) for x in new.get("related", [])}
    out = []
    for rel, xc in sorted(n - o):
        kind, text = _EU_KIND.get(rel, ("eu_related", rel + " {x}"))
        out.append((kind, text.format(x=xc)))
    for rel, xc in sorted(o - n):
        out.append(("eu_related_removed", f"{xc} ({rel.replace('_', ' ')}) no longer listed"))
    return out


def _describe_nim(old: dict, new: dict) -> list[tuple[str, str]]:
    def by_id(d: dict) -> dict[str, set[str]]:
        r: dict[str, set[str]] = {}
        for mc, notif in d.get("measures", []):
            r.setdefault(mc, set()).add(notif)
        return r

    o, n = by_id(old), by_id(new)
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    redated = sorted(m for m in set(n) & set(o) if n[m] != o[m])
    out = []
    if added:
        dates = sorted({d for m in added for d in n[m] if d})
        when = f" (notified {_fmt_list(dates, 4)})" if dates else ""
        out.append(("new_measure", f"{len(added)} new national measure(s) notified{when}: {_fmt_list(added)}"))
    if removed:
        out.append(("measure_removed", f"{len(removed)} measure(s) no longer listed: {_fmt_list(removed)}"))
    if redated:
        out.append(("measure_updated", f"{len(redated)} measure(s) with a changed notification date: {_fmt_list(redated)}"))
    return out


def _describe_cz(old: dict, new: dict) -> list[tuple[str, str]]:
    o = {v[0]: v for v in old.get("versions", [])}
    out = []
    for url, typ, novely in new.get("versions", []):
        day = _date_of(url)
        by = f" (amended by {_fmt_list(novely)})" if novely else ""
        if url not in o:
            if typ == "BUDOUCI":
                out.append(("new_version", f"future version from {day} published{by}"))
            elif typ == "AKTUALNI":
                out.append(("new_version", f"new version in force from {day}{by}"))
            else:
                out.append(("new_version", f"new version {day} ({typ.lower() or 'unknown status'}){by}"))
            continue
        _, otyp, onov = o[url]
        if otyp != typ:
            if otyp == "BUDOUCI" and typ == "AKTUALNI":
                out.append(("version_in_force", f"version from {day} is now in force"))
            else:
                out.append(("version_status", f"version {day}: {otyp.lower() or '?'} → {typ.lower() or '?'}"))
        if sorted(onov) != sorted(novely):
            out.append(("amending_act", f"version {day}: amending acts now {_fmt_list(novely) or 'none'}"))
    newurls = {v[0] for v in new.get("versions", [])}
    for url in o:
        if url not in newurls:
            out.append(("version_removed", f"version {_date_of(url)} no longer listed"))
    words = {"JE_MENEN": ("amending_act", "amended by"), "JE_PROVADEN": ("implementing_decree", "new implementing regulation"),
             "JE_ZRUSEN": ("repealed", "repealed by")}
    orel, nrel = old.get("related", {}), new.get("related", {})
    for typ, (kind, text) in words.items():
        added = sorted(set(nrel.get(typ, [])) - set(orel.get(typ, [])))
        if added:
            out.append((kind, f"{text} {_fmt_list(added)}"))
    return out


def _describe_sk(old: dict, new: dict) -> list[tuple[str, str]]:
    o = {v[0]: v for v in old.get("versions", [])}
    today = date.today().isoformat()
    out = []
    for iri, frm, to, novely in new.get("versions", []):
        by = f" (amended by {_fmt_list([x + ' Z. z.' for x in novely])})" if novely else ""
        if iri not in o:
            when = "future version from" if frm and frm > today else "new version in force from"
            out.append(("new_version", f"{when} {frm or _date_of(iri)}{by}"))
            continue
        _, ofrm, oto, onov = o[iri]
        if (ofrm, oto) != (frm, to):
            out.append(("version_dates", f"version {_date_of(iri)}: in force {ofrm or '?'}–{oto or 'open'} → "
                                         f"{frm or '?'}–{to or 'open'}"))
        if sorted(onov) != sorted(novely):
            out.append(("amending_act", f"version {_date_of(iri)}: amending acts now {_fmt_list(novely) or 'none'}"))
    newiris = {v[0] for v in new.get("versions", [])}
    for iri in o:
        if iri not in newiris:
            out.append(("version_removed", f"version {_date_of(iri)} no longer listed"))
    return out


def _describe_at(old: dict, new: dict) -> list[tuple[str, str]]:
    o = {r[0]: r for r in old.get("norms", [])}
    n = {r[0]: r for r in new.get("norms", [])}
    added = [n[k] for k in sorted(set(n) - set(o))]
    changed = [n[k] for k in sorted(set(n) & set(o)) if n[k] != o[k]]
    removed = [o[k] for k in sorted(set(o) - set(n))]
    out = []

    def paras(rows: list) -> str:
        return _fmt_list(sorted({r[1] for r in rows if r[1]}))

    if added:
        starts = sorted({r[3] for r in added if r[3]})
        when = f", in force from {_fmt_list(starts, 4)}" if starts else ""
        out.append(("new_version", f"{len(added)} new norm version(s) ({paras(added)}{when})"))
    if changed:
        out.append(("norm_changed", f"{len(changed)} norm version(s) changed ({paras(changed)})"))
    if removed:
        out.append(("version_removed", f"{len(removed)} norm version(s) no longer listed ({paras(removed)})"))
    amend = sorted(set(new.get("amendments", [])) - set(old.get("amendments", [])))
    if amend:
        out.append(("amending_act", f"amended by {_fmt_list(amend)}"))
    return out


def _describe_de(old: dict, new: dict) -> list[tuple[str, str]]:
    out = []
    if old.get("builddate") != new.get("builddate"):
        out.append(("new_build", f"new text build {_gii_date(new.get('builddate', ''))} "
                                 f"(was {_gii_date(old.get('builddate', ''))})"))
    for note in new.get("stand", []):
        if note not in old.get("stand", []):
            pending = "nachgewiesen" in note or "noch nicht" in note
            out.append(("pending_amendment" if pending else "amending_act", f"Stand: {note}"))
    for note in old.get("stand", []):
        if note not in new.get("stand", []):
            out.append(("stand_removed", f"no longer noted: {note}"))
    return out


def _gii_date(b: str) -> str:
    return f"{b[0:4]}-{b[4:6]}-{b[6:8]}" if len(b) >= 8 else (b or "?")


DESCRIBE = {"eu": _describe_eu, "nim": _describe_nim, "cz": _describe_cz, "sk": _describe_sk,
            "at": _describe_at, "de": _describe_de}


def describe(key: str, old: dict, new: dict) -> list[tuple[str, str]]:
    """``[(kind, plain-text line)]`` for a changed item; a generic line if nothing specific is found."""
    lines = DESCRIBE[key.split(":", 1)[0]](old or {}, new or {})
    return lines or [("changed", "the source's record changed (details in the state file diff)")]


# --- one run ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plain(text: str, limit: int = 300) -> str:
    """Untrusted text made inert for one line: no control or bidi characters, collapsed, capped."""
    t = re.sub(r"\s+", " ", _CTRL.sub("", str(text))).strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _title(item: Item, lines: list[tuple[str, str]]) -> str:
    first = lines[0][1] if lines else "changed"
    more = f" (+{len(lines) - 1} more)" if len(lines) > 1 else ""
    return _plain(f"[change-alert] {item.label}: {first}", 180) + more


def run_check(
    watch: WatchList,
    prev_state: dict | None,
    http: Http,
    *,
    sources: Iterable[str] = SOURCES,
    align: dict[str, list[dict]] | None = None,
    now: str | None = None,
    log: Callable[[str], None] = lambda msg: None,
) -> tuple[dict, dict]:
    """Check every watched item once. Returns ``(new_state, report)``.

    ``report``: ``changes`` (one per changed item: key, title, kinds, lines, url, alignments), ``warnings``,
    ``baseline`` (keys seen for the first time), ``health`` alerts, request and timing counts.
    """
    started = time.monotonic()
    now = now or _now()
    prev_state = prev_state or {}
    prev_items: dict[str, dict] = prev_state.get("items", {})
    first_run = not prev_items
    sources = [s for s in SOURCES if s in set(sources)]
    align = align or {}
    items: dict[str, dict] = {}
    warnings: list[str] = []
    failed: dict[str, list[str]] = {}
    checked: set[str] = set()

    def keep_previous(prefix: Callable[[str], bool]) -> None:
        for k, v in prev_items.items():
            if prefix(k) and k not in items:
                items[k] = v

    def fail(source: str, msg: str) -> None:
        failed.setdefault(source, []).append(msg)
        warnings.append(f"{source}: {msg}")
        log(f"  warning: {source}: {msg}")

    new_items: dict[str, Item] = {}
    if "cellar" in sources and watch.directives:
        checked.add("cellar")
        log(f"cellar: {len(watch.directives)} directive(s)")
        try:
            got = check_cellar(http, watch.directives)
            watched = lambda k: k.split(":")[1] in watch.directives  # noqa: E731
            # One side of the answer (amendments/corrigenda/consolidations, or notified measures) empty for every
            # watched directive while the state had rows there: a partial outage, not a change.
            for side, field in (("eu", "related"), ("nim", "measures")):
                had = any(k.startswith(side + ":") and watched(k) and (v.get("data") or {}).get(field)
                          for k, v in prev_items.items())
                now_any = any(k.startswith(side + ":") and it.data.get(field) for k, it in got.items())
                if had and not now_any:
                    fail("cellar", f"no {field} returned for any watched directive; kept the previous fingerprints")
                    got = {k: it for k, it in got.items() if not k.startswith(side + ":")}
                    keep_previous(lambda k, side=side: k.startswith(side + ":") and watched(k))
            # One record that vanished (a state's measures, a directive's related acts) is more likely a glitch than
            # a withdrawal: warn and keep the previous fingerprint for VANISH_AFTER runs, then report the removal.
            for k, v in prev_items.items():
                if k in items or not watched(k) or not (k.startswith("nim:") or k.startswith("eu:")):
                    continue
                field = "measures" if k.startswith("nim:") else "related"
                had = len((v.get("data") or {}).get(field) or [])
                if not had or (k in got and got[k].data.get(field)):
                    continue
                runs = int(v.get("vanished", 0)) + 1
                if runs < VANISH_AFTER:
                    msg = (f"{k}: no {field} returned (had {had}); kept the previous fingerprint "
                           f"({runs}/{VANISH_AFTER} runs)")
                    warnings.append(f"cellar: {msg}")
                    log(f"  warning: cellar: {msg}")
                    items[k] = dict(v, vanished=runs)
                    got.pop(k, None)
                elif k not in got:  # accepted: report the removal of every measure of that state
                    got[k] = Item(k, "cellar", str(v.get("label") or k), str(v.get("url") or ""), {"measures": []})
            for k, it in got.items():
                new_items[k] = it
        except SourceError as e:
            fail("cellar", str(e))
            keep_previous(lambda k: (k.startswith("eu:") or k.startswith("nim:")) and k.split(":")[1] in watch.directives)

    # Sources not selected in this run (--sources subset) keep their items untouched.
    keep_previous(lambda k: str((prev_items[k] or {}).get("source") or k.split(":", 1)[0]) not in sources)

    checkers = {"cz": check_cz, "sk": check_sk, "at": check_at, "de": check_de}
    for act in watch.acts:
        if act.country not in sources:
            continue
        key = act_key(act)
        if key is None:
            warnings.append(f"{act.country}: cannot watch {act.ref!r} (use {'a Gesetzesnummer' if act.country == 'at' else 'the act reference'})")
            continue
        checked.add(act.country)
        log(f"{act.country}: {act.ref}")
        prev_data = (prev_items.get(key) or {}).get("data")
        try:
            fn = checkers[act.country]
            it = fn(http, act, prev_data) if act.country in ("sk", "de") else fn(http, act)
            new_items[key] = it
        except SourceError as e:
            fail(act.country, str(e))
            if key in prev_items:
                items[key] = prev_items[key]

    changes: list[dict] = []
    baseline: list[str] = []
    for key, it in new_items.items():
        st = it.to_state()
        old = prev_items.get(key)
        if old is not None:
            st["since"] = old.get("since", now)
            st["changed_at"] = old.get("changed_at", "")
        else:
            st["since"] = now
            st["changed_at"] = ""
        items[key] = st
        if old is None:
            # A state's first measure for an already-watched directive is news; anything else unseen is a baseline.
            if not first_run and key.startswith("nim:") and f"eu:{key.split(':')[1]}" in prev_items:
                old = {"data": {"measures": []}, "fingerprint": "new"}
            else:
                baseline.append(key)
                continue
        if old.get("fingerprint") == st["fingerprint"]:
            continue
        lines = describe(key, old.get("data") or {}, it.data)
        st["changed_at"] = now
        aligned = align.get(key, [])
        changes.append({
            "key": _KEY_SAFE.sub("_", key), "source": it.source, "label": _plain(it.label, 120),
            "title": _title(it, lines), "kinds": sorted({k for k, _ in lines}),
            "lines": [_plain(text) for _, text in lines], "url": it.url, "detected": now,
            "fingerprint": st["fingerprint"], "previous": old.get("fingerprint", ""),
            "alignments": aligned,
        })

    health = dict(prev_state.get("health", {}))
    for s in sorted(checked):
        h = dict(health.get(s, {}))
        if s in failed:
            h["failures"] = int(h.get("failures", 0)) + 1
            h["last_error"] = _plain("; ".join(failed[s]), 500)
            h["last_error_at"] = now
        else:
            h["failures"] = 0
            h["last_ok"] = now
            h.pop("last_error", None)
            h.pop("last_error_at", None)
        health[s] = h
    unhealthy = {s: h for s, h in health.items() if h.get("failures", 0) >= HEALTH_AFTER}

    events = [{k: c[k] for k in ("key", "source", "label", "title", "kinds", "lines", "url", "detected", "fingerprint")}
              for c in changes] + list(prev_state.get("events", []))
    state = {
        "schema": SCHEMA,
        "generator": "openacquis.alerts",
        "watchlist": watch.origin,
        "checked_at": now,
        "health": health,
        "items": dict(sorted(items.items())),
        "events": events[:MAX_EVENTS],
    }
    report = {
        "checked_at": now,
        "watchlist": watch.origin,
        "items": len(items),
        "changes": changes,
        "baseline": sorted(baseline),
        "warnings": warnings,
        "unhealthy": unhealthy,
        "requests": http.requests,
        "seconds": round(time.monotonic() - started, 1),
    }
    return state, report


# --- state file -------------------------------------------------------------------------


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not an alerts state file of schema {SCHEMA}")
    return data


def _stable(state: dict) -> dict:
    """The state without per-run timestamps, so an all-quiet run leaves the file (and git) untouched."""
    s = {k: v for k, v in state.items() if k != "checked_at"}
    s["health"] = {k: {kk: vv for kk, vv in v.items() if kk != "last_ok"} for k, v in state.get("health", {}).items()}
    return s


def dump_state(state: dict) -> str:
    """JSON with one line per item and per event: small, and a change shows as a one-line git diff."""
    def one(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

    parts = []
    for k, v in state.items():
        if k == "items" and isinstance(v, dict):
            body = ",\n".join(f"  {one(kk)}: {one(vv)}" for kk, vv in v.items())
            parts.append(f" {one(k)}: {{\n{body}\n }}" if v else f" {one(k)}: {{}}")
        elif k == "events" and isinstance(v, list):
            body = ",\n".join(f"  {one(e)}" for e in v)
            parts.append(f" {one(k)}: [\n{body}\n ]" if v else f" {one(k)}: []")
        else:
            parts.append(f" {one(k)}: {one(v)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def write_state(path: Path, state: dict, previous: dict | None = None) -> bool:
    """Write ``state`` unless only timestamps differ from ``previous``; ``True`` if the file was written."""
    if previous and _stable(previous) == _stable(state):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump_state(state), encoding="utf-8")
    tmp.replace(path)
    return True


# --- issue texts (GitHub markdown) ------------------------------------------------------


def _code(text: str) -> str:
    """Untrusted text as an inline code span: no mentions, links, images or markup."""
    t = _plain(text).replace("`", "'")
    return f"`{t}`"


def marker(key: str) -> str:
    return f"<!-- openacquis-alert:{_KEY_SAFE.sub('_', key)} -->"


NOTE = ("_Automatic signal from OpenAcquis change alerts: the source's record changed. It is a hint for a "
        "reviewer, not a finding about the transposition. Check the source before acting._")


def change_markdown(c: dict) -> str:
    lines = [f"**{_code(c['label'])}** ({c['source']}), detected {c['detected']}", ""]
    lines += [f"- {_code(x)}" for x in c["lines"]]
    lines += ["", f"Source: <{c['url']}>"]
    for a in c.get("alignments", []):
        built = f" (built on the text of {a['date']})" if a.get("date") else ""
        lines.append(f"Used by alignment page [{_code(a['id'])}]({a['url']}){built}: it may need a rebuild.")
    lines += ["", f"Fingerprint {c['previous'] or 'none'} → {c['fingerprint']}", "", NOTE]
    return "\n".join(lines)


def issue_body(c: dict) -> str:
    return marker(c["key"]) + "\n" + change_markdown(c)


def summary_markdown(report: dict) -> str:
    out = [f"## Change alerts ({report['checked_at']})", "",
           f"Watch list: {report['watchlist']}. {report['items']} items, {report['requests']} requests, "
           f"{report['seconds']} s.", ""]
    out.append(f"**{len(report['changes'])} change(s)**" + (":" if report["changes"] else "."))
    for c in report["changes"]:
        out.append(f"- {_code(c['title'])}")
    if report["baseline"]:
        out += ["", f"{len(report['baseline'])} item(s) seen for the first time (baseline, no alert)."]
    if report["warnings"]:
        out += ["", f"**{len(report['warnings'])} warning(s)** (sources that did not answer keep their previous "
                    "fingerprint; no alert is raised for them):"]
        out += [f"- {_code(w)}" for w in report["warnings"]]
    return "\n".join(out) + "\n"


def health_markdown(report: dict) -> str:
    out = ["<!-- openacquis-alerts-health -->",
           f"These sources failed in {HEALTH_AFTER} or more consecutive change-alert runs. Their items keep "
           "the last good fingerprint, so changes there are not detected until they answer again.", ""]
    for s, h in sorted(report["unhealthy"].items()):
        out.append(f"- **{s}**: {h['failures']} failed runs; last error {_code(h.get('last_error', ''))} "
                   f"({h.get('last_error_at', '')})")
    out += ["", "This issue is updated by the workflow and closed when every source answers again."]
    return "\n".join(out) + "\n"


def write_outputs(report: dict, out_dir: Path) -> None:
    """``changes.json``, ``summary.md``, ``issues/NNN/{key,title,body.md,comment.md}`` and, if a source keeps
    failing, ``health.md``: plain files the workflow hands to ``gh`` (never interpolated into scripts)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "changes.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out_dir / "summary.md").write_text(summary_markdown(report), encoding="utf-8")
    for i, c in enumerate(report["changes"], 1):
        d = out_dir / "issues" / f"{i:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "key").write_text(marker(c["key"]), encoding="utf-8")
        (d / "title").write_text(c["title"], encoding="utf-8")
        (d / "body.md").write_text(issue_body(c), encoding="utf-8")
        (d / "comment.md").write_text(change_markdown(c), encoding="utf-8")
    health = out_dir / "health.md"
    if report["unhealthy"]:
        health.write_text(health_markdown(report), encoding="utf-8")
    elif health.exists():
        health.unlink()


# --- Atom feed ----------------------------------------------------------------------------


def _attr(text: str) -> str:
    return xml_escape(str(text), {'"': "&quot;"})


def render_feed(state: dict, site_url: str = SITE_URL, limit: int = 50) -> str:
    """An Atom feed of the latest change events (valid, with no entries, before the first change)."""
    events = state.get("events", [])[:limit]
    updated = (events[0]["detected"] if events else state.get("checked_at")) or "1970-01-01T00:00:00Z"
    x = xml_escape
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<feed xmlns="http://www.w3.org/2005/Atom">',
           "  <title>OpenAcquis change alerts</title>",
           "  <subtitle>Changes in EU and national legal sources watched by OpenAcquis. Signals for review, "
           "not findings.</subtitle>",
           f"  <id>{FEED_ID}</id>",
           f'  <link rel="self" type="application/atom+xml" href="{_attr(site_url)}feed.xml"/>',
           f'  <link rel="alternate" type="text/html" href="{_attr(site_url)}"/>',
           f"  <updated>{x(updated)}</updated>",
           "  <author><name>OpenAcquis</name></author>"]
    for e in events:
        content = "\n".join(_plain(line) for line in e.get("lines", []))
        out += ["  <entry>",
                f"    <id>{FEED_ID}/{x(_KEY_SAFE.sub('_', e['key']))}/{x(e.get('fingerprint', ''))}</id>",
                f"    <title>{x(_plain(e.get('title', ''), 200))}</title>",
                f"    <updated>{x(e.get('detected', updated))}</updated>",
                f'    <link rel="alternate" href="{_attr(e.get("url") or site_url)}"/>',
                f'    <category term="{_attr(e.get("source", ""))}"/>',
                f'    <content type="text">{x(content)}</content>',
                "  </entry>"]
    out.append("</feed>")
    return "\n".join(out) + "\n"


def write_feed(state_path: Path, out_path: Path, site_url: str = SITE_URL) -> int:
    """Render ``state_path``'s events to ``out_path``; returns the number of entries."""
    state = load_state(state_path) if state_path.exists() else {}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_feed(state, site_url), encoding="utf-8")
    return min(len(state.get("events", [])), 50)


# --- command line -------------------------------------------------------------------------


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    import argparse

    root = root or Path.cwd()
    p = argparse.ArgumentParser(prog="check_changes", description="Change alerts for watched EU and national sources.")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="check the sources against the state file (default)")
    c.add_argument("--state", type=Path, default=root / "data" / "alerts" / "state.json")
    c.add_argument("--watch", type=Path, default=root / "data" / "directives.json",
                   help="directives file (default data/directives.json; built-in NIS2 list if missing)")
    c.add_argument("--align-dir", type=Path, default=None,
                   help="alignment pages, to say which ones a changed act affects (default site/data/align, "
                        "else the committed site/static-data)")
    c.add_argument("--out-dir", type=Path, help="write changes.json, summary.md, issues/ and health.md here")
    c.add_argument("--sources", default=",".join(SOURCES), help=f"comma-separated subset of {','.join(SOURCES)}")
    c.add_argument("--dry-run", action="store_true", help="do not write the state file")
    c.add_argument("--delay", type=float, default=1.0, help="seconds between requests to one host (default 1)")
    c.add_argument("--cache-ttl", type=float, default=0.0,
                   help="reuse plain GET/POST answers younger than this many seconds from $OPENACQUIS_CACHE/alerts "
                        "(default 0: off)")
    f = sub.add_parser("feed", help="render the Atom feed from the state file")
    f.add_argument("--state", type=Path, default=root / "data" / "alerts" / "state.json")
    f.add_argument("--out", type=Path, default=root / "site" / "feed.xml")
    f.add_argument("--site-url", default=SITE_URL)
    args = p.parse_args(["check", *(argv or [])] if not argv or argv[0] not in ("check", "feed", "-h", "--help") else argv)

    if args.cmd == "feed":
        n = write_feed(args.state, args.out, args.site_url)
        print(f"{args.out}: {n} entries")
        return 0

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)

    watch = load_watchlist(args.watch)
    try:
        watch.origin = str(Path(watch.origin).resolve().relative_to(root.resolve()))
    except ValueError:
        pass
    prev = load_state(args.state)
    http = Http(delay=args.delay, cache_dir=default_cache_dir(), cache_ttl=args.cache_ttl)
    unknown = set(args.sources.split(",")) - set(SOURCES)
    if unknown:
        p.error(f"unknown source(s): {', '.join(sorted(unknown))}")
    align_dir = args.align_dir
    if align_dir is None:
        align_dir = root / "site" / "data" / "align"
        if not align_dir.is_dir():
            align_dir = root / "site" / "static-data"
    state, report = run_check(watch, prev, http, sources=args.sources.split(","),
                              align=alignment_index(align_dir), log=log)
    written = False if args.dry_run else write_state(args.state, state, prev)
    if args.out_dir:
        write_outputs(report, args.out_dir)
    print(json.dumps({
        "watchlist": report["watchlist"], "items": report["items"], "changes": len(report["changes"]),
        "baseline": len(report["baseline"]), "warnings": report["warnings"], "requests": report["requests"],
        "seconds": report["seconds"], "state_written": written,
        "titles": [ch["title"] for ch in report["changes"]],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
