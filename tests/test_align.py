"""Alignment pipeline, evaluation against gold links, CLI `align` / `eval`, and the site's alignment page."""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openacquis.align import (
    AlignmentResult,
    align,
    default_acts,
    default_threshold,
    load_documents,
    make_judge,
    path_label,
)
from openacquis.cli import main
from openacquis.evalset import GoldLink, save_jsonl
from openacquis.evaluate import (
    coverage,
    evaluate,
    format_report,
    link_scores,
    on_path,
    public,
    retrieval,
)
from openacquis.judge import FakeJudge, Label, RuleJudge
from openacquis.provisions import Document, Provision

ROOT = Path(__file__).resolve().parent.parent
EU = "32099L0001"
ACT = "1/2099"
NOW = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)


def eu_doc() -> Document:
    d = Document("eu", EU, "cs", title="Směrnice o kybernetické bezpečnosti", url="https://eur-lex.example/x")

    def add(pid, kind, label, number, text="", heading=None, parent=None):
        d.add(Provision(f"eu:{EU}:cs:{pid}", "eu", EU, "cs", kind, label, number, text, heading,
                        f"eu:{EU}:cs:{parent}" if parent else None, url=f"https://eur-lex.example/{pid}"))

    add("art1", "article", "Článek 1", "1", heading="Předmět")
    add("art1.par1", "paragraph", "1.", "1", "Tato směrnice stanoví opatření k dosažení vysoké úrovně kybernetické bezpečnosti.", parent="art1")
    add("art2", "article", "Článek 2", "2", heading="Oznamovací povinnosti")
    add("art2.par1", "paragraph", "1.", "1", "Členské státy zajistí, aby subjekty předložily týmu CSIRT:", parent="art2")
    add("art2.par1.pt_a", "point", "a)", "a", "nejpozději do 24 hodin po zjištění významného incidentu včasné varování;", parent="art2.par1")
    add("art2.par1.pt_b", "point", "b)", "b", "nejpozději do jednoho měsíce závěrečnou zprávu o incidentu.", parent="art2.par1")
    add("art2.par2", "paragraph", "2.", "2", "Členské státy stanoví pokuty za porušení oznamovacích povinností.", parent="art2")
    add("art3", "article", "Článek 3", "3", heading="Přezkum")
    add("art3.par1", "paragraph", "1.", "1", "Komise přezkoumá fungování této směrnice a podá zprávu Evropskému parlamentu.", parent="art3")
    return d


def cz_doc(act: str = ACT) -> Document:
    d = Document("cz", act, "cs", title="Zákon o kybernetické bezpečnosti", url="https://e-sbirka.example/1")

    def add(pid, kind, label, number, text="", heading=None, parent=None):
        d.add(Provision(f"cz:{act}:{pid}", "cz", act, "cs", kind, label, number, text, heading,
                        f"cz:{act}:{parent}" if parent else None, url=f"https://e-sbirka.example/{pid}"))

    add("hlava1", "hlava", "HLAVA I", "1", heading="Obecná ustanovení")
    add("par1", "par", "§ 1", "1", heading="Předmět úpravy", parent="hlava1")
    add("par1.odst1", "odst", "(1)", "1", "Tento zákon upravuje opatření k zajištění kybernetické bezpečnosti.", parent="par1")
    add("par5", "par", "§ 5", "5", heading="Hlášení incidentů", parent="hlava1")
    add("par5.odst1", "odst", "(1)", "1", "Poskytovatel služby hlásí Úřadu", parent="par5")
    add("par5.odst1.pism_a", "pism", "a)", "a", "do 24 hodin po zjištění významného incidentu včasné varování,", parent="par5.odst1")
    add("par5.odst1.pism_b", "pism", "b)", "b", "do 30 dnů závěrečnou zprávu o vyřešení incidentu.", parent="par5.odst1")
    add("par9", "par", "§ 9", "9", heading="Přestupky", parent="hlava1")
    add("par9.odst1", "odst", "(1)", "1", "Za přestupek lze uložit pokutu do 10 000 000 Kč, nesplní-li poskytovatel oznamovací povinnost.", parent="par9")
    add("par12", "par", "§ 12", "12", heading="Úřední deska", parent="hlava1")
    add("par12.odst1", "odst", "(1)", "1", "Úřad zveřejňuje rozhodnutí na úřední desce po dobu patnácti dnů.", parent="par12")
    return d


