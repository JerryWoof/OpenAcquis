import json
import urllib.parse
from pathlib import Path

import pytest

from openacquis.adapters.cz import (
    API_URL,
    ESbirkaClient,
    HttpFetcher,
    fetch_act,
    html_text,
    parse_fragments,
    parse_reference,
    split_label,
)
from openacquis.provisions import Document

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeFetch:
    """Serve saved e-Sbírka responses by URL; records every URL requested."""

    def __init__(self, act: str, pages: int = 1):
        self.urls = []
        num, year = act.split("/")
        meta = load(f"cz_{num}_{year}_meta.json")
        enc = lambda u: urllib.parse.quote(u, safe="")  # noqa: E731
        self.routes = {
            f"{API_URL}/dokumenty-sbirky/{enc(f'/sb/{year}/{num}')}": meta,
        }
        stale = meta["staleUrl"]
        for p in range(pages):
            name = f"cz_{num}_{year}_fragmenty_p{p}.json" if pages > 1 else f"cz_{num}_{year}_fragmenty.json"
            self.routes[f"{API_URL}/dokumenty-sbirky/{enc(stale)}/fragmenty?cisloStranky={p}"] = load(name)

    def __call__(self, url):
        self.urls.append(url)
        return self.routes[url]


@pytest.fixture(scope="module")
def kyber():
    fetch = FakeFetch("264/2025", pages=2)
    doc = ESbirkaClient(fetch).document("264/2025")
    doc.fetch = fetch  # for inspection in tests
    return doc


# --- references -------------------------------------------------------------


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("264/2025", ("264/2025", "/sb/2025/264")),
        (" č. 264/2025 Sb. ", ("264/2025", "/sb/2025/264")),
        ("/sb/2025/264", ("264/2025", "/sb/2025/264")),
        ("/eli/cz/sb/2025/264/2025-11-01", ("264/2025", "/sb/2025/264/2025-11-01")),
        ("https://e-sbirka.gov.cz/sb/2012/89/2026-01-01#par_1", ("89/2012", "/sb/2012/89/2026-01-01")),
        ("https://www.e-sbirka.cz/sb/2012/89", ("89/2012", "/sb/2012/89")),
    ],
)
def test_parse_reference(ref, expected):
    r = parse_reference(ref)
    assert (r.act_id, r.stale_url) == expected


def test_parse_reference_version_and_errors():
    assert parse_reference("264/2025", "0000-00-00").stale_url == "/sb/2025/264/0000-00-00"
    with pytest.raises(ValueError):
        parse_reference("Zákon o kybernetické bezpečnosti")
    with pytest.raises(ValueError):
        parse_reference("264/2025", "1.11.2025")


# --- XHTML ------------------------------------------------------------------


def test_split_label_and_footnote_refs():
    x = (
        '<var>(3)</var> Tento zákon zapracovává příslušný předpis Evropské unie'
        '<a data-odkaz-id="1" href="#" class="int_odkaz"><sup>2</sup>)</a> a zároveň&nbsp;navazuje.'
    )
    assert split_label(x) == ("(3)", "Tento zákon zapracovává příslušný předpis Evropské unie a zároveň navazuje.")
    assert split_label("<var><sup>2</sup>)</var> Směrnice") == ("2)", "Směrnice")
    assert split_label("Zrušují se:") == (None, "Zrušují se:")


def test_html_table_rows():
    t = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>x <br/>y</td></tr></table>"
    assert html_text(t) == "A | B\n1 | x\ny"


# --- 264/2025 (two fixture pages) ------------------------------------------


def test_document_metadata_and_requests(kyber):
    assert kyber.source == "cz" and kyber.act_id == "264/2025" and kyber.language == "cs"
    assert kyber.title == "Zákon č. 264/2025 Sb., o kybernetické bezpečnosti"
    assert kyber.date == "2025-11-01"
    assert kyber.eli == "/eli/cz/sb/2025/264/2025-11-01"
    assert kyber.url == "https://e-sbirka.gov.cz/sb/2025/264/2025-11-01"
    # metadata, then both pages of the dated version
    assert len(kyber.fetch.urls) == 3
    assert kyber.fetch.urls[1].endswith("%2Fsb%2F2025%2F264%2F2025-11-01/fragmenty?cisloStranky=0")


