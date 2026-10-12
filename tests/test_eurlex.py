from pathlib import Path

import pytest

from openacquis.eurlex import (
    CELLAR_CELEX,
    EurLexClient,
    Response,
    TextNotAvailable,
    counts,
    default_cache_dir,
    eli_for_celex,
    is_waf_challenge,
    parse_html,
    validate_celex,
    validate_language,
)

FIXTURES = Path(__file__).parent / "fixtures"
NIS2 = "32022L2555"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def nis2(lang: str, **kw):
    return parse_html(fixture(f"eurlex_{NIS2}_{lang}.xhtml"), NIS2, lang, **kw)


def tail(doc):
    """Provision IDs without the ``eu:CELEX:lang:`` prefix."""
    return [p.id.split(":", 3)[3] for p in doc]


# --- parsing: Publications Office XHTML (trimmed NIS2: Art. 2, 6, 23, 46, Annex III) ---


def test_nis2_en_structure():
    d = nis2("en")
    assert d.source == "eu" and d.act_id == NIS2 and d.language == "en"
    assert d.title.startswith("DIRECTIVE (EU) 2022/2555 OF THE EUROPEAN PARLIAMENT")
    assert "(NIS 2 Directive)" in d.title
    assert d.eli == "http://data.europa.eu/eli/dir/2022/2555/oj"
    assert counts(d) == {"article": 4, "paragraph": 25, "point": 17, "subpoint": 12, "annex": 1}
    assert [p.label for p in d.roots()] == ["Article 2", "Article 6", "Article 23", "Article 46", "ANNEX III"]


def test_nis2_en_article_paragraph_point():
    d = nis2("en")
    art = d.by_id("eu:32022L2555:en:art23")
    assert (art.kind, art.number, art.heading, art.text) == ("article", "23", "Reporting obligations", "")
    assert art.url.endswith("TXT/?uri=CELEX:32022L2555#art_23")
    assert [c.number for c in d.children(art.id)] == [str(i) for i in range(1, 12)]

    par = d.by_id("eu:32022L2555:en:art23.par4")
    assert par.label == "4." and par.parent_id == art.id
    assert par.text.startswith("Member States shall ensure that, for the purpose of notification")
    assert par.url.endswith("#023.004")

    pt = d.by_id("eu:32022L2555:en:art23.par4.pt_a")
    assert pt.kind == "point" and pt.label == "(a)" and pt.number == "a"
    assert "within 24 hours" in pt.text
    assert pt.url == par.url  # points link to their paragraph's anchor

    sub = d.by_id("eu:32022L2555:en:art23.par4.pt_d.subpt_iv")
    assert sub.kind == "subpoint" and sub.label == "(iv)"


def test_subparagraphs_join_paragraph_text():
    par = nis2("en").by_id("eu:32022L2555:en:art23.par1")
    lines = par.text.split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("Each Member State shall ensure")
    assert lines[2].startswith("In the case of a cross-border")


def test_points_directly_under_article_and_nested():
    d = nis2("en")
    art6 = d.by_id("eu:32022L2555:en:art6")
    assert art6.text == "For the purposes of this Directive, the following definitions apply:"
    assert tail(p for _, p in d.walk(art6.id)) == [
        "art6", "art6.pt1", "art6.pt1.subpt_a", "art6.pt1.subpt_b", "art6.pt1.subpt_c", "art6.pt2",
    ]
    assert d.by_id("eu:32022L2555:en:art6.pt1").text == "‘network and information system’ means:"
    ii = d.by_id("eu:32022L2555:en:art2.par2.pt_f.subpt_ii")
    assert ii.text.startswith("at regional level")


def test_article_without_paragraphs_and_footer_ignored():
    d = nis2("en")
    a46 = d.by_id("eu:32022L2555:en:art46")
    assert a46.text == "This Directive is addressed to the Member States."
    assert d.children(a46.id) == []
    assert not any("Done at" in p.text or "METSOLA" in p.text for p in d)


def test_annex_flattened():
    anx = nis2("en").by_id("eu:32022L2555:en:anx_iii")
    assert (anx.kind, anx.label, anx.number, anx.heading) == ("annex", "ANNEX III", "iii", "CORRELATION TABLE")
    assert anx.text.split("\n")[1] == "Article 1(1) Article 1(1)"
    assert nis2("en", annexes=False).get("eu:32022L2555:en:anx_iii") is None