def gl(eu_local: str, cz_local: str, act: str = ACT) -> GoldLink:
    return GoldLink(f"eu:{EU}:cs:{eu_local}", f"cz:{act}:{cz_local}")


GOLD = [
    gl("art1.par1", "par1.odst1"),
    gl("art2.par1.pt_a", "par5.odst1.pism_a"),
    gl("art2.par1.pt_b", "par5.odst1.pism_b"),
    gl("art2.par2", "par9"),  # the table cites a whole §
]


# --- helpers ----------------------------------------------------------------------------


def test_on_path():
    assert on_path("cz:1:par5", "cz:1:par5.odst1")
    assert on_path("cz:1:par5.odst1", "cz:1:par5")
    assert not on_path("cz:1:par5", "cz:1:par50")
    assert not on_path("cz:1:par5.odst1", "cz:1:par5.odst2")


def test_path_label():
    eu, cz = eu_doc(), cz_doc()
    assert path_label(eu, f"eu:{EU}:cs:art2.par1.pt_a") == "Čl. 2 odst. 1 písm. a)"
    assert path_label(cz, f"cz:{ACT}:par5.odst1.pism_b") == "§ 5 odst. 1 písm. b)"
    # numbered points (definitions, e.g. NIS2 Čl. 6) are cited "bod N", letters below them "písm. x)"
    eu.add(Provision(f"eu:{EU}:cs:art3.pt5", "eu", EU, "cs", "point", "5)", "5", "pojem", None, f"eu:{EU}:cs:art3"))
    eu.add(Provision(f"eu:{EU}:cs:art3.pt5.subpt_a", "eu", EU, "cs", "subpoint", "a)", "a", "x", None,
                     f"eu:{EU}:cs:art3.pt5"))
    assert path_label(eu, f"eu:{EU}:cs:art3.pt5") == "Čl. 3 bod 5"
    assert path_label(eu, f"eu:{EU}:cs:art3.pt5.subpt_a") == "Čl. 3 bod 5 písm. a)"


def test_default_threshold_per_embedder():
    assert default_threshold("lexical:char3-5") == 0.20
    assert default_threshold("lexical:char3-5+rollup-mean") == 0.20
    assert default_threshold("embed:intfloat/multilingual-e5-base") == 0.85
    assert default_threshold("embed:some/other-model") == 0.85
    assert default_threshold("unknown") == 0.20


def test_default_acts_adds_gold_references(tmp_path):
    p = tmp_path / "g.jsonl"
    save_jsonl([gl("art1.par1", "par1.odst1", "264/2025"), gl("art2.par2", "par3", "181/2014")], p)
    assert default_acts("32022L2555", "cz", gold=p) == ["264/2025", "181/2014"]
    assert default_acts("32099L0001", "cz", gold=tmp_path / "missing.jsonl") == []


class FakeEurLex:
    def __init__(self):
        self.calls = []

    def document(self, celex, lang):
        self.calls.append((celex, lang))
        return eu_doc()


class FakeESbirka:
    def __init__(self, broken=()):
        self.broken = set(broken)
        self.calls = []

    def document(self, ref, version=None):
        self.calls.append((ref, version))
        if ref in self.broken:
            raise LookupError(f"{ref} not found")
        return cz_doc(ref)


