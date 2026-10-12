import io
import json
import struct
import zipfile
from pathlib import Path

import pytest

from openacquis.adapters.cz import parse_fragments
from openacquis.eurlex import parse_html
from openacquis.evalset import (
    GoldLink,
    sentence_support,
    check_links,
    containment,
    cz_ref_to_ids,
    doc_rows,
    doc_text,
    docx_rows,
    celex_chains,
    download,
    eu_ref_to_ids,
    find_table_files,
    fix_celex,
    links_from_rows,
    load_jsonl,
    normalize_cz_ref,
    parse_cz_ref,
    parse_eu_ref,
    published_act,
    read_table,
    resolve_eu_id,
    save_jsonl,
    summarize,
    table_rows,
)
from openacquis.provisions import Document, Provision

FIXTURES = Path(__file__).parent / "fixtures"
DATA = Path(__file__).parent.parent / "data" / "eval"
NIS2 = "32022L2555"
TABLE_URL = "https://www.psp.cz/sqw/text/orig2.sqw?idd=244067"


# --- reference parsing ------------------------------------------------------------


@pytest.mark.parametrize(
    "ref, ids",
    [
        ("čl. 23 odst. 4 písm. a)", ["art23.par4.pt_a"]),
        ("Čl. 23 odst. 4 písm. a)", ["art23.par4.pt_a"]),
        ("Čl. 4", ["art4"]),
        ("Čl. 6 odst. 8\r\t", ["art6.par8"]),
        ("Čl. 8 odst. 1 až 3", ["art8.par1", "art8.par2", "art8.par3"]),
        ("Čl. 11 odst. 3 písm. a) až c)", ["art11.par3.pt_a", "art11.par3.pt_b", "art11.par3.pt_c"]),
        ("Čl. 32 odst. 4 písm. a), b), e) a h)",
         ["art32.par4.pt_a", "art32.par4.pt_b", "art32.par4.pt_e", "art32.par4.pt_h"]),
        ("Čl. 32 odst. 2 písm. e) a f) a odst. 4 písm. b)",
         ["art32.par2.pt_e", "art32.par2.pt_f", "art32.par4.pt_b"]),
        # a deeper level after a list qualifies only the last item
        ("Čl. 32 odst. 1 a 4 písm. e)", ["art32.par1", "art32.par4.pt_e"]),
        ("Čl. 6 odst. 1 a odst. 5", ["art6.par1", "art6.par5"]),
        ("Čl. 23 odst. 4 písm. d) bod ii)", ["art23.par4.pt_d.subpt_ii"]),
        ("Čl. 6 bod 5", ["art6.pt5"]),
        # inflected keywords are whole words, not "bod" + a letter read as a number
        ("čl. 2 odst. 2 písm. f) bodu ii)", ["art2.par2.pt_f.subpt_ii"]),
        ("čl. 21 body 1 a 2", ["art21.pt1", "art21.pt2"]),
        # a letter under a numbered (definition) point nests under it
        ("čl. 6 bod 5 písm. a)", ["art6.pt5.subpt_a"]),
        ("čl. 6 bod 5 písm. a) a b) a bod 6", ["art6.pt5.subpt_a", "art6.pt5.subpt_b", "art6.pt6"]),
        # single-character roman ranges of sub-points are roman, letter ranges of písmena stay letters
        ("čl. 2 odst. 2 písm. a) bod i) až v)", [f"art2.par2.pt_a.subpt_{x}" for x in ("i", "ii", "iii", "iv", "v")]),
        ("čl. 2 odst. 2 písm. a) bod v až x",
         [f"art2.par2.pt_a.subpt_{x}" for x in ("v", "vi", "vii", "viii", "ix", "x")]),
        ("čl. 2 písm. c) až d)", ["art2.pt_c", "art2.pt_d"]),
        ("příloha I", ["anx_i"]),
        ("Čl. 23 odst. 1 věta první, pátá a poslední", ["art23.par1"]),
        # indents ("odrážka čtvrtá"), chapeaux ("návětí") and annex sections (kept as qualifiers)
        ("Čl. 2 odst. 1 písm. b) bod ii) odrážka čtvrtá", ["art2.par1.pt_b.subpt_ii.ind4"]),
        ("Čl. 2 odst. 1 návětí", ["art2.par1"]),
        ("Příloha I oddíl VII první odstavec", ["anx_i"]),
        ("Příloha I Oddíl I bod 1", ["anx_i.pt1"]),
        # the level word repeated after "až" is still a range
        ("Čl. 5 odst. 1 až odst. 3", ["art5.par1", "art5.par2", "art5.par3"]),
    ],
)
def test_parse_eu_ref(ref, ids):
    assert eu_ref_to_ids(ref) == ids


@pytest.mark.parametrize(
    "ref, ids",
    [
        ("§ 12 odst. 3 písm. b)", ["par12.odst3.pism_b"]),
        ("§ 73 ", ["par73"]),
        ("§ 4a odst. 2", ["par4a.odst2"]),
        ("§ 15 odst. 1 a 2", ["par15.odst1", "par15.odst2"]),
        ("§ 17 odst. 1 až 3", ["par17.odst1", "par17.odst2", "par17.odst3"]),
        ("§ 7 písm. a) až c)", ["par7.pism_a", "par7.pism_b", "par7.pism_c"]),
        ("§ 4 odst. 1 písm. a) bod 1 až 3, 13 a 15 a písm. b)",
         ["par4.odst1.pism_a.bod1", "par4.odst1.pism_a.bod2", "par4.odst1.pism_a.bod3",
          "par4.odst1.pism_a.bod13", "par4.odst1.pism_a.bod15", "par4.odst1.pism_b"]),
        ("příloha č. 1", ["priloha1"]),
        ("Čl. II bod 3", ["cl2.bod3"]),
        ("§ 2 bodu 5", ["par2.bod5"]),
        ("§ 12 body 1 a 2", ["par12.bod1", "par12.bod2"]),
        ("Příloha č. 1 Oddíl I bod 1", ["priloha1.oddil1.bod1"]),
        ("příloha č. 2 oddíl 1 a oddíl 3 bod 2", ["priloha2.oddil1", "priloha2.oddil3.bod2"]),
        ("část 3 bod 1", ["cast3.bod1"]),
        ("§ 20 odst. 1 až odst. 3", ["par20.odst1", "par20.odst2", "par20.odst3"]),
        # a number after a list of letters is the next paragraph, never a letter "pism2"
        ("§ 11 odst. 1 písm. a) až e) a 2",
         ["par11.odst1.pism_a", "par11.odst1.pism_b", "par11.odst1.pism_c", "par11.odst1.pism_d",
          "par11.odst1.pism_e", "par11.odst2"]),
    ],
)
def test_parse_cz_ref(ref, ids):
    assert cz_ref_to_ids(ref) == ids


