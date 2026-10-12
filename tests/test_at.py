import json
import re
import ssl
import urllib.error
from pathlib import Path

import pytest

from openacquis.adapters.at import (
    ActRef,
    BgblRef,
    HttpFetcher,
    MeasureRef,
    RisClient,
    provision_id,
    fetch_act,
    measure_reference,
    parse_bgbl,
    parse_norms,
    parse_reference,
    parse_search,
)
from openacquis.provisions import Document

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = "2026-10-12"
NISG = "at:20013065"


def text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeFetch:
    """Serve saved RIS responses by URL; records every URL requested.

    Search URLs are built with the client's own ``search_url``; document XML is
    served from ``at_<NOR>.xml`` for any ``.../Bundesnormen/<NOR>/<NOR>.xml``.
    """

    def __init__(self, routes=None):
        self.urls = []
        self.routes = dict(routes or {})
        self.client = RisClient(self, today=lambda: TODAY)

    def search(self, fixture, page=1, **params):
        self.routes[self.client.search_url(page, **params)] = text(fixture)
        return self

    def __call__(self, url):
        self.urls.append(url)
        if url in self.routes:
            return self.routes[url]
        m = re.search(r"/Bundesnormen/(NOR\d+)/\1\.xml$", url)
        if m and (FIXTURES / f"at_{m.group(1)}.xml").exists():
            return text(f"at_{m.group(1)}.xml")
        raise LookupError(f"no fixture for {url}")


def nisg_fetch():
    f = FakeFetch()
    params = {"Gesetzesnummer": "20013065", "Fassung.FassungVom": TODAY}
    f.search("at_20013065_search_p1.json", 1, **params)
    f.search("at_20013065_search_p2.json", 2, **params)
    return f


@pytest.fixture(scope="module")
def nisg():
    fetch = nisg_fetch()
    doc = fetch.client.document("20013065")
    doc.fetch = fetch
    return doc


# --- references -------------------------------------------------------------


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("20013065", ActRef(gesetzesnummer="20013065")),
        ("BGBl. I Nr. 94/2025", ActRef(bgbl=BgblRef("I", "94", 2025))),
        ("BGBl I 94/2025", ActRef(bgbl=BgblRef("I", "94", 2025))),
        ("BGBl. II Nr. 215/2019", ActRef(bgbl=BgblRef("II", "215", 2019))),
        ("BGBl. Nr. 51/1991", ActRef(bgbl=BgblRef("", "51", 1991))),
        ("https://www.ris.bka.gv.at/eli/bgbl/i/2025/94/P3/NOR40273862", ActRef(bgbl=BgblRef("I", "94", 2025))),
        ("https://www.ris.bka.gv.at/eli/bgbl/1991/51", ActRef(bgbl=BgblRef("", "51", 1991))),
        ("https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=20013065&FassungVom=2026-10-01",
         ActRef(gesetzesnummer="20013065", version="2026-10-01")),
        ("NISG 2026", ActRef(abbreviation="NISG 2026")),
        ("B-VG", ActRef(abbreviation="B-VG")),
    ],
)
def test_parse_reference(ref, expected):
    assert parse_reference(ref) == expected


def test_parse_reference_version_and_errors():
    assert parse_reference("20013065", "2026-09-30").version == "2026-09-30"
    with pytest.raises(ValueError):
        parse_reference("20013065", "30.09.2026")
    for bad in ("264/2025", "irgendein Gesetz ohne Kürzel", ""):
        with pytest.raises(ValueError):
            parse_reference(bad)
    with pytest.raises(ValueError):
        ActRef(abbreviation="NISG").act_id  # needs resolving first


def test_bgbl_ref_forms():
    b = parse_bgbl("Stammfassung BGBl. I Nr. 5/2024, zuletzt geändert durch BGBl. I Nr. 52/2025")
    assert (b.citation, b.organ, b.nummer, b.eli) == (
        "BGBl. I Nr. 5/2024", "BGBl. I Nr.", "5/2024", "https://www.ris.bka.gv.at/eli/bgbl/i/2024/5")
    # RIS has issue ELIs only for the authentic electronic BGBl (2004 on); older ones would be 404
    assert parse_bgbl("BGBl. Nr. 1/1930").eli is None
    assert parse_bgbl("BGBl. I Nr. 100/2003").eli is None
    assert parse_bgbl("BGBl. II Nr. 100/2010").eli == "https://www.ris.bka.gv.at/eli/bgbl/ii/2010/100"
    assert parse_bgbl("kein Zitat") is None