def test_load_documents_skips_unfetchable_acts():
    eurlex, esb = FakeEurLex(), FakeESbirka(broken={"2/2099"})
    logs = []
    eu, docs = load_documents(EU, "cz", [ACT, "2/2099"], eurlex=eurlex, national_client=esb, log=logs.append)
    assert eurlex.calls == [(EU, "cs")]
    assert [d.act_id for d in docs] == [ACT]
    assert esb.calls == [(ACT, None), ("2/2099", None)]
    assert "skipping 2/2099" in logs[0]
    with pytest.raises(LookupError):
        load_documents(EU, "cz", ["2/2099"], eurlex=eurlex, national_client=esb)
    with pytest.raises(ValueError):
        load_documents(EU, "fr", [ACT], eurlex=eurlex, national_client=esb)


def test_load_documents_pins_default_version():
    esb = FakeESbirka()
    load_documents("32022L2555", "cz", ["264/2025"], eurlex=FakeEurLex(), national_client=esb)
    assert esb.calls == [("264/2025", "2025-11-01")]


# --- pipeline ------------------------------------------------------------------------------


def test_align_links_gaps_and_extras():
    res = align(eu_doc(), cz_doc(), threshold=0.25, now=NOW)
    assert isinstance(res, AlignmentResult)
    assert res.method == "lexical:char3-5" and res.threshold == 0.25
    by = res.by_eu()
    a = by[f"eu:{EU}:cs:art2.par1.pt_a"]
    assert a.links == [f"cz:{ACT}:par5.odst1.pism_a"]
    assert a.candidates[0].status == "candidate"
    # the Commission's review clause has nothing similar in national law, but no member state
    # transposes it: set aside (final provisions), not a gap candidate
    assert f"eu:{EU}:cs:art3.par1" in res.unlinked
    assert f"eu:{EU}:cs:art3.par1" not in res.gap_candidates
    assert res.leftovers.eu_out_of_scope[f"eu:{EU}:cs:art3.par1"] == "final_provisions"
    extras = {x["national_id"] for x in res.extra_rule_candidates}
    assert f"cz:{ACT}:par12.odst1" in extras  # official notice board: nothing in the directive
    assert f"cz:{ACT}:par5.odst1.pism_a" not in extras
    assert all(x["status"] == "candidate" for x in res.extra_rule_candidates)
    s = res.summary()
    assert s["eu_units"] == len(res.provisions) and s["gap_candidates"] == len(res.gap_candidates)
    assert s["extra_rule_sections"] == 1


def test_align_json_export_keeps_evidence():
    res = align(eu_doc(), [cz_doc()], threshold=0.25, now=NOW)
    d = json.loads(res.to_json())
    assert d["status"] == "candidate" and "not legal findings" in d["disclaimer"]
    assert d["created_at"] == "2026-10-12T08:30:00Z"
    assert d["method"] == "lexical:char3-5" and d["judge"] is None
    row = next(r for r in d["provisions"] if r["eu_id"].endswith("art2.par1.pt_a"))
    assert row["eu_label"] == "Čl. 2 odst. 1 písm. a)"
    assert row["eu_url"].startswith("https://eur-lex.example/")
    assert "24 hodin" in row["eu_text"]
    top = row["candidates"][0]
    assert top["rank"] == 1 and top["national_label"] == "§ 5 odst. 1 písm. a)"
    nat = d["national_texts"][top["national_id"]]
    assert nat["act"] == ACT and "24 hodin" in nat["text"] and nat["url"]
    assert [g["eu_id"] for g in d["gap_candidates"]] == res.gap_candidates
    assert d["schema"] == 2 and "leftovers" in d
    assert all(g["status"] == "candidate" for g in d["gap_candidates"])


def test_align_with_judge_uses_related_labels():
    a_id, n_id = f"eu:{EU}:cs:art2.par1.pt_a", f"cz:{ACT}:par5.odst1.pism_a"
    judge = FakeJudge({(a_id, n_id): Label.SAME})
    res = align(eu_doc(), cz_doc(), judge=judge, judge_k=2, now=NOW)
    assert res.judge == "fake-judge"
    by = res.by_eu()
    assert by[a_id].links == [n_id] and by[a_id].label == "same"
    # every other EU unit: all candidates judged unrelated -> missing -> gap candidate
    assert by[f"eu:{EU}:cs:art1.par1"].label == "missing"
    assert by[f"eu:{EU}:cs:art1.par1"].gap
    assert len(judge.calls) == 2 * len(res.provisions)
    d = res.to_dict()
    row = next(r for r in d["provisions"] if r["eu_id"] == a_id)
    assert row["label"] == "same" and row["judgments"][0]["status"] == "unreviewed"
    assert d["summary"]["judgment_labels"]["same"] == 1


