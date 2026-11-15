import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openacquis import CellarClient

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
build_site = importlib.util.module_from_spec(_spec)
sys.modules["build_site"] = build_site  # dataclasses need the module registered
_spec.loader.exec_module(build_site)

C = "http://publications.europa.eu/resource/authority/country/"
NOW = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)


def binding(**kw):
    return {k: {"type": "literal", "value": v} for k, v in kw.items()}


def result(*rows):
    return {"results": {"bindings": list(rows)}}


class FakeFetch:
    """Answers by CELEX; CELEX numbers in ``broken`` raise like a flaky endpoint."""

    def __init__(self, broken=()):
        self.broken = set(broken)
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        for celex in self.broken:
            if celex in query:
                raise TimeoutError("timed out")
        if "measure_national_implementing" in query:
            return result(
                binding(m="urn:m2", c=C + "CZE", reference="408/2025", title="Vyhláška", journal_date="2025-10-14"),
                binding(m="urn:m1", c=C + "CZE", reference="264/2025", title="Zákon o kybernetické bezpečnosti", journal_date="2025-06-11"),
                binding(m="urn:m3", c=C + "DEU", title="NIS2UmsuCG", act_type="Gesetz"),
            )
        return result(binding(w="urn:w", title="Directive (EU) 2022/2555", date="2022-12-14", deadline="2024-10-17"))


ENTRIES = [
    build_site.Entry("32022L2555", "NIS2", "Cybersecurity"),
    build_site.Entry("32019L0882", "European Accessibility Act", "Accessibility"),
]


def test_shapes_index_and_records():
    index, records = build_site.build_site_data(CellarClient(FakeFetch()), ENTRIES, now=NOW)
    assert index["built_at"] == "2026-10-12T08:30:00Z"
    assert index["mode"] == "live"
    assert len(index["member_states"]) == 27
    nis2 = index["directives"][0]
    assert "measures" not in nis2
    assert nis2["total_measures"] == 3 and nis2["countries_with_measures"] == 2
    assert nis2["transposition_deadlines"] == ["2024-10-17"]
    assert len(nis2["counts"]) == 27 and nis2["counts"]["CZE"] == 2 and nis2["counts"]["IRL"] == 0
    assert nis2["eurlex_nim_url"].endswith("CELEX:32022L2555")
    # measures sorted by country, then date
    assert [m["reference"] for m in records[0]["measures"]] == ["264/2025", "408/2025", None]


def test_failing_directive_is_recorded_not_fatal():
    fetch = FakeFetch(broken={"32019L0882"})
    index, records = build_site.build_site_data(CellarClient(fetch), ENTRIES, now=NOW)
    ok, failed = index["directives"]
    assert ok["error"] is None and ok["total_measures"] == 3
    assert "TimeoutError" in failed["error"]
    assert failed["total_measures"] == 0 and records[1]["measures"] == []


def test_retries_then_succeeds():
    calls, waits = [], []

    def flaky(query):
        calls.append(query)
        if len(calls) < 3:
            raise OSError("503")
        return {"ok": True}

    fetch = build_site.with_retries(flaky, retries=3, backoff=1, sleep=waits.append)
    assert fetch("q") == {"ok": True}
    assert waits == [1, 2]

    with pytest.raises(OSError):
        build_site.with_retries(lambda q: (_ for _ in ()).throw(OSError("down")), retries=1, sleep=waits.append)("q")


def test_cache(tmp_path):
    calls = []
    fetch = build_site.with_cache(lambda q: calls.append(q) or {"n": len(calls)}, tmp_path)
    assert fetch("a") == {"n": 1}
    assert fetch("a") == {"n": 1}
    assert calls == ["a"]


def test_write_site_data(tmp_path):
    (tmp_path / "32000L0001.json").write_text("{}")  # stale file from an older list
    index, records = build_site.build_site_data(CellarClient(FakeFetch()), ENTRIES[:1], now=NOW)
    build_site.write_site_data(tmp_path, index, records)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["32022L2555.json", "index.json"]
    rec = json.loads((tmp_path / "32022L2555.json").read_text(encoding="utf-8"))
    assert rec["built_at"] == "2026-10-12T08:30:00Z"
    assert rec["measures"][0]["title"] == "Zákon o kybernetické bezpečnosti"