# --- search API -------------------------------------------------------------


def test_parse_search_hits_and_errors():
    total, norms = parse_search(text("at_20013065_search_p1.json"))
    assert total == 8 and [n.apa for n in norms] == ["§ 0", "§ 1", "§ 3", "§ 3a", "§ 7"]
    title = norms[0]
    assert title.is_title and title.abkuerzung == "NISG 2026" and title.stammfassung == "BGBl. I Nr. 94/2025"
    p3 = norms[2]
    assert p3.nor == "NOR40273862" and p3.gesetzesnummer == "20013065" and p3.in_force == "2026-10-01"
    assert p3.eli == "https://ris.bka.gv.at/eli/bgbl/i/2025/94/P3/NOR40273862"
    assert p3.xml_url.endswith("/NOR40273862/NOR40273862.xml")
    err = {"OgdSearchResult": {"Error": {"Applikation": "BrKons", "Message": "Schema Validation Error"}}}
    with pytest.raises(LookupError, match="Schema Validation"):
        parse_search(json.dumps(err))
    empty = {"OgdSearchResult": {"OgdDocumentResults": {"Hits": {"#text": "0"}}}}
    assert parse_search(empty) == (0, [])


def test_no_text_in_force_is_lookup_error():
    f = FakeFetch()
    empty = json.dumps({"OgdSearchResult": {"OgdDocumentResults": {"Hits": {"#text": "0"}}}})
    f.routes[f.client.search_url(1, Gesetzesnummer="20010536", **{"Fassung.FassungVom": TODAY})] = empty
    with pytest.raises(LookupError, match="pass a version date"):
        f.client.document("20010536")


# --- document ---------------------------------------------------------------


def test_document_metadata_and_requests(nisg):
    assert (nisg.source, nisg.act_id, nisg.language) == ("at", "20013065", "de")
    assert nisg.title.startswith("Bundesgesetz zur Gewährleistung eines hohen Cybersicherheitsniveaus")
    assert nisg.title.endswith("(Netz- und Informationssystemsicherheitsgesetz 2026 – NISG 2026), StF: BGBl. I Nr. 94/2025")
    assert nisg.date == "2026-10-01"  # latest Inkrafttretensdatum among the documents in force
    assert nisg.eli == "https://www.ris.bka.gv.at/eli/bgbl/i/2025/94"
    assert nisg.url == "https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=20013065"
    urls = nisg.fetch.urls
    assert sum("Seitennummer=2" in u for u in urls) == 1  # second search page requested
    assert sum(u.endswith(".xml") for u in urls) == 7  # every document but the § 0 title document
    assert "NOR40277627" not in " ".join(u for u in urls if u.endswith(".xml"))


def test_ids_hierarchy_and_text(nisg):
    p = nisg.by_id(f"{NISG}:par3.z1.lit_b")
    assert (p.kind, p.label, p.number) == ("lit", "b)", "b")
    assert p.parent_id == f"{NISG}:par3.z1"
    assert p.text.startswith("ein Gerät oder eine Gruppe")
    z1 = nisg.by_id(f"{NISG}:par3.z1")
    assert z1.text == "„Netz- und Informationssystem“" and z1.parent_id == f"{NISG}:par3"
    assert "IKT-Produkt" in nisg.by_id(f"{NISG}:par3.z6").text  # <gdash/> is a hyphen
    par3 = nisg.by_id(f"{NISG}:par3")
    assert par3.text == "Im Sinne dieses Bundesgesetzes bedeutet"  # § 3. stripped, no Abs.
    assert par3.label == "§ 3" and par3.heading == "Begriffsbestimmungen"
    assert par3.eli == par3.url == "https://ris.bka.gv.at/eli/bgbl/i/2025/94/P3/NOR40273862"
    assert p.url == par3.url and p.eli is None  # sub-units link to their § document
    # lit directly under an Absatz (list level 2 without a Ziffer)
    lj = nisg.by_id(f"{NISG}:par32.abs4.lit_j")
    assert lj.parent_id == f"{NISG}:par32.abs4" and "Multi-Faktor-Authentifizierung" in lj.text
    # "a." items are litterae too
    assert nisg.by_id(f"{NISG}:par24.abs2.z3.lit_a").label == "a."
    assert len(nisg.children(f"{NISG}:par32")) == 5