def test_align_with_rule_judge_runs_keyless():
    res = align(eu_doc(), cz_doc(), judge=RuleJudge(), judge_k=1, now=NOW)
    assert res.judge == "rule-judge"
    assert all(j.method == "heuristic" and j.confidence <= 0.4 for a in res.provisions for j in a.judgments)


def test_make_judge():
    assert make_judge("none") is None
    assert isinstance(make_judge("rule"), RuleJudge)
    with pytest.raises(ValueError):
        make_judge("oracle")


# --- evaluation ------------------------------------------------------------------------------


def test_retrieval_recall_levels():
    res = align(eu_doc(), cz_doc(), k=5, now=NOW)
    r = retrieval(res.match, GOLD, ks=(1, 5))
    prov = r["provision"]
    assert prov["items"] == 4 and prov["gold_links"] == 4
    assert 0 < prov["recall@1"] <= prov["recall@5"] <= 1
    # the whole-§ gold link: any unit of § 9 counts
    assert r["section"]["recall@5"] == 1.0
    art = r["article"]
    assert art["items"] == 2  # art1, art2
    assert art["recall@1"] == 1.0


def test_link_scores_counts():
    eu, cz = eu_doc(), cz_doc()
    pred = [
        (f"eu:{EU}:cs:art2.par1.pt_a", f"cz:{ACT}:par5.odst1.pism_a"),  # correct
        (f"eu:{EU}:cs:art2.par2", f"cz:{ACT}:par9.odst1"),  # correct (gold is all of § 9)
        (f"eu:{EU}:cs:art2.par1.pt_b", f"cz:{ACT}:par1.odst1"),  # wrong
        (f"eu:{EU}:cs:art3.par1", f"cz:{ACT}:par12.odst1"),  # not covered by the table
    ]
    s = link_scores(pred, GOLD, eu, [cz])
    assert s["predicted"] == 4 and s["correct"] == 2
    assert s["precision"] == 0.5 and s["recall"] == 0.5
    assert s["in_scope"]["predicted"] == 3 and s["in_scope"]["precision"] == pytest.approx(2 / 3)
    assert s["eu_recall"] == 0.5
    sec = link_scores(pred, GOLD, eu, [cz], level="section")
    assert sec["gold"] == 4 and sec["correct"] == 2


def test_link_on_ancestor_does_not_credit_gold_points():
    """A link on a lead-in (art2.par1) must not count as found for the points the table lists."""
    eu, cz = eu_doc(), cz_doc()
    pred = [(f"eu:{EU}:cs:art2.par1", f"cz:{ACT}:par5.odst1.pism_a")]
    s = link_scores(pred, GOLD, eu, [cz])
    assert s["correct"] == 0 and s["found"] == 0 and s["eu_found"] == 0
    # ... while a link on a part of a gold provision does count for it
    gold = [gl("art2.par1", "par5.odst1")]
    pred = [(f"eu:{EU}:cs:art2.par1.pt_a", f"cz:{ACT}:par5.odst1.pism_a")]
    s = link_scores(pred, gold, eu, [cz])
    assert s["correct"] == 1 and s["found"] == 1 and s["eu_recall"] == 1.0


def test_coverage_credits_only_own_unit_or_parts():
    from dataclasses import replace as dc_replace

    res = align(eu_doc(), cz_doc(), threshold=0.99)
    provs = [dc_replace(a, links=[f"cz:{ACT}:par5.odst1"] if a.eu_id.endswith("art2.par1") else [])
             for a in res.provisions]
    c = coverage(dc_replace(res, provisions=provs), GOLD)
    assert c["gold_eu_provisions_with_link"] == 0  # pt_a and pt_b have no link of their own


