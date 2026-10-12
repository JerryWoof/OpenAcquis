"""Regression tests for the v0.4 evaluation review: rank-fusion thresholds and gap lists,
the scope filter's treatment of joint EU bodies, and the cluster-aware gap-list scores."""

import sys
from datetime import datetime, timezone
from pathlib import Path

from openacquis.align import (
    DEFAULT_THRESHOLDS,
    align,
    default_gap_margin,
    default_threshold,
    rank_only,
)
from openacquis.evaluate import (
    cross_validated_gap_margin,
    default_margins,
    format_report,
    gap_by_article,
    leftover_ablation,
    wilson,
)
from openacquis.leftovers import GapCandidate, Leftovers, eu_scope, find_leftovers
from openacquis.match import BM25Embedder, HybridEmbedder, LexicalEmbedder
from openacquis.provisions import Document, Provision

sys.path.insert(0, str(Path(__file__).parent))
from test_align import ACT, EU, GOLD  # noqa: E402
from test_v04_review import big_cz, big_eu  # noqa: E402

NOW = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)
RRF = "hybrid:rrf60(lexical:char3-5*1,embed:intfloat/multilingual-e5-base:sym*1)"


# --- reciprocal rank fusion ----------------------------------------------------------------


def test_rrf_does_not_fall_back_to_dense_hybrid_threshold():
    assert rank_only(RRF) and rank_only("hybrid-rrf")
    assert not rank_only("hybrid:z-query(lexical:char3-5*1,embed:intfloat/multilingual-e5-base:sym*1)")
    t = default_threshold(RRF)
    assert t == DEFAULT_THRESHOLDS["hybrid-rrf"] != DEFAULT_THRESHOLDS["hybrid-dense"]
    assert t < 2 / 61  # below the largest possible two-channel RRF score
    assert default_threshold("hybrid:rrf60(lexical:char3-5*1,bm25*1)") == t  # stdlib channels too
    assert default_gap_margin(RRF) < t / 10
    # other hybrids are unchanged
    assert default_threshold("hybrid:z-query(lexical:char3-5*1,embed:x*1)") == DEFAULT_THRESHOLDS["hybrid-dense"]
    assert default_threshold("hybrid:z-query(lexical:char3-5*1,bm25*0.5)") == DEFAULT_THRESHOLDS["hybrid"]


def test_rrf_alignment_has_links_but_no_gap_list():
    emb = HybridEmbedder([LexicalEmbedder(stem=True), BM25Embedder(stem=True)], fusion="rrf")
    logs = []
    res = align(big_eu(), big_cz(), embedder=emb, now=NOW, log=logs.append)
    assert res.threshold == DEFAULT_THRESHOLDS["hybrid-rrf"]
    assert res.gap_candidates == [] and res.leftovers.gap_note
    assert any("no gap list" in m for m in logs)
    assert "no_gap_list" in res.leftovers.eu_unlinked_not_gap.values() or not res.unlinked
    d = res.to_dict()
    assert d["gap_candidates"] == [] and d["leftovers"]["gap_note"] and d["summary"]["gap_note"]
    # the measurement path still sees the raw gap list, for comparison
    ab = leftover_ablation(res, GOLD)
    assert ab["rank_only"] and ab["default_margin"] == default_gap_margin(res.method)


def test_find_leftovers_gaps_off():
    res = align(big_eu(), big_cz(), threshold=0.25, now=NOW)
    links = {a.eu_id: a.links for a in res.provisions}
    lo = find_leftovers(res.match, links, threshold=0.25, acts=[ACT], gaps=False)
    assert lo.gap_candidates == [] and lo.gap_note
    assert lo.extra_rule_candidates  # extra rules are unaffected
    assert find_leftovers(res.match, links, threshold=0.25, acts=[ACT]).gap_note is None


# --- scope filter ----------------------------------------------------------------------------


def _eu(*units: tuple[str, str, str | None]) -> Document:
    d = Document("eu", EU, "cs")
    d.add(Provision(f"eu:{EU}:cs:art14", "eu", EU, "cs", "article", "Článek 14", "14", "", "Skupina pro spolupráci"))
    for pid, text, heading in units:
        d.add(Provision(f"eu:{EU}:cs:{pid}", "eu", EU, "cs", "paragraph", pid, "1", text, heading, f"eu:{EU}:cs:art14"))
    return d


