import io
import json

import pytest

from openacquis.match import (
    Candidate,
    Embedder,
    LexicalEmbedder,
    Matcher,
    MatchResult,
    SentenceTransformerEmbedder,
    VectorCache,
    candidates_from_jsonl,
    czech_stem,
    level_id,
    match,
    normalize_tokens,
    units,
    write_jsonl,
)
from openacquis.provisions import Document, Provision

EU = "32099L0001"


def eu_doc() -> Document:
    d = Document("eu", EU, "cs", title="Směrnice o kybernetické bezpečnosti")

    def add(pid, kind, label, number, text="", heading=None, parent=None):
        d.add(Provision(f"eu:{EU}:cs:{pid}", "eu", EU, "cs", kind, label, number, text, heading,
                        f"eu:{EU}:cs:{parent}" if parent else None, url=f"https://eur-lex.example/{pid}"))

    add("art1", "article", "Článek 1", "1", heading="Předmět")
    add("art1.par1", "paragraph", "1.", "1", "Tato směrnice stanoví opatření k dosažení vysoké úrovně kybernetické bezpečnosti.", parent="art1")
    add("art2", "article", "Článek 2", "2", heading="Oznamovací povinnosti")
    add("art2.par1", "paragraph", "1.", "1", "Členské státy zajistí, aby subjekty předložily týmu CSIRT:", parent="art2")
    add("art2.par1.pt_a", "point", "a)", "a", "nejpozději do 24 hodin po zjištění významného incidentu včasné varování;", parent="art2.par1")
    add("art2.par1.pt_b", "point", "b)", "b", "nejpozději do jednoho měsíce závěrečnou zprávu o incidentu.", parent="art2.par1")
    add("art2.par2", "paragraph", "2.", "2", "Členské státy stanoví pokuty za porušení oznamovacích povinností.", parent="art2")
    return d


def cz_doc(act="1/2099") -> Document:
    d = Document("cz", act, "cs", title="Zákon o kybernetické bezpečnosti")

    def add(pid, kind, label, number, text="", heading=None, parent=None):
        d.add(Provision(f"cz:{act}:{pid}", "cz", act, "cs", kind, label, number, text, heading,
                        f"cz:{act}:{parent}" if parent else None, url=f"https://e-sbirka.example/{pid}"))

    add("hlava1", "hlava", "HLAVA I", "1", heading="Obecná ustanovení")
    add("par1", "par", "§ 1", "1", heading="Předmět úpravy", parent="hlava1")
    add("par1.odst1", "odst", "(1)", "1", "Tento zákon upravuje opatření k zajištění kybernetické bezpečnosti.", parent="par1")
    add("par5", "par", "§ 5", "5", heading="Hlášení incidentů", parent="hlava1")
    add("par5.odst1", "odst", "(1)", "1", "Poskytovatel služby hlásí Úřadu", parent="par5")
    add("par5.odst1.pism_a", "pism", "a)", "a", "do 24 hodin po zjištění významného incidentu včasné varování,", parent="par5.odst1")
    add("par5.odst1.pism_b", "pism", "b)", "b", "do 30 dnů závěrečnou zprávu o vyřešení incidentu.", parent="par5.odst1")
    add("par9", "par", "§ 9", "9", heading="Přestupky", parent="hlava1")
    add("par9.odst1", "odst", "(1)", "1", "Za přestupek lze uložit pokutu do 10 000 000 Kč, nesplní-li poskytovatel oznamovací povinnost.", parent="par9")
    add("ppc1", "ppc", "1)", "1", "Směrnice Evropského parlamentu a Rady (EU) 2099/1.")
    return d


def tail(pid: str) -> str:
    return pid.rsplit(":", 1)[1]


# --- normalisation ------------------------------------------------------------


def test_czech_stem_and_tokens():
    assert czech_stem("incidentů") == "incident"
    assert czech_stem("incidentech") == "incident"
    assert czech_stem("hodin") == czech_stem("hodinách")
    assert normalize_tokens("Oznámení INCIDENTŮ") == ["oznamen", "incident"]
    assert normalize_tokens("Oznámení", stem=False, diacritics=True) == ["oznámení"]


def test_lexical_similarity_basic():
    e = LexicalEmbedder()
    rows = e.similarities(["hlášení incidentu"], ["hlášení incidentů do 24 hodin", "pokuta za přestupek", ""])
    assert rows[0][0] > 0.3 and rows[0][0] > rows[0][1]
    assert rows[0][2] == 0.0
    assert isinstance(e, Embedder)
    # deterministic
    assert e.similarities(["a b c"], ["a b c d"]) == LexicalEmbedder().similarities(["a b c"], ["a b c d"])


# --- units --------------------------------------------------------------------


