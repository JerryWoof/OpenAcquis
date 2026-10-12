import copy
import io
import json
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from openacquis import alerts
from openacquis.alerts import (
    NIS2_WATCH,
    Http,
    Response,
    SourceError,
    WatchAct,
    WatchList,
    alignment_index,
    dump_state,
    load_watchlist,
    parse_watchlist,
    render_feed,
    run_check,
    write_outputs,
    write_state,
)

FIX = Path(__file__).parent / "fixtures"
NOW1 = "2026-10-12T05:00:00Z"
NOW2 = "2026-10-19T05:00:00Z"


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


def gii_zip(xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("BJNR12D0B0025.xml", xml)
    return buf.getvalue()


class FakeWeb:
    """Answers the change-signal requests of every source from the trimmed fixtures (and records them)."""

    def __init__(self):
        self.eu = json.loads(fixture("alerts_cellar_eu.json"))
        self.nim = json.loads(fixture("alerts_cellar_nim.json"))
        self.historie = json.loads(fixture("alerts_cz_historie.json"))
        self.souvislosti = json.loads(fixture("alerts_cz_souvislosti.json"))
        self.sk_page = fixture("alerts_sk_69_2018.html")
        self.sk_md5 = "Dcf8MKjhHyjJrvYTVCrCQA=="
        self.ris = json.loads(fixture("alerts_at_20013065.json"))
        self.gii_xml = fixture("alerts_de_bsig_2025.xml")
        self.gii_etag = '"cede-657b138fa6953"'
        self.down: set[str] = set()  # hosts answering 503
        self.log: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, headers, data=None):
        self.log.append((method, url, headers))
        host = urllib.parse.urlsplit(url).netloc
        if host in self.down:
            return Response(503, {}, b"maintenance")
        if "sparql" in url:
            q = urllib.parse.parse_qs(data.decode())["query"][0]
            body = self.nim if "measure_national_implementing" in q else self.eu
            return Response(200, {}, json.dumps(body).encode())
        if url.endswith("/historie"):
            return Response(200, {}, json.dumps(self.historie).encode())
        if url.endswith("/souvislosti"):
            return Response(200, {}, json.dumps(self.souvislosti).encode())
        if "slov-lex" in url:
            if method == "HEAD":
                return Response(200, {"content-md5": self.sk_md5})
            return Response(200, {"content-md5": self.sk_md5}, self.sk_page)
        if "data.bka.gv.at" in url:
            return Response(200, {}, json.dumps(self.ris).encode())
        if "gesetze-im-internet" in url:
            if headers.get("If-None-Match") == self.gii_etag:
                return Response(304, {"etag": self.gii_etag})
            return Response(200, {"etag": self.gii_etag, "last-modified": "Tue, 28 Jul 2026 19:55:37 GMT"},
                            gii_zip(self.gii_xml))
        return Response(404, {}, b"")

    def requests(self, needle: str) -> list[tuple[str, str, dict]]:
        return [r for r in self.log if needle in r[1]]


def http(web) -> Http:
    return Http(web, delay=0, backoff=0, sleep=lambda s: None)


def nis2() -> WatchList:
    return WatchList(dict(NIS2_WATCH.directives), list(NIS2_WATCH.acts), "test")


@pytest.fixture
def seeded():
    web = FakeWeb()
    state, report = run_check(nis2(), None, http(web), now=NOW1)
    return web, state, report


# --- watch list ------------------------------------------------------------------------------


def test_watchlist_registry_shape_and_fallback(tmp_path):
    data = {"schema": 1, "directives": [
        {"celex": "32022L2555", "short": "NIS2", "countries": {
            "cz": {"status": "ok", "acts": ["264/2025"], "label": "264/2025 Sb."},
            "de": {"status": "ok", "acts": ["bsig_2025", "bsi-kritisv"], "label": "BSIG"},
            "fr": {"status": "ok", "acts": ["x"]},  # no adapter: CELLAR still watches its measures
            "at": {"status": "none", "acts": []}}},
        {"celex": "32023L0970", "short": "Pay Transparency", "countries": {"cz": {"acts": ["264/2025"]}}},
        {"celex": "not-a-celex"},
    ]}
    w = parse_watchlist(data)
    assert w.directives == {"32022L2555": "NIS2", "32023L0970": "Pay Transparency"}
    assert [(a.country, a.ref) for a in w.acts] == [("cz", "264/2025"), ("de", "bsig_2025"), ("de", "bsi-kritisv")]
    assert w.acts[0].label == "CZ 264/2025 Sb. (NIS2, Pay Transparency)"
    assert w.acts[2].label == "DE bsi-kritisv (NIS2)"
    assert load_watchlist(tmp_path / "missing.json").directives == {"32022L2555": "NIS2"}
    assert len(load_watchlist(None).acts) == 4