def test_metrics_are_not_rounded_before_display():
    eu, cz = eu_doc(), cz_doc()
    pred = [(f"eu:{EU}:cs:art2.par1.pt_a", f"cz:{ACT}:par5.odst1.pism_a"),
            (f"eu:{EU}:cs:art2.par1.pt_b", f"cz:{ACT}:par1.odst1"),
            (f"eu:{EU}:cs:art2.par2", f"cz:{ACT}:par1.odst1")]
    assert link_scores(pred, GOLD, eu, [cz])["precision"] == 1 / 3


def test_evaluate_report_and_text():
    report = evaluate(eu_doc(), [cz_doc()], GOLD, ks=(1, 5), threshold=0.25)
    pub = public(report)
    json.dumps(pub)  # serialisable
    assert set(pub["retrieval"]) == {"provision", "paragraph", "section", "article"}
    assert pub["threshold"] == 0.25 and pub["gold_pairs"] == 4
    assert pub["sweep"] and pub["best_threshold"]["threshold"] in [r["threshold"] for r in pub["sweep"]]
    c = pub["coverage"]
    assert c["gold_eu_provisions"] == 4 and c["eu_units_covered_by_table"] >= 4
    text = format_report(report)
    assert "R@1" in text and "not legal correctness" in text


def test_evaluate_with_judge():
    report = evaluate(eu_doc(), [cz_doc()], GOLD, ks=(1,), judge=RuleJudge(), judge_k=2)
    j = report["judge"]
    assert j["judge"] == "rule-judge" and j["judged_pairs_in_scope"] > 0
    assert "links" in j and "coverage" in j
    assert "Judge (rule-judge)" in format_report(report)


def test_coverage_counts_gaps_listed_in_table():
    res = align(eu_doc(), cz_doc(), threshold=0.99)  # nothing passes: every unit is unlinked
    c = coverage(res, GOLD)
    assert c["eu_units_with_link"] == 0
    assert c["unlinked_eu_units"] == len(res.provisions)
    assert c["unlinked_listed_in_table"] == 5  # art1.par1, art2.par1 (+a, b), art2.par2
    # the review clause (art3, "Přezkum") is a final provision, not a gap candidate
    assert c["gap_candidates"] == 5 and c["gap_candidates_listed_in_table"] == 5


# --- CLI ------------------------------------------------------------------------------------


def test_cli_align_writes_json(tmp_path, capsys):
    out = tmp_path / "out.json"
    rc = main(["align", EU, "cz", "--acts", ACT, "-e", "lexical", "--threshold", "0.25", "-o", str(out)],
              eurlex=FakeEurLex(), esbirka=FakeESbirka())
    assert rc == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["threshold"] == 0.25 and d["national"][0]["act_id"] == ACT
    err = capsys.readouterr().err
    assert "gap candidates" in err and "not findings" in err


def test_cli_align_rule_judge_to_stdout(capsys):
    rc = main(["align", EU, "cz", "--acts", ACT, "-e", "lexical", "--judge", "rule", "--judge-k", "1"],
              eurlex=FakeEurLex(), esbirka=FakeESbirka())
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["judge"] == "rule-judge"


def test_cli_align_anthropic_needs_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = main(["align", EU, "cz", "--acts", ACT, "--judge", "anthropic"], eurlex=FakeEurLex(), esbirka=FakeESbirka())
    assert rc == 2 and "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_cli_eval(tmp_path, capsys):
    gold = tmp_path / "gold.jsonl"
    save_jsonl(GOLD, gold)
    report = tmp_path / "report.json"
    rc = main(["eval", "nis2-cz", "--gold", str(gold), "--acts", ACT, "-e", "lexical", "--k", "1,3", "--errors", "2",
               "-o", str(report)], eurlex=FakeEurLex(), esbirka=FakeESbirka())
    assert rc == 0
    out = capsys.readouterr().out
    assert "R@1" in out and "R@3" in out
    assert json.loads(report.read_text(encoding="utf-8"))["ks"] == [1, 3]
    rc = main(["eval", "nis2-cz", "--gold", str(gold), "--acts", ACT, "-e", "hybrid-lex", "-f", "json"],
              eurlex=FakeEurLex(), esbirka=FakeESbirka())
    assert rc == 0 and "retrieval" in json.loads(capsys.readouterr().out)


