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
    check_links,
    containment,
    cz_ref_to_ids,
    doc_rows,
    doc_text,
    docx_rows,
    download,
    eu_ref_to_ids,
    links_from_rows,
    load_jsonl,
    parse_cz_ref,
    parse_eu_ref,
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


@pytest.mark.live
def test_live_table_from_psp(tmp_path):
    data = download(TABLE_URL, cache_dir=tmp_path)
    tr = table_rows(read_table(data), celex=NIS2)
    assert len(tr) == 479
    assert tr[0].national_ref == "§ 1 odst. 1" and tr[0].eu_ref == "Čl. 1 odst. 1"