def test_recitals_optional_and_footnote_markers_dropped():
    assert nis2("en").get("eu:32022L2555:en:rct1") is None
    d = nis2("en", recitals=True)
    r1 = d.by_id("eu:32022L2555:en:rct1")
    assert r1.kind == "recital" and r1.label == "(1)" and r1.url.endswith("#rct_1")
    # the OJ footnote reference "(4)" after "Directive (EU) 2016/1148" is not part of the text
    assert r1.text.startswith("Directive (EU) 2016/1148 of the European Parliament and the Council aimed")
    assert [p.kind for p in d.roots()][:3] == ["recital", "recital", "article"]


def test_nis2_cs_matches_en_structure():
    en, cs = nis2("en"), nis2("cs")
    assert tail(en) == tail(cs)
    a = cs.by_id("eu:32022L2555:cs:art23")
    assert (a.label, a.heading) == ("Článek 23", "Oznamovací povinnosti")
    pt = cs.by_id("eu:32022L2555:cs:art23.par3.pt_a")
    assert pt.label == "a)" and pt.number == "a"
    assert cs.by_id("eu:32022L2555:cs:art2.par2.pt_f.subpt_ii").label == "ii)"
    assert cs.title.startswith("SMĚRNICE EVROPSKÉHO PARLAMENTU A RADY (EU) 2022/2555")


def test_round_trip_json():
    d = nis2("cs")
    from openacquis.provisions import Document

    assert Document.from_json(d.to_json()).to_dict() == d.to_dict()


# --- parsing: synthetic snippets ------------------------------------------------

OJ_SNIPPET = """<html><body>
<p class="oj-ti-art">Artikel 5</p><p class="oj-sti-art">Test</p>
<p class="oj-normal">(1) Die Mitgliedstaaten stellen sicher, dass</p>
<table><tr><td><p class="oj-normal">a)</p></td><td><p class="oj-normal">eins;</p></td></tr></table>
<p class="oj-normal">Unterabsatz 2:</p>
<table><tr><td><p class="oj-normal">a)</p></td><td><p class="oj-normal">noch eins;</p></td></tr></table>
<table><tr><td><p class="oj-normal">—</p></td><td><p class="oj-normal">Gedankenstrich</p></td></tr></table>
<p class="oj-normal">(2) Zweiter Absatz.</p>
<table class="oj-table"><tr><td><p>x</p></td><td><p>y</p></td><td><p>z</p></td></tr></table>
<p class="oj-ti-section-1">KAPITEL II</p>
<p class="oj-normal">not part of any article</p>
</body></html>"""


def test_german_paragraphs_duplicate_labels_indents_and_data_tables():
    d = parse_html(OJ_SNIPPET, NIS2, "de")
    assert tail(d) == ["art5", "art5.par1", "art5.par1.pt_a", "art5.par1.pt_a_2", "art5.par1.ind1", "art5.par2"]
    assert d.by_id("eu:32022L2555:de:art5.par1").label == "(1)"
    assert d.by_id("eu:32022L2555:de:art5.par1").text == "Die Mitgliedstaaten stellen sicher, dass\nUnterabsatz 2:"
    assert d.by_id("eu:32022L2555:de:art5.par1.pt_a_2").label == "a)"
    assert d.by_id("eu:32022L2555:de:art5.par2").text == "Zweiter Absatz.\nx y z"


def test_no_articles_is_an_error():
    with pytest.raises(ValueError):
        parse_html("<html><body><p>nothing here</p></body></html>", NIS2, "en")


def art2_ids(doc):
    return [i for i in tail(doc) if i == "art2" or i.startswith("art2.")]


def test_nis2_greek_points_and_annex_without_anx_ids():
    """EL: Greek point letters map to Latin IDs; annexes have no anx_ ids in this manifestation."""
    d = nis2("el")
    assert counts(d) == {"article": 2, "paragraph": 16, "point": 12, "subpoint": 5, "annex": 1}
    pt = d.by_id("eu:32022L2555:el:art1.par2.pt_a")
    assert pt.label == "α)" and pt.number == "a"
    assert d.by_id("eu:32022L2555:el:art2.par2.pt_a.subpt_iii").label == "iii)"
    anx = d.by_id("eu:32022L2555:el:anx_iii")
    assert (anx.label, anx.heading) == ("ΠΑΡΑΡΤΗΜΑ III", "ΠΙΝΑΚΑΣ ΑΝΤΙΣΤΟΙΧΙΑΣ")
    assert art2_ids(d) == art2_ids(nis2("en"))


def test_nis2_hungarian_roman_subpoints_with_dot():
    d = nis2("hu")
    sub = d.by_id("eu:32022L2555:hu:art2.par2.pt_a.subpt_i")
    assert sub.label == "i." and sub.kind == "subpoint"
    assert art2_ids(d) == art2_ids(nis2("en"))