def test_cli_dense_embedder_without_extra_fails_cleanly(monkeypatch, capsys):
    import importlib.util as iu

    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec", lambda name, *a: None if name == "sentence_transformers" else real(name, *a))

    class NoFetch:
        def __getattr__(self, name):
            raise AssertionError("texts must not be fetched before the embedder is checked")

    rc = main(["align", EU, "cz", "--acts", ACT, "--embedder", "e5"], eurlex=NoFetch(), esbirka=NoFetch())
    err = capsys.readouterr().err
    assert rc == 2 and "openacquis[embeddings]" in err and "Traceback" not in err


def test_cli_eval_bad_k(tmp_path, capsys):
    gold = tmp_path / "gold.jsonl"
    save_jsonl(GOLD, gold)
    assert main(["eval", "nis2-cz", "--gold", str(gold), "--k", "a,b"], eurlex=FakeEurLex(), esbirka=FakeESbirka()) == 2


def test_bundled_gold_set_loads():
    from openacquis.align import gold_path
    from openacquis.evaluate import load_gold

    p = gold_path("32022L2555", "cz")
    assert p is not None
    gold = load_gold(p)
    assert len(gold) == 569 and all(g.national_id.startswith("cz:264/2025:") for g in gold)


# --- website ----------------------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
build_site = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("build_site", build_site)
_spec.loader.exec_module(build_site)

ENTRY = build_site.AlignmentEntry(EU, "TEST", "CZE", "cz", "Czechia", "unused.jsonl", "Test table",
                                  "https://psp.example/t", "https://psp.example/h")


def test_alignment_page_data_shape():
    res = align(eu_doc(), cz_doc(), threshold=0.25, now=NOW)
    gold = GOLD + [GoldLink(f"eu:{EU}:cs:art2", f"cz:{ACT}:par5")]  # article-level entry
    d = build_site.alignment_page_data(ENTRY, res, gold, now=NOW)
    json.dumps(d)
    assert d["id"] == f"{EU}_cz" and d["built_at"] == "2026-10-12T08:30:00Z" and d["mode"] == "live"
    arts = {a["id"].split(":")[-1]: a for a in d["articles"]}
    assert set(arts) == {"art1", "art2", "art3"}
    assert arts["art2"]["official"] == [f"cz:{ACT}:par5"]
    rows = {r["id"].split(":")[-1]: r for a in d["articles"] for r in a["rows"]}
    assert set(rows) == {"art1.par1", "art2.par1", "art2.par2", "art3.par1"}
    r = rows["art2.par1"]
    assert r["official"] == [f"cz:{ACT}:par5.odst1.pism_a", f"cz:{ACT}:par5.odst1.pism_b"]
    assert r["official_eu"] == ["Čl. 2 odst. 1 písm. a)", "Čl. 2 odst. 1 písm. b)"]
    assert r["machine"][0]["id"].startswith(f"cz:{ACT}:par5.odst1")
    assert r["agreement"] in ("match", "same_section")
    assert rows["art3.par1"]["agreement"] == "no_table_entry"
    assert "24 hodin" in r["text"] and r["url"].startswith("https://eur-lex.example/")
    for nid in r["official"] + [m["id"] for m in r["machine"]]:
        assert d["national_texts"][nid]["url"].startswith("https://e-sbirka.example/")
    s = d["summary"]
    assert s["rows"] == 4 and s["rows_with_official"] == 3
    assert s["match"] + s["same_section"] + s["differs"] + s["article_level"] + s["no_table_entry"] == 4