def test_watchlist_other_shapes():
    assert parse_watchlist(["32022L2555", "x"]).directives == {"32022L2555": "32022L2555"}
    w = parse_watchlist([{"celex": "32022L2555", "national": [{"country": "SVK", "act_id": "69/2018"}],
                          "acts": {"at": {"act_id": "20013065", "label": "AT NISG 2026"}}}])
    assert {(a.country, a.ref) for a in w.acts} == {("sk", "69/2018"), ("at", "20013065")}
    assert alerts.act_key(WatchAct("sk", "69/2018-366/2024")) == "sk:69/2018"
    assert alerts.act_key(WatchAct("at", "NISG 2026")) is None  # needs a Gesetzesnummer
    with pytest.raises(ValueError):
        parse_watchlist("32022L2555")


# --- baseline and quiet runs ---------------------------------------------------------------------


def test_first_run_is_a_silent_baseline(seeded):
    web, state, report = seeded
    assert report["changes"] == [] and report["warnings"] == []
    assert set(state["items"]) == {"eu:32022L2555", "nim:32022L2555:CZE", "nim:32022L2555:DEU", "cz:264/2025",
                                   "sk:69/2018", "at:20013065", "de:bsig_2025"}
    assert len(report["baseline"]) == 7
    cz = state["items"]["cz:264/2025"]["data"]
    assert cz["versions"][0] == ["/sb/2012/89/2027-01-01", "BUDOUCI", ["159/2026 Sb.", "460/2016 Sb."]]
    assert cz["related"] == {"JE_PROVADEN": ["334/2025 Sb.", "408/2025 Sb."]}  # MENI (what it amends) ignored
    sk = state["items"]["sk:69/2018"]["data"]
    assert sk["versions"][-1] == ["/SK/ZZ/2018/69/20260430", "2026-04-30", "", ["67/2026"]]
    at = state["items"]["at:20013065"]["data"]
    assert len(at["norms"]) == 3 and at["amendments"] == ["BGBl. I Nr. 94/2025"]
    de = state["items"]["de:bsig_2025"]["data"]
    assert de["builddate"] == "20260728215536" and de["etag"] == web.gii_etag
    assert de["stand"][0] == "Geändert durch Art. 4 G v. 11.3.2026 I Nr. 66"
    nim = state["items"]["nim:32022L2555:CZE"]["data"]["measures"]
    assert len(nim) == 3 and nim[0][0].startswith("72022L2555CZE_")


def test_second_run_quiet_cheap_and_state_untouched(seeded, tmp_path):
    web, state, _ = seeded
    path = tmp_path / "state.json"
    assert write_state(path, state)
    web.log.clear()
    state2, report = run_check(nis2(), json.loads(path.read_text()), http(web), now=NOW2)
    assert report["changes"] == [] and report["baseline"] == [] and report["warnings"] == []
    # Slov-Lex: HEAD only (same content-md5); gesetze-im-internet.de: conditional GET answered 304
    assert [m for m, _, _ in web.requests("slov-lex")] == ["HEAD"]
    assert web.requests("gesetze-im-internet")[0][2]["If-None-Match"] == web.gii_etag
    assert not write_state(path, state2, json.loads(path.read_text()))  # only timestamps differ
    assert json.loads(dump_state(state2)) == state2


# --- changes per source -------------------------------------------------------------------------