def test_absatz_markers_and_closing_text(nisg):
    a1 = nisg.by_id(f"{NISG}:par1.abs1")
    assert a1.label == "(1)" and a1.text.startswith("(Verfassungsbestimmung) Die Erlassung")
    assert nisg.by_id(f"{NISG}:par1").text == ""
    a3 = nisg.by_id(f"{NISG}:par7.abs3")
    lines = a3.text.split("\n")  # the Schlussteil after the list is appended to the Absatz
    assert lines[0].endswith("zu entziehen, wenn") and lines[1].startswith("Nach einer rechtskräftigen Entziehung")
    assert [c.id for c in nisg.children(a3.id)] == [f"{NISG}:par7.abs3.z1", f"{NISG}:par7.abs3.z2"]
    assert nisg.by_id(f"{NISG}:par7.abs6").text.endswith("\nfestlegen.")


def test_structure_headings_and_annex(nisg):
    h1 = nisg.by_id(f"{NISG}:hauptstueck1")
    assert (h1.label, h1.heading, h1.parent_id) == ("1. Hauptstück", "Allgemeine Bestimmungen", None)
    assert nisg.by_id(f"{NISG}:par1").parent_id == h1.id
    ab = nisg.by_id(f"{NISG}:hauptstueck2.abschnitt1")
    assert ab.heading == "Zuständige Behörde" and ab.parent_id == f"{NISG}:hauptstueck2"
    assert ab.url == nisg.by_id(f"{NISG}:par3a").url  # links to the § where the group opens
    assert nisg.by_id(f"{NISG}:par3a").parent_id == ab.id
    assert nisg.by_id(f"{NISG}:par7").parent_id == f"{NISG}:hauptstueck2.abschnitt2"
    assert nisg.by_id(f"{NISG}:par24").parent_id == f"{NISG}:hauptstueck3.abschnitt1"
    anl = nisg.by_id(f"{NISG}:anl2")
    assert (anl.kind, anl.label, anl.heading, anl.parent_id) == ("anl", "Anlage 2", "Sonstige kritische Sektoren", None)
    assert "als PDF dokumentiert" in anl.text
    kinds = [p.kind for p in nisg]
    assert kinds[:3] == ["hauptstueck", "par", "abs"] and kinds[-1] == "anl"


def test_roundtrip_json(nisg):
    again = Document.from_json(nisg.to_json())
    assert [p.id for p in again] == [p.id for p in nisg]
    assert len({p.id for p in nisg}) == len(nisg)


def test_parse_norms_act_id_from_norms():
    fetch = nisg_fetch()
    norms = fetch.client.norms("20013065")
    doc = parse_norms([(n, None if n.is_title else fetch.client.xml(n)) for n in norms[:3]], version="2026-10-01")
    assert doc.act_id == "20013065" and doc.url.endswith("&FassungVom=2026-10-01")
    assert doc.get(f"{NISG}:par3.z1.lit_a") is not None


def test_sections_sublit_and_artikel():
    f = FakeFetch()
    f.search("at_20010722_par4_search.json", Gesetzesnummer="20010722", **{
        "Fassung.FassungVom": "2026-09-30", "Abschnitt.Typ": "Paragraph", "Abschnitt.Von": "4", "Abschnitt.Bis": "4"})
    f.search("at_10000138_art49_search.json", Gesetzesnummer="10000138", **{
        "Fassung.FassungVom": TODAY, "Abschnitt.Typ": "Artikel", "Abschnitt.Von": "49", "Abschnitt.Bis": "49"})
    nisv = f.client.document("20010722", "2026-09-30", sections=["§ 4"])
    aa = nisv.by_id("at:20010722:par4.abs1.z1.lit_a.sublit_aa")
    assert (aa.kind, aa.label, aa.parent_id) == ("sublit", "aa)", "at:20010722:par4.abs1.z1.lit_a")
    bvg = f.client.document("10000138", sections=["Art. 49"])
    assert [p.id for p in bvg if p.kind == "art"] == ["at:10000138:art49"]  # Art. 49a/49b filtered out
    a = bvg.by_id("at:10000138:art49.abs1")
    assert a.text.startswith("Die Bundesgesetze sind vom Bundeskanzler im Bundesgesetzblatt kundzumachen")
    assert bvg.title.startswith("Bundes-Verfassungsgesetz (B-VG), StF: BGBl. Nr. 1/1930")
    assert bvg.eli is None  # no issue ELI on RIS for BGBl. Nr. 1/1930
    with pytest.raises(ValueError):
        f.client.document("10000138", sections=["Kapitel 3"])