def test_alignment_page_article_level_gold_covers_paragraph_rows():
    res = align(eu_doc(), cz_doc(), threshold=0.25, now=NOW)
    gold = GOLD + [GoldLink(f"eu:{EU}:cs:art3", f"cz:{ACT}:par12")]  # the table cites only "Čl. 3"
    d = build_site.alignment_page_data(ENTRY, res, gold, now=NOW)
    rows = {r["id"].split(":")[-1]: r for a in d["articles"] for r in a["rows"]}
    assert rows["art3.par1"]["official"] == [] and rows["art3.par1"]["agreement"] == "article_level"
    assert d["summary"]["rows_with_official"] == 4 and d["summary"]["article_level"] == 1


def test_write_alignments_live_static_and_fallback(tmp_path):
    res = align(eu_doc(), cz_doc(), threshold=0.25, now=NOW)
    data = build_site.alignment_page_data(ENTRY, res, GOLD, now=NOW)
    static = tmp_path / "static"
    out = tmp_path / "data"

    review = build_site.review_page_data(ENTRY, res, now=NOW)

    # live build, refreshing the static copy
    missing = build_site.write_alignments(out, [ENTRY], write_static=True, static_dir=static, reviews_dir=tmp_path,
                                          build=lambda e, log: {"alignment": data, "review": review})
    assert missing == []
    assert json.loads((out / "align" / f"{EU}_cz.json").read_text(encoding="utf-8"))["mode"] == "live"
    assert json.loads((static / f"{EU}_cz.json").read_text(encoding="utf-8"))["mode"] == "static"
    assert json.loads((out / "review" / f"{EU}_cz.json").read_text(encoding="utf-8"))["suggestions"]
    m = json.loads((out / "matrix" / f"{EU}.json").read_text(encoding="utf-8"))
    assert [c["code"] for c in m["countries"]] == ["cz"] and m["articles"][0]["cells"]["cz"]["href"]

    # live fails -> static copy
    def broken(e, log):
        raise TimeoutError("e-Sbírka timed out")

    logs = []
    assert build_site.write_alignments(out, [ENTRY], static_dir=static, build=broken, log=logs.append,
                                       reviews_dir=tmp_path) == []
    assert json.loads((out / "align" / f"{EU}_cz.json").read_text(encoding="utf-8"))["mode"] == "static"
    assert any("static copy" in m for m in logs)

    # offline, no static copy -> reported missing, nothing written
    assert build_site.write_alignments(tmp_path / "o", [ENTRY], offline=True, static_dir=tmp_path / "none") == [f"{EU}_cz"]


PORTALS = {"cz": ("https://e-sbirka.gov.cz/",), "sk": ("https://static.slov-lex.sk/",),
           "at": ("https://ris.bka.gv.at/", "https://www.ris.bka.gv.at/"), "de": ("https://www.gesetze-im-internet.de/",)}


def test_committed_static_alignment_is_valid():
    """The fallback copies the Pages build uses when texts cannot be fetched."""
    assert {e.country_code for e in build_site.ALIGNMENTS} == {"cz", "sk", "at", "de"}
    for e in build_site.ALIGNMENTS:
        p = build_site.STATIC_DIR / f"{e.slug}.json"
        assert p.exists(), f"run python scripts/build_site.py --write-static to create {p}"
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["schema"] == build_site.ALIGNMENT_SCHEMA and d["celex"] == e.celex
        assert d["summary"]["rows"] > 100
        assert d["has_official"] == bool(e.gold)
        if e.gold:
            assert d["summary"]["rows_with_official"] > 50
        assert d["method"].startswith("hybrid:") and "embed:" not in d["method"]  # stdlib only
        assert d["eu"]["language"] == {"cz": "cs", "sk": "sk", "at": "de", "de": "de"}[e.country_code]
        for a in d["articles"]:
            for r in a["rows"]:
                assert r["url"].startswith("https://eur-lex.europa.eu/")
                for nid in r["official"] + [m["id"] for m in r["machine"]]:
                    assert nid.startswith(e.country_code + ":")
                    url = d["national_texts"][nid]["url"]
                    assert url is None or url.startswith(PORTALS[e.country_code])
        rv = json.loads((build_site.STATIC_DIR / f"{e.slug}.review.json").read_text(encoding="utf-8"))
        kinds = {s["kind"] for s in rv["suggestions"]}
        assert "link" in kinds and "extra_rule" in kinds
        for s in rv["suggestions"]:
            assert (s["eu_id"] is None or s["eu_id"] in rv["eu_texts"])
            assert (s["national_id"] is None or s["national_id"] in rv["national_texts"])


