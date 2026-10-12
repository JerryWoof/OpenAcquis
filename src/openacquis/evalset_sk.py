"""Evaluation sets from Slovak correlation tables (*tabuľky zhody*).

A Slovak government bill that transposes EU law carries, for each directive, an official
*tabuľka zhody návrhu právneho predpisu s právom Európskej únie*. The National Council
(NR SR) publishes it as ``.docx`` on the bill's print page
(``https://www.nrsr.sk/web/Dynamic/Download.aspx?DocID=...``). Unlike the Czech template,
every row also says *how* the provision is transposed and whether the bill complies:

==  ===================  ==========================================================
 1  Článok (Č, O, V, P)  EU provision, ``Č:2, O:2, P:a), P:i``
 2  Text                 EU text
 3  Spôsob transpozície  ``N`` (normal), ``O`` (with an option), ``D`` (at discretion),
                         ``n.a.`` (no transposition)
 4  Číslo                national act: ``Návrh zákona`` (the bill), ``Zákon č. 69/2018 Z. z.``
 5  Článok (Č, §, O, V, P)  national provision, ``Č:I, §:17, O:1, P:c), P:1``
 6  Text                 national text (of the bill)
 7  Zhoda                compliance: ``Ú`` (full), ``Č`` (partial), ``Ž`` (none), ``n.a.``
 8  Poznámky             notes
 9  Identifikácia goldplatingu      ``GP-N`` (no gold-plating), ``GP-A`` ...
10  Identifikácia oblasti goldplatingu  explanation
==  ===================  ==========================================================

This module turns such a table into gold links between OpenAcquis provision IDs:

    eu:32022L2555:sk:art2.par2.pt_a.subpt_i   <->   sk:69/2018:par17.ods1.pism_c.bod1

* ``Č:I`` in a national reference is the **article of the amending bill**; the bill's
  articles are mapped to the act each one amends (read from the act as promulgated:
  ``Čl. I`` of 366/2024 amends 69/2018), and the ID points into that act **in the
  version produced by the amendment** (Slov-Lex ``69/2018`` as amended by ``366/2024``).
  A bill article that amends nothing (the new act's own body) is the new act itself;
  final provisions and amending points (``Č:VI, Bod 5``) point into the promulgated
  amending act (``sk:366/2024:cl6.bod5``).
* Several acts in one row: the act cell lists them one per line and the reference cell
  has one group of references per act. Equal counts are paired in order; otherwise each
  group gets the act at the nearest relative line position (flagged ``act_assignment``).
* Rows with no national provision (``n.a.``, or an option not used) become **negative**
  records (empty ``national_id``): the government states that nothing transposes them.
* Compliance (``status``), method and gold-plating flags are kept on every record:
  :class:`SkGoldLink` extends :class:`openacquis.evalset.GoldLink` with these fields, so
  :func:`openacquis.evalset.load_jsonl` still reads the files (it drops the extra fields).

Gold links are what the government's table *asserts* about its bill; they are evidence
for evaluating matching, not findings about whether Slovak law transposes the directive
correctly.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .evalset import (
    EXCERPT,
    GoldLink,
    check_links,
    docx_rows,
    resolve_eu_id,
    sentence_support,
    summarize,
    text_score,
)
from .provisions import Document, int_to_roman, make_segment, roman_to_int

#: Compliance column (*zhoda*) -> ``status`` (same vocabulary as the Czech tables).
COMPLIANCE = {
    "Ú": "fully_compatible",
    "Č": "partially_compatible",
    "Ž": "incompatible",
    "n.a.": "not_applicable",
}
#: Method of transposition (*spôsob transpozície*).
METHODS = {
    "N": "normal",  # bežná transpozícia
    "O": "option",  # transpozícia s možnosťou voľby
    "D": "discretion",  # transpozícia podľa úvahy (dobrovoľná)
    "n.a.": "not_transposed",  # transpozícia sa neuskutočňuje
}
#: Status of a negative record whose row gives no compliance.
STATUS_NO_PROVISION = "no_national_provision"

#: Codes and other acts cited by name instead of number.
NAMED_ACTS = {
    "občiansky zákonník": "40/1964",
    "obchodný zákonník": "513/1991",
    "zákonník práce": "311/2001",
    "civilný sporový poriadok": "160/2015",
    "civilný mimosporový poriadok": "161/2015",
    "správny súdny poriadok": "162/2015",
    "správny poriadok": "71/1967",
    "trestný zákon": "300/2005",
    "trestný poriadok": "301/2005",
    "ústava": "460/1992",
    "antidiskriminačný zákon": "365/2004",
}
BILL = "bill"  # act key of "Návrh zákona" (the amending bill)


# --- record --------------------------------------------------------------------------


@dataclass
class SkGoldLink(GoldLink):
    """A :class:`GoldLink` with the extra columns of a Slovak *tabuľka zhody*.

    ``status`` is the normalised compliance (:data:`COMPLIANCE`), ``"listed"`` when the
    row gives none, or :data:`STATUS_NO_PROVISION` for a negative without compliance.
    Negative records (the table names no national provision) have an empty
    ``national_id``. ``national_act`` is the act the ID points into and ``act_version``
    its Slov-Lex version (``20250101``); ``bill_article`` the article of the amending
    bill the table cited (``I``).
    """

    method: str = ""  # N | O | D | n.a. (raw codes joined by "/" when a row has several)
    compliance: str = ""  # Ú | Č | Ž | n.a. as printed
    gold_plating: str = ""  # GP-N | GP-A | ... as printed (spaces and dashes normalised)
    gold_plating_note: str = ""
    table_note: str = ""  # the table's "Poznámky" column
    national_act: str = ""  # "69/2018"
    act_version: str = ""  # Slov-Lex version key of the text checked against
    bill_article: str = ""  # article of the amending bill ("I"), empty if the base act was cited
    act_assignment: str = ""  # "single" | "ordered" | "position" (several acts in one row) | "text" (moved by text)
    negative: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> SkGoldLink:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def save_jsonl(links: Iterable[GoldLink], path: str | Path) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for link in links:
            f.write(json.dumps(asdict(link), ensure_ascii=False) + "\n")
            n += 1
    return n


def load_jsonl(path: str | Path) -> list[SkGoldLink]:
    with open(path, encoding="utf-8") as f:
        return [SkGoldLink.from_dict(json.loads(line)) for line in f if line.strip()]


# --- rows ------------------------------------------------------------------------------------


@dataclass
class SkRow:
    """One data row of a tabuľka zhody (cells cleaned, the EU reference carried down)."""

    index: int
    eu_ref: str
    eu_text: str
    method: str
    act: str
    national_ref: str
    national_text: str
    compliance: str
    note: str = ""
    gold_plating: str = ""
    gold_plating_note: str = ""


_EU_START = re.compile(r"^\s*(?:(?:Č|C|Čl)\s*\.?\s*:?\s*\d|Príloh)", re.IGNORECASE)


def _cell(s: str) -> str:
    s = s.replace("\xa0", " ").replace("\t", " ")
    lines = (re.sub(r"[ ]+", " ", ln).strip() for ln in s.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def _keep_lines(s: str) -> str:
    """Whitespace-normalised cell that keeps its (empty) lines: line positions align cells."""
    return "\n".join(re.sub(r"\s+", " ", ln.replace("\xa0", " ")).strip() for ln in s.split("\n")).strip("\n ")


def _code(s: str) -> str:
    """``"n.a"`` -> ``"n.a."``, ``"GP –  N"`` -> ``"GP-N"``, several codes joined by ``/``."""
    out = []
    for tok in re.split(r"\n+", s.replace("\xa0", " ").strip()):
        t = re.sub(r"\s+", "", tok.replace("–", "-").replace("—", "-"))
        if not t:
            continue
        if t.lower().rstrip(".") == "n.a":
            t = "n.a."
        if t not in out:
            out.append(t)
    return "/".join(out)


def sk_table_rows(rows: Sequence[Sequence[str]]) -> list[SkRow]:
    """Data rows of a tabuľka zhody from :func:`openacquis.evalset.docx_rows` output.

    A data row has (at least) ten cells and an EU reference (``Č:``, ``Príloha``) in the
    first; header rows, rows of nested tables (points quoted inside a text cell) and the
    side-by-side annex tables that follow the main table are skipped. A row with an empty
    first cell continues the EU reference above it (a vertically merged cell).
    """
    out: list[SkRow] = []
    last_ref = ""
    for r in rows:
        if len(r) < 10 or len(r) > 11:
            continue
        first = r[0].strip()
        if first and not _EU_START.match(first):
            continue
        if not first:
            if not last_ref or not any(c.strip() for c in r[2:7]):
                continue
            eu_ref = last_ref
        else:
            eu_ref = last_ref = _keep_lines(r[0])
        out.append(SkRow(
            index=len(out),
            eu_ref=eu_ref,
            eu_text=_cell(r[1]),
            method=_code(r[2]),
            act=_keep_lines(r[3]),
            national_ref=_keep_lines(r[4]),
            national_text=_cell(r[5]),
            compliance=_code(r[6]),
            note=_cell(r[7]),
            gold_plating=_code(r[8]),
            gold_plating_note=_cell(r[9]),
        ))
    return out


# --- references ---------------------------------------------------------------------------------

# a value: 12, 3a, IV, c), ii; an upper-case letter followed by ":" is the next keyword
# ("C: 4"), not a roman numeral
_VAL = r"(?:\d+[a-z]?|[IVXLCDM]+(?![a-zA-Z])(?!\s*:)|[a-z]{1,4}(?![\w]))\)?"
_TOKEN = re.compile(
    rf"""
    (?P<kw>Príloh[a-y]*(?:\s*č\.)?|Bod(?:y|u)?|Čl\.?|§|[Pp]ododsek[a-y]*|[ČCOPBV](?=\s*:)|O(?=\s+\d))
    \s*:?\s*
    (?P<vals>{_VAL}(?:\s*(?:,|až|a|-)\s*{_VAL})*)?
    |(?P<sep>[,;/]|a(?!\w))
    |(?P<other>\S+)
    """,
    re.VERBOSE,
)


def _vals(s: str) -> list[str]:
    """``"4 a 5"`` -> [4, 5]; ``"7 až 9"`` -> [7, 8, 9]; ``"d,e"`` -> [d, e]; ``"a"`` -> [a]."""
    pieces = [p for p in re.split(r"(až|-|,)|\s+", s) if p]
    out: list[str] = []
    pending_range = False
    for i, p in enumerate(pieces):
        if p in ("až", "-"):
            pending_range = True
            continue
        if p == ",":
            continue
        if p == "a" and out and i + 1 < len(pieces):
            continue  # "4 a 5": the conjunction, not the letter a)
        v = p.strip("()").strip()
        if not v:
            continue
        if pending_range and out:
            out[-1:] = _expand(out[-1], v)
        else:
            out.append(v)
        pending_range = False
    return out


def _expand(a: str, b: str) -> list[str]:
    if a.isdigit() and b.isdigit() and int(a) <= int(b) <= int(a) + 200:
        return [str(i) for i in range(int(a), int(b) + 1)]
    ra, rb = roman_to_int(a), roman_to_int(b)
    if ra and rb and ra <= rb and (len(a) > 1 or len(b) > 1):
        conv = [int_to_roman(i) for i in range(ra, rb + 1)]
        return [c.upper() for c in conv] if a.isupper() else conv
    if len(a) == len(b) == 1 and a.isalpha() and b.isalpha() and a <= b:
        return [chr(c) for c in range(ord(a), ord(b) + 1)]
    return [a, b]


@dataclass
class RefGroup:
    """References of one act inside a cell: leaf paths of ``(kind, value)``."""

    leaves: list[list[tuple[str, str]]] = field(default_factory=list)
    line: int = 0  # line of the cell where the group starts
    qualifier: str = ""  # sentence ("V:1") qualifiers
    unparsed: str = ""


_ROMAN_ONLY = re.compile(r"^(?:i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv)$")
# levels: Č 0, Príloha / § and "Bod" (amending point) 1, O 2, P letter 3, P digit / B 4
_LEVEL = {"art": 0, "annex": 1, "par": 1, "abod": 1, "ods": 2, "pism": 3, "bod": 4}


def _kind(kw: str) -> str:
    k = kw.rstrip(".:").strip()
    if k.startswith("Príloh"):
        return "annex"
    if k.startswith("Bod"):
        return "abod"
    if k.lower().startswith("pododsek"):
        return "pododsek"
    return {"Č": "art", "Čl": "art", "C": "art", "§": "par", "O": "ods", "P": "pism", "B": "bod", "V": "veta"}[k]


def _is_child_point(prev: str, new: str) -> bool:
    """``P:a), P:i``: is ``new`` a sub-point of point ``prev`` (not its next sibling)?"""
    if new.isdigit():
        return True
    if not _ROMAN_ONLY.match(new):
        return False
    if len(new) == 1 and len(prev) == 1 and prev.isalpha() and ord(new) == ord(prev) + 1:
        return False  # "h)" then "i)": the next letter
    return not (prev and _ROMAN_ONLY.match(prev) and len(prev) > 1)  # "ii" then "iii": siblings


def parse_sk_ref(ref: str) -> list[RefGroup]:
    """Parse a Slovak-table reference cell into groups of leaf paths.

    A group starts at each ``Č`` (article) and at each ``§`` / ``Príloha`` not directly
    after an article. Within a group every token closes the deeper levels it replaces, so
    ``"C: 1\\n§: 1\\nO: 1\\nP: a\\nP: b\\n§: 5"`` gives ``§1 ods1 a``, ``§1 ods1 b`` and ``§5``.
    ``P`` is a lettered point (``písm.``), or a numbered/roman sub-point under a letter;
    ``B`` is a numbered point (``bod``); ``V`` (sentence) is kept as a qualifier.

    >>> [g.leaves for g in parse_sk_ref("Č:I, §:17, O:1, P:c), P:1")]
    [[[('art', 'I'), ('par', '17'), ('ods', '1'), ('pism', 'c'), ('bod', '1')]]]
    """
    s = ref.replace("\xa0", " ").replace("–", "-")
    s = re.sub(r"pododesk", "pododsek", s, flags=re.IGNORECASE)  # a typo of the CER table
    s = re.sub(r"(\d+)\.\s*([Pp]ododsek\w*)", r"Pododsek \1", s)  # "2. pododsek" (the CER template)
    groups: list[RefGroup] = []
    cur: RefGroup | None = None
    path: list[tuple[str, list[str]]] = []  # (kind, values)
    prev_kind = ""
    last_end = 0

    def emit() -> None:
        if not path or cur is None:
            return
        # a deeper level after a list qualifies only its last item: the others stand alone
        prefix: list[tuple[str, str]] = []
        for i, (k, vs) in enumerate(path):
            for v in vs[:-1] if i + 1 < len(path) else []:
                cur.leaves.append(prefix + [(k, v)])
            if i + 1 == len(path):
                for v in vs:
                    cur.leaves.append(prefix + [(k, v)])
            prefix = prefix + [(k, vs[-1])]

    for m in _TOKEN.finditer(s):
        if m.group("sep"):
            continue
        if m.group("other"):
            if cur is None:
                cur = RefGroup(line=s.count("\n", 0, m.start()))
                groups.append(cur)
            tok = m.group("other").strip(",;.:()")
            if tok:
                cur.unparsed = f"{cur.unparsed} {tok}".strip()
            continue
        kind = _kind(m.group("kw"))
        vals = _vals(m.group("vals") or "")
        if kind == "annex" and not vals:
            vals = ["0"]  # "Príloha" without a number
        if not vals:
            if cur is not None:
                cur.unparsed = f"{cur.unparsed} {m.group().strip()}".strip()
            continue
        if kind in ("veta", "pododsek"):  # sentence / subparagraph: IDs cannot express them
            if cur is not None:
                cur.qualifier = f"{cur.qualifier} {kind} {','.join(vals)}".strip()
            continue
        # a § after an article starts a new group when a blank line separates them: the
        # article stands alone ("Č:VII" = final provisions) and the § belongs to the next act
        gap = prev_kind == "art" and re.search(r"\n\s*\n", s[last_end:m.start()]) is not None
        new_group = kind == "art" or (kind in ("par", "annex") and (prev_kind != "art" or gap))
        if new_group or cur is None:
            emit()
            path = []
            cur = RefGroup(line=s.count("\n", 0, m.start()))
            groups.append(cur)
        if kind == "pism":
            # a P under a lettered point is a sub-point ("P:c), P:1"), else a letter / numbered point
            last = path[-1] if path else None
            if last and last[0] == "pism" and _is_child_point(last[1][-1], vals[0]):
                kind = "bod"
            elif last and last[0] == "bod" and (vals[0].isdigit() == last[1][-1].isdigit()):
                kind = "bod"  # "P:c), P:3, P:4": the next sub-point
            elif vals[0].isdigit() and not any(k == "pism" for k, _ in path):
                kind = "pism"  # a numbered point under § / ods.: "bod" or not is settled by the act's IDs
        level = _LEVEL[kind]
        if path and _LEVEL[path[-1][0]] >= level:
            emit()
            path = [(k, vs) for k, vs in path if _LEVEL[k] < level]
        path.append((kind, vals))
        prev_kind = kind
        last_end = m.end()
    emit()
    return [g for g in groups if g.leaves or g.unparsed]


# --- acts in a row ---------------------------------------------------------------------------

_ACT_IN_CELL = re.compile(
    r"(?P<bill>návrh(?:u)?\s+zákona)"
    r"|(?:zákon[a-z]*\s+)?(?:(?:NR\s+SR|Národnej\s+rady\s+Slovenskej\s+republiky)\s+)?"
    r"č\.\s*(?P<n1>\d+[a-z]?)\s*/\s*(?P<y1>\d{4})"
    r"|(?<![\d/])(?P<n2>\d+[a-z]?)\s*/\s*(?P<y2>\d{4})\s*(?:Z\.\s*z\.|Zb\.)"
    r"|(?P<named>" + "|".join(re.escape(k) for k in sorted(NAMED_ACTS, key=len, reverse=True)) + r")",
    re.IGNORECASE,
)


@dataclass
class ActMention:
    act: str  # "69/2018" or BILL
    line: int
    as_amended: bool = False  # "Zákon č. 69/2018 Z. z. v znení návrhu zákona"


def acts_in_cell(cell: str) -> list[ActMention]:
    """Acts named in the "Číslo" cell, in order, with the line each starts on."""
    out: list[ActMention] = []
    for m in _ACT_IN_CELL.finditer(cell):
        line = cell.count("\n", 0, m.start())
        if m.group("bill"):
            # "Zákon č. 69/2018 Z. z. v znení návrhu zákona": the base act as amended, not a new mention
            before = cell[max(0, m.start() - 12):m.start()].lower()
            if out and "znení" in before:
                out[-1].as_amended = True
                continue
            out.append(ActMention(BILL, line))
        elif m.group("named"):
            out.append(ActMention(NAMED_ACTS[m.group("named").lower()], line))
        else:
            n, y = (m.group("n1"), m.group("y1")) if m.group("n1") else (m.group("n2"), m.group("y2"))
            out.append(ActMention(f"{n.lower()}/{y}", line))
    return out


def assign_acts(groups: Sequence[RefGroup], acts: Sequence[ActMention], ref_lines: int, act_lines: int
                ) -> tuple[list[ActMention | None], str]:
    """Pair each reference group with an act mention: ``(acts per group, how)``.

    One distinct act: all groups. As many groups as mentions: in order. Otherwise a
    monotonic assignment that minimises the distance between relative line positions.
    """
    if not groups:
        return [], ""
    if not acts:
        return [None] * len(groups), "none"
    distinct = {(a.act, a.as_amended) for a in acts}
    if len(distinct) == 1:
        return [acts[0]] * len(groups), "single"
    if len(groups) == len(acts):
        return list(acts), "ordered"
    gp = [g.line / max(ref_lines, 1) for g in groups]
    ap = [a.line / max(act_lines, 1) for a in acts]
    n, m = len(groups), len(acts)
    inf = float("inf")
    # cost[i][j]: best cost of groups[:i+1] with group i on act j (non-decreasing j)
    cost = [[inf] * m for _ in range(n)]
    back = [[0] * m for _ in range(n)]
    for j in range(m):
        cost[0][j] = abs(gp[0] - ap[j]) + 0.01 * j
    for i in range(1, n):
        for j in range(m):
            k = min(range(j + 1), key=lambda k: cost[i - 1][k])
            cost[i][j] = cost[i - 1][k] + abs(gp[i] - ap[j])
            back[i][j] = k
    j = min(range(m), key=lambda j: cost[n - 1][j])
    out: list[ActMention | None] = [None] * n
    for i in range(n - 1, -1, -1):
        out[i] = acts[j]
        j = back[i][j]
    return out, "position"


# --- bill articles -> acts -------------------------------------------------------------------

_AMENDED_ACT = re.compile(
    r"^\s*(?:Zákon|Nariadenie vlády|Vyhláška|Ústavný zákon)[^č]{0,120}?č\.\s*(\d+[a-z]?)\s*/\s*(\d{4})", re.I)


def bill_articles(promulgated: Document) -> dict[int, str | None]:
    """Article number of the amending act -> the act it amends.

    ``None`` for the final provisions ("Tento zákon nadobúda účinnosť ...") and for any
    article that amends nothing; the article holding the new act's own §§ maps to the
    act itself. Read from the text as promulgated (``vyhlasene_znenie``).
    """
    out: dict[int, str | None] = {}
    act = promulgated.act_id
    for p in promulgated:
        if p.kind != "cl":
            continue
        n = roman_to_int(p.number) or (int(p.number) if p.number.isdigit() else None)
        if n is None:
            continue
        m = _AMENDED_ACT.match(p.text or "")
        if m:
            out[n] = f"{m[1].lower()}/{m[2]}"
        elif any(q.kind == "par" and q.parent_id == p.id for q in promulgated):
            out[n] = act
        else:
            out[n] = None
    return out


# --- rows -> links ----------------------------------------------------------------------------


@dataclass
class Target:
    """Where a national reference group points: act, version request, bill article."""

    act: str  # act id the provision lives in
    amended_by: str | None = None  # amending act whose version is wanted
    bill_article: str = ""
    promulgated: bool = False  # into the amending act as promulgated (its own articles)


def _national_local(leaf: list[tuple[str, str]], drop_article: bool) -> str:
    out = []
    for k, v in leaf:
        if k == "art":
            if drop_article:
                continue
            n = roman_to_int(v) if not v.isdigit() else int(v)
            out.append(make_segment("cl", str(n if n else v)))
        elif k == "annex":
            out.append(make_segment("priloha", v))
        elif k == "abod":
            out.append(make_segment("bod", v))
        else:
            out.append(make_segment(k, v))
    return ".".join(out)


def _eu_local(leaf: list[tuple[str, str]]) -> str:
    out = []
    kinds = [k for k, _ in leaf]
    for k, v in leaf:
        if k == "art":
            out.append(make_segment("article", v))
        elif k == "annex":
            out.append(make_segment("annex", v))
        elif k == "ods":
            out.append(make_segment("paragraph", v))
        elif k == "pism" or (k == "bod" and "pism" not in kinds):
            out.append(make_segment("point", v))  # "Č:2, B:1": a numbered point (definitions)
        elif k == "bod":
            out.append(make_segment("subpoint", v))
        else:  # "§" or an amending point in an EU reference: not an EU unit
            raise ValueError(f"{k} in an EU reference")
    if not kinds or kinds[0] not in ("art", "annex"):
        raise ValueError("EU reference must start with an article or annex")
    return ".".join(out)


def _excerpt(s: str, n: int = EXCERPT) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def eu_ids(ref: str) -> tuple[list[str], str]:
    """Local EU IDs of an EU reference cell and the unparsed rest."""
    ids, bad, _ = eu_ids_qualified(ref)
    return ids, bad


def eu_ids_qualified(ref: str) -> tuple[list[str], str, str]:
    """:func:`eu_ids` plus the sentence / subparagraph qualifier (``"pododsek 2"``, ``"veta 1"``)."""
    ids: list[str] = []
    bad: list[str] = []
    quals: list[str] = []
    for g in parse_sk_ref(ref):
        if g.qualifier and g.qualifier not in quals:
            quals.append(g.qualifier)
        if g.unparsed:
            bad.append(g.unparsed)
        for leaf in g.leaves:
            try:
                i = _eu_local(leaf)
            except ValueError:
                bad.append(" ".join(f"{k}:{v}" for k, v in leaf))
                continue
            if i not in ids:
                ids.append(i)
    return ids, " ".join(bad), "; ".join(quals)


def _target(group: RefGroup, mention: ActMention | None, final_act: str, bills: dict[int, str | None],
            last_bill_article: int | None) -> tuple[Target, int | None, bool]:
    """The act a group points into: ``(target, bill article used, drop the leading article)``.

    * "Návrh zákona": the article (``Č:I``; else the bill article of the previous group,
      else I) selects the amended act, in its version after the amendment; the article
      holding the new act's own §§ is the new act. A bare article or amending points
      (``Č:VI, Bod 5``) point into the promulgated amending act.
    * An act the bill amends, cited by number ("Zákon č. 69/2018 Z. z. v znení návrhu
      zákona"): that act after the amendment; a leading ``Č:`` before a § is dropped.
    * Any other act: its version in force when the amendment took effect; ``Č`` is kept
      when nothing follows it (``Ústava Čl. 46``).
    """
    first = group.leaves[0] if group.leaves else []
    art = first[0][1] if first and first[0][0] == "art" else None
    art_n = (int(art) if art.isdigit() else roman_to_int(art)) if art else None
    has_par = _has_par(group)
    act = mention.act if mention else BILL
    amended = {a: n for n, a in sorted(bills.items(), reverse=True) if a and a != final_act}
    if act == BILL:
        n = art_n if art_n is not None else (last_bill_article if last_bill_article is not None else 1)
        base = bills.get(n, final_act if n == 1 and not bills else None)
        if base is None or not has_par:
            return Target(final_act, None, _roman(n), True), n, False
        if base == final_act:
            return Target(final_act, None, _roman(n)), n, True
        return Target(base, final_act, _roman(n)), n, True
    if act in amended or (mention is not None and mention.as_amended):
        # the bill article only when the group cited the bill: "v znení návrhu zákona", or a leading
        # "Č:" that is the bill's article for this act (not the act's own article: "Zákonník práce Čl. 4")
        n = amended.get(act)
        cites_bill = (mention is not None and mention.as_amended) or (art_n is not None and art_n == n and has_par)
        return Target(act, final_act, _roman(n) if cites_bill else ""), n, has_par
    return Target(act), None, has_par


def _has_par(group: RefGroup) -> bool:
    return any(k in ("par", "annex") for leaf in group.leaves for k, _ in leaf)


def _roman(n: int | None) -> str:
    return int_to_roman(n).upper() if n else ""


def links_from_sk_rows(
    rows: Iterable[SkRow],
    celex: str,
    final_act: str,
    bills: dict[int, str | None],
    source_url: str = "",
    eu_language: str = "sk",
) -> list[SkGoldLink]:
    """Gold links (one per EU ID x national ID of a row) and negatives (no national provision).

    ``final_act`` is the act the bill became (``"366/2024"``); ``bills`` maps the bill's
    articles to the acts they amend (:func:`bill_articles`). National IDs are built in the
    bill's numbering; :func:`check_sk_links` verifies them against the texts.
    """
    links: list[SkGoldLink] = []
    for r in rows:
        eids, eu_bad, eu_qual = eu_ids_qualified(r.eu_ref)
        common = dict(
            celex=celex, row=r.index, source_url=source_url, raw_eu_ref=r.eu_ref.replace("\n", " "),
            raw_national_ref=re.sub(r"\s*\n\s*", " ", r.national_ref).strip(),
            method=r.method, compliance=r.compliance, gold_plating=r.gold_plating,
            gold_plating_note=_excerpt(r.gold_plating_note), table_note=_excerpt(r.note),
            eu_text=_excerpt(r.eu_text), national_text=_excerpt(r.national_text),
        )
        status = COMPLIANCE.get(r.compliance.split("/")[0], "") if r.compliance else ""
        notes = [f"EU ref qualifier: {eu_qual}"] if eu_qual else []
        if eu_bad:
            notes.append(f"EU ref not fully parsed: {eu_bad!r}")
        national_ref = r.national_ref
        if not national_ref.strip() and status in ("fully_compatible", "partially_compatible"):
            # the reference cell is empty but the text cell names the provision ("Príloha č. 1 k zákonu ...")
            m = _REF_IN_TEXT.match(r.national_text)
            if m:
                national_ref = m.group(1)
                notes.append(f"national ref taken from the text cell: {national_ref!r}")
        groups = parse_sk_ref(national_ref) if national_ref.strip() else []
        groups = [g for g in groups if g.leaves]
        if not groups:
            if national_ref.strip():
                notes.append(f"national ref not parsed: {national_ref!r}")
            if status in ("fully_compatible", "partially_compatible") and r.method != "n.a.":
                continue  # compliance claimed but no reference given: neither a link nor a negative
            method = r.method.split("/")[0]
            if method in ("D", "O") and status in ("", "not_applicable"):
                neg_status = STATUS_NO_PROVISION  # an option / discretion not used ("n.a." compliance or none)
            else:
                neg_status = status or ("not_applicable" if method == "n.a." else STATUS_NO_PROVISION)
            for e in eids or [""]:
                links.append(SkGoldLink(
                    eu_id=f"eu:{celex}:{eu_language}:{e}" if e else "", national_id="", status=neg_status,
                    note="; ".join(notes), negative=True, **common))
            continue
        acts = acts_in_cell(r.act)
        assigned, how = assign_acts(groups, acts, national_ref.count("\n") + 1, r.act.count("\n") + 1)
        last_art: int | None = None
        for g, mention in zip(groups, assigned):
            target, art_used, drop = _target(g, mention, final_act, bills, last_art)
            if mention is None or mention.act == BILL:
                last_art = art_used
            gnotes = list(notes)
            if g.qualifier:
                gnotes.append(f"SK ref qualifier: {g.qualifier}")
            if g.unparsed:
                gnotes.append(f"SK ref not fully parsed: {g.unparsed!r}")
            if mention is None:
                gnotes.append("no act named in the row: the bill assumed")
            nat_ids = []
            for leaf in g.leaves:
                if target.promulgated:
                    local = _national_local(leaf, drop_article=False)
                    if not leaf or leaf[0][0] != "art":
                        local = f"cl{art_used or 1}." + local if local else f"cl{art_used or 1}"
                else:
                    local = _national_local(leaf, drop_article=drop and len(leaf) > 1)
                if local and local not in nat_ids:
                    nat_ids.append(local)
            for e in eids or [""]:
                for c in nat_ids:
                    nid = f"sk:{target.act}:{c}"
                    links.append(SkGoldLink(
                        eu_id=f"eu:{celex}:{eu_language}:{e}" if e else "", national_id=nid,
                        status=status or "listed", bill_national_id=nid, note="; ".join(gnotes),
                        national_act=target.act, bill_article=target.bill_article,
                        act_version=("vyhlasene_znenie" if target.promulgated else
                                     f"amended_by:{target.amended_by}" if target.amended_by else ""),
                        act_assignment=how, **common))
    mark_partial_negatives(links)
    return links


#: A provision named at the start of a text cell whose reference cell is empty.
_REF_IN_TEXT = re.compile(r"^\s*((?:Príloha\s+č\.\s*\d+|§\s*\d+[a-z]?)(?:\s+(?:ods\.|O:)\s*\d+)?)\b")
PARTIAL_NEGATIVE = "partial_negative"


def mark_partial_negatives(links: Sequence[SkGoldLink]) -> None:
    """Flag negatives whose EU ID also has positive links (flag ``partial_negative``).

    Rows that split one EU paragraph by sentence or subparagraph (``V:2``, ``Pododsek 2``)
    give the same EU ID a link from one row and "nothing transposes it" from another: such a
    negative is about a part of the provision only, not a true negative for the whole ID.
    """
    pos = {lk.eu_id for lk in links if not lk.negative and lk.eu_id}
    for lk in links:
        if not lk.negative:
            continue
        if lk.eu_id in pos:
            if PARTIAL_NEGATIVE not in lk.flags:
                lk.flags.append(PARTIAL_NEGATIVE)
        elif PARTIAL_NEGATIVE in lk.flags:
            lk.flags.remove(PARTIAL_NEGATIVE)


# --- checking ------------------------------------------------------------------------------


def resolve_eu(eu_id: str, doc: Document) -> tuple[str, str]:
    """:func:`openacquis.evalset.resolve_eu_id` plus annex numbering (``anx1`` / ``anx_i``)."""
    rid, how = resolve_eu_id(eu_id, doc)
    if how != "unresolved":
        return rid, how
    m = re.match(r"^(?P<head>.*:)anx_?(?P<n>\w+?)(?P<rest>(?:\..*)?)$", eu_id)
    if m:
        n = m["n"]
        alts = []
        if n == "0":  # "Príloha" without a number: the directive's only annex
            alts += [f"{m['head']}anx1{m['rest']}", f"{m['head']}anx_i{m['rest']}"]
        elif n.isdigit():
            alts.append(f"{m['head']}anx_{int_to_roman(int(n))}{m['rest']}")
        elif roman_to_int(n):
            alts.append(f"{m['head']}anx{roman_to_int(n)}{m['rest']}")
        for alt in alts:
            if doc.get(alt) is not None:
                return alt, "fallback"
    # "Č:1, O:1" for an article without numbered paragraphs
    alt = re.sub(r"(:art\w+)\.par1(?=\.|$)", r"\1", eu_id)
    if alt != eu_id and doc.get(alt) is not None:
        return alt, "fallback"
    return eu_id, "unresolved"


def adjust_national_id(nid: str, doc: Document) -> str | None:
    """A known numbering difference for an ID missing from ``doc``, else ``None``.

    The tables write ``§:3, O:1, P:q`` for a § without numbered paragraphs (the adapter
    has ``par3.pism_q``), ``§ 2 P:a`` for letters that sit in its paragraph 1
    (``par2.ods1.pism_a``), ``P:5`` for a numbered point (``bod5``), and ``Príloha`` for
    the single unnumbered annex (``priloha0``). A paragraph cited above a letter of a § the
    act does not divide into paragraphs is dropped (``par5.ods9.pism_e`` -> ``par5.pism_e``).
    """
    if doc.get(nid) is not None:
        return None
    m = re.match(r"^(?P<head>.*:par\w+?)\.ods\d+(?P<rest>\..+)$", nid)
    if m and doc.get(m["head"]) is not None and not any(
            q.kind == "ods" for q in doc.children(m["head"])):
        # "§:5 O:9 P:e" for a § without odseky: the paragraph number is the bill's
        alt = m["head"] + m["rest"]
        if doc.get(alt) is not None:
            return alt
    cands = [
        re.sub(r"\.ods1(?=\.|$)", "", nid),
        re.sub(r"\.pism(\d+)(?=\.|$)", r".bod\1", nid),
        re.sub(r"\.ods1(?=\.|$)", "", re.sub(r"\.pism(\d+)(?=\.|$)", r".bod\1", nid)),
        re.sub(r"(:par\w+)(?=\.(?:pism|bod))", r"\1.ods1", nid),  # "§ 2 P:a" for a § whose letters sit in ods. 1
        re.sub(r":priloha0$", ":priloha1", nid),
        re.sub(r":priloha1$", ":priloha0", nid),
    ]
    for c in cands:
        if c != nid and doc.get(c) is not None:
            return c
    return None


def candidate_acts(link: SkGoldLink, row: SkRow | None, bills: dict[int, str | None], final_act: str) -> list[str]:
    """Other acts a link may belong to when its own act does not confirm it.

    The acts named in the row (when several acts share a row, groups and acts are paired
    by position, which can be wrong) and, for a link through an article of the bill, the
    acts of the neighbouring articles (the bill's articles are numbered as submitted;
    parliament may insert articles, so the promulgated act's numbering can shift).
    """
    out: list[str] = []
    if row is not None:
        out += [a.act for a in acts_in_cell(row.act) if a.act != BILL]
    if link.bill_article:
        n = roman_to_int(link.bill_article) or 0
        out += [a for k, a in sorted(bills.items()) if a and abs(k - n) <= 2 and a != final_act]
    return [a for i, a in enumerate(out) if a != link.national_act and a not in out[:i]]


def _retarget(lk: SkGoldLink, row: SkRow | None, docs: dict[str, Document], cands: list[str], accept: float,
              bills: dict[int, str | None] | None = None) -> None:
    """Move an unconfirmed link (or one renumbered inside its act) to the candidate act
    whose text at the same ID matches the table.

    The same ID in another act the row names is preferred over a renumbering inside the
    link's act when its text reaches ``accept`` (the cell quotes several acts, so the
    renumbering is a guess from text alone). ``bill_article`` and ``act_assignment`` are
    reset for the new act.
    """
    if row is None or not cands:
        return
    local = (lk.bill_national_id or lk.national_id).split(":", 2)[2]
    best = None
    for act in cands:
        doc = docs.get(act)
        if doc is None:
            continue
        nid = f"sk:{act}:{local}"
        nid = adjust_national_id(nid, doc) or nid
        p = doc.get(nid)
        if p is None:
            continue
        sc = max(text_score(doc, p, row.national_text),
                 sentence_support(" ".join(x for x in (p.heading, p.text) if x), row.national_text))
        if sc >= accept and (best is None or sc > best[0]):
            best = (sc, act, nid, doc)
    if best is None:
        return
    sc, act, nid, doc = best
    lk.note = _add_note(lk.note, f"act changed by text: {lk.national_act} -> {act}")
    if lk.national_check == "renumbered":
        lk.note = re.sub(r";? ?bill \S+ matches act \S+ by text", "", lk.note).strip("; ")
    lk.national_act, lk.national_id, lk.bill_national_id = act, nid, nid
    lk.national_check, lk.national_similarity = "same_number", round(sc, 3)
    n = next((k for k, a in sorted((bills or {}).items()) if a == act), None)
    lk.bill_article = _roman(n) if n is not None and lk.bill_article else ""
    lk.act_assignment = "text"
    lk.act_version = (doc.eli or doc.url or "").rstrip("/").rsplit("/", 1)[-1] or lk.act_version


def check_sk_links(
    links: list[SkGoldLink],
    eu_doc: Document | None,
    national_docs: dict[str, Document],
    rows: Sequence[SkRow] | None = None,
    accept: float = 0.6,
    margin: float = 0.2,
    bills: dict[int, str | None] | None = None,
    final_act: str = "",
) -> list[SkGoldLink]:
    """Verify IDs in place (see :func:`openacquis.evalset.check_links`).

    ``national_docs`` maps a key to the document a link is checked against: the act id
    (``"69/2018"``) for links into an act, ``"<act>@vyhlasene_znenie"`` for links into a
    promulgated amending act. Links whose act was not fetched stay ``not_checked``.
    A link its act does not confirm (``unresolved`` / ``unverified``) is moved to a
    :func:`candidate_acts` act whose text at the same ID matches the table (with ``bills``).
    """
    full = {r.index: r for r in rows} if rows is not None else {}
    if eu_doc is not None:
        for lk in links:
            if not lk.eu_id:
                continue
            rid, how = resolve_eu(lk.eu_id, eu_doc)
            if how == "fallback":
                lk.note = _add_note(lk.note, f"EU ID mapped {lk.eu_id.rsplit(':', 1)[1]} -> {rid.rsplit(':', 1)[1]}")
            lk.eu_id, lk.eu_check = rid, how
            if how != "unresolved":
                row = full.get(lk.row)
                lk.eu_similarity = round(text_score(eu_doc, eu_doc.by_id(rid), row.eu_text if row else lk.eu_text), 3)
    by_doc: dict[str, list[SkGoldLink]] = {}
    for lk in links:
        if lk.negative or not lk.national_id:
            continue
        key = f"{lk.national_act}@vyhlasene_znenie" if lk.act_version == "vyhlasene_znenie" else lk.national_act
        by_doc.setdefault(key, []).append(lk)
    for key, group in by_doc.items():
        doc = national_docs.get(key)
        if doc is None:
            continue
        for lk in group:
            sub = letter_subpoint(lk.bill_national_id, lk.raw_national_ref, doc)
            if sub:
                lk.note = _add_note(lk.note, f"ID adjusted {lk.bill_national_id.rsplit(':', 1)[1]} -> "
                                             f"{sub.rsplit(':', 1)[1]} (O: after a letter read as its point)")
                lk.bill_national_id = sub
                continue
            alt = adjust_national_id(lk.bill_national_id, doc)
            if alt:
                old_local, new_local = lk.bill_national_id.rsplit(":", 1)[1], alt.rsplit(":", 1)[1]
                lk.note = _add_note(lk.note, f"ID adjusted {old_local} -> {new_local}")
                lk.bill_national_id = alt
        check_links(group, None, doc, rows=rows, accept=accept, margin=margin)
        version = (doc.eli or doc.url or "").rstrip("/").rsplit("/", 1)[-1]
        for lk in group:
            lk.act_version = version or lk.act_version
            p = doc.get(lk.national_id)
            if p is not None and "PDF: " in (p.text or ""):
                # only a pointer to the PDF is in the text: a match with the table's own pointer
                # ("viď ... prílohou č. 1") confirms nothing
                lk.national_check, lk.national_similarity = "unverified", None
                lk.note = re.sub(r";? ?bill \S+ matches act \S+ by text", "", lk.note).strip("; ")
                lk.note = _add_note(lk.note, "published on Slov-Lex as PDF only: text not compared")
            elif lk.national_check in ("unresolved", "unverified", "renumbered") and bills is not None:
                row = full.get(lk.row)
                _retarget(lk, row, national_docs, candidate_acts(lk, row, bills, final_act), accept, bills)
    mark_partial_negatives(links)
    return links


def letter_subpoint(nid: str, raw_ref: str, doc: Document) -> str | None:
    """``par5.ods2`` -> ``par5.pism_f.bod2`` when the reference reads ``§: 5 P: f O: 2`` and the act has that point.

    The CER template writes the numbered points of a lettered point as ``O:``; the parser reads
    them as paragraphs (``O`` after ``P`` usually is the next paragraph: ``O: 1 P: b O: 3``).
    Used only when ``doc`` lacks the paragraph.
    """
    if doc.get(nid) is not None:
        return None
    head, _, local = nid.rpartition(":")
    m = re.match(r"^par(\w+?)\.ods(\d+)((?:\..*)?)$", local)
    if not m:
        return None
    par, n, rest = m.groups()
    # the nearest "P: x" before "O: n" within the same §, with only "O:" tokens between
    pat = (rf"§\s*:?\s*{re.escape(par)}\b(?:(?!§).)*?P\s*:\s*([a-z])\)?[\s,]+"
           rf"(?:O\s*:\s*\d+[\s,]+)*O\s*:\s*{n}(?!\d)")
    # "§: 5 P: f §: 5 O: 14": the § repeated before the point continues it
    ref = re.sub(rf"(§\s*:?\s*{re.escape(par)}\b(?:(?!§).)*?)\s*§\s*:?\s*{re.escape(par)}\b(?=[\s,]+O\s*:)",
                 r"\1", raw_ref, flags=re.S)
    hit = re.search(pat, ref, re.S)
    if not hit:
        return None
    cand = f"{head}:par{par}.pism_{hit.group(1)}.bod{n}{rest}"
    return cand if doc.get(cand) is not None else None


def _add_note(note: str, extra: str) -> str:
    return f"{note}; {extra}" if note else extra


def summarize_sk(links: Sequence[SkGoldLink], rows: Sequence[SkRow] = ()) -> dict:
    """:func:`openacquis.evalset.summarize` over the positive links, plus the label columns."""
    pos = [lk for lk in links if not lk.negative]
    neg = [lk for lk in links if lk.negative]
    s = summarize(pos)
    s["negatives"] = {
        "records": len(neg),
        "rows": len({lk.row for lk in neg}),
        "eu_ids": len({lk.eu_id for lk in neg if lk.eu_id}),
        "status": dict(Counter(lk.status for lk in neg).most_common()),
        "eu_check": dict(Counter(lk.eu_check for lk in neg).most_common()),
        PARTIAL_NEGATIVE: sum(PARTIAL_NEGATIVE in lk.flags for lk in neg),
    }
    confirmed = {lk.national_id for lk in pos if lk.national_id
                 and lk.national_check in ("same_number", "renumbered", "ancestor")}
    nat = {lk.national_id for lk in pos if lk.national_id}
    s["national_confirmed_share"] = round(len(confirmed) / len(nat), 4) if nat else None
    s["by_act"] = dict(Counter(lk.national_act for lk in pos).most_common())
    s["status"] = dict(Counter(lk.status for lk in pos).most_common())
    s["act_assignment"] = dict(Counter(lk.act_assignment for lk in pos).most_common())
    s["notes"] = {
        "renumbered": sum(1 for lk in pos if lk.national_check == "renumbered"),
        "id_adjusted": sum(1 for lk in pos if "ID adjusted" in lk.note),
        "qualifier": sum(1 for lk in pos if "qualifier" in lk.note),
    }
    if rows:
        s["table_rows"] = len(rows)
        used = {lk.row for lk in links}
        s["rows_without_records"] = [r.index for r in rows if r.index not in used]
        s["method"] = dict(Counter(r.method for r in rows).most_common())
        s["compliance"] = dict(Counter(r.compliance for r in rows).most_common())
        s["gold_plating"] = dict(Counter(r.gold_plating for r in rows).most_common())
    return s


def default_cache_dir() -> Path:
    """``$OPENACQUIS_CACHE/nrsr``, else ``~/.cache/openacquis/nrsr``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    base = Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"
    return base / "nrsr"


def read_sk_table(data: bytes) -> list[SkRow]:
    """Data rows of a tabuľka zhody ``.docx``."""
    if data[:2] != b"PK":
        raise ValueError("expected a .docx (zip) file")
    return sk_table_rows(docx_rows(data))
