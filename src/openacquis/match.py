"""Candidate matching: which national provisions may transpose each EU provision.

Given a directive as a :class:`~openacquis.provisions.Document` (e.g. NIS2 in
Czech) and one or more national Documents (e.g. Act 264/2025 Sb.), rank for
every EU provision the national provisions whose text is most similar::

    from openacquis.match import Matcher

    result = Matcher(k=5).match(nis2_cs, [act_264])
    for c in result.candidates():            # leaf level: art23.par4.pt_a -> par16.odst2.pism_a ...
        print(c.eu_id, c.rank, c.national_id, round(c.score, 3))
    by_article = result.rollup("article")    # art23 -> par15, par16, ...
    write_jsonl(by_article, "nis2_cz.jsonl", eu_doc=nis2_cs, national_docs=[act_264])

The output is a list of **candidates**, not findings: a high score means "read
these two texts side by side", nothing more. Every :class:`Candidate` carries
``status="candidate"`` until a human (or the later LLM judge) reviews it, and
the JSONL export keeps the provision IDs, URLs and the compared text so each
suggestion can be checked against the official texts.

Similarity is pluggable through the :class:`Embedder` protocol:

* :class:`LexicalEmbedder` (default, stdlib only, deterministic): TF-IDF over
  character n-grams of lightly normalised Czech words (lower case, diacritics
  stripped, common case endings removed), cosine similarity through an inverted
  index. Works for any pair of texts in the *same* language.
* :class:`SentenceTransformerEmbedder` (optional extra
  ``pip install 'openacquis[embeddings]'``, i.e. ``sentence-transformers``):
  multilingual dense embeddings, default ``intfloat/multilingual-e5-base`` with
  its ``query: `` / ``passage: `` prefixes; ``BAAI/bge-m3`` also works. Vectors
  are cached on disk (``$OPENACQUIS_CACHE/embeddings``) keyed by model and text.

Matching units: by default every provision with its own text (articles'
paragraphs and points, § odstavce and písmena; *leaf-ish* units), embedded with
context: the headings above it and the beginning of each enclosing provision's
text (so ``a)`` under "... předloží: " is read with that lead-in). Coarser
granularities (``"paragraph"``, ``"article"``) embed a whole subtree as one
unit. Leaf scores can be rolled up to paragraph or article level with
:meth:`MatchResult.rollup`.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import sqlite3
import unicodedata
from array import array
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import IO, Any, Protocol, runtime_checkable

from .provisions import Document, Provision

# --- provision kinds ---------------------------------------------------------

# Article-level units on either side (EU article / annex / recital; Czech § / článek / příloha).
ARTICLE_KINDS = {"article", "annex", "recital", "par", "cl", "priloha"}
# Paragraph-level units (EU paragraph; Czech odstavec).
PARAGRAPH_KINDS = {"paragraph", "odst"}
# Never matched: Czech structural units (část, hlava, díl ...) and footnotes.
SKIP_KINDS = {"cast", "hlava", "dil", "oddil", "pododdil", "ppc", "part", "title", "chapter", "section"}

GRANULARITIES = ("leaf", "paragraph", "article")
LEVELS = ("paragraph", "article")

STATUS = "candidate"  # unreviewed machine suggestion; never a finding


# --- output ------------------------------------------------------------------


@dataclass
class Candidate:
    """One ranked suggestion: national provision ``national_id`` may transpose ``eu_id``.

    ``score`` is a similarity (cosine for the built-in embedders, roughly 0..1),
    comparable only within one ``method``; it is not a probability. ``rank``
    starts at 1 for each ``eu_id``. ``level`` is ``leaf``, ``paragraph`` or
    ``article`` (the granularity the IDs are at). ``status`` stays
    ``"candidate"`` until reviewed.
    """

    eu_id: str
    national_id: str
    score: float
    rank: int
    method: str
    level: str = "leaf"
    status: str = STATUS

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Candidate:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def _snippet(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def candidates_to_jsonl(
    candidates: Iterable[Candidate],
    eu_doc: Document | None = None,
    national_docs: Sequence[Document] = (),
    text_limit: int = 400,
) -> str:
    """One candidate per line. With the documents, each line also carries the evidence:
    ``eu_text`` / ``national_text`` (the compared provisions, subtree text, cut to
    ``text_limit`` characters), ``eu_url`` / ``national_url`` and ``national_act``."""
    nat_index: dict[str, Document] = {}
    for d in national_docs:
        for p in d:
            nat_index[p.id] = d
    out = []
    for c in candidates:
        row: dict[str, Any] = c.to_dict()
        if eu_doc is not None and (p := eu_doc.get(c.eu_id)) is not None:
            row["eu_text"] = _snippet(eu_doc.full_text(p.id, sep=" "), text_limit)
            row["eu_url"] = p.url
        nd = nat_index.get(c.national_id)
        if nd is not None:
            q = nd.by_id(c.national_id)
            row["national_act"] = nd.act_id
            row["national_text"] = _snippet(nd.full_text(q.id, sep=" "), text_limit)
            row["national_url"] = q.url
        out.append(json.dumps(row, ensure_ascii=False) + "\n")
    return "".join(out)


def write_jsonl(
    candidates: Iterable[Candidate],
    path: str | Path | IO[str],
    eu_doc: Document | None = None,
    national_docs: Sequence[Document] = (),
    text_limit: int = 400,
) -> None:
    """Write :func:`candidates_to_jsonl` to a path or an open text file."""
    s = candidates_to_jsonl(candidates, eu_doc, national_docs, text_limit)
    if hasattr(path, "write"):
        path.write(s)  # type: ignore[union-attr]
    else:
        Path(path).write_text(s, encoding="utf-8")


def candidates_from_jsonl(lines: Iterable[str]) -> list[Candidate]:
    return [Candidate.from_dict(json.loads(line)) for line in lines if line.strip()]


# --- matching units ----------------------------------------------------------


@dataclass
class Unit:
    """A piece of text to embed: one provision (``id``), possibly with its subtree.

    ``text`` is what gets embedded (context + body); ``body`` is the provision's
    own (or subtree) text without context.
    """

    id: str
    kind: str
    body: str
    text: str
    ancestors: tuple[str, ...] = ()  # enclosing provision IDs, outermost first


def _ancestry(doc: Document, p: Provision) -> list[Provision]:
    chain = []
    seen = set()
    cur = doc.get(p.parent_id) if p.parent_id else None
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        chain.append(cur)
        cur = doc.get(cur.parent_id) if cur.parent_id else None
    return chain[::-1]


def _context(chain: Sequence[Provision], self_heading: str | None, prefix_chars: int) -> str:
    parts: list[str] = []
    for a in chain:
        if a.heading and a.heading not in parts:
            parts.append(a.heading)
    if self_heading and self_heading not in parts:
        parts.append(self_heading)
    for a in chain:
        if a.kind in SKIP_KINDS or not a.text or prefix_chars <= 0:
            continue
        t = " ".join(a.text.split("\n", 1)[0].split())  # first subparagraph only
        parts.append(t if len(t) <= prefix_chars else t[:prefix_chars].rsplit(" ", 1)[0] + " …")
    return " | ".join(parts)


def _subtree_text(doc: Document, pid: str) -> str:
    return " ".join(
        " ".join(x for x in (p.heading, p.text) if x) for _, p in doc.walk(pid) if p.kind not in SKIP_KINDS
    )


def _level_kinds(level: str) -> set[str]:
    if level == "article":
        return ARTICLE_KINDS
    if level == "paragraph":
        return PARAGRAPH_KINDS
    raise ValueError(f"unknown level {level!r}; expected one of {LEVELS}")


def level_id(doc: Document, provision_id: str, level: str) -> str:
    """The enclosing provision at ``level`` ("paragraph" or "article").

    Falls back to the article when there is no paragraph (points straight under
    an article, a § without odstavce), and to the provision itself when neither
    exists (an annex, a bare structural unit)."""
    p = doc.by_id(provision_id)
    chain = _ancestry(doc, p) + [p]
    for kinds in ([_level_kinds(level)] + ([ARTICLE_KINDS] if level == "paragraph" else [])):
        for a in reversed(chain):
            if a.kind in kinds:
                return a.id
    return p.id


def units(
    doc: Document,
    granularity: str = "leaf",
    context: bool = True,
    prefix_chars: int = 200,
    include: Callable[[Provision], bool] | None = None,
) -> list[Unit]:
    """Split a document into matching units.

    ``granularity``:

    * ``"leaf"``: every provision with its own text (a paragraph's lead-in and
      each of its points are separate units);
    * ``"paragraph"``: EU paragraphs / Czech odstavce with their subtree (an
      article or § without paragraphs is one unit);
    * ``"article"``: articles, annexes, §§, články, přílohy with their subtree.

    With ``context`` the embedded text starts with the headings above the unit
    and the first ``prefix_chars`` characters of each enclosing provision's text.
    Structural units (část, hlava ...) and footnotes are skipped; ``include`` can
    filter further (return False to drop a provision).
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"unknown granularity {granularity!r}; expected one of {GRANULARITIES}")
    out: list[Unit] = []
    for p in doc:
        if p.kind in SKIP_KINDS or (include is not None and not include(p)):
            continue
        chain = _ancestry(doc, p)
        if granularity == "leaf":
            body = " ".join((p.text or "").split())
            if not body:
                continue
        else:
            kinds = _level_kinds(granularity)
            if p.kind not in kinds:
                # paragraph granularity: an article with no paragraph children is its own unit
                if not (granularity == "paragraph" and p.kind in ARTICLE_KINDS
                        and not any(c.kind in PARAGRAPH_KINDS for c in doc.children(p.id))):
                    continue
            if any(a.kind in kinds for a in chain):
                continue  # nested inside a unit already taken (rare)
            body = " ".join(_subtree_text(doc, p.id).split())
            if not body:
                continue
        ctx = _context(chain, p.heading, prefix_chars) if context else ""
        if granularity != "leaf" and context:
            ctx = _context(chain, None, prefix_chars)  # own heading is already in the body
        text = f"{ctx} | {body}" if ctx else body
        out.append(Unit(p.id, p.kind, body, text, tuple(a.id for a in chain)))
    return out


