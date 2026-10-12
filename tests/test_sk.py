import json
from collections import Counter
from pathlib import Path

import pytest

from openacquis.adapters.sk import (
    STATIC_URL,
    ActRef,
    HttpFetcher,
    SlovLexClient,
    _ordinal,
    fetch_act,
    html_text,
    measure_reference,
    parse_act,
    parse_history,
    parse_reference,
    select_version,
    Version,
)
from openacquis.provisions import Document

FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeFetch:
    """Serve saved Slov-Lex pages by URL; records every URL requested."""

    ROUTES = {
        f"{STATIC_URL}/static/SK/ZZ/2018/69/": "sk_69_2018_index.html",
        f"{STATIC_URL}/static/SK/ZZ/2018/69/20260430.html": "sk_69_2018_20260430.html",
        f"{STATIC_URL}/static/SK/ZZ/2021/452/": "sk_452_2021_index.html",
        f"{STATIC_URL}/static/SK/ZZ/2021/452/20260818.html": "sk_452_2021_20260818.html",
        f"{STATIC_URL}/static/SK/ZZ/2024/366/20250101.html": "sk_366_2024_20250101.html",
    }

    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if url not in self.ROUTES:
            raise LookupError(f"Slov-Lex 404 for {url}")
        return read(self.ROUTES[url])


@pytest.fixture(scope="module")
def kyber():
    fetch = FakeFetch()
    doc = SlovLexClient(fetch, today="2026-10-12").document("69/2018")
    doc.fetch = fetch
    return doc


@pytest.fixture(scope="module")
def ekom():
    return SlovLexClient(FakeFetch(), today="2026-10-12").document("452/2021 Z. z.")


@pytest.fixture(scope="module")
def novela():
    return SlovLexClient(FakeFetch()).document("366/2024", "20250101")


# --- references -------------------------------------------------------------


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("69/2018", ("69/2018", None, None)),
        (" č. 69/2018 Z. z. ", ("69/2018", None, None)),
        ("Zákon č. 69/2018 Z.z.", ("69/2018", None, None)),
        ("40/1964 Zb.", ("40/1964", None, None)),
        ("69/2018-366/2024", ("69/2018", None, "366/2024")),
        ("/SK/ZZ/2018/69/20250101", ("69/2018", "20250101", None)),
        ("https://www.slov-lex.sk/ezbierky/pravne-predpisy/SK/ZZ/2018/69/20260430#paragraf-1", ("69/2018", "20260430", None)),
        ("https://www.slov-lex.sk/pravne-predpisy/SK/ZZ/2018/69/", ("69/2018", None, None)),
        ("https://static.slov-lex.sk/static/SK/ZZ/2018/69/vyhlasene_znenie.html", ("69/2018", "vyhlasene_znenie", None)),
    ],
)
def test_parse_reference(ref, expected):
    r = parse_reference(ref)
    assert (r.act_id, r.version, r.amended_by) == expected
    assert r.iri == "/SK/ZZ/" + "/".join(reversed(r.act_id.split("/")))


def test_parse_reference_version_and_errors():
    assert parse_reference("69/2018", "0000-00-00").version == "vyhlasene_znenie"
    assert parse_reference("69/2018", "2025-03-01").version == "2025-03-01"
    assert parse_reference("40/1964").citation == "40/1964 Zb."
    assert parse_reference("69/2018").citation == "69/2018 Z. z."
    with pytest.raises(ValueError):
        parse_reference("Zákon o kybernetickej bezpečnosti")
    with pytest.raises(ValueError):
        parse_reference("69/2018", "1.1.2025")
    with pytest.raises(ValueError):
        parse_reference("69/2018", "2025-13-01")


# --- CELLAR measures ----------------------------------------------------------


