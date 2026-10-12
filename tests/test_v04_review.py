"""v0.4: gap / extra-rule candidates (leftovers), review records, the dataset export, and the review CLI."""

import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openacquis.align import align
from openacquis.cli import main
from openacquis.dataset import _csv, build_dataset, reviewed_rows
from openacquis.evaluate import leftover_ablation
from openacquis.leftovers import eu_scope, find_leftovers, national_boilerplate
from openacquis.provisions import Document, Provision
from openacquis.review import (
    ReviewError,
    _main,
    append_records,
    check_issue,
    load_reviews,
    make_record,
    merge,
    records_from_text,
    suggestion_id,
    validate_record,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_align import ACT, EU, GOLD, cz_doc, eu_doc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)


# --- leftovers --------------------------------------------------------------------------------


def big_eu() -> Document:
    d = eu_doc()

    def add(pid, kind, label, number, text="", heading=None, parent=None):
        d.add(Provision(f"eu:{EU}:cs:{pid}", "eu", EU, "cs", kind, label, number, text, heading,
                        f"eu:{EU}:cs:{parent}" if parent else None))

    add("art4", "article", "Článek 4", "4", heading="Vzdělávání")
    add("art4.par1", "paragraph", "1.", "1", "Členské státy podporují programy zvyšování povědomí o kybernetické hygieně "
        "ve školách a knihovnách.", parent="art4")
    add("art5", "article", "Článek 5", "5", heading="Skupina pro spolupráci")
    add("art5.par1", "paragraph", "1.", "1", "Skupina pro spolupráci přijme jednací řád a Komise jí poskytuje sekretariát.",
        parent="art5")
    return d


def big_cz() -> Document:
    d = cz_doc()

    def add(pid, kind, label, number, text="", heading=None, parent=None):
        d.add(Provision(f"cz:{ACT}:{pid}", "cz", ACT, "cs", kind, label, number, text, heading,
                        f"cz:{ACT}:{parent}" if parent else None))

    add("par2", "par", "§ 2", "2", heading="Vymezení pojmů")
    add("par2.odst1", "odst", "(1)", "1", "Pro účely tohoto zákona se rozumí aktivem informace nebo služba.", parent="par2")
    add("par30", "par", "§ 30", "30", heading="Přechodná ustanovení")
    add("par30.odst1", "odst", "(1)", "1", "Řízení zahájená přede dnem nabytí účinnosti se dokončí podle dosavadních předpisů.",
        parent="par30")
    add("par31", "par", "§ 31", "31", heading="Účinnost")
    add("par31.odst1", "odst", "(1)", "1", "Tento zákon nabývá účinnosti prvním dnem měsíce.", parent="par31")
    return d


def test_eu_scope_final_provisions_and_union_level():
    eu = big_eu()
    assert eu_scope(eu, f"eu:{EU}:cs:art3.par1") == "final_provisions"  # "Přezkum"
    assert eu_scope(eu, f"eu:{EU}:cs:art5.par1") == "union_level"  # Cooperation Group + Commission only
    assert eu_scope(eu, f"eu:{EU}:cs:art4.par1") is None  # member states
    assert eu_scope(eu, f"eu:{EU}:cs:art2.par2") is None
    other = Document("eu", EU, "fi")
    other.add(Provision(f"eu:{EU}:fi:art1", "eu", EU, "fi", "article", "1 artikla", "1", "Komissio", "Uudelleentarkastelu"))
    assert eu_scope(other, f"eu:{EU}:fi:art1") is None  # no keyword lists for this language


