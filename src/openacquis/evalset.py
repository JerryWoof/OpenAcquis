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
   an empty bill cell continues the previous bill provision (vertically merged cell).
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
   flagged ``renumbered``.

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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
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
    national_check: str = "not_checked"  # same_number | renumbered | unverified | unresolved | not_checked
    national_similarity: float | None = None
    eu_text: str = ""
    national_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

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
             |věta|věty|vět|pododstavec|pododstavce|část|části)(?![^\W\d_]))  # whole words: "bodu" is not "bod" + "u"
    |(?P<ord>(?:{_ORDINALS})\b)
    |(?P<conj>(?:až|a|nebo)(?![\w)])|[,;])
    |(?P<val>\(?(?:\d+[a-z]?|[a-z]{{1,4}})(?:\)|\.)?)
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
}
_QUALIFIERS = {"věta", "věty", "vět", "pododstavec", "pododstavce", "pododst."}
# "bod" under a písmeno is level 4 (EU sub-point i), Czech bod 5); a "bod" with no písmeno above it is a
# numbered point ("čl. 6 bod 5", "§ 2 bod 5") at the písmeno level ("tbod"), and a písmeno below such a
# point is one level deeper ("spism": "čl. 6 bod 5 písm. a)").
_LEVEL = {"cast": 0, "art": 1, "par": 1, "annex": 1, "odst": 2, "pism": 3, "tbod": 3, "bod": 4, "spism": 4}


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


def _parse(ref: str, segment: Callable[[list[tuple[str, str]]], str]) -> ParsedRef:
    s = _clean_ref(ref)
    toks = [(m.lastgroup, m.group()) for m in _TOKEN.finditer(s)]
    done: list[list[tuple[str, str]]] = []
    branches: list[list[tuple[str, str]]] = [[]]
    qualifier: list[str] = []
    unparsed: list[str] = []
    i = 0
    while i < len(toks):
        kind, tok = toks[i]
        low = tok.lower()
        if kind == "kw" and low in _QUALIFIERS:
            qualifier.append(low)
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
                if k2 == "val" or (k2 == "other" and level_kind == "annex" and re.fullmatch(r"[IVXLC]+", t2)):
                    values.append(_value(t2))
                    i += 1
                elif k2 == "conj" and t2.lower() == "až" and values and i + 1 < len(toks) and toks[i + 1][0] == "val":
                    values[-1:] = _expand(values[-1], _value(toks[i + 1][1]), level_kind)
                    i += 2
                elif k2 == "conj" and i + 1 < len(toks) and toks[i + 1][0] == "val":
                    i += 1  # "a 2", ", 3": next value of the same list
                else:
                    break
            if not values:
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
        else:
            raise ValueError(f"{k} not valid in an EU reference")
    if not out or kinds[0] not in ("art", "annex"):
        raise ValueError("EU reference must start with an article or annex")
    return ".".join(out)


def _cz_segment(path: list[tuple[str, str]]) -> str:
    kinds = [k for k, _ in path]
    if not kinds or kinds[0] not in ("par", "art", "annex", "cast"):
        raise ValueError("Czech reference must start with §, čl., příloha or část")
    out = []
    for k, v in path:
        prefix = {"par": "par", "art": "cl", "annex": "priloha", "cast": "cast", "tbod": "bod",
                  "spism": "pism"}.get(k, k)
        if k in ("annex", "art") and roman_to_int(v):
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
    return _parse(ref, _cz_segment)


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


def doc_rows(text: str, width: int = 5, key_col: int = 2, key: re.Pattern = CELEX_RE) -> list[list[str]]:
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
    """One data row of a correlation table, with the bill cells carried down."""

    index: int
    national_ref: str
    national_text: str
    celex: str
    eu_ref: str
    eu_text: str
    compatibility: str | None = None