# --- embedders ---------------------------------------------------------------


Rows = Sequence[Sequence[float]]


@runtime_checkable
class Embedder(Protocol):
    """Anything that scores queries (EU units) against passages (national units).

    ``name`` ends up in :attr:`Candidate.method`. ``similarities`` returns one
    row per query with one score per passage (higher = more similar).
    """

    name: str

    def similarities(self, queries: Sequence[str], passages: Sequence[str]) -> Rows: ...


# Czech light stemmer (after Dolamic & Savoy, "Indexing and stemming approaches
# for the Czech language", 2009): remove one case ending, then a possessive.
_CASE_SUFFIXES: tuple[tuple[int, tuple[str, ...]], ...] = (
    (7, ("atech",)),
    (6, ("ětem", "etem", "atům")),
    (5, ("ech", "ich", "ích", "ého", "ěmi", "emi", "ému", "ěte", "ete", "ěti", "eti", "ího", "iho",
         "ími", "ímu", "imu", "ách", "ata", "aty", "ých", "ama", "ami", "ové", "ovi", "ými")),
    (4, ("em", "es", "ém", "ím", "ům", "at", "ám", "os", "us", "ým", "mi", "ou")),
    (3, ("e", "i", "í", "ě", "u", "y", "ů", "a", "o", "á", "é", "ý")),
)
_POSSESSIVE = (5, ("ov", "in", "ův"))
_WORD = re.compile(r"\w+", re.UNICODE)