def test_cz_new_future_version_in_force_and_decree(seeded):
    web, state, _ = seeded
    h = web.historie["historie"]
    h[0]["typZneni"] = "AKTUALNI"  # the 2027-01-01 version came into force
    h.insert(0, {"staleUrl": "/sb/2012/89/2028-01-01", "typZneni": "BUDOUCI",
                 "novely": [{"kodDokumentuSbirky": "12/2027 Sb."}]})
    web.souvislosti["souvislosti"][1]["dokumentySbirky"].append({"kodDokumentuSbirky": "99/2026 Sb."})
    _, report = run_check(nis2(), state, http(web), now=NOW2)
    (c,) = report["changes"]
    assert c["key"] == "cz:264/2025"
    assert c["kinds"] == ["implementing_decree", "new_version", "version_in_force"]
    assert c["lines"][0] == "future version from 2028-01-01 published (amended by 12/2027 Sb.)"
    assert "version from 2027-01-01 is now in force" in c["lines"]
    assert "new implementing regulation 99/2026 Sb." in c["lines"]
    assert c["title"].startswith("[change-alert] CZ 264/2025 Sb. (Cybersecurity Act): future version from 2028-01-01")


def test_sk_rewrite_without_new_content_is_quiet_new_row_alerts(seeded):
    web, state, _ = seeded
    web.sk_md5 = "changed=="
    state2, report = run_check(nis2(), state, http(web), now=NOW2)
    assert report["changes"] == []
    assert state2["items"]["sk:69/2018"]["data"]["md5"] == "changed=="
    web.sk_md5 = "changed-again=="
    web.sk_page = web.sk_page.replace(b"</table>", (
        b'<tr class="effectivenessHistoryItem" data-iri="/SK/ZZ/2018/69/20990101" data-vyhlasene="0" '
        b'data-ucinnostod="2099-01-01" data-ucinnostdo=""><td>12.</td><td></td>'
        b'<td><a href="x">5/2099&nbsp;Z.&nbsp;z.</a></td></tr></table>'))
    _, report = run_check(nis2(), state2, http(web), now=NOW2)
    (c,) = report["changes"]
    assert c["lines"] == ["future version from 2099-01-01 (amended by 5/2099 Z. z.)"]


def test_at_norm_changed_and_amendment(seeded):
    web, state, _ = seeded
    docs = web.ris["OgdSearchResult"]["OgdDocumentResults"]["OgdDocumentReference"]
    docs[1]["Data"]["Metadaten"]["Allgemein"]["Geaendert"] = "2026-10-15"
    docs[0]["Data"]["Metadaten"]["Bundesrecht"]["BrKons"]["Aenderung"] = "BGBl. I Nr. 94/2025, BGBl. I Nr. 7/2026"
    _, report = run_check(nis2(), state, http(web), now=NOW2)
    (c,) = report["changes"]
    assert c["kinds"] == ["amending_act", "norm_changed"]
    assert c["lines"][-1] == "amended by BGBl. I Nr. 7/2026"


def test_de_new_build_with_pending_amendment(seeded):
    web, state, _ = seeded
    web.gii_etag = '"new"'
    web.gii_xml = web.gii_xml.replace(b"20260728215536", b"20261015101010").replace(
        b"</langue>", "</langue><standangabe><standtyp>Hinweis</standtyp><standkommentar>Änderung durch Art. 2 "
                      "G v. 1.10.2026 I Nr. 300 textlich nachgewiesen</standkommentar></standangabe>".encode())
    _, report = run_check(nis2(), state, http(web), now=NOW2)
    (c,) = report["changes"]
    assert c["kinds"] == ["new_build", "pending_amendment"]
    assert c["lines"][0] == "new text build 2026-10-15 (was 2026-07-28)"


def test_cellar_new_measure_new_country_and_new_directive(seeded):
    web, state, _ = seeded
    b = web.nim["results"]["bindings"]
    b.append({"celex": {"value": "32022L2555"}, "mc": {"value": "72022L2555DEU_999"}, "notif": {"value": "2026-10-14"}})
    b.append({"celex": {"value": "32022L2555"}, "mc": {"value": "72022L2555ESP_1"}, "notif": {"value": "2026-10-14"}})
    web.eu["results"]["bindings"].append({"celex": {"value": "32022L2555"}, "rel": {"value": "consolidated"},
                                          "xc": {"value": "02022L2555-20261101"}})
    watch = nis2()
    watch.directives["32023L0970"] = "Pay Transparency"  # newly watched: baseline, not an alert
    b.append({"celex": {"value": "32023L0970"}, "mc": {"value": "72023L0970CZE_5"}})
    _, report = run_check(watch, state, http(web), now=NOW2)
    by = {c["key"]: c for c in report["changes"]}
    assert set(by) == {"eu:32022L2555", "nim:32022L2555:DEU", "nim:32022L2555:ESP"}
    assert by["eu:32022L2555"]["lines"] == ["new consolidated version 02022L2555-20261101"]
    assert by["nim:32022L2555:DEU"]["lines"] == [
        "1 new national measure(s) notified (notified 2026-10-14): 72022L2555DEU_999"]
    assert by["nim:32022L2555:ESP"]["label"] == "NIS2 × Spain"
    assert set(report["baseline"]) == {"eu:32023L0970", "nim:32023L0970:CZE"}