def test_offline_build_from_bundled_samples(tmp_path):
    assert build_site.main(["--offline", "--out", str(tmp_path)]) == 0
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["mode"] == "offline-sample"
    celexes = {d["celex"] for d in index["directives"]}
    assert "32022L2555" in celexes
    assert celexes >= {e.celex for e in build_site.DIRECTIVES}  # every directive is listed
    for d in index["directives"]:
        assert d["error"] is None and (d["total_measures"] > 0) == d["measures_available"]
        assert (tmp_path / f"{d['celex']}.json").exists()
    assert sum(d["measures_available"] for d in index["directives"]) == len(build_site.sample_celexes())
    # every provision-level link points at data that was written
    nis2 = next(d for d in index["directives"] if d["celex"] == "32022L2555")
    assert index["dataset"] is True and nis2["dataset"] is True
    assert {a["href"].split("id=")[1] for a in nis2["alignments"]} == {
        "32022L2555_cz", "32022L2555_sk", "32022L2555_at", "32022L2555_de"}
    for a in nis2["alignments"]:
        slug = a["href"].split("id=")[1]
        assert (tmp_path / "align" / f"{slug}.json").exists() and (tmp_path / "review" / f"{slug}.json").exists()
    assert nis2["matrix_href"] and (tmp_path / "matrix" / "32022L2555.json").exists()
    # language codes for lang attributes (also filled in for older static copies)
    at = json.loads((tmp_path / "align" / "32022L2555_at.json").read_text(encoding="utf-8"))
    assert at["national_language"] == "de" and at["eu"]["language"] == "de"
    rv = json.loads((tmp_path / "review" / "32022L2555_cz.json").read_text(encoding="utf-8"))
    assert rv["eu_language"] == "cs" and rv["national_language"] == "cs"


def test_offline_build_without_provision_data_links_nothing(tmp_path):
    """--no-alignment / --no-dataset: index.json and <CELEX>.json carry no links to missing files."""
    assert build_site.main(["--offline", "--no-alignment", "--no-dataset", "--out", str(tmp_path)]) == 0
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    rec = json.loads((tmp_path / "32022L2555.json").read_text(encoding="utf-8"))
    nis2 = next(d for d in index["directives"] if d["celex"] == "32022L2555")
    assert index["dataset"] is False
    for d in (nis2, rec):
        assert d["alignments"] == [] and d["matrix_href"] is None and d["dataset"] is False


def test_link_built_pages_drops_missing_country_and_review(tmp_path):
    index, records = build_site.build_site_data(CellarClient(FakeFetch()), ENTRIES[:1], now=NOW)
    for sub in ("align", "review", "matrix"):
        (tmp_path / sub).mkdir()
    (tmp_path / "align" / "32022L2555_cz.json").write_text("{}")
    (tmp_path / "align" / "32022L2555_sk.json").write_text("{}")
    (tmp_path / "review" / "32022L2555_cz.json").write_text("{}")
    build_site.link_built_pages(tmp_path, index, records)
    for d in (index["directives"][0], records[0]):
        assert [a["href"] for a in d["alignments"]] == ["alignment.html?id=32022L2555_cz", "alignment.html?id=32022L2555_sk"]
        assert [a["review_href"] for a in d["alignments"]] == ["review.html?id=32022L2555_cz", None]
        assert d["matrix_href"] is None


# --- site/assets/app.js (run in node when available) --------------------------------------------

APP_JS = ROOT / "site" / "assets" / "app.js"
NODE = shutil.which("node")


def run_app_js(expr: str):
    """Evaluate ``expr`` (with ``O`` = window.OpenAcquis) in node and return its JSON value."""
    script = (
        "const vm = require('vm'); const fs = require('fs');"
        "const ctx = { window: {}, URLSearchParams, URL, console }; vm.createContext(ctx);"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(APP_JS))}, 'utf8'), ctx);"
        f"const O = ctx.window.OpenAcquis; process.stdout.write(JSON.stringify({expr}));"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


