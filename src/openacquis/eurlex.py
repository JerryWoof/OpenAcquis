"""Fetch EU act texts per language and split them into provisions.

Where the text comes from
-------------------------
1. **CELLAR content negotiation** (default, tried first):
   ``https://publications.europa.eu/resource/celex/{CELEX}`` with
   ``Accept: application/xhtml+xml`` and ``Accept-Language: {lang}`` redirects to
   the XHTML manifestation of that language version. For acts published since
   about 2014 this is the Publications Office "CONVEX" output: the same markup
   EUR-Lex shows, with ``oj-*`` CSS classes and ELI anchors (``art_23``,
   ``023.004``, ``rct_1``, ``anx_I``). Older acts only have a plain
   ``text/html`` manifestation, which is requested next.
2. **EUR-Lex** ``/legal-content/{LANG}/TXT/HTML/?uri=CELEX:{CELEX}`` (fallback).
   EUR-Lex sits behind an AWS WAF that answers scripted clients with
   ``202`` + ``x-amzn-waf-action: challenge`` and an empty body; that is
   detected (:func:`is_waf_challenge`) and reported instead of being parsed.

Formex (the OJ's XML schema) is also in CELLAR, but the XHTML carries the same
structure and is what readers see on EUR-Lex, so it is the format parsed here.

Parsing
-------
:func:`parse_html` turns either format into a :class:`~openacquis.provisions.Document`:
articles (with headings), numbered paragraphs ``1.``, points ``(a)``/``a)``/``(1)``,
sub-points ``(i)``/``i)``/``i.``, indents ``—``; recitals and annexes optionally (an
annex is kept as one provision with its flattened text). Greek (``α)``, ``στ)``) and
Cyrillic (``а)``, ``б)``) point letters get the Latin number at the same position
(``a``, ``f``, ``a``, ``b``) so IDs line up across languages. Text an amending act
quotes (``‘(17) ...’;``) stays text of the amending point. Text that follows
points inside a paragraph (a later subparagraph) is appended to the paragraph's
own text. If the same point label occurs twice in one container (two lists in
two subparagraphs) the second one gets the number ``a_2``.

Downloads are cached on disk under ``~/.cache/openacquis/eurlex`` (override the
root with the ``OPENACQUIS_CACHE`` environment variable).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .cellar import USER_AGENT
from .provisions import Document, Provision, int_to_roman, make_id, normalize_number, roman_to_int

CELLAR_CELEX = "https://publications.europa.eu/resource/celex/{celex}"
EURLEX_HTML = "https://eur-lex.europa.eu/legal-content/{lang}/TXT/HTML/?uri=CELEX:{celex}"
EURLEX_PAGE = "https://eur-lex.europa.eu/legal-content/{lang}/TXT/?uri=CELEX:{celex}"

#: The 24 official EU languages (ISO 639-1).
LANGUAGES = (
    "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "ga", "hr",
    "hu", "it", "lt", "lv", "mt", "nl", "pl", "pt", "ro", "sk", "sl", "sv",
)

CELEX_RE = re.compile(r"^[0-9CE]\d{4}[A-Z]{1,2}\d{4}$")
DEFAULT_SOURCES = ("cellar", "eurlex")


# --- errors ---------------------------------------------------------------
class TextNotAvailable(LookupError):
    """No source returned a usable text for this act and language."""


# --- HTTP -----------------------------------------------------------------
@dataclass
class Response:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def text(self) -> str:
        m = re.search(r"charset=([\w-]+)", self.content_type, re.IGNORECASE)
        return self.body.decode(m.group(1) if m else "utf-8", errors="replace")


#: ``get(url, headers) -> Response``; never raises for HTTP status codes.
HttpGet = Callable[[str, dict[str, str]], Response]


def http_get(url: str, headers: dict[str, str], timeout: float = 60.0) -> Response:
    """GET with the project User-Agent; HTTP errors come back as a :class:`Response`."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, resp.url, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, url, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b"")


def is_waf_challenge(resp: Response) -> bool:
    """True when EUR-Lex's bot protection answered instead of the document."""
    if resp.headers.get("x-amzn-waf-action"):
        return True
    if resp.status == 202 and not resp.body.strip():
        return True
    head = resp.body[:4000].lower()
    return b"awswafintegration" in head or b"challenge.js" in head or b"gokuprops" in head


# --- validation -------------------------------------------------------------
def validate_celex(celex: str) -> str:
    c = celex.strip().upper()
    if not CELEX_RE.match(c):
        raise ValueError(f"{celex!r} is not a CELEX number (expected e.g. 32022L2555 for NIS2)")
    return c


def validate_language(language: str) -> str:
    lang = language.strip().lower()
    if lang not in LANGUAGES:
        raise ValueError(f"{language!r} is not an official EU language code ({', '.join(LANGUAGES)})")
    return lang