def test_measure_reference_on_notified_nis2_measures():
    measures = json.loads(read("sk_nis2_measures.json"))
    mapped = [measure_reference(m["reference"], m["title"]) for m in measures]
    assert all(mapped) and len(mapped) == 9
    assert [m.ref for m in mapped][:3] == ["264/2022-203/2024", "452/2021-366/2024", "69/2018-366/2024"]
    assert {m.act_id for m in mapped} >= {"69/2018", "452/2021", "40/1964", "513/1991"}


def test_measure_reference_other_forms():
    m = measure_reference("372/2021", "Zákon č. 372/2021 Z. z., ktorým sa mení a dopĺňa zákon č. 79/2015 Z. z. o odpadoch")
    assert (m.act_id, m.amended_by, m.amends, m.ref) == ("372/2021", None, ("79/2015",), "372/2021")
    # reference missing or not a Collection of Laws number: fall back to the title
    m = measure_reference(None, "Zákon 272/2016 Z. z. o dôveryhodných službách (consolidated version as amended by zákon č. 365/2024 Z. z.)")
    assert m.ref == "272/2016-365/2024"
    assert measure_reference("MF/22164/2014-74", "Opatrenie Ministerstva financií Slovenskej republiky z 3. decembra 2014 č. MF/22164/2014-74") is None
    assert measure_reference(None, None) is None


# --- versions -----------------------------------------------------------------


def test_history_and_version_selection():
    versions = parse_history(read("sk_452_2021_index.html"))
    assert versions[0].promulgated and versions[0].key == "vyhlasene_znenie"
    last = versions[-1]
    assert (last.key, last.valid_from, last.valid_to, last.amended_by) == ("20270101", "2027-01-01", None, ("243/2026",))
    pick = lambda ref, today="2026-10-12": select_version(versions, parse_reference(ref), today).key  # noqa: E731
    assert pick("452/2021") == "20260818"  # in force today, not the future 2027 version
    assert pick("452/2021", "2030-01-01") == "20270101"
    assert pick("452/2021-366/2024") == "20250101"
    with pytest.raises(LookupError):
        pick("452/2021-999/2030")
    with pytest.raises(LookupError):
        select_version(versions, ActRef(2021, "452", "2000-01-01"), "2026-10-12")
    k = parse_history(read("sk_69_2018_index.html"))
    assert select_version(k, parse_reference("69/2018", "0000-00-00"), "2026-10-12").promulgated
    assert select_version(k, parse_reference("69/2018", "2025-06-30"), "2026-10-12").amended_by == ("366/2024",)


def test_amending_act_in_stages_selects_latest_started_version():
    # 575/2001 as amended by 201/2024: that act produced three versions (live history, 2026-10-12)
    versions = [
        Version("/SK/ZZ/2001/575/20240801", "2024-08-01", "2024-08-31", ("150/2024",)),
        Version("/SK/ZZ/2001/575/20240901", "2024-09-01", "2024-09-30", ("201/2024",)),
        Version("/SK/ZZ/2001/575/20241001", "2024-10-01", "2024-12-31", ("201/2024",)),
        Version("/SK/ZZ/2001/575/20250101", "2025-01-01", "2025-06-30", ("201/2024", "278/2024")),
        Version("/SK/ZZ/2001/575/20250701", "2025-07-01", None, ("90/2025",)),
    ]
    pick = lambda today: select_version(versions, parse_reference("575/2001-201/2024"), today).key  # noqa: E731
    assert pick("2026-10-12") == "20250101"  # every stage of 201/2024, not only the first
    assert pick("2024-10-15") == "20241001"  # stages not yet in force are left out
    assert pick("2024-01-01") == "20240901"  # none started yet: the first one


def test_client_requests(kyber):
    assert kyber.fetch.urls == [
        f"{STATIC_URL}/static/SK/ZZ/2018/69/",
        f"{STATIC_URL}/static/SK/ZZ/2018/69/20260430.html",
    ]
    # an exact version id needs no history page
    fetch = FakeFetch()
    SlovLexClient(fetch).document("/SK/ZZ/2018/69/20260430")
    assert fetch.urls == [f"{STATIC_URL}/static/SK/ZZ/2018/69/20260430.html"]
    with pytest.raises(LookupError):
        SlovLexClient(FakeFetch()).document("1/2099")