REC = {"schema": 1, "suggestion_id": "extra_rule:cz:264/2025:par5.odst1", "directive": "32022L2555", "country": "cz",
       "kind": "extra_rule", "eu_id": None, "national_id": "cz:264/2025:par5.odst1", "machine": {},
       "verdict": "correct", "corrected_ids": [], "reviewer": "tester", "date": "2026-10-12", "note": ""}


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("kind, ids, ok", [
    ("extra_rule", ["eu:32022L2555:cs:art23.par4.pt_a"], True),
    ("extra_rule", [f"eu:32022L2555:cs:art{i}" for i in range(1, 22)], False),  # > MAX_CORRECTED
    ("extra_rule", ["eu:32019L0882:cs:art1"], False),  # another directive
    ("extra_rule", ["eu:32022L2555:cs:art1."], False),  # trailing dot
    ("extra_rule", ["eu:32022L2555:cs:.art1"], False),
    ("link", ["cz:264/2025:par12.odst1"], True),
    ("link", ["cz:264/2025:par12..odst1"], False),
    ("link", ["sk:69/2018:par1"], False),  # another country
    ("link", ["cz:![x](http://e/a):par1"], False),  # act part outside A-Za-z0-9_./-
    ("link", ["cz:264/2025 x:par1"], False),
])
def test_js_check_record_matches_python_validator(kind, ids, ok):
    from openacquis.review import ReviewError, suggestion_id, validate_record

    rec = dict(REC, corrected_ids=ids, kind=kind)
    if kind == "link":
        rec.update(eu_id="eu:32022L2555:cs:art1.par1", national_id="cz:264/2025:par1")
    rec["suggestion_id"] = suggestion_id(kind, rec["eu_id"], rec["national_id"])
    errs = run_app_js(f"O._checkRecord({json.dumps(rec)})")
    try:
        validate_record(rec)
        py_ok = True
    except ReviewError:
        py_ok = False
    assert (errs == []) is ok is py_ok, errs


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_issue_link_refuses_urls_too_long_for_github():
    rec = dict(REC, verdict="confirm", note="")
    assert run_app_js(f"O._issueLink({json.dumps(rec)})").startswith("https://github.com/")
    for note in ('"' * 2000, "ř" * 2000):  # valid notes (<= 2000 chars) that encode to > 12k characters
        assert run_app_js(f"[O._checkRecord({json.dumps(dict(rec, note=note))}), O._issueLink({json.dumps(dict(rec, note=note))})]") == [[], None]


def test_review_page_accessibility_and_styles():
    html = (ROOT / "site" / "review.html").read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    # the long suggestion card is not a live region; a short status is announced instead
    assert '<div id="r-card"></div>' in html and 'id="r-status"' in html
    # machine alternatives and human review status do not use the official-table style
    assert '"official", nat, sg.alternatives' not in js and "pill official\", text: \" already" not in js
    assert '"machine alt", nat, sg.alternatives' in js and "pill reviewed" in js


def test_curated_list_is_valid():
    from openacquis.cellar import validate_directive_celex

    celexes = [e.celex for e in build_site.DIRECTIVES]
    assert len(set(celexes)) == len(celexes) >= 8
    for c in celexes:
        assert validate_directive_celex(c) == c


def test_offline_build_v05_states_feed_and_pages(tmp_path):
    """Every registry pair is built from site/static-data; countries with nothing to align, baseline acts,
    official tables (CZ and SK) and the change feed show up where the pages read them."""
    out = tmp_path / "data"
    assert build_site.main(["--offline", "--out", str(out)]) == 0
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    mw = next(d for d in index["directives"] if d["celex"] == "32022L2041")
    assert [n["country_code"] for n in mw["not_aligned"]] == ["at"] and mw["matrix_href"]
    m = json.loads((out / "matrix" / "32022L2041.json").read_text(encoding="utf-8"))
    assert [c["code"] for c in m["countries"]] == ["cz", "sk", "de"] and m["not_aligned"][0]["code"] == "at"
    pay = json.loads((out / "align" / "32023L0970_de.json").read_text(encoding="utf-8"))
    assert pay["registry"]["status"] == "baseline" and pay["has_official"] is False
    cer = json.loads((out / "align" / "32022L2557_sk.json").read_text(encoding="utf-8"))
    assert cer["has_official"] and cer["official_source"]["url"].startswith("https://www.nrsr.sk/")
    csrd = next(d for d in index["directives"] if d["celex"] == "32022L2464")
    assert csrd["provision_level"] is False and csrd["alignments"] == []
    assert (tmp_path / "feed.xml").read_text(encoding="utf-8").startswith("<?xml")
    for page in ("index.html", "directive.html", "alignment.html", "matrix.html", "review.html", "data.html"):
        html = (ROOT / "site" / page).read_text(encoding="utf-8")
        assert 'type="application/atom+xml"' in html and 'href="data.html"' in html