def eli_for_celex(celex: str) -> str | None:
    """ELI of the original OJ publication for directives/regulations/decisions."""
    m = re.match(r"^3(\d{4})([LRD])(\d{4})$", celex)
    if not m:
        return None
    kind = {"L": "dir", "R": "reg", "D": "dec"}[m.group(2)]
    return f"http://data.europa.eu/eli/{kind}/{m.group(1)}/{int(m.group(3))}/oj"


# --- cache ------------------------------------------------------------------
def default_cache_dir() -> Path:
    """``$OPENACQUIS_CACHE`` or ``~/.cache/openacquis``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    return Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"


@dataclass
class SourceText:
    """Raw text of one language version, as downloaded."""

    celex: str
    language: str
    source: str  # "cellar" or "eurlex"
    url: str  # final URL(s) the body came from
    content_type: str
    html: str


class _Cache:
    def __init__(self, root: Path):
        self.dir = root / "eurlex"

    def _paths(self, celex: str, lang: str) -> tuple[Path, Path]:
        stem = f"{celex}_{lang}"
        return self.dir / f"{stem}.html", self.dir / f"{stem}.json"

    def get(self, celex: str, lang: str) -> SourceText | None:
        body, meta = self._paths(celex, lang)
        if not (body.exists() and meta.exists()):
            return None
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            return SourceText(html=body.read_text(encoding="utf-8"), **m)
        except (OSError, ValueError, TypeError):
            return None

    def put(self, st: SourceText) -> None:
        body, meta = self._paths(st.celex, st.language)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            body.write_text(st.html, encoding="utf-8")
            m = {k: getattr(st, k) for k in ("celex", "language", "source", "url", "content_type")}
            meta.write_text(json.dumps(m), encoding="utf-8")
        except OSError:
            pass  # caching is best effort


# --- client -----------------------------------------------------------------
class EurLexClient:
    """Download act texts and parse them into provisions.

    ``get`` replaces the network (tests pass a stub); ``cache_dir`` is a path,
    ``None`` for :func:`default_cache_dir` or ``False`` to disable caching;
    ``sources`` is the order in which ``"cellar"`` and ``"eurlex"`` are tried.
    """

    def __init__(
        self,
        get: HttpGet | None = None,
        cache_dir: Path | str | None | bool = None,
        sources: tuple[str, ...] = DEFAULT_SOURCES,
    ):
        self._get = get or http_get
        if cache_dir is False:
            self._cache = None
        else:
            self._cache = _Cache(Path(cache_dir) if cache_dir not in (None, True) else default_cache_dir())
        unknown = set(sources) - set(DEFAULT_SOURCES)
        if unknown:
            raise ValueError(f"unknown sources {sorted(unknown)}; use {DEFAULT_SOURCES}")
        self.sources = sources

    def fetch(self, celex: str, language: str, refresh: bool = False) -> SourceText:
        """Raw (X)HTML of one language version, from cache or the first working source."""
        celex, lang = validate_celex(celex), validate_language(language)
        if self._cache and not refresh:
            hit = self._cache.get(celex, lang)
            if hit:
                return hit
        problems: list[str] = []
        for source in self.sources:
            fetcher = self._from_cellar if source == "cellar" else self._from_eurlex
            st = fetcher(celex, lang, problems)
            if st:
                if self._cache:
                    self._cache.put(st)
                return st
        raise TextNotAvailable(f"No text for {celex} in {lang.upper()}: " + "; ".join(problems))

    def document(
        self,
        celex: str,
        language: str,
        recitals: bool = False,
        annexes: bool = True,
        refresh: bool = False,
    ) -> Document:
        """Fetch and split one language version into a :class:`Document`."""
        st = self.fetch(celex, language, refresh=refresh)
        return parse_html(st.html, st.celex, st.language, recitals=recitals, annexes=annexes)

    # sources ---------------------------------------------------------------
    def _from_cellar(self, celex: str, lang: str, problems: list[str]) -> SourceText | None:
        url = CELLAR_CELEX.format(celex=celex)
        for accept in ("application/xhtml+xml", "text/html"):
            headers = {"Accept": accept, "Accept-Language": lang}
            resp = self._get(url, headers)
            if resp.status == 300:  # several documents (e.g. DOC_1, DOC_2): fetch them all
                resp = self._multiple_choices(resp, headers)
            if resp.status == 200 and resp.body.strip():
                return SourceText(celex, lang, "cellar", resp.url, resp.content_type, resp.text())
            problems.append(f"CELLAR {accept}: HTTP {resp.status}")
        return None

    def _multiple_choices(self, resp: Response, headers: dict[str, str]) -> Response:
        links = list(dict.fromkeys(re.findall(r'href="([^"]+/DOC_\d+)"', resp.text())))
        links.sort(key=lambda u: int(u.rsplit("_", 1)[1]))
        parts = [self._get(u, headers) for u in links]
        if not parts or any(p.status != 200 for p in parts):
            return Response(300, resp.url, resp.headers, b"")
        body = b"\n".join(p.body for p in parts)
        return Response(200, " ".join(p.url for p in parts), parts[0].headers, body)

    def _from_eurlex(self, celex: str, lang: str, problems: list[str]) -> SourceText | None:
        resp = self._get(EURLEX_HTML.format(lang=lang.upper(), celex=celex), {"Accept": "text/html"})
        if is_waf_challenge(resp):
            problems.append("EUR-Lex: bot challenge (WAF), not parsed")
        elif resp.status == 200 and resp.body.strip():
            return SourceText(celex, lang, "eurlex", resp.url, resp.content_type, resp.text())
        else:
            problems.append(f"EUR-Lex: HTTP {resp.status}")
        return None


def fetch_document(celex: str, language: str, **kw) -> Document:
    """Convenience: ``EurLexClient().document(celex, language, **kw)``."""
    return EurLexClient().document(celex, language, **kw)


# --- minimal DOM ------------------------------------------------------------
_VOID = {"br", "hr", "col", "img", "meta", "link", "input", "area", "base", "wbr", "source"}
# Opening one of these implicitly closes an open <p> (HTML rule; old EUR-Lex pages rely on it).
_CLOSES_P = {"p", "div", "table", "ul", "ol", "dl", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "pre", "blockquote"}


@dataclass(eq=False)
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None = None
    children: list[_Node | str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        """CSS classes with the ``oj-`` prefix removed (old and new OJ markup agree)."""
        return {c[3:] if c.startswith("oj-") else c for c in self.attrs.get("class", "").split()}

    def elements(self) -> Iterator[_Node]:
        return (c for c in self.children if isinstance(c, _Node))


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.cur = self.root
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag in _CLOSES_P and self.cur.tag == "p" and self.cur.parent is not None:
            self.cur = self.cur.parent
        node = _Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        if tag not in ("script", "style"):
            self.cur.children.append(_Node(tag, {k: v or "" for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        n: _Node | None = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if not self._skip:
            self.cur.children.append(data)


def _parse_dom(html: str) -> _Node:
    b = _TreeBuilder()
    b.feed(html)
    b.close()
    return b.root


def _is_note_ref(n: _Node) -> bool:
    if n.tag != "a":
        return False
    if n.attrs.get("href", "").startswith("#ntr") or n.attrs.get("id", "").startswith("ntc"):
        return True
    return any("note-tag" in c.classes for c in n.elements())


def _raw_text(n: _Node) -> str:
    out: list[str] = []

    def rec(x: _Node) -> None:
        for c in x.children:
            if isinstance(c, str):
                out.append(c)
            elif c.tag == "br":
                out.append(" ")
            elif not _is_note_ref(c):
                rec(c)

    rec(n)
    return "".join(out)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _text(n: _Node) -> str:
    return _clean(_raw_text(n))


def _rows(table: _Node) -> list[_Node]:
    rows = []
    for c in table.elements():
        if c.tag == "tr":
            rows.append(c)
        elif c.tag in ("tbody", "thead", "tfoot"):
            rows.extend(r for r in c.elements() if r.tag == "tr")
    return rows


def _lines(n: _Node) -> Iterator[str]:
    """Readable lines of a block: one per paragraph, one per innermost table row."""
    if n.tag == "tr" and not any(d.tag == "table" for d in _descendants(n)):
        line = " ".join(t for t in (_text(td) for td in n.elements()) if t)
        if line:
            yield line
        return
    if n.tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd") and not any(
        d.tag in ("p", "div", "table") for d in _descendants(n)
    ):
        t = _text(n)
        if t:
            yield t
        return
    for c in n.elements():
        if not _is_note_ref(c):
            yield from _lines(c)


def _descendants(n: _Node) -> Iterator[_Node]:
    for c in n.elements():
        yield c
        yield from _descendants(c)


# --- labels -----------------------------------------------------------------
# Point letters: Latin, Greek (α, β, ... στ, ια) and Cyrillic (а, б, ...).
_LETTERS = r"[0-9A-Za-z]{1,6}|[α-ωϛϙ]{1,4}|[а-яѝ]{1,3}"
_POINT_LABEL = re.compile(
    rf"^(\(\s*(?:{_LETTERS})\s*\)|(?:{_LETTERS})\)|\d{{1,3}}[a-z]?\.|[ivxlc]{{1,6}}\.|[—–\-•▪])$"
)
_DASH = {"—", "–", "-", "•", "▪"}
# "1. text" (most languages) or "(1) text" (German): number in group 1 or 2.
_PARAGRAPH = re.compile(r"^(?:(\d{1,3}[a-z]?)\.|\((\d{1,3}[a-z]?)\))\s+(.+)$", re.DOTALL)
_ARTICLE_NUM = re.compile(r"\d+\s?[a-z]?\b|\d+")
# Greek points are numbered with Greek numerals (α=1 ... θ=9, ι=10, ια=11, στ=6),
# Bulgarian ones with the Cyrillic alphabet (ъ and ь are not used). Both are
# mapped to the Latin letter at the same position so IDs line up across languages.
_GREEK_NUM = {
    "α": 1, "β": 2, "γ": 3, "δ": 4, "ε": 5, "ϛ": 6, "ζ": 7, "η": 8, "θ": 9,
    "ι": 10, "κ": 20, "λ": 30, "μ": 40, "ν": 50, "ξ": 60, "ο": 70, "π": 80, "ϙ": 90,
}
_CYRILLIC_ORDER = "абвгдежзийклмнопрстуфхцчшщюя"


def _alpha(n: int) -> str:
    """1 -> ``a``, 26 -> ``z``, 27 -> ``aa`` (spreadsheet-style)."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out


