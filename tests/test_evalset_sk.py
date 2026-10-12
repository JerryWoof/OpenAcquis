import json
from pathlib import Path

import pytest

from openacquis import evalset
from openacquis.evalset_sk import (
    BILL,
    ActMention,
    SkGoldLink,
    acts_in_cell,
    adjust_national_id,
    assign_acts,
    bill_articles,
    candidate_acts,
    check_sk_links,
    eu_ids,
    eu_ids_qualified,
    letter_subpoint,
    links_from_sk_rows,
    load_jsonl,
    parse_sk_ref,
    read_sk_table,
    resolve_eu,
    save_jsonl,
    sk_table_rows,
    summarize_sk,
)
from openacquis.provisions import Document, Provision

FIXTURES = Path(__file__).parent / "fixtures"
DATA = Path(__file__).parent.parent / "data" / "eval"
TABLE = FIXTURES / "evalsk_nis2_553679_excerpt.docx"
NIS2 = "32022L2555"
BILLS = {1: "69/2018", 2: "455/1991", 6: "452/2021", 7: None}


def leaves(ref: str) -> list[list[str]]:
    return [[".".join(f"{k}{v}" for k, v in leaf) for leaf in g.leaves] for g in parse_sk_ref(ref)]


# --- references -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("Č:I, §:17, O:1, P:c), P:1", [["artI.par17.ods1.pismc.bod1"]]),
        # a further P under a lettered point is the next sub-point; a letter is the next point
        ("Č:I, §:17, O:1,\nP:c), P:3\nP:4 ", [["artI.par17.ods1.pismc.bod3", "artI.par17.ods1.pismc.bod4"]]),
        ("Č:I, §:17, O:1, P:d)\n\n\nP:f)", [["artI.par17.ods1.pismd", "artI.par17.ods1.pismf"]]),
        ("Č:2, O:2, P:a), P:i, P:ii", [["art2.ods2.pisma.bodi", "art2.ods2.pisma.bodii"]]),
        ("Č:2, O:2, P:h), P:i)", [["art2.ods2.pismh", "art2.ods2.pismi"]]),
        # CER style: one keyword per line, a § without an article continues the article above
        ("C: 1\n§: 1\nO: 1\nP: a\nP: b\n\n§: 5\nP: f", [["art1.par1.ods1.pisma", "art1.par1.ods1.pismb"], ["par5.pismf"]]),
        # Pay Transparency style: "§ 2", B for bod, lists with "a", ranges with "až"
        ("§ 2\nP:j\nB:1\n§ 2a\nO:4 a 5", [["par2.pismj.bod1"], ["par2a.ods4", "par2a.ods5"]]),
        ("§ 229\nO:4 až 6", [["par229.ods4", "par229.ods5", "par229.ods6"]]),
        ("O:1,4 a 5", [["ods1", "ods4", "ods5"]]),
        ("P:d,e", [["pismd", "pisme"]]),
        # a new article starts a new group; "C: 4" is a keyword, not a roman value of the list above
        ("§ 14\nO:4 a 5\nČ:IV\n§ 1", [["par14.ods4", "par14.ods5"], ["artIV.par1"]]),
        ("C: 1\n§: 2\nP: h\nC: 4\n§: 3a", [["art1.par2.pismh"], ["art4.par3a"]]),
        # an article alone, then (after a blank line) a § of the next act
        ("Č:VII\n\n\n§:35, O:7", [["artVII"], ["par35.ods7"]]),
        ("Č:VI,\nBod 5 ", [["artVI.abod5"]]),
        ("Č:I, Príloha č. 1", [["artI.annex1"]]),
        ("§ 19\nPríloha", [["par19"], ["annex0"]]),
        ("Č:I, §:6: O:1", [["artI.par6.ods1"]]),
        ("O\xa011", [["ods11"]]),
    ],
)
def test_parse_sk_ref(ref, expected):
    assert leaves(ref) == expected


def test_parse_sk_ref_qualifiers():
    g = parse_sk_ref("Č:19, O:1, Pododsek 1, V:1 a 4")[0]
    assert [list(map(tuple, leaf)) for leaf in g.leaves] == [[("art", "19"), ("ods", "1")]]
    assert g.qualifier == "pododsek 1 veta 1,4"
    assert not g.unparsed


