"""Regression tests for the v0.5 pipeline review of the static pages: transposition notices as link
candidates, extra-rule lists of general codes, registry fixes (override, version, act labels)."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from openacquis.adapters import MeasureMapping
from openacquis.align import DEFAULT_EXTRA_MAX_SHARE, align, default_version
from openacquis.directives import load_registry, parse_registry, resolve_main_acts, validate
from openacquis.leftovers import (
    EXTRA_NOTE_BROAD,
    directive_number,
    find_leftovers,
    transposition_notice,
)
from openacquis.match import LexicalEmbedder, Matcher
from openacquis.provisions import Document, Provision

sys.path.insert(0, str(Path(__file__).parent))
import test_align  # noqa: E402
import test_directives  # noqa: E402

NOW = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)
EU, ACT = test_align.EU, test_align.ACT
NOTICE = f"cz:{ACT}:par20.odst1"
CITING = f"cz:{ACT}:par21.odst1"


def _doc(source: str, act: str, lang: str, units: list[tuple]) -> Document:
    d = Document(source, act, lang, title="Test")
    for pid, kind, number, text, heading, parent in units:
        d.add(Provision(f"{source}:{act}:{pid}", source, act, lang, kind, pid, number, text, heading,
                        f"{source}:{act}:{parent}" if parent else None, url=f"https://x.example/{pid}"))
    return d


# --- which units are notices -----------------------------------------------------------------


def test_directive_number():
    assert directive_number("32020L1828") == "2020/1828"
    assert directive_number("32019L0904") == "2019/904"
    assert directive_number("32016R0679") is None and directive_number(None) is None


def test_austrian_transposition_clause_is_a_notice():
    d = _doc("at", "10002070", "de", [
        ("art4", "art", "4", "", "Umsetzungshinweis", None),
        ("art4.abs2", "abs", "2", "(2) Mit diesem Bundesgesetz wird die Richtlinie (EU) 2022/2381 zur Gewährleistung "
         "einer ausgewogeneren Vertretung von Frauen und Männern unter den Direktoren börsenotierter Gesellschaften "
         "und über damit zusammenhängende Maßnahmen, ABl. Nr. L 315 vom 7.12.2022 S. 44, umgesetzt.", None, "art4"),
        ("par86.abs1", "abs", "1", "(1) Der Aufsichtsrat besteht aus mindestens drei Mitgliedern; Frauen und Männer "
         "sind im Aufsichtsrat jeweils mit einem Anteil von mindestens 30 Prozent vertreten.", None, None),
    ])
    assert transposition_notice(d, "at:10002070:art4.abs2", "2022/2381") == "transposition_note"
    assert transposition_notice(d, "at:10002070:par86.abs1", "2022/2381") is None


def test_slovak_annex_listing_transposed_acts_is_a_notice():
    d = _doc("sk", "69/2018", "sk", [
        ("priloha3", "priloha", "3", "Príloha č. 3 k zákonu č. 69/2018 Z. z. ZOZNAM PREBERANÝCH PRÁVNE ZÁVÄZNÝCH "
         "AKTOV EURÓPSKEJ ÚNIE\nSmernica Európskeho parlamentu a Rady (EÚ) 2022/2555 zo 14. decembra 2022 o "
         "opatreniach na zabezpečenie vysokej spoločnej úrovne kybernetickej bezpečnosti v Únii (smernica NIS 2) "
         "(Ú. v. EÚ L 333, 27. 12. 2022).", None, None),
        ("priloha3.bod1", "bod", "1", "Smernica Rady 2008/114/ES (Ú. v. EÚ L 345, 23. 12. 2008).", None, "priloha3"),
    ])
    assert transposition_notice(d, "sk:69/2018:priloha3", "2022/2555") == "transposition_note"
    assert transposition_notice(d, "sk:69/2018:priloha3.bod1", "2022/2555") == "transposition_note"  # in the annex


def test_list_item_that_is_mostly_the_directive_title_is_a_citation():
    item = ("2. qualifizierte Einrichtungen aus anderen Mitgliedstaaten der Europäischen Union, die in dem Verzeichnis "
            "der Europäischen Kommission nach Artikel 5 Absatz 1 Satz 4 der Richtlinie (EU) 2020/1828 des Europäischen "
            "Parlaments und des Rates vom 25. November 2020 über Verbandsklagen zum Schutz der Kollektivinteressen der "
            "Verbraucher und zur Aufhebung der Richtlinie 2009/22/EG (ABl. L 409 vom 4.12.2020, S. 1) eingetragen sind.")
    d = _doc("de", "vdug", "de", [("par2.abs1.nr2", "nr", "2", item, None, None)])
    assert transposition_notice(d, "de:vdug:par2.abs1.nr2", "2020/1828") == "directive_citation"
    # a citation of another act is not the directive's title: the unit stays a candidate
    assert transposition_notice(d, "de:vdug:par2.abs1.nr2", "2022/2555") is None


def test_substantive_unit_mentioning_transposition_is_not_a_notice():
    d = _doc("cz", ACT, "cs", [
        ("par1.odst1", "odst", "1", "Tento zákon zapracovává příslušné předpisy Evropské unie a upravuje práva a "
         "povinnosti poskytovatelů regulovaných služeb, působnost Národního úřadu pro kybernetickou a informační "
         "bezpečnost, hlášení kybernetických bezpečnostních incidentů, bezpečnostní opatření, kontrolu a sankce "
         "za porušení povinností, jakož i spolupráci s orgány jiných členských států.", None, None),
    ])
    assert transposition_notice(d, f"cz:{ACT}:par1.odst1", "2099/1") is None


# --- notices in the pipeline ----------------------------------------------------------------------


def _eu_with_final() -> Document:
    d = test_align.eu_doc()
    d.add(Provision(f"eu:{EU}:cs:art4", "eu", EU, "cs", "article", "Článek 4", "4", "", "Provedení ve vnitrostátním právu"))
    d.add(Provision(f"eu:{EU}:cs:art4.par1", "eu", EU, "cs", "paragraph", "1.", "1",
                    "Členské státy uvedou v účinnost předpisy nezbytné pro dosažení souladu s touto směrnicí.",
                    None, f"eu:{EU}:cs:art4"))
    return d


def _cz_with_notices() -> Document:
    d = test_align.cz_doc()
    title = ("směrnice Evropského parlamentu a Rady (EU) 2099/1 ze dne 1. ledna 2099 o kybernetické bezpečnosti, "
             "o hlášení významného incidentu, včasném varování do 24 hodin po zjištění a závěrečné zprávě o incidentu "
             "(Úř. věst. L 1, 1.1.2099)")
    for par, text in (("par20", f"Tento zákon zapracovává {title}."),
                      ("par21", f"Úřad spolupracuje s orgány podle {title}.")):
        d.add(Provision(f"cz:{ACT}:{par}", "cz", ACT, "cs", "par", f"§ {par[3:]}", par[3:], "", "Závěrečné ustanovení",
                        f"cz:{ACT}:hlava1"))
        d.add(Provision(f"cz:{ACT}:{par}.odst1", "cz", ACT, "cs", "odst", "(1)", "1", text, None, f"cz:{ACT}:{par}"))
    return d


def test_notices_are_candidates_of_final_provisions_only():
    eu, cz = _eu_with_final(), _cz_with_notices()
    plain = align(eu, [cz], threshold=0.0, exclude_notices=False, now=NOW)
    tops = {a.eu_id: a.candidates[0].national_id for a in plain.provisions}
    assert {NOTICE, CITING} & set(tops.values())  # without the guard a notice wins some row

    res = align(eu, [cz], threshold=0.0, now=NOW)
    assert res.leftovers.national_excluded == {NOTICE: "transposition_note", CITING: "directive_citation"}
    final = f"eu:{EU}:cs:art4.par1"  # "Provedení ve vnitrostátním právu"
    for a in res.provisions:
        ids = {c.national_id for c in a.candidates}
        assert CITING not in ids, a.eu_id  # a unit citing the directive is no candidate at all
        if a.eu_id != final:  # not even of other final provisions (art3 "Přezkum")
            assert NOTICE not in ids and NOTICE not in a.links, a.eu_id
    # the "Transposition" article keeps the transposition clause, as the official tables pair them
    assert res.by_eu()[final].candidates[0].national_id == NOTICE
    # never extra-rule candidates; recorded with the boilerplate and in the export
    assert not {x["national_id"] for x in res.extra_rule_candidates} & {NOTICE, CITING}
    linked = {n for a in res.provisions for n in a.links}
    assert all(res.leftovers.national_boilerplate.get(x) or x in linked for x in (NOTICE, CITING))
    d = res.to_dict()
    assert d["leftovers"]["national_excluded"][CITING] == "directive_citation"
    assert d["summary"]["national_units_excluded"] == 2


def test_masking_keeps_the_other_scores():
    eu, cz = _eu_with_final(), _cz_with_notices()
    mr = Matcher(LexicalEmbedder(), k=3).match(eu, [cz])
    keep = f"eu:{EU}:cs:art4.par1"
    m2 = mr.masked([NOTICE], keep_rows=[keep])
    j = [u.id for u in mr.national_units].index(NOTICE)
    for i, u in enumerate(mr.eu_units):
        for jj in range(len(mr.national_units)):
            if jj != j or u.id == keep:
                assert m2.scores[i][jj] == mr.scores[i][jj]
            else:
                assert m2.scores[i][jj] < min(mr.scores[i])
    assert all(c.national_id != NOTICE for c in m2.candidates() if c.eu_id != keep)
    assert all(len(m2.for_eu(u.id)) == len(mr.for_eu(u.id)) for u in mr.eu_units)  # still k candidates
    assert mr.masked([]) is mr


# --- extra-rule lists of general codes -------------------------------------------------------------


def test_extra_rules_off_and_share_cap():
    res = align(test_align.eu_doc(), [test_align.cz_doc()], threshold=0.25, now=NOW)
    links = {a.eu_id: a.links for a in res.provisions}
    lo = find_leftovers(res.match, links, threshold=0.25, acts=[ACT])
    n = len(lo.extra_rule_candidates)
    assert n and lo.extra_note is None
    off = find_leftovers(res.match, links, threshold=0.25, acts=[ACT], extra_rules=False)
    assert off.extra_rule_candidates == [] and off.extra_suppressed == n and off.extra_note == EXTRA_NOTE_BROAD
    assert off.gap_candidates == lo.gap_candidates  # gaps unaffected
    capped = find_leftovers(res.match, links, threshold=0.25, acts=[ACT], extra_max_share=0.01)
    assert capped.extra_rule_candidates == [] and "match nothing" in capped.extra_note
    assert find_leftovers(res.match, links, threshold=0.25, acts=[ACT], extra_max_share=1.0).extra_rule_candidates


def test_align_reports_why_there_is_no_extra_rule_list():
    res = align(test_align.eu_doc(), [test_align.cz_doc()], threshold=0.25, extra_rules=False, extra_note="baseline",
                now=NOW)
    assert res.extra_rule_candidates == [] and res.leftovers.extra_note == "baseline"
    s = res.summary()
    assert s["extra_rule_candidates"] == 0 and s["extra_note"] == "baseline" and s["extra_rule_candidates_suppressed"]
    assert 0.3 <= DEFAULT_EXTRA_MAX_SHARE <= 0.6


# --- registry ------------------------------------------------------------------------------------


def test_registry_flags_general_codes_and_baselines():
    reg = load_registry()
    for celex, cc in (("32022L2041", "cz"), ("32023L0970", "cz"), ("32022L2381", "de"), ("32022L2381", "at"),
                      ("32019L0904", "at"), ("32019L0790", "at")):
        e = reg.get(celex).country(cc)
        assert not e.reports_extra_rules and not resolve_main_acts(reg.get(celex), cc).extra_rules, (celex, cc)
    assert not reg.get("32023L0970").country("de").reports_extra_rules  # baseline
    assert reg.get("32022L2555").country("cz").reports_extra_rules


def test_registry_validates_new_keys():
    raw = test_directives.raw(cz={"status": "ok", "acts": [ACT], "extra_rules": "no", "act_labels": {"x/1": "X"}})
    problems = validate(raw)
    assert any("extra_rules must be true or false" in p for p in problems)
    assert any("act_labels names an act that is not in acts" in p for p in problems)
    e = parse_registry(test_directives.raw(cz={"status": "ok", "acts": [ACT, "2/2099"], "label": "1/2099 Sb.",
                                                "act_labels": {"2/2099": "Z2"}})).get(EU).country("cz")
    assert e.act_label(ACT) == "1/2099 Sb." and e.act_label("2/2099") == "Z2" and e.act_label("3/2099") == ""
    assert e.to_dict()["act_labels"] == {"2/2099": "Z2"}


def test_single_use_plastics_de_keeps_ewkfondsg_from_cellar():
    entry = load_registry().get("32019L0904")
    mapped = [MeasureMapping(f"u{i}", "t", ref, "mapped", ref) for i, ref in enumerate(["ewkkennzv", "ewkverbotsv", "krwg"])]
    res = resolve_main_acts(entry, "de", mapped)
    assert res.acts == ("ewkverbotsv", "ewkkennzv", "ewkfondsg") and res.source == "override"
    assert res.act_label("ewkfondsg") == "EWKFondsG"


def test_nis2_sk_page_uses_the_gold_set_version():
    assert load_registry().get("32022L2555").country("sk").version == "2025-01-01"
    assert default_version("32022L2555", "sk") == "2025-01-01"
    # pairs with a gold set and no registry version follow the gold set's version
    assert default_version("32022L2557", "sk") == "2025-01-01"
    assert default_version("32022L2557", "at") is None


# --- build_alignments --------------------------------------------------------------------------


def test_build_job_labels_secondary_acts_and_notes_suppressed_extras(tmp_path):
    ba = test_directives.ba
    e = test_directives.entry(cz={"status": "ok", "acts": [ACT, "2/2099"], "label": "1/2099 Sb.",
                                  "act_labels": {"2/2099": "Z2"}, "extra_rules": False})

    class Client(test_directives.FakeClient):
        def document(self, ref, version=None):
            return test_align.cz_doc(ref)

    rec = ba.build_job(e, "cz", out_dir=tmp_path, eurlex=test_align.FakeEurLex(), client=Client(),
                       embedder="lexical", now=NOW)
    assert rec["status"] == "ok", rec
    data = json.loads((tmp_path / f"{EU}_cz.json").read_text(encoding="utf-8"))
    review = json.loads((tmp_path / f"{EU}_cz.review.json").read_text(encoding="utf-8"))
    assert [n["label"] for n in data["national"]] == ["1/2099 Sb.", "Z2"]
    assert data["extra_note"] == EXTRA_NOTE_BROAD and data["summary"]["extra_rule_candidates"] == 0
    assert data["summary"]["extra_rule_candidates_suppressed"] > 0
    assert not [s for s in review["suggestions"] if s["kind"] == "extra_rule"]  # not in the review queue


def test_build_job_baseline_gets_its_own_note(tmp_path):
    ba = test_directives.ba
    e = test_directives.entry(cz={"status": "baseline", "acts": [ACT], "note": "existing law"})
    ba.build_job(e, "cz", out_dir=tmp_path, eurlex=test_align.FakeEurLex(), client=test_directives.FakeClient(),
                 embedder="lexical", now=NOW)
    data = json.loads((tmp_path / f"{EU}_cz.json").read_text(encoding="utf-8"))
    assert data["extra_note"] == ba.EXTRA_NOTE_BASELINE