def test_leaf_units_have_context_and_skip_structure():
    us = {tail(u.id): u for u in units(cz_doc())}
    assert set(us) == {"par1.odst1", "par5.odst1", "par5.odst1.pism_a", "par5.odst1.pism_b", "par9.odst1"}
    a = us["par5.odst1.pism_a"]
    assert a.body.startswith("do 24 hodin")
    assert "Hlášení incidentů" in a.text and "Poskytovatel služby hlásí Úřadu" in a.text
    assert a.ancestors[-1].endswith(":par5.odst1")
    plain = {tail(u.id): u for u in units(cz_doc(), context=False)}
    assert plain["par5.odst1.pism_a"].text == plain["par5.odst1.pism_a"].body


def test_coarser_units():
    arts = {tail(u.id): u for u in units(eu_doc(), "article")}
    assert set(arts) == {"art1", "art2"}
    assert "včasné varování" in arts["art2"].body and "pokuty" in arts["art2"].body
    paras = [tail(u.id) for u in units(cz_doc(), "paragraph")]
    assert paras == ["par1.odst1", "par5.odst1", "par9.odst1"]
    with pytest.raises(ValueError):
        units(eu_doc(), "chapter")


def test_level_id():
    d = eu_doc()
    assert tail(level_id(d, f"eu:{EU}:cs:art2.par1.pt_b", "article")) == "art2"
    assert tail(level_id(d, f"eu:{EU}:cs:art2.par1.pt_b", "paragraph")) == "art2.par1"
    c = cz_doc()
    assert tail(level_id(c, "cz:1/2099:par5.odst1.pism_a", "article")) == "par5"


# --- matching -----------------------------------------------------------------


def test_match_top_candidates():
    res = Matcher(k=2).match(eu_doc(), cz_doc())
    assert isinstance(res, MatchResult)
    assert len(res.eu_units) == 5 and len(res.national_units) == 5
    cands = res.candidates()
    assert all(isinstance(c, Candidate) and c.status == "candidate" for c in cands)
    best = {tail(c.eu_id): tail(c.national_id) for c in cands if c.rank == 1}
    assert best["art2.par1.pt_a"] == "par5.odst1.pism_a"
    assert best["art2.par1.pt_b"] == "par5.odst1.pism_b"
    assert best["art2.par2"] == "par9.odst1"
    assert best["art1.par1"] == "par1.odst1"
    ranks = [c.rank for c in cands if c.eu_id.endswith("art2.par1.pt_a")]
    assert ranks == [1, 2]
    scores = [c.score for c in cands if c.eu_id.endswith("art2.par1.pt_a")]
    assert scores == sorted(scores, reverse=True)
    assert cands[0].method == "lexical:char3-5" and cands[0].level == "leaf"
    assert [tail(c.eu_id) for c in res.for_eu(f"eu:{EU}:cs:art2.par1", k=1)] == [
        "art2.par1", "art2.par1.pt_a", "art2.par1.pt_b"]


def test_match_shortcut_and_min_score():
    cands = match(eu_doc(), [cz_doc()], k=1)
    assert len(cands) == 5 and {c.rank for c in cands} == {1}
    res = Matcher().match(eu_doc(), cz_doc())
    assert all(c.score >= 0.5 for c in res.candidates(k=5, min_score=0.5))


def test_multiple_national_documents():
    other = cz_doc("2/2099")
    res = Matcher(k=10).match(eu_doc(), [cz_doc(), other])
    acts = {c.national_id.split(":")[1] for c in res.candidates()}
    assert acts == {"1/2099", "2/2099"}
    with pytest.raises(ValueError):
        Matcher().match(eu_doc(), [cz_doc(), cz_doc()])


def test_rollup_to_article():
    res = Matcher(k=3).match(eu_doc(), cz_doc())
    up = res.rollup("article")
    best = {tail(c.eu_id): tail(c.national_id) for c in up if c.rank == 1}
    assert best == {"art1": "par1", "art2": "par5"}
    assert all(c.level == "article" and c.method.endswith("+rollup-mean") for c in up)
    mx = {(tail(c.eu_id), tail(c.national_id)): c.score for c in res.rollup("article", agg="max")}
    mean = {(tail(c.eu_id), tail(c.national_id)): c.score for c in up}
    assert mx[("art2", "par5")] >= mean[("art2", "par5")]
    # paragraph level on the EU side, § on the national side
    pa = res.rollup("paragraph", "article")
    assert {tail(c.eu_id) for c in pa} == {"art1.par1", "art2.par1", "art2.par2"}
    with pytest.raises(ValueError):
        res.rollup("article", agg="median")


def test_article_granularity():
    res = Matcher(granularity="article", k=1).match(eu_doc(), cz_doc())
    best = {tail(c.eu_id): tail(c.national_id) for c in res.candidates()}
    assert best["art1"] == "par1"
    assert all(c.level == "article" for c in res.candidates())


def test_empty_inputs():
    empty = Document("cz", "3/2099", "cs")
    res = Matcher().match(eu_doc(), empty)
    assert res.candidates() == []


# --- export -------------------------------------------------------------------