def test_national_boilerplate_categories():
    cz = big_cz()
    assert national_boilerplate(cz, f"cz:{ACT}:par2.odst1") == "definitions"
    assert national_boilerplate(cz, f"cz:{ACT}:par30.odst1") == "transitional"
    assert national_boilerplate(cz, f"cz:{ACT}:par31.odst1") == "entry_into_force"
    assert national_boilerplate(cz, f"cz:{ACT}:par9.odst1") == "penalties"  # "Přestupky"
    assert national_boilerplate(cz, f"cz:{ACT}:par12.odst1") is None
    sk = Document("sk", "1/2020", "sk")
    sk.add(Provision("sk:1/2020:cl1", "sk", "1/2020", "sk", "cl", "Čl. I", "1"))
    sk.add(Provision("sk:1/2020:cl2", "sk", "1/2020", "sk", "cl", "Čl. II", "2",
                     "Zákon č. 73/1998 Z. z. o štátnej službe v znení zákona č. 1/1999 Z. z. sa mení a dopĺňa takto:"))
    sk.add(Provision("sk:1/2020:cl2.bod1", "sk", "1/2020", "sk", "bod", "1.", "1", "V § 84 sa ...", None, "sk:1/2020:cl2"))
    sk.add(Provision("sk:1/2020:priloha1", "sk", "1/2020", "sk", "priloha", "Príloha č. 1", "1",
                     "Prevziať prílohu - Príloha č. 1\nPDF: https://static.slov-lex.sk/x.pdf"))
    sk.add(Provision("sk:1/2020:pozn1", "sk", "1/2020", "sk", "pozn", "1)", "1", "Zákon č. 2/2000 Z. z."))
    assert national_boilerplate(sk, "sk:1/2020:cl2.bod1") == "amendment"
    assert national_boilerplate(sk, "sk:1/2020:priloha1") == "no_text"
    assert national_boilerplate(sk, "sk:1/2020:pozn1") == "footnote"
    de = Document("de", "x", "de")
    de.add(Provision("de:x:par65", "de", "x", "de", "par", "§ 65", "65", "", "Bußgeldvorschriften"))
    de.add(Provision("de:x:par66", "de", "x", "de", "par", "§ 66", "66", "", "Übergangsregelungen"))
    assert national_boilerplate(de, "de:x:par65") == "penalties"
    assert national_boilerplate(de, "de:x:par66") == "transitional"


def test_leftovers_filters_and_structure():
    res = align(big_eu(), big_cz(), threshold=0.25, now=NOW)
    lo = res.leftovers
    gaps = set(res.gap_candidates)
    assert f"eu:{EU}:cs:art4.par1" in gaps  # nothing in the act on awareness programmes
    assert lo.eu_out_of_scope.get(f"eu:{EU}:cs:art5.par1") == "union_level"
    assert lo.eu_out_of_scope.get(f"eu:{EU}:cs:art3.par1") == "final_provisions"
    assert f"eu:{EU}:cs:art2.par1" not in gaps  # a lead-in whose points are linked
    g = next(x for x in lo.gap_candidates if x.eu_id.endswith("art4.par1"))
    assert g.article_gap and g.margin > 0 and g.status == "candidate"
    assert lo.gap_articles()[0]["whole_article"]
    extras = {x["national_id"] for x in res.extra_rule_candidates}
    assert f"cz:{ACT}:par12.odst1" in extras
    assert not extras & {f"cz:{ACT}:par2.odst1", f"cz:{ACT}:par30.odst1", f"cz:{ACT}:par31.odst1"}
    assert {lo.national_boilerplate[f"cz:{ACT}:par30.odst1"], lo.national_boilerplate[f"cz:{ACT}:par31.odst1"]} == {
        "transitional", "entry_into_force"}
    assert [s["section_id"] for s in lo.extra_rule_sections()] == [f"cz:{ACT}:par12"]


def test_leftovers_v03_mode_and_margin():
    res = align(big_eu(), big_cz(), threshold=0.25, now=NOW)
    links = {a.eu_id: a.links for a in res.provisions}
    v03 = find_leftovers(res.match, links, threshold=0.25, acts=[ACT], gap_margin=0, scope_filter=False,
                         boilerplate_filter=False, structure=False)
    assert [g.eu_id for g in v03.gap_candidates] == res.unlinked
    strict = find_leftovers(res.match, links, threshold=0.25, acts=[ACT], gap_margin=0.2)
    assert len(strict.gap_candidates) <= len(res.gap_candidates)
    assert all(g.best_score < 0.05 for g in strict.gap_candidates)
    none = find_leftovers(res.match, links, threshold=0.25, acts=["other/act"])
    assert none.extra_rule_candidates == []  # only the main act's units can be extra rules


