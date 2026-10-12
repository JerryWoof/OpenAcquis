"""v1.0 directive registry (data/directives.json, openacquis.directives) and scripts/build_alignments.py (offline)."""

import copy
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openacquis.adapters import MeasureMapping
from openacquis.align import DEFAULT_ACTS, DEFAULT_VERSIONS
from openacquis.directives import (
    COUNTRIES,
    REGISTRY_PATH,
    RegistryError,
    base_ref,
    load_registry,
    parse_registry,
    pick_main_act,
    resolve_main_acts,
    validate,
)

sys.path.insert(0, str(Path(__file__).parent))
import test_align  # noqa: E402
import test_at  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_alignments", ROOT / "scripts" / "build_alignments.py")
ba = importlib.util.module_from_spec(_spec)
sys.modules["build_alignments"] = ba
_spec.loader.exec_module(ba)

NOW = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)
EU, ACT = test_align.EU, test_align.ACT


def raw(**countries):
    return {"schema": 1, "directives": [{
        "celex": EU, "short": "Test", "topic": "Cybersecurity", "deadline": "2024-10-17",
        "deadlines": ["2024-10-17"], "countries": countries or {"cz": {"status": "ok", "acts": [ACT], "label": "1/2099 Sb."}},
    }]}


def mm(ref, title="", status="mapped"):
    return MeasureMapping(f"urn:{ref}", title, ref, status, ref if status == "mapped" else None)


# --- the bundled registry ------------------------------------------------------------------


def test_bundled_registry_is_valid():
    reg = load_registry()
    assert len(reg) == 12 and len(set(reg.celexes())) == 12
    assert reg.celexes()[0] == "32022L2555" and [d.rank for d in reg] == list(range(1, 13))
    assert all(set(d.countries) == set(COUNTRIES) for d in reg)
    assert validate(json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))) == []


def test_bundled_registry_agrees_with_nis2_defaults():
    nis2 = load_registry().get("32022l2555")
    for cc in COUNTRIES:
        assert nis2.country(cc).main == DEFAULT_ACTS[("32022L2555", cc)][0]
    assert nis2.country("cz").version == DEFAULT_VERSIONS[("32022L2555", "cz")]


def test_bundled_registry_records_missing_transpositions():
    reg = load_registry()
    pay = reg.get("32023L0970")
    assert pay.country("de").status == "baseline" and "not notified" in pay.country("de").note
    assert pay.country("at").status == "baseline" and "no federal measure" in pay.country("at").note
    assert reg.get("32022L2041").country("at").status == "none"
    assert reg.get("32019L1937").country("de").override  # CELLAR maps HinSchG as not_found
    with pytest.raises(KeyError, match="not in the directive registry"):
        reg.get("32099L0001")


# --- validation ----------------------------------------------------------------------------


@pytest.mark.parametrize("change, problem", [
    (lambda d: d["directives"][0].update(celex="32022R2554"), "not a directive CELEX"),
    (lambda d: d["directives"][0]["countries"].update(fr={"status": "ok", "acts": ["x"]}), "no adapter for country 'fr'"),
    (lambda d: d["directives"][0]["countries"]["cz"].update(status="maybe"), "status 'maybe'"),
    (lambda d: d["directives"][0]["countries"]["cz"].update(acts=[]), "needs at least one act"),
    (lambda d: d["directives"][0]["countries"]["cz"].update(status="none"), "must have no acts"),
    (lambda d: d["directives"][0]["countries"]["cz"].update(status="baseline"), "needs a note"),
    (lambda d: d["directives"][0]["countries"]["cz"].update(version="1.11.2025"), "must be YYYY-MM-DD"),
    (lambda d: d["directives"][0]["countries"]["cz"].update(max_sections=0), "positive integer"),
    (lambda d: d["directives"][0]["countries"]["cz"].update(acts=[ACT, ACT]), "listed twice"),
    (lambda d: d["directives"][0]["countries"]["cz"].update(public_note=3), "public_note must be a string"),
    (lambda d: d["directives"][0].update(deadline="soon"), "deadline 'soon'"),
    (lambda d: d["directives"][0].update(colour="red"), "unknown key 'colour'"),
    (lambda d: d["directives"].append(copy.deepcopy(d["directives"][0])), "listed twice"),
    (lambda d: d.update(schema=9), "schema must be 1"),
])
def test_validate_reports_problems(change, problem):
    d = raw()
    change(d)
    problems = validate(d)
    assert any(problem in p for p in problems), problems
    with pytest.raises(RegistryError, match="invalid directive registry"):
        parse_registry(d)


def test_load_registry_from_file(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(raw()), encoding="utf-8")
    reg = load_registry(p)
    assert reg.get(EU).country("cz").acts == (ACT,) and reg.get(EU).country("sk").status == "none"
    p.write_text("{", encoding="utf-8")
    with pytest.raises(RegistryError, match="not valid JSON"):
        load_registry(p)