# --- parsing: legacy plain HTML (pre-2014 acts; trimmed Directive 95/46/EC) -------


def test_legacy_html():
    d = parse_html(fixture("eurlex_31995L0046_en.html"), "31995L0046", "en", recitals=True)
    assert d.title.startswith("Directive 95/46/EC of the European Parliament")
    assert counts(d) == {"recital": 2, "article": 4, "paragraph": 4, "point": 8, "indent": 2}
    a2 = d.by_id("eu:31995L0046:en:art2")
    assert (a2.heading, a2.text) == ("Definitions", "For the purposes of this Directive:")
    assert d.by_id("eu:31995L0046:en:art2.pt_h").text.startswith("'the data subject's consent'")
    assert d.by_id("eu:31995L0046:en:art3.par2.ind2").text.startswith("by a natural person")
    a34 = d.by_id("eu:31995L0046:en:art34")
    assert a34.heading is None and a34.text == "This Directive is addressed to the Member States."


LEGACY_SNIPPET = """<html><body><div>
<p>Article 7</p><p>Points</p>
<p>1. Intro:</p>
<p>(a) first;</p><p>(i) sub one;</p><p>(ii) sub two;</p>
<p>(b) second;</p><p>(c) c</p><p>(d) d</p><p>(e) e</p><p>(f) f</p><p>(g) g</p><p>(h) h</p><p>(i) letter i;</p>
<p>2. Second paragraph.</p>
<p>CHAPTER II</p>
<p>Article 8</p><p>Some text without heading that ends with a full stop.</p>
<p>Done at Brussels, 1 January 2000.</p>
<p>ANNEX</p><p>LIST</p><p>Item one</p>
</div></body></html>"""


def test_legacy_roman_subpoints_and_annex():
    d = parse_html(LEGACY_SNIPPET, "32000L0001", "en")
    assert tail(d)[:5] == ["art7", "art7.par1", "art7.par1.pt_a", "art7.par1.pt_a.subpt_i", "art7.par1.pt_a.subpt_ii"]
    assert d.by_id("eu:32000L0001:en:art7.par1.pt_i").text == "letter i;"
    assert d.by_id("eu:32000L0001:en:art7").heading == "Points"
    a8 = d.by_id("eu:32000L0001:en:art8")
    assert a8.heading is None and a8.text.startswith("Some text")
    anx = d.by_id("eu:32000L0001:en:anx1")
    assert (anx.heading, anx.text) == ("LIST", "Item one")


LEGACY_HEADINGS = """<html><body>
<p>Article 1 Objective</p><p>1. This Directive concerns water.</p>
<p>Article 2</p><p>1. Text</p><p>EUR 1 000</p><p>This sentence is kept.</p><p>2. Second paragraph.</p>
<p>Section 2: Commercial communications</p>
<p>Article 3</p><p>Definitions</p><p>For the purposes of this Directive:</p>
<p>(a) a;</p><p>(b) b:</p><p>(i) one;</p><p>(ii) two;</p><p>(c) c;</p><p>(d) d;</p>
<p>(e) e</p><p>(f) f</p><p>(g) g</p><p>(h) coordinated field:</p>
<p>(i) concerns this;</p><p>(ii) does not cover that.</p>
<p>Article 4 of Directive 90/1/EEC is not an article heading.</p>
<p>Done at Brussels, 1 January 2000.</p>
</body></html>"""


def test_legacy_article_heading_on_same_line_and_section_headings():
    d = parse_html(LEGACY_HEADINGS, "32000L0002", "en")
    a1 = d.by_id("eu:32000L0002:en:art1")
    assert (a1.label, a1.heading) == ("Article 1", "Objective")
    par = d.by_id("eu:32000L0002:en:art2.par1")
    assert par.text == "Text\nEUR 1 000\nThis sentence is kept."  # not taken for a heading
    assert d.by_id("eu:32000L0002:en:art2.par2").text == "Second paragraph."  # mixed-case section heading dropped
    assert d.by_id("eu:32000L0002:en:art3").heading == "Definitions"
    assert d.get("eu:32000L0002:en:art4") is None  # an in-text reference, not an article
    assert "Article 4 of Directive" in d.by_id("eu:32000L0002:en:art3").text


def test_legacy_letters_and_roman_subpoints_by_sequence():
    ids = tail(parse_html(LEGACY_HEADINGS, "32000L0002", "en"))
    assert "art3.pt_b.subpt_i" in ids and "art3.pt_b.subpt_ii" in ids
    assert "art3.pt_c" in ids and "art3.pt_d" in ids  # (c), (d) after (ii) are points again
    assert "art3.pt_h.subpt_i" in ids and "art3.pt_h.subpt_ii" in ids  # (i), (ii) under (h)
    assert "art3.pt_i" not in ids