def _cell(s: str) -> str:
    s = s.replace("\xa0", " ")
    lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def table_rows(rows: Sequence[Sequence[str]], celex: str | None = None) -> list[TableRow]:
    """Data rows of a rozdílová tabulka: ``[bill ref, bill text, CELEX, EU ref, EU text, (compatibility)]``.

    Rows without a CELEX in the third cell (titles, headers, the closing list of EU acts)
    are skipped. An empty bill reference continues the previous one. With ``celex``,
    only rows for that EU act are returned.
    """
    out: list[TableRow] = []
    ref, text = "", ""
    n = 0
    for r in rows:
        if len(r) < 5 or not CELEX_RE.match(r[2] or ""):
            continue
        if _clean_ref(r[0]):
            ref, text = _clean_ref(r[0]), _cell(r[1])
        elif _cell(r[1]):
            text = _cell(r[1])
        comp = None
        if len(r) > 5 and r[5].strip():
            comp = COMPATIBILITY.get(_clean_ref(r[5]).lower(), _clean_ref(r[5]))
        row = TableRow(n, ref, text, CELEX_RE.match(r[2]).group(1), _clean_ref(r[3]), _cell(r[4]), comp)
        n += 1
        if celex is None or row.celex == celex:
            out.append(row)
    return out


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

    Rows whose references do not parse still give a link with an empty ID and a note,
    so nothing in the table is silently dropped.
    """
    links: list[GoldLink] = []
    for r in rows:
        if not r.national_ref:
            continue
        eu, cz = parse_eu_ref(r.eu_ref), parse_cz_ref(r.national_ref)
        notes = []
        if eu.qualifier:
            notes.append(f"EU ref qualifier: {eu.qualifier}")
        if cz.qualifier:
            notes.append(f"CZ ref qualifier: {cz.qualifier}")
        if eu.unparsed:
            notes.append(f"EU ref not fully parsed: {eu.unparsed!r}")
        if cz.unparsed:
            notes.append(f"CZ ref not fully parsed: {cz.unparsed!r}")
        for e in eu.ids or [""]:
            for c in cz.ids or [""]:
                eu_id = f"eu:{r.celex}:{eu_language}:{e}" if e else ""
                cz_id = f"{national_source}:{act_id}:{c}" if c else ""
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

    Fallback: the tables cite numbered points directly under an article (definitions in
    Art. 6) as ``odst.``; the parsed directive has them as points (``art6.pt5``), and
    letters under them as sub-points.
    """
    if doc.get(eu_id) is not None:
        return eu_id, "exact"
    m = re.match(r"^(?P<head>.*:art\w+)\.par(?P<n>\d+)(?P<rest>(?:\..*)?)$", eu_id)
    if m:
        rest = m["rest"].replace(".pt_", ".subpt_")
        alt = f"{m['head']}.pt{m['n']}{rest}"
        if doc.get(alt) is not None:
            return alt, "fallback"
    return eu_id, "unresolved"


@dataclass
class _Match:
    id: str
    check: str
    score: float | None


def _kind_of(local_id: str) -> str:
    """``"par12.odst3.pism_b"`` -> ``"pism"``."""
    return re.match(r"[a-z]+", local_id.rsplit(":", 1)[-1].rsplit(".", 1)[-1]).group()


def _match_national(
    bill_id: str, bill_text: str, doc: Document, by_kind: dict[str, list[Provision]],
    accept: float, margin: float,
) -> _Match:
    """Map a bill-numbered ID to the published act by comparing the quoted bill text."""
    p = doc.get(bill_id)
    same = text_score(doc, p, bill_text) if p is not None else 0.0
    if p is not None and same >= accept:
        return _Match(bill_id, "same_number", round(same, 3))
    if not bill_text.strip():
        return _Match(bill_id, "unverified" if p is not None else "unresolved", None)
    bill_words = set(_words(bill_text))
    cands = []
    for q in by_kind.get(_kind_of(bill_id), []):
        w = set(_words(_subtree_text(doc, q)))
        if w:
            common = len(w & bill_words)
            cands.append((max(common / len(w), common / len(bill_words)), q))
    cands.sort(key=lambda t: -t[0])
    best, best_score = None, 0.0
    for _, q in cands[:8]:
        sc = text_score(doc, q, bill_text)
        if sc > best_score:
            best, best_score = q, sc
    if best is not None and best.id != bill_id and best_score >= accept and best_score >= same + margin:
        return _Match(best.id, "renumbered", round(best_score, 3))
    if p is None:
        return _Match(bill_id, "unresolved", None)
    return _Match(bill_id, "unverified", round(same, 3))


