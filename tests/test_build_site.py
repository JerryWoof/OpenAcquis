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
    for d in index["directives"]:
        assert d["error"] is None and d["total_measures"] > 0
        assert (tmp_path / f"{d['celex']}.json").exists()
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