def test_ids_hierarchy_and_text(kyber):
    p = kyber.by_id("cz:264/2025:par2.odst1.pism_b")
    assert (p.kind, p.label, p.number) == ("pism", "b)", "b")
    assert p.parent_id == "cz:264/2025:par2.odst1"
    assert p.text.startswith("informací ")
    assert p.eli == "/eli/cz/sb/2025/264/2025-11-01/dokument/norma/cast_1/hlava_1/par_2/odst_1/pism_b"
    assert p.url == "https://e-sbirka.gov.cz/sb/2025/264/2025-11-01#par_2-odst_1-pism_b"

    par1 = kyber.by_id("cz:264/2025:par1")
    assert (par1.label, par1.heading, par1.parent_id) == ("§ 1", "Předmět úpravy", "cz:264/2025:cast1.hlava1")
    assert [c.id for c in kyber.children(par1.id)] == [f"cz:264/2025:par1.odst{i}" for i in range(1, 5)]
    assert "unie a zároveň" in kyber.by_id("cz:264/2025:par1.odst3").text  # footnote marker removed

    hlava = kyber.by_id("cz:264/2025:cast1.hlava1")
    assert (hlava.kind, hlava.label, hlava.heading) == ("hlava", "HLAVA I", "Základní ustanovení")
    assert kyber.by_id("cz:264/2025:cast1").heading == "KYBERNETICKÁ BEZPEČNOST"
    assert kyber.by_id("cz:264/2025:par4.odst1.pism_a.bod15").text == "vesmírný průmysl a"


def test_headings_above_and_folded_text(kyber):
    # heading printed above § 4 (Nadpis_nad) is attached to it
    assert kyber.by_id("cz:264/2025:par4").heading == "Podmínky pro registraci regulované služby"
    # an own heading wins over a pending group heading
    assert kyber.by_id("cz:264/2025:par71").heading == "Přechodná ustanovení"
    # continuation text after the letters is the paragraph's own text
    odst = kyber.by_id("cz:264/2025:par71.odst1")
    assert odst.text.endswith("rozhodnutí o registraci regulované služby.")
    assert "\na to počínaje dnem" in odst.text
    # unnumbered sole paragraph is folded into the §; its points hang from the §
    par72 = kyber.by_id("cz:264/2025:par72")
    assert (par72.text, par72.heading) == ("Zrušují se:", "Zrušovací ustanovení")
    assert kyber.by_id("cz:264/2025:par72.bod1").parent_id == par72.id
    assert kyber.by_id("cz:264/2025:par73").text.startswith("Tento zákon nabývá účinnosti")


def test_prefix_postfix_skipped_footnotes_kept(kyber):
    kinds = {p.kind for p in kyber}
    assert kinds == {"cast", "hlava", "dil", "par", "odst", "pism", "bod", "ppc"}
    assert not any("Pekarová" in p.text for p in kyber)
    fn = kyber.by_id("cz:264/2025:ppc2")
    assert fn.label == "2)" and "(EU) 2022/2555" in fn.text and fn.parent_id is None


def test_roundtrip_json(kyber):
    again = Document.from_json(kyber.to_json())
    assert [p.to_dict() for p in again] == [p.to_dict() for p in kyber]
    assert len({p.id for p in kyber}) == len(kyber)


# --- other shapes: annex with tables, amending act with quoted text ---------


def test_annex_and_unnumbered_paragraph():
    doc = fetch_act("408/2025", fetch=FakeFetch("408/2025"))
    par1 = doc.by_id("cz:408/2025:par1")
    assert par1.text.startswith("Tato vyhláška zapracovává příslušný předpis Evropské unie,")
    assert doc.by_id("cz:408/2025:par1.pism_a").parent_id == par1.id
    annex = doc.by_id("cz:408/2025:priloha0")
    assert (annex.kind, annex.label, annex.text) == ("priloha", "Příloha", "")
    bod1 = doc.by_id("cz:408/2025:priloha0.bod1")
    assert bod1.text.startswith("Veřejná správa\n") and " | " in bod1.text