def _latin_label(label: str) -> str:
    """Greek / Cyrillic point letters as their Latin positional equivalent: ``α)`` -> ``a``.

    Any other label is returned unchanged.
    """
    core = label.strip("()[]. \u2018\u201e«").lower()
    greek = core.replace("στ", "ϛ")
    if greek and all(c in _GREEK_NUM for c in greek):
        return _alpha(sum(_GREEK_NUM[c] for c in greek))
    if len(core) == 1 and core in _CYRILLIC_ORDER:
        return _alpha(_CYRILLIC_ORDER.index(core) + 1)
    return label


# Text quoted by an amending act (``‘(17) ...’;``, ``„Article 2a ...“``): opening
# quote -> characters that close it.
_QUOTES = {"‘": "’", "„": "“”", "«": "»", "“": "”", "‚": "‘’"}
_APOSTROPHE = re.compile(r"(?<=\w)[’'](?=\w)")


def _quote_opened(line: str) -> str | None:
    """The opening quote if ``line`` starts a quotation it does not close itself."""
    if not line or line[0] not in _QUOTES:
        return None
    o, s = line[0], _APOSTROPHE.sub("", line)
    closes = sum(s.count(c) for c in _QUOTES[o] if c != o)
    return o if closes < s.count(o) else None


def _track_quote(line: str, quote: str | None) -> str | None:
    """The quotation open after ``line``, given the one open before it."""
    if quote is None:
        return _quote_opened(line)
    return None if _quote_closed(line, quote) else quote