def test_offline_site_build_includes_alignment(tmp_path):
    assert build_site.main(["--offline", "--out", str(tmp_path)]) == 0
    d = json.loads((tmp_path / "align" / "32022L2555_cz.json").read_text(encoding="utf-8"))
    assert d["mode"] == "static"
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    nis2 = next(x for x in index["directives"] if x["celex"] == "32022L2555")
    assert nis2["alignments"][0]["href"] == "alignment.html?id=32022L2555_cz"
    assert [a["country"] for a in nis2["alignments"]] == ["CZE", "SVK", "AUT", "DEU"]
    assert nis2["matrix_href"] == "matrix.html?celex=32022L2555"
    m = json.loads((tmp_path / "matrix" / "32022L2555.json").read_text(encoding="utf-8"))
    assert [c["code"] for c in m["countries"]] == ["cz", "sk", "at", "de"]
    art23 = next(a for a in m["articles"] if a["key"] == "art23")
    assert art23["heading"] == "Reporting obligations"
    assert art23["cells"]["cz"]["official"] and art23["cells"]["sk"]["official"] is None
    assert (tmp_path / "review" / "32022L2555_sk.json").exists()
    assert (tmp_path / "dataset" / "links.csv").exists() and (tmp_path / "dataset" / "datapackage.json").exists()


def test_site_never_uses_innerhtml():
    js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in js.splitlines() if not line.lstrip().startswith(("*", "/*", "//")))
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code and "document.write" not in code
    assert "renderAlignment" in (ROOT / "site" / "alignment.html").read_text(encoding="utf-8")


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="needs node")
def test_site_href_allow_list():
    """safeHref() judges the parsed protocol: tabs, newlines or control characters inside the scheme
    do not get a javascript: link through."""
    import re
    import subprocess

    js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    fn = re.search(r"function safeHref\(v\) \{.*?\n  \}", js, re.S).group(0)
    cases = ["java\tscript:x", "JaVa\nScRiPt:x", "\x01javascript:x", " javascript:x", "data:text/html,x",
             "vbscript:x", "https://eur-lex.europa.eu/x", "http://a.example/", "directive.html?celex=1"]
    script = (f"{fn}\nconst cases = {json.dumps(cases)};\n"
              "for (const loc of ['https://x.example/OpenAcquis/a.html', 'file:///tmp/site/a.html']) {\n"
              "  globalThis.location = {href: loc, protocol: new URL(loc).protocol};\n"
              "  console.log(JSON.stringify(cases.map(safeHref)));\n}\n")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.split()
    expected = [False] * 6 + [True, True, True]
    assert [json.loads(line) for line in out] == [expected, expected]


# --- live ---------------------------------------------------------------------------------------


@pytest.mark.live
def test_live_eval_nis2_cz_lexical():
    from openacquis.align import gold_path
    from openacquis.evaluate import load_gold

    eu, docs = load_documents("32022L2555", "cz")
    report = evaluate(eu, docs, load_gold(gold_path("32022L2555", "cz")), ks=(1, 5, 10))
    r = report["retrieval"]
    # numbers reported in docs/evaluation.md (2026-10-12); allow small drift if texts change
    assert r["provision"]["recall@5"] > 0.45
    assert r["article"]["recall@5"] > 0.8