def test_leftover_ablation_report():
    res = align(big_eu(), big_cz(), threshold=0.25, now=NOW)
    ab = leftover_ablation(res, GOLD)
    steps = [r["step"] for r in ab["rows"]]
    assert steps[0].startswith("v0.3") and steps[-1].startswith("+ gap margin")
    assert 0 <= ab["base_rate_gap"] <= 1 and 0 <= ab["base_rate_extra"] <= 1
    assert ab["rows"][0]["gap_candidates"] == len(res.unlinked)


# --- review records -----------------------------------------------------------------------------

EU_ID = "eu:32022L2555:cs:art23.par4.pt_a"
NAT_ID = "cz:264/2025:par16.odst1"


def rec(**kw):
    base = dict(directive="32022L2555", country="cz", kind="link", eu_id=EU_ID, national_id=NAT_ID,
                verdict="confirm", reviewer="octocat", machine={"label": "candidate", "score": 0.58, "rank": 1},
                day="2026-10-12")
    base.update(kw)
    return make_record(**base)


def test_make_and_validate_record():
    r = rec()
    assert r["suggestion_id"] == suggestion_id("link", EU_ID, NAT_ID) == f"link:{EU_ID}>{NAT_ID}"
    assert validate_record(r) is r
    g = rec(kind="gap", national_id=None, verdict="correct", corrected_ids=["cz:264/2025:par16.odst2"])
    assert g["suggestion_id"] == f"gap:{EU_ID}>-"
    x = rec(kind="extra_rule", eu_id=None, verdict="correct", corrected_ids=["eu:32022L2555:cs:art21.par2.pt_a"])
    assert x["corrected_ids"] == ["eu:32022L2555:cs:art21.par2.pt_a"]


@pytest.mark.parametrize("change, message", [
    ({"verdict": "maybe"}, "verdict must be one of"),
    ({"reviewer": "not a handle!"}, "GitHub user name"),
    ({"date": "2026-13-40"}, "date"),
    ({"national_id": "sk:69/2018:par1"}, "belongs to SK, not CZ"),
    ({"eu_id": "eu:32019L1937:cs:art1"}, "belongs to 32019L1937"),
    ({"verdict": "correct"}, "needs corrected_ids"),
    ({"corrected_ids": ["cz:264/2025:par1"]}, "must be empty"),
    ({"note": "x" * 2001}, "longer than 2000"),
    ({"suggestion_id": "link:x>y"}, "suggestion_id must be"),
    ({"machine": {"score": "high"}}, "machine.score must be a number"),
    ({"extra": 1}, "unknown fields"),
])
def test_validate_record_rejects(change, message):
    r = dict(rec())
    r.update(change)
    with pytest.raises(ReviewError, match=message):
        validate_record(r)


def test_validate_known_ids():
    with pytest.raises(ReviewError, match="not a provision of this alignment"):
        validate_record(rec(), known_ids={EU_ID})
    validate_record(rec(), known_ids={EU_ID, NAT_ID})


def test_records_from_issue_body_and_jsonl():
    r = rec()
    body = ("### Review record\n\n```json\n" + json.dumps(r, indent=1) + "\n```\n\n### Anything else? (optional)\n\n"
            "_No response_\n\n### Licence\n\n- [X] I agree that this review is published under CC-BY-4.0 ...")
    assert records_from_text(body) == [r]
    assert records_from_text(json.dumps(r) + "\n\n" + json.dumps(r)) == [r, r]
    with pytest.raises(ValueError):
        records_from_text("no json here")


