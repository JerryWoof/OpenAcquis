import importlib.util
import json
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


def test_curated_list_is_valid():
    from openacquis.cellar import validate_directive_celex

    celexes = [e.celex for e in build_site.DIRECTIVES]
    assert len(set(celexes)) == len(celexes) >= 8
    for c in celexes:
        assert validate_directive_celex(c) == c