def _quote_closed(line: str, quote: str) -> bool:
    """``line`` ends the quotation opened by ``quote`` (``...’;`` / ``...“.``)."""
    s = _APOSTROPHE.sub("", line).rstrip(" ;.,:")
    closers = _QUOTES[quote]
    if not s or s[-1] not in closers:
        return False
    return sum(s.count(c) for c in closers if c != quote) > s.count(quote)


def _point_rows(table: _Node) -> list[tuple[str, _Node]] | None:
    """``[(label, content_cell)]`` if every row of the table is a numbered point."""
    rows = _rows(table)
    out = []
    for r in rows:
        cells = [c for c in r.elements() if c.tag in ("td", "th")]
        if len(cells) != 2:
            return None
        label = _text(cells[0])
        if not _POINT_LABEL.match(label):
            return None
        out.append((label, cells[1]))
    return out or None


def _paragraph(text: str, parens: bool = True) -> tuple[str, str, str] | None:
    """``(label, number, rest)`` if ``text`` opens a numbered paragraph."""
    m = _PARAGRAPH.match(text)
    if not m or (m.group(2) and not parens):
        return None
    number = m.group(1) or m.group(2)
    label = f"{number}." if m.group(1) else f"({number})"
    return label, number, m.group(3).strip()


def _article_number(label: str) -> str:
    m = _ARTICLE_NUM.search(label)
    return m.group(0).replace(" ", "") if m else label


def _anchor(n: _Node, prefix: tuple[str, ...]) -> str | None:
    x: _Node | None = n
    while x is not None:
        i = x.attrs.get("id", "")
        if i.startswith(prefix):
            return i
        x = x.parent
    return None