@pytest.mark.parametrize(
    "ref, ids",
    [
        ("Č:2, O:2, P:a),\nP:i", ["art2.par2.pt_a.subpt_i"]),
        ("Č:6, O:16", ["art6.par16"]),  # definitions: mapped to points when checked
        ("Č:2, B:1", ["art2.pt1"]),  # a numbered point directly under the article
        ("Príloha č. 1", ["anx1"]),
        ("Č: 1\nO:1\nP: a\n\nP: b", ["art1.par1.pt_a", "art1.par1.pt_b"]),
    ],
)
def test_eu_ids(ref, ids):
    got, bad = eu_ids(ref)
    assert got == ids and bad == ""


def test_acts_in_cell():
    acts = acts_in_cell("Návrh zákona\n\nZákon č. 69/2018 Z. z. v znení návrhu zákona\nZákon č. 308/1993\nZ. z.\n"
                        "Civilný sporový poriadok\n73/1998 Z. z.")
    assert [(a.act, a.line, a.as_amended) for a in acts] == [
        (BILL, 0, False), ("69/2018", 2, True), ("308/1993", 3, False), ("160/2015", 5, False), ("73/1998", 6, False)]


def test_assign_acts():
    groups = parse_sk_ref("§:8\n\n§:18\n\n§:19\n\n§:2,\nO:2")
    four = [ActMention("40/1964", 0), ActMention("40/1964", 2), ActMention("40/1964", 4), ActMention("513/1991", 6)]
    got, how = assign_acts(groups, four, 8, 7)
    assert how == "ordered" and [a.act for a in got] == ["40/1964"] * 3 + ["513/1991"]
    got, how = assign_acts(groups, [ActMention(BILL, 0), ActMention(BILL, 3)], 8, 4)
    assert how == "single"
    # fewer acts than groups: monotonic, by relative line position
    groups = parse_sk_ref("§:8\n§:18\n§:19\n\n\n\n§:2,\nO:2")
    got, how = assign_acts(groups, [ActMention("40/1964", 0), ActMention("513/1991", 6)], 8, 7)
    assert how == "position" and [a.act for a in got] == ["40/1964", "40/1964", "40/1964", "513/1991"]


# --- table ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rows():
    return read_sk_table(TABLE.read_bytes())


def test_read_table(rows):
    # header rows, a nested-table row and a side-by-side annex row are skipped
    assert len(rows) == 14
    assert [r.eu_ref for r in rows[:3]] == ["Č:1, O:1", "Č:1, O:2", "Č:2, O:2, P:a),\nP:i"]
    assert [(r.method, r.compliance, r.gold_plating) for r in rows[:2]] == [("N", "Ú", "GP-N")] * 2
    assert {r.method for r in rows} == {"N", "D", "n.a."}
    assert rows[5].method == "n.a." and rows[5].national_ref == ""


def test_table_rows_carry_eu_ref_down():
    head = ["Č:3, O:1", "text", "N", "Návrh zákona", "Č:I, §:4", "národný", "Ú", "", "GP - N", ""]
    cont = ["", "", "N", "Návrh zákona", "Č:I, §:5", "iný", "Č", "", "GP-A", "dôvod"]
    nested = ["a)", "a) bod"]
    out = sk_table_rows([head, nested, cont, ["Článok", "Text", "Spôsob", "Číslo", "Článok", "Text", "Zhoda", "", "", ""]])
    assert [(r.eu_ref, r.national_ref, r.compliance, r.gold_plating) for r in out] == [
        ("Č:3, O:1", "Č:I, §:4", "Ú", "GP-N"), ("Č:3, O:1", "Č:I, §:5", "Č", "GP-A")]


@pytest.fixture(scope="module")
def links(rows):
    return links_from_sk_rows(rows, NIS2, "366/2024", BILLS, source_url="https://example.test/t.docx")


def by_row(links, i):
    return [lk for lk in links if lk.row == i]