# --- 69/2018 ------------------------------------------------------------------


def test_document_metadata(kyber):
    assert (kyber.source, kyber.act_id, kyber.language) == ("sk", "69/2018", "sk")
    assert kyber.title == "Zákon č. 69/2018 Z. z. o kybernetickej bezpečnosti a o zmene a doplnení niektorých zákonov"
    assert kyber.date == "2026-04-30"
    assert kyber.eli == "https://www.slov-lex.sk/pravne-predpisy/SK/ZZ/2018/69/20260430"
    assert kyber.url == "https://www.slov-lex.sk/ezbierky/pravne-predpisy/SK/ZZ/2018/69/20260430"


def test_ids_hierarchy_and_text(kyber):
    p = kyber.by_id("sk:69/2018:par2.ods2.pism_a.bod1")
    assert (p.kind, p.label, p.number) == ("bod", "1.", "1")
    assert p.parent_id == "sk:69/2018:par2.ods2.pism_a"
    assert p.text.startswith("najčastejšie prijíma rozhodnutia")
    assert p.url == f"{STATIC_URL}/static/SK/ZZ/2018/69/20260430.html#paragraf-2.odsek-2.pismeno-a.bod-1"
    assert p.eli is None
    # source line breaks are spaces
    assert "službu cloud computingu" in kyber.by_id("sk:69/2018:par2.ods2").text
    # unnumbered odsek of § 1 is folded: its letters hang off the §
    assert kyber.by_id("sk:69/2018:par1").text == "Tento zákon upravuje"
    assert kyber.by_id("sk:69/2018:par1.pism_a").parent_id == "sk:69/2018:par1"
    assert kyber.get("sk:69/2018:par1.ods1") is None
    kinds = Counter(p.kind for p in kyber)
    assert kinds == {"cl": 2, "par": 4, "ods": 11, "pism": 21, "bod": 17, "priloha": 2, "pozn": 3}


def test_headings_and_clanok(kyber):
    assert kyber.by_id("sk:69/2018:par1").heading == "Predmet zákona"
    # § without own heading takes the group heading printed above it
    assert kyber.by_id("sk:69/2018:par21").heading == "Osobitné povinnosti"
    assert kyber.by_id("sk:69/2018:par29c").heading == "Kontrola"
    # §§ in group headings that the markup puts beside Čl. I still belong to Čl. I
    assert {kyber.by_id(f"sk:69/2018:par{n}").parent_id for n in ("1", "2", "21", "29c")} == {"sk:69/2018:cl1"}
    cl2 = kyber.by_id("sk:69/2018:cl2")
    assert (cl2.label, cl2.number, cl2.parent_id) == ("Čl. II", "2", None)
    assert cl2.text.startswith("Zákon Národnej rady Slovenskej republiky č. 198/1994 Z. z. o Vojenskom spravodajstve")


def _par(n, heading=""):
    nadpis = f'<div class="paragrafNadpis">{heading}</div>' if heading else ""
    return (f'<div class="paragraf Skupina " id="paragraf-{n}"><div class="paragrafOznacenie">§ {n}</div>{nadpis}'
            f'<div class="odsek Skupina " id="paragraf-{n}.odsek-1"><div class="odsekOznacenie"></div>'
            f'<div class="text">Text {n}.</div></div></div>')


def _group(slug, label, body, kind="skupinaParagrafov"):
    return (f'<div class="{kind} Skupina " id="predpis.{kind}-{slug}">'
            f'<div class="{kind}Oznacenie">{label}</div>{body}</div>')