def test_inflected_part_keyword_is_not_split():
    r = parse_cz_ref("části první § 3")
    assert "cast_i.par3" not in r.ids and not r.ok  # reported as not understood, never a wrong ID


def test_parse_ref_qualifiers_and_errors():
    r = parse_eu_ref("Čl. 23 odst. 1 první pododstavec")
    assert r.ids == ["art23.par1"] and r.qualifier == "první pododstavec" and r.ok
    r = parse_cz_ref("§ 64 odst. 1 věta první až čtvrtá")
    assert r.ids == ["par64.odst1"] and r.qualifier == "věta první čtvrtá"
    bad = parse_eu_ref("viz důvodová zpráva")
    assert bad.ids == [] and not bad.ok and bad.unparsed
    assert parse_cz_ref("odst. 2").ids == []  # no § / článek: not a full reference
    q = parse_eu_ref("Příloha I oddíl VII první odstavec")
    assert q.qualifier == "oddíl VII první odstavec" and q.ok
    assert parse_eu_ref("Čl. 2 odst. 1 návětí").qualifier == "návětí"  # not the letter "n"


@pytest.mark.parametrize(
    "ref, parsed, flags",
    [
        ("ČÁST PRVNÍ\n§ 1 odst. 1 písm. a)\n", "§ 1 odst. 1 písm. a)", []),
        ("ČÁST SEDMÁ § 41 bod 1.", "§ 41 bod 1.", []),
        ("ČÁST X Účinnost (§ 42) ", "§ 42", []),
        ("ČÁST TŘETÍ bod 1.", "část 3 bod 1.", ["amendment_point"]),
        ("ČÁST DESÁTÁ", "část 10", ["part_ref"]),
        ("§ 12 odst. 3", "§ 12 odst. 3", []),
    ],
)
def test_normalize_cz_ref_parts(ref, parsed, flags):
    out, fl, _ = normalize_cz_ref(ref)
    assert (out, fl) == (parsed, flags)


def test_fix_celex_and_chains():
    known = {"32013L0034", "32022L2464", "32006L0043", "32022L2523"}
    assert fix_celex("32019L1937") == ("32019L1937", "")
    assert fix_celex("02006L0043")[0] == "32006L0043"  # consolidated-text prefix
    assert fix_celex("32013L32022L2464", known)[0] == "32022L2464"
    assert fix_celex("23022L2464", known)[0] == "32022L2464"
    assert fix_celex("32022L246", known)[0] == "32022L2464"
    assert fix_celex("32013L004", known)[0] == "32013L0034"
    # well-formed numbers are never "corrected": NIS2 and CER differ by one character
    assert fix_celex("32022L2557", {"32022L2555"}) == ("32022L2557", "")
    assert fix_celex("1234X", known)[0] is None
    assert celex_chains("32019L1937\r\r32019L1937\r")[0] == [["32019L1937"]]
    chains, _ = celex_chains("\t32013L0034 ve znění 32021L2101 ve znění 32023L2775")
    assert chains == [["32013L0034", "32021L2101", "32023L2775"]]


# --- reading Word files -------------------------------------------------------------


def _cfb(streams: dict[str, bytes]) -> bytes:
    """Minimal OLE compound file (512-byte sectors, one FAT sector) holding ``streams``."""
    ssz, end, free = 512, 0xFFFFFFFE, 0xFFFFFFFF
    data_secs, fat, start = [], [0xFFFFFFFD, end], {}  # sector 0 = FAT, 1 = directory
    for name, body in streams.items():
        body = body + b"\0" * (-len(body) % ssz)
        n = len(body) // ssz
        start[name] = 2 + len(data_secs)
        for k in range(n):
            data_secs.append(body[k * ssz:(k + 1) * ssz])
            fat.append(start[name] + k + 1 if k < n - 1 else end)
    fat += [free] * (ssz // 4 - len(fat))

    def entry(name: str, typ: int, sec: int, size: int) -> bytes:
        raw = name.encode("utf-16-le") + b"\0\0"
        e = bytearray(128)
        e[:len(raw)] = raw
        struct.pack_into("<H", e, 64, len(raw))
        e[66] = typ
        struct.pack_into("<III", e, 68, free, free, free)
        struct.pack_into("<IQ", e, 116, sec, size)
        return bytes(e)

    dir_sec = entry("Root Entry", 5, end, 0) + b"".join(
        entry(n, 2, start[n], len(b)) for n, b in streams.items()
    )
    dir_sec += b"\0" * (ssz - len(dir_sec))
    hdr = bytearray(512)
    hdr[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", hdr, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", hdr, 44, 1)  # FAT sectors
    struct.pack_into("<I", hdr, 48, 1)  # first directory sector
    struct.pack_into("<III", hdr, 56, 4096, end, 0)
    struct.pack_into("<II", hdr, 68, end, 0)
    struct.pack_into("<109I", hdr, 76, 0, *([free] * 108))
    return bytes(hdr) + struct.pack(f"<{ssz // 4}I", *fat) + dir_sec + b"".join(data_secs)


def _word97(text: str) -> bytes:
    """A .doc whose piece table points at ``text`` stored as UTF-16 at offset 0x800."""
    fc = 0x800
    wd = bytearray(fc) + text.encode("utf-16-le")
    wd += b"\0" * max(0, 4096 - len(wd))
    struct.pack_into("<H", wd, 0x0A, 0x0200)  # table stream = 1Table
    plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, fc, 0)
    clx = b"\x01" + struct.pack("<H", 2) + b"\0\0" + b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<II", wd, 0x01A2, 0, len(clx))
    table = clx + b"\0" * (4096 - len(clx))
    return _cfb({"WordDocument": bytes(wd), "1Table": table})