def test_public_notes_replace_developer_notes_on_the_site():
    """Every registry note the site would otherwise cut to a fragment has a public wording."""
    bs = ba._load_build_site()
    reg = load_registry()
    for d in reg:
        for cc in COUNTRIES:
            c = d.country(cc)
            if c.note and bs.public_note(c.note, override=c.override) != c.note:
                assert c.public_note, (d.celex, cc, c.note)
                assert bs._note_of(c) == c.public_note and not bs._INTERNAL_NOTE.search(c.public_note)


# --- resolution ----------------------------------------------------------------------------


def test_override_wins_over_the_mapping():
    d = parse_registry(raw(de={"status": "ok", "acts": ["hinschg"], "override": True, "note": "not_found"})).get(EU)
    r = resolve_main_acts(d, "de", [mm("hembv")])
    assert r.acts == ("hinschg",) and r.source == "override"


def test_mapping_confirms_registry_acts():
    d = parse_registry(raw(
        sk={"status": "ok", "acts": ["69/2018", "60/2018"]},
        at={"status": "ok", "acts": ["20013065"], "aliases": ["NISG 2026"]},
    )).get(EU)
    assert resolve_main_acts(d, "sk").source == "registry"  # no mapping consulted
    r = resolve_main_acts(d, "sk", [mm("69/2018-366/2024"), mm("575/2001")])
    assert r.acts == ("69/2018",) and r.source == "cellar"
    r = resolve_main_acts(d, "at", [mm("NISV"), mm("NISG 2026")])
    assert r.acts == ("20013065",) and r.source == "cellar"


def test_mapping_without_registry_act_guesses_main_act():
    d = parse_registry(raw()).get(EU)
    ms = [mm("270/2025", "Zákon č. 270/2025 Sb., kterým se mění zákon č. 40/2009 Sb."),
          mm("408/2025", "Vyhláška č. 408/2025 Sb., o regulovaných službách"),
          mm("264/2025", "Zákon č. 264/2025 Sb., o kybernetické bezpečnosti"),
          mm(None, "Důvodová zpráva", status="not_a_law")]
    r = resolve_main_acts(d, "cz", ms)
    assert r.acts == ("264/2025",) and r.source == "cellar-heuristic" and "guessed 264/2025" in r.note
    r = resolve_main_acts(d, "cz", [mm(None, status="not_found")])
    assert r.acts == (ACT,) and r.source == "registry" and "no measure mapped" in r.note


def test_nothing_to_align():
    d = parse_registry(raw(at={"status": "none", "note": "AT: no statutory minimum wage"})).get(EU)
    r = resolve_main_acts(d, "at", [mm("GAG 2005")])
    assert r.acts == () and r.source == "none" and "minimum wage" in r.note
    assert resolve_main_acts(d, "de").source == "none"  # not in the registry at all


def test_pick_main_act_and_base_ref():
    assert pick_main_act("sk", [mm("575/2001", "Zákon č. 575/2001 Z. z., ktorým sa mení ..."),
                                mm("69/2018", "Zákon č. 69/2018 Z. z. o kybernetickej bezpečnosti")]) == "69/2018"
    assert pick_main_act("de", [mm("x", "Gesetz zur Änderung"), mm("bsig_2025", "BSIG")]) == "bsig_2025"
    assert pick_main_act("cz", []) is None
    assert base_ref("69/2018-366/2024") == "69/2018" and base_ref(" NISG  2026 ") == "nisg 2026"


# --- build_alignments ----------------------------------------------------------------------


class FakeClient(test_align.FakeESbirka):
    pass


def entry(**countries):
    return parse_registry(raw(**countries)).get(EU)


def test_build_job_writes_site_format(tmp_path):
    bs = ba._load_build_site()
    e = entry(cz={"status": "ok", "acts": [ACT, "2/2099"], "label": "1/2099 Sb."})
    rec = ba.build_job(e, "cz", out_dir=tmp_path, eurlex=test_align.FakeEurLex(),
                       client=FakeClient(broken={"2/2099"}), embedder="lexical", now=NOW)
    assert rec["status"] == "ok" and rec["source"] == "registry" and rec["failed_acts"] == ["2/2099"]
    data = json.loads((tmp_path / f"{EU}_cz.json").read_text(encoding="utf-8"))
    review = json.loads((tmp_path / f"{EU}_cz.review.json").read_text(encoding="utf-8"))
    # same keys as build_site.py's own shaping, plus the registry record and the format version
    from openacquis.align import align

    res = align(test_align.eu_doc(), [test_align.cz_doc()], now=NOW)
    ae = bs.AlignmentEntry(EU, "Test", "CZE", "cz", "Czechia", "1/2099 Sb.")
    assert set(data) == set(bs.alignment_page_data(ae, res, [], now=NOW)) | {"registry", "format"}
    assert set(review) == set(bs.review_page_data(ae, res, now=NOW)) | {"format"}
    assert data["mode"] == "static" and data["format"] == 1 and review["reviewed"] == {}
    assert data["national"][0]["label"] == "1/2099 Sb."
    assert [a["ok"] for a in data["registry"]["acts"]] == [True, False]
    assert rec["eu_units"] == data["summary"]["eu_units"]
    assert rec["linked_share"] == round(data["summary"]["eu_units_with_link"] / data["summary"]["eu_units"], 3)
    assert rec["bytes"]["alignment"] == (tmp_path / f"{EU}_cz.json").stat().st_size