def test_group_heading_applies_to_every_par_without_own_heading():
    # as on Slov-Lex 69/2018: "Kontrola" over §§ 29c-29h, "Osobitné povinnosti" wrapping §§ 21-29b
    # with a nested "Blokovanie" group and §§ that have their own headings
    body = (
        _group("osobitne", "Osobitné povinnosti",
               _par("21") + _par("22") + _par("24", "Hlásenia")
               + _group("blokovanie", "Blokovanie", _par("27b") + _par("27c")) + _par("28"),
               kind="skupinaElementov")
        + _group("kontrola", "Kontrola", _par("29c") + _par("29d") + _par("29h"))
        + _par("30", "Priestupky") + _par("31")
    )
    html = f'<html><body><h1>1/2099 Z. z.</h1><div id="predpis">{body}</div></body></html>'
    doc = parse_act(html)
    heads = {p.number: p.heading for p in doc if p.kind == "par"}
    assert heads == {
        "21": "Osobitné povinnosti", "22": "Osobitné povinnosti", "24": "Hlásenia",
        "27b": "Blokovanie", "27c": "Blokovanie", "28": "Osobitné povinnosti",
        "29c": "Kontrola", "29d": "Kontrola", "29h": "Kontrola",
        "30": "Priestupky", "31": None,  # outside any group
    }


def test_quoted_text_and_footnotes(kyber):
    bod = kyber.by_id("sk:69/2018:cl2.bod1")
    lines = bod.text.split("\n")
    assert lines[0] == "V § 2 ods. 1 sa za písmeno g) vkladá nové písmeno h), ktoré znie:"
    assert lines[1] == "„h) aktivity a ohrozenia v kybernetickom priestore,1ba)“."
    assert "Doterajšie písmená h) až j) sa označujú ako písmená i) až k)." in lines
    assert not [p for p in kyber if p.id.startswith("sk:69/2018:cl2.bod1.")]  # quoted letter is text
    # footnote markers are dropped from the act's own text; footnotes are provisions
    assert "podľa § 33 ods. 5." in kyber.by_id("sk:69/2018:par2.ods1").text
    f = kyber.by_id("sk:69/2018:pozn10a")
    assert (f.label, f.number) == ("10a)", "10a")
    assert f.text.startswith("Napríklad § 6 ods. 2 písm. k)")


def test_annexes(kyber):
    a1 = kyber.by_id("sk:69/2018:priloha1")
    assert a1.label == "Príloha č. 1 k zákonu č. 69/2018 Z. z."
    assert a1.text.endswith(".pdf") and "PDF: https://static.slov-lex.sk/pdf/prilohy/SK/ZZ/2018/69/" in a1.text
    a3 = kyber.by_id("sk:69/2018:priloha3")
    assert a3.text.startswith("ZOZNAM PREBERANÝCH PRÁVNE ZÁVÄZNÝCH AKTOV EURÓPSKEJ ÚNIE\nSmernica Európskeho parlamentu a Rady (EÚ) 2022/2555")


def test_roundtrip(kyber):
    again = Document.from_json(kyber.to_json())
    assert [p.id for p in again] == [p.id for p in kyber]
    assert "(1) Úrad je oprávnený" in kyber.full_text("sk:69/2018:par29c")


# --- 452/2021: parts, chapters, § with letters only ------------------------------------


def test_structure(ekom):
    assert ekom.date == "2026-08-18"
    cast2 = ekom.by_id("sk:452/2021:cast2")
    assert (cast2.label, cast2.heading, cast2.parent_id) == ("Druhá časť", "Regulácia elektronických komunikácií", None)
    h = ekom.by_id("sk:452/2021:cast2.hlava2")
    assert (h.kind, h.number, h.parent_id) == ("hlava", "2", "sk:452/2021:cast2")
    assert ekom.by_id("sk:452/2021:par15").parent_id == "sk:452/2021:cast2.hlava2"
    assert ekom.by_id("sk:452/2021:cast1").heading == "ZÁKLADNÉ USTANOVENIA"
    p3 = ekom.by_id("sk:452/2021:par3")
    assert p3.text == "Na účely tohto zákona sa rozumie:"
    assert ekom.by_id("sk:452/2021:par3.pism_j").parent_id == "sk:452/2021:par3"
    assert "POPIS PROJEKTU" in ekom.by_id("sk:452/2021:priloha6").text