def test_doc_text_reads_piece_table_and_fields():
    text = "Titul\r§ 1\x07Text \x13 PAGE \x143\x15 a\x0bdál\x07\x07"
    out = doc_text(_word97(text))
    assert out == "Titul\r§ 1\x07Text 3 a\ndál\x07\x07"


def test_doc_rows_and_read_table_from_doc():
    cells = ["§ 1 odst. 1", "(1) Tento zákon", NIS2, "Čl. 1 odst. 1", "1. Touto směrnicí"]
    text = "Nadpis\rNavrhovaný\x07Odpovídající\x07\x07" + "\x07".join(cells) + "\x07\x07"
    text += "\x07".join(["", "", NIS2, "Čl. 1 odst. 2", "2. Za tímto účelem"]) + "\x07\x07"
    rows = read_table(_word97(text))
    assert rows[0] == ["Navrhovaný", "Odpovídající"]  # text before the table dropped
    assert rows[1] == cells
    assert rows[2][:4] == ["", "", NIS2, "Čl. 1 odst. 2"]
    tr = table_rows(rows)
    assert [(r.national_ref, r.eu_ref) for r in tr] == [("§ 1 odst. 1", "Čl. 1 odst. 1"), ("§ 1 odst. 1", "Čl. 1 odst. 2")]
    assert tr[1].national_text == "(1) Tento zákon"  # merged cell carried down