def test_build_job_records_failures_and_none(tmp_path):
    rec = ba.build_job(entry(), "cz", out_dir=tmp_path, eurlex=test_align.FakeEurLex(),
                       client=FakeClient(broken={ACT}), embedder="lexical", now=NOW)
    assert rec["status"] == "failed" and "none of the acts could be fetched" in rec["error"]
    assert not list(tmp_path.glob("*.json"))
    rec = ba.build_job(entry(), "sk", out_dir=tmp_path, eurlex=None, client=None)
    assert rec["status"] == "none"


def test_compact_format_drops_texts(tmp_path):
    rec = ba.build_job(entry(), "cz", out_dir=tmp_path, eurlex=test_align.FakeEurLex(), client=FakeClient(),
                       embedder="lexical", fmt=ba.FORMAT_COMPACT, now=NOW)
    data = json.loads((tmp_path / f"{EU}_cz.json").read_text(encoding="utf-8"))
    review = json.loads((tmp_path / f"{EU}_cz.review.json").read_text(encoding="utf-8"))
    assert rec["format"] == data["format"] == review["format"] == 2
    texts = [*data["national_texts"].values(), *review["eu_texts"].values(), *review["national_texts"].values()]
    assert texts and all(t["text"] == "" and t["text_omitted"] and "url" in t for t in texts)
    assert all(r["text"] == "" for a in data["articles"] for r in a["rows"])


def test_fetch_at_capped_records_cap():
    client = test_at.nisg_fetch().client
    res = resolve_main_acts(entry(at={"status": "ok", "acts": ["20013065"], "label": "NISG 2026"}), "at")
    full, info = ba.fetch_at_capped(client, "20013065", None, None)
    assert not info["capped"]
    docs, report = ba.fetch_acts("at", client, res, at_max_sections=3)
    assert report[0]["capped"] and report[0]["sections_fetched"] == 3
    assert report[0]["sections_total"] == info["sections_total"] > 3
    assert 0 < len(docs[0]) < len(full)


def test_run_country_is_resumable(tmp_path, monkeypatch):
    reg_path = tmp_path / "r.json"
    reg_path.write_text(json.dumps(raw()), encoding="utf-8")
    calls = []

    class Client(FakeClient):
        def document(self, ref, version=None):
            calls.append(ref)
            return super().document(ref, version)

    monkeypatch.setattr(ba, "make_eurlex", lambda state_dir: test_align.FakeEurLex())
    monkeypatch.setattr(ba, "make_national_client", lambda cc, ttl_days=7.0: Client())
    out, state = tmp_path / "out", tmp_path / "state"
    argv = ["--registry", str(reg_path), "--out", str(out), "--state", str(state), "--countries", "cz", "sk",
            "--jobs", "1", "--embedder", "lexical"]
    assert ba.main(argv) == 0
    assert calls == [ACT] and (out / f"{EU}_cz.json").is_file() and (out / f"{EU}_cz.review.json").is_file()
    report = json.loads((state / "report.json").read_text(encoding="utf-8"))
    assert [j["status"] for j in report["jobs"]] == ["ok", "none"]
    assert ba.main(argv) == 0 and calls == [ACT]  # second run: done earlier, skipped
    assert ba.main([*argv, "--compact"]) == 0 and calls == [ACT, ACT]  # other format: redone
    assert ba.main([*argv, "--compact", "--force"]) == 0 and len(calls) == 3


def test_failures_are_logged(tmp_path):
    rec = {"id": f"{EU}_cz", "status": "failed", "error": "LookupError: gone"}
    ba.save_state(tmp_path, rec)
    assert "LookupError: gone" in (tmp_path / "failures.log").read_text(encoding="utf-8")
    assert not ba.done(tmp_path, tmp_path, f"{EU}_cz", 1)


def test_host_gate_keeps_a_gap(tmp_path):
    waits = []
    gate = ba.HostGate(tmp_path / "h.lock", gap=5.0, sleep=waits.append)
    with gate():
        pass
    with gate():
        pass
    assert len(waits) == 1 and 4.0 < waits[0] <= 5.0


def test_seed_directive_is_left_to_build_site(capsys, monkeypatch):
    monkeypatch.setattr(ba, "run_country", lambda *a, **k: pytest.fail("must not build"))
    assert ba.main(["--only", "32022L2555", "--jobs", "1"]) == 2
    assert "built by scripts/build_site.py" in capsys.readouterr().err


def test_fractional_list_levels_are_rounded_down():
    xml = '<liste><schlussteil ebene="0.5" typ="abs">x</schlussteil><aufzaehlung ebene="2"/></liste>'
    assert ba._fractional_levels(xml) == '<liste><schlussteil ebene="0" typ="abs">x</schlussteil><aufzaehlung ebene="2"/></liste>'