# --- §§ inside Artikel (DSG: "Art. 2 § 4") ------------------------------------------

DSG = "at:10001597"


def dsg_fetch(**sections):
    f = FakeFetch()
    f.search("at_10001597_search.json", Gesetzesnummer="10001597", **{"Fassung.FassungVom": TODAY})
    for num in sections.get("pars", ()):
        f.search("at_10001597_search.json", Gesetzesnummer="10001597", **{
            "Fassung.FassungVom": TODAY, "Abschnitt.Typ": "Paragraph", "Abschnitt.Von": num, "Abschnitt.Bis": num})
    return f


def test_artikel_wrapping_pars_holds_the_structure():
    doc = dsg_fetch().client.document("10001597")
    parents = {p.id: p.parent_id for p in doc if p.kind in ("art", "par", "hauptstueck", "abschnitt")}
    assert parents == {
        f"{DSG}:art1": None,
        f"{DSG}:art1.par1": f"{DSG}:art1",
        f"{DSG}:art2": None,
        f"{DSG}:art2.hauptstueck1": f"{DSG}:art2",
        f"{DSG}:art2.hauptstueck1.abschnitt1": f"{DSG}:art2.hauptstueck1",
        f"{DSG}:art2.par4": f"{DSG}:art2.hauptstueck1.abschnitt1",
        f"{DSG}:art2.par5": f"{DSG}:art2.hauptstueck1.abschnitt1",
        f"{DSG}:art2.hauptstueck1.abschnitt2": f"{DSG}:art2.hauptstueck1",
        f"{DSG}:art2.par7": f"{DSG}:art2.hauptstueck1.abschnitt2",
    }
    assert doc.by_id(f"{DSG}:art2.hauptstueck1").heading.startswith("Durchführung der Datenschutz-Grundverordnung")
    assert doc.by_id(f"{DSG}:art2.par4").heading == "Anwendungsbereich und Durchführungsbestimmung"
    assert doc.eli is None  # Stammfassung BGBl. I Nr. 165/1999: no issue ELI on RIS
    # ID tails of CELLAR measures ("par4.abs1") find the § inside its Artikel
    assert provision_id(doc, "par4.abs1") == f"{DSG}:art2.par4.abs1"
    assert provision_id(doc, "art1.par1") == f"{DSG}:art1.par1"
    assert provision_id(doc, "par99") is None


def test_sections_come_back_in_document_order():
    f = dsg_fetch(pars=("7", "1"))
    doc = f.client.document("10001597", sections=["§ 7", "§ 1"])
    assert [p.id for p in doc if p.kind in ("art", "par")] == [
        f"{DSG}:art1", f"{DSG}:art1.par1", f"{DSG}:art2", f"{DSG}:art2.par7"]


def _ris_xml(body: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?><risdok xmlns="http://www.bka.gv.at"><nutzdaten>'
            '<abschnitt><ueberschrift typ="titel">Text</ueberschrift>' + body
            + '<ueberschrift typ="titel">Zuletzt aktualisiert am</ueberschrift></abschnitt></nutzdaten></risdok>')


def _items(tag, *texts):
    return "<liste><%s ebene=\"1\">%s</%s></liste>" % (
        tag, "".join(f"<listelem><symbol>{i}.</symbol>{t}</listelem>" for i, t in enumerate(texts, 1)), tag)