def test_joint_bodies_are_not_union_level():
    d = _eu(("art14.par1", "Skupina pro spolupráci má tyto úkoly: vyměňovat si osvědčené postupy.", None),
            ("art14.par2", "Síť CSIRT si vyměňuje informace o incidentech.", None),
            ("art14.par3", "Komise přijme prováděcí akty a agentura ENISA vede rejstřík.", None),
            ("art14.par4", "Skupina pro spolupráci přijme jednací řád a Komise jí poskytuje sekretariát.", None))
    # Cooperation Group / CSIRTs network: made up of national representatives, so not set aside
    assert eu_scope(d, f"eu:{EU}:cs:art14.par1") is None
    assert eu_scope(d, f"eu:{EU}:cs:art14.par2") is None
    # Commission / ENISA only: still union level
    assert eu_scope(d, f"eu:{EU}:cs:art14.par3") == "union_level"
    assert eu_scope(d, f"eu:{EU}:cs:art14.par4") == "union_level"


def test_linked_out_of_scope_units_are_reported():
    res = align(big_eu(), big_cz(), threshold=0.25, now=NOW)
    links = {a.eu_id: a.links for a in res.provisions}
    art5 = f"eu:{EU}:cs:art5.par1"  # union level
    links[art5] = [f"cz:{ACT}:par12.odst1"]
    lo = find_leftovers(res.match, links, threshold=0.25, acts=[ACT])
    assert lo.eu_linked_out_of_scope.get(art5) == "union_level"
    assert art5 not in lo.eu_out_of_scope  # eu_out_of_scope only holds unlinked units
    assert "eu_linked_out_of_scope" in res.to_dict()["leftovers"]


# --- cluster-aware gap scores ---------------------------------------------------------------


def test_wilson():
    assert wilson(0, 0) is None
    lo, hi = wilson(13, 16)
    assert 0.55 < lo < 0.6 and 0.92 < hi < 0.94
    assert wilson(2, 2)[0] < wilson(12, 13)[0]


def test_gap_by_article_and_outside_top_article():
    lo = Leftovers(gap_candidates=[GapCandidate(f"e:art32.p{i}", None, None, 0.1, "e:art32", True) for i in range(12)]
                   + [GapCandidate(f"e:art{i}.p1", None, None, 0.1, f"e:art{i}", False) for i in (1, 2, 3, 4)])
    gold_eu = ["e:art1", "e:art2", "e:art3"]
    arts = gap_by_article(lo, gold_eu)
    assert arts[0] == {"article_id": "e:art32", "candidates": 12, "listed_in_table": 0}
    from openacquis.evaluate import _leftover_scores
    s = _leftover_scores(lo, gold_eu, [])
    assert s["gap_precision"] == 13 / 16
    assert s["gap_top_article"] == "e:art32" and s["gap_top_article_candidates"] == 12
    assert s["gap_precision_outside_top_article"] == 0.25 and s["gap_candidates_outside_top_article"] == 4


def test_ablation_margins_follow_the_method_and_margin_cv():
    assert default_margins("lexical:char3-5")[:4] == [0.0, 0.005, 0.0075, 0.01]
    assert default_margins("embed:intfloat/multilingual-e5-base")[3] == 0.005
    res = align(big_eu(), big_cz(), threshold=0.25, now=NOW)
    ab = leftover_ablation(res, GOLD)
    assert ab["default_margin"] == 0.01 and "gap_by_article" in ab
    assert "eu_set_aside" in ab["rows"][1] and "eu_set_aside_listed_in_table" in ab["rows"][1]
    cv = cross_validated_gap_margin(res, GOLD)
    assert set(cv) >= {"margins", "parity", "by_article", "note", "threshold"}
    assert len(cv["parity"]["chosen"]) == 2 and all(m in cv["margins"] for m in cv["parity"]["chosen"])
    assert cv["parity"]["candidates"] <= len(res.unlinked)
    assert "not re-tuned" in cv["note"]


def test_format_report_shows_cluster_and_cv_lines():
    from openacquis.evaluate import evaluate

    rep = evaluate(big_eu(), [big_cz()], GOLD, threshold=0.25)
    text = format_report(rep)
    assert "margin chosen out of sample" in text and "leave one article out" in text