def test_js_review_record_shape_validates():
    """The record site/assets/app.js builds (reviewRecord) passes the Python validator."""
    js_like = {"schema": 1, "suggestion_id": f"link:{EU_ID}>{NAT_ID}", "directive": "32022L2555", "country": "cz",
               "kind": "link", "eu_id": EU_ID, "national_id": NAT_ID,
               "machine": {"label": "candidate", "method": "hybrid:z-query(...)", "generator": "openacquis 0.4.0",
                           "score": 0.4242, "rank": 1},
               "verdict": "confirm", "corrected_ids": [], "reviewer": "test-user", "date": "2026-10-12", "note": ""}
    validate_record(js_like)
    js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    for key in js_like:
        assert key in js  # every field is produced by the page


def test_merge_statuses():
    a = rec(reviewer="alice")
    b = rec(reviewer="bob")
    c1 = rec(kind="gap", national_id=None, verdict="correct", corrected_ids=["cz:264/2025:par16"], reviewer="alice")
    c2 = rec(kind="gap", national_id=None, verdict="correct", corrected_ids=["cz:264/2025:par17"], reviewer="bob")
    x = rec(kind="extra_rule", eu_id=None, national_id="cz:264/2025:par44.odst1", verdict="reject", reviewer="alice")
    later = rec(reviewer="alice", verdict="reject", day="2026-10-13")
    ds = {d.suggestion_id: d for d in merge([a, b, c1, c2, x])}
    assert ds[a["suggestion_id"]].status == "confirmed" and ds[a["suggestion_id"]].reviewers == ["alice", "bob"]
    assert ds[c1["suggestion_id"]].status == "disputed"
    assert ds[x["suggestion_id"]].status == "rejected" and ds[x["suggestion_id"]].links() == []
    assert ds[a["suggestion_id"]].links() == [(EU_ID, NAT_ID)]
    assert {d.suggestion_id: d.status for d in merge([a, b, later])}[a["suggestion_id"]] == "disputed"
    assert merge([c1])[0].links() == [(EU_ID, "cz:264/2025:par16")]


def test_append_and_load_reviews(tmp_path):
    r = rec()
    paths = append_records([r, r], tmp_path)
    assert [p.name for p in paths] == ["32022L2555_cz.jsonl"]
    assert append_records([r], tmp_path) == []  # already there
    assert load_reviews(tmp_path) == [r]
    (tmp_path / "32022L2555_sk.jsonl").write_text(json.dumps(r) + "\n", encoding="utf-8")
    with pytest.raises(ReviewError, match="belongs in 32022L2555_cz.jsonl"):
        load_reviews(tmp_path)


def test_bundled_reviews_validate():
    load_reviews()  # data/reviews/ (may be empty) must always validate


# --- CLI ------------------------------------------------------------------------------------------