def check_links(
    links: list[GoldLink],
    eu_doc: Document | None = None,
    national_doc: Document | None = None,
    rows: Iterable[TableRow] | None = None,
    accept: float = 0.6,
    margin: float = 0.2,
) -> list[GoldLink]:
    """Verify (and where needed remap) the IDs of ``links`` in place; returns ``links``.

    EU side: :func:`resolve_eu_id`, plus ``eu_similarity`` (:func:`text_score` of the
    provision against the table's EU text). National side: the bill text quoted in the
    table is compared with the act at the same ID (``same_number`` if the score reaches
    ``accept``); otherwise the best-matching provision of the same kind is used when it
    scores at least ``margin`` higher (``renumbered``, with a note); otherwise the bill
    ID is kept (``unverified``, or ``unresolved`` if the ID does not exist in the act).

    Pass the :class:`TableRow` list as ``rows`` so the full cell texts are compared;
    without it the (truncated) excerpts stored in the links are used.
    """
    full = {r.index: r for r in rows} if rows is not None else {}
    by_kind: dict[str, list[Provision]] = {}
    if national_doc is not None:
        for p in national_doc:
            by_kind.setdefault(p.kind, []).append(p)
    cache: dict[tuple[str, str], _Match] = {}
    for link in links:
        row = full.get(link.row)
        eu_text = row.eu_text if row else link.eu_text
        bill_text = row.national_text if row else link.national_text
        if eu_doc is not None and link.eu_id:
            rid, how = resolve_eu_id(link.eu_id, eu_doc)
            if how == "fallback":
                link.note = _add_note(link.note, f"EU ID mapped {link.eu_id.rsplit(':', 1)[1]} -> {rid.rsplit(':', 1)[1]}")
            link.eu_id, link.eu_check = rid, how
            if how != "unresolved":
                link.eu_similarity = round(text_score(eu_doc, eu_doc.by_id(rid), eu_text), 3)
        if national_doc is not None and link.bill_national_id:
            key = (link.bill_national_id, bill_text)
            if key not in cache:
                cache[key] = _match_national(link.bill_national_id, bill_text, national_doc, by_kind, accept, margin)
            m = cache[key]
            link.national_id, link.national_check, link.national_similarity = m.id, m.check, m.score
            if m.check == "renumbered":
                link.note = _add_note(
                    link.note,
                    f"bill {link.bill_national_id.rsplit(':', 1)[1]} matches act {m.id.rsplit(':', 1)[1]} by text",
                )
    return links


def _add_note(note: str, extra: str) -> str:
    return f"{note}; {extra}" if note else extra


def summarize(links: Sequence[GoldLink]) -> dict:
    """Counts for the README / build report: links, distinct IDs, resolution rates, unresolved IDs."""
    eu_ids = {lk.eu_id for lk in links if lk.eu_id}
    cz_ids = {lk.national_id for lk in links if lk.national_id}
    eu_checks: dict[str, set[str]] = {}
    cz_checks: dict[str, set[str]] = {}
    for lk in links:
        if lk.eu_id:
            eu_checks.setdefault(lk.eu_check, set()).add(lk.eu_id)
        if lk.national_id:
            cz_checks.setdefault(lk.national_check, set()).add(lk.national_id)
    eu_ok = {i for k, v in eu_checks.items() if k in ("exact", "fallback") for i in v}
    cz_ok = {i for k, v in cz_checks.items() if k in ("same_number", "renumbered", "unverified") for i in v}
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