def czech_stem(word: str) -> str:
    """Strip one Czech case ending and one possessive suffix (``"incidentů"`` -> ``"incident"``)."""
    for min_len, sufs in _CASE_SUFFIXES:
        if len(word) > min_len - 1:
            for s in sufs:
                if word.endswith(s) and len(word) - len(s) >= 2:
                    word = word[: -len(s)]
                    break
            else:
                continue
            break
    min_len, sufs = _POSSESSIVE
    if len(word) > min_len - 1:
        for s in sufs:
            if word.endswith(s):
                return word[: -len(s)]
    return word


def strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_tokens(text: str, stem: bool = True, diacritics: bool = False) -> list[str]:
    """Lower-case words, optionally stemmed (Czech) and with diacritics stripped."""
    toks = _WORD.findall(text.lower())
    if stem:
        toks = [czech_stem(t) for t in toks]
    if not diacritics:
        toks = [strip_diacritics(t) for t in toks]
    return [t for t in toks if t]


class LexicalEmbedder:
    """TF-IDF over character n-grams of normalised words, cosine similarity (stdlib only).

    Each word is stemmed (light Czech stemmer), stripped of diacritics, padded as
    ``<word>`` and cut into n-grams of length ``ngram_range``; short words are
    also kept whole. Weights are sublinear TF (1 + log tf) times smoothed IDF,
    fitted on queries + passages together, then L2-normalised. Features in more
    than ``max_df`` of all texts (``"<pr"``, ``"ch>"`` ...) are dropped: they
    barely move the cosine but dominate the cost of the inverted index.
    Deterministic: the same inputs always give the same scores.
    """

    def __init__(
        self,
        ngram_range: tuple[int, int] = (3, 5),
        stem: bool = True,
        diacritics: bool = False,
        max_df: float = 0.5,
        min_df: int = 1,
    ):
        lo, hi = ngram_range
        if not 1 <= lo <= hi:
            raise ValueError(f"bad ngram_range {ngram_range!r}")
        self.ngram_range = (lo, hi)
        self.stem = stem
        self.diacritics = diacritics
        self.max_df = max_df
        self.min_df = min_df
        self.name = f"lexical:char{lo}-{hi}" + ("" if stem else ":nostem")

    def features(self, text: str) -> dict[str, int]:
        """Raw term counts of the n-gram features of ``text``."""
        lo, hi = self.ngram_range
        counts: dict[str, int] = {}
        for tok in normalize_tokens(text, self.stem, self.diacritics):
            w = f"<{tok}>"
            if len(w) <= hi:
                counts[w] = counts.get(w, 0) + 1
            for n in range(lo, hi + 1):
                for i in range(0, len(w) - n + 1):
                    g = w[i : i + n]
                    counts[g] = counts.get(g, 0) + 1
        return counts

    def vectors(self, texts: Sequence[str]) -> list[dict[str, float]]:
        """L2-normalised TF-IDF vectors (sparse dicts) fitted on ``texts``."""
        raw = [self.features(t) for t in texts]
        n = len(raw)
        df: dict[str, int] = {}
        for c in raw:
            for f in c:
                df[f] = df.get(f, 0) + 1
        max_count = self.max_df * n
        idf = {
            f: math.log((1 + n) / (1 + d)) + 1.0
            for f, d in df.items()
            if d >= self.min_df and (n < 4 or d <= max_count)
        }
        vecs = []
        for c in raw:
            v = {f: (1.0 + math.log(tf)) * idf[f] for f, tf in c.items() if f in idf}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            vecs.append({f: x / norm for f, x in v.items()})
        return vecs

    def similarities(self, queries: Sequence[str], passages: Sequence[str]) -> list[list[float]]:
        vecs = self.vectors(list(queries) + list(passages))
        qv, pv = vecs[: len(queries)], vecs[len(queries) :]
        postings: dict[str, list[tuple[int, float]]] = {}
        for j, v in enumerate(pv):
            for f, w in v.items():
                postings.setdefault(f, []).append((j, w))
        rows = []
        for v in qv:
            acc = [0.0] * len(pv)
            for f, wq in v.items():
                plist = postings.get(f)
                if plist:
                    for j, wp in plist:
                        acc[j] += wq * wp
            rows.append(acc)
        return rows