def test_cli_review_validate_add_stats(tmp_path, capsys):
    f = tmp_path / "batch.jsonl"
    f.write_text(json.dumps(rec()) + "\n", encoding="utf-8")
    assert main(["review", "validate", str(f)]) == 0
    assert "1 valid review record" in capsys.readouterr().out
    bad = tmp_path / "bad.md"
    bad.write_text("```json\n" + json.dumps({**rec(), "verdict": "maybe"}) + "\n```", encoding="utf-8")
    assert main(["review", "validate", str(bad), "-f", "json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is False and "verdict" in out["errors"][0]
    d = tmp_path / "reviews"
    assert main(["review", "add", str(f), "--dir", str(d)]) == 0
    assert (d / "32022L2555_cz.jsonl").exists()
    capsys.readouterr()
    assert main(["review", "stats", "--dir", str(d)]) == 0
    assert "1 review records, 1 suggestions reviewed: confirmed 1" in capsys.readouterr().out
    assert main(["review", "validate", str(tmp_path / "missing.jsonl")]) == 2
    capsys.readouterr()
    # --author: a pull request's records must all be the PR author's own
    reviewer = rec()["reviewer"]
    assert main(["review", "validate", str(f), "--author", reviewer.upper()]) == 0
    capsys.readouterr()
    assert main(["review", "add", str(f), "--dir", str(d), "--author", "someone-else"]) == 1
    assert "is not 'someone-else'" in capsys.readouterr().err


# --- dataset ---------------------------------------------------------------------------------------


def test_dataset_build(tmp_path):
    reviews = tmp_path / "reviews"
    append_records([rec(), rec(kind="gap", national_id=None, eu_id="eu:32022L2555:cs:art36", verdict="correct",
                               corrected_ids=["cz:264/2025:par59"]),
                    rec(eu_id="eu:32022L2555:cs:art1.par1", national_id="cz:264/2025:par44.odst1", verdict="reject")],
                   reviews)
    out = tmp_path / "dataset"
    s = build_dataset(out, reviews_dir=reviews, now=NOW)
    assert s.by_pair["32022L2555 × CZ"] == 568 + 2  # 569 gold links (568 distinct pairs) + 2 reviewed
    assert s.reviewed == 2 and s.review_records == 3
    rows = list(csv.DictReader((out / "links.csv").open(encoding="utf-8")))
    assert len(rows) == s.rows == s.official + 2
    off = next(r for r in rows if r["source"] == "official_correlation_table")
    assert off["status"] == "listed" and off["source_url"].startswith("https://www.psp.cz/")
    assert off["eu_provision"] and ":" not in off["eu_provision"]
    revd = [r for r in rows if r["source"] == "human_review"]
    assert {r["status"] for r in revd} == {"confirmed", "corrected"}
    assert {r["relation"] for r in rows} == {"transposition"}
    assert all(r["reviewers"] == "octocat" for r in revd)
    pkg = json.loads((out / "datapackage.json").read_text(encoding="utf-8"))
    assert pkg["licenses"][0]["name"] == "CC-BY-4.0"
    assert any("eur-lex" in src["path"] for src in pkg["sources"])
    assert [f["name"] for f in pkg["resources"][0]["schema"]["fields"]][:3] == ["link_id", "directive", "country"]
    assert len((out / "links.jsonl").read_text(encoding="utf-8").splitlines()) == s.rows
    assert len((out / "reviews.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    assert "CC-BY-4.0" in (out / "README.md").read_text(encoding="utf-8")


def test_reviewed_rows_skip_rejected_and_disputed():
    rows = reviewed_rows([rec(reviewer="a"), rec(reviewer="b", verdict="reject")])
    assert rows == []


def test_reviewed_rows_of_baseline_pairs_are_existing_law():
    # Pay Transparency x AT is a baseline pair (the act was not notified): a confirmed review says the
    # existing law covers the EU provision, not that it transposes it
    r = rec(directive="32023L0970", country="at", eu_id="eu:32023L0970:de:art7.par1",
            national_id="at:10008858:par1")
    rows = reviewed_rows([r], baseline={("32023L0970", "at")})
    assert [x["relation"] for x in rows] == ["existing_law"]
    assert reviewed_rows([r], baseline=set())[0]["relation"] == "transposition"
    assert reviewed_rows([rec()])[0]["relation"] == "transposition"  # NIS2 x CZ is no baseline
    from openacquis.dataset import _baseline_pairs

    assert ("32023L0970", "at") in _baseline_pairs() and ("32022L2555", "cz") not in _baseline_pairs()


def test_cli_dataset_build(tmp_path, capsys):
    reviews = tmp_path / "r"
    reviews.mkdir()
    assert main(["dataset", "build", "-o", str(tmp_path / "ds"), "--reviews", str(reviews)]) == 0
    out = capsys.readouterr().out
    assert "from official correlation tables, 0 from human review" in out and "32022L2555 × CZ: 568" in out
    shutil.rmtree(tmp_path / "ds")


# --- hardening (review bot and dataset) -------------------------------------------------------------

LICENCE_OK = "### Licence\n\n- [X] I agree that this review is published under CC-BY-4.0 as part of the dataset."


def issue(*records, comment="_No response_", licence=LICENCE_OK, raw=None):
    block = raw if raw is not None else "\n".join(json.dumps(r, indent=1) for r in records)
    return (f"### Review record\n\n```json\n{block}\n```\n\n### Anything else? (optional)\n\n{comment}\n\n"
            f"{licence}\n")


EVIL = "@torvalds please look ![x](https://evil.example/p.png) [click](https://evil.example)"


def _no_user_text(errors):
    text = " ".join(errors)
    for bad in ("@", "evil", "<img", "]("):
        assert bad not in text, (bad, text)


def test_errors_do_not_echo_user_values():
    r = {**rec(), EVIL: 1, "national_id": "@github/security-team <img src=x>"}
    r["machine"] = {**r["machine"], EVIL: 2}
    with pytest.raises(ReviewError) as e:
        validate_record(r)
    _no_user_text(e.value.errors)
    assert any("unknown fields (1)" in m for m in e.value.errors)
    assert any("unknown machine fields (1)" in m for m in e.value.errors)
    assert any("suggestion_id does not match" in m for m in e.value.errors)
    # a markup-free act part that still is not an act number is rejected
    with pytest.raises(ReviewError, match="national_id must look like"):
        validate_record({**rec(), "national_id": "cz:![x](//evil.example/p.png):par1"})
    with pytest.raises(ReviewError, match="unknown fields: extra"):  # plain names are still listed
        validate_record({**rec(), "extra": 1})


@pytest.mark.parametrize("change", [
    {"reviewer": "octocat\n"}, {"country": "cz\n"}, {"directive": "32022L2555\n"}, {"date": "2026-10-12\n"},
])
def test_trailing_newline_rejected(change):
    with pytest.raises(ReviewError):
        validate_record({**rec(), **change})


def test_gap_country_newline_not_written(tmp_path):
    g = rec(kind="gap", national_id=None, verdict="reject")
    with pytest.raises(ReviewError):
        append_records([{**g, "country": "cz\n"}], tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", [
    {"machine": {"score": float("nan")}}, {"machine": {"score": float("inf")}}, {"schema": True}, {"schema": 1.0},
])
def test_non_finite_score_and_bool_schema_rejected(change):
    with pytest.raises(ReviewError):
        validate_record({**rec(), **change})


def test_nan_and_deep_nesting_in_input():
    line = json.dumps(rec()).replace('"score": 0.58', '"score": NaN')
    with pytest.raises(ValueError, match="NaN"):
        records_from_text(line)
    with pytest.raises(ValueError, match="NaN"):
        records_from_text("```json\n" + line + "\n```")
    with pytest.raises(ValueError, match="nested too deeply"):
        records_from_text("```json\n" + "[" * 100000 + "]" * 100000 + "\n```")


def test_cli_validate_deep_nesting_no_traceback(tmp_path, capsys):
    f = tmp_path / "deep.md"
    f.write_text("```json\n" + "[" * 100000 + "]" * 100000 + "\n```", encoding="utf-8")
    assert main(["review", "validate", str(f), "-f", "json"]) == 1
    assert "nested too deeply" in capsys.readouterr().out


def test_load_reviews_rejects_nan(tmp_path):
    line = json.dumps(rec()).replace('"score": 0.58', '"score": NaN')
    (tmp_path / "32022L2555_cz.jsonl").write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ReviewError, match="NaN"):
        load_reviews(tmp_path)


def test_records_only_from_review_record_section():
    r = rec()
    body = issue(raw='{"schema": 1}', comment="```json\n" + json.dumps(r) + "\n```")
    assert records_from_text(body) == [{"schema": 1}]
    assert records_from_text(issue(r)) == [r]


def test_check_issue():
    r = rec()
    assert check_issue(issue(r), "octocat") == {"valid": True, "records": 1, "errors": []}
    assert check_issue(issue(r), "OctoCat")["valid"]  # GitHub logins are case-insensitive
    out = check_issue(issue(r), "mallory")  # filing a review in someone else's name
    assert not out["valid"] and "reviewer must be the GitHub user who opened this issue" in out["errors"][0]
    out = check_issue(issue(r, licence="### Licence\n\n- [ ] I agree that this review is published under CC-BY-4.0"),
                      "octocat")
    assert not out["valid"] and out["errors"] == ["please tick the CC-BY-4.0 licence box"]
    # a ticked-looking line typed into the comment field does not count
    out = check_issue(issue(r, comment="- [x] I agree that this review is published under CC-BY-4.0",
                            licence="### Licence\n\n- [ ] no"), "octocat")
    assert not out["valid"]
    # a record in the comment field is not counted
    out = check_issue(issue(raw='{"schema": 1}', comment="```json\n" + json.dumps(r) + "\n```"), "octocat")
    assert not out["valid"] and out["records"] == 0
    out = check_issue(issue(raw="not json"), "octocat")
    assert not out["valid"] and "could not be read" in out["errors"][0]
    out = check_issue(issue({**r, EVIL: 1, "national_id": "@github/security-team <img src=x>"}), "octocat")
    assert not out["valid"]
    _no_user_text(out["errors"])
    many = [{**r, "verdict": "maybe", "date": "x", "note": 1}] * 30
    assert len(check_issue(issue(raw=json.dumps(many)), "octocat")["errors"]) <= 20


def test_check_issue_main(tmp_path, capsys):
    f = tmp_path / "issue.md"
    f.write_text(issue(rec()), encoding="utf-8")
    assert _main(["check-issue", str(f), "octocat"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert _main(["check-issue", str(f), "someone-else"]) == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False
    assert _main(["nope"]) == 2


def test_workflow_least_privilege():
    wf = (ROOT / ".github" / "workflows" / "reviews.yml").read_text(encoding="utf-8")
    assert "persist-credentials: false" in wf
    assert "openacquis.review check-issue" in wf
    validate, comment = wf.split("\n  comment:\n")
    assert "issues: write" not in validate and "checkout" not in comment


def test_csv_formula_injection_guarded():
    out = _csv([{"note": '=HYPERLINK("http://evil.example","x")', "reviewers": "octocat",
                 "raw_eu_ref": "-1", "source_title": "@x", "machine_score": 0.5}])
    row = next(csv.DictReader(out.splitlines()))
    assert row["note"] == '\'=HYPERLINK("http://evil.example","x")'
    assert row["raw_eu_ref"] == "'-1" and row["source_title"] == "'@x"
    assert row["reviewers"] == "octocat" and row["machine_score"] == "0.5"


def test_datapackage_structure(tmp_path):
    out = tmp_path / "ds"
    reviews = tmp_path / "r"
    reviews.mkdir()
    build_dataset(out, reviews_dir=reviews, now=NOW)
    pkg = json.loads((out / "datapackage.json").read_text(encoding="utf-8"))
    # tabular-data-package would require every resource to be tabular; the JSONL ones are not
    assert pkg["profile"] == "data-package"
    import re
    assert re.fullmatch(r"[a-z0-9._-]+", pkg["name"])
    names = [r["name"] for r in pkg["resources"]]
    assert len(names) == len(set(names))
    for res in pkg["resources"]:
        assert re.fullmatch(r"[a-z0-9._-]+", res["name"]) and (out / res["path"]).exists()
        if res.get("profile") == "tabular-data-resource":
            fields = [f["name"] for f in res["schema"]["fields"]]
            header = (out / res["path"]).read_text(encoding="utf-8").splitlines()[0].split(",")
            assert header == fields
            assert set(res["schema"]["primaryKey"]) <= set(fields)
            assert all(f["type"] in ("string", "number", "integer", "date") for f in res["schema"]["fields"])
    for line in (out / "links.jsonl").read_text(encoding="utf-8").splitlines()[:5]:
        json.loads(line, parse_constant=lambda c: pytest.fail(c))
