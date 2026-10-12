"""Evaluation set from Czech correlation tables (*rozdílové tabulky*).

A Czech government bill that transposes EU law carries an official table that pairs
each provision of the bill (``§ 12 odst. 3 písm. b)``) with the EU provisions it
implements (``Čl. 21 odst. 2 písm. b)``). This module turns such a table into
*gold links* between OpenAcquis provision IDs, so automatic matching can be scored:

    eu:32022L2555:cs:art21.par2.pt_b   <->   cz:264/2025:par12.odst3.pism_b

Pipeline (see ``scripts/build_evalset.py``):

1. Read the table. psp.cz publishes it as Word 97 ``.doc`` (read here with the
   stdlib: an OLE compound file, the text piece table and the cell marks) and
   sometimes ``.docx`` (zip + XML). Both give rows of cells.
2. :func:`table_rows` keeps the data rows (bill ref, bill text, CELEX, EU ref, EU text);
   an empty bill cell continues the previous bill provision (vertically merged cell). It also
   reads the four-column template with the CELEX in the header, CELEX cells that list several
   acts, consolidated acts (``32013L0034 ve znění 32022L2464``) or typos (:func:`fix_celex`),
   and section headers that switch to another national act. Bill references with a part
   (``ČÁST TŘETÍ bod 1.``) go through :func:`normalize_cz_ref`.
3. :func:`parse_eu_ref` / :func:`parse_cz_ref` turn a printed reference into local
   provision IDs (``"čl. 23 odst. 4 písm. a)"`` -> ``["art23.par4.pt_a"]``); lists and
   ranges (``písm. a) až c)``, ``odst. 1 a 2``) expand to several IDs, and sentence or
   subparagraph qualifiers (``věta první``) are kept as a note.
4. :func:`links_from_rows` builds one :class:`GoldLink` per (EU ID, Czech ID) pair.
5. :func:`check_links` compares the IDs with parsed documents. EU IDs are tried literally
   and with known fallbacks (the table writes definitions as ``Čl. 6 odst. 5`` where the
   directive has points, ``art6.pt5``). The table quotes the *bill*, whose numbering can
   differ from the act finally published, so each Czech ID is checked by comparing the
   bill text quoted in the table with the act's text at that ID; if it does not match,
   the best-matching provision of the same kind is proposed instead and the link is
   flagged ``renumbered``. Points of parts that amend other acts are matched by text against
   the act's amending parts, whose numbering often differs from the bill's.

:func:`find_table_files` / :func:`published_act` locate a bill's table and final act number
on psp.cz, so a set can be built from a directive and a print number alone.

Gold links are what the government's table *asserts*; they are evidence for evaluation,
not findings about correct or incorrect transposition.
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import re
import struct
import time
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .cellar import USER_AGENT
from .provisions import Document, Provision, int_to_roman, make_segment, roman_to_int

#: Status of a pair taken from a table that has no compatibility column (the 2024
#: template of the rozdílová tabulka lists pairs only).
STATUS_LISTED = "listed"
#: Older tables state a compatibility per row; normalised values.
COMPATIBILITY = {
    "plně slučitelné": "fully_compatible",
    "částečně slučitelné": "partially_compatible",
    "neslučitelné": "incompatible",
    "neaplikovatelné": "not_applicable",
}

CELEX_RE = re.compile(r"^\s*([0-9CE]\d{4}[A-Z]{1,2}\d{4})\s*$")
#: A CELEX column cell: starts with something CELEX-like. Cells can list several acts
#: (``32019L1937 32019L1937``), consolidated acts (``32013L0034 ve znění 32022L2464``) and typos.
CELEX_CELL_RE = re.compile(r"^\s*[0-9CE]\d{3,4}[A-Z]{1,2}\d")
#: Flags a link can carry (``GoldLink.flags``).
FLAGS = {
    "amendment_point": "the national ref is a point (bod) of a part that amends another act (novelizační bod)",
    "part_ref": "the national ref names a whole part (ČÁST) of the bill, mapped to the § it holds",
    "consolidated": "the EU ref is numbered in a base act as amended (eu_act), not in the target directive",
    "celex_typo": "the table's CELEX cell was corrected (typo, truncation, consolidated-text prefix 0)",
    "section_act": "the national ref belongs to another act named in a section header of the table",
}
EXCERPT = 500  # characters of table text kept per link as evidence


# --- gold link ------------------------------------------------------------------


@dataclass
class GoldLink:
    """One EU provision <-> national provision pair asserted by an official table.

    ``eu_id`` / ``national_id`` are full OpenAcquis provision IDs (the national one in
    the numbering of the act as published, after :func:`check_links`). ``status`` is the
    compatibility given by the table, or ``"listed"`` when the table gives none.
    ``raw_*_ref`` are the references exactly as printed; ``eu_text`` / ``national_text``
    are excerpts of the table's text cells (the bill text, not the final act).
    ``eu_check`` / ``national_check`` say how the ID was verified against parsed texts.
    """

    eu_id: str
    national_id: str
    status: str = STATUS_LISTED
    raw_eu_ref: str = ""
    raw_national_ref: str = ""
    source_url: str = ""
    note: str = ""
    celex: str = ""
    row: int | None = None  # data row in the table (0-based among all CELEX rows, header excluded)
    bill_national_id: str = ""  # ID in the bill's numbering, as the table printed it
    eu_check: str = "not_checked"  # exact | fallback | unresolved | not_checked
    eu_similarity: float | None = None
    national_check: str = "not_checked"  # same_number | renumbered | ancestor | unverified | unresolved | not_checked
    national_similarity: float | None = None
    eu_text: str = ""
    national_text: str = ""
    # Written only when set (so older files and their bytes stay as they were):
    eu_act: str = ""  # CELEX whose numbering the EU ref follows, when not ``celex`` (consolidated base act)
    flags: list[str] = field(default_factory=list)  # see FLAGS

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("eu_act", "flags"):
            if not d[k]:
                del d[k]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> GoldLink:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def save_jsonl(links: Iterable[GoldLink], path: str | Path) -> int:
    """Write one link per line (UTF-8); returns the number written."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for link in links:
            f.write(json.dumps(link.to_dict(), ensure_ascii=False) + "\n")
            n += 1
    return n


def load_jsonl(path: str | Path) -> list[GoldLink]:
    with open(path, encoding="utf-8") as f:
        return [GoldLink.from_dict(json.loads(line)) for line in f if line.strip()]


# --- reference parsing ------------------------------------------------------------

