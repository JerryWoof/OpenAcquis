"""CLI tests for ``text`` and ``law`` (offline: saved fixtures, stubbed network)."""

import json
import sys
from pathlib import Path

import pytest

from openacquis.adapters.cz import ESbirkaClient
from openacquis.cli import main
from openacquis.eurlex import EurLexClient, Response

sys.path.insert(0, str(Path(__file__).parent))
from test_cz import FakeFetch  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def eurlex_client(lang="cs"):
    body = (FIXTURES / f"eurlex_32022L2555_{lang}.xhtml").read_bytes()
    seen = []

    def get(url, headers):
        seen.append(headers.get("Accept-Language"))
        return Response(200, url, {"content-type": "application/xhtml+xml; charset=UTF-8"}, body)

    client = EurLexClient(get=get, cache_dir=False)
    client.seen = seen
    return client


def esbirka_client():
    return ESbirkaClient(FakeFetch("264/2025", pages=2))


# --- text -------------------------------------------------------------------


def test_text_article_tree(capsys):
    client = eurlex_client("cs")
    assert main(["text", "32022L2555", "--lang", "cs", "--article", "23"], eurlex=client) == 0
    out = capsys.readouterr().out
    assert client.seen[0] == "cs"
    assert out.startswith("32022L2555  SMĚRNICE EVROPSKÉHO PARLAMENTU A RADY (EU) 2022/2555")
    assert "\nČlánek 23 Oznamovací povinnosti\n" in out
    assert "\n    a) bez zbytečného odkladu, nejpozději však do 24 hodin" in out
    assert "\n      i) podrobný popis incidentu" in out
    assert "Článek 46" not in out
    assert out.rstrip().endswith("23 provisions (article 1, paragraph 11, point 7, subpoint 4)")


def test_text_json_whole_document(capsys):
    assert main(["text", "32022l2555", "-l", "en", "-f", "json"], eurlex=eurlex_client("en")) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["act_id"] == "32022L2555" and doc["language"] == "en"
    assert doc["eli"] == "http://data.europa.eu/eli/dir/2022/2555/oj"
    ids = [p["id"] for p in doc["provisions"]]
    assert "eu:32022L2555:en:art23.par4.pt_a" in ids
    assert not any(":rct" in i for i in ids)  # recitals only with --recitals


def test_text_recitals_flag(capsys):
    assert main(["text", "32022L2555", "-f", "jsonl", "--recitals"], eurlex=eurlex_client("en")) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["kind"] == "recital"


def test_text_id_jsonl(capsys):
    rc = main(["text", "32022L2555", "-l", "cs", "--id", "art23.par4.pt_a", "-f", "jsonl"], eurlex=eurlex_client())
    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    p = json.loads(lines[0])
    assert p["id"] == "eu:32022L2555:cs:art23.par4.pt_a" and p["label"] == "a)"
    assert p["url"].endswith("#023.004")


def test_text_missing_article(capsys):
    assert main(["text", "32022L2555", "-l", "cs", "--article", "99"], eurlex=eurlex_client()) == 2
    assert "art99 not found" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["text", "nis2"], ["text", "32022L2555", "--lang", "cz"]])
def test_text_bad_input(argv, capsys):
    assert main(argv, eurlex=eurlex_client()) == 2
    assert capsys.readouterr().err.startswith("error:")


# --- law --------------------------------------------------------------------


def test_law_section_subtree(capsys):
    assert main(["law", "cz", "264/2025", "--section", "2"], esbirka=esbirka_client()) == 0
    out = capsys.readouterr().out
    assert out.startswith("264/2025  Zákon č. 264/2025 Sb., o kybernetické bezpečnosti\n")
    assert "https://e-sbirka.gov.cz/sb/2025/264/2025-11-01" in out
    assert "\n§ 2" in out
    assert "\n  (1) Pro účely tohoto zákona se rozumí" in out
    assert "\n    b) informací zpracovaná" in out
    assert "§ 4" not in out


def test_law_id_json(capsys):
    rc = main(["law", "CZE", "č. 264/2025 Sb.", "--id", "par2.odst1.pism_b", "-f", "json"], esbirka=esbirka_client())
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["source"] == "cz" and doc["act_id"] == "264/2025"
    assert [p["id"] for p in doc["provisions"]] == ["cz:264/2025:par2.odst1.pism_b"]


def test_law_whole_act_jsonl(capsys):
    assert main(["law", "cz", "264/2025", "-f", "jsonl"], esbirka=esbirka_client()) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == len({r["id"] for r in rows}) > 20
    assert all(r["id"].startswith("cz:264/2025:") for r in rows)


def test_law_missing_section(capsys):
    assert main(["law", "cz", "264/2025", "--section", "999"], esbirka=esbirka_client()) == 2
    assert "par999 not found" in capsys.readouterr().err


def test_law_at_section_inside_artikel(capsys):
    from test_at import dsg_fetch

    # DSG: the §§ sit in Artikel 2, so "-s 4" is art2.par4, not par4
    assert main(["law", "at", "10001597", "-s", "4", "-f", "json"], ris=dsg_fetch().client) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["provisions"][0]["id"] == "at:10001597:art2.par4"
    assert main(["law", "at", "10001597", "-s", "99"], ris=dsg_fetch().client) == 2
    assert "par99 not found" in capsys.readouterr().err


@pytest.mark.parametrize("country", ["fr", "xx"])
def test_law_unsupported_country(country, capsys):
    assert main(["law", country, "1/2020"], esbirka=esbirka_client()) == 2
    assert capsys.readouterr().err.startswith("error:")


@pytest.mark.live
def test_text_and_law_live(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENACQUIS_CACHE", str(tmp_path))
    assert main(["text", "32022L2555", "--lang", "cs", "--article", "23"]) == 0
    assert "Článek 23" in capsys.readouterr().out
    assert main(["law", "cz", "264/2025", "--section", "2", "-f", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["provisions"][0]["id"] == "cz:264/2025:par2"