def test_cli_passes_through_to_alerts_and_judge_batch(tmp_path, capsys):
    from openacquis.cli import main as cli

    (tmp_path / "data" / "alerts").mkdir(parents=True)
    assert cli(["alerts", "--root", str(tmp_path), "feed", "--out", str(tmp_path / "feed.xml")]) == 0
    assert (tmp_path / "feed.xml").exists()
    with pytest.raises(SystemExit):
        cli(["judge-batch", "--help"])
    assert "Message Batches" in capsys.readouterr().out


# --- baseline pairs and public notes (v0.5 review findings) -------------------------------------

def test_public_note_drops_developer_clauses():
    pn = build_site.public_note
    assert pn("via the GesLeiPoG amendment; CELLAR's measures map to 'EU' / 'GesLeiPoG' / BVergG") == "via the GesLeiPoG amendment"
    assert pn("both AT measures map as no_reference; AWG 2002 by hand") == "AWG 2002 by hand"
    assert pn("CELLAR maps the DE measure to the 2015 FüPoG (bgrembg_2015); the second measure is not_found") == ""
    assert pn("all 13 DE measures map as not_found", override=True) == build_site.HAND_SET_NOTE
    keep = "AT: no federal measure notified (28 of 30 measures are Land laws); GlBG aligned as the existing federal equal-treatment law"
    assert pn(keep) == keep and pn("") == ""


def test_offline_build_baseline_pairs_and_public_notes(tmp_path):
    out = tmp_path / "data"
    assert build_site.main(["--offline", "--out", str(out)]) == 0
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    internal = build_site._INTERNAL_NOTE
    for d in index["directives"]:
        for a in d["alignments"]:
            assert not internal.search(a["note"]), (d["celex"], a["note"])
        for n in d["not_aligned"]:
            assert not internal.search(n["note"]), (d["celex"], n["note"])
    for path in (out / "align").glob("*.json"):
        assert not internal.search(json.loads(path.read_text(encoding="utf-8"))["registry"].get("note", "")), path.name
    pay = next(d for d in index["directives"] if d["celex"] == "32023L0970")
    assert {a["country_code"]: a["status"] for a in pay["alignments"]}["at"] == "baseline"
    # the review page of a baseline pair knows it (notice and reworded question)
    rv = json.loads((out / "review" / "32023L0970_at.json").read_text(encoding="utf-8"))
    assert rv["registry"]["status"] == "baseline"
    assert json.loads((out / "review" / "32022L2555_cz.json").read_text(encoding="utf-8"))["registry"]["status"] == "ok"


def test_site_never_says_not_notified_from_registry_status():
    js = APP_JS.read_text(encoding="utf-8")
    index_html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    for wrong in ("No measure notified for this directive", "no measure notified for ", "Notified transposition",
                  "not notified: compared", "has notified no measure for this directive that"):
        assert wrong not in js, wrong
    assert "no measure notified" not in index_html and '<span aria-hidden="true">✓</span>' not in index_html
    assert 'className: "pill gap", style: "margin-left:0"' not in js and "pill baseline" in js
    assert "safeHref(d.eurlex_nim_url)" in js and '$("nim-link").href = d.eurlex_nim_url;' not in js.replace(
        'if (d.eurlex_nim_url && safeHref(d.eurlex_nim_url)) $("nim-link").href = d.eurlex_nim_url;', "")
    assert '"aria-label": code + " " + r.name + ": no act to align"' not in js
    assert "KIND_TEXT_BASELINE" in js and "pruneApiLinks" in js
    css = (ROOT / "site" / "assets" / "style.css").read_text(encoding="utf-8")
    assert ".pill.baseline" in css and "table.prov" in css


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_baseline_text_uses_the_measure_count():
    at, de, unknown = run_app_js('[O._baselineText("Austria", "GlBG", 30), O._baselineText("Germany", "EntgTranspG", 0),'
                                 ' O._baselineText("Austria", "GlBG", null)]')
    assert "Austria notified 30 measures" in at and "notified no measure" not in at
    assert "Germany notified no measure" in de
    assert unknown == "GlBG was not notified for this directive; it is compared as existing law."


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_review_articles_in_numeric_order():
    keys = ["art34", "art8", "anx_ii", "art27", "art10a", "art10", "anx_i", "art2"]
    assert run_app_js(f"O._sortArts({json.dumps(keys)})") == [
        "art2", "art8", "art10", "art10a", "art27", "art34", "anx_i", "anx_ii"]
    assert run_app_js('[O._artLabel("anx_iii"), O._artLabel("art5")]') == ["Annex III", "Art. 5"]