# --- 366/2024: amending act ----------------------------------------------------------


def test_amending_act(novela):
    assert novela.title.startswith("Zákon č. 366/2024 Z. z. ktorým sa mení a dopĺňa zákon č. 69/2018 Z. z.")
    assert [p.id for p in novela] == [
        "sk:366/2024:cl1", "sk:366/2024:cl1.bod5", "sk:366/2024:cl1.bod25", "sk:366/2024:cl2", "sk:366/2024:cl2.bod1",
        "sk:366/2024:cl3",
    ]
    # Čl. II is nested in Čl. I in the markup; it is still a top-level článok
    assert novela.by_id("sk:366/2024:cl2").parent_id is None
    # a § quoted by an amending point stays text of the point, not a provision of 366/2024
    b25 = novela.by_id("sk:366/2024:cl1.bod25")
    assert b25.text.startswith("§ 5a vrátane nadpisu znie:\n„§ 5a Systém certifikácie kybernetickej bezpečnosti")
    assert not [p for p in novela if p.kind == "par"]
    # a footnote quoted by an amending point without a div.citat wrapper (Čl. III) is text of
    # the point, with its marker, not a footnote of 366/2024
    assert not [p for p in novela if p.kind == "pozn"]
    cl3 = novela.by_id("sk:366/2024:cl3")
    assert cl3.text.endswith(
        "Poznámka pod čiarou k odkazu 27g znie:\n„27g) § 52a zákona č. 452/2021 Z. z. v znení zákona č. 366/2024 Z. z.“.")


# --- helpers ------------------------------------------------------------------------------


def test_ordinals_and_html_text():
    assert [_ordinal(w) for w in ("prva", "Druhá", "dvadsiata_prva", "Ôsmy", "IV", "xyz")] == [1, 2, 21, 8, 4, None]
    assert html_text('Text<a href="#"><sup>2</sup>)</a> a&nbsp;ďalej <br/>riadok') == "Text a ďalej\nriadok"


def test_parse_act_reads_act_id_and_errors():
    doc = parse_act(read("sk_366_2024_20250101.html"))
    assert doc.act_id == "366/2024" and doc.url is None
    assert doc.by_id("sk:366/2024:cl1.bod5").url is None
    with pytest.raises(LookupError):
        parse_act("<html><h1>1/2020 Z. z.</h1></html>")


def test_http_fetcher_cache(tmp_path, monkeypatch):
    calls = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        calls.append((req.full_url, req.get_header("User-agent")))
        return Resp("<h1>69/2018 Z. z.</h1>".encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    f = HttpFetcher(cache_dir=tmp_path, delay=0)
    assert f("https://static.slov-lex.sk/x") == "<h1>69/2018 Z. z.</h1>"
    assert f("https://static.slov-lex.sk/x") == "<h1>69/2018 Z. z.</h1>"
    assert len(calls) == 1 and calls[0][1].startswith("OpenAcquis/")
    assert f.requests == 1 and len(list(tmp_path.iterdir())) == 1


# --- live ----------------------------------------------------------------------------------


@pytest.mark.live
def test_live_cybersecurity_act():
    doc = fetch_act("69/2018")
    assert doc.act_id == "69/2018" and doc.title.startswith("Zákon č. 69/2018 Z. z.")
    assert len(doc) > 500
    assert doc.by_id("sk:69/2018:par1").heading == "Predmet zákona"
    assert "2022/2555" in "\n".join(p.text for p in doc if p.kind == "priloha")
    notified = fetch_act("69/2018-366/2024")
    assert notified.date == "2025-01-01"
