import io
import json

import pytest

from openacquis.match import (
    BM25Embedder,
    Candidate,
    Channel,
    HybridEmbedder,
    StructuralPrior,
    make_hybrid,
    reciprocal_rank_fusion,
    standardize,
    weighted_fusion,
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


# --- v0.4: BM25, fusion, structural prior --------------------------------------


class FixedEmbedder:
    """Returns a fixed score matrix (ignores the texts); records what it was given."""

    def __init__(self, rows, name="fixed"):
        self.rows = rows
        self.name = name
        self.seen = []

    def similarities(self, queries, passages):
        self.seen.append((list(queries), list(passages)))
        return [list(r) for r in self.rows]


def test_bm25_ranks_shared_words_first():
    rows = BM25Embedder().similarities(
        ["hlášení incidentu do 24 hodin"],
        ["incident se hlásí do 24 hodin", "pokuta za přestupek", "hlášení incidentů Úřadu"])
    assert rows[0][0] > rows[0][1] and rows[0][2] > rows[0][1]
    assert rows[0][1] == 0.0
    assert BM25Embedder().similarities(["x"], []) == [[]]
    res = Matcher(BM25Embedder(), k=1).match(eu_doc(), cz_doc())
    best = {tail(c.eu_id): tail(c.national_id) for c in res.candidates()}
    assert best["art2.par1.pt_a"] == "par5.odst1.pism_a" and res.method == "bm25"


def test_standardize_and_weighted_fusion():
    m = [[1.0, 2.0, 3.0], [3.0, 3.0, 3.0]]
    g = standardize(m, "global")
    assert abs(sum(x for r in g for x in r)) < 1e-9
    q = standardize(m, "query")
    assert q[1] == [0.0, 0.0, 0.0] and q[0][0] < 0 < q[0][2]
    assert standardize(m, "none") == m
    with pytest.raises(ValueError):
        standardize(m, "rank")
    # opposite rankings: the weight decides
    a = [[1.0, 0.0]]
    b = [[0.0, 1.0]]
    assert weighted_fusion([a, b], [2, 1], "query")[0][0] > weighted_fusion([a, b], [2, 1], "query")[0][1]
    assert weighted_fusion([a, b], [1, 2], "query")[0][0] < weighted_fusion([a, b], [1, 2], "query")[0][1]
    with pytest.raises(ValueError):
        weighted_fusion([a, b], [1])


def test_reciprocal_rank_fusion():
    a = [[0.9, 0.5, 0.1]]   # ranks 1, 2, 3
    b = [[0.1, 0.8, 0.7]]   # ranks 3, 1, 2
    f = reciprocal_rank_fusion([a, b], k=60)[0]
    assert f[1] == pytest.approx(1 / 62 + 1 / 61)
    assert max(range(3), key=lambda j: f[j]) == 1  # good in both beats best in one
    assert reciprocal_rank_fusion([a, b], [1, 0])[0] == pytest.approx([1 / 61, 1 / 62, 1 / 63])


def test_hybrid_embedder_keeps_the_first_channels_scale_and_finds_both_hits():
    lex = FixedEmbedder([[0.30, 0.02, 0.01], [0.01, 0.02, 0.03]], "lexical:char3-5")
    dense = FixedEmbedder([[0.80, 0.79, 0.78], [0.78, 0.90, 0.79]], "embed:e5")
    h = HybridEmbedder([lex, dense])
    rows = h.similarities(["q1", "q2"], ["p1", "p2", "p3"])
    assert max(range(3), key=lambda j: rows[0][j]) == 0   # lexical's clear hit survives
    assert max(range(3), key=lambda j: rows[1][j]) == 1   # dense rescues the second query
    flat = [x for r in rows for x in r]
    mu = sum(flat) / len(flat)
    lex_flat = [x for r in lex.rows for x in r]
    assert mu == pytest.approx(sum(lex_flat) / len(lex_flat))  # back on the lexical scale
    assert h.name.startswith("hybrid:z-global(lexical:char3-5*1,embed:e5*1)")
    r = HybridEmbedder([lex, dense], fusion="rrf")
    assert r.name.startswith("hybrid:rrf60(")
    with pytest.raises(ValueError):
        HybridEmbedder([lex], fusion="max")
    with pytest.raises(ValueError):
        HybridEmbedder([])


def test_hybrid_body_channel_and_matcher_integration():
    lex = LexicalEmbedder()
    body = Channel(LexicalEmbedder(), 0.5, "body")
    h = HybridEmbedder([lex, body])
    res = Matcher(h, k=1).match(eu_doc(), cz_doc())
    best = {tail(c.eu_id): tail(c.national_id) for c in res.candidates()}
    assert best["art2.par1.pt_a"] == "par5.odst1.pism_a"
    assert best["art2.par2"] == "par9.odst1"
    assert all(c.status == "candidate" and c.method.startswith("hybrid:") for c in res.candidates())
    assert "@body*0.5" in res.method
    with pytest.raises(ValueError):
        h.similarities(["a"], ["b"])  # body needs units
    with pytest.raises(ValueError):
        Channel(lex, 1, "heading")


def test_body_channel_reads_unit_bodies():
    rec = FixedEmbedder([[0.0] * 5] * 5)
    HybridEmbedder([Channel(rec, 1, "body")]).score_units(
        units(eu_doc()), units(cz_doc()), eu_doc(), {u.id: cz_doc() for u in units(cz_doc())})
    queries, _ = rec.seen[0]
    assert queries[2].startswith("nejpozději do 24 hodin") and "|" not in queries[2]


def test_structural_prior_section_and_siblings():
    eu, cz = eu_doc(), cz_doc()
    eu_u, cz_u = units(eu), units(cz)
    nat = {u.id: cz for u in cz_u}
    ids = [tail(u.id) for u in cz_u]
    assert ids == ["par1.odst1", "par5.odst1", "par5.odst1.pism_a", "par5.odst1.pism_b", "par9.odst1"]
    eids = [tail(u.id) for u in eu_u]
    assert eids == ["art1.par1", "art2.par1", "art2.par1.pt_a", "art2.par1.pt_b", "art2.par2"]
    # pt_b is ambiguous between par1 and par5.odst1.pism_b; its siblings point to § 5
    s = [[0.9, 0.1, 0.1, 0.1, 0.1],
         [0.1, 0.8, 0.3, 0.3, 0.1],
         [0.1, 0.3, 0.9, 0.2, 0.1],
         [0.50, 0.1, 0.1, 0.48, 0.1],
         [0.1, 0.1, 0.1, 0.1, 0.7]]
    assert StructuralPrior().apply(s, eu_u, cz_u, eu, nat) == s
    out = StructuralPrior(sibling_weight=1.0).apply(s, eu_u, cz_u, eu, nat)
    assert max(range(5), key=lambda j: out[3][j]) == 3          # siblings pull pt_b into § 5
    assert out[0] == pytest.approx(s[0])                         # art1.par1 has no sibling: unchanged
    sec = StructuralPrior(section_weight=1.0).apply(s, eu_u, cz_u, eu, nat)
    # the best unit of a § keeps its score; weaker units of a strong § move up
    assert sec[2][2] == pytest.approx(0.9) and sec[2][3] > s[2][3]
    assert StructuralPrior(0.5, 0.25, 1.0).label == "+prior(sec0.5,odst0.25,sib1)"
    # through the hybrid embedder and Matcher
    h = HybridEmbedder([FixedEmbedder(s)], prior=StructuralPrior(sibling_weight=1.0))
    res = Matcher(h, k=1).match(eu, cz)
    assert tail(res.for_eu(eu_u[3].id)[0].national_id) == "par5.odst1.pism_b"
    assert "+prior(sib1)" in res.method


def test_make_hybrid_names():
    h = make_hybrid("hybrid-lex")
    assert isinstance(h, HybridEmbedder) and all(
        not isinstance(c.embedder, SentenceTransformerEmbedder) for c in h.channels)
    stub = SentenceTransformerEmbedder(model=StubModel(), cache_dir=False)
    full = make_hybrid("hybrid", dense=stub)
    assert any(c.embedder is stub for c in full.channels)
    assert sum(c.embedder is stub for c in full.channels) == 2  # text and body channels share the stub
    assert make_hybrid("hybrid-rrf", dense=stub).fusion == "rrf"
    res = Matcher(full, k=1).match(eu_doc(), cz_doc())
    assert len(res.candidates()) == 5
    with pytest.raises(ValueError):
        make_hybrid("hybrid-nope")


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


@pytest.mark.live
def test_live_e5_is_cross_lingual():
    """The dense path compares across languages (DE / SK directive text vs a Czech provision);
    lexical does not. Same-language matching is still the default (directive fetched in the
    country's language)."""
    pytest.importorskip("sentence_transformers")
    emb = SentenceTransformerEmbedder(cache_dir=False, device="cpu", prefixes=("query: ", "query: "))
    cz = ["do 24 hodin po zjištění významného incidentu předloží včasné varování",
          "za přestupek lze uložit pokutu do 10 000 000 Kč"]
    for q in ("unverzüglich, in jedem Fall innerhalb von 24 Stunden nach Kenntnisnahme des erheblichen "
              "Sicherheitsvorfalls, eine Frühwarnung",
              "bez zbytočného odkladu a v každom prípade do 24 hodín od zistenia významného incidentu "
              "včasné varovanie"):
        row = emb.similarities([q], cz)[0]
        assert row[0] > row[1]