# --- dense embeddings (optional extra) ---------------------------------------


def default_cache_dir() -> Path:
    """``$OPENACQUIS_CACHE/embeddings``, else ``~/.cache/openacquis/embeddings``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    base = Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"
    return base / "embeddings"


class VectorCache:
    """On-disk vector store: one SQLite file per model namespace, keyed by sha256(namespace, text).

    The namespace must include every setting that changes the vector (see
    :attr:`SentenceTransformerEmbedder.cache_namespace`).

    Vectors are stored as float32 bytes (stdlib :mod:`array`)."""

    def __init__(self, root: Path | str, model: str):
        self.path = Path(root) / (re.sub(r"[^\w.-]+", "_", model) + ".sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.model = model
        self._db = sqlite3.connect(self.path)
        self._db.execute("CREATE TABLE IF NOT EXISTS vec (key TEXT PRIMARY KEY, data BLOB)")

    def key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{text}".encode()).hexdigest()

    def get_many(self, texts: Sequence[str]) -> dict[str, list[float]]:
        keys = {self.key(t): t for t in texts}
        found: dict[str, list[float]] = {}
        ks = list(keys)
        for i in range(0, len(ks), 500):
            chunk = ks[i : i + 500]
            q = f"SELECT key, data FROM vec WHERE key IN ({','.join('?' * len(chunk))})"
            for k, data in self._db.execute(q, chunk):
                a = array("f")
                a.frombytes(data)
                found[keys[k]] = a.tolist()
        return found

    def put_many(self, items: dict[str, Sequence[float]]) -> None:
        self._db.executemany(
            "INSERT OR REPLACE INTO vec (key, data) VALUES (?, ?)",
            [(self.key(t), array("f", [float(x) for x in v]).tobytes()) for t, v in items.items()],
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


E5_PREFIXES = ("query: ", "passage: ")


class SentenceTransformerEmbedder:
    """Multilingual dense embeddings via ``sentence-transformers`` (optional extra).

    ``model_name`` defaults to ``intfloat/multilingual-e5-base``; for E5 models
    the texts get ``query: `` (EU side) and ``passage: `` (national side)
    prefixes, as the model card asks; other models (``BAAI/bge-m3``) get none
    unless ``prefixes`` is given. Vectors are normalised, so the score is the
    cosine. ``model`` injects an already-loaded model (or a test stub with an
    ``encode(texts, batch_size=..., normalize_embeddings=...)`` method).
    ``cache_dir=False`` disables the vector cache.
    """

    def __init__(
        self,
        model_name: str = "intfloat/multilingual-e5-base",
        model: Any = None,
        prefixes: tuple[str, str] | None = None,
        batch_size: int = 32,
        device: str | None = None,
        cache_dir: Path | str | None | bool = None,
        max_seq_length: int | None = 512,
    ):
        self.model_name = model_name
        self.prefixes = prefixes if prefixes is not None else (
            E5_PREFIXES if "e5" in model_name.lower() else ("", "")
        )
        self.batch_size = batch_size
        self.device = device
        self.max_seq_length = max_seq_length
        self._model = model
        self.name = f"embed:{model_name}"
        if cache_dir is False:
            self.cache: VectorCache | None = None
        else:
            root = Path(cache_dir) if cache_dir not in (None, True) else default_cache_dir()
            # max_seq_length truncates the input and so changes the vector: part of the namespace
            self.cache = VectorCache(root, self.cache_namespace)
        self.encoded = 0  # texts actually sent to the model (cache misses)

    @property
    def cache_namespace(self) -> str:
        """Model name plus the settings that change the vectors (vectors are always normalised)."""
        return f"{self.model_name}@len{self.max_seq_length or 'default'}.norm"

    @property
    def model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:  # pragma: no cover - depends on the environment
                raise ImportError(
                    "SentenceTransformerEmbedder needs the optional extra: "
                    "pip install 'openacquis[embeddings]' (sentence-transformers)"
                ) from e
            self._model = SentenceTransformer(self.model_name, device=self.device)
            if self.max_seq_length:
                self._model.max_seq_length = self.max_seq_length
        return self._model

    def encode(self, texts: Sequence[str], kind: str = "passage") -> list[list[float]]:
        """Normalised vectors for ``texts`` (``kind`` = ``"query"`` or ``"passage"``)."""
        prefix = self.prefixes[0] if kind == "query" else self.prefixes[1]
        full = [prefix + t for t in texts]
        cached = self.cache.get_many(full) if self.cache else {}
        todo = list(dict.fromkeys(t for t in full if t not in cached))
        if todo:
            vecs = self.model.encode(todo, batch_size=self.batch_size, normalize_embeddings=True,
                                     show_progress_bar=False)
            new = {t: [float(x) for x in v] for t, v in zip(todo, vecs)}
            self.encoded += len(todo)
            if self.cache:
                self.cache.put_many(new)
            cached.update(new)
        return [cached[t] for t in full]

    def similarities(self, queries: Sequence[str], passages: Sequence[str]) -> list[list[float]]:
        q = self.encode(queries, "query")
        p = self.encode(passages, "passage")
        try:
            import numpy as np
        except ImportError:
            return [[sum(a * b for a, b in zip(u, v)) for v in p] for u in q]
        if not q or not p:
            return [[] for _ in q]
        return (np.asarray(q, dtype=np.float32) @ np.asarray(p, dtype=np.float32).T).tolist()


# --- matching ----------------------------------------------------------------


def _top_k(row: Sequence[float], k: int, min_score: float | None) -> list[tuple[int, float]]:
    # ties broken by document order (lower index first): deterministic
    best = heapq.nsmallest(k, range(len(row)), key=lambda j: (-row[j], j))
    return [(j, row[j]) for j in best if min_score is None or row[j] >= min_score]


@dataclass
class MatchResult:
    """All scores between EU units (rows) and national units (columns)."""

    eu_doc: Document
    national_docs: list[Document]
    eu_units: list[Unit]
    national_units: list[Unit]
    scores: list[list[float]]
    method: str
    granularity: str = "leaf"
    k: int = 5
    _nat_doc: dict[str, Document] = field(default_factory=dict, repr=False)

    def candidates(self, k: int | None = None, min_score: float | None = None) -> list[Candidate]:
        """Top-``k`` national units for every EU unit, EU document order."""
        k = self.k if k is None else k
        out = []
        for u, row in zip(self.eu_units, self.scores):
            for rank, (j, s) in enumerate(_top_k(row, k, min_score), 1):
                out.append(Candidate(u.id, self.national_units[j].id, round(s, 6), rank,
                                     self.method, self.granularity))
        return out

    def for_eu(self, eu_id: str, k: int | None = None) -> list[Candidate]:
        """Candidates for one EU unit (exact ID, or every unit under it if not itself a unit)."""
        return [c for c in self.candidates(k) if c.eu_id == eu_id or c.eu_id.startswith(eu_id + ".")]

    def rollup(
        self,
        level: str = "article",
        national_level: str | None = None,
        agg: str = "mean",
        k: int | None = None,
        min_score: float | None = None,
    ) -> list[Candidate]:
        """Scores between enclosing EU and national provisions at ``level``.

        For EU provision A (e.g. ``art23``) and national provision B (e.g.
        ``par16``): for each EU unit under A take its best score against any
        unit under B, then combine those with ``agg``: ``"mean"`` (default: how
        much of A is covered by B) or ``"max"`` (best single pair).
        ``national_level`` defaults to ``level``.
        """
        if agg not in ("mean", "max"):
            raise ValueError("agg must be 'mean' or 'max'")
        k = self.k if k is None else k
        nat_level = national_level or level
        eu_groups: dict[str, list[int]] = {}
        for i, u in enumerate(self.eu_units):
            eu_groups.setdefault(level_id(self.eu_doc, u.id, level), []).append(i)
        col_group: list[int] = []
        nat_keys: list[str] = []
        nat_pos: dict[str, int] = {}
        for u in self.national_units:
            key = level_id(self._nat_doc[u.id], u.id, nat_level)
            if key not in nat_pos:
                nat_pos[key] = len(nat_keys)
                nat_keys.append(key)
            col_group.append(nat_pos[key])
        out = []
        for a, rows in eu_groups.items():
            totals = [0.0 if agg == "mean" else -math.inf] * len(nat_keys)
            for i in rows:
                best = [-math.inf] * len(nat_keys)
                for j, s in enumerate(self.scores[i]):
                    g = col_group[j]
                    if s > best[g]:
                        best[g] = s
                if agg == "mean":
                    totals = [t + b for t, b in zip(totals, best)]
                else:
                    totals = [max(t, b) for t, b in zip(totals, best)]
            agg_scores = [t / len(rows) for t in totals] if agg == "mean" else totals
            for rank, (j, s) in enumerate(_top_k(agg_scores, k, min_score), 1):
                out.append(Candidate(a, nat_keys[j], round(s, 6), rank,
                                     f"{self.method}+rollup-{agg}", level))
        return out

    def to_jsonl(self, candidates: Iterable[Candidate] | None = None, text_limit: int = 400) -> str:
        """JSONL with evidence (texts, URLs); defaults to :meth:`candidates`."""
        cs = self.candidates() if candidates is None else candidates
        return candidates_to_jsonl(cs, self.eu_doc, self.national_docs, text_limit)


class Matcher:
    """Rank national provisions for each EU provision.

    ``embedder`` defaults to :class:`LexicalEmbedder`. ``granularity``,
    ``context``, ``prefix_chars`` and the include filters are passed to
    :func:`units`. ``k`` is the default number of candidates per EU unit.
    """

    def __init__(
        self,
        embedder: Embedder | None = None,
        granularity: str = "leaf",
        context: bool = True,
        prefix_chars: int = 200,
        k: int = 5,
        include_eu: Callable[[Provision], bool] | None = None,
        include_national: Callable[[Provision], bool] | None = None,
    ):
        self.embedder = embedder if embedder is not None else LexicalEmbedder()
        if granularity not in GRANULARITIES:
            raise ValueError(f"unknown granularity {granularity!r}; expected one of {GRANULARITIES}")
        self.granularity = granularity
        self.context = context
        self.prefix_chars = prefix_chars
        self.k = k
        self.include_eu = include_eu
        self.include_national = include_national

    def match(self, eu_doc: Document, national_docs: Document | Sequence[Document]) -> MatchResult:
        if isinstance(national_docs, Document):
            national_docs = [national_docs]
        national_docs = list(national_docs)
        eu_units = units(eu_doc, self.granularity, self.context, self.prefix_chars, self.include_eu)
        nat_units: list[Unit] = []
        nat_doc: dict[str, Document] = {}
        for d in national_docs:
            for u in units(d, self.granularity, self.context, self.prefix_chars, self.include_national):
                if u.id in nat_doc:
                    raise ValueError(f"national provision id {u.id!r} occurs in two documents")
                nat_doc[u.id] = d
                nat_units.append(u)
        if eu_units and nat_units:
            scores = [list(r) for r in self.embedder.similarities([u.text for u in eu_units],
                                                                  [u.text for u in nat_units])]
        else:
            scores = [[] for _ in eu_units]
        return MatchResult(eu_doc, national_docs, eu_units, nat_units, scores, self.embedder.name,
                           self.granularity, self.k, nat_doc)


def match(
    eu_doc: Document,
    national_docs: Document | Sequence[Document],
    embedder: Embedder | None = None,
    k: int = 5,
    granularity: str = "leaf",
    context: bool = True,
) -> list[Candidate]:
    """Shortcut: top-``k`` :class:`Candidate` list for every EU unit."""
    return Matcher(embedder, granularity, context, k=k).match(eu_doc, national_docs).candidates()