# --- document builder ---------------------------------------------------------
class _DocBuilder:
    def __init__(self, celex: str, lang: str):
        self.celex, self.lang = celex, lang
        self.page = EURLEX_PAGE.format(lang=lang.upper(), celex=celex)
        self.doc = Document(source="eu", act_id=celex, language=lang, eli=eli_for_celex(celex), url=self.page)
        self._paths: dict[str | None, list[tuple[str, str]]] = {None: []}

    def add(
        self,
        kind: str,
        label: str,
        number: str,
        parent: Provision | None,
        text: str = "",
        heading: str | None = None,
        anchor: str | None = None,
    ) -> Provision:
        parent_id = parent.id if parent else None
        base = normalize_number(number)
        num, k = base, 1
        while True:
            path = self._paths[parent_id] + [(kind, num)]
            pid = make_id("eu", self.celex, path, self.lang)
            if self.doc.get(pid) is None:
                break
            k += 1
            num = f"{base}_{k}"
        if anchor is None and parent is not None:
            url = parent.url
        else:
            url = f"{self.page}#{anchor}" if anchor else self.page
        p = Provision(
            id=pid, source="eu", act_id=self.celex, language=self.lang, kind=kind, label=label,
            number=num, text=text, heading=heading, parent_id=parent_id, url=url,
        )
        self._paths[pid] = path
        return self.doc.add(p)

    @staticmethod
    def append(p: Provision, text: str) -> None:
        if text:
            p.text = f"{p.text}\n{text}" if p.text else text

    def point_kind(self, parent: Provision, label: str) -> tuple[str, str]:
        """(kind, number) for a point-like child of ``parent``."""
        if label in _DASH:
            n = sum(1 for c in self.doc.children(parent.id) if c.kind == "indent") + 1
            return "indent", str(n)
        kind = "point" if parent.kind in ("article", "paragraph", "annex") else "subpoint"
        return kind, _latin_label(label)


# --- CONVEX (oj-*) parser -------------------------------------------------------
class _OjParser:
    """Walks Publications Office (X)HTML: ``oj-ti-art``, ``oj-normal``, point tables, ELI ids.

    Annexes are found by their ``anx_*`` id or, where a manifestation has no
    such ids (some language versions, older CONVEX output), by a ``doc-ti``
    title such as ``ANNEX II`` / ``ΠΑΡΑΡΤΗΜΑ II`` after the articles. Text that
    an amending act quotes (``‘(17) ...’;``, a replaced ``‘Article 19a ...’``)
    stays text of the amending provision, like the Czech adapter's ``Block_Citace``.
    """

    def __init__(self, b: _DocBuilder, recitals: bool, annexes: bool):
        self.b, self.recitals, self.annexes = b, recitals, annexes
        self.article: Provision | None = None
        self.paragraph: Provision | None = None
        self.annex_open: Provision | None = None  # annex started by a bare doc-ti title
        self.in_annex = False
        self.quote: str | None = None  # open quotation at article level
        self.seen_article = False
        self.title: list[str] = []

    def run(self, root: _Node) -> None:
        self.visit(root)
        if self.title:
            self.b.doc.title = " ".join(self.title)

    def _close_article(self) -> None:
        self.article = self.paragraph = None
        self.quote = None

    def visit(self, n: _Node) -> None:
        for c in n.elements():
            self.element(c)

    def element(self, n: _Node) -> None:
        cls, nid = n.classes, n.attrs.get("id", "")
        if n.tag == "hr" or "note" in cls or _is_note_ref(n):
            return
        if n.tag == "div" and "final" in cls:
            self._close_article()
            return
        if n.tag == "div" and (nid.startswith("anx_") or self._annex_div(n)):
            self._close_article()
            self.annex_open, self.in_annex = None, True
            if self.annexes:
                self.annex(n, nid)
            return
        if n.tag == "p":
            self.paragraph_node(n, cls)
            return
        if n.tag == "table":
            self.table(n)
            return
        self.visit(n)
        if nid.startswith("art_") and "." not in nid:
            self._close_article()

    def _annex_div(self, n: _Node) -> bool:
        """A div after the articles whose first paragraph is an annex title."""
        if not self.seen_article:
            return False
        first = next((d for d in _descendants(n) if d.tag == "p"), None)
        return first is not None and "doc-ti" in first.classes and bool(_ANNEX_LINE.match(_text(first)))

    def paragraph_node(self, n: _Node, cls: set[str]) -> None:
        text = _text(n)
        if not text:
            return
        if self.article is not None and (self.quote or _quote_opened(text)):
            self._quoted(text, self.paragraph or self.article)
            return
        if "ti-art" in cls:
            self.seen_article = True
            self.annex_open, self.in_annex = None, False
            self.article = self.b.add(
                "article", text, _article_number(text), None, anchor=_anchor(n, ("art_",))
            )
            self.paragraph = None
            return
        if "sti-art" in cls and self.article is not None and self.article.heading is None:
            self.article.heading = text
            return
        if not self.seen_article:
            if "doc-ti" in cls and not self.b.doc.provisions:
                self.title.append(text)
            return
        if "doc-ti" in cls and _ANNEX_LINE.match(text):  # annex without an enclosing div/id
            self._close_article()
            self.in_annex = True
            self.annex_open = (
                self.b.add("annex", text, _annex_number(text), None) if self.annexes else None
            )
            return
        if self.annex_open is not None:
            a = self.annex_open
            if "doc-ti" in cls and a.heading is None and not a.text:
                a.heading = text
            else:
                self.b.append(a, text)
            return
        if any(c.startswith("ti-section") for c in cls):
            self._close_article()
            return
        if self.article is None or "signatory" in cls:
            return
        m = _paragraph(text)
        if m and self.b.doc.get(self._para_id(m[1])) is None:
            self.paragraph = self.b.add(
                "paragraph", m[0], m[1], self.article, text=m[2], anchor=_anchor(n, tuple("0123456789"))
            )
            return
        self.b.append(self.paragraph or self.article, text)

    def _quoted(self, line: str, target: Provision) -> None:
        """Append one line of (possibly) quoted text and track the open quotation."""
        self.b.append(target, line)
        self.quote = _track_quote(line, self.quote)

    def _para_id(self, number: str) -> str:
        assert self.article is not None
        return f"{self.article.id}.par{normalize_number(number)}"

    def table(self, n: _Node) -> None:
        if self.annex_open is not None:
            for line in _lines(n):
                self.b.append(self.annex_open, line)
            return
        if self.in_annex:
            return
        if not self.seen_article:
            rows = _point_rows(n)
            if self.recitals and rows:
                for label, cell in rows:
                    if re.fullmatch(r"\(\d+\)", label):
                        self.b.add(
                            "recital", label, label, None, text="\n".join(_lines(cell)),
                            anchor=_anchor(n, ("rct_",)),
                        )
            return
        container = self.paragraph or self.article
        if container is None:
            return
        if self.quote or _quote_opened(next(_lines(n), "")):
            for line in _lines(n):
                self._quoted(line, container)
            return
        rows = _point_rows(n)
        if rows is None:
            for line in _lines(n):
                self.b.append(container, line)
            return
        for label, cell in rows:
            self.point(label, cell, container)

    def point(self, label: str, cell: _Node, parent: Provision) -> None:
        kind, number = self.b.point_kind(parent, label)
        p = self.b.add(kind, label, number, parent)
        self.cell(cell, p)

    def cell(self, n: _Node, p: Provision, quote: str | None = None) -> str | None:
        """Fill point ``p`` from a table cell; returns the quotation still open, if any."""
        for c in n.elements():
            if _is_note_ref(c):
                continue
            if quote is not None or (
                c.tag in ("p", "table", "div") and _quote_opened(next(_lines(c), ""))
            ):
                for line in _lines(c):
                    self.b.append(p, line)
                    quote = _track_quote(line, quote)
                continue
            if c.tag == "table":
                rows = _point_rows(c)
                if rows:
                    for label, cell in rows:
                        self.point(label, cell, p)
                else:
                    for line in _lines(c):
                        self.b.append(p, line)
            elif c.tag == "p":
                self.b.append(p, _text(c))
            else:
                quote = self.cell(c, p, quote)
        return quote

    def annex(self, n: _Node, nid: str) -> None:
        lines = list(_lines(n))
        if not lines:
            return
        label = lines.pop(0)
        heading = None
        titles = [_text(p) for p in _descendants(n) if p.tag == "p" and "doc-ti" in p.classes]
        if len(titles) > 1 and lines and lines[0] == titles[1]:
            heading = lines.pop(0)
        number = nid[4:] if nid.startswith("anx_") and nid[4:] else _annex_number(label)
        self.b.add("annex", label, number, None, text="\n".join(lines), heading=heading, anchor=nid or None)