def test_links_bill_article_maps_to_amended_act(links):
    (lk,) = by_row(links, 2)
    assert lk.eu_id == "eu:32022L2555:sk:art2.par2.pt_a.subpt_i"
    assert lk.national_id == "sk:69/2018:par17.ods1.pism_c.bod1"
    assert (lk.status, lk.method, lk.compliance, lk.gold_plating) == ("fully_compatible", "N", "Ú", "GP-N")
    assert lk.bill_article == "I" and lk.act_version == "amended_by:366/2024" and not lk.negative


def test_links_multi_and_other_acts(links):
    assert [lk.national_id for lk in by_row(links, 3)] == [
        "sk:69/2018:par17.ods1.pism_c.bod5", "sk:69/2018:par3.ods1.pism_y"]
    # "Zákon č. 69/2018 Z. z. v znení návrhu zákona": the base act after the amendment; Č:I dropped
    assert [lk.national_id for lk in by_row(links, 6)] == [
        "sk:69/2018:par2.ods3.pism_a", "sk:69/2018:par2.ods3.pism_b", "sk:69/2018:par2.ods3.pism_c"]
    assert by_row(links, 6)[0].act_version == "amended_by:366/2024"
    assert [lk.national_id for lk in by_row(links, 7)] == ["sk:69/2018:priloha1"]
    assert [(lk.national_id, lk.act_assignment) for lk in by_row(links, 8)] == [
        ("sk:523/2004:par3.ods1", "ordered"), ("sk:69/2018:par2.ods3", "ordered")]
    assert by_row(links, 8)[0].act_version == ""  # not amended: checked in the version in force
    assert [lk.national_id for lk in by_row(links, 9)] == [
        "sk:40/1964:par8", "sk:40/1964:par18", "sk:40/1964:par19", "sk:513/1991:par2.ods2"]
    assert [lk.national_id for lk in by_row(links, 13)] == ["sk:69/2018:priloha1"]
    assert by_row(links, 13)[0].eu_id == "eu:32022L2555:sk:anx1"


def test_links_into_the_promulgated_bill(links):
    # final provisions and amending points of other acts point into the amending act as promulgated
    assert [(lk.national_id, lk.act_version) for lk in by_row(links, 10)] == [
        ("sk:366/2024:cl7", "vyhlasene_znenie"), ("sk:575/2001:par35.ods7", "")]
    assert [lk.national_id for lk in by_row(links, 11)] == ["sk:366/2024:cl6.bod5"]


def test_negatives(links):
    neg = [lk for lk in links if lk.negative]
    assert [(lk.row, lk.eu_id.rsplit(":", 1)[1], lk.status, lk.method) for lk in neg] == [
        (4, "art2.par5.pt_b", "no_national_provision", "D"),
        (5, "art2.par6", "not_applicable", "n.a."),
        (12, "art46", "not_applicable", "n.a."),
    ]
    assert all(lk.national_id == "" for lk in neg)


def test_bill_articles():
    d = Document("sk", "366/2024", "sk")
    d.add(Provision("sk:366/2024:cl1", "sk", "366/2024", "sk", "cl", "Čl. I", "1",
                    "Zákon č. 69/2018 Z. z. o kybernetickej bezpečnosti ... sa mení a dopĺňa takto:"))
    d.add(Provision("sk:366/2024:cl2", "sk", "366/2024", "sk", "cl", "Čl. II", "2",
                    "Zákon Národnej rady Slovenskej republiky č. 145/1995 Z. z. o správnych poplatkoch sa mení takto:"))
    d.add(Provision("sk:366/2024:cl3", "sk", "366/2024", "sk", "cl", "Čl. III", "3",
                    "Tento zákon nadobúda účinnosť 1. januára 2025."))
    assert bill_articles(d) == {1: "69/2018", 2: "145/1995", 3: None}
    new = Document("sk", "367/2024", "sk")
    new.add(Provision("sk:367/2024:cl1", "sk", "367/2024", "sk", "cl", "Čl. I", "1"))
    new.add(Provision("sk:367/2024:par1", "sk", "367/2024", "sk", "par", "§ 1", "1", "Predmet",
                      parent_id="sk:367/2024:cl1"))
    assert bill_articles(new) == {1: "367/2024"}