def test_jsonl_export_keeps_evidence(tmp_path):
    e, c = eu_doc(), cz_doc()
    res = Matcher(k=1).match(e, c)
    lines = res.to_jsonl().splitlines()
    assert len(lines) == 5
    row = json.loads(next(line for line in lines if "art2.par1.pt_a" in line))
    assert row["national_id"] == "cz:1/2099:par5.odst1.pism_a"
    assert row["status"] == "candidate" and row["rank"] == 1 and 0 < row["score"] <= 1
    assert "24 hodin" in row["eu_text"] and "24 hodin" in row["national_text"]
    assert row["eu_url"].endswith("art2.par1.pt_a") and row["national_act"] == "1/2099"
    path = tmp_path / "c.jsonl"
    write_jsonl(res.candidates(), path, e, [c])
    back = candidates_from_jsonl(path.read_text(encoding="utf-8").splitlines())
    assert back == res.candidates()
    buf = io.StringIO()
    write_jsonl(res.rollup("article"), buf)  # without documents: no evidence fields
    assert "eu_text" not in json.loads(buf.getvalue().splitlines()[0])


# --- dense embedder with a stub model -------------------------------------------


class StubModel:
    """Bag-of-letters 'embedding'; records what was encoded."""

    def __init__(self):
        self.calls = []

    def encode(self, texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False):
        self.calls.append(list(texts))
        out = []
        for t in texts:
            v = [float(t.lower().count(ch)) for ch in "abcdeghijklmnoprstuvyz"]
            n = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / n for x in v])
        return out


def test_sentence_transformer_embedder_prefixes_and_cache(tmp_path):
    model = StubModel()
    emb = SentenceTransformerEmbedder(model=model, cache_dir=tmp_path)
    assert emb.prefixes == ("query: ", "passage: ") and emb.name == "embed:intfloat/multilingual-e5-base"
    rows = emb.similarities(["incident"], ["incident", "pokuta"])
    assert rows[0][0] > rows[0][1]
    assert model.calls == [["query: incident"], ["passage: incident", "passage: pokuta"]]
    assert emb.encoded == 3
    # second run: everything from the on-disk cache, model not called again
    model2 = StubModel()
    emb2 = SentenceTransformerEmbedder(model=model2, cache_dir=tmp_path)
    assert emb2.similarities(["incident"], ["incident", "pokuta"])[0] == pytest.approx(rows[0])
    assert model2.calls == [] and emb2.encoded == 0
    # other models get no prefixes; cache is per model
    bge = SentenceTransformerEmbedder("BAAI/bge-m3", model=StubModel(), cache_dir=tmp_path)
    assert bge.prefixes == ("", "")
    bge.encode(["x"])
    assert len(list(tmp_path.glob("*.sqlite"))) == 2


def test_vector_cache_roundtrip(tmp_path):
    c = VectorCache(tmp_path, "m")
    c.put_many({"a": [0.5, 0.25], "b": [1.0, 0.0]})
    assert c.get_many(["a", "b", "zz"]) == {"a": [0.5, 0.25], "b": [1.0, 0.0]}
    assert c.key("a") != VectorCache(tmp_path, "other").key("a")


def test_vector_cache_is_namespaced_by_max_seq_length(tmp_path):
    a = SentenceTransformerEmbedder(model=StubModel(), cache_dir=tmp_path)
    b = SentenceTransformerEmbedder(model=StubModel(), cache_dir=tmp_path, max_seq_length=128)
    assert a.cache.path != b.cache.path and a.cache.key("x") != b.cache.key("x")


def test_matcher_with_dense_stub(tmp_path):
    emb = SentenceTransformerEmbedder(model=StubModel(), cache_dir=False)
    res = Matcher(emb, k=1).match(eu_doc(), cz_doc())
    assert len(res.candidates()) == 5
    assert res.candidates()[0].method == "embed:intfloat/multilingual-e5-base"


# --- live -----------------------------------------------------------------------


@pytest.mark.live
def test_live_nis2_art23_lexical():
    from openacquis.adapters import fetch_act
    from openacquis.eurlex import EurLexClient

    nis2 = EurLexClient().document("32022L2555", "cs")
    act = fetch_act("264/2025")
    res = Matcher(k=3).match(nis2, act)
    top = [c.national_id for c in res.candidates() if c.eu_id == "eu:32022L2555:cs:art23.par4.pt_b"]
    assert "cz:264/2025:par16.odst3.pism_a" in top  # 72-hour incident notification
    art = [c.national_id for c in res.rollup("article") if c.eu_id == "eu:32022L2555:cs:art23"]
    assert "cz:264/2025:par16" in art


@pytest.mark.live
def test_live_e5_embedder_runs():
    pytest.importorskip("sentence_transformers")
    emb = SentenceTransformerEmbedder(cache_dir=False, device="cpu")
    rows = emb.similarities(["hlášení incidentu do 24 hodin"],
                            ["incident hlásí do 24 hodin", "pokuta za přestupek"])
    assert rows[0][0] > rows[0][1]