def _annex_number(label: str) -> str:
    """``"ANNEX II"`` -> ``"II"``; a lone ``"ANNEX"`` -> ``"1"``."""
    m = _ANNEX_LINE.match(label)
    if not m:
        return label
    return (m.group(2) or "").strip() or "1"


# --- legacy plain-HTML parser ----------------------------------------------------
_ARTICLE_WORDS = (
    r"Article|Artikel|Articolo|Artículo|Artigo|Articolul|Artikolu|Airteagal|Artykuł|"
    r"Článek|Článok|Članak|Člen|Член|Άρθρο|Artikkel"
)
# "Article 1", "Article 1a", "12 artikla" -- optionally followed on the same line by
# the heading, as in some old plain-HTML texts ("Article 1 Objective").
_ARTICLE_LINE = re.compile(
    rf"^(?P<label>(?:{_ARTICLE_WORDS})\s+\d+(?:\s?[a-z](?![^\W\d_]))?"
    rf"|\d+[a-z]?\.?\s+(?:artikla|cikk|straipsnis|pants))(?:\s+(?P<heading>\S.*?))?\s*$",
    re.IGNORECASE,
)
# Chapter / section / title headings between articles ("CHAPTER II", "Section 2: Commercial
# communications", "I LUKU", "2. FEJEZET").
_HEADING_WORDS = (
    r"CHAPTER|SECTION|SUB-SECTION|SUBSECTION|TITLE|PART|CHAPITRE|TITRE|PARTIE|KAPITEL|ABSCHNITT|"
    r"UNTERABSCHNITT|TITEL|TEIL|KAPITOLA|ODDÍL|ODDIEL|HLAVA|ČÁST|ČASŤ|CAPO|SEZIONE|TITOLO|PARTE|"
    r"CAPÍTULO|SECCIÓN|SECÇÃO|TÍTULO|HOOFDSTUK|AFDELING|DEEL|ROZDZIAŁ|SEKCJA|DZIAŁ|TYTUŁ|POGLAVJE|"
    r"ODDELEK|POGLAVLJE|ODJELJAK|ГЛАВА|РАЗДЕЛ|ДЯЛ|ΚΕΦΑΛΑΙΟ|ΤΜΗΜΑ|ΤΙΤΛΟΣ|ΜΕΡΟΣ|CAPITOLUL|"
    r"SECȚIUNEA|SECŢIUNEA|TITLUL|AFSNIT|AVSNITT|AVDELNING|KAPITLU|TAQSIMA|TITOLU|CAIBIDIL|ROINN|TEIDEAL"
)
_HEADING_LINE = re.compile(
    rf"^(?:(?:{_HEADING_WORDS})\s+(?:[IVXLC]+|\d+[a-z]?)\b\.?(?:\s*[:.\-–—]\s*\S.*)?"
    r"|(?:[IVXLC]+|\d+)\.?\s+(?:LUKU|JAKSO|OSASTO|FEJEZET|SZAKASZ|CÍM|SKYRIUS|SKIRSNIS|DALIS|"
    r"NODAĻA|IEDAĻA|SADAĻA|PEATÜKK|JAGU|OSA)\b.*)$",
    re.IGNORECASE,
)