# --- checking -----------------------------------------------------------------------------


def _doc(act: str, items: dict[str, str], source: str = "sk", lang: str = "sk") -> Document:
    d = Document(source, act, lang)
    for pid, text in items.items():
        local = pid.rsplit(":", 1)[-1]
        parent = pid.rsplit(".", 1)[0] if "." in local else None
        d.add(Provision(pid, source, act, lang, local.rsplit(".", 1)[-1], local, local, text, parent_id=parent))
    return d


def test_resolve_eu_fallbacks():
    eu = _doc(NIS2, {"eu:X:sk:art1": "Predmet", "eu:X:sk:art6": "Vymedzenie", "eu:X:sk:art6.pt18": "uzol",
                     "eu:X:sk:anx_i": "Odvetvia"}, source="eu")
    assert resolve_eu("eu:X:sk:art6.par18", eu) == ("eu:X:sk:art6.pt18", "fallback")
    assert resolve_eu("eu:X:sk:anx1", eu) == ("eu:X:sk:anx_i", "fallback")
    assert resolve_eu("eu:X:sk:anx0", eu) == ("eu:X:sk:anx_i", "fallback")
    assert resolve_eu("eu:X:sk:art1.par1", eu) == ("eu:X:sk:art1", "fallback")
    assert resolve_eu("eu:X:sk:art9", eu) == ("eu:X:sk:art9", "unresolved")


def test_adjust_national_id():
    d = _doc("69/2018", {"sk:69/2018:par3": "Na účely", "sk:69/2018:par3.pism_y": "y",
                         "sk:76/2026:par2.ods1.pism_a": "a", "sk:69/2018:priloha0": "Príloha"})
    assert adjust_national_id("sk:69/2018:par3.ods1.pism_y", d) == "sk:69/2018:par3.pism_y"
    assert adjust_national_id("sk:76/2026:par2.pism_a", d) == "sk:76/2026:par2.ods1.pism_a"
    assert adjust_national_id("sk:69/2018:priloha1", d) == "sk:69/2018:priloha0"
    assert adjust_national_id("sk:69/2018:par3", d) is None


def test_check_sk_links(rows, links):
    links = [SkGoldLink(**{**lk.to_dict()}) for lk in links]
    r2, r8 = rows[2], rows[8]
    eu = _doc(NIS2, {f"eu:{NIS2}:sk:art2.par2.pt_a.subpt_i": r2.eu_text,
                     f"eu:{NIS2}:sk:art6.pt35": r8.eu_text}, source="eu")
    act = _doc("69/2018", {
        "sk:69/2018:par17.ods1.pism_c.bod1": r2.national_text,
        "sk:69/2018:par2.ods3": "Tento zákon sa nevzťahuje na úplne iný text bez vzťahu",
    })
    act.eli = "https://www.slov-lex.sk/pravne-predpisy/SK/ZZ/2018/69/20250101"
    other = _doc("523/2004", {"sk:523/2004:par3.ods1": r8.national_text})
    check_sk_links(links, eu, {"69/2018": act, "523/2004": other}, rows=rows, bills=BILLS, final_act="366/2024")
    (lk,) = by_row(links, 2)
    assert (lk.eu_check, lk.national_check, lk.act_version) == ("exact", "same_number", "20250101")
    assert lk.eu_similarity == 1.0 and lk.national_similarity == 1.0
    l8 = by_row(links, 8)
    assert l8[0].eu_id == f"eu:{NIS2}:sk:art6.pt35" and l8[0].eu_check == "fallback"
    assert l8[0].national_check == "same_number"
    # unverified in 69/2018: 523/2004 (named in the same row) has the ID, but not that text either
    assert l8[1].national_id == "sk:69/2018:par2.ods3" and l8[1].national_check == "unverified"
    # links into acts that were not fetched stay unchecked
    assert {lk.national_check for lk in by_row(links, 9)} == {"not_checked"}