def test_second_list_in_absatz_gets_one_suffix_and_ziffernliste_items():
    from openacquis.adapters.at import Norm

    xml = _ris_xml(
        '<absatz typ="abs"><gldsym>§ 10.</gldsym> (1) Erstens:</absatz>' + _items("aufzaehlung", "a", "b")
        + '<absatz typ="satz">Zweitens:</absatz>' + _items("ziffernliste", "c", "d", "e"))
    doc = parse_norms([(Norm(nor="NOR1", gesetzesnummer="1", apa="§ 10", doc_type="Paragraph"), xml)], act_id="1")
    kids = [(p.id.rsplit(".", 1)[1], p.text) for p in doc.children("at:1:par10.abs1")]
    assert kids == [("z1", "a"), ("z2", "b"), ("z1_2", "c"), ("z2_2", "d"), ("z3_2", "e")]
    assert doc.by_id("at:1:par10.abs1").text == "Erstens:\nZweitens:"


# --- resolving references --------------------------------------------------------


def test_resolve_bgbl_picks_law_with_that_stammfassung():
    f = FakeFetch().search("at_kundm_94_2025.json", Kundmachungsorgannummer="94/2025",
                           **{"Fassung.FassungVom": TODAY})
    assert f.client.resolve("BGBl. I Nr. 94/2025").gesetzesnummer == "20013065"
    f.search("at_kundm_94_2025.json", Kundmachungsorgannummer="94/2025")  # all versions: same few hits
    with pytest.raises(LookupError, match="no consolidated federal law"):
        f.client.resolve("BGBl. II Nr. 94/2025")


def test_resolve_prefers_law_in_force_and_reports_ambiguity():
    f = FakeFetch().search("at_kundm_51_1991.json", Kundmachungsorgannummer="51/1991",
                           **{"Fassung.FassungVom": TODAY})
    assert f.client.resolve("BGBl. Nr. 51/1991").gesetzesnummer == "10005768"  # AVG, not AVG-ÜR (repealed 2007)
    r = f.client.resolve(ActRef(bgbl=BgblRef("", "51", 1991), abbreviation="AVG"))
    assert r.gesetzesnummer == "10005768" and r.abbreviation == "AVG"
    g = FakeFetch().search("at_kundm_51_1991.json", Kundmachungsorgannummer="51/1991",
                           **{"Fassung.FassungVom": "2000-01-01"})
    with pytest.raises(LookupError, match="several laws"):
        g.client.resolve("BGBl. Nr. 51/1991", "2000-01-01")  # both in force then
    assert g.client.resolve(ActRef(bgbl=BgblRef("", "51", 1991), abbreviation="AVG-ÜR 1991",
                                   version="2000-01-01")).gesetzesnummer == "10005769"


def test_resolve_falls_back_to_all_versions():
    empty = json.dumps({"OgdSearchResult": {"OgdDocumentResults": {"Hits": {"#text": "0"}}}})
    f = FakeFetch().search("at_kundm_51_1991.json", Kundmachungsorgannummer="51/1991")
    f.routes[f.client.search_url(1, Kundmachungsorgannummer="51/1991", **{"Fassung.FassungVom": TODAY})] = empty
    assert f.client.resolve("BGBl. Nr. 51/1991").gesetzesnummer == "10005768"
    assert any("FassungVom" not in u for u in f.urls)


def test_fetch_act_with_bgbl_reference():
    f = nisg_fetch().search("at_kundm_94_2025.json", Kundmachungsorgannummer="94/2025",
                            **{"Fassung.FassungVom": TODAY})
    doc = f.client.document("BGBl. I Nr. 94/2025")
    assert doc.act_id == "20013065" and doc.get(f"{NISG}:par32.abs4.lit_a") is not None


# --- CELLAR measures ----------------------------------------------------------------