def test_amending_act_quotes_are_text():
    doc = fetch_act("270/2025", fetch=FakeFetch("270/2025"))
    assert doc.by_id("cz:270/2025:cast1").heading == "Změna trestního zákoníku"
    cl1 = doc.by_id("cz:270/2025:cl1")
    assert (cl1.label, cl1.parent_id) == ("Čl. I", "cz:270/2025:cast1")
    bod18 = doc.by_id("cz:270/2025:cl1.bod18")
    assert bod18.heading is None  # quoted heading of the amended § stays inside the text
    assert "„§ 67\nPeněžitý trest\n(1) Peněžitý trest" in bod18.text
    # quoted § / písm. never become provisions of the amending act
    assert not any(p.kind == "par" for p in doc)
    assert "„i) zákaz plnění" in doc.by_id("cz:270/2025:cl1.bod8").text


def test_parse_fragments_act_id_from_meta():
    meta = load("cz_408_2025_meta.json")
    doc = parse_fragments(load("cz_408_2025_fragmenty.json")["seznam"], meta)
    assert doc.act_id == "408/2025" and doc.get("cz:408/2025:par1") is not None


def test_repeated_section_number_children_follow_full_path():
    """§ 1 in part 1 and again in part 2: part 2's subsections hang off cast2.par1."""
    base = "/eli/cz/sb/2020/1/2020-01-01/dokument/norma/"

    def frag(path, ftype, depth, text):
        return {"kodTypuFragmentu": ftype, "hloubka": depth, "eli": base + path, "xhtml": text}

    frags = [
        frag("cast_1", "Cast", 3, "<var>ČÁST PRVNÍ</var>"),
        frag("cast_1/par_1", "Paragraf", 4, "<var>§ 1</var>"),
        frag("cast_1/par_1/odst_1", "Odstavec_Dc", 5, "<var>(1)</var> A."),
        frag("cast_2", "Cast", 3, "<var>ČÁST DRUHÁ</var>"),
        frag("cast_2/par_1", "Paragraf", 4, "<var>§ 1</var>"),
        frag("cast_2/par_1/odst_1", "Odstavec_Dc", 5, "<var>(1)</var> B."),
        frag("cast_2/par_1/odst_2", "Odstavec_Dc", 5, "<var>(2)</var> C."),
    ]
    doc = parse_fragments(frags, act_id="1/2020")
    ids = [p.id for p in doc]
    assert ids == [
        "cz:1/2020:cast1", "cz:1/2020:par1", "cz:1/2020:par1.odst1",
        "cz:1/2020:cast2", "cz:1/2020:cast2.par1", "cz:1/2020:cast2.par1.odst1", "cz:1/2020:cast2.par1.odst2",
    ]
    for p in doc:
        if p.parent_id:
            assert p.id.startswith(p.parent_id + ".") or p.kind in ("par",)


# --- HTTP layer -------------------------------------------------------------


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
        calls.append((req.full_url, req.get_header("User-agent"), timeout))
        return Resp(b'{"ok": 1}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    f = HttpFetcher(cache_dir=tmp_path, delay=0)
    assert f("https://example.test/a") == {"ok": 1}
    assert f("https://example.test/a") == {"ok": 1}  # served from cache
    assert len(calls) == 1 and calls[0][1].startswith("OpenAcquis/")
    assert f.requests == 1 and len(list(tmp_path.iterdir())) == 1


def test_default_cache_dir_env(monkeypatch, tmp_path):
    from openacquis.adapters.cz import default_cache_dir

    monkeypatch.setenv("OPENACQUIS_CACHE", str(tmp_path))
    assert default_cache_dir() == tmp_path / "esbirka"


# --- live -------------------------------------------------------------------


@pytest.mark.live
def test_live_264_2025(tmp_path):
    doc = fetch_act("264/2025", fetch=HttpFetcher(cache_dir=tmp_path))
    assert doc.title.startswith("Zákon č. 264/2025 Sb.")
    pars = [p for p in doc if p.kind == "par"]
    assert len(pars) >= 73
    assert doc.by_id("cz:264/2025:par1").heading == "Předmět úpravy"
    assert doc.get("cz:264/2025:par12.odst2.pism_b") is not None
    assert len(doc) > 700