def test_legacy_greek_points():
    html = "<html><body><p>Άρθρο 1</p><p>α) πρώτο·</p><p>β) δεύτερο·</p><p>στ) έκτο.</p></body></html>"
    assert tail(parse_html(html, "32000L0002", "el")) == ["art1", "art1.pt_a", "art1.pt_b", "art1.pt_f"]


def test_convex_without_annex_ids_93_13():
    """Older CONVEX markup (no oj- prefix, no anx_ ids): the annex is found by its title."""
    d = parse_html(fixture("eurlex_31993L0013_en.html"), "31993L0013", "en")
    assert counts(d) == {"article": 3, "paragraph": 2, "point": 3, "annex": 1}
    anx = d.by_id("eu:31993L0013:en:anx1")
    assert (anx.label, anx.heading) == ("ANNEX", "TERMS REFERRED TO IN ARTICLE 3 (3)")
    assert "(q)" not in anx.text and "excluding or limiting the legal liability" in anx.text
    assert "Scope of subparagraphs (g), (j) and (1)" in anx.text
    # definitions in quotes are not amending text
    assert d.by_id("eu:31993L0013:en:art2.pt_a").text.startswith("‘unfair terms’ means")


QUOTED = """<html><body>
<p class="oj-ti-art">Article 1</p><p class="oj-normal">Directive X is amended as follows:</p>
<table><tr><td><p class="oj-normal">(1)</p></td><td>
  <p class="oj-normal">the following points are added:</p>
  <table><tr><td><p class="oj-normal">‘(17)</p></td><td><p class="oj-normal">“a” means b;</p></td></tr></table>
  <table><tr><td><p class="oj-normal">(18)</p></td><td><p class="oj-normal">“c” means d.’;</p></td></tr></table>
</td></tr></table>
<table><tr><td><p class="oj-normal">(2)</p></td><td>
  <p class="oj-normal">Article 19a is replaced by the following:</p>
  <div><p class="oj-ti-art">‘Article 19a</p><p class="oj-normal">1. Undertakings shall report.</p>
  <table><tr><td><p class="oj-normal">(a)</p></td><td><p class="oj-normal">their model.’;</p></td></tr></table></div>
</td></tr></table>
<table><tr><td><p class="oj-normal">(3)</p></td><td><p class="oj-normal">the term ‘x’ is deleted;</p>
  <table><tr><td><p class="oj-normal">(a)</p></td><td><p class="oj-normal">real sub-point.</p></td></tr></table>
</td></tr></table>
</body></html>"""


def test_quoted_amendment_text_stays_text():
    d = parse_html(QUOTED, "32022L2464", "en")
    assert tail(d) == ["art1", "art1.pt1", "art1.pt2", "art1.pt3", "art1.pt3.subpt_a"]
    pt1 = d.by_id("eu:32022L2464:en:art1.pt1").text.split("\n")
    assert pt1 == ["the following points are added:", "‘(17) “a” means b;", "(18) “c” means d.’;"]
    pt2 = d.by_id("eu:32022L2464:en:art1.pt2").text
    assert "‘Article 19a" in pt2 and pt2.endswith("(a) their model.’;")


# --- validation and helpers ---------------------------------------------------------


def test_validation():
    assert validate_celex(" 32022l2555 ") == NIS2
    assert validate_language("CS") == "cs"
    with pytest.raises(ValueError):
        validate_celex("2022/2555")
    with pytest.raises(ValueError):
        validate_language("cz")  # country code, not a language code
    assert eli_for_celex("32019L0882") == "http://data.europa.eu/eli/dir/2019/882/oj"
    assert eli_for_celex("52021PC0001") is None


def test_waf_detection():
    assert is_waf_challenge(Response(202, "u", {"x-amzn-waf-action": "challenge"}, b""))
    assert is_waf_challenge(Response(202, "u", {}, b""))
    assert is_waf_challenge(Response(200, "u", {}, b"<html><script src='x/challenge.js'></script>"))
    assert not is_waf_challenge(Response(200, "u", {}, b"<html><p class='oj-ti-art'>Article 1</p>"))


def test_cache_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENACQUIS_CACHE", str(tmp_path))
    assert default_cache_dir() == tmp_path
    monkeypatch.delenv("OPENACQUIS_CACHE")
    assert default_cache_dir() == Path.home() / ".cache" / "openacquis"