def _article_line(line: str) -> tuple[str, str | None] | None:
    """``(label, heading)`` if ``line`` opens an article; heading ``None`` if not on the line."""
    m = _ARTICLE_LINE.match(line)
    if not m:
        return None
    heading = m.group("heading")
    if heading is not None and (
        not heading[0].isupper() or len(heading) > 150 or heading.endswith((".", ",", ";", ":"))
    ):
        return None  # an in-text reference such as "Article 5 of Directive ..."
    return m.group("label"), heading


def _is_heading_line(line: str) -> bool:
    """A chapter/section/title heading (keyword, or an all-capitals line of words)."""
    if len(line) > 200 or line.endswith((",", ";")):
        return False
    if _HEADING_LINE.match(line):
        return True
    letters = sum(c.isalpha() for c in line)
    return (
        line.isupper()
        and not any(c.isdigit() for c in line)
        and letters >= 4
        and letters >= 0.8 * len(line.replace(" ", ""))
    )


_ANNEX_LINE = re.compile(
    r"^(ANNEX|ANNEXE|ANHANG|PŘÍLOHA|PRÍLOHA|PRILOGA|PRILOG|ALLEGATO|ANEXO|ANEXA|BIJLAGE|BILAG|"
    r"BILAGA|LIITE|MELLÉKLET|ZAŁĄCZNIK|PRIEDAS|PIELIKUMS|LISA|ΠΑΡΑΡΤΗΜΑ|ПРИЛОЖЕНИЕ|"
    r"IARSCRÍBHINN|ANNESS)(\s+[IVXLC]+|\s+\d+)?\s*$"
)
_FINAL_LINE = re.compile(
    r"^(Done at|Fait à|Geschehen zu|Hecho en|Fatto a|Gedaan te|Feito em|Udfærdiget i|Utfärdad i|"
    r"V Bruseli|V Lucemburku|V Štrasburku|V Luxemburgu|V Štrasburgu|V Bruseli dne|Съставено в|Ετέθη|"
    r"Sporządzono w|Adoptat|Kelt|Briselē|Luksemburgā|Strasbūrā|Priimta|Helsingissä|Brysselissä|"
    r"Luxemburgissa|Strasbourgissa|Brüssel|Magħmul)"
)
_LEGACY_LETTERS = r"[a-z]{1,5}|[α-ωϛϙ]{1,4}|[а-яѝ]{1,3}"
_LEGACY_POINT = re.compile(
    rf"^(\(\s*(?:[0-9]{{1,4}}[a-z]?|{_LEGACY_LETTERS})\s*\)|(?:{_LEGACY_LETTERS})\))\s+(.+)$", re.DOTALL
)
_LEGACY_INDENT = re.compile(r"^([—–\-])\s+(.+)$", re.DOTALL)


def _legacy_lines(root: _Node) -> list[str]:
    body = next((d for d in _descendants(root) if d.tag == "body"), root)
    return [line for line in _lines(body) if line]


def _next_letter(number: str) -> str | None:
    """``"b"`` -> ``"c"``; ``None`` for anything but a single letter a-y."""
    return chr(ord(number) + 1) if len(number) == 1 and "a" <= number < "z" else None


def _next_roman(number: str) -> str | None:
    n = roman_to_int(number)
    return int_to_roman(n + 1) if n else None


