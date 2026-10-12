"""scripts/judge_session.py (judge via Claude Code subagents), judgment_agreement, and the judge on the website."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from openacquis.evalset import GoldLink
from openacquis.evaluate import format_judgment_agreement, judgment_agreement
from openacquis.judge import build_user_message, system_prompt_for
from openacquis.judge_batch import load_workload

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


js = _load("judge_session")
build_site = sys.modules.get("build_site") or _load("build_site")

CELEX = "32022L2555"
EU1 = ("eu:32022L2555:cs:art23.par4", "4. bez zbytečného odkladu a v každém případě do 24 hodin poté, co se o "
       "významném incidentu dozvěděl, včasné varování;")
EU2 = ("eu:32022L2555:cs:art2.par5", "5. Členské státy mohou stanovit, že se tato směrnice vztahuje na subjekty "
       "veřejné správy na místní úrovni.")
NAT = {
    "cz:264/2025:par15.odst1": "(1) Regulovaná služba podá Úřadu nejpozději do 12 hodin poté, co se o významném "
                               "incidentu dozvěděla, včasné varování.",
    "cz:264/2025:par50": "Tento zákon nabývá účinnosti prvním dnem kalendářního měsíce.",
    "cz:264/2025:par9": "(9) Dlouhý text…",
    "cz:264/2025:par2.odst3": "(3) Tento zákon se vztahuje rovněž na subjekty veřejné správy na místní úrovni.",
}
#: n -> answer, for the four pairs of the fixture (k = 3)
ANSWERS = {
    0: {"label": "stricter", "confidence": 0.9, "rationale": "24 hours vs 12 hours.",
        "evidence_eu": "do 24 hodin poté", "evidence_national": "nejpozději do 12 hodin poté"},
    1: {"label": "unrelated", "confidence": 0.95, "rationale": "Entry into force.", "evidence_eu": "",
        "evidence_national": ""},
    2: {"label": "partial", "confidence": 0.6, "rationale": "<img src=x onerror=alert(1)>",
        "evidence_eu": "včasné varování", "evidence_national": "tento text tam není"},
    3: {"label": "option", "confidence": 80, "rationale": "Option used.", "evidence_eu": "Členské státy mohou stanovit",
        "evidence_national": "vztahuje rovněž na subjekty veřejné správy na místní úrovni"},
}


def page(tmp_path):
    data = {
        "celex": CELEX, "country_code": "cz", "national_language": "cs", "eu": {"language": "cs"},
        "articles": [
            {"id": "eu:32022L2555:cs:art23", "label": "Článek 23", "heading": "Ohlašovací povinnosti",
             "rows": [{"id": EU1[0], "label": "Čl. 23 odst. 4", "text": EU1[1],
                       "machine": [{"id": "cz:264/2025:par15.odst1", "score": 0.61, "above_threshold": True},
                                   {"id": "cz:264/2025:par50", "score": 0.2, "above_threshold": True},
                                   {"id": "cz:264/2025:par9", "score": 0.1, "above_threshold": False}],
                       "official": [], "units": 1, "linked_units": 1, "gaps": []}]},
            {"id": "eu:32022L2555:cs:art2", "label": "Článek 2", "heading": "Oblast působnosti",
             "rows": [{"id": EU2[0], "label": "Čl. 2 odst. 5", "text": EU2[1],
                       "machine": [{"id": "cz:264/2025:par2.odst3", "score": 0.7, "above_threshold": True}],
                       "official": [], "units": 1, "linked_units": 1, "gaps": []}]},
        ],
        "national_texts": {k: {"label": k, "text": v, "section": k.split(".")[0]} for k, v in NAT.items()},
    }
    p = tmp_path / f"{CELEX}_cz.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def write_results(outdir, answers, ids=True, per_chunk=2):
    wl_pairs = json.loads((outdir / "chunk_000.json").read_text(encoding="utf-8"))
    for c in sorted(outdir.glob("chunk_*.json")):
        wl_pairs = json.loads(c.read_text(encoding="utf-8"))
        out = []
        for p in wl_pairs:
            if p["n"] in answers:
                a = {"n": p["n"], **answers[p["n"]]}
                if ids:
                    a.update(eu_id=p["eu_id"], national_id=p["national_id"])
                out.append(a)
        (outdir / c.name.replace("chunk_", "result_")).write_text(
            json.dumps({"chunk": int(c.stem[-3:]), "judgments": out}, ensure_ascii=False), encoding="utf-8")


def test_prep_exports_the_api_prompt_and_messages(tmp_path):
    src = page(tmp_path)
    m = js.prep(src, "cz", tmp_path / "w", size=3)
    assert (m["pairs"], m["chunks"], m["clipped"]) == (4, 2, 1)
    w = tmp_path / "w"
    assert (w / "system.txt").read_text(encoding="utf-8") == system_prompt_for("cz")
    assert "result_NNN.json" in (w / "instructions.txt").read_text(encoding="utf-8")
    assert json.loads((w / "schema.json").read_text())["properties"]["label"]["enum"]
    wl = load_workload(src, k=3, country="cz")
    chunks = [json.loads((w / f"chunk_00{i}.json").read_text(encoding="utf-8")) for i in (0, 1)]
    assert [len(c) for c in chunks] == [3, 1]
    flat = chunks[0] + chunks[1]
    assert [x["n"] for x in flat] == [0, 1, 2, 3]
    assert all(x["message"] == build_user_message(p.eu, p.national) for x, p in zip(flat, wl.pairs))
    # results present: a new export would mix old answers with new chunks
    (w / "result_000.json").write_text("{}")
    with pytest.raises(js.SessionError):
        js.prep(src, "cz", w)
    js.prep(src, "cz", w, force=True)


def test_collect_verifies_evidence_and_writes_judge_batch_lines(tmp_path):
    src = page(tmp_path)
    w = tmp_path / "w"
    js.prep(src, "cz", w, size=2)
    write_results(w, ANSWERS)
    out = tmp_path / "j.jsonl"
    r = js.collect(src, "cz", w, out=out)
    assert (r["judged"], r["missing"], r["problems"], r["files"]) == (4, [], [], 2)
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [x["label"] for x in rows] == ["stricter", "unrelated", "partial", "option"]
    assert rows[0]["evidence_verified"] and rows[0]["evidence_national_span"]
    assert rows[0]["model"] == js.DEFAULT_MODEL and rows[0]["prompt_version"].startswith("judge-cs-")
    assert rows[0]["celex"] == CELEX and rows[0]["candidate_rank"] == 1 and rows[0]["status"] == "unreviewed"
    assert not rows[2]["evidence_verified"] and rows[2]["confidence"] == 0.3  # national quote not found: x 0.5
    assert any("clipped" in n for n in rows[2]["notes"])
    assert rows[3]["confidence"] == 0.8 and any("percentage" in n for n in rows[3]["notes"])
    assert r["related"] == 3 and r["related_verified"] == 2


def test_collect_reports_missing_bad_and_mismatched_answers(tmp_path, capsys):
    src = page(tmp_path)
    w = tmp_path / "w"
    js.prep(src, "cz", w, size=4)
    bad = {0: ANSWERS[0], 1: {**ANSWERS[1], "label": "maybe"}, 3: ANSWERS[3]}
    write_results(w, bad)
    data = json.loads((w / "result_000.json").read_text(encoding="utf-8"))
    data["judgments"].append({**ANSWERS[1], "n": 99})
    data["judgments"].append({**ANSWERS[1], "n": 2, "eu_id": EU2[0], "national_id": "cz:264/2025:par9"})
    (w / "result_000.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = js.collect(src, "cz", w, out=tmp_path / "j.jsonl")
    assert r["judged"] == 2 and r["missing"] == [1, 2] and len(r["problems"]) == 3
    assert js.main(["collect", str(src), "cz", str(w), "--out", str(tmp_path / "j.jsonl")]) == js.EXIT_MISSING
    assert "missing answers for 2 pairs" in capsys.readouterr().err


def test_collect_reads_workflow_output_and_checks_the_manifest(tmp_path):
    src = page(tmp_path)
    w = tmp_path / "w"
    js.prep(src, "cz", w, size=2)
    chunks = [{"judgments": [{"n": n, **a} for n, a in ANSWERS.items() if n < 2]},
              {"judgments": [{"n": n, **a} for n, a in ANSWERS.items() if n >= 2]}]
    wf = w / "workflow.json"
    wf.write_text(json.dumps({"result": json.dumps(chunks, ensure_ascii=False)}, ensure_ascii=False), encoding="utf-8")
    assert js.collect(src, "cz", wf, out=tmp_path / "j.jsonl")["judged"] == 4
    # the alignment changed since prep: refuse to pair old answers with new candidates
    data = json.loads(src.read_text(encoding="utf-8"))
    data["articles"][0]["rows"][0]["machine"].reverse()
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(js.SessionError, match="other pairs"):
        js.collect(src, "cz", wf, out=tmp_path / "j.jsonl")


GOLD = [GoldLink(eu_id="eu:32022L2555:cs:art23.par4.pt_a", national_id="cz:264/2025:par15", status="listed"),
        GoldLink(eu_id="eu:32022L2555:cs:art2.par5", national_id="cz:264/2025:par2.odst4", status="listed"),
        GoldLink(eu_id="eu:32022L2555:cs:art99", national_id="cz:264/2025:par1", status="listed")]


def r3(d):
    """Metrics are stored unrounded; compare at three decimals as reported."""
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in d.items()}


def records(tmp_path):
    src = page(tmp_path)
    w = tmp_path / "w"
    js.prep(src, "cz", w)
    write_results(w, ANSWERS)
    out = tmp_path / "j.jsonl"
    js.collect(src, "cz", w, out=out)
    return out, [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]


def test_judgment_agreement_on_a_tiny_gold_set(tmp_path):
    _, recs = records(tmp_path)
    r = judgment_agreement(recs, GOLD, examples=5)
    assert (r["pairs"], r["pairs_in_scope"], r["eu_units_in_scope"]) == (4, 4, 2)
    # top-1: par15.odst1 (on the path of the table's par15) and par2.odst3 (same § as par2.odst4 only)
    assert r3(r["machine_top1"]) == {"pairs": 2, "precision": 0.5, "section_precision": 1.0}
    assert r3(r["judge_related"]) == {"pairs": 3, "precision": 0.333, "section_precision": 0.667}
    assert r3(r["judge_same_or_partial"]) == {"pairs": 1, "precision": 0.0, "section_precision": 0.0}
    assert (r["table_pairs"], r["table_pairs_kept"], r["other_pairs"], r["other_pairs_rejected"]) == (1, 1, 3, 1)
    assert r["related_not_in_table"] == 2 and len(r["related_not_in_table_examples"]) == 2
    assert r["labels"] == {"option": 1, "partial": 1, "stricter": 1, "unrelated": 1}
    assert (r["related"], r["related_evidence_verified"]) == (3, 2)
    assert "judge: related (any rank)" in format_judgment_agreement(r)
    # an EU unit the table does not cover is out of scope
    assert judgment_agreement(recs, GOLD[2:])["pairs_in_scope"] == 0


def test_evaluate_cli_with_a_gold_file(tmp_path, capsys):
    out, _ = records(tmp_path)
    gold = tmp_path / "gold.jsonl"
    gold.write_text("".join(json.dumps({"eu_id": g.eu_id, "national_id": g.national_id, "status": "listed"}) + "\n"
                            for g in GOLD), encoding="utf-8")
    assert js.main(["evaluate", str(out), "--gold", str(gold), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert round(rep["judge_related"]["precision"], 3) == 0.333 and rep["gold"] == str(gold)


def test_evaluate_reproduces_the_committed_nis2_cz_run():
    """The numbers in docs/evaluation.md ("LLM judge (NIS2 × CZ)") from the committed JSONL and table."""
    path = ROOT / "data" / "judgments" / "32022L2555_cz.jsonl"
    if not path.is_file():
        pytest.skip("no committed judgments")
    r = js.evaluate(path, "nis2-cz")
    assert (r["pairs"], r["pairs_in_scope"]) == (606, 414)
    assert r3(r["machine_top1"]) == {"pairs": 138, "precision": 0.449, "section_precision": 0.558}
    assert r3(r["judge_related"]) == {"pairs": 94, "precision": 0.755, "section_precision": 0.883}
    assert r3(r["judge_same_or_partial"]) == {"pairs": 71, "precision": 0.831, "section_precision": 0.915}
    assert (r["table_pairs"], r["table_pairs_kept"], r["other_pairs"], r["other_pairs_rejected"]) == (106, 71, 308, 285)
    assert r["related_not_in_table"] == 23 and (r["related"], r["related_evidence_verified"]) == (96, 96)


def test_site_attaches_judgments_and_counts_them_in_the_matrix(tmp_path):
    jpath, _ = records(tmp_path)
    data = json.loads(page(tmp_path).read_text(encoding="utf-8"))
    data.update(id=f"{CELEX}_cz", short="NIS2", country_name="Czechia", method="m", built_at="x", mode="static")
    build_site._add_judgments(data, jpath)
    ms = data["articles"][0]["rows"][0]["machine"]
    assert ms[0]["judge"]["label"] == "stricter" and ms[0]["judge"]["evidence_verified"] is True
    assert ms[2]["judge"]["rationale"] == "<img src=x onerror=alert(1)>"  # kept as text; the page uses textContent
    assert set(ms[0]["judge"]) == set(build_site.JUDGE_FIELDS)
    assert data["judge"]["status"] == "unreviewed" and (data["judge"]["shown"], data["judge"]["related"]) == (4, 3)
    m = build_site.matrix_data(CELEX, [data])
    cells = {a["key"]: a["cells"]["cz"]["judge"] for a in m["articles"]}
    assert cells["art23"] == {"related": 2, "stricter": 1, "different_value": 0}
    # no file, or no matching pair: the page is unchanged
    plain = json.loads(page(tmp_path).read_text(encoding="utf-8"))
    build_site._add_judgments(plain, tmp_path / "none.jsonl")
    assert "judge" not in plain and "judge" not in build_site.matrix_data(CELEX, [{**data, "judge": None}])["articles"][0]["cells"]["cz"]


def test_site_build_without_judgments_dir_shows_none(tmp_path):
    out = tmp_path / "data"
    entries = [a for a in build_site.ALIGNMENTS if a.slug == f"{CELEX}_cz"]
    assert build_site.write_alignments(out, entries, offline=True, judgments_dir=None) == []
    assert "judge" not in json.loads((out / "align" / f"{CELEX}_cz.json").read_text(encoding="utf-8"))
    jd = tmp_path / "judgments"
    jd.mkdir()
    jd.joinpath(f"{CELEX}_cz.jsonl").write_text(json.dumps(
        {"eu_id": "eu:32022L2555:cs:art1.par1", "national_id": "cz:264/2025:par13.odst1", "label": "partial",
         "confidence": 0.5, "rationale": "r", "evidence_eu": "", "evidence_national": "", "evidence_verified": False,
         "notes": [], "model": "m", "prompt_version": "p"}) + "\n", encoding="utf-8")
    build_site.write_alignments(out, entries, offline=True, judgments_dir=jd)
    page_data = json.loads((out / "align" / f"{CELEX}_cz.json").read_text(encoding="utf-8"))
    assert page_data["judge"]["shown"] == 1 and page_data["judge"]["models"] == ["m"]


def test_api_leaves_out_the_website_only_judge_fields(tmp_path):
    api = sys.modules.get("build_api") or _load("build_api")
    jpath, _ = records(tmp_path)
    data = json.loads(page(tmp_path).read_text(encoding="utf-8"))
    data.update(id=f"{CELEX}_cz", short="NIS2", country="CZE", country_name="Czechia", method="m", built_at="x", mode="static")
    build_site._add_judgments(data, jpath)
    out = api._strip_alignment(data, {"eu_provisions": "p.json"})
    assert "judge" not in out and all("judge" not in m for a in out["articles"] for r in a["rows"] for m in r["machine"])
    m = build_site.matrix_data(CELEX, [data])
    assert all("judge" not in c for a in api._strip_judge_cells(m["articles"]) for c in a["cells"].values())
