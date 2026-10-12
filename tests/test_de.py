import io
import json
import urllib.parse
import zipfile
from pathlib import Path

import pytest

from openacquis.adapters.de import (
    BASE_URL,
    NEURIS_API,
    TOC_URL,
    BgblRef,
    GiiClient,
    HttpFetcher,
    fetch_act,
    german_ordinal,
    item_unit,
    land_of,
    map_measure,
    map_measures,
    measure_bgbl,
    norm_unit,
    parse_bgbl,
    parse_gii_xml,
    parse_reference,
    slugify,
    structure_unit,
)
from openacquis.cellar import CellarClient, NationalMeasure

FIXTURES = Path(__file__).parent / "fixtures"
BSIG_XML = (FIXTURES / "de_bsig_2025.xml").read_bytes()
EXPR = "eli/bund/bgbl-1/2025/301-2/2026-07-29/1/deu"


def load(name):
    return (FIXTURES / name).read_bytes()


def zipped(xml: bytes, name="BJNR12D0B0025.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, xml)
        zf.writestr("image.jpg", b"\xff\xd8")
    return buf.getvalue()


def search(**params):
    return f"{NEURIS_API}?{urllib.parse.urlencode({**params, 'size': '50'})}"


EMPTY = json.dumps({"totalItems": 0, "member": []}).encode()


class FakeFetch:
    """Serve saved GII / NeuRIS responses by URL; unknown URLs are 404s. Records every URL."""

    def __init__(self, neuris=True):
        self.urls = []
        self.routes = {f"{BASE_URL}/bsig_2025/xml.zip": zipped(BSIG_XML)}
        if neuris:
            hit = load("de_neuris_search_bsig.json")
            self.routes.update({
                search(risAbbreviation="BSIG 2025"): hit,
                search(abbreviation="BSIG"): hit,
                search(eli="eli/bund/bgbl-1/2025/301"): hit,
                search(eli="eli/bund/bgbl-1/2025/301-2"): hit,
                search(eli="eli/bund/bgbl-1/2024/52"): load("de_neuris_search_2024_52.json"),
                f"{NEURIS_API}/{EXPR}": load("de_neuris_expression_bsig.json"),
            })

    def __call__(self, url):
        self.urls.append(url)
        if url in self.routes:
            return self.routes[url]
        if url.startswith(NEURIS_API + "?"):
            return EMPTY
        raise LookupError(f"404 for {url}")


@pytest.fixture(scope="module")
def bsig():
    fetch = FakeFetch()
    doc = GiiClient(fetch).document("bsig_2025")
    doc.fetch = fetch
    return doc


# --- references -------------------------------------------------------------


@pytest.mark.parametrize(
    "ref, slug, abbreviation",
    [
        ("bsig_2025", "bsig_2025", "bsig_2025"),
        ("BSIG", "bsig", "BSIG"),
        ("BSIG 2025", "bsig_2025", "BSIG 2025"),
        ("BSI-KritisV", "bsi-kritisv", "BSI-KritisV"),
        ("https://www.gesetze-im-internet.de/bsig_2025/__30.html", "bsig_2025", None),
        ("http://www.gesetze-im-internet.de/bsig_2025/xml.zip", "bsig_2025", None),
    ],
)
def test_parse_reference_slug(ref, slug, abbreviation):
    r = parse_reference(ref)
    assert (r.slug, r.abbreviation) == (slug, abbreviation)


def test_parse_reference_eli_and_bgbl():
    r = parse_reference("https://testphase.rechtsinformationen.bund.de/v1/legislation/eli/bund/bgbl-1/2025/301-2/2026-07-29/1/deu")
    assert r.eli == "eli/bund/bgbl-1/2025/301-2" and r.slug is None
    assert parse_reference("BGBl. 2025 I Nr. 301").bgbl == BgblRef(2025, 1, "301")
    with pytest.raises(ValueError):
        parse_reference("§ 30 ???")
    with pytest.raises(ValueError):
        parse_reference("")


@pytest.mark.parametrize(
    "cite, eli",
    [
        ("BGBl. 2025 I Nr. 301", "eli/bund/bgbl-1/2025/301"),
        ("BGBl. I 2025 Nr. 301", "eli/bund/bgbl-1/2025/301"),
        ("BGBl. I Nr. 301/2025", "eli/bund/bgbl-1/2025/301"),
        ("BGBl. I 2009 S. 2821", "eli/bund/bgbl-1/2009/s2821"),
        ("BGBl. II 2010 S. 5", "eli/bund/bgbl-2/2010/s5"),
        ("Ersetzt G 206-2 v. 14.8.2009 I 2821", "eli/bund/bgbl-1/2009/s2821"),
        ("Art. 1 G v. 2.12.2025 I Nr. 301", "eli/bund/bgbl-1/2025/301"),
    ],
)
def test_parse_bgbl(cite, eli):
    assert parse_bgbl(cite).work_eli == eli


def test_parse_bgbl_needs_year():
    with pytest.raises(ValueError, match="year"):
        parse_bgbl("BGBl. I S. 2821")
    assert str(BgblRef(2025, 1, "301")) == "BGBl. 2025 I Nr. 301"


def test_slugify():
    assert slugify("BSIG 2025") == "bsig_2025"
    assert slugify("1-DM-GoldMünzG") == "1-dm-goldm_nzg"
    assert slugify("BImSchV 1 2010") == "bimschv_1_2010"


# --- numbering ----------------------------------------------------------------


@pytest.mark.parametrize(
    "label, unit",
    [
        ("1.", ("nr", "1")), ("2a.", ("nr", "2a")), ("1.1", ("nr", "1_1")), ("(3)", ("nr", "3")),
        ("a)", ("buchst", "a")), ("A.", ("buchst", "a")), ("aa)", ("dbuchst", "aa")),
        ("-", ("strich", None)), ("", ("strich", None)), ("RD", ("dt", "rd")),
    ],
)
def test_item_unit(label, unit):
    assert item_unit(label) == unit


@pytest.mark.parametrize(
    "enbez, unit",
    [
        ("§ 30", ("par", "30")), ("§ 5a", ("par", "5a")), ("Art 12a", ("art", "12a")),
        ("(XXXX) §§ 3 bis 6", ("par", "3_bis_6")), ("Anlage 1", ("anlage", "1")),
        ("Anlage (zu § 118)", ("anlage", "0")), ("Anhang EV", ("anhang", "ev")),
        ("Präambel", ("praeambel", "1")), ("Inhaltsübersicht", None), ("Eingangsformel", None),
    ],
)
def test_norm_unit(enbez, unit):
    assert norm_unit(enbez) == unit


@pytest.mark.parametrize(
    "bez, unit",
    [
        ("Teil 2", ("teil", "2")), ("Kapitel 3a", ("kapitel", "3a")), ("Dritter Abschnitt", ("abschnitt", "3")),
        ("Einundzwanzigster Abschnitt", ("abschnitt", "21")), ("Allgemeiner Teil", ("teil", "4")),
        ("IV a.", ("gliederung", "iva")), ("Freiheitsstrafe", ("gliederung", "4")), ("", ("gliederung", "4")),
    ],
)
def test_structure_unit(bez, unit):
    assert structure_unit(bez, 4) == unit


def test_german_ordinal():
    assert [german_ordinal(w) for w in ("Erster", "Siebenter", "Dreizehnter", "Dreißigster", "Erstes", "Allgemeiner")] == [
        1, 7, 13, 30, 1, None,
    ]


# --- BSIG (trimmed real XML) --------------------------------------------------------


def test_document_metadata(bsig):
    assert bsig.source == "de" and bsig.act_id == "bsig_2025" and bsig.language == "de"
    assert bsig.title.startswith("Gesetz über das Bundesamt für Sicherheit") and bsig.title.endswith("(BSIG)")
    assert bsig.date == "2026-07-29"  # NeuRIS expression in force from
    assert bsig.eli == EXPR
    assert bsig.url == "https://www.gesetze-im-internet.de/bsig_2025/index.html"


def test_ids_and_hierarchy(bsig):
    p = bsig.by_id("de:bsig_2025:par28.abs1.nr3.buchst_a")
    assert (p.kind, p.label, p.number) == ("buchst", "a)", "a")
    assert p.text == "mindestens 50 Mitarbeiter beschäftigen oder"
    assert p.parent_id == "de:bsig_2025:par28.abs1.nr3"
    assert bsig.by_id("de:bsig_2025:par28.abs1").parent_id == "de:bsig_2025:par28"
    # § IDs ignore Teil/Kapitel, but the § hangs off its Kapitel
    assert bsig.by_id("de:bsig_2025:par30").parent_id == "de:bsig_2025:teil3.kapitel2"
    k = bsig.by_id("de:bsig_2025:teil2.kapitel1")
    assert (k.kind, k.label, k.heading, k.parent_id) == ("kapitel", "Kapitel 1", "Aufgaben und Befugnisse", "de:bsig_2025:teil2")
    for p in bsig:
        if p.parent_id and p.kind not in ("par", "art", "kapitel"):
            assert p.id.startswith(p.parent_id + ".")


def test_section_heading_and_text(bsig):
    p = bsig.by_id("de:bsig_2025:par30")
    assert (p.label, p.heading, p.text) == ("§ 30", "Risikomanagementmaßnahmen besonders wichtiger Einrichtungen und wichtiger Einrichtungen", "")
    abs1 = bsig.by_id("de:bsig_2025:par30.abs1")
    assert abs1.label == "(1)" and abs1.text.startswith("Besonders wichtige Einrichtungen und wichtige Einrichtungen sind verpflichtet")
    assert "(1)" not in abs1.text
    assert [c.id.rsplit(".", 1)[1] for c in bsig.children("de:bsig_2025:par30.abs2")] == [f"nr{i}" for i in range(1, 11)]
    # § without Absätze: the text is the §'s own; § 2 Nr. 2 has letters a) ...
    assert bsig.by_id("de:bsig_2025:par1").text.startswith("Das Bundesamt für Sicherheit")
    assert bsig.by_id("de:bsig_2025:par2.nr2.buchst_e").text.startswith("die Verfassungsschutzbehörden")


def test_text_after_list_stays_with_paragraph(bsig):
    abs1 = bsig.by_id("de:bsig_2025:par32.abs1")
    assert abs1.text.endswith("Die Verpflichtung nach Satz 1 gilt frühestens ab Einrichtung des Meldewegs.")
    assert "24 Stunden" not in abs1.text  # list items are children, not text
    assert "24 Stunden" in bsig.by_id("de:bsig_2025:par32.abs1.nr1").text
    assert "Abschlussmeldung" in bsig.full_text("de:bsig_2025:par32.abs1")


def test_second_list_in_paragraph_gets_suffix(bsig):
    kids = [c.id.rsplit(".", 1)[1] for c in bsig.children("de:bsig_2025:par8.abs4")]
    assert kids[:2] == ["nr1", "nr2"] and "nr1_2" in kids


def test_later_list_in_same_paragraph_is_suffixed_as_a_whole():
    xml = SYNTH.replace(
        "<P>(1a) Eingefügt.</P>",
        "<P>(1a) Benennen: <DL><DT>1.</DT><DD><LA>eins</LA></DD><DT>2.</DT><DD><LA>zwei</LA></DD></DL></P>"
        "<P>Verantwortlich zu sein: <DL><DT>1.</DT><DD><LA>A</LA></DD><DT>2.</DT><DD><LA>B</LA></DD>"
        "<DT>3.</DT><DD><LA>C</LA></DD></DL></P>")
    doc = parse_gii_xml(xml.encode(), "testg")
    kids = [(c.id.rsplit(".", 1)[1], c.text) for c in doc.children("de:testg:par1.abs1a")]
    # Satz 2 Nr. 3 is nr3_2, not nr3: every item of the second list carries the suffix
    assert kids == [("nr1", "eins"), ("nr2", "zwei"), ("nr1_2", "A"), ("nr2_2", "B"), ("nr3_2", "C")]


def test_annex_table(bsig):
    a = bsig.by_id("de:bsig_2025:anlage1")
    assert (a.kind, a.label, a.heading, a.parent_id) == ("anlage", "Anlage 1", "Sektoren besonders wichtiger und wichtiger Einrichtungen", None)
    assert "Nummer | Sektor | Branche | Einrichtungsart" in a.text
    assert "1.1 | | Stromversorgung" in a.text
    assert "Fundstelle" not in a.text  # editorial comment dropped
    assert a.url == "https://www.gesetze-im-internet.de/bsig_2025/anlage_1.html"


def test_skips_table_of_contents(bsig):
    assert not any("Inhalts" in p.label for p in bsig)


def test_eli_and_urls(bsig):
    assert bsig.by_id("de:bsig_2025:par30").eli == EXPR + "#art-z30"
    assert bsig.by_id("de:bsig_2025:par30.abs1").eli == EXPR + "#art-z30_abs-z1"
    assert bsig.by_id("de:bsig_2025:par30.abs2.nr1").eli is None
    assert bsig.by_id("de:bsig_2025:par30.abs2.nr1").url == "https://www.gesetze-im-internet.de/bsig_2025/__30.html"
    assert bsig.by_id("de:bsig_2025:teil1").url.endswith("BJNR12D0B0025.html#BJNR12D0B0025BJNG000100000")


def test_counts(bsig):
    from collections import Counter

    kinds = Counter(p.kind for p in bsig)
    assert kinds["par"] == 6 and kinds["anlage"] == 1 and kinds["teil"] == 3 and kinds["kapitel"] == 3
    assert len({p.id for p in bsig}) == len(bsig)


def test_without_neuris():
    fetch = FakeFetch(neuris=False)
    doc = GiiClient(fetch, neuris=False).document("bsig_2025")
    assert doc.eli is None and doc.date is None
    assert doc.by_id("de:bsig_2025:par30").eli is None
    assert fetch.urls == [f"{BASE_URL}/bsig_2025/xml.zip"]


def test_neuris_failure_is_not_fatal():
    fetch = FakeFetch(neuris=False)  # NeuRIS answers nothing: search empty, expression 404
    doc = GiiClient(fetch).document("bsig_2025")
    assert doc.eli is None and len(doc) > 50


def test_roundtrip_json(bsig):
    from openacquis.provisions import Document

    again = Document.from_json(bsig.to_json())
    assert [p.to_dict() for p in again] == [p.to_dict() for p in bsig]


# --- resolution --------------------------------------------------------------------


def test_resolve_abbreviation_via_neuris():
    fetch = FakeFetch()
    c = GiiClient(fetch)
    assert c.resolve("BSIG") == "bsig_2025"  # bsig/xml.zip is a 404, NeuRIS knows "BSIG 2025"
    assert f"{BASE_URL}/bsig/xml.zip" in fetch.urls


def test_resolve_bgbl_and_eli():
    c = GiiClient(FakeFetch())
    assert c.resolve("BGBl. 2025 I Nr. 301") == "bsig_2025"
    assert c.resolve("eli/bund/bgbl-1/2025/301-2") == "bsig_2025"
    assert fetch_act("BGBl. 2025 I Nr. 301", fetch=FakeFetch()).act_id == "bsig_2025"


def test_resolve_errors():
    c = GiiClient(FakeFetch())
    with pytest.raises(LookupError, match="abbreviation"):
        c.resolve("XYZG")
    with pytest.raises(LookupError, match="NeuRIS"):
        c.resolve("BGBl. 2025 I Nr. 999")
    with pytest.raises(LookupError):
        GiiClient(FakeFetch(), neuris=False).resolve("BSIG")
    with pytest.raises(ValueError, match="current"):
        c.document("bsig_2025", "2025-12-06")


def test_bad_zip():
    with pytest.raises(LookupError, match="zip"):
        GiiClient(lambda url: b"<html>not found</html>").xml("bsig_2025")


def test_toc():
    toc = b"""<?xml version="1.0" encoding="UTF-8" ?><items>
<item><title>Gesetz \xc3\xbcber das BSI</title><link>http://www.gesetze-im-internet.de/bsig_2025/xml.zip</link></item>
<item><title>BGB</title><link>http://www.gesetze-im-internet.de/bgb/xml.zip</link></item></items>"""
    assert GiiClient({TOC_URL: toc}.__getitem__).toc() == [("Gesetz über das BSI", "bsig_2025"), ("BGB", "bgb")]


# --- edge cases on a synthetic file ----------------------------------------------------

SYNTH = """<?xml version="1.0" encoding="UTF-8" ?>
<dokumente doknr="BJNR0000"><norm doknr="BJNR0000"><metadaten><jurabk>TestG</jurabk><langue>Testgesetz</langue>
<fundstelle><periodikum>BGBl. I</periodikum><zitstelle>2024, Nr. 5</zitstelle></fundstelle></metadaten></norm>
<norm doknr="BJNR0000E1"><metadaten><enbez>Präambel</enbez></metadaten><textdaten><text><Content><P>Im Bewusstsein<BR/>der Verantwortung.</P></Content></text></textdaten></norm>
<norm doknr="BJNR0000G1"><metadaten><gliederungseinheit><gliederungskennzahl>010</gliederungskennzahl><gliederungsbez>Erster Abschnitt</gliederungsbez><gliederungstitel>Allgemeines</gliederungstitel></gliederungseinheit></metadaten></norm>
<norm doknr="BJNR0000E2"><metadaten><enbez>§ 1</enbez><titel>Zweck</titel></metadaten><textdaten><text><Content>
<P>(1) Satz<FnR ID="F1"/> eins: <DL Type="arabic"><DT>1.</DT><DD><LA>erstens mit <DL Type="alpha"><DT>a)</DT><DD><LA>Buchstabe <DL Type="a-alpha"><DT>aa)</DT><DD><LA>Doppel</LA></DD></DL></LA></DD></DL>und danach</LA></DD><DT>-</DT><DD><LA>Strich</LA></DD><DT>-</DT><DD><LA>Strich zwei</LA></DD></DL></P>
<P>Fortsetzung von Absatz 1.</P><P>(1a) Eingefügt.</P></Content><Footnotes><Footnote ID="F1">Fußnote</Footnote></Footnotes></text></textdaten></norm>
<norm doknr="BJNR0000E3"><metadaten><enbez>(XXXX) §§ 2 bis 4</enbez><titel>(weggefallen)</titel></metadaten><textdaten><text><Content><P/></Content></text></textdaten></norm>
<norm doknr="BJNR0000G2"><metadaten><gliederungseinheit><gliederungskennzahl>020</gliederungskennzahl><gliederungsbez>Zweiter Abschnitt</gliederungsbez></gliederungseinheit></metadaten></norm>
<norm doknr="BJNR0000E4"><metadaten><enbez>§ 1</enbez></metadaten><textdaten><text><Content><P>Gleiche Nummer.</P></Content></text></textdaten></norm>
<norm doknr="BJNR0000E5"><metadaten><enbez>Art 5a</enbez></metadaten><textdaten><text><Content><P>(1) Artikel.</P></Content></text></textdaten></norm>
<norm doknr="BJNR0000E6"><metadaten><titel>Anlage (zu § 1)</titel></metadaten><textdaten><text><Content><P><table><tgroup><tbody><row><entry>x</entry><entry>y</entry><entry/></row></tbody></tgroup></table></P></Content></text></textdaten></norm>
</dokumente>"""


@pytest.fixture(scope="module")
def synth():
    return parse_gii_xml(SYNTH.encode(), "testg")


def test_synthetic_structure(synth):
    ids = [p.id for p in synth]
    assert ids == [
        "de:testg:praeambel1",
        "de:testg:abschnitt1", "de:testg:par1", "de:testg:par1.abs1", "de:testg:par1.abs1.nr1",
        "de:testg:par1.abs1.nr1.buchst_a", "de:testg:par1.abs1.nr1.buchst_a.dbuchst_aa",
        "de:testg:par1.abs1.strich1", "de:testg:par1.abs1.strich2", "de:testg:par1.abs1a",
        "de:testg:par2_bis_4", "de:testg:abschnitt2", "de:testg:abschnitt2.par1", "de:testg:art5a",
        "de:testg:art5a.abs1", "de:testg:anlage0",
    ]


def test_synthetic_texts(synth):
    assert synth.by_id("de:testg:praeambel1").text == "Im Bewusstsein\nder Verantwortung."
    assert synth.by_id("de:testg:par1.abs1").text == "Satz eins:\nFortsetzung von Absatz 1."  # footnote marker dropped
    assert synth.by_id("de:testg:par1.abs1.nr1").text == "erstens mit\nund danach"
    assert synth.by_id("de:testg:par1.abs1.strich2").label == "-"
    assert synth.by_id("de:testg:par1.abs1a").label == "(1a)"
    gone = synth.by_id("de:testg:par2_bis_4")
    assert (gone.label, gone.heading, gone.text) == ("§§ 2 bis 4", "(weggefallen)", "")
    assert synth.by_id("de:testg:abschnitt1").heading == "Allgemeines"
    assert synth.by_id("de:testg:anlage0").text == "x | y"
    assert synth.by_id("de:testg:art5a").url == f"{BASE_URL}/testg/art_5a.html"
    assert synth.by_id("de:testg:par2_bis_4").url == f"{BASE_URL}/testg/BJNR0000.html#BJNR0000E3"
    assert "Fußnote" not in synth.full_text("de:testg:par1")


def test_not_gii_xml():
    with pytest.raises(ValueError):
        parse_gii_xml(b"<dokumente/>", "x")


# --- CELLAR measures ------------------------------------------------------------------


def cellar_client():
    rec = json.loads(load("de_cellar_nis2.json"))
    return CellarClient(lambda q: rec["numbers"] if "number_official_journal" in q else rec["measures"])


def test_map_measures_nis2():
    out = {m.title[:30]: m for m in map_measures("32022L2555", cellar=cellar_client(), client=GiiClient(FakeFetch()))}
    fed = next(m for m in out.values() if m.title.startswith("Gesetz zur Umsetzung der NIS-2"))
    assert (fed.status, fed.refs, fed.bgbl, fed.elis) == ("mapped", ["bsig_2025"], "BGBl. 2025 I Nr. 301", ["eli/bund/bgbl-1/2025/301-2"])
    statuses = sorted(m.status for m in out.values())
    assert statuses == ["land", "land", "land", "mapped", "not_a_law", "not_found"]
    # BGBl. 2024 I Nr. 52 exists, but was published in February, not on the measure's date
    runderlass = next(m for m in out.values() if m.title.startswith("Gemeinsamer Runderlass"))
    assert runderlass.status == "not_found" and runderlass.refs == []


def test_map_measure_without_number():
    m = NationalMeasure("u", "DEU", None, "Bundesgesetz X", "Gesetz", None, "2025-01-01", None, None)
    assert map_measure(m, GiiClient(FakeFetch())).status == "no_reference"


def test_measure_bgbl_and_land():
    assert str(measure_bgbl("2025-12-05", "301", "00001-00068")) == "BGBl. 2025 I Nr. 301"
    assert measure_bgbl("2009-07-30", "13", "00397-00428").work_eli == "eli/bund/bgbl-1/2009/s397"
    assert measure_bgbl("2025-06-24", "2016, 282", None) is None
    assert land_of("Gesetz zur Änderung des Bayerischen Digitalgesetzes") == "Bayerisch"
    assert land_of("Verordnung zur Umsetzung der NIS-2-Richtlinie im Land Brandenburg") == "Brandenburg"
    assert land_of("Gesetz über das Bundesamt für Sicherheit") is None
    # Land adjectives in titles of Land laws
    for title, land in [
        ("Hamburgisches Informationssicherheitsgesetz", "Hamburg"),
        ("Berliner Informationssicherheitsgesetz", "Berlin"),
        ("Brandenburgisches Informationssicherheitsgesetz", "Brandenburg"),
        ("Schleswig-Holsteinisches Informationssicherheitsgesetz", "Schleswig-Holstein"),
        ("Rheinland-Pfälzisches Informationssicherheitsgesetz", "Rheinland-Pfälzisch"),
        ("Baden-Württembergisches Cybersicherheitsgesetz", "Baden-Württemberg"),
        ("Gesetz über die Informationssicherheit im Land Sachsen-Anhalt", "Sachsen-Anhalt"),
    ]:
        assert land_of(title) == land, title


def test_administrative_measure_is_not_looked_up_in_bgbl():
    m = NationalMeasure("u", "DEU", None, "Gemeinsamer Runderlass zur Umsetzung europarechtlicher Vorgaben",
                        "Gesetz", None, "2024-12-23", None, None)
    fetch = FakeFetch()
    out = map_measure(m, GiiClient(fetch), "52", "01215-01218")
    assert out.status == "not_found" and "administrative" in out.note
    assert not fetch.urls  # no BGBl lookup


def test_resolve_abbreviation_via_gii_toc():
    toc = (b'<?xml version="1.0" encoding="UTF-8" ?><items>'
           b"<item><title>Energiewirtschaftsgesetz</title><link>http://www.gesetze-im-internet.de/enwg_2005/xml.zip</link></item>"
           b"<item><title>Telekommunikationsgesetz</title><link>http://www.gesetze-im-internet.de/tkg_2021/xml.zip</link></item>"
           b"<item><title>EnWG-Verordnung</title><link>http://www.gesetze-im-internet.de/enwgv/xml.zip</link></item>"
           b"</items>")
    fetch = FakeFetch()
    fetch.routes[TOC_URL] = toc
    c = GiiClient(fetch)
    # enwg/xml.zip is a 404 and NeuRIS does not have the act: the TOC has enwg_2005 (jurabk "EnWG 2005")
    assert c.resolve("EnWG") == "enwg_2005"
    assert c.resolve("TKG") == "tkg_2021"
    assert GiiClient(fetch, neuris=False).resolve("EnWG") == "enwg_2005"
    with pytest.raises(LookupError, match="abbreviation"):
        c.resolve("XYZG")
    assert land_of("Sachsen-Anhalt: Gesetz") == "Sachsen-Anhalt"


# --- HTTP layer -------------------------------------------------------------


def test_http_fetcher_cache(tmp_path, monkeypatch):
    calls = []

    class Resp:
        def read(self):
            return b"PK"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        calls.append((req.full_url, req.get_header("User-agent")))
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    f = HttpFetcher(cache_dir=tmp_path, delay=0)
    assert f(f"{BASE_URL}/bsig_2025/xml.zip") == b"PK"
    assert f(f"{BASE_URL}/bsig_2025/xml.zip") == b"PK"  # served from cache
    f(f"{NEURIS_API}?abbreviation=BSIG")
    assert len(calls) == 2 and calls[0][1].startswith("OpenAcquis/")
    assert {p.name for p in tmp_path.iterdir()} == {"gii", "neuris"}


def test_http_fetcher_error(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(LookupError, match="404"):
        HttpFetcher(cache_dir=False, delay=0)(f"{BASE_URL}/nope/xml.zip")


# --- live -------------------------------------------------------------------


@pytest.mark.live
def test_live_bsig(tmp_path):
    doc = GiiClient(HttpFetcher(cache_dir=tmp_path)).document("BSIG")
    assert doc.act_id == "bsig_2025"
    assert len(doc) > 600
    p = doc.by_id("de:bsig_2025:par30.abs2.nr1")
    assert "Risikoanalyse" in p.text
    assert doc.eli and doc.eli.startswith("eli/bund/bgbl-1/2025/301-2/")
    assert doc.by_id("de:bsig_2025:par30").eli.endswith("#art-z30")


@pytest.mark.live
def test_live_cellar_mapping(tmp_path):
    out = map_measures("32022L2555", client=GiiClient(HttpFetcher(cache_dir=tmp_path)))
    assert any(m.status == "mapped" and "bsig_2025" in m.refs for m in out)