def test_check_moves_link_to_the_act_whose_text_matches(rows):
    # the bill's article numbering can differ from the act as promulgated: "C: 4" cited for
    # an amendment that ended up in another article
    row = sk_table_rows([["Č:1, O:4", "dôverné informácie", "N", "Návrh zákona", "C: 4\n§: 3a\nO: 2",
                          "(2) Určiť informáciu ako limitovanú podľa odseku 1 je možné len v oblastiach",
                          "Ú", "", "GP-N", ""]])
    bills = {1: "367/2024", 4: "319/2002", 5: "215/2004", 12: None}
    (lk,) = links_from_sk_rows(row, "32022L2557", "367/2024", bills)
    assert lk.national_id == "sk:319/2002:par3a.ods2" and lk.bill_article == "IV"
    assert candidate_acts(lk, row[0], bills, "367/2024") == ["215/2004"]
    wrong = _doc("319/2002", {"sk:319/2002:par3": "Obrana"})
    right = _doc("215/2004", {"sk:215/2004:par3a.ods2": row[0].national_text})
    check_sk_links([lk], None, {"319/2002": wrong, "215/2004": right}, rows=row, bills=bills, final_act="367/2024")
    assert (lk.national_id, lk.national_act, lk.national_check) == ("sk:215/2004:par3a.ods2", "215/2004", "same_number")
    assert "act changed by text: 319/2002 -> 215/2004" in lk.note


def test_jsonl_roundtrip_and_core_loader(tmp_path, links):
    path = tmp_path / "x_sk.jsonl"
    assert save_jsonl(links, path) == len(links)
    back = load_jsonl(path)
    assert [b.to_dict() for b in back] == [lk.to_dict() for lk in links]
    core = evalset.load_jsonl(path)  # extra fields are dropped by the core loader
    assert core[2].national_id == links[2].national_id and not hasattr(core[2], "compliance")


def test_summarize_sk(rows, links):
    s = summarize_sk(links, rows)
    assert s["negatives"]["records"] == 3 and s["negatives"]["status"] == {
        "no_national_provision": 1, "not_applicable": 2}
    assert s["by_act"]["69/2018"] >= 8 and s["table_rows"] == 14
    assert s["method"] == {"N": 11, "n.a.": 2, "D": 1}
    assert s["rows_without_records"] == []


# --- regressions -----------------------------------------------------------------------------


def _kdoc(act: str, items: list[tuple[str, str, str]]) -> Document:
    """A document from ``(local id, kind, text)``; parents follow the IDs."""
    d = Document("sk", act, "sk")
    for local, kind, text in items:
        parent = f"sk:{act}:{local.rsplit('.', 1)[0]}" if "." in local else None
        d.add(Provision(f"sk:{act}:{local}", "sk", act, "sk", kind, local, local, text, parent_id=parent))
    return d


def _row(eu_ref, method, act, ref, text, compliance, eu_text="text EÚ"):
    return sk_table_rows([[eu_ref, eu_text, method, act, ref, text, compliance, "", "GP-N", ""]])[0]


def test_eu_qualifiers_are_kept_and_partial_negatives_flagged():
    assert eu_ids_qualified("Č: 12 O: 2 2. pododsek") == (["art12.par2"], "", "pododsek 2")
    assert eu_ids_qualified("Č: 4 O: 2 2. pododesk")[:2] == (["art4.par2"], "")  # typo in the CER table
    rows = [
        _row("Č:31, O:4, V:1", "N", "Návrh zákona", "Č:I, §:5, O:1", "text", "Ú"),
        _row("Č:31, O:4, V:2", "D", "", "", "", ""),
        _row("Č:46", "D", "", "", "", "n.a."),  # an option not used, whatever the compliance says
    ]
    rows = [type(r)(**{**r.__dict__, "index": i}) for i, r in enumerate(rows)]
    links = links_from_sk_rows(rows, NIS2, "366/2024", BILLS)
    pos, neg1, neg2 = links
    assert "EU ref qualifier: veta 1" in pos.note and "EU ref qualifier: veta 2" in neg1.note
    assert neg1.negative and neg1.eu_id == pos.eu_id and neg1.flags == ["partial_negative"]
    assert (neg2.status, neg2.flags) == ("no_national_provision", [])
    assert summarize_sk(links)["negatives"]["partial_negative"] == 1


