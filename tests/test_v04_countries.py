"""v0.4 integration: adapter registry, `law` / `measures --map` / `align` for SK, AT, DE (offline fixtures)."""

import json
import sys
from pathlib import Path

import pytest

from openacquis.adapters import ADAPTERS, MeasureMapping, fetch_measure, get_adapter, map_measures
from openacquis.adapters.de import GiiClient
from openacquis.adapters.sk import SlovLexClient
from openacquis.align import (
    DEFAULT_ACTS,
    align,
    choose_embedder,
    default_gap_margin,
    default_threshold,
    load_documents,
    path_label,
)
from openacquis.cellar import CellarClient, NationalMeasure
from openacquis.cli import LAW_ADAPTERS, main
from openacquis.provisions import Document, Provision

sys.path.insert(0, str(Path(__file__).parent))
import test_at  # noqa: E402
import test_de  # noqa: E402
import test_sk  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def sk_client():
    return SlovLexClient(test_sk.FakeFetch(), today="2026-10-12")


def at_client():
    return test_at.nisg_fetch().client


def de_client():
    return GiiClient(test_de.FakeFetch())


# --- registry ---------------------------------------------------------------------------


def test_registry_has_four_countries():
    assert list(ADAPTERS) == ["cz", "sk", "at", "de"]
    assert LAW_ADAPTERS == {"CZE": "cz", "SVK": "sk", "AUT": "at", "DEU": "de"}
    assert [a.language for a in ADAPTERS.values()] == ["cs", "sk", "de", "de"]
    assert get_adapter("SK").code == "sk" and get_adapter("AUT").code == "at" and get_adapter("de").client == "GiiClient"
    with pytest.raises(ValueError, match="available: CZ, SK, AT, DE"):
        get_adapter("fr")
    assert isinstance(ADAPTERS["sk"].make_client(fetch=lambda u: ""), SlovLexClient)
    assert not ADAPTERS["de"].versions and not ADAPTERS["de"].stem


def test_default_acts_for_nis2():
    assert {c: DEFAULT_ACTS[("32022L2555", c)][0] for c in ("cz", "sk", "at", "de")} == {
        "cz": "264/2025", "sk": "69/2018", "at": "20013065", "de": "bsig_2025"}


# --- law -----------------------------------------------------------------------------------


def test_law_sk_section(capsys):
    assert main(["law", "sk", "69/2018", "--id", "par21.ods3", "-f", "jsonl"], slovlex=sk_client()) == 0
    rows = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rows[0]["id"] == "sk:69/2018:par21.ods3" and len(rows) > 1


def test_law_sk_article_roman(capsys):
    assert main(["law", "SVK", "69/2018", "-a", "II", "-f", "jsonl"], slovlex=sk_client()) == 0
    rows = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rows[0]["id"] == "sk:69/2018:cl2"


def test_law_at_section(capsys):
    assert main(["law", "at", "20013065", "--section", "32", "-f", "jsonl"], ris=at_client()) == 0
    ids = [json.loads(x)["id"] for x in capsys.readouterr().out.splitlines()]
    assert ids[0] == "at:20013065:par32" and "at:20013065:par32.abs4.lit_a" in ids


def test_law_de_section_and_no_versions(capsys):
    assert main(["law", "de", "bsig_2025", "-s", "30", "-f", "jsonl"], gii=de_client()) == 0
    ids = [json.loads(x)["id"] for x in capsys.readouterr().out.splitlines()]
    assert ids[0] == "de:bsig_2025:par30"
    assert main(["law", "de", "bsig_2025", "--version-date", "2025-01-01"], gii=de_client()) == 2
    assert "current text" in capsys.readouterr().err


# --- notified measures -> adapter references -------------------------------------------------


def _measures(name, country):
    return [NationalMeasure(m["uri"], country, m.get("reference"), m.get("title"), m.get("act_type"),
                            m.get("journal"), m.get("journal_date"), m.get("notified"), m.get("commission_ref"))
            for m in json.loads((FIXTURES / name).read_text(encoding="utf-8"))]


def test_map_measures_sk_all_mapped():
    out = map_measures("sk", _measures("sk_nis2_measures.json", "SVK"))
    assert len(out) == 9 and all(m.status == "mapped" for m in out)
    assert "69/2018-366/2024" in [m.ref for m in out]