# --- country comparison (countries.json) --------------------------------------------------------

def _fake_page(celex, cc, name, labels, *, status="ok", verified=True):
    """An alignment page with one article and one paragraph row per entry of ``labels`` (a list of label
    lists, one per candidate in rank order)."""
    rows, nat = [], {}
    for i, labs in enumerate(labels):
        machine = []
        for k, lab in enumerate(labs):
            nid = f"{cc}:act:par{i}.{k}"
            nat[nid] = {"label": f"§ {i}.{k}", "act": "act", "url": f"https://example.org/{nid}", "text": "t"}
            machine.append({"id": nid, "score": 0.5, "above_threshold": True, "judge": {
                "label": lab, "confidence": 0.8 - 0.05 * k, "rationale": "why", "evidence_eu": "eu quote",
                "evidence_national": "nat quote", "evidence_verified": verified}})
        rows.append({"id": f"eu:{celex}:en:art1.par{i}", "label": f"Art. 1({i})", "url": "https://eur-lex.example",
                     "machine": machine})
    return {"celex": celex, "id": f"{celex}_{cc}", "country_code": cc, "country_name": name,
            "national_language": "de", "eu": {"language": "de"}, "national": [{"act_id": "act", "label": "ACT"}],
            "national_texts": nat, "articles": [{"id": f"eu:{celex}:en:art1", "key": "art1", "rows": rows}],
            "registry": {"status": status, "note": ""}, "judge": {"models": ["claude-opus-5-5 (x)"]}}


def test_countries_data_coverage_labels_and_states():
    entries = [build_site.Entry("32000L0001", "Alpha", "T", 1), build_site.Entry("32000L0002", "Beta", "T", 2)]
    pages = [
        _fake_page("32000L0001", "de", "Germany", [["unrelated", "partial"], ["unrelated"], ["stricter", "same"], ["option"]]),
        _fake_page("32000L0001", "at", "Austria", [["same"], ["different_value"]]),
        _fake_page("32000L0002", "de", "Germany", [["partial"], ["unrelated"]], status="baseline"),
    ]
    states = [build_site.CountryState("32000L0002", "AUT", "at", "Austria", "no act")]
    d = build_site.countries_data(pages, directives=entries, states=states, now=NOW)
    assert [c["name"] for c in d["countries"]] == ["Austria", "Germany"]  # alphabetical, not by score
    de = next(c for c in d["summary"] if c["code"] == "de")
    # 4 paragraphs, 3 with a related candidate; the baseline pair is not in the totals
    assert (de["rows"], de["covered"], de["gaps"], de["directives"], de["baseline_pairs"]) == (4, 3, 1, 1, 1)
    assert (de["stricter"], de["different_value"], de["option"]) == (1, 0, 1)
    cell = d["matrix"][0]["cells"]["de"]
    assert cell["coverage"] == 0.75 and sum(cell["best"].values()) == cell["covered"]
    assert cell["best"] == {"same": 0, "partial": 1, "stricter": 1, "different_value": 0, "option": 1}
    assert cell["href"] == "alignment.html?id=32000L0001_de"
    row2 = d["matrix"][1]["cells"]
    assert row2["de"]["state"] == "baseline" and row2["at"] == {"state": "none", "note": "no act"}
    assert d["specific"]["de"]["total"] == 1 and d["specific"]["de"]["items"][0]["label"] == "stricter"
    assert d["specific"]["at"]["items"][0]["national_act"] == "ACT"


def test_countries_data_unverified_quotes_are_not_shown_as_specific():
    page = _fake_page("32000L0001", "de", "Germany", [["stricter"], ["option"]], verified=False)
    d = build_site.countries_data([page], directives=[build_site.Entry("32000L0001", "Alpha", "T", 1)], now=NOW)
    de = d["summary"][0]
    assert de["covered"] == 2 and de["stricter"] == 0 and de["option"] == 0 and de["unverified_excluded"] == 2
    assert d["specific"]["de"]["items"] == []