_ORDINALS = (
    "první|druhá|druhý|druhé|třetí|čtvrtá|čtvrtý|pátá|pátý|šestá|šestý|sedmá|sedmý|"
    "poslední|předposlední|úvodní"
)
_TOKEN = re.compile(
    rf"""
    (?P<kw>§|(?:čl|odst|písm|příl|pododst)\.
          |(?:článek|článku|odstavec|odstavce|písmeno|písmena|bod|bodu|body|příloha|přílohy
             |věta|věty|vět|pododstavec|pododstavce|část|části|odrážka|odrážky|odrážce
             |oddíl|oddílu|návětí|úvodní\s+část)(?![^\W\d_]))  # whole words: "bodu" is not "bod" + "u"
    |(?P<ord>(?:{_ORDINALS})\b)
    |(?P<conj>(?:až|a|nebo)(?![\w)])|[,;])
    |(?P<val>\(?(?:\d+[a-z]?|[a-z]{{1,4}})(?:\)|\.)?(?![^\W\d_]))  # "návětí" is not the letter "n"
    |(?P<other>\S+)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_KEYWORDS = {
    "§": "par", "čl.": "art", "článek": "art", "článku": "art",
    "odst.": "odst", "odstavec": "odst", "odstavce": "odst",
    "písm.": "pism", "písmeno": "pism", "písmena": "pism",
    "bod": "bod", "bodu": "bod", "body": "bod",
    "příloha": "annex", "přílohy": "annex", "příl.": "annex",
    "část": "cast", "části": "cast",
    "odrážka": "ind", "odrážky": "ind", "odrážce": "ind",
}
_QUALIFIERS = {"věta", "věty", "vět", "pododstavec", "pododstavce", "pododst.", "návětí", "úvodní část"}
#: Qualifiers followed by a number: "oddíl VII" of an annex (annexes are not split into sections).
_NUMBERED_QUALIFIERS = {"oddíl", "oddílu"}
_ORDINAL_NUMBER = {w: i for i, w in enumerate(
    ["první", "druhá|druhý|druhé", "třetí", "čtvrtá|čtvrtý", "pátá|pátý", "šestá|šestý", "sedmá|sedmý"], 1)
    for w in w.split("|")}
# "bod" under a písmeno is level 4 (EU sub-point i), Czech bod 5); a "bod" with no písmeno above it is a
# numbered point ("čl. 6 bod 5", "§ 2 bod 5") at the písmeno level ("tbod"), and a písmeno below such a
# point is one level deeper ("spism": "čl. 6 bod 5 písm. a)").
_LEVEL = {"cast": 0, "art": 1, "par": 1, "annex": 1, "odst": 2, "oddil": 2, "pism": 3, "tbod": 3, "bod": 4,
          "spism": 4, "ind": 5}


def _contextual_kind(kind: str, branch: list[tuple[str, str]]) -> str:
    """The level kind of ``bod`` / ``písm.`` given the open branch (see :data:`_LEVEL`)."""
    kinds = [k for k, _ in branch]
    if kind == "bod":
        if "pism" in kinds:
            return "bod"
        return "tbod"
    if kind == "pism" and "tbod" in kinds:
        # a further letter of the same point, unless a higher level ("odst.") closed the point meanwhile
        return "spism"
    return kind


@dataclass
class ParsedRef:
    """Result of parsing a printed reference."""

    ids: list[str]  # local IDs (without ``source:act[:lang]:``), in printed order
    qualifier: str = ""  # sentence / subparagraph words that IDs cannot express
    unparsed: str = ""  # tokens that were not understood (empty = clean parse)

    @property
    def ok(self) -> bool:
        return bool(self.ids) and not self.unparsed


def _clean_ref(s: str) -> str:
    s = s.replace("\xa0", " ").replace("‑", "-")
    return re.sub(r"\s+", " ", s).strip(" \t\r\n;:")


def _value(tok: str) -> str:
    return tok.strip("().").lower()


_ROMAN_CHARS = set("ivxlcdm")


def _expand(a: str, b: str, kind: str = "") -> list[str]:
    """``až`` ranges: ``1..4``, ``a..d``, ``ii..iv``; anything else -> both ends.

    Single-character ends that are both roman numerals are ambiguous ("i až v"). Under
    ``písm.`` they are letters; elsewhere (``bod i) až v)``) they are roman numerals, and so
    are any two roman ends whose letter range would cross non-roman letters.
    """
    if a.isdigit() and b.isdigit() and int(a) <= int(b) <= int(a) + 200:
        return [str(i) for i in range(int(a), int(b) + 1)]
    letters = (
        [chr(c) for c in range(ord(a), ord(b) + 1)]
        if len(a) == len(b) == 1 and a.isalpha() and b.isalpha() and a <= b else None
    )
    ra, rb = roman_to_int(a), roman_to_int(b)
    if ra and rb and ra <= rb <= ra + 50:
        single = len(a) + len(b) == 2
        letter_kind = kind in ("pism", "spism")
        if not single or (not letter_kind and (kind in ("bod", "tbod") or not set(letters or []) <= _ROMAN_CHARS)):
            return [int_to_roman(i) for i in range(ra, rb + 1)]
    if letters is not None:
        return letters
    return [a, b]


def _parse(ref: str, segment: Callable[[list[tuple[str, str]]], str], sections: bool = False) -> ParsedRef:
    """Parse ``ref``; with ``sections``, ``oddíl`` is a level of the ID (Czech annexes are split into
    sections, ``priloha1.oddil1``); otherwise it is kept as a qualifier (EU annexes are parsed whole)."""
    s = _clean_ref(ref)
    toks = [(m.lastgroup, m.group()) for m in _TOKEN.finditer(s)]
    done: list[list[tuple[str, str]]] = []
    branches: list[list[tuple[str, str]]] = [[]]
    qualifier: list[str] = []
    unparsed: list[str] = []
    i = 0
    while i < len(toks):
        kind, tok = toks[i]
        low = re.sub(r"\s+", " ", tok.lower())
        if kind == "kw" and low in _QUALIFIERS:
            qualifier.append(low)
            i += 1
            continue
        if kind == "kw" and low in _NUMBERED_QUALIFIERS and sections and branches[-1]:
            i += 1
            if i < len(toks) and (toks[i][0] == "val" or re.fullmatch(r"[IVXLC]+", toks[i][1])):
                branch_val = _value(toks[i][1])
                i += 1
                last = branches[-1][-1]
                if _LEVEL[last[0]] >= _LEVEL["oddil"]:
                    done.extend(branches)
                    branches = [[seg for seg in b if _LEVEL[seg[0]] < _LEVEL["oddil"]] for b in branches[-1:]]
                branches = [b + [("oddil", branch_val)] for b in branches]
            else:
                unparsed.append(tok)
            continue
        if kind == "kw" and low in _NUMBERED_QUALIFIERS:
            i += 1
            if i < len(toks) and (toks[i][0] == "val" or re.fullmatch(r"[IVXLC]+", toks[i][1])):
                low = f"{low} {toks[i][1]}"
                i += 1
            qualifier.append(low)
            continue
        if kind == "kw" and _KEYWORDS[low] == "ind":
            # "odrážka čtvrtá" (an indent, numbered by an ordinal word after the keyword)
            if i + 1 < len(toks) and toks[i + 1][1].lower() in _ORDINAL_NUMBER and branches[-1]:
                level = _LEVEL["ind"]
                branches = [b + [("ind", str(_ORDINAL_NUMBER[toks[i + 1][1].lower()]))] for b in branches]
                i += 2
                continue
            unparsed.append(tok)
            i += 1
            continue
        if kind == "ord":
            qualifier.append(low)
            i += 1
            continue
        if kind == "kw":
            level_kind = _contextual_kind(_KEYWORDS[low], branches[-1])
            i += 1
            if level_kind == "annex" and i < len(toks) and toks[i][1].lower() == "č.":
                i += 1
            values: list[str] = []
            while i < len(toks):
                k2, t2 = toks[i]
                if k2 == "val" and level_kind in ("pism", "spism") and values and _value(t2).isdigit() \
                        and not any(v.isdigit() for v in values):
                    # "písm. a) až e) a 2": a number after letters is never a letter; it is the next
                    # item of the nearest numbered level above ("odst. 2")
                    up = next((k for k, _ in reversed(branches[-1]) if k in ("odst", "tbod")), None)
                    if up is not None:
                        toks.insert(i, ("kw", "odst." if up == "odst" else "bod"))
                        break
                if k2 == "val" or (k2 == "other" and level_kind == "annex" and re.fullmatch(r"[IVXLC]+", t2)):
                    values.append(_value(t2))
                    i += 1
                elif k2 == "conj" and t2.lower() == "až" and values and i + 1 < len(toks) and toks[i + 1][0] == "val":
                    values[-1:] = _expand(values[-1], _value(toks[i + 1][1]), level_kind)
                    i += 2
                elif (k2 == "conj" and t2.lower() == "až" and values and i + 2 < len(toks)
                      and toks[i + 1][0] == "kw" and toks[i + 2][0] == "val"
                      and _KEYWORDS.get(re.sub(r"\s+", " ", toks[i + 1][1].lower())) == _KEYWORDS[low]):
                    # "odst. 1 až odst. 3": the level word repeated after "až"
                    values[-1:] = _expand(values[-1], _value(toks[i + 2][1]), level_kind)
                    i += 3
                elif k2 == "conj" and i + 1 < len(toks) and toks[i + 1][0] == "val":
                    i += 1  # "a 2", ", 3": next value of the same list
                else:
                    break
            if not values:
                if i >= 2 and toks[i - 2][0] == "ord":
                    qualifier.append(low)  # "první odstavec": a subparagraph, not a numbered paragraph
                else:
                    unparsed.append(tok)
                continue
            level = _LEVEL[level_kind]
            last = branches[-1][-1] if branches[-1] else None
            if last is not None and _LEVEL[last[0]] >= level:
                # same or higher level again ("... písm. f) a odst. 4 ..."): close the open branches
                done.extend(branches)
                prefixes: list[list[tuple[str, str]]] = []
                for b in branches:
                    pre = [seg for seg in b if _LEVEL[seg[0]] < level]
                    if pre not in prefixes:
                        prefixes.append(pre)
                branches = prefixes
            elif last is not None and len(branches) > 1:
                # deeper level after a list ("odst. 1 a 4 písm. e)"): in Czech citation
                # practice it qualifies only the last item, the others stand alone
                done.extend(b for b in branches if b[-1] != last)
                branches = [b for b in branches if b[-1] == last]
            branches = [b + [(level_kind, v)] for b in branches for v in values]
            continue
        if kind == "conj":
            i += 1
            continue
        unparsed.append(tok)
        i += 1
    done.extend(b for b in branches if b)
    ids = []
    for b in done:
        try:
            sid = segment(b)
        except ValueError:
            unparsed.append(" ".join(v for _, v in b))
            continue
        if sid not in ids:
            ids.append(sid)
    return ParsedRef(ids, " ".join(qualifier), " ".join(unparsed))


def _eu_segment(path: list[tuple[str, str]]) -> str:
    out = []
    kinds = [k for k, _ in path]
    for k, v in path:
        if k == "art":
            out.append(make_segment("article", v))
        elif k == "annex":
            out.append(make_segment("annex", v))
        elif k == "odst":
            out.append(make_segment("paragraph", v))
        elif k in ("pism", "tbod"):
            # a lettered point, or a numbered point directly under an article / paragraph ("čl. 6 bod 5")
            out.append(make_segment("point", v))
        elif k in ("bod", "spism"):
            # a sub-point: "písm. f) bod ii)", or a letter under a numbered point ("čl. 6 bod 5 písm. a)")
            out.append(make_segment("subpoint", v))
        elif k == "ind":
            out.append(make_segment("indent", v))
        else:
            raise ValueError(f"{k} not valid in an EU reference")
    if not out or kinds[0] not in ("art", "annex"):
        raise ValueError("EU reference must start with an article or annex")
    return ".".join(out)


def _cz_segment(path: list[tuple[str, str]]) -> str:
    kinds = [k for k, _ in path]
    if not kinds or kinds[0] not in ("par", "art", "annex", "cast"):
        raise ValueError("Czech reference must start with §, čl., příloha or část")
    if "ind" in kinds:
        raise ValueError("indents (odrážky) are not numbered in Czech law IDs")
    out = []
    for k, v in path:
        prefix = {"par": "par", "art": "cl", "annex": "priloha", "cast": "cast", "tbod": "bod",
                  "spism": "pism"}.get(k, k)
        if k in ("annex", "art", "oddil") and roman_to_int(v):
            v = str(roman_to_int(v))  # e-Sbírka numbers články and přílohy in arabic (cl2 for Čl. II)
        out.append(make_segment(prefix, v))
    return ".".join(out)


def parse_eu_ref(ref: str) -> ParsedRef:
    """Czech-language reference to an EU act -> local EU provision IDs.

    >>> parse_eu_ref("čl. 23 odst. 4 písm. a)").ids
    ['art23.par4.pt_a']
    >>> parse_eu_ref("Čl. 32 odst. 2 písm. e) a f) a odst. 4 písm. b)").ids
    ['art32.par2.pt_e', 'art32.par2.pt_f', 'art32.par4.pt_b']
    """
    return _parse(ref, _eu_segment)


def parse_cz_ref(ref: str) -> ParsedRef:
    """Czech law reference -> local IDs as built by :mod:`openacquis.adapters.cz`.

    >>> parse_cz_ref("§ 12 odst. 3 písm. b)").ids
    ['par12.odst3.pism_b']
    """
    return _parse(ref, _cz_segment, sections=True)


def eu_ref_to_ids(ref: str) -> list[str]:
    return parse_eu_ref(ref).ids


def cz_ref_to_ids(ref: str) -> list[str]:
    return parse_cz_ref(ref).ids


# --- reading Word files (stdlib) -----------------------------------------------------

_CFB_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_END = 0xFFFFFFFA  # sector numbers >= this end a chain


def cfb_streams(data: bytes) -> dict[str, bytes]:
    """Streams of an OLE compound file (the container of ``.doc``), by name.

    Only regular-sector streams are returned (those >= the mini-stream cutoff,
    usually 4096 bytes), which covers ``WordDocument`` and the table stream.
    """
    if data[:8] != _CFB_MAGIC:
        raise ValueError("not an OLE compound file (.doc)")
    ssz = 1 << struct.unpack_from("<H", data, 30)[0]
    nfat, dir_start = struct.unpack_from("<I", data, 44)[0], struct.unpack_from("<I", data, 48)[0]
    cutoff = struct.unpack_from("<I", data, 56)[0]
    difat_start, ndifat = struct.unpack_from("<II", data, 68)
    difat = list(struct.unpack_from("<109I", data, 76))
    per = ssz // 4
    sec = difat_start
    while sec < _END and ndifat > 0:
        vals = struct.unpack_from(f"<{per}I", data, (sec + 1) * ssz)
        difat.extend(vals[:-1])
        sec, ndifat = vals[-1], ndifat - 1
    fat: list[int] = []
    for s in difat[:nfat]:
        fat.extend(struct.unpack_from(f"<{per}I", data, (s + 1) * ssz))

    def chain(s: int) -> bytes:
        out = bytearray()
        seen = 0
        while s < _END:
            out += data[(s + 1) * ssz:(s + 2) * ssz]
            s = fat[s]
            seen += 1
            if seen > len(fat):
                raise ValueError("corrupt compound file (sector loop)")
        return bytes(out)

    directory = chain(dir_start)
    streams: dict[str, bytes] = {}
    for off in range(0, len(directory) - 127, 128):
        e = directory[off:off + 128]
        name_len = struct.unpack_from("<H", e, 64)[0]
        if e[66] != 2 or name_len < 2:  # 2 = stream
            continue
        name = e[:name_len - 2].decode("utf-16-le", "replace")
        start, size = struct.unpack_from("<IQ", e, 116)
        if size >= cutoff:
            streams[name] = chain(start)[:size]
    return streams


_FIELD = re.compile(r"\x13[^\x13\x14\x15]*\x14([^\x13\x14\x15]*)\x15|\x13[^\x13\x14\x15]*\x15")
_CONTROL = re.compile(r"[\x00-\x06\x08\x0e-\x12\x16-\x1d]")


def doc_text(data: bytes) -> str:
    """Text of a Word 97-2003 ``.doc`` (from its piece table), with Word's marks kept:
    ``\\x07`` ends a table cell (and a row), ``\\r`` a paragraph; ``\\x0b`` (line break)
    becomes ``\\n``. Fields keep their displayed result."""
    st = cfb_streams(data)
    wd = st.get("WordDocument")
    if wd is None:
        raise ValueError("no WordDocument stream: not a Word .doc file")
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    if flags & 0x0100:
        raise ValueError("encrypted .doc files are not supported")
    table = st.get("1Table" if flags & 0x0200 else "0Table")
    if table is None:
        raise ValueError("table stream missing")
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, 0x01A2)
    clx = table[fc_clx:fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 1:  # Prc (formatting) entries precede the piece table
        i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
    if i >= len(clx) or clx[i] != 2:
        raise ValueError("piece table not found")
    size = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + size]
    n = (size - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    parts = []
    for j in range(n):
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * j + 2)[0]
        length = cps[j + 1] - cps[j]
        if fc & 0x40000000:  # 8-bit text
            off = (fc & ~0x40000000) // 2
            parts.append(wd[off:off + length].decode("cp1252", "replace"))
        else:
            parts.append(wd[fc:fc + 2 * length].decode("utf-16-le", "replace"))
    text = "".join(parts)
    text = _FIELD.sub(lambda m: m.group(1) or "", text)
    text = text.replace("\x0b", "\n").replace("\x1e", "-").replace("\x1f", "").replace("\x0c", "\r")
    return _CONTROL.sub("", text)


def doc_rows(text: str, width: int = 5, key_col: int = 2, key: re.Pattern = CELEX_CELL_RE) -> list[list[str]]:
    """Group the cells of :func:`doc_text` output into table rows.

    In ``.doc`` text every cell ends with ``\\x07`` and every row with one more
    ``\\x07``, so an empty cell and a row end look alike. A row of ``width`` cells is
    recognised when the token after it is empty and its ``key_col`` cell is empty or
    matches ``key`` (the CELEX column); anything else (headers, merged cells, other
    tables) is read up to the next empty token. Text before a table is dropped from
    its first cell (it follows the last paragraph mark).
    """
    toks = text.split("\x07")
    rows: list[list[str]] = []
    i = 0
    while i < len(toks) - 1:
        cand = toks[i:i + width + 1]
        if (
            len(cand) == width + 1
            and cand[width] == ""
            and (not cand[key_col].strip() or key.match(cand[key_col]))
        ):
            rows.append(cand[:width])
            i += width + 1
            continue
        j = i
        while j < len(toks) and toks[j] != "":
            j += 1
        rows.append(toks[i:j])
        i = j + 1
    for r in rows:
        if r and "\r" in r[0] and not r[0].endswith("\r"):
            # first cell after body text: keep only what follows the last paragraph mark
            head, _, tail = r[0].rpartition("\r")
            if head.strip():
                r[0] = tail
    return [[c.replace("\r", "\n") for c in r] for r in rows]


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_rows(data: bytes) -> list[list[str]]:
    """All table rows of a ``.docx`` (cells as text, paragraphs joined by ``\\n``).

    A cell continuing a vertical merge is returned empty, as in ``.doc`` text.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    rows = []
    for tr in root.iter(_W + "tr"):
        cells = []
        for tc in tr.findall(_W + "tc"):
            vm = tc.find(f"{_W}tcPr/{_W}vMerge")
            if vm is not None and vm.get(_W + "val") != "restart":
                cells.append("")
                continue
            paras = []
            for p in tc.iter(_W + "p"):
                buf = []
                for el in p.iter():
                    if el.tag == _W + "t":
                        buf.append(el.text or "")
                    elif el.tag == _W + "tab":
                        buf.append("\t")
                    elif el.tag in (_W + "br", _W + "cr"):
                        buf.append("\n")
                paras.append("".join(buf))
            cells.append("\n".join(paras))
        rows.append(cells)
    return rows


