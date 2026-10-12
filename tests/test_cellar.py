import json

import pytest

from openacquis import CellarClient
from openacquis.cellar import count_by_country, validate_directive_celex
from openacquis.cli import main
from openacquis.countries import normalize_country

C = "http://publications.europa.eu/resource/authority/country/"


def binding(**kw):
    return {k: {"type": "literal", "value": v} for k, v in kw.items()}


DIRECTIVE = {
    "results": {
        "bindings": [
            binding(
                w="http://publications.europa.eu/resource/cellar/x",
                title="Directive (EU) 2022/2555 (NIS 2 Directive)",
                date="2022-12-14",
                deadline="2024-10-17",
            )
        ]
    }
}

MEASURES = {
    "results": {
        "bindings": [
            binding(m="urn:m1", c=C + "CZE", reference="264/2025", title="Zákon č. 264/2025 Sb., o kybernetické bezpečnosti", act_type="Zákon", journal_date="2025-06-11"),
            binding(m="urn:m2", c=C + "CZE", reference="408/2025", title="Vyhláška č. 408/2025 Sb.", act_type="Vyhláška", journal_date="2025-10-14"),
            binding(m="urn:m3", c=C + "DEU", reference="BGBl. 2025 I", act_type="Gesetz"),
        ]
    }
}


class FakeFetch:
    def __init__(self):
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        return MEASURES if "measure_national_implementing" in query else DIRECTIVE


def test_validate_celex():
    assert validate_directive_celex(" 32022l2555 ") == "32022L2555"
    with pytest.raises(ValueError):
        validate_directive_celex("32016R0679")  # GDPR is a regulation, not a directive


def test_normalize_country():
    assert normalize_country("cz") == "CZE"
    assert normalize_country("EL") == "GRC"
    assert normalize_country("deu") == "DEU"
    with pytest.raises(ValueError):
        normalize_country("XX")


def test_directive_parsing():
    d = CellarClient(FakeFetch()).directive("32022L2555")
    assert d.title.endswith("(NIS 2 Directive)")
    assert d.transposition_deadlines == ["2024-10-17"]
    assert d.eurlex_nim_url.endswith("CELEX:32022L2555")


def test_measures_and_country_filter():
    fetch = FakeFetch()
    measures = CellarClient(fetch).national_measures("32022L2555", country="cz")
    assert f"<{C}CZE>" in fetch.queries[-1]
    assert [m.country for m in measures] == ["CZE", "CZE", "DEU"]
    assert measures[0].reference == "264/2025"
    assert count_by_country(measures) == {"CZE": 2, "DEU": 1}


def test_cli_json(capsys):
    rc = main(["measures", "32022L2555", "-f", "json"], client=CellarClient(FakeFetch()))
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["title"].startswith("Zákon č. 264/2025")


def test_cli_summary(capsys):
    rc = main(["summary", "32022L2555"], client=CellarClient(FakeFetch()))
    out = capsys.readouterr().out
    assert rc == 0
    assert "3 from 2 member states" in out
    assert "IRL Ireland" in out and "none notified" in out


def test_cli_bad_celex(capsys):
    assert main(["summary", "not-a-celex"], client=CellarClient(FakeFetch())) == 2


@pytest.mark.live
def test_live_nis2_czechia():
    measures = CellarClient().national_measures("32022L2555", country="CZE")
    refs = {m.reference for m in measures}
    assert "264/2025" in refs  # Czech Cybersecurity Act