def test_o_after_a_letter_is_its_point():
    act = _kdoc("367/2024", [("par5", "par", "Ministerstvo"), ("par5.pism_e", "pism", "spravuje zoznam"),
                             ("par5.pism_e.bod3", "bod", "zoznam všetkých incidentov,"),
                             ("par5.pism_f", "pism", "je kontaktným miestom"),
                             ("par5.pism_f.bod2", "bod", "plní úlohy"), ("par5.pism_f.bod14", "bod", "predkladá")])
    assert letter_subpoint("sk:367/2024:par5.ods2", "C: 1 §: 5 P: f O: 2", act) == "sk:367/2024:par5.pism_f.bod2"
    assert letter_subpoint("sk:367/2024:par5.ods14", "C: 1 §: 5 P: f §: 5 O: 14", act) == \
        "sk:367/2024:par5.pism_f.bod14"
    assert letter_subpoint("sk:367/2024:par5.ods3", "§: 5 P: f O: 13 O: 9 P: e O: 3", act) == \
        "sk:367/2024:par5.pism_e.bod3"
    assert letter_subpoint("sk:367/2024:par5.ods2", "§: 5 O: 2", act) is None  # no letter before it
    # a paragraph cited above a letter of a § without paragraphs is dropped
    assert adjust_national_id("sk:367/2024:par5.ods9.pism_e", act) == "sk:367/2024:par5.pism_e"
    row = _row("Č:9, O:7", "N", "Návrh zákona", "C: 1\n§: 5\nP: f\nO: 2", "f) je kontaktným miestom 2. plní úlohy", "Ú")
    (lf, lo) = links_from_sk_rows([row], "32022L2557", "367/2024", {1: "367/2024"})
    check_sk_links([lf, lo], None, {"367/2024": act}, rows=[row], bills={1: "367/2024"}, final_act="367/2024")
    assert (lo.national_id, lo.national_check) == ("sk:367/2024:par5.pism_f.bod2", "same_number")
    assert "O: after a letter read as its point" in lo.note


def test_pdf_annex_is_never_confirmed():
    act = _kdoc("69/2018", [("priloha1", "priloha", "Prevziať prílohu - Príloha č. 1 k zákonu č. 69/2018 Z. z. "
                                                    "PDF: https://static.slov-lex.sk/pdf/x.pdf")])
    row = _row("Príloha č. 1", "N", "Návrh zákona", "Č:I, Príloha č. 1",
               "viď nižšie tabuľka s prílohou č. 1 k zákonu č. 69/2018 Z. z.", "Ú")
    (lk,) = links_from_sk_rows([row], NIS2, "366/2024", BILLS)
    check_sk_links([lk], None, {"69/2018": act}, rows=[row], bills=BILLS, final_act="366/2024")
    assert (lk.national_check, lk.national_similarity) == ("unverified", None)
    assert "PDF only" in lk.note


def test_reference_taken_from_the_text_cell():
    row = _row("Príloha", "N", "návrh zákona", "", "Príloha č. 1 k zákonu č. ...../2024 Z. z.", "Ú")
    (lk,) = links_from_sk_rows([row], "32022L2557", "367/2024", {1: "367/2024"})
    assert lk.national_id == "sk:367/2024:priloha1" and "taken from the text cell" in lk.note


def test_bill_article_only_when_the_bill_is_cited():
    bills = {1: "76/2026", 2: "311/2001"}
    row = _row("Č:4, O:1", "N", "Zákonník práce", "Čl. 4", "Zamestnanci majú právo", "Ú")
    (lk,) = links_from_sk_rows([row], "32023L0970", "76/2026", bills)
    assert (lk.national_id, lk.bill_article) == ("sk:311/2001:cl4", "")
    row = _row("Č:4, O:1", "N", "Zákon č. 311/2001 Z. z. v znení návrhu zákona", "§ 13 O:1", "text", "Ú")
    (lk,) = links_from_sk_rows([row], "32023L0970", "76/2026", bills)
    assert (lk.national_id, lk.bill_article) == ("sk:311/2001:par13.ods1", "II")