def read_table(data: bytes) -> list[list[str]]:
    """Rows of a correlation table from ``.doc`` or ``.docx`` bytes."""
    if data[:8] == _CFB_MAGIC:
        return doc_rows(doc_text(data))
    if data[:2] == b"PK":
        return docx_rows(data)
    raise ValueError("unsupported file: expected Word .doc or .docx")


# --- rows -> links -----------------------------------------------------------------------


@dataclass
class TableRow:
    """One data row of a correlation table, with the bill cells carried down.

    ``celex`` is the EU act the row was selected for. ``eu_act`` is set when the EU
    reference is numbered in another act: the base act of a consolidated reference
    (``32013L0034 ve znění 32022L2464``). ``national_act`` is set when a section header
    of the table names the national act the rows belong to (tables covering several acts).
    """

    index: int
    national_ref: str
    national_text: str
    celex: str
    eu_ref: str
    eu_text: str
    compatibility: str | None = None
    eu_act: str = ""
    national_act: str = ""
    section: str = ""
    notes: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _cell(s: str) -> str:
    s = s.replace("\xa0", " ")
    lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n"))
    return "\n".join(ln for ln in lines if ln)


# --- CELEX cells ----------------------------------------------------------------------------

_CELEX_WELL_FORMED = re.compile(r"^([0-9CE])(\d{4})([A-Z]{1,2})(\d{4})$")
_CELEX_CELL_TOKEN = re.compile(r"ve\s+znění|[0-9CE][0-9A-Z]{4,19}", re.IGNORECASE)