# --- client with a stubbed network ---------------------------------------------------

XHTML = "application/xhtml+xml;charset=UTF-8"


class FakeGet:
    """Serves ``routes[(url, accept)]`` -> Response; records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers.get("Accept"), headers.get("Accept-Language")))
        return self.routes.get((url, headers.get("Accept")), Response(404, url, {}, b"not found"))


def ok(url, body, ctype=XHTML):
    return Response(200, url, {"content-type": ctype}, body.encode())


def test_client_cellar_and_cache(tmp_path):
    cellar = CELLAR_CELEX.format(celex=NIS2)
    get = FakeGet({(cellar, "application/xhtml+xml"): ok(cellar + "/DOC_1", fixture(f"eurlex_{NIS2}_cs.xhtml"))})
    client = EurLexClient(get=get, cache_dir=tmp_path)
    d = client.document("32022l2555", "CS")
    assert d.language == "cs" and len(d.children(None)) == 5
    assert get.calls == [(cellar, "application/xhtml+xml", "cs")]

    st = client.fetch(NIS2, "cs")  # served from the cache
    assert st.source == "cellar" and st.url == cellar + "/DOC_1" and len(get.calls) == 1
    assert (tmp_path / "eurlex" / f"{NIS2}_cs.html").exists()
    # a new client with the same cache dir does not touch the network either
    assert EurLexClient(get=FakeGet({}), cache_dir=tmp_path).fetch(NIS2, "cs").html == st.html

    client.fetch(NIS2, "cs", refresh=True)
    assert len(get.calls) == 2


def test_client_falls_back_to_text_html_then_eurlex(tmp_path):
    cellar = CELLAR_CELEX.format(celex="31995L0046")
    legacy = fixture("eurlex_31995L0046_en.html")
    get = FakeGet({(cellar, "text/html"): ok(cellar, legacy, "text/html;charset=UTF-8")})
    st = EurLexClient(get=get, cache_dir=False).fetch("31995L0046", "en")
    assert st.source == "cellar" and st.content_type.startswith("text/html")
    assert [c[1] for c in get.calls] == ["application/xhtml+xml", "text/html"]

    eurlex = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:31995L0046"
    get = FakeGet({(eurlex, "text/html"): ok(eurlex, legacy, "text/html")})
    st = EurLexClient(get=get, cache_dir=False).fetch("31995L0046", "en")
    assert st.source == "eurlex" and "Article 34" in st.html


def test_client_reports_waf_challenge():
    eurlex = "https://eur-lex.europa.eu/legal-content/CS/TXT/HTML/?uri=CELEX:32022L2555"
    waf = Response(202, eurlex, {"x-amzn-waf-action": "challenge"}, b"")
    get = FakeGet({(eurlex, "text/html"): waf})
    with pytest.raises(TextNotAvailable, match="WAF"):
        EurLexClient(get=get, cache_dir=False).fetch(NIS2, "cs")
    with pytest.raises(ValueError):
        EurLexClient(get=get, sources=("formex",))


def test_client_multiple_choices_concatenates_documents():
    cellar = CELLAR_CELEX.format(celex=NIS2)
    part = "http://publications.europa.eu/resource/cellar/x.0006.03/DOC_{}"
    choices = f'<html><a href="{part.format(2)}">2</a><a href="{part.format(1)}">1</a></html>'
    get = FakeGet({
        (cellar, "application/xhtml+xml"): Response(300, cellar, {"content-type": "text/html"}, choices.encode()),
        (part.format(1), "application/xhtml+xml"): ok(part.format(1), '<p class="oj-ti-art">Article 1</p><p class="oj-normal">One.</p>'),
        (part.format(2), "application/xhtml+xml"): ok(part.format(2), '<p class="oj-ti-art">Article 2</p><p class="oj-normal">Two.</p>'),
    })
    d = EurLexClient(get=get, cache_dir=False).document(NIS2, "en")
    assert [(p.label, p.text) for p in d] == [("Article 1", "One."), ("Article 2", "Two.")]


# --- live ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_nis2_en_cs(tmp_path):
    client = EurLexClient(cache_dir=tmp_path)
    en = client.document(NIS2, "en")
    cs = client.document(NIS2, "cs")
    for d in (en, cs):
        c = counts(d)
        assert c["article"] == 46 and c["paragraph"] == 189 and c["annex"] == 3
    assert tail(en) == tail(cs)
    assert "24 hours" in en.by_id("eu:32022L2555:en:art23.par4.pt_a").text
    assert "24 hodin" in cs.by_id("eu:32022L2555:cs:art23.par4.pt_a").text