def test_vanished_country_is_a_warning_not_a_change(seeded):
    web, state, _ = seeded
    b = web.nim["results"]["bindings"]
    b[:] = [x for x in b if "DEU" not in x["mc"]["value"]]
    state2, report = run_check(nis2(), state, http(web), now=NOW2)
    assert report["changes"] == []
    assert "nim:32022L2555:DEU" in report["warnings"][0]
    assert state2["items"]["nim:32022L2555:DEU"]["fingerprint"] == state["items"]["nim:32022L2555:DEU"]["fingerprint"]


def test_vanished_country_is_reported_after_a_few_runs_without_health_alert(seeded):
    web, state, _ = seeded
    b = web.nim["results"]["bindings"]
    b[:] = [x for x in b if "DEU" not in x["mc"]["value"]]
    for i in range(alerts.VANISH_AFTER - 1):
        state, report = run_check(nis2(), state, http(web), now=f"2026-10-{20 + i}T05:00:00Z")
        assert report["changes"] == [] and report["unhealthy"] == {}
        assert state["health"]["cellar"]["failures"] == 0
    state, report = run_check(nis2(), state, http(web), now=NOW2)
    assert [c["key"] for c in report["changes"]] == ["nim:32022L2555:DEU"]
    assert report["unhealthy"] == {} and state["health"]["cellar"]["failures"] == 0
    state, report = run_check(nis2(), state, http(web), now="2026-10-26T05:00:00Z")
    assert report["changes"] == [] and "nim:32022L2555:DEU" not in state["items"]


def test_empty_eu_side_is_an_outage_not_a_change(seeded):
    web, state, _ = seeded
    assert state["items"]["eu:32022L2555"]["data"]["related"]
    web.eu = {"results": {"bindings": []}}
    for i in range(alerts.VANISH_AFTER + 1):
        state2, report = run_check(nis2(), state, http(web), now=f"2026-10-{20 + i}T05:00:00Z")
        assert report["changes"] == []
        assert state2["items"]["eu:32022L2555"]["fingerprint"] == state["items"]["eu:32022L2555"]["fingerprint"]
        state = state2
    assert any("related" in w for w in report["warnings"])
    assert set(report["unhealthy"]) == {"cellar"}


def test_one_directive_losing_related_acts_waits_before_reporting(seeded):
    web, state, _ = seeded
    watch = nis2()
    watch.directives["32023L0970"] = "Pay Transparency"
    web.eu["results"]["bindings"].append({"celex": {"value": "32023L0970"}, "rel": {"value": "corrected_by"},
                                          "xc": {"value": "32023L0970R(01)"}})
    state, _ = run_check(watch, state, http(web), now=NOW2)
    web.eu["results"]["bindings"] = [x for x in web.eu["results"]["bindings"]
                                     if x["celex"]["value"] != "32022L2555"]
    for i in range(alerts.VANISH_AFTER - 1):
        state, report = run_check(watch, state, http(web), now=f"2026-10-{20 + i}T05:00:00Z")
        assert report["changes"] == [] and "eu:32022L2555" in " ".join(report["warnings"])
    state, report = run_check(watch, state, http(web), now="2026-10-26T05:00:00Z")
    assert [c["key"] for c in report["changes"]] == ["eu:32022L2555"]


def test_source_subset_keeps_the_other_items(seeded):
    web, state, _ = seeded
    state2, report = run_check(nis2(), state, http(web), sources=["cz"], now=NOW2)
    assert set(state2["items"]) == set(state["items"])
    assert state2["items"]["sk:69/2018"] == state["items"]["sk:69/2018"]
    _, report = run_check(nis2(), state2, http(web), now="2026-10-26T05:00:00Z")
    assert report["baseline"] == [] and report["changes"] == []