def test_measure_reference_nis2_measures():
    measures = json.loads(text("at_nis2_measures.json"))
    got = [measure_reference(m) for m in measures]
    assert [(m.ref, m.abbreviation, m.provisions) for m in got] == [
        ("NISG 2026", "NISG 2026", []),
        ("BGBl. I Nr. 5/2024", "IFG", ["par6.abs1.z7"]),
        ("NISV", "NISV", []),
        ("NISG", "NISG", []),
        ("BGBl. I Nr. 100/2003", "BGBlG", ["par3.z1", "par9.abs1"]),
        ("BGBl. Nr. 51/1991", "AVG", ["par45.abs3", "par58.abs2"]),
        ("BGBl. Nr. 52/1991", "VStG", ["par9"]),
        ("BGBl. Nr. 1/1930", "B-VG", ["art49.abs1"]),
    ]
    assert [m.basis for m in got][:2] == ["abbreviation in title", "Stammfassung in title"]
    assert got[2].kind == "Verordnung"
    r = got[5].act_ref()
    assert r.bgbl == BgblRef("", "51", 1991) and r.abbreviation == "AVG"
    assert measure_reference({"title": "Verordnung über irgendetwas", "reference": None}).ref is None
    # several §§ after one "§§"
    titles = {
        "§§ 45 und 58 des Allgemeinen Verwaltungsverfahrensgesetzes 1991 – AVG; Stammfassung BGBl. Nr. 51/1991":
            ["par45", "par58"],
        "§§ 3 bis 5 des Bundesgesetzblattgesetzes (BGBlG), BGBl. I Nr. 100/2003": ["par3", "par4", "par5"],
        "§§ 45 Abs. 3, 58 Abs. 2 und 59 des AVG; Stammfassung BGBl. Nr. 51/1991": ["par45.abs3", "par58.abs2", "par59"],
    }
    for t, provisions in titles.items():
        assert measure_reference({"title": t}).provisions == provisions
    with pytest.raises(LookupError):
        MeasureRef(ref=None, basis="no citation found").act_ref()


def test_measure_document_fetches_cited_sections_only():
    f = FakeFetch().search("at_10000138_art49_search.json", Gesetzesnummer="10000138", **{
        "Fassung.FassungVom": TODAY, "Abschnitt.Typ": "Artikel", "Abschnitt.Von": "49", "Abschnitt.Bis": "49"})
    m = MeasureRef(ref="10000138", provisions=["art49.abs1"])
    doc = f.client.measure_document(m)
    assert doc.get("at:10000138:art49.abs1") is not None
    assert sum(u.endswith(".xml") for u in f.urls) == 1


# --- HTTP ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_fetcher_cache_and_user_agent(monkeypatch, tmp_path):
    calls = []

    def fake_urlopen(req, timeout, context=None):
        calls.append((req.full_url, req.get_header("User-agent"), timeout))
        return _Resp('{"ok": "ä"}'.encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    f = HttpFetcher(cache_dir=tmp_path, delay=0)
    assert f("https://example.test/a") == '{"ok": "ä"}'
    assert f("https://example.test/a") == '{"ok": "ä"}'  # served from cache
    assert len(calls) == 1 and calls[0][1].startswith("OpenAcquis/")
    assert f.requests == 1 and len(list(tmp_path.iterdir())) == 1


def test_http_fetcher_tls_error_message(monkeypatch, tmp_path):
    def fake_urlopen(req, timeout, context=None):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("openacquis.adapters.at._certifi_context", lambda: None)
    with pytest.raises(LookupError, match="HARICA"):
        HttpFetcher(cache_dir=False, delay=0)("https://data.bka.gv.at/ris/api/v2.6/Bundesrecht")


def test_default_cache_dir_env(monkeypatch, tmp_path):
    from openacquis.adapters.at import default_cache_dir

    monkeypatch.setenv("OPENACQUIS_CACHE", str(tmp_path))
    assert default_cache_dir() == tmp_path / "ris"


# --- live ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_nisg_2026(tmp_path):
    doc = fetch_act("BGBl. I Nr. 94/2025", version="2026-10-12", fetch=HttpFetcher(cache_dir=tmp_path))
    assert doc.act_id == "20013065" and "NISG 2026" in doc.title
    pars = [p for p in doc if p.kind == "par"]
    assert len(pars) >= 53
    assert doc.by_id(f"{NISG}:par32").heading == "Risikomanagementmaßnahmen im Bereich der Cybersicherheit"
    assert doc.get(f"{NISG}:par32.abs4.lit_j") is not None
    assert len(doc) > 550


@pytest.mark.live
def test_live_nis2_measures_coverage(tmp_path):
    from openacquis.cellar import CellarClient

    client = RisClient(HttpFetcher(cache_dir=tmp_path))
    measures = CellarClient().national_measures("32022L2555", "AUT")
    fetched = 0
    for m in measures:
        mr = measure_reference(m)
        for version in (None, "2026-09-30"):  # NISG 2018 and NISV were repealed on 2026-10-01
            try:
                doc = client.measure_document(mr, version)
            except LookupError:
                continue
            assert all(doc.get(f"at:{doc.act_id}:{t}") is not None for t in mr.provisions)
            fetched += 1
            break
    assert fetched == len(measures) >= 8