def test_retarget_prefers_the_same_id_in_another_act_and_resets_bill_article():
    # CER row 151: 10/1996 § 1 ods. 2 is quoted verbatim, but 367/2024 has a § whose text matches the cell
    quote = ("(2) Tento zákon sa vzťahuje aj na kritické subjekty v odvetví dopravy. "
             "(3) Ministerstvo vnútra zverejní zoznam sektorov.")
    row = _row("Č:1, O:2", "N", "Návrh zákona\nZákon č. 10/1996 Z. z.", "C: 1 §: 1 O: 2", quote, "Ú")
    bills = {1: "367/2024"}
    (lk,) = links_from_sk_rows([row], "32022L2557", "367/2024", bills)
    assert lk.national_id == "sk:367/2024:par1.ods2" and lk.bill_article == "I"
    own = _kdoc("367/2024", [("par1", "par", "Predmet"), ("par1.ods2", "ods", "Tento zákon upravuje pôsobnosť "
                                                                             "orgánov štátnej správy"),
                             ("par16", "par", ""), ("par16.ods3", "ods", "Ministerstvo vnútra zverejní zoznam sektorov.")])
    other = _kdoc("10/1996", [("par1", "par", ""), ("par1.ods2", "ods", "Tento zákon sa vzťahuje aj na kritické "
                                                                      "subjekty v odvetví dopravy a ich prevádzkovateľov.")])
    check_sk_links([lk], None, {"367/2024": own, "10/1996": other}, rows=[row], bills=bills, final_act="367/2024")
    # the in-act renumbering (par16.ods3, 1.0) loses to the exact ID of the other act (0.8)
    assert (lk.national_id, lk.national_check) == ("sk:10/1996:par1.ods2", "same_number")
    assert lk.national_similarity < 1.0 and "matches act" not in lk.note
    assert (lk.bill_article, lk.act_assignment) == ("", "text")


# --- the published sets -----------------------------------------------------------------------


@pytest.mark.parametrize("key, main_act", [("nis2", "69/2018"), ("cer", "367/2024"), ("pay", "76/2026")])
def test_published_sets(key, main_act):
    path = DATA / f"{key}_sk.jsonl"
    if not path.exists():
        pytest.skip(f"{path.name} not built")
    links = load_jsonl(path)
    summary = json.loads(path.with_name(f"{key}_sk_summary.json").read_text(encoding="utf-8"))
    pos = [lk for lk in links if not lk.negative]
    neg = [lk for lk in links if lk.negative]
    assert len(pos) == summary["links"] and len(neg) == summary["negatives"]["records"]
    assert all(lk.national_id.startswith("sk:") and lk.eu_id for lk in pos)
    assert all(not lk.national_id and lk.status in ("not_applicable", "no_national_provision") for lk in neg)
    assert summary["by_act"][main_act] == max(summary["by_act"].values())
    assert summary["eu_resolved_share"] >= 0.95 and summary["national_resolved_share"] >= 0.9
    # no two bill IDs of a row are renumbered onto one act ID
    seen: dict[tuple, set] = {}
    for lk in pos:
        if lk.national_check != "ancestor":
            seen.setdefault((lk.row, lk.national_id), set()).add(lk.bill_national_id)
    assert all(len(v) == 1 for v in seen.values())
    # a negative whose EU ID also has positive links is flagged
    pos_eu = {lk.eu_id for lk in pos}
    assert all(("partial_negative" in lk.flags) == (lk.eu_id in pos_eu) for lk in neg)
    # PDF-only annexes are never confirmed by text
    assert not any("PDF only" in lk.note and lk.national_check != "unverified" for lk in pos)


# --- live ---------------------------------------------------------------------------------------


@pytest.mark.live
def test_live_download_nis2_table(tmp_path):
    data = evalset.download("https://www.nrsr.sk/web/Dynamic/Download.aspx?DocID=553679", cache_dir=tmp_path)
    rows = read_sk_table(data)
    assert len(rows) >= 280
    assert sum(r.method == "n.a." for r in rows) >= 60