def test_offline_build_writes_countries_json(tmp_path):
    out = tmp_path / "data"
    assert build_site.main(["--offline", "--out", str(out)]) == 0
    d = json.loads((out / "countries.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in d["countries"]] == sorted(c["name"] for c in d["countries"])
    assert len(d["matrix"]) == 12 and d["status"] == "unreviewed"
    for s in d["summary"]:
        assert 0 <= s["covered"] <= s["rows"] and s["gaps"] == s["rows"] - s["covered"]


def _page_with(celex, cc, name, covered, total, model):
    """A page with ``total`` paragraphs of which ``covered`` have a related candidate (>= 20 rows by default use)."""
    labels = [["same"]] * covered + [["unrelated"]] * (total - covered)
    p = _fake_page(celex, cc, name, labels)
    p["judge"] = {"models": [model]}
    return p


def test_countries_relative_needs_5_points_and_same_judge_model():
    entries = [build_site.Entry("32000L0001", "Alpha", "T", 1), build_site.Entry("32000L0002", "Beta", "T", 2),
               build_site.Entry("32000L0003", "Gamma", "T", 3), build_site.Entry("32000L0004", "Delta", "T", 4)]
    O, S = "claude-opus-5-5 (x)", "claude-sonnet-5-5 (x)"
    pages = []
    # Alpha (all Opus): de 20/20, at 18/20 and cz 10/20 -> de is 25 points above the mean of at and cz (70 %)
    for cc, name, cov in [("de", "Germany", 20), ("at", "Austria", 18), ("cz", "Czechia", 10)]:
        pages.append(_page_with("32000L0001", cc, name, cov, 20, O))
    # Beta: de 20/20 vs at 19/20 and cz 20/20: 2.5 points, below the 5-point limit
    for cc, name, cov in [("de", "Germany", 20), ("at", "Austria", 19), ("cz", "Czechia", 20)]:
        pages.append(_page_with("32000L0002", cc, name, cov, 20, O))
    # Gamma: de (Opus) 20/20 vs at 0/20 and cz 0/20 judged by Sonnet: not comparable, skipped
    pages.append(_page_with("32000L0003", "de", "Germany", 20, 20, O))
    for cc, name in [("at", "Austria"), ("cz", "Czechia")]:
        pages.append(_page_with("32000L0003", cc, name, 0, 20, S))
    # Delta: de and cz Opus 20/20 and 0/20, at Sonnet 20/20: only one comparable other -> skipped
    pages.append(_page_with("32000L0004", "de", "Germany", 20, 20, O))
    pages.append(_page_with("32000L0004", "cz", "Czechia", 0, 20, O))
    pages.append(_page_with("32000L0004", "at", "Austria", 20, 20, S))
    d = build_site.countries_data(pages, directives=entries, now=NOW)
    rel = d["relative"]
    assert [x["celex"] for x in rel["de"]["highest"]] == ["32000L0001"] and rel["de"]["lowest"] == []
    assert rel["de"]["highest"][0]["others"] == 2 and rel["de"]["highest"][0]["diff"] == 0.3
    assert rel["de"]["compared"] == 2  # Alpha and Beta only
    assert [x["celex"] for x in rel["cz"]["lowest"]] == ["32000L0001"]
    assert [x["celex"] for x in rel["at"]["highest"]] == ["32000L0001"]
    assert [x["celex"] for x in rel["at"]["lowest"]] == ["32000L0002"]  # exactly 5 points below counts
    assert d["relative_min_diff"] == 0.05


def test_countries_data_lists_only_directives_with_pages():
    entries = [build_site.Entry("32000L0001", "Alpha", "T", 1), build_site.Entry("32000L0002", "Beta", "T", 2)]
    d = build_site.countries_data([_fake_page("32000L0001", "de", "Germany", [["same"]])], directives=entries,
                                  states=[build_site.CountryState("32000L0002", "AUT", "at", "Austria", "n")], now=NOW)
    assert [r["celex"] for r in d["matrix"]] == ["32000L0001"]  # Beta has only a "no act" state and no page