def _legacy_parent(
    b: _DocBuilder, point: Provision | None, num: str, container: Provision, upcoming: str | None
) -> Provision:
    """Where a legacy ``(x)`` point goes, deciding letters vs. roman sub-points by sequence.

    ``point`` is the last point/sub-point added in this container; ``upcoming``
    the number of the next point-like line (look-ahead).
    """
    if point is None or roman_to_int(num) is None:
        return container
    if point.kind == "subpoint":
        top = b.doc.get(point.parent_id) if point.parent_id else None
        if top is None:
            return container
        if num == _next_roman(point.number):
            return top  # (ii) after (i): next sub-point
        if num == _next_letter(top.number):
            return container  # (c) after (b)(ii): next point
        return top
    if num == _next_letter(point.number):
        # (i) after (h) is point (i) -- unless (ii) follows, then it opens h's sub-points
        return point if num == "i" and upcoming == "ii" else container
    return point


def _parse_legacy(b: _DocBuilder, root: _Node, recitals: bool, annexes: bool) -> None:
    article = paragraph = point = annex = None
    seen_article = final = expect_heading = False
    lines = _legacy_lines(root)

    def upcoming(i: int) -> str | None:
        """Number of the next point-like line after line ``i`` (same article)."""
        for line in lines[i + 1:]:
            m = _LEGACY_POINT.match(line)
            if m:
                return normalize_number(_latin_label(m.group(1)))
            if _article_line(line) or _paragraph(line, parens=b.lang == "de"):
                return None
        return None

    for i, line in enumerate(lines):
        if _ANNEX_LINE.match(line) and (seen_article or annex):
            article = paragraph = point = None
            annex = b.add("annex", line, _annex_number(line), None) if annexes else None
            expect_heading, final = True, True
            continue
        if annex is not None:
            if expect_heading and line.isupper() and not line.startswith(">"):  # not ">TABLE>"
                annex.heading, expect_heading = line, False
            else:
                b.append(annex, line)
                expect_heading = False
            continue
        if final:
            continue
        art = _article_line(line)
        if art:
            seen_article = True
            article = b.add("article", art[0], _article_number(art[0]), None, heading=art[1])
            expect_heading = art[1] is None
            paragraph = point = None
            continue
        if not seen_article:
            m = _LEGACY_POINT.match(line)
            if recitals and m and re.fullmatch(r"\(\d+\)", m.group(1)):
                b.add("recital", m.group(1), m.group(1), None, text=m.group(2).strip())
            continue
        if article is None:
            continue
        if _FINAL_LINE.match(line):
            final, article = True, None
            continue
        para = _paragraph(line, parens=b.lang == "de")
        if not para and _is_heading_line(line):  # chapter / section heading
            article = paragraph = point = None
            continue
        heading_ok = expect_heading and len(line) < 200 and not line.endswith((".", ":", ";", ","))
        expect_heading = False
        if heading_ok and not para and not _LEGACY_POINT.match(line):
            article.heading = line
            continue
        if para and b.doc.get(f"{article.id}.par{normalize_number(para[1])}") is None:
            paragraph = b.add("paragraph", para[0], para[1], article, text=para[2])
            point = None
            continue
        container = paragraph or article
        m = _LEGACY_POINT.match(line)
        if m:
            label = m.group(1)
            num = normalize_number(_latin_label(label))
            parent = _legacy_parent(b, point, num, container, upcoming(i) if num == "i" else None)
            kind, number = b.point_kind(parent, label)
            point = b.add(kind, label, number, parent, text=m.group(2).strip())
            continue
        m = _LEGACY_INDENT.match(line)
        if m:
            parent = point or container
            kind, number = b.point_kind(parent, m.group(1))
            b.add(kind, m.group(1), number, parent, text=m.group(2).strip())
            continue
        b.append(container, line)


# --- public parser ----------------------------------------------------------------
def parse_html(
    html: str,
    celex: str,
    language: str,
    recitals: bool = False,
    annexes: bool = True,
) -> Document:
    """Split an act's (X)HTML into a :class:`Document` of provisions.

    Recognises the Publications Office markup (``oj-ti-art`` etc., used since
    about 2014 by CELLAR and EUR-Lex) and falls back to a line-based parser
    for older plain-HTML texts.
    """
    celex, lang = validate_celex(celex), validate_language(language)
    root = _parse_dom(html)
    b = _DocBuilder(celex, lang)
    if any("ti-art" in d.classes for d in _descendants(root)):
        _OjParser(b, recitals, annexes).run(root)
    else:
        _parse_legacy(b, root, recitals, annexes)
        strong = next((d for d in _descendants(root) if d.tag == "strong" and _text(d)), None)
        b.doc.title = _text(strong) if strong else None
    if not any(p.kind == "article" for p in b.doc.provisions):
        raise ValueError(f"no articles found in the {lang.upper()} text of {celex}")
    return b.doc


def counts(doc: Document) -> dict[str, int]:
    """Number of provisions per kind, e.g. ``{"article": 46, "paragraph": 198, ...}``."""
    out: dict[str, int] = {}
    for p in doc.provisions:
        out[p.kind] = out.get(p.kind, 0) + 1
    return out