def test_map_measures_at_with_sections():
    out = map_measures("at", _measures("at_nis2_measures.json", "AUT"))
    assert all(m.status == "mapped" for m in out)
    avg = next(m for m in out if m.abbreviation == "AVG")
    assert avg.sections == ["§ 45", "§ 58"] and avg.ref == "BGBl. Nr. 51/1991"
    nisg = next(m for m in out if m.ref == "NISG 2026")
    assert nisg.sections == []


def test_map_measures_cz_from_reference_and_title():
    ms = [NationalMeasure("u1", "CZE", "264/2025", "Zákon č. 264/2025 Sb.", None, None, None, None, None),
          NationalMeasure("u2", "CZE", "XYZ", "Vyhláška č. 409/2025 Sb., o bezpečnostních opatřeních", None, None, None,
                          None, None),
          NationalMeasure("u3", "CZE", None, "Explanatory note", None, None, None, None, None)]
    out = map_measures("cz", ms)
    assert [(m.status, m.ref) for m in out] == [("mapped", "264/2025"), ("mapped", "409/2025"), ("no_reference", None)]


def test_map_measures_de_uses_cellar_numbers_and_neuris():
    cellar = test_de.cellar_client()
    out = map_measures("de", cellar.national_measures("32022L2555", "DEU"), celex="32022L2555", cellar=cellar,
                       client=de_client())
    mapped = [m for m in out if m.status == "mapped"]
    assert [m.ref for m in mapped] == ["bsig_2025"]
    assert {m.status for m in out} >= {"land", "not_found", "not_a_law"}


def _sk_cellar():
    rows = json.loads((FIXTURES / "sk_nis2_measures.json").read_text(encoding="utf-8"))
    C = "http://publications.europa.eu/resource/authority/country/"

    def lit(v):
        return {"type": "literal", "value": v}

    bindings = [{"m": {"type": "uri", "value": r["uri"]}, "c": {"type": "uri", "value": C + "SVK"},
                 **{k: lit(r[k]) for k in ("reference", "title", "journal", "journal_date", "notified") if r.get(k)}}
                for r in rows]
    return CellarClient(lambda q: {"results": {"bindings": bindings}})


def test_cli_measures_map(capsys):
    assert main(["measures", "32022L2555", "-c", "SK", "--map"], client=_sk_cellar()) == 0
    out = capsys.readouterr().out
    assert "69/2018-366/2024" in out and "9 of 9 measures mapped to Slov-Lex" in out
    assert main(["measures", "32022L2555", "--map"], client=_sk_cellar()) == 2


def test_fetch_measure_sk_and_unmapped():
    doc = fetch_measure("sk", sk_client(), MeasureMapping("u", "t", "69/2018", "mapped", "69/2018"))
    assert doc.act_id == "69/2018"
    with pytest.raises(LookupError, match="not mapped"):
        fetch_measure("at", at_client(), MeasureMapping("u", "t", None, "land", None))


# --- align across countries ------------------------------------------------------------------


class FakeEurLex:
    def __init__(self, doc):
        self.doc = doc
        self.calls = []

    def document(self, celex, lang):
        self.calls.append((celex, lang))
        return self.doc


def eu_doc(lang: str) -> Document:
    celex = "32022L2555"
    d = Document("eu", celex, lang, title="NIS2")

    def add(pid, kind, label, number, text="", heading=None, parent=None):
        d.add(Provision(f"eu:{celex}:{lang}:{pid}", "eu", celex, lang, kind, label, number, text, heading,
                        f"eu:{celex}:{lang}:{parent}" if parent else None, url=f"https://eur-lex.europa.eu/{pid}"))

    add("art23", "article", "Artikel 23", "23", heading="Berichtspflichten")
    add("art23.par4", "paragraph", "(4)", "4", "Die Mitgliedstaaten stellen sicher, dass die Einrichtungen übermitteln:",
        parent="art23")
    add("art23.par4.pt_a", "point", "a)", "a", "unverzüglich, in jedem Fall aber innerhalb von 24 Stunden nach "
        "Kenntnisnahme des erheblichen Sicherheitsvorfalls eine Frühwarnung", parent="art23.par4")
    add("art21", "article", "Artikel 21", "21", heading="Risikomanagementmaßnahmen im Bereich der Cybersicherheit")
    add("art21.par2", "paragraph", "(2)", "2", "Die Maßnahmen umfassen zumindest Folgendes:", parent="art21")
    add("art21.par2.pt_a", "point", "a)", "a", "Konzepte in Bezug auf die Risikoanalyse und Sicherheit für "
        "Informationssysteme;", parent="art21.par2")
    return d