# --- outages ------------------------------------------------------------------------------------


def test_outage_keeps_fingerprint_counts_failures_and_recovers(seeded, tmp_path):
    web, state, _ = seeded
    web.down = {"e-sbirka.gov.cz", "publications.europa.eu"}
    for i in range(3):
        state, report = run_check(nis2(), state, http(web), now=f"2026-10-{20 + i}T05:00:00Z")
        assert report["changes"] == []
        assert "cz:264/2025" in state["items"] and "nim:32022L2555:CZE" in state["items"]
    assert len(report["warnings"]) == 2 and "HTTP 503" in report["warnings"][0]
    assert set(report["unhealthy"]) == {"cellar", "cz"}
    assert state["health"]["cz"]["failures"] == 3 and state["health"]["sk"]["failures"] == 0
    write_outputs(report, tmp_path)
    assert "e-sbirka.gov.cz" in (tmp_path / "health.md").read_text()
    web.down = set()
    state, report = run_check(nis2(), state, http(web), now=NOW2)
    assert report["changes"] == [] and report["unhealthy"] == {}
    write_outputs(report, tmp_path)
    assert not (tmp_path / "health.md").exists()


def test_http_retries_5xx_but_not_4xx():
    calls = []

    def flaky(method, url, headers, data=None):
        calls.append(url)
        if url.endswith("/gone"):
            return Response(404, {})
        return Response(503, {}) if len(calls) < 3 else Response(200, {}, b"ok")

    sleeps = []
    h = Http(flaky, delay=0, retries=2, backoff=1, sleep=sleeps.append)
    assert h.request("https://x.test/a").body == b"ok"
    assert sleeps == [1, 2] and h.requests == 3
    with pytest.raises(SourceError, match="HTTP 404"):
        h.request("https://x.test/gone")
    assert h.requests == 4


def test_user_agent_and_delay_per_host():
    seen = []
    clock = []

    def fetch(method, url, headers, data=None):
        seen.append(headers["User-Agent"])
        return Response(200, {}, b"{}")

    h = Http(fetch, delay=5, sleep=clock.append)
    h.request("https://a.test/1")
    h.request("https://b.test/1")  # other host: no wait
    h.request("https://a.test/2")
    assert seen[0].startswith("OpenAcquis/") and len(clock) == 1 and clock[0] > 4


# --- outputs ------------------------------------------------------------------------------------


def test_issue_texts_are_inert(seeded, tmp_path):
    web, state, _ = seeded
    web.gii_etag = '"x"'
    evil = "@maintainer [click](https://evil.test) `rm -rf` <img src=x> ‮"
    web.gii_xml = web.gii_xml.replace("Geändert durch Art. 4 G v. 11.3.2026 I Nr. 66".encode(), evil.encode())
    align = {"de:bsig_2025": [{"id": "32022L2555_de", "date": "2026-07-29",
                               "url": "https://jerrywoof.github.io/OpenAcquis/alignment.html?id=32022L2555_de"}]}
    state2, report = run_check(nis2(), state, http(web), now=NOW2, align=align)
    write_outputs(report, tmp_path)
    issue = tmp_path / "issues" / "001"
    body = (issue / "body.md").read_text()
    assert body.startswith("<!-- openacquis-alert:de:bsig_2025 -->\n")
    assert (issue / "key").read_text() == "<!-- openacquis-alert:de:bsig_2025 -->"
    line = next(x for x in body.splitlines() if "maintainer" in x)
    assert line.startswith("- `") and line.endswith("`") and line.count("`") == 2  # one code span, no backticks inside
    assert "‮" not in body and "‮" not in (issue / "title").read_text()
    assert "built on the text of 2026-07-29" in body and "not a finding" in body
    assert json.loads((tmp_path / "changes.json").read_text())["changes"][0]["key"] == "de:bsig_2025"
    assert state2["events"][0]["key"] == "de:bsig_2025"