def _plausible_celex(tok: str) -> bool:
    m = _CELEX_WELL_FORMED.match(tok)
    return bool(m) and 1950 <= int(m[2]) <= 2099 and m[1] in "03CE"


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def fix_celex(tok: str, known: Iterable[str] = ()) -> tuple[str | None, str]:
    """``(CELEX, note)`` for a token of a table's CELEX cell; ``note`` is empty when it was fine.

    A well-formed CELEX is kept (the consolidated-text prefix ``0`` becomes ``3``:
    ``02006L0043`` -> ``32006L0043``). A malformed one (``23022L2464``, ``32022L246``,
    ``32013L32022L2464``) becomes a ``known`` CELEX (the acts listed elsewhere in the table)
    it contains, or the unique closest one within two edits; otherwise ``None``.
    Well-formed CELEX numbers are never "corrected": ``32022L2555`` (NIS2) and ``32022L2557``
    (CER) differ by one character.
    """
    tok = tok.upper()
    if _plausible_celex(tok):
        if tok[0] == "0":
            return "3" + tok[1:], f"consolidated-text CELEX {tok} read as 3{tok[1:]}"
        return tok, ""
    cands = sorted({k for k in known if _plausible_celex(k)})
    inside = [k for k in cands if k in tok]
    if len(inside) == 1:
        return inside[0], f"CELEX cell {tok!r} read as {inside[0]}"
    scored = sorted((_edit_distance(tok, k), k) for k in cands)
    if scored and scored[0][0] <= 2 and (len(scored) == 1 or scored[1][0] > scored[0][0]):
        return scored[0][1], f"CELEX typo {tok!r} read as {scored[0][1]}"
    return None, f"CELEX cell token {tok!r} not understood"


def celex_chains(cell: str, known: Iterable[str] = ()) -> tuple[list[list[str]], list[str]]:
    """Acts cited by a CELEX cell, as chains ``[base, amended by, ...]``, and correction notes.

    ``"32019L1937 32019L1937"`` -> ``[["32019L1937"]]`` (repeats, one per EU ref line, merged);
    ``"32013L0034 ve znění 32021L2101 ve znění 32023L2775"`` ->
    ``[["32013L0034", "32021L2101", "32023L2775"]]``.
    """
    known = list(known)
    chains: list[list[str]] = []
    notes: list[str] = []
    amend = False
    for m in _CELEX_CELL_TOKEN.finditer(cell or ""):
        tok = m.group()
        if tok.lower().startswith("ve"):
            amend = True
            continue
        c, note = fix_celex(tok, known)
        if note and note not in notes:
            notes.append(note)
        if c is None:
            amend = False
            continue
        if amend and chains:
            if c not in chains[-1]:
                chains[-1].append(c)
        else:
            chains.append([c])
        amend = False
    out: list[list[str]] = []
    for ch in chains:
        if ch not in out:
            out.append(ch)
    return out, notes


def _known_celex(rows: Sequence[Sequence[str]]) -> set[str]:
    """Well-formed CELEX numbers anywhere in a table's CELEX column, header or closing list."""
    known: set[str] = set()
    for r in rows:
        cells = [r[2]] if len(r) >= 5 else list(r[:1]) if len(r) <= 2 else list(r)
        for c in cells:
            for tok in _CELEX_CELL_TOKEN.findall(c or ""):
                t = tok.upper()
                if _plausible_celex(t):
                    known.add("3" + t[1:] if t[0] == "0" else t)
    return known


# --- table layout -------------------------------------------------------------------------------

#: Header cell of the template that names the EU act once, for the whole table (CER, 2025).
_HEADER_CELEX = re.compile(r"CELEX\s*(?:č\.)?\s*:?\s*([0-9CE]\d{4}[A-Z]{1,2}\d{4})", re.IGNORECASE)
#: Section header row: "<national act> | Odpovídající předpis EU".
_SECTION = re.compile(r"Odpovídající\s+předpis\s+EU", re.IGNORECASE)
_SECTION_ACT = re.compile(r"(?:zákona?|vyhlášky|vyhláška|nařízení vlády)\s+č\.\s*(\d+/\d{4})", re.IGNORECASE)
_HEADER_CELL = re.compile(r"^\s*(?:Ustanovení|Obsah|Celex)", re.IGNORECASE)


def _section_label(cell: str) -> str:
    s = re.sub(r"\s+", " ", (cell or "").replace("\xa0", " ")).strip()
    s = re.sub(r"^Navrhovaný právní předpis\s*(?:\(resp\.\s*jiný\s*právní\s*předpis\))?\s*", "", s, flags=re.I)
    return s.strip()


def _section_act(label: str, section_acts: Mapping[str, str] | None) -> str:
    for needle, act in (section_acts or {}).items():
        if needle.lower() in label.lower():
            return act
    m = _SECTION_ACT.search(label)
    return m.group(1) if m else ""


def table_rows(
    rows: Sequence[Sequence[str]],
    celex: str | None = None,
    section_acts: Mapping[str, str] | None = None,
) -> list[TableRow]:
    """Data rows of a rozdílová tabulka.

    Two templates are read:

    * five columns ``[bill ref, bill text, CELEX, EU ref, EU text, (compatibility)]``: rows
      whose third cell starts with a CELEX are data rows (titles, headers and the closing
      list of EU acts are skipped). The CELEX cell may list the act several times, a
      consolidated act (``32013L0034 ve znění 32022L2464``: the EU ref is numbered in
      2013/34 as amended, ``eu_act``) or contain typos (:func:`fix_celex`);
    * four columns ``[bill ref, bill text, EU ref, EU text]`` with the CELEX given once in
      the header (``CELEX 32022L2557``).

    An empty bill reference continues the previous one. A two-cell row
    ``[<act>, "Odpovídající předpis EU"]`` starts a section for another national act
    (``Změna zákona č. 416/2023 Sb.``); its number is read from the label, or looked up in
    ``section_acts`` (label substring -> ``"93/2009"``) when the label gives none.
    ``index`` counts all data rows of the table (every EU act), from 0. With ``celex``,
    only rows citing that act are returned.
    """
    known = _known_celex(rows)
    if celex:
        known.add(celex)
    out: list[TableRow] = []
    ref, text = "", ""
    header_celex = ""
    section, section_act = "", ""
    n = 0
    for r in rows:
        if len(r) == 2 and _SECTION.search(r[1] or ""):
            label = _section_label(r[0])
            if label != section:
                section, section_act = label, _section_act(label, section_acts)
                ref, text = "", ""
            continue
        if r and _HEADER_CELL.match(r[0] or ""):
            for c in r:
                m = _HEADER_CELEX.search(c or "")
                if m:
                    header_celex = m.group(1).upper()
            continue
        comp = None
        if len(r) >= 5 and CELEX_CELL_RE.match(r[2] or ""):
            nref, ntext, ccell, eref, etext = r[:5]
            if len(r) > 5 and r[5].strip():
                comp = COMPATIBILITY.get(_clean_ref(r[5]).lower(), _clean_ref(r[5]))
        elif len(r) == 4 and header_celex:
            nref, ntext, eref, etext = r
            ccell = header_celex
        else:
            continue
        if _clean_ref(nref):
            ref, text = _clean_ref(nref), _cell(ntext)
        elif _cell(ntext):
            text = _cell(ntext)
        if len(r) == 4 and not _clean_ref(eref):
            continue  # a bill provision with no EU counterpart in this template
        chains, notes = celex_chains(ccell, known)
        index = n
        n += 1
        if not chains:
            continue
        if celex:
            hit = [ch for ch in chains if celex in ch]
            if not hit:
                continue
            chain = hit[0]
            others = sorted({c for ch in chains for c in ch} - set(chain))
            if others:
                notes.append(f"CELEX cell also cites {', '.join(others)}")
            target = celex
        else:
            chain = chains[0]
            target = chain[-1]
        eu_act = chain[0] if chain[0] != target else ""
        flags = []
        if eu_act:
            flags.append("consolidated")
            notes.append(f"EU ref numbered in {eu_act} as amended by {', '.join(chain[1:])}")
        if any(nt.startswith(("CELEX typo", "CELEX cell", "consolidated-text")) for nt in notes):
            flags.append("celex_typo")
        row = TableRow(index, ref, text, target, _clean_ref(eref), _cell(etext), comp,
                       eu_act=eu_act, national_act=section_act, section=section, notes=notes, flags=flags)
        out.append(row)
    return out