def _docx(rows: list[list[str | None]]) -> bytes:
    """``None`` = cell continuing a vertical merge."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    trs = []
    for r in rows:
        tcs = []
        for c in r:
            if c is None:
                tcs.append("<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>")
            else:
                ps = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in c.split("\n"))
                tcs.append(f"<w:tc>{ps}</w:tc>")
        trs.append("<w:tr>" + "".join(tcs) + "</w:tr>")
    xml = f'<w:document xmlns:w="{w}"><w:body><w:tbl>{"".join(trs)}</w:tbl></w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_docx_rows_with_vertical_merge_and_compatibility():
    data = _docx([
        ["Ustanovení", "Obsah", "Celex č.", "Ustanovení", "Obsah", "Kompatibilita"],
        ["§ 16 odst. 1", "(1) Poskytovatel\nhlásí", NIS2, "Čl. 23 odst. 4 písm. a)", "a) včasné varování", "plně slučitelné"],
        [None, None, NIS2, "Čl. 23 odst. 1", "1. Členské státy", "Plně slučitelné"],
    ])
    rows = read_table(data)
    assert rows[1][1] == "(1) Poskytovatel\nhlásí" and rows[2][0] == ""
    assert rows == docx_rows(data)
    tr = table_rows(rows)
    assert [r.compatibility for r in tr] == ["fully_compatible", "fully_compatible"]
    links = links_from_rows(tr, "264/2025", source_url="x")
    assert [(lk.eu_id, lk.national_id, lk.status) for lk in links] == [
        ("eu:32022L2555:cs:art23.par4.pt_a", "cz:264/2025:par16.odst1", "fully_compatible"),
        ("eu:32022L2555:cs:art23.par1", "cz:264/2025:par16.odst1", "fully_compatible"),
    ]


def test_read_table_rejects_other_files():
    with pytest.raises(ValueError):
        read_table(b"%PDF-1.7")


# --- the real table (excerpt) -----------------------------------------------------------


@pytest.fixture(scope="module")
def excerpt():
    return json.loads((FIXTURES / "eval_t0759c0_excerpt.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def nis2_cs():
    html = (FIXTURES / f"eurlex_{NIS2}_cs.xhtml").read_text(encoding="utf-8")
    return parse_html(html, NIS2, "cs")  # Art. 2, 6, 23, 46 only


@pytest.fixture(scope="module")
def act_264():
    frags = []
    for p in (0, 1):
        frags += json.loads((FIXTURES / f"cz_264_2025_fragmenty_p{p}.json").read_text(encoding="utf-8"))["seznam"]
    meta = json.loads((FIXTURES / "cz_264_2025_meta.json").read_text(encoding="utf-8"))
    return parse_fragments(frags, meta)  # trimmed: § 1, 2, 4, 71-73


def test_excerpt_rows(excerpt):
    rows = doc_rows(excerpt["text"])
    assert rows[0] == ["Navrhovaný právní předpis", "Odpovídající předpis EU"]
    tr = table_rows(rows, celex=NIS2)
    assert len(tr) == 11
    assert [r.index for r in tr] == list(range(11))
    assert tr[1].national_ref == "§ 1 odst. 1" and tr[1].eu_ref == "Čl. 1 odst. 2"
    assert tr[6].national_ref == "§ 4 odst. 1 písm. a) bod 1 až 11, 13 a 15 a písm. b)"
    assert tr[7].national_ref == tr[6].national_ref  # merged cell
    assert tr[-1].national_ref == "§ 73" and tr[-1].eu_ref == "Čl. 41 odst. 1"
    assert tr[3].national_text.startswith("(1) Pro účely tohoto zákona se rozumí")


def test_excerpt_links_and_checks(excerpt, nis2_cs, act_264):
    tr = table_rows(doc_rows(excerpt["text"]), celex=NIS2)
    links = links_from_rows(tr, "264/2025", source_url=TABLE_URL)
    assert len(links) == 52
    assert all(lk.status == "listed" and lk.source_url == TABLE_URL for lk in links)
    check_links(links, nis2_cs, act_264, rows=tr)

    by = {(lk.row, lk.eu_id.rsplit(":", 1)[1], lk.national_id.rsplit(":", 1)[1]): lk for lk in links}
    # definitions: the table writes "Čl. 6 odst. 1" for Art. 6 point (1)
    d = by[(3, "art6.pt1", "par2.odst1.pism_c")]
    assert d.eu_check == "fallback" and "art6.par1 -> art6.pt1" in d.note
    assert d.national_check == "same_number" and d.national_similarity >= 0.9
    assert d.eu_similarity >= 0.9
    # same numbering in bill and act, verified by text
    b = by[(5, "art2.par5.pt_a", "par4.odst1.pism_a.bod1")]
    assert (b.eu_check, b.national_check) == ("exact", "same_number")
    # not in the trimmed fixtures
    assert by[(0, "art1.par1", "par1.odst1")].eu_check == "unresolved"
    assert by[(9, "art2.par3", "par5.odst1.pism_d")].national_check == "unresolved"

    s = summarize(links)
    assert s["links"] == 52 and s["rows"] == 11
    assert s["eu_check"] == {"exact": 6, "fallback": 1, "unresolved": 5}
    assert s["national_unresolved"] == ["cz:264/2025:par2.odst3.pism_g", "cz:264/2025:par5.odst1.pism_d"]


def test_renumbered_provision_is_found_by_text():
    # the bill had an extra letter, so its písm. u) is písm. t) of the act
    act = Document("cz", "1/2025", "cs")
    act.add(Provision("cz:1/2025:par42", "cz", "1/2025", "cs", "par", "§ 42", "42"))
    act.add(Provision("cz:1/2025:par42.odst3", "cz", "1/2025", "cs", "odst", "(3)", "3", "Úřad",
                      parent_id="cz:1/2025:par42"))
    letters = {
        "s": "uzavírá veřejnoprávní smlouvu s provozovatelem národního týmu",
        "t": "provádí kontrolu plnění povinností podle tohoto zákona a ukládá nápravná opatření,",
        "u": "ukládá správní tresty za nedodržení povinností stanovených tímto zákonem,",
    }
    for k, v in letters.items():
        act.add(Provision(f"cz:1/2025:par42.odst3.pism_{k}", "cz", "1/2025", "cs", "pism", f"{k})", k, v,
                          parent_id="cz:1/2025:par42.odst3"))
    link = GoldLink("", "cz:1/2025:par42.odst3.pism_u", bill_national_id="cz:1/2025:par42.odst3.pism_u",
                    national_text="(3) Úřad u) provádí kontrolu plnění povinností podle tohoto zákona a ukládá nápravná opatření,")
    same = GoldLink("", "cz:1/2025:par42.odst3.pism_s", bill_national_id="cz:1/2025:par42.odst3.pism_s",
                    national_text="(3) Úřad s) uzavírá veřejnoprávní smlouvu s provozovatelem národního týmu")
    gone = GoldLink("", "cz:1/2025:par42.odst3.pism_x", bill_national_id="cz:1/2025:par42.odst3.pism_x",
                    national_text="(3) Úřad x) vede seznam")
    check_links([link, same, gone], national_doc=act)
    assert (link.national_id, link.national_check) == ("cz:1/2025:par42.odst3.pism_t", "renumbered")
    assert "bill par42.odst3.pism_u matches act par42.odst3.pism_t" in link.note
    assert link.bill_national_id.endswith("pism_u")
    assert (same.national_check, same.national_similarity) == ("same_number", 1.0)
    assert gone.national_check == "unresolved" and gone.national_id.endswith("pism_x")


def test_resolve_eu_id_fallback_to_points():
    eu = Document("eu", NIS2, "cs")
    for pid, kind in (("art6", "article"), ("art6.pt1", "point"), ("art6.pt1.subpt_a", "subpoint")):
        parent = pid.rsplit(".", 1)[0] if "." in pid else None
        eu.add(Provision(f"eu:{NIS2}:cs:{pid}", "eu", NIS2, "cs", kind, "", "1", "x",
                         parent_id=f"eu:{NIS2}:cs:{parent}" if parent else None))
    assert resolve_eu_id(f"eu:{NIS2}:cs:art6.par1", eu) == (f"eu:{NIS2}:cs:art6.pt1", "fallback")
    assert resolve_eu_id(f"eu:{NIS2}:cs:art6.par1.pt_a", eu) == (f"eu:{NIS2}:cs:art6.pt1.subpt_a", "fallback")
    assert resolve_eu_id(f"eu:{NIS2}:cs:art6.pt1", eu)[1] == "exact"
    assert resolve_eu_id(f"eu:{NIS2}:cs:art7.par1", eu)[1] == "unresolved"


def test_containment():
    assert containment("ukládá správní tresty", "(3) Úřad u) ukládá správní tresty, a") == 1.0
    assert containment("ukládá správní tresty", "nic společného") == 0.0
    assert containment("", "x") == 0.0


# --- other table templates (excerpts of real tables) ----------------------------------------------


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_multi_celex_cells_in_doc():
    # Whistleblower bill (t0352a0.doc): CELEX cells list the act once per EU ref line, and a
    # continuation row (empty bill cells) has such a cell, which used to break the row grouping
    rows = doc_rows(_fixture("eval_t0352a0_excerpt.json")["text"])
    tr = table_rows(rows, celex="32019L1937")
    assert [r.national_ref for r in tr] == [
        "§ 1 odst. 1 písm. a) až c)", "§ 21 odst. 1 až 3", "§ 21 odst. 1 až 3", "§ 22 odst. 1 až odst. 3"]
    assert tr[2].eu_ref.startswith("Článek 5 odst. 6") and tr[2].national_text == tr[1].national_text
    links = links_from_rows(tr[1:2], "171/2023")
    assert {lk.eu_id.rsplit(":", 1)[1] for lk in links} == {"art12.par1.pt_a", "art12.par1.pt_b", "art16.par1", "art18.par1"}
    assert len(links) == 4 * 3  # 4 EU IDs x § 21 odst. 1, 2, 3
    assert sorted({lk.national_id.rsplit(":", 1)[1] for lk in links_from_rows(tr[3:4], "171/2023")}) == [
        "par22.odst1", "par22.odst2", "par22.odst3"]  # "odst. 1 až odst. 3": the middle one too


def test_four_column_template_with_celex_in_header():
    # CER bill (t0947d0.docx): no CELEX column, "CELEX 32022L2557" in the header, "ČÁST" prefixes
    tr = table_rows(_fixture("eval_t0947d0_excerpt.json")["rows"], celex="32022L2557")
    assert len(tr) == 12 and all(r.celex == "32022L2557" for r in tr)
    assert table_rows(_fixture("eval_t0947d0_excerpt.json")["rows"], celex="32022L2555") == []
    links = links_from_rows(tr, "266/2025")
    got = {(lk.national_id.split(":", 2)[2], lk.eu_id.split(":", 3)[3]): lk for lk in links}
    assert ("par1.odst1.pism_a", "art26.par2") in got  # two EU refs in one cell
    assert ("par2.pism_a", "art2.pt5") in got
    assert ("par5.pism_a.bod1", "art9.par1") in got
    assert ("par42", "art26.par1") in got  # "ČÁST X Účinnost (§ 42)"
    amend = got[("cast3.bod1", "art1.par1.pt_a")]
    assert amend.flags == ["amendment_point"] and amend.raw_national_ref == "ČÁST TŘETÍ bod 1."
    assert got[("cast10", "art1.par1.pt_a")].flags == ["part_ref"]
    assert not summarize(links)["unparsed_refs"]


def test_sections_consolidated_refs_and_typos():
    # CSRD bill (t0783b0.docx): sections per national act, "2013/34 ve znění 2022/2464", typos
    rows = _fixture("eval_t0783b0_excerpt.json")["rows"]
    tr = table_rows(rows, celex="32022L2464", section_acts={"o auditorech": "93/2009"})
    assert [(r.national_act, r.eu_act) for r in tr] == [
        ("563/1991", ""), ("563/1991", "32013L0034"), ("563/1991", "32013L0034"), ("93/2009", "32006L0043")]
    assert all("celex_typo" in r.flags for r in tr)
    links = links_from_rows(tr, "563/1991")
    cons = [lk for lk in links if lk.eu_act]
    assert cons and all(lk.eu_id.startswith("eu:32013L0034:cs:") or lk.eu_id.startswith("eu:32006L0043:cs:")
                        for lk in cons)
    assert all(lk.celex == "32022L2464" and "consolidated" in lk.flags for lk in cons)
    aud = [lk for lk in links if lk.national_id.startswith("cz:93/2009:")]
    assert aud and all("section_act" in lk.flags for lk in aud)
    # without a celex filter every row keeps its own act; the 416/2023 section is read from its label
    every = table_rows(rows)
    assert [r.celex for r in every][:3] == ["32023L2775", "32023L2775", "32013L0034"]
    assert every[-1].celex == "32022L2523" and every[-1].national_act == "416/2023"
    assert every[-2].national_act == ""  # auditors' section: no act number in its label


def _act(act: str, provs: list[tuple[str, str, str | None, str]]) -> Document:
    """A national document from ``(local id, kind, heading, text)``; parents follow the IDs."""
    d = Document("cz", act, "cs")
    for local, kind, heading, text in provs:
        parent = None
        if "." in local:
            parent = f"cz:{act}:{local.rsplit('.', 1)[0]}"
        elif kind == "par" and d.roots():
            parent = d.roots()[-1].id if d.roots()[-1].kind == "cast" else None
        d.add(Provision(f"cz:{act}:{local}", "cz", act, "cs", kind, local, local[-1], text, heading,
                        parent_id=parent))
    return d


def test_part_references_are_matched_by_text():
    # bill parts are offset against the act (a part was dropped); a part with one change has no points
    act = _act("9/2025", [
        ("cast1", "cast", None, ""),
        ("par1", "par", None, "Tento zákon upravuje odolnost subjektů kritické infrastruktury."),
        ("cast2", "cast", "Změna zákona o působnosti Správy státních hmotných rezerv", ""),
        ("par31", "par", None, "Zákon č. 97/1993 Sb. se mění takto:"),
        ("par31.bod1", "bod", None, "V § 4b odst. 1 se za slovo „situace“ vkládají slova „, k zachování poskytování základní služby“."),
        ("par31.bod2", "bod", None, "V § 4b odst. 2 se za slovo „systému“ vkládají slova „, subjekt kritické infrastruktury“."),
        ("cast3", "cast", "Změna zákona o zeměměřictví", ""),
        ("par32", "par", None, "V § 5 odst. 1 zákona č. 200/1994 Sb., o zeměměřictví, se slova „ochrany kritické "
                                "infrastruktury podle krizového zákona“ nahrazují slovy „připravenosti na krizové situace“."),
    ])
    rows = [["", "", "", ""],
            ["Ustanovení", "Obsah", "CELEX 32022L2557", "Ustanovení"],
            ["ČÁST TŘETÍ bod 2.", "2. V § 4b odst. 2 se za slovo „systému“ vkládají slova „, subjekt kritické infrastruktury“.",
             "Čl. 1 odst. 1 písm. a)", "a) stanoví"],
            ["ČÁST ČTVRTÁ bod 1.", "1. § 5 odst. 1 se slova „ochrany kritické infrastruktury podle krizového zákona“ "
             "nahrazují slovy „připravenosti na krizové situace“.", "Čl. 1 odst. 1 písm. a)", "a) stanoví"],
            ["ČÁST PÁTÁ bod 2.", "2. V § 11 odst. 1 písm. f) se slova „ochranu kritické infrastruktury“ zrušují.",
             "Čl. 1 odst. 1 písm. a)", "a) stanoví"]]
    tr = table_rows(rows, celex="32022L2557")
    links = check_links(links_from_rows(tr, "9/2025"), national_doc=act, rows=tr)
    got = [(lk.bill_national_id.split(":")[2], lk.national_id.split(":")[2], lk.national_check) for lk in links]
    assert got == [("cast3.bod2", "par31.bod2", "renumbered"), ("cast4.bod1", "par32", "renumbered"),
                   ("cast5.bod2", "cast5.bod2", "unresolved")]
    assert "matches act par31.bod2 (part 2) by text" in links[0].note


def test_paragraph_level_added_ancestor_and_ties():
    act = _act("8/2025", [
        ("par2", "par", None, ""),
        ("par2.odst1", "odst", None, "Pro účely tohoto zákona se rozumí"),
        ("par2.odst1.pism_a", "pism", None, "základní službou služba nezbytná pro zachování důležitých funkcí,"),
        ("par2.odst1.pism_b", "pism", None, "kritickou infrastrukturou aktivum nebo systém,"),
        ("par28", "par", None, "Přestupky podle tohoto zákona projednává ministerstvo."),
        ("par15", "par", None, "Ministerstvo"),
        ("par15.pism_a", "pism", None, "vede seznam,"),
        ("par15.pism_b", "pism", None, "vydává pokyny,"),
    ])
    level = GoldLink("", "cz:8/2025:par2.pism_b", bill_national_id="cz:8/2025:par2.pism_b",
                     national_text="b) kritickou infrastrukturou aktivum nebo systém,")
    anc = GoldLink("", "cz:8/2025:par28.pism_a", bill_national_id="cz:8/2025:par28.pism_a",
                   national_text="Přestupky podle tohoto zákona projednává ministerstvo.")
    tie = GoldLink("", "cz:8/2025:par15.pism_j", bill_national_id="cz:8/2025:par15.pism_j",
                   national_text="Ministerstvo a) vede seznam, b) vydává pokyny,")
    check_links([level, anc, tie], national_doc=act)
    assert (level.national_id, level.national_check) == ("cz:8/2025:par2.odst1.pism_b", "renumbered")
    assert (anc.national_id, anc.national_check) == ("cz:8/2025:par28", "ancestor") and "parent par28" in anc.note
    # the quote supports every letter equally: no letter is picked, the quoted § is used
    assert (tie.national_id, tie.national_check) == ("cz:8/2025:par15", "ancestor")
    assert summarize([level, anc])["national_resolved_share"] == 1.0


def _row_links(act: str, row: int, ids: list[str], quote: str) -> list[GoldLink]:
    return [GoldLink("", f"cz:{act}:{i}", row=row, bill_national_id=f"cz:{act}:{i}", national_text=quote)
            for i in ids]


def test_renumbering_never_lands_on_another_id_of_the_row():
    # Whistleblower § 21: the row quotes odst. 1-3 of the bill; the act added words to odst. 2,
    # whose own text then falls below the threshold, while odst. 3 is fully in the quote
    act = _act("171/2023", [
        ("par21", "par", None, ""),
        ("par21.odst1", "odst", None, "Povinný subjekt vede v elektronické podobě evidenci údajů o přijatých oznámeních."),
        ("par21.odst2", "odst", None, "Příslušná osoba je povinna uchovávat oznámení podané prostřednictvím vnitřního "
                                      "oznamovacího systému a dokumenty související s oznámením po dobu 5 let ode dne "
                                      "přijetí oznámení a ministerstvo stejně tak u oznámení jemu podaných."),
        ("par21.odst3", "odst", None, "Do evidence podle odstavce 1 a k uchovávaným oznámením má přístup pouze "
                                      "příslušná osoba."),
    ])
    quote = ("(1) Povinný subjekt vede v elektronické podobě evidenci údajů o přijatých oznámeních. (2) Příslušná "
             "osoba je povinna uchovávat oznámení podané prostřednictvím vnitřního oznamovacího systému. (3) Do "
             "evidence podle odstavce 1 a k uchovávaným oznámením má přístup pouze příslušná osoba.")
    links = _row_links("171/2023", 36, ["par21.odst1", "par21.odst2", "par21.odst3"], quote)
    check_links(links, national_doc=act)
    got = {lk.bill_national_id.rsplit(":", 1)[1]: (lk.national_id.rsplit(":", 1)[1], lk.national_check)
           for lk in links}
    assert got["par21.odst2"] == ("par21.odst2", "unverified")
    assert got["par21.odst3"] == ("par21.odst3", "same_number")
    assert len({lk.national_id for lk in links}) == 3
    s = summarize(links)
    assert sum(s["national_check"].values()) == s["national_ids"] == 3


def test_sentence_support_confirms_an_id_in_a_long_quote():
    act = _act("1/2025", [
        ("par29", "par", None, ""),
        ("par29.odst1", "odst", None, "Úřad vede registr poskytovatelů, jejich provozoven, odpovědných osob, "
                                      "kontaktních údajů a poskytovaných služeb včetně jejich změn a zániku. "
                                      "Registr je neveřejný a údaje z něj lze poskytnout pouze orgánům činným v "
                                      "trestním řízení. Podrobnosti o vedení registru stanoví prováděcí právní předpis "
                                      "vydaný ministerstvem po projednání s dotčenými ústředními správními úřady."),
        ("par29.odst2", "odst", None, "Úřad zveřejní seznam poskytovatelů na svých internetových stránkách."),
    ])
    quote = ("Registr je neveřejný a údaje z něj lze poskytnout pouze orgánům činným v trestním řízení. "
             + "Další text jiných ustanovení, který se netýká registru, je zde citován v plné délce. " * 4)
    (lk,) = _row_links("1/2025", 1, ["par29.odst1"], quote)
    check_links([lk], national_doc=act)
    assert (lk.national_id, lk.national_check) == ("cz:1/2025:par29.odst1", "same_number")
    assert "whole sentence" in lk.note
    # words in order but not contiguous do not count: formulaic sentences recur
    assert sentence_support("O rozhodnutí podle § 20 odst. 3 orgán dozoru neprodleně informuje Evropskou komisi",
                            "orgán dozoru zakáže výrobek a neprodleně o tom informuje Evropskou komisi") < 0.9


def test_letters_are_aligned_when_the_act_inserted_a_letter():
    # Whistleblower § 2 odst. 1: the act inserted b), so the bill's b) and c) are the act's c) and d)
    act = _act("171/2023", [
        ("par2", "par", None, ""),
        ("par2.odst1", "odst", None, "Oznámení obsahuje informace o možném protiprávním jednání, které"),
        ("par2.odst1.pism_a", "pism", None, "má znaky trestného činu,"),
        ("par2.odst1.pism_b", "pism", None, "má znaky přestupku, za který zákon stanoví sazbu pokuty alespoň 100 000 Kč,"),
        ("par2.odst1.pism_c", "pism", None, "porušuje tento zákon, nebo"),
        ("par2.odst1.pism_d", "pism", None, "porušuje jiný právní předpis nebo předpis Evropské unie v oblasti"),
    ])
    quote = ("(1) Oznámení obsahuje informace o možném protiprávním jednání, které a) má znaky trestného činu, "
             "nebo b) porušuje tento zákon, jiný právní předpis nebo předpis Evropské unie v oblasti")
    links = _row_links("171/2023", 1, ["par2.odst1.pism_a", "par2.odst1.pism_b", "par2.odst1.pism_c"], quote)
    check_links(links, national_doc=act)
    assert [(lk.national_id.rsplit(".", 1)[1], lk.national_check) for lk in links] == [
        ("pism_a", "same_number"), ("pism_c", "renumbered"), ("pism_d", "renumbered")]
    assert "letters of the list aligned" in links[2].note


def test_collision_takes_the_same_shift():
    # two points of a row matched the same act point; the second takes the first one's shift
    act = _act("125/2006", [
        ("par2", "par", None, ""),
        ("par2.odst1", "odst", None, "Na účely tohoto zákona sa rozumie"),
        ("par2.odst1.pism_a", "pism", None, "diskrimináciou"),
        ("par2.odst1.pism_a.bod8", "bod", None, "pri zverejňovaní informácií o odmeňovaní zamestnancov a o kritériách"),
        ("par2.odst1.pism_a.bod9", "bod", None, "pri zverejňovaní ponuky zamestnania"),
    ])
    quote = "diskrimináciou 8. pri zverejňovaní informácií o odmeňovaní zamestnancov a o kritériách 9. pri zverejňovaní ponuky zamestnania"
    links = _row_links("125/2006", 67, ["par1.odst1.pism_a.bod8", "par1.odst1.pism_a.bod9"], quote)
    from openacquis.evalset import _undo_collisions
    # simulate the text match of both onto bod8
    for lk in links:
        lk.national_id, lk.national_check = "cz:125/2006:par2.odst1.pism_a.bod8", "renumbered"
    links[0].national_similarity, links[1].national_similarity = 1.0, 0.9
    _undo_collisions([(lk, act, quote) for lk in links])
    assert [lk.national_id.rsplit(":", 1)[1] for lk in links] == ["par2.odst1.pism_a.bod8", "par2.odst1.pism_a.bod9"]
    assert "renumbered like" in links[1].note


def test_check_links_uses_the_document_of_each_act():
    cer = "32022L2464"
    base = Document("eu", "32013L0034", "cs")
    base.add(Provision("eu:32013L0034:cs:art19a", "eu", "32013L0034", "cs", "article", "Článek 19a", "19a",
                       "Velké podniky zahrnou do zprávy vedení informace o udržitelnosti."))
    target = Document("eu", cer, "cs")
    lk = GoldLink("eu:32013L0034:cs:art19a", "", celex=cer, eu_act="32013L0034", flags=["consolidated"],
                  eu_text="Velké podniky zahrnou do zprávy vedení informace o udržitelnosti.")
    check_links([lk], eu_doc=target)  # the target's text cannot check a consolidated ref
    assert lk.eu_check == "not_checked" and "not checked" in lk.note
    check_links([lk], eu_doc=target, eu_docs=[base])
    assert lk.eu_check == "exact" and lk.eu_similarity == 1.0
    other = _act("93/2009", [("par2", "par", None, "Pro účely tohoto zákona se rozumí auditorem fyzická osoba.")])
    main = _act("563/1991", [("par2", "par", None, "Účetní jednotky vedou účetnictví.")])
    sec = GoldLink("", "cz:93/2009:par2", bill_national_id="cz:93/2009:par2", flags=["section_act"],
                   national_text="Pro účely tohoto zákona se rozumí auditorem fyzická osoba.")
    check_links([sec], national_doc=main)
    assert sec.national_check == "not_checked"  # never checked against the wrong act
    check_links([sec], national_doc=main, national_docs=[other])
    assert sec.national_check == "same_number"


def test_new_fields_are_written_only_when_set():
    plain = GoldLink("eu:x:cs:art1", "cz:1/2025:par1")
    assert "flags" not in plain.to_dict() and "eu_act" not in plain.to_dict()
    flagged = GoldLink("eu:x:cs:art1", "cz:1/2025:cast3.bod1", flags=["amendment_point"])
    assert flagged.to_dict()["flags"] == ["amendment_point"]
    assert GoldLink.from_dict(flagged.to_dict()) == flagged


def test_find_table_on_psp_pages():
    files = find_table_files((FIXTURES / "eval_psp_tiskt_9_947.html").read_bytes())
    assert [f["name"] for f in files] == ["t0947d0.docx", "t0947d0.pdf"]
    assert files[0]["url"] == "https://www.psp.cz/sqw/text/orig2.sqw?idd=253085"
    assert find_table_files("<html>Návrh zákona</html>") == []
    hist = "Zákon vyhlášen 4. 8. 2025 ve Sbírce zákonů v částce 266 pod číslem <a href='x'>266/2025 Sb.</a>"
    assert published_act(hist.encode("cp1250")) == "266/2025"
    assert published_act("Návrh zákona rozeslán poslancům") is None


# --- storage and download --------------------------------------------------------------------


def test_jsonl_roundtrip(tmp_path):
    links = [
        GoldLink(f"eu:{NIS2}:cs:art23.par4.pt_a", "cz:264/2025:par16.odst1", raw_eu_ref="Čl. 23 odst. 4 písm. a)",
                 raw_national_ref="§ 16 odst. 1", source_url=TABLE_URL, row=132, eu_similarity=1.0),
        GoldLink(f"eu:{NIS2}:cs:art23.par1", "cz:264/2025:par16.odst1", note="EU ref qualifier: věta třetí"),
    ]
    path = tmp_path / "gold.jsonl"
    assert save_jsonl(links, path) == 2
    assert load_jsonl(path) == links
    line = path.read_text(encoding="utf-8").splitlines()[0]
    assert "písm." in line  # UTF-8, not escaped
    assert GoldLink.from_dict({**links[0].to_dict(), "unknown_field": 1}) == links[0]


def test_download_uses_cache(tmp_path):
    calls = []

    def get(url):
        calls.append(url)
        return b"DOC"

    assert download(TABLE_URL, cache_dir=tmp_path, get=get) == b"DOC"
    assert download(TABLE_URL, cache_dir=tmp_path, get=get) == b"DOC"
    assert calls == [TABLE_URL]
    assert download(TABLE_URL, cache_dir=False, get=get) == b"DOC" and len(calls) == 2


# --- the published gold set ---------------------------------------------------------------------


def test_published_gold_set_is_consistent():
    links = load_jsonl(DATA / "nis2_cz.jsonl")
    summary = json.loads((DATA / "nis2_cz_summary.json").read_text(encoding="utf-8"))
    assert len(links) == summary["links"]
    assert summarize(links)["eu_check"] == summary["eu_check"]
    for lk in links:
        assert lk.eu_id.startswith(f"eu:{NIS2}:cs:") and lk.national_id.startswith("cz:264/2025:")
        assert lk.status == "listed" and lk.source_url == TABLE_URL
        assert lk.raw_eu_ref and lk.raw_national_ref and lk.national_text and lk.eu_text
        assert lk.eu_check in {"exact", "fallback", "unresolved"}
        assert lk.national_check in {"same_number", "renumbered", "unverified", "unresolved"}
        if lk.national_check == "renumbered":
            assert lk.national_id != lk.bill_national_id and "matches act" in lk.note


GOLD_SETS = {
    # file: (CELEX, act, psp.cz table URL)
    "32022L2557_cz.jsonl": ("32022L2557", "266/2025", "https://www.psp.cz/sqw/text/orig2.sqw?idd=253085"),
    "32019L0882_cz.jsonl": ("32019L0882", "424/2023", "https://www.psp.cz/sqw/text/orig2.sqw?idd=232281"),
    "32019L1937_cz.jsonl": ("32019L1937", "171/2023", "https://www.psp.cz/sqw/text/orig2.sqw?idd=221044"),
}


@pytest.mark.parametrize("name", sorted(GOLD_SETS))
def test_published_gold_sets_are_consistent(name):
    celex, act, url = GOLD_SETS[name]
    links = load_jsonl(DATA / name)
    summary = json.loads((DATA / name.replace(".jsonl", "_summary.json")).read_text(encoding="utf-8"))
    assert len(links) == summary["links"] and summarize(links)["national_check"] == summary["national_check"]
    assert summary["source"]["table"] == url and summary["source"]["act"] == act
    for lk in links:
        assert lk.celex == celex and lk.eu_id.startswith(f"eu:{celex}:cs:")
        assert lk.national_id.startswith(f"cz:{act}:") and lk.source_url == url and lk.status == "listed"
        assert lk.eu_check in {"exact", "fallback", "unresolved"}
        assert lk.national_check in {"same_number", "renumbered", "ancestor", "unverified", "unresolved"}
        assert set(lk.flags) <= {"amendment_point", "part_ref"}


def test_published_whistleblower_regressions():
    links = load_jsonl(DATA / "32019L1937_cz.jsonl")
    nat = {lk.national_id.rsplit(":", 1)[1] for lk in links}
    # § 21 odst. 2 (retention, Art. 18) and the middle paragraphs of "odst. 1 až odst. 3" are linked
    assert {"par21.odst2", "par20.odst2", "par22.odst2", "par11.odst2", "par2.odst1.pism_d"} <= nat
    assert not any("pism2" in lk.bill_national_id for lk in links)
    # no two bill IDs of a row are mapped onto one act ID by renumbering
    seen: dict[tuple, set] = {}
    for lk in links:
        if lk.national_check != "ancestor":
            seen.setdefault((lk.row, lk.national_id), set()).add(lk.bill_national_id)
    assert all(len(v) == 1 for v in seen.values())


@pytest.mark.live
def test_live_table_from_psp(tmp_path):
    data = download(TABLE_URL, cache_dir=tmp_path)
    tr = table_rows(read_table(data), celex=NIS2)
    assert len(tr) == 479
    assert tr[0].national_ref == "§ 1 odst. 1" and tr[0].eu_ref == "Čl. 1 odst. 1"


@pytest.mark.live
def test_live_cer_table_found_from_the_print(tmp_path):
    from openacquis.evalset import history_url, tisk_url

    files = find_table_files(download(tisk_url(9, 947), cache_dir=tmp_path))
    assert files[0]["name"] == "t0947d0.docx"
    assert published_act(download(history_url(9, 947), cache_dir=tmp_path)) == "266/2025"
    tr = table_rows(read_table(download(files[0]["url"], cache_dir=tmp_path)), celex="32022L2557")
    assert len(tr) == 200