def test_feed_is_valid_atom_with_escaped_entries(seeded, tmp_path):
    web, state, _ = seeded
    empty = ET.fromstring(render_feed(state))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    assert empty.findall("a:entry", ns) == [] and empty.find("a:updated", ns).text == NOW1
    web.historie["historie"].insert(0, {"staleUrl": "/sb/2012/89/2028-01-01", "typZneni": "BUDOUCI",
                                        "novely": [{"kodDokumentuSbirky": "<b>&1/2027 Sb."}]})
    state2, _ = run_check(nis2(), state, http(web), now=NOW2)
    feed = ET.fromstring(render_feed(state2))
    (entry,) = feed.findall("a:entry", ns)
    assert "<b>&1/2027" in entry.find("a:content", ns).text
    assert entry.find("a:link", ns).get("href") == "https://e-sbirka.gov.cz/sb/2025/264"
    assert entry.find("a:id", ns).text.startswith(alerts.FEED_ID + "/cz:264/2025/")
    path = tmp_path / "state.json"
    write_state(path, state2)
    assert alerts.main(["feed", "--state", str(path), "--out", str(tmp_path / "feed.xml")]) == 0
    assert ET.parse(tmp_path / "feed.xml").getroot().find("a:entry", ns) is not None


def test_events_capped_and_alignment_index(tmp_path, seeded):
    _, state, _ = seeded
    s = copy.deepcopy(state)
    s["events"] = [{"key": f"k{i}"} for i in range(500)]
    web = FakeWeb()
    web.sk_md5 = "x"
    out, _ = run_check(nis2(), s, http(web), now=NOW2)
    assert len(out["events"]) == alerts.MAX_EVENTS
    d = tmp_path / "align"
    d.mkdir()
    (d / "32022L2555_sk.json").write_text(json.dumps(
        {"id": "32022L2555_sk", "country_code": "sk", "national": [{"act_id": "69/2018", "date": "2026-04-30"}]}))
    (d / "broken.json").write_text("{")
    (d / "32022L2555_sk.review.json").write_text(json.dumps(
        {"id": "x", "country_code": "sk", "national": [{"act_id": "69/2018"}]}))
    idx = alignment_index(d)
    assert idx == {"sk:69/2018": [{"id": "32022L2555_sk", "date": "2026-04-30",
                                   "url": "https://jerrywoof.github.io/OpenAcquis/alignment.html?id=32022L2555_sk"}]}


def test_load_state_rejects_other_files(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"schema": 99}')
    with pytest.raises(ValueError):
        alerts.load_state(p)
    assert alerts.load_state(tmp_path / "none.json") == {}


def test_committed_state_file_is_valid():
    path = Path(__file__).parent.parent / "data" / "alerts" / "state.json"
    if not path.exists():
        pytest.skip("no seeded state file")
    state = alerts.load_state(path)
    assert state["items"] and all(v["fingerprint"] == alerts.fingerprint(v["data"]) for v in state["items"].values())


# --- live ---------------------------------------------------------------------------------------


@pytest.mark.live
def test_live_signals_for_nis2_main_acts():
    h = Http(delay=1.0)
    cz = alerts.check_cz(h, WatchAct("cz", "264/2025"))
    assert any(v[1] == "AKTUALNI" for v in cz.data["versions"])
    sk = alerts.check_sk(h, WatchAct("sk", "69/2018"), None)
    assert sk.data["md5"] and len(sk.data["versions"]) >= 11
    de = alerts.check_de(h, WatchAct("de", "bsig_2025"), None)
    again = alerts.check_de(h, WatchAct("de", "bsig_2025"), de.data)  # 304 -> same data
    assert again.data == de.data and len(de.data["builddate"]) == 14


def test_cli_falls_back_to_static_alignment_pages(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(alerts, "run_check", lambda *a, **k: (seen.update(align=k["align"]) or ({}, {
        "watchlist": "w", "items": 0, "changes": [], "baseline": [], "warnings": [], "requests": 0, "seconds": 0})))
    static = tmp_path / "site" / "static-data"
    static.mkdir(parents=True)
    (static / "32022L2555_cz.json").write_text(json.dumps(
        {"id": "32022L2555_cz", "country_code": "cz", "national": [{"act_id": "264/2025", "date": "2025-11-01"}]}))
    (static / "32022L2555_cz.review.json").write_text("{}")
    assert alerts.main(["check", "--dry-run", "--sources", "cz"], root=tmp_path) == 0
    assert [x["id"] for x in seen["align"]["cz:264/2025"]] == ["32022L2555_cz"]
