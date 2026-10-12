"""Common provision data model shared by the EU and national text adapters.

A legal act is stored as a :class:`Document`: act metadata plus a *flat*,
document-ordered list of :class:`Provision` objects linked by ``parent_id``.
Flat lists are easy to serialise (one JSON object per line) and to diff; the
tree is rebuilt on demand with :meth:`Document.children` / :meth:`Document.walk`.

Provision IDs are stable, path-like strings built by :func:`make_id`::

    eu:32022L2555:cs:art23.par4.pt_a      NIS2, Czech text, Art. 23(4)(a)
    cz:264/2025:par12.odst3.pism_b        Act 264/2025 Sb., § 12 odst. 3 písm. b)

``source:act_id[:language]:segment.segment...`` where each segment is a short
kind prefix plus a normalised number. The ID depends only on the act and the
printed numbering, so it survives re-fetching and re-parsing. Where an official
ELI exists it is kept alongside in :attr:`Provision.eli`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field

# Default ID prefixes for common provision kinds. Kinds not listed here are
# used verbatim as their own prefix (e.g. Czech adapters may pass "odst", "pism").
KIND_PREFIXES: dict[str, str] = {
    "title": "tit",
    "part": "part",
    "chapter": "chap",
    "section": "sec",
    "subsection": "subsec",
    "article": "art",
    "paragraph": "par",
    "subparagraph": "subpar",
    "point": "pt",
    "subpoint": "subpt",
    "letter": "let",
    "indent": "ind",
    "annex": "anx",
    "recital": "rct",
}

_LABEL_WORDS = re.compile(
    r"^(article|art\.?|paragraph|point|chapter|section|annex|recital|"
    r"článek|čl\.?|odstavec|odst\.?|písmeno|písm\.?|bod|příloha)\s+",
    re.IGNORECASE,
)


def normalize_number(number: str | int) -> str:
    """Normalise a printed provision number for use in an ID.

    ``"Article 23"`` -> ``"23"``, ``"§ 4a"`` -> ``"4a"``, ``"(b)"`` -> ``"b"``,
    ``"IV"`` -> ``"iv"`` (roman numerals are kept, not converted), ``"1."`` -> ``"1"``.
    """
    s = str(number).strip()
    s = _LABEL_WORDS.sub("", s)
    s = s.replace("§", "").strip()
    s = s.strip("()[].) ").lower()
    s = re.sub(r"[^\w]+", "_", s).strip("_")
    if not s:
        raise ValueError(f"cannot normalise empty provision number {number!r}")
    return s


_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_ROMAN = re.compile(r"^(?=[ivxlcdm]+$)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")


def roman_to_int(s: str) -> int | None:
    """``"iv"`` / ``"IV"`` -> 4; ``None`` if ``s`` is not a well-formed roman numeral."""
    s = s.strip().lower()
    if not s or not _ROMAN.match(s):
        return None
    total = 0
    for i, ch in enumerate(s):
        v = _ROMAN_VALUES[ch]
        total += -v if i + 1 < len(s) and _ROMAN_VALUES[s[i + 1]] > v else v
    return total


def int_to_roman(n: int) -> str:
    """4 -> ``"iv"`` (lower case, as used in point labels)."""
    out = []
    for v, sym in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                   (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


def make_segment(kind: str, number: str | int) -> str:
    """One ID segment: prefix + number, with ``_`` before alphabetic numbers.

    >>> make_segment("article", "23"), make_segment("point", "(a)")
    ('art23', 'pt_a')
    """
    prefix = KIND_PREFIXES.get(kind, kind)
    num = normalize_number(number)
    return f"{prefix}{num}" if num[0].isdigit() else f"{prefix}_{num}"


def make_id(
    source: str,
    act_id: str,
    path: Sequence[tuple[str, str | int]],
    language: str | None = None,
) -> str:
    """Build a stable provision ID from the act and a (kind, number) path.

    ``make_id("eu", "32022L2555", [("article", 23), ("paragraph", 4), ("point", "a")], "cs")``
    returns ``"eu:32022L2555:cs:art23.par4.pt_a"``.
    """
    head = [source.strip().lower(), act_id.strip()]
    if language:
        head.append(language.strip().lower())
    segments = ".".join(make_segment(k, n) for k, n in path)
    return ":".join(head + [segments]) if segments else ":".join(head)


@dataclass
class Provision:
    id: str
    source: str  # "eu" or ISO 3166-1 alpha-2 country code, lowercase
    act_id: str  # CELEX for EU acts, national reference (e.g. "264/2025") otherwise
    language: str | None
    kind: str  # "article", "paragraph", "point", "section", "odst", ...
    label: str  # as printed: "Article 23", "§ 12", "(a)"
    number: str  # normalised number: "23", "12", "a"
    text: str = ""  # own text only, without children
    heading: str | None = None
    parent_id: str | None = None
    eli: str | None = None
    url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Provision:
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Document:
    source: str
    act_id: str
    language: str | None
    title: str | None = None
    date: str | None = None
    eli: str | None = None
    url: str | None = None
    provisions: list[Provision] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._index: dict[str, int] | None = None

    # --- lookup -----------------------------------------------------------
    def _idx(self) -> dict[str, int]:
        if self._index is None or len(self._index) != len(self.provisions):
            self._index = {p.id: i for i, p in enumerate(self.provisions)}
        return self._index

    def add(self, provision: Provision) -> Provision:
        """Append a provision (document order); rejects duplicate IDs."""
        if provision.id in self._idx():
            raise ValueError(f"duplicate provision id {provision.id!r}")
        self.provisions.append(provision)
        self._index = None
        return provision

    def by_id(self, provision_id: str) -> Provision:
        """Return the provision with this ID (``KeyError`` if missing)."""
        return self.provisions[self._idx()[provision_id]]

    def get(self, provision_id: str) -> Provision | None:
        i = self._idx().get(provision_id)
        return None if i is None else self.provisions[i]

    def __len__(self) -> int:
        return len(self.provisions)

    def __iter__(self) -> Iterator[Provision]:
        return iter(self.provisions)

    # --- tree -------------------------------------------------------------
    def roots(self) -> list[Provision]:
        """Top-level provisions (no parent), in document order."""
        return [p for p in self.provisions if p.parent_id is None]

    def children(self, provision_id: str | None) -> list[Provision]:
        """Direct children of a provision (``None`` gives the roots)."""
        return [p for p in self.provisions if p.parent_id == provision_id]

    def walk(self, provision_id: str | None = None) -> Iterator[tuple[int, Provision]]:
        """Depth-first ``(depth, provision)`` pairs.

        From the roots when ``provision_id`` is None, otherwise the provision
        itself (depth 0) and its descendants.
        """
        kids: dict[str | None, list[Provision]] = {}
        for p in self.provisions:
            kids.setdefault(p.parent_id, []).append(p)

        def rec(p: Provision, depth: int) -> Iterator[tuple[int, Provision]]:
            yield depth, p
            for c in kids.get(p.id, []):
                yield from rec(c, depth + 1)

        starts = kids.get(None, []) if provision_id is None else [self.by_id(provision_id)]
        for s in starts:
            yield from rec(s, 0)

    def full_text(self, provision_id: str, sep: str = "\n") -> str:
        """Text of a provision plus all its descendants, labels included."""
        parts = []
        for _, p in self.walk(provision_id):
            line = " ".join(x for x in (p.label, p.heading, p.text) if x)
            if line:
                parts.append(line)
        return sep.join(parts)

    # --- serialisation ----------------------------------------------------
    def meta(self) -> dict:
        return {
            "source": self.source,
            "act_id": self.act_id,
            "language": self.language,
            "title": self.title,
            "date": self.date,
            "eli": self.eli,
            "url": self.url,
        }

    def to_dict(self) -> dict:
        return {**self.meta(), "provisions": [p.to_dict() for p in self.provisions]}

    @classmethod
    def from_dict(cls, d: dict) -> Document:
        doc = cls(**{k: d.get(k) for k in ("source", "act_id", "language", "title", "date", "eli", "url")})
        for p in d.get("provisions", []):
            doc.add(Provision.from_dict(p))
        return doc

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, s: str) -> Document:
        return cls.from_dict(json.loads(s))

    def to_jsonl(self) -> str:
        """One provision per line; each line is self-contained (has act metadata via its fields)."""
        return "".join(json.dumps(p.to_dict(), ensure_ascii=False) + "\n" for p in self.provisions)


def provisions_from_jsonl(lines: Iterable[str]) -> list[Provision]:
    """Parse JSON-lines output of :meth:`Document.to_jsonl` (blank lines ignored)."""
    return [Provision.from_dict(json.loads(line)) for line in lines if line.strip()]