# --- national references with parts (ČÁST) ---------------------------------------------------

_PART_WORDS = [
    "první", "druhá", "třetí", "čtvrtá", "pátá", "šestá", "sedmá", "osmá", "devátá", "desátá",
    "jedenáctá", "dvanáctá", "třináctá", "čtrnáctá", "patnáctá", "šestnáctá", "sedmnáctá",
    "osmnáctá", "devatenáctá", "dvacátá",
]
_PART = re.compile(r"^část\s+(?P<p>[^\s]+)\s*(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_ANCHOR_START = re.compile(r"^(?:§|čl\.|článek|článku|příloh)", re.IGNORECASE)


def _part_number(word: str) -> int | None:
    w = word.lower().strip(".,")
    if w in _PART_WORDS:
        return _PART_WORDS.index(w) + 1
    if w.isdigit():
        return int(w)
    return roman_to_int(w)


def normalize_cz_ref(ref: str) -> tuple[str, list[str], str]:
    """``(ref to parse, flags, note)`` for a bill reference that may start with ``ČÁST <n>``.

    Provision IDs are anchored at the § / článek, so ``ČÁST PRVNÍ § 1 odst. 1`` is just
    ``§ 1 odst. 1``. A part that amends another act is cited by its amendment points
    (``ČÁST TŘETÍ bod 1.``, flag ``amendment_point``) or as a whole (``ČÁST DESÁTÁ``, flag
    ``part_ref``); these become ``část 3 bod 1`` -> ``cast3.bod1``, which
    :func:`check_links` maps to the § that the part holds in the act. A heading with the
    § in brackets (``ČÁST X Účinnost (§ 42)``) gives that §.
    """
    s = _clean_ref(ref)
    m = _PART.match(s)
    if not m:
        return s, [], ""
    k = _part_number(m["p"])
    rest = m["rest"].strip()
    if k is None:
        return s, [], ""
    if _ANCHOR_START.match(rest):
        return rest, [], ""
    br = re.search(r"\(\s*(§[^()]*)\)", rest)
    if br:
        return br.group(1).strip(), [], f"CZ ref {s!r} read as {br.group(1).strip()!r}"
    if re.match(r"^(?:bod|body|bodu|bodech)\b", rest, re.IGNORECASE):
        return f"část {k} {rest}", ["amendment_point"], "amendment point of a part amending another act"
    return f"část {k}", ["part_ref"], f"whole part {k} cited" + (f" ({rest})" if rest else "")


def _excerpt(s: str, n: int = EXCERPT) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def links_from_rows(
    rows: Iterable[TableRow],
    act_id: str,
    source_url: str = "",
    eu_language: str = "cs",
    national_source: str = "cz",
) -> list[GoldLink]:
    """One :class:`GoldLink` per (EU ID, Czech ID) pair of each row (bill numbering).

    The EU ID is built on ``row.eu_act`` when the row cites a consolidated act, the
    national ID on ``row.national_act`` when a section header names another act (else
    ``act_id``). Bill references starting with ``ČÁST`` go through :func:`normalize_cz_ref`.
    Rows whose references do not parse still give a link with an empty ID and a note,
    so nothing in the table is silently dropped.
    """
    links: list[GoldLink] = []
    for r in rows:
        if not r.national_ref:
            continue
        cz_ref, cz_flags, cz_note = normalize_cz_ref(r.national_ref)
        eu, cz = parse_eu_ref(r.eu_ref), parse_cz_ref(cz_ref)
        notes = []
        if eu.qualifier:
            notes.append(f"EU ref qualifier: {eu.qualifier}")
        if cz.qualifier:
            notes.append(f"CZ ref qualifier: {cz.qualifier}")
        if eu.unparsed:
            notes.append(f"EU ref not fully parsed: {eu.unparsed!r}")
        if cz.unparsed:
            notes.append(f"CZ ref not fully parsed: {cz.unparsed!r}")
        if cz_note:
            notes.append(cz_note)
        notes.extend(r.notes)
        flags = list(r.flags) + [f for f in cz_flags if f not in r.flags]
        act = act_id
        if r.national_act and r.national_act != act_id:
            act = r.national_act
            flags.append("section_act")
            notes.append(f"table section: {r.section}")
        elif r.section and not r.national_act:
            notes.append(f"table section without an act number: {r.section}")
        eu_act = r.eu_act if r.eu_act and r.eu_act != r.celex else ""
        for e in eu.ids or [""]:
            for c in cz.ids or [""]:
                eu_id = f"eu:{eu_act or r.celex}:{eu_language}:{e}" if e else ""
                cz_id = f"{national_source}:{act}:{c}" if c else ""
                links.append(
                    GoldLink(
                        eu_id=eu_id,
                        national_id=cz_id,
                        status=r.compatibility or STATUS_LISTED,
                        raw_eu_ref=r.eu_ref,
                        raw_national_ref=r.national_ref,
                        source_url=source_url,
                        note="; ".join(notes),
                        celex=r.celex,
                        row=r.index,
                        bill_national_id=cz_id,
                        eu_text=_excerpt(r.eu_text),
                        national_text=_excerpt(r.national_text),
                        eu_act=eu_act,
                        flags=list(flags),
                    )
                )
    return links


# --- checking IDs against parsed texts ------------------------------------------------------

_LABEL_TOKEN = re.compile(r"(?:^|\s)(?:\(\d+[a-z]?\)|\d+[a-z]?[.)]|[a-z]{1,4}\)|§\s*\d+[a-z]?)(?=\s)")
_WORD = re.compile(r"\w+")


def _words(s: str) -> list[str]:
    s = _LABEL_TOKEN.sub(" ", " " + s.lower() + " ")
    return _WORD.findall(s)


def containment(needle: str, haystack: str) -> float:
    """Share of ``needle``'s words found, in order, in ``haystack`` (0..1)."""
    a, b = _words(needle), _words(haystack)
    if not a or not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(m.size for m in sm.get_matching_blocks()) / len(a)


def _own_text(doc: Document, p: Provision) -> str:
    """Text that identifies a provision: its own text, or its whole subtree if that is short."""
    own = " ".join(x for x in (p.heading, p.text) if x)
    if len(_words(own)) >= 6:
        return own
    return _subtree_text(doc, p)


def _subtree_text(doc: Document, p: Provision) -> str:
    return " ".join(" ".join(x for x in (q.heading, q.text) if x) for _, q in doc.walk(p.id))


def text_score(doc: Document, p: Provision, quoted: str) -> float:
    """How well a table's quoted text supports provision ``p`` (0..1).

    The larger of: share of ``p``'s own words found in the quote (a quote of a whole
    paragraph contains each of its points), and share of the quote's words found in
    ``p`` with its sub-provisions (a quote of one paragraph of a cited §).
    """
    if not quoted.strip():
        return 0.0
    return max(containment(_own_text(doc, p), quoted), containment(quoted, _subtree_text(doc, p)))


def resolve_eu_id(eu_id: str, doc: Document) -> tuple[str, str]:
    """``(id, how)``: ``how`` is ``exact``, ``fallback`` (known numbering difference) or ``unresolved``.

    Fallbacks: the tables cite numbered points directly under an article (definitions in
    Art. 6) as ``odst.``; the parsed directive has them as points (``art6.pt5``), and
    letters under them as sub-points. A point of an annex that the parsed directive keeps
    whole (``anx_i.pt1``) falls back to the annex.
    """
    if doc.get(eu_id) is not None:
        return eu_id, "exact"
    m = re.match(r"^(?P<head>.*:art\w+)\.par(?P<n>\d+)(?P<rest>(?:\..*)?)$", eu_id)
    if m:
        rest = m["rest"].replace(".pt_", ".subpt_")
        alt = f"{m['head']}.pt{m['n']}{rest}"
        if doc.get(alt) is not None:
            return alt, "fallback"
    m = re.match(r"^(?P<anx>.*:anx_[a-z0-9]+)\.", eu_id)
    if m and doc.get(m["anx"]) is not None:
        return m["anx"], "fallback"  # annexes are parsed as a whole: "Příloha I oddíl I bod 1" -> anx_i
    return eu_id, "unresolved"


@dataclass
class _Match:
    id: str
    check: str
    score: float | None
    note: str = ""


def _kind_of(local_id: str) -> str:
    """``"par12.odst3.pism_b"`` -> ``"pism"``."""
    return re.match(r"[a-z]+", local_id.rsplit(":", 1)[-1].rsplit(".", 1)[-1]).group()


#: A sentence of a provision found verbatim (this share of its words as one contiguous run)
#: in the quoted text confirms the provision even when the quote covers many other provisions.
SENTENCE_SUPPORT = 0.9
_SENTENCE_MIN_WORDS = 8


def sentence_support(text: str, quoted: str, min_words: int = _SENTENCE_MIN_WORDS) -> float:
    """How much of a sentence of ``text`` (with at least ``min_words`` words) appears verbatim in
    ``quoted``: the longest contiguous run of its words, as a share of the sentence (best sentence).

    A table cell that quotes many provisions (Slovak rows, a Czech row citing ``odst. 1 až 3``)
    gives each of them a low :func:`text_score`, but a whole sentence of the right provision is
    still there verbatim. Words in order but not contiguous (:func:`containment`) are not enough:
    formulaic sentences ("... neprodleně informuje Evropskou komisi a ostatní členské státy ...")
    recur across paragraphs.
    """
    q = _words(quoted)
    best = 0.0
    for sent in re.split(r"(?<=[.;:])\s+", text):
        w = _words(sent)
        if len(w) < min_words or not q:
            continue
        sm = difflib.SequenceMatcher(None, w, q, autojunk=False)
        best = max(best, sm.find_longest_match(0, len(w), 0, len(q)).size / len(w))
    return best


def _match_national(
    bill_id: str, bill_text: str, doc: Document, by_kind: dict[str, list[Provision]],
    accept: float, margin: float, exclude: frozenset[str] = frozenset(),
) -> _Match:
    """Map a bill-numbered ID to the published act by comparing the quoted bill text.

    ``exclude``: the other IDs cited by the same row; a renumbering never lands on one of
    them (the quote holds their text too, so it would match them just as well).
    """
    p = doc.get(bill_id)
    same = text_score(doc, p, bill_text) if p is not None else 0.0
    if p is not None and same >= accept:
        return _Match(bill_id, "same_number", round(same, 3))
    if not bill_text.strip():
        return _Match(bill_id, "unverified" if p is not None else "unresolved", None)
    if p is not None:
        sup = sentence_support(_own_text(doc, p), bill_text)
        if sup >= SENTENCE_SUPPORT:
            return _Match(bill_id, "same_number", round(sup, 3),
                          "confirmed by a whole sentence of its text quoted in the table")
    # the act added or dropped a paragraph level ("§ 2 písm. a)" -> "§ 2 odst. 1 písm. a)"):
    # the same letter one level up or down, if its text matches
    head, _, local = bill_id.rpartition(":")
    variants = []
    if re.search(r"\.odst1(?=\.)", local):
        variants.append(re.sub(r"\.odst1(?=\.)", "", local, count=1))
    m = re.match(r"^((?:par|cl)[0-9a-z]+)\.(pism_|bod)", local)
    if m:
        variants.append(f"{m[1]}.odst1{local[len(m[1]):]}")
    for v in variants:
        q = doc.get(f"{head}:{v}")
        if q is not None and q.id not in exclude:
            sc = text_score(doc, q, bill_text)
            if sc >= accept and sc >= same + margin:
                return _Match(q.id, "renumbered", round(sc, 3))
    bill_words = set(_words(bill_text))
    cands = []
    for q in by_kind.get(_kind_of(bill_id), []):
        w = set(_words(_subtree_text(doc, q)))
        if w:
            common = len(w & bill_words)
            cands.append((max(common / len(w), common / len(bill_words)), q))
    cands.sort(key=lambda t: -t[0])
    best, best_score = None, 0.0
    scores = []
    for _, q in cands[:8]:
        sc = text_score(doc, q, bill_text)
        scores.append(sc)
        if sc > best_score and q.id not in exclude:
            best, best_score = q, sc
    # a quote of a whole list supports each of its items equally: no basis to pick one (and when
    # another ID of the row matches as well, the quote is that provision's, not this one's)
    tied = sum(sc >= best_score - 0.01 for sc in scores) > 1
    if (best is not None and best.id != bill_id and best_score >= accept and best_score >= same + margin
            and not tied):
        return _Match(best.id, "renumbered", round(best_score, 3))
    if p is None:
        # the table cites a level the act does not have ("§ 28 písm. a)" where § 28 has no
        # letters) but quotes the text of the provision above it
        anc = bill_id
        while "." in anc.rpartition(":")[2]:
            anc = anc.rsplit(".", 1)[0]
            q = doc.get(anc)
            if q is not None:
                sc = text_score(doc, q, bill_text)
                if sc >= accept:
                    return _Match(anc, "ancestor", round(sc, 3))
                break
        return _Match(bill_id, "unresolved", None)
    return _Match(bill_id, "unverified", round(same, 3))


_AMENDED = re.compile(r"^\s*(?:\d+\.\s*)?(?:(?:V|Za|Před|Na konci)\s+)?§\s*(\d+[a-z]*)\b")


def _amended_section(text: str) -> str | None:
    """The § an amendment point changes: ``"2. V § 11 odst. 1 se ..."`` -> ``"11"``."""
    m = _AMENDED.match(text)
    return m.group(1) if m else None


def _match_part(bill_id: str, bill_text: str, doc: Document, accept: float) -> tuple[_Match, str]:
    """Map a part reference of the bill (``cz:X:cast9.bod2``, ``cz:X:cast10``) to the act by text.

    Parts that amend other acts are numbered differently in the bill and in the act once
    parts are added or dropped, and a part with a single change has no points (its § holds
    the change). So every § of the act's amending parts (heading ``Změna ...``) and each of
    its points is a candidate; the one whose text best matches the bill text quoted in the
    table is taken if it reaches ``accept``, else the link stays ``unresolved``.
    """
    head, _, local = bill_id.rpartition(":")
    m = re.match(r"^cast(\d+)(?:\.bod(\w+))?$", local)
    if not m or not bill_text.strip():
        return _Match(bill_id, "unresolved", None), ""
    parts = [p for p in doc.roots() if p.kind == "cast"] or [p for p in doc if p.kind == "cast"]
    amending = [p for p in parts if (p.heading or "").lower().startswith("změna")] or parts[1:]
    cands: list[tuple[Provision, str]] = []  # (provision, its part number)
    for part in amending:
        for _, q in doc.walk(part.id):
            tail = q.id.rpartition(":")[2]
            if re.match(r"^(?:par|cl)[0-9a-z]+\.bod\w+$", tail) or (
                re.match(r"^(?:par|cl)[0-9a-z]+$", tail) and not doc.children(q.id)
            ):  # a point, or a § that holds a single change (a § with points only introduces them)
                cands.append((q, part.number))
    # amendment points are formulaic ("V § 4b odst. 2 se slova ... nahrazují slovy ..."), so words
    # alone match the wrong point: a candidate that changes another § is not considered
    target = _amended_section(bill_text)
    scored = [
        (text_score(doc, q, bill_text), q, pn) for q, pn in cands
        if not target or _amended_section(q.text) in (None, target)
    ]
    best, best_score, best_part = None, 0.0, ""
    if scored:
        top = max(sc for sc, _, _ in scored)
        # among near-ties take a point, preferably the bill's point number
        near = [t for t in scored if t[0] >= top - 0.02]
        best_score, best, best_part = max(
            near, key=lambda t: (".bod" in t[1].id, t[1].id.endswith(f".bod{m[2]}") if m[2] else False, t[0])
        )
    if best is None or best_score < accept:
        guess = f"; best candidate {best.id.rpartition(':')[2]} ({best_score:.2f}) not confirmed by text" if best else ""
        return _Match(bill_id, "unresolved", round(best_score, 3) if best else None), (
            f"bill part {local} not found in the act" + guess)
    tail = best.id.rpartition(":")[2]
    bod = tail.partition(".bod")[2]
    same = best_part == m[1] and bod == (m[2] or "")
    return _Match(best.id, "same_number" if same else "renumbered", round(best_score, 3)), (
        f"bill {local} matches act {tail} (part {best_part}) by text")


def _doc_for(docs: Mapping[str, Document], single: Document | None, act: str, own: bool) -> Document | None:
    """The document an ID's act points to; a lone document passed alone serves IDs of the row's own act."""
    if act in docs:
        return docs[act]
    return single if own else None


def check_links(
    links: list[GoldLink],
    eu_doc: Document | None = None,
    national_doc: Document | None = None,
    rows: Iterable[TableRow] | None = None,
    accept: float = 0.6,
    margin: float = 0.2,
    eu_docs: Iterable[Document] = (),
    national_docs: Iterable[Document] = (),
) -> list[GoldLink]:
    """Verify (and where needed remap) the IDs of ``links`` in place; returns ``links``.

    EU side: :func:`resolve_eu_id`, plus ``eu_similarity`` (:func:`text_score` of the
    provision against the table's EU text). National side: the bill text quoted in the
    table is compared with the act at the same ID (``same_number`` if the score reaches
    ``accept``); otherwise the best-matching provision of the same kind is used when it
    scores at least ``margin`` higher (``renumbered``, with a note); otherwise the bill
    ID is kept (``unverified``, or ``unresolved`` if the ID does not exist in the act).
    A part reference (``cast3.bod1``, see :func:`normalize_cz_ref`) is matched by text
    against the §§ and points of the act's amending parts (:func:`_match_part`).

    Each ID is checked against the document of its own act: ``eu_doc`` / ``national_doc``,
    or one of ``eu_docs`` / ``national_docs`` (base acts of consolidated EU refs, other
    national acts of a multi-act table). IDs with no document stay ``not_checked``.

    Safeguards for rows that quote several provisions: an ID whose whole sentence is in the
    quote is kept (:func:`sentence_support`); a renumbering never lands on another ID of the
    same row; letters of a list are re-aligned in order when the act inserted one
    (:func:`_align_letters`); two IDs of a row never end on one act ID (:func:`_undo_collisions`).

    Pass the :class:`TableRow` list as ``rows`` so the full cell texts are compared;
    without it the (truncated) excerpts stored in the links are used.
    """
    full = {r.index: r for r in rows} if rows is not None else {}
    eus = {d.act_id: d for d in ([eu_doc] if eu_doc is not None else []) + list(eu_docs)}
    nats = {d.act_id: d for d in ([national_doc] if national_doc is not None else []) + list(national_docs)}
    by_kind: dict[str, dict[str, list[Provision]]] = {}
    for act, d in nats.items():
        kinds = by_kind.setdefault(act, {})
        for p in d:
            kinds.setdefault(p.kind, []).append(p)
    cache: dict[tuple[str, str, frozenset[str]], tuple[_Match, str]] = {}
    row_ids: dict[int | None, set[str]] = {}
    for link in links:
        if link.bill_national_id:
            row_ids.setdefault(link.row, set()).add(link.bill_national_id)
    checked: list[tuple[GoldLink, Document, str]] = []  # (link, its act, the quoted bill text)
    for link in links:
        row = full.get(link.row)
        eu_text = row.eu_text if row else link.eu_text
        bill_text = row.national_text if row else link.national_text
        eu = (_doc_for(eus, eu_doc, link.eu_id.split(":")[1], not link.eu_act)
              if link.eu_id and link.eu_id.count(":") >= 3 else None)
        if eu is not None:
            rid, how = resolve_eu_id(link.eu_id, eu)
            if how == "fallback":
                link.note = _add_note(link.note, f"EU ID mapped {link.eu_id.rsplit(':', 1)[1]} -> {rid.rsplit(':', 1)[1]}")
            link.eu_id, link.eu_check = rid, how
            if how != "unresolved":
                link.eu_similarity = round(text_score(eu, eu.by_id(rid), eu_text), 3)
        elif link.eu_id and link.eu_act:
            link.note = _add_note(link.note, f"no text of {link.eu_act} (consolidated) given: EU ID not checked")
        nat_act = link.bill_national_id.split(":")[1] if link.bill_national_id.count(":") >= 2 else ""
        nat = (_doc_for(nats, national_doc, nat_act, "section_act" not in link.flags)
               if link.bill_national_id else None)
        if nat is not None:
            siblings = frozenset(row_ids.get(link.row, set()) - {link.bill_national_id})
            key = (link.bill_national_id, bill_text, siblings)
            if key not in cache:
                kinds = by_kind.get(nat.act_id) or {}
                if not kinds:
                    for p in nat:
                        kinds.setdefault(p.kind, []).append(p)
                    by_kind[nat.act_id] = kinds
                if link.bill_national_id.rpartition(":")[2].startswith("cast"):
                    cache[key] = _match_part(link.bill_national_id, bill_text, nat, accept)
                else:
                    cache[key] = (_match_national(link.bill_national_id, bill_text, nat, kinds, accept, margin,
                                                  exclude=siblings), "")
            m, part_note = cache[key]
            link.national_id, link.national_check, link.national_similarity = m.id, m.check, m.score
            checked.append((link, nat, bill_text))
            if m.note:
                link.note = _add_note(link.note, m.note)
            if part_note:
                link.note = _add_note(link.note, part_note)
            elif m.check == "renumbered":
                link.note = _add_note(
                    link.note,
                    f"bill {link.bill_national_id.rsplit(':', 1)[1]} matches act {m.id.rsplit(':', 1)[1]} by text",
                )
            elif m.check == "ancestor":
                link.note = _add_note(
                    link.note,
                    f"bill {link.bill_national_id.rsplit(':', 1)[1]} not in the act; the quoted text is its "
                    f"parent {m.id.rsplit(':', 1)[1]}",
                )
    _align_letters(checked)
    _undo_collisions(checked, accept)
    return links


def _local(pid: str) -> str:
    return pid.rsplit(":", 1)[1]


def _letter_key(pid: str) -> tuple[int, str]:
    v = _local(pid).rsplit(".pism_", 1)[1]
    return len(v), v


def _align_letters(checked: Sequence[tuple[GoldLink, Document, str]]) -> None:
    """Re-align the letters of a list when the act inserted or dropped one.

    A row citing ``písm. a) až c)`` whose quote does not support one of the letters
    (``unverified``, or renumbered to another letter of the list) while the act has the same number of *other* letters fully quoted
    (``a), c), d)``: a letter was inserted before ``b)``) maps the bill's letters, in order,
    to the quoted ones. A short letter ("porušuje tento zákon, nebo") is trivially found in
    a long quote, so a ``same_number`` check alone does not prove the alignment.
    """
    groups: dict[tuple[int | None, str, str], list[GoldLink]] = {}
    ctx: dict[tuple[int | None, str, str], tuple[Document, str]] = {}
    for lk, doc, quote in checked:
        bid = lk.bill_national_id
        if not _local(bid).rsplit(".", 1)[-1].startswith("pism_") or lk.national_check == "not_checked":
            continue
        parent = bid.rsplit(".", 1)[0]
        key = (lk.row, doc.act_id, parent)
        groups.setdefault(key, []).append(lk)
        ctx[key] = (doc, quote)
    for key, lks in groups.items():
        doc, quote = ctx[key]
        parent = key[2]
        bill = sorted({lk.bill_national_id for lk in lks}, key=_letter_key)
        by_bill = {b: [lk for lk in lks if lk.bill_national_id == b] for b in bill}
        sibling = [lk.national_check == "renumbered" and lk.national_id.rsplit(".", 1)[0] == parent for lk in lks]
        if doc.get(parent) is None or not any(lk.national_check == "unverified" or sib for lk, sib in zip(lks, sibling)):
            continue
        if any(lk.national_check not in ("same_number", "unverified") and not sib for lk, sib in zip(lks, sibling)):
            continue
        letters = [q for q in doc.children(parent) if q.kind == "pism"]
        quoted = [(q, containment(_own_text(doc, q), quote)) for q in letters]
        quoted = [(q, sc) for q, sc in quoted if sc >= SENTENCE_SUPPORT]
        if len(quoted) != len(bill) or {q.id for q, _ in quoted} == set(bill):
            continue
        for b, (q, sc) in zip(bill, quoted):
            if q.id == b:
                continue
            for lk in by_bill[b]:
                if lk.national_id == q.id:
                    continue  # already renumbered there by text
                lk.national_id, lk.national_check, lk.national_similarity = q.id, "renumbered", round(sc, 3)
                lk.note = re.sub(r";? ?bill \S+ matches act \S+ by text", "", lk.note).strip("; ")
                lk.note = _add_note(lk.note, f"letters of the list aligned in order with the quoted text: bill "
                                             f"{_local(b)} -> act {_local(q.id)}")


def _shifted(bill: str, target: str, other: str) -> str | None:
    """Apply the renumbering ``bill`` -> ``target`` to a sibling: (``par1.ods1.pism_a.bod8`` ->
    ``par2.ods1.pism_a.bod8``) gives ``par1.ods1.pism_a.bod9`` -> ``par2.ods1.pism_a.bod9``."""
    b, t, o = bill.split("."), target.split("."), other.split(".")
    if len(b) != len(t) or len(o) != len(b):
        return None
    diff = [i for i in range(len(b)) if b[i] != t[i]]
    if not diff or any(o[i] != b[i] for i in diff):
        return None
    return ".".join(t[i] if i in diff else o[i] for i in range(len(o)))


def _undo_collisions(checked: Sequence[tuple[GoldLink, Document, str]], accept: float = 0.6) -> None:
    """Two distinct bill IDs of one row never map to the same act ID.

    When they do, the text of the row matched one provision best for both: the bill ID that
    is that provision (or else the best-scoring one) keeps it. The others take the same
    renumbering as the kept one when the act has that ID and the quote supports it
    (``bod8`` -> ``par2...bod8`` gives ``bod9`` -> ``par2...bod9``); otherwise they go back to
    their own number (``unverified``, or ``unresolved`` if the act lacks it).
    """
    by_target: dict[tuple[int | None, str], dict[str, list[tuple[GoldLink, Document, str]]]] = {}
    for lk, doc, quote in checked:
        if lk.national_id:
            by_target.setdefault((lk.row, lk.national_id), {}).setdefault(lk.bill_national_id, []).append(
                (lk, doc, quote))
    for (_, nid), bills in by_target.items():
        if len(bills) < 2:
            continue

        def rank(b: str) -> tuple[bool, float]:
            lk = bills[b][0][0]
            return b == nid, lk.national_similarity or 0.0

        keep = max(bills, key=rank)
        row = next(iter(bills.values()))[0][0].row
        taken = {lk.national_id for lk, _, _ in checked if lk.row == row}
        for b, items in bills.items():
            if b == keep:
                continue
            for lk, doc, quote in items:
                if lk.national_check != "renumbered":
                    continue  # several letters of a § without letters share their "ancestor" legitimately
                lk.note = re.sub(r";? ?bill \S+ matches act \S+ by text", "", lk.note).strip("; ")
                head = nid.rpartition(":")[0]
                sh = _shifted(_local(keep), _local(nid), _local(b)) if nid != keep else None
                q = doc.get(f"{head}:{sh}") if sh else None
                if q is not None and q.id not in taken:
                    sc = text_score(doc, q, quote)
                    if sc >= accept:
                        lk.national_id, lk.national_similarity = q.id, round(sc, 3)
                        lk.note = _add_note(lk.note, f"bill {_local(b)} renumbered like {_local(keep)} -> "
                                                     f"{_local(nid)}: act {sh}")
                        continue
                lk.note = _add_note(lk.note, f"bill {_local(b)} also matched act {_local(nid)}, which the row's "
                                             f"{_local(keep)} holds: kept unconfirmed")
                exists = doc.get(b) is not None
                lk.national_id = b
                lk.national_check = "unverified" if exists else "unresolved"
                lk.national_similarity = round(text_score(doc, doc.by_id(b), quote), 3) if exists else None


def _add_note(note: str, extra: str) -> str:
    return f"{note}; {extra}" if note else extra


_CHECK_RANK = {c: i for i, c in enumerate(
    ["exact", "same_number", "fallback", "renumbered", "ancestor", "unverified", "unresolved", "not_checked"])}


def summarize(links: Sequence[GoldLink]) -> dict:
    """Counts for the README / build report: links, distinct IDs, resolution rates, unresolved IDs."""
    eu_ids = {lk.eu_id for lk in links if lk.eu_id}
    cz_ids = {lk.national_id for lk in links if lk.national_id}
    # an ID reached by several links counts once, under its best check, so the counts add up
    eu_best: dict[str, str] = {}
    cz_best: dict[str, str] = {}
    for lk in links:
        if lk.eu_id and _CHECK_RANK.get(lk.eu_check, 9) < _CHECK_RANK.get(eu_best.get(lk.eu_id, ""), 10):
            eu_best[lk.eu_id] = lk.eu_check
        if lk.national_id and (_CHECK_RANK.get(lk.national_check, 9)
                               < _CHECK_RANK.get(cz_best.get(lk.national_id, ""), 10)):
            cz_best[lk.national_id] = lk.national_check
    eu_checks: dict[str, set[str]] = {}
    cz_checks: dict[str, set[str]] = {}
    for i, c in eu_best.items():
        eu_checks.setdefault(c, set()).add(i)
    for i, c in cz_best.items():
        cz_checks.setdefault(c, set()).add(i)
    eu_ok = {i for k, v in eu_checks.items() if k in ("exact", "fallback") for i in v}
    cz_ok = {i for k, v in cz_checks.items() if k in ("same_number", "renumbered", "unverified", "ancestor") for i in v}
    return {
        "links": len(links),
        "rows": len({lk.row for lk in links}),
        "eu_ids": len(eu_ids),
        "national_ids": len(cz_ids),
        "eu_check": {k: len(v) for k, v in sorted(eu_checks.items())},
        "national_check": {k: len(v) for k, v in sorted(cz_checks.items())},
        "eu_resolved_share": round(len(eu_ok) / len(eu_ids), 4) if eu_ids else None,
        "national_resolved_share": round(len(cz_ok) / len(cz_ids), 4) if cz_ids else None,
        "unparsed_refs": sorted({lk.raw_eu_ref for lk in links if not lk.eu_id}
                                | {lk.raw_national_ref for lk in links if not lk.national_id}),
        "eu_unresolved": sorted(eu_checks.get("unresolved", set())),
        "national_unresolved": sorted(cz_checks.get("unresolved", set())),
    }


# --- download (polite, cached) ---------------------------------------------------------------


def default_cache_dir() -> Path:
    """``$OPENACQUIS_CACHE/psp``, else ``~/.cache/openacquis/psp``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    base = Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"
    return base / "psp"


def download(
    url: str,
    cache_dir: Path | str | None | bool = None,
    get: Callable[[str], bytes] | None = None,
    timeout: float = 60.0,
) -> bytes:
    """GET a document with the project User-Agent; cached on disk without expiry
    (a published parliamentary print does not change). ``get`` replaces the network."""


    path = None
    if cache_dir is not False:
        base = Path(cache_dir) if cache_dir not in (None, True) else default_cache_dir()
        path = base / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".bin")
        if path.exists():
            return path.read_bytes()
    if get is not None:
        data = get(url)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        time.sleep(1.0)  # be polite to psp.cz between consecutive downloads
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError:
            pass
    return data


# --- finding a bill's table on psp.cz -----------------------------------------------------------

PSP_URL = "https://www.psp.cz"
#: Heading of the bill attachment that holds the correlation table.
TABLE_ATTACHMENT = "Kompatibilita s právem EU"


def tisk_url(term: int, tisk: int) -> str:
    """Attachment list of print ``tisk``/0 of the ``term``-th Chamber term (9 = 2021-25, 10 = from 2025)."""
    return f"{PSP_URL}/sqw/text/tiskt.sqw?o={term}&ct={tisk}&ct1=0"


def history_url(term: int, tisk: int) -> str:
    """Legislative history of the bill (readings, amendments, the act number once published)."""
    return f"{PSP_URL}/sqw/historie.sqw?o={term}&t={tisk}"


def table_file_url(idd: int | str) -> str:
    return f"{PSP_URL}/sqw/text/orig2.sqw?idd={idd}"


def _html_text(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1250", "replace")  # psp.cz pages are windows-1250


def find_table_files(page: bytes | str) -> list[dict]:
    """Files of the "Kompatibilita s právem EU" attachment on a print's page (:func:`tisk_url`).

    Returns ``[{"name": "t0947d0.docx", "url": ..., "idd": "253085"}, ...]`` with Word
    files first (``.docx``, then ``.doc``), PDFs last; empty if the bill has no table.
    """
    html = _html_text(page)
    i = html.find(TABLE_ATTACHMENT)
    if i < 0:
        return []
    j = html.find("<th", i)
    block = html[i:j if j > 0 else len(html)]
    files = [
        {"name": m["name"].strip(), "idd": m["idd"], "url": table_file_url(m["idd"])}
        for m in re.finditer(r'href="[^"]*orig2\.sqw\?idd=(?P<idd>\d+)"[^>]*>(?P<name>[^<]+)</a>', block)
    ]
    order = {".docx": 0, ".doc": 1}
    return sorted(files, key=lambda f: order.get(Path(f["name"]).suffix.lower(), 2))


def published_act(history: bytes | str) -> str | None:
    """``"266/2025"`` from a bill history page once the act is published, else ``None``."""
    text = re.sub(r"<[^>]+>", " ", _html_text(history)).replace("\xa0", " ")
    m = re.search(r"pod\s+číslem\s+(\d+/\d{4})\s*Sb", text)
    return m.group(1) if m else None