def test_align_austria_with_fixture_act():
    eurlex = FakeEurLex(eu_doc("de"))
    eu, docs = load_documents("32022L2555", "at", eurlex=eurlex, national_client=at_client())
    assert eurlex.calls == [("32022L2555", "de")]
    assert [d.act_id for d in docs] == ["20013065"]
    res = align(eu, docs, embedder=choose_embedder("hybrid-lex", "de"))
    assert ":nostem" in res.method  # German: no Czech stemmer
    by = res.by_eu()
    assert by["eu:32022L2555:de:art21.par2.pt_a"].links == ["at:20013065:par32.abs4.lit_a"]
    assert res.transposing_acts == ["20013065"]
    assert path_label(docs[0], "at:20013065:par32.abs4.lit_a") == "§ 32 Abs. 4 lit. a"
    assert path_label(eu, "eu:32022L2555:de:art23.par4.pt_a") == "Art. 23 Abs. 4 Buchst. a"
    d = res.to_dict()
    assert d["schema"] == 2 and all(x["status"] == "candidate" for x in d["extra_rule_candidates"])


def test_align_slovakia_skips_duplicate_measure_and_labels():
    eurlex = FakeEurLex(eu_doc("sk"))
    logs = []
    measures = [MeasureMapping("u", "t", "69/2018", "mapped", "69/2018"),
                MeasureMapping("u2", "t", "MF/1", "no_reference")]
    eu, docs = load_documents("32022L2555", "sk", eurlex=eurlex, national_client=sk_client(), measures=measures,
                              log=logs.append)
    assert eurlex.calls == [("32022L2555", "sk")]
    assert [d.act_id for d in docs] == ["69/2018"]
    assert any("already fetched" in m for m in logs)
    # "Čl. I" wrapping the §§ is not part of the citation; amending articles keep theirs
    assert path_label(docs[0], "sk:69/2018:par21.ods3.pism_a") == "§ 21 ods. 3 písm. a)"
    assert path_label(docs[0], "sk:69/2018:cl2") == "Čl. II"


def test_align_germany_current_text_only():
    eurlex = FakeEurLex(eu_doc("de"))
    eu, docs = load_documents("32022L2555", "de", eurlex=eurlex, national_client=de_client())
    assert [d.act_id for d in docs] == ["bsig_2025"]
    assert path_label(docs[0], "de:bsig_2025:par30.abs1") == "§ 30 Abs. 1"


def test_cli_align_country_dispatch(tmp_path, capsys):
    out = tmp_path / "at.json"
    rc = main(["align", "32022L2555", "at", "-e", "hybrid-lex", "-o", str(out)],
              eurlex=FakeEurLex(eu_doc("de")), ris=at_client())
    assert rc == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["national"][0]["act_id"] == "20013065" and d["eu"]["language"] == "de"
    assert "main act 20013065" in capsys.readouterr().err


# --- embedders and thresholds ---------------------------------------------------------------


def test_choose_embedder_auto_without_extra(monkeypatch):
    import importlib.util as iu

    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec", lambda name, *a: None if name == "sentence_transformers" else real(name, *a))
    e = choose_embedder("auto", "cs")
    assert e.name.startswith("hybrid:") and "embed:" not in e.name and ":nostem" not in e.name
    assert ":nostem" in choose_embedder("auto", "de").name
    with pytest.raises(ValueError, match="openacquis\\[embeddings\\]"):
        choose_embedder("hybrid", "cs")
    with pytest.raises(ValueError):
        choose_embedder("nonsense", "cs")


def test_thresholds_and_margins_per_family():
    lex_hybrid = "hybrid:z-query(lexical:char3-5*1,lexical:char3-5@body*0.5,bm25*0.5)+prior(sec0.25,sib1)"
    dense_hybrid = "hybrid:z-query(lexical:char3-5*1,embed:intfloat/multilingual-e5-base:sym*1)"
    assert default_threshold(lex_hybrid) == 0.17
    assert default_threshold(dense_hybrid) == 0.14
    assert default_threshold("lexical:char3-5:nostem") == 0.20
    assert default_gap_margin(lex_hybrid) == 0.01 and default_gap_margin(dense_hybrid) == 0.01
    assert default_gap_margin("embed:intfloat/multilingual-e5-base") == 0.005
