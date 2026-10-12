import json

import pytest

from openacquis.provisions import (
    Document,
    Provision,
    make_id,
    make_segment,
    normalize_number,
    provisions_from_jsonl,
)

CELEX = "32022L2555"


def eu(path, kind, label, number, text="", parent=None, heading=None):
    return Provision(
        id=make_id("eu", CELEX, path, "cs"),
        source="eu",
        act_id=CELEX,
        language="cs",
        kind=kind,
        label=label,
        number=number,
        text=text,
        heading=heading,
        parent_id=parent,
    )


@pytest.fixture
def doc():
    d = Document(source="eu", act_id=CELEX, language="cs", title="NIS 2", date="2022-12-14")
    a23 = d.add(eu([("article", 23)], "article", "Článek 23", "23", heading="Ohlašovací povinnosti"))
    p4 = d.add(eu([("article", 23), ("paragraph", 4)], "paragraph", "4.", "4", "Pro účely oznámení:", a23.id))
    d.add(eu([("article", 23), ("paragraph", 4), ("point", "a")], "point", "a)", "a", "včasné varování do 24 hodin;", p4.id))
    d.add(eu([("article", 23), ("paragraph", 4), ("point", "b")], "point", "b)", "b", "oznámení do 72 hodin;", p4.id))
    d.add(eu([("article", 24)], "article", "Článek 24", "24", "Používání evropských systémů certifikace."))
    return d


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("23", "23"),
        (23, "23"),
        ("Article 23", "23"),
        ("§ 12", "12"),
        ("§ 4a", "4a"),
        ("(a)", "a"),
        ("b)", "b"),
        ("B", "b"),
        ("IV", "iv"),
        ("(ii)", "ii"),
        ("1.", "1"),
        ("odst. 3", "3"),
        ("písm. b)", "b"),
        ("12 bis", "12_bis"),
    ],
)
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected


def test_normalize_number_empty():
    with pytest.raises(ValueError):
        normalize_number("()")


def test_make_segment():
    assert make_segment("article", "23") == "art23"
    assert make_segment("point", "(a)") == "pt_a"
    assert make_segment("chapter", "IV") == "chap_iv"
    assert make_segment("odst", "3") == "odst3"  # unknown kind used as prefix


def test_make_id_examples():
    assert (
        make_id("EU", CELEX, [("article", 23), ("paragraph", 4), ("point", "a")], "CS")
        == "eu:32022L2555:cs:art23.par4.pt_a"
    )
    assert (
        make_id("cz", "264/2025", [("par", "§ 12"), ("odst", "3"), ("pism", "b)")])
        == "cz:264/2025:par12.odst3.pism_b"
    )
    assert make_id("eu", CELEX, [], "en") == "eu:32022L2555:en"


def test_make_id_is_stable_across_label_styles():
    a = make_id("eu", CELEX, [("article", "Article 4a"), ("point", "(B)")], "en")
    b = make_id("eu", CELEX, [("article", "4a"), ("point", "b")], "en")
    assert a == b == "eu:32022L2555:en:art4a.pt_b"


def test_lookup_and_duplicates(doc):
    assert len(doc) == 5
    p = doc.by_id("eu:32022L2555:cs:art23.par4.pt_a")
    assert p.text.startswith("včasné varování")
    assert doc.get("nope") is None
    with pytest.raises(KeyError):
        doc.by_id("nope")
    with pytest.raises(ValueError):
        doc.add(Provision(**p.to_dict()))


def test_tree(doc):
    assert [p.number for p in doc.roots()] == ["23", "24"]
    assert [p.number for p in doc.children("eu:32022L2555:cs:art23.par4")] == ["a", "b"]
    walked = [(d, p.id.split(":")[-1]) for d, p in doc.walk()]
    assert walked == [
        (0, "art23"),
        (1, "art23.par4"),
        (2, "art23.par4.pt_a"),
        (2, "art23.par4.pt_b"),
        (0, "art24"),
    ]
    sub = [p.number for _, p in doc.walk("eu:32022L2555:cs:art23.par4")]
    assert sub == ["4", "a", "b"]


def test_full_text(doc):
    text = doc.full_text("eu:32022L2555:cs:art23")
    assert text.splitlines() == [
        "Článek 23 Ohlašovací povinnosti",
        "4. Pro účely oznámení:",
        "a) včasné varování do 24 hodin;",
        "b) oznámení do 72 hodin;",
    ]
    assert "Článek 24" not in text


def test_json_roundtrip(doc):
    again = Document.from_json(doc.to_json())
    assert again == doc
    assert again.title == "NIS 2"
    assert json.loads(doc.to_json())["provisions"][0]["heading"] == "Ohlašovací povinnosti"
    # tree helpers still work after a round trip
    assert [p.number for p in again.children("eu:32022L2555:cs:art23")] == ["4"]


def test_jsonl_roundtrip(doc):
    lines = doc.to_jsonl().splitlines()
    assert len(lines) == 5
    assert json.loads(lines[0])["act_id"] == CELEX
    assert provisions_from_jsonl(lines + [""]) == doc.provisions


def test_provision_from_dict_ignores_unknown_keys():
    p = Provision.from_dict(
        {"id": "x", "source": "cz", "act_id": "264/2025", "language": "cs", "kind": "par",
         "label": "§ 1", "number": "1", "extra": "ignored"}
    )
    assert p.text == "" and p.parent_id is None
