"""Alignment pipeline: directive text + national acts -> candidates -> (judgments) -> gaps and extras.

One call runs the steps end to end for one directive and one country::

    from openacquis.align import align, load_documents

    eu, national = load_documents("32022L2555", "sk")          # NIS2 in Slovak + Act 69/2018 Z. z.
    result = align(eu, national, embedder=choose_embedder("auto", "sk"))
    result.gap_candidates[:3]                                   # EU provisions with nothing similar
    open("nis2_sk.json", "w").write(result.to_json())

Countries are the adapters in :data:`openacquis.adapters.ADAPTERS` (cz, sk, at, de).
The directive is fetched **in the language of the national text** (cs, sk, de, de), so
the lexical channels compare like with like; IDs of EU provisions are the same in
every language, so evaluation sets and pages carry over.

Steps

1. **Candidates** (:mod:`openacquis.match`): for every EU unit (paragraph lead-ins,
   points, ...) the ``k`` most similar national units.
2. **Judgments** (optional, :mod:`openacquis.judge`): the top ``judge_k`` candidates
   of every EU unit whose score reaches ``judge_min_score`` are labelled by a judge
   (``RuleJudge`` without a key, ``AnthropicJudge`` with one).
3. **Predicted links**: without a judge, the best ``links_per_eu`` candidates with a
   score at or above ``threshold``; with a judge, the candidates it labels as related.
4. **Leftovers** (:mod:`openacquis.leftovers`, v0.4): *gap candidates* (EU units with
   no link whose best score stays clearly below the threshold, after setting aside
   final provisions, provisions addressed only to EU bodies, and lead-ins or points of
   linked lists) and *extra-rule candidates* (units of the **main** transposing act, in
   a § with no link at all, that match nothing, after setting aside definitions,
   penalty lists, transitional, repeal and entry-into-force clauses and amendments of
   other acts). Rank-fusion scores (``hybrid-rrf``) get links but no gap list
   (:func:`rank_only`).

Everything here is a **machine suggestion** (``status="candidate"``): a gap candidate
means "nothing similar enough was found in the acts searched", not that the
directive was left untransposed, and an extra-rule candidate may well be ordinary
national procedure rather than gold-plating. Each item keeps the provision IDs,
scores, and (in the JSON export) the texts and URLs to check it against.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .judge import Judge, Judgment, Passage, provision_status
from .leftovers import (
    Leftovers,
    directive_number,
    find_leftovers,
    is_transposition_article,
    transposition_notice,
)
from .match import Candidate, Embedder, LexicalEmbedder, Matcher, MatchResult, level_id
from .goldsets import GOLD_SETS
from .provisions import Document

STATUS = "candidate"  # every output of this module is an unreviewed suggestion
SCHEMA = 2  # JSON export; 2 = v0.4 (structured gap and extra-rule candidates)

#: Transposing acts searched by default, per (CELEX, country). The **first** act is the
#: main transposing act: only its units can be extra-rule candidates. The evaluation set's
#: own references are added to these (see :func:`default_acts`).
DEFAULT_ACTS: dict[tuple[str, str], list[str]] = {
    ("32022L2555", "cz"): ["264/2025"],  # Zákon č. 264/2025 Sb., o kybernetické bezpečnosti
    ("32022L2555", "sk"): ["69/2018"],  # Zákon č. 69/2018 Z. z. o kybernetickej bezpečnosti
    ("32022L2555", "at"): ["20013065"],  # NISG 2026 (Gesetzesnummer), BGBl. I Nr. 94/2025
    ("32022L2555", "de"): ["bsig_2025"],  # BSIG as re-enacted by BGBl. 2025 I Nr. 301
}

#: Consolidated version used by default, per (CELEX, country): the version the
#: evaluation set was checked against, so IDs line up. ``None`` = current.
DEFAULT_VERSIONS: dict[tuple[str, str], str | None] = {
    ("32022L2555", "cz"): "2025-11-01",
}

#: Score at or above which a candidate becomes a predicted link (no judge). Scores are
#: only comparable within one embedder, so each has its own threshold. Chosen on the
#: NIS2 x CZ evaluation set (docs/evaluation.md): near the threshold with the best link F1
#: (and the 2-fold cross-validated choices), rounded. Tuned on the data it is scored on.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "lexical": 0.20,
    "embed:intfloat/multilingual-e5-base": 0.85,
    "embed:intfloat/multilingual-e5-base:sym": 0.87,
    "embed": 0.85,
    "hybrid": 0.17,  # hybrid-lex (stdlib channels; scores mapped onto the lexical scale)
    "hybrid-dense": 0.14,  # hybrid with e5 channels
    # reciprocal rank fusion: scores are sums of w / (60 + rank), at most 2/61 (about 0.033)
    # for two channels; 0.03 is about where both channels rank the candidate in their top few
    # (best link F1 on NIS2 x CZ: 0.030; 2-fold choices 0.0317 / 0.0306)
    "hybrid-rrf": 0.03,
}

#: How far below the link threshold a gap candidate's best score must stay (see
#: :mod:`openacquis.leftovers`). For scores on the lexical scale (lexical, hybrids); dense
#: scores are squeezed into a narrow band, so their margin is smaller.
DEFAULT_GAP_MARGINS: dict[str, float] = {"lexical": 0.01, "hybrid": 0.01, "embed": 0.005, "hybrid-rrf": 0.001}

#: No extra-rule list when more than this share of the main act's units would be candidates
#: (:func:`openacquis.leftovers.find_leftovers`): the act is broader than the directive. The v0.4
#: pages of single-purpose acts stay at or below a third; general codes (Labour Code, AktG,
#: AWG 2002) reach 50-84 %.
DEFAULT_EXTRA_MAX_SHARE = 0.5

#: Why rank-fusion results get no gap list (see :func:`rank_only`).
RANK_ONLY_NOTE = ("no gap list: reciprocal rank fusion scores only ranks, so every EU unit's best "
                  "candidate scores about the same and a low score does not mean 'nothing similar'")

#: Default evaluation sets per (CELEX, country), relative to the repository root
#: (from :data:`openacquis.goldsets.GOLD_SETS`).
GOLD_FILES: dict[tuple[str, str], str] = {s.key: s.path for s in GOLD_SETS.values()}

#: Embedder names accepted by :func:`choose_embedder` / the command line.
EMBEDDERS = ["auto", "lexical", "e5", "e5-sym", "bge-m3", "bm25", "hybrid-lex", "hybrid", "hybrid-rrf"]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _family(method: str) -> str:
    base = method.split("+", 1)[0]
    if base.startswith("hybrid"):
        if rank_only(method):
            return "hybrid-rrf"
        return "hybrid-dense" if "embed:" in method else "hybrid"
    return base


def rank_only(method: str) -> bool:
    """Scores built from ranks only (reciprocal rank fusion, ``hybrid:rrf60(...)``).

    Such scores have no absolute level: a threshold on them can pick links, but a low best
    score does not mean that nothing similar was found, so :func:`align` reports no gap
    candidates for them (on NIS2 x CZ their gap lists agree with the table's silence less
    often than a random list would).
    """
    return method.startswith("hybrid:rrf") or method == "hybrid-rrf"


def default_threshold(method: str) -> float:
    """Threshold for an embedder ``name`` / :attr:`Candidate.method` (``lexical:char3-5`` -> lexical)."""
    base = _family(method)
    if base in DEFAULT_THRESHOLDS:
        return DEFAULT_THRESHOLDS[base]
    return DEFAULT_THRESHOLDS.get(base.split(":", 1)[0], DEFAULT_THRESHOLDS["lexical"])


def default_gap_margin(method: str) -> float:
    base = _family(method).split(":", 1)[0].replace("-dense", "")
    return DEFAULT_GAP_MARGINS.get(base, DEFAULT_GAP_MARGINS["lexical"])


def gold_path(celex: str, country: str) -> Path | None:
    rel = GOLD_FILES.get((celex.upper(), country.lower()))
    if rel is None:
        return None
    p = REPO_ROOT / rel
    return p if p.exists() else None


def _act_of(provision_id: str) -> str:
    """``cz:264/2025:par1.odst1`` -> ``264/2025``."""
    parts = provision_id.split(":")
    return parts[1] if len(parts) > 2 else ""


def _registry_entry(celex: str, country: str):
    """The directive registry's entry (``data/directives.json``) or None (missing file, unknown directive)."""
    try:
        from .directives import load_registry

        return load_registry().get(celex).country(country)
    except (OSError, KeyError, ValueError):
        return None


def registry_acts(celex: str, country: str) -> list[str]:
    """Main transposing act(s) recorded in the directive registry (``[]`` for ``none`` or unknown)."""
    e = _registry_entry(celex, country)
    return list(e.acts) if e is not None and e.alignable else []


def default_version(celex: str, country: str) -> str | None:
    """:data:`DEFAULT_VERSIONS`, else the registry's ``version``, else the version the pair's evaluation
    set follows (:data:`openacquis.goldsets.BY_PAIR`, so the page and ``openacquis eval`` compare the
    official table with the same text), else ``None`` (current text)."""
    from .goldsets import BY_PAIR

    key = (celex.upper(), country.lower())
    if key in DEFAULT_VERSIONS:
        return DEFAULT_VERSIONS[key]
    e = _registry_entry(*key)
    if e is not None and e.version:
        return e.version
    gs = BY_PAIR.get(key)
    return gs.version if gs is not None else None


def default_acts(celex: str, country: str, gold: str | Path | None = None) -> list[str]:
    """National acts to search: :data:`DEFAULT_ACTS` (else the directive registry's acts) plus every
    act the evaluation set references."""
    celex, country = celex.upper(), country.lower()
    acts = list(DEFAULT_ACTS.get((celex, country)) or registry_acts(celex, country))
    path = Path(gold) if gold else gold_path(celex, country)
    if path is not None and path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    act = _act_of(json.loads(line).get("national_id", ""))
                    if act and act not in acts:
                        acts.append(act)
    return acts


def notified_measures(
    celex: str,
    country: str,
    *,
    cellar: Any = None,
    national_client: Any = None,
) -> list[Any]:
    """CELLAR's notified measures for ``celex`` in ``country``, mapped by the country's adapter.

    Returns :class:`openacquis.adapters.MeasureMapping` items (``status == "mapped"`` ones
    can be fetched). Germany also queries NeuRIS through ``national_client``.
    """
    from .adapters import get_adapter, map_measures
    from .cellar import CellarClient

    a = get_adapter(country)
    cellar = cellar or CellarClient()
    measures = cellar.national_measures(celex, a.alpha3)
    return map_measures(a.code, measures, celex=celex, cellar=cellar, client=national_client)


def load_documents(
    celex: str,
    country: str,
    acts: Sequence[str] | None = None,
    *,
    version: str | None = "default",
    language: str | None = None,
    eurlex: Any = None,
    national_client: Any = None,
    measures: bool | Sequence[Any] = False,
    cellar: Any = None,
    log: Callable[[str], None] = lambda msg: None,
) -> tuple[Document, list[Document]]:
    """Fetch the directive (in the country's language) and the national acts.

    ``acts`` defaults to :func:`default_acts`. With ``measures=True`` every measure CELLAR
    lists as notified for the country and the adapter can map (:func:`notified_measures`)
    is added after them; a sequence of :class:`~openacquis.adapters.MeasureMapping` can be
    passed instead. An act or measure the adapter cannot fetch is skipped with a log
    message; an act already fetched (same act ID) is not fetched twice; if nothing can be
    fetched, ``LookupError``. ``version="default"`` uses :data:`DEFAULT_VERSIONS` (``None`` =
    current text). Clients are injectable for tests (``EurLexClient`` and the adapter's
    client, e.g. ``ESbirkaClient``).
    """
    from .adapters import fetch_measure, get_adapter
    from .eurlex import EurLexClient

    adapter = get_adapter(country)
    code = adapter.code
    celex = celex.upper()
    lang = language or adapter.language
    if version == "default":
        version = default_version(celex, code)
    acts = list(acts) if acts else default_acts(celex, code)
    if not acts and not measures:
        raise LookupError(f"no default transposing acts for {celex} in {code.upper()}; pass acts explicitly "
                          "or use the notified measures")
    eu = (eurlex or EurLexClient()).document(celex, lang)
    client = national_client or adapter.make_client()
    docs: list[Document] = []
    seen: set[str] = set()

    def keep(doc: Document) -> None:
        if doc.act_id in seen:
            log(f"skipping {doc.act_id}: already fetched")
            return
        seen.add(doc.act_id)
        docs.append(doc)

    for ref in acts:
        try:
            keep(client.document(ref, version))
        except Exception as exc:  # unknown act, network, unexpected API answer
            log(f"skipping {ref}: {type(exc).__name__}: {exc}")
    if measures:
        mapped = (notified_measures(celex, code, cellar=cellar, national_client=client)
                  if measures is True else list(measures))
        usable = [m for m in mapped if m.status == "mapped"]
        log(f"{len(mapped)} notified measures, {len(usable)} mapped to {adapter.portal}")
        for m in usable:
            try:
                keep(fetch_measure(code, client, m))
            except Exception as exc:
                log(f"skipping measure {m.ref}: {type(exc).__name__}: {exc}")
    if not docs:
        raise LookupError(f"none of the acts {acts} could be fetched")
    return eu, docs


def choose_embedder(name: str = "auto", language: str = "cs") -> Embedder:
    """The embedder for ``name`` (see :data:`EMBEDDERS`), set up for the texts' ``language``.

    ``auto``: ``hybrid`` (lexical + multilingual-e5) when the ``openacquis[embeddings]`` extra
    is installed, else the stdlib ``hybrid-lex``. The lexical and BM25 channels use the
    light Czech stemmer for Czech and Slovak and no stemmer otherwise (German). Raises
    ``ValueError`` before any text is fetched when a dense model is asked for without the extra.
    """
    from .match import DENSE_HYBRIDS, HYBRIDS, BM25Embedder, make_hybrid

    if name not in EMBEDDERS and "/" not in name:
        raise ValueError(f"unknown embedder {name!r}; expected one of {', '.join(EMBEDDERS)}")
    have_dense = importlib.util.find_spec("sentence_transformers") is not None
    if name == "auto":
        name = "hybrid" if have_dense else "hybrid-lex"
    stem = language in ("cs", "sk")
    stdlib_only = name in ("lexical", "bm25") or (name in HYBRIDS and name not in DENSE_HYBRIDS)
    if not stdlib_only and not have_dense:
        raise ValueError(f"--embedder {name} needs the optional extra: pip install 'openacquis[embeddings]' "
                         "(sentence-transformers); --embedder lexical, bm25 and hybrid-lex work without it")
    if name == "lexical":
        return LexicalEmbedder(stem=stem)
    if name == "bm25":
        return BM25Embedder(stem=stem)
    if name in HYBRIDS:
        return make_hybrid(name, stem=stem)
    return make_embedder(name)


# --- result ----------------------------------------------------------------------


@dataclass
class AlignedProvision:
    """One EU unit with its candidates, judgments and predicted links."""

    eu_id: str
    candidates: list[Candidate]
    judgments: list[Judgment] = field(default_factory=list)
    links: list[str] = field(default_factory=list)  # predicted national IDs, best first
    label: str | None = None  # judge's provision status (same/partial/.../missing), None without judge
    status: str = STATUS

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def gap(self) -> bool:
        return not self.links


@dataclass
class AlignmentResult:
    eu_doc: Document
    national_docs: list[Document]
    transposing_acts: list[str]
    match: MatchResult
    provisions: list[AlignedProvision]
    threshold: float
    k: int
    judge: str | None = None  # judge model, None when not judged
    extra_rule_candidates: list[dict] = field(default_factory=list)
    created_at: str = ""
    leftovers: Leftovers | None = None

    @property
    def method(self) -> str:
        return self.match.method

    @property
    def unlinked(self) -> list[str]:
        """EU units with no predicted link (the v0.3 gap list, before any filtering)."""
        return [a.eu_id for a in self.provisions if a.gap]

    @property
    def gap_candidates(self) -> list[str]:
        """EU units reported as gap candidates (:mod:`openacquis.leftovers`); IDs in document order."""
        if self.leftovers is None:
            return self.unlinked
        return [g.eu_id for g in self.leftovers.gap_candidates]

    def links(self) -> list[Candidate]:
        """Predicted links as candidates (one per EU unit and national ID)."""
        out = []
        for a in self.provisions:
            by_id = {c.national_id: c for c in a.candidates}
            for nid in a.links:
                c = by_id.get(nid)
                if c is not None:
                    out.append(c)
        return out

    def by_eu(self) -> dict[str, AlignedProvision]:
        return {a.eu_id: a for a in self.provisions}

    def summary(self) -> dict:
        n = len(self.provisions)
        gaps = len(self.gap_candidates)
        nat_units = sum(1 for u in self.match.national_units if _act_of(u.id) in self.transposing_acts)
        out = {
            "eu_units": n,
            "national_units": len(self.match.national_units),
            "eu_units_with_link": n - len(self.unlinked),
            "gap_candidates": gaps,
            "transposing_act_units": nat_units,
            "extra_rule_candidates": len(self.extra_rule_candidates),
            "predicted_links": sum(len(a.links) for a in self.provisions),
        }
        if self.leftovers is not None:
            lo = self.leftovers
            out["gap_articles"] = len(lo.gap_articles())
            out["extra_rule_sections"] = len(lo.extra_rule_sections())
            out["eu_units_out_of_scope"] = len(lo.eu_out_of_scope)  # unlinked units set aside
            out["eu_units_linked_out_of_scope"] = len(lo.eu_linked_out_of_scope)
            if lo.gap_note:
                out["gap_note"] = lo.gap_note
            out["national_units_boilerplate"] = len(lo.national_boilerplate)
            out["national_units_excluded"] = len(lo.national_excluded)
            if lo.extra_note:
                out["extra_note"] = lo.extra_note
                out["extra_rule_candidates_suppressed"] = lo.extra_suppressed
        if self.judge:
            labels: dict[str, int] = {}
            for a in self.provisions:
                for j in a.judgments:
                    labels[j.label.value] = labels.get(j.label.value, 0) + 1
            out["judged_pairs"] = sum(labels.values())
            out["judgment_labels"] = dict(sorted(labels.items()))
        return out

    # --- export ---------------------------------------------------------------
    def to_dict(self, text_limit: int = 600) -> dict:
        nat_doc = {p.id: d for d in self.national_docs for p in d}

        def eu_text(pid: str) -> str:
            return _snippet(self.eu_doc.full_text(pid, sep=" "), text_limit)

        def nat(pid: str) -> dict:
            d = nat_doc[pid]
            p = d.by_id(pid)
            return {"id": pid, "act": d.act_id, "label": path_label(d, pid), "url": p.url,
                    "text": _snippet(d.full_text(pid, sep=" "), text_limit)}

        rows = []
        for a in self.provisions:
            p = self.eu_doc.by_id(a.eu_id)
            row: dict[str, Any] = {
                "eu_id": a.eu_id,
                "eu_label": path_label(self.eu_doc, a.eu_id),
                "eu_url": p.url,
                "eu_text": eu_text(a.eu_id),
                "candidates": [
                    {"national_id": c.national_id, "rank": c.rank, "score": c.score,
                     "national_label": path_label(nat_doc[c.national_id], c.national_id)}
                    for c in a.candidates
                ],
                "links": a.links,
                "gap_candidate": a.eu_id in self._gap_set(),
                "status": a.status,
            }
            if self.judge:
                row["label"] = a.label
                row["judgments"] = [j.to_dict() for j in a.judgments]
            rows.append(row)
        linked = {nid for a in self.provisions for nid in a.links} | {
            c.national_id for a in self.provisions for c in a.candidates
        } | {x["national_id"] for x in self.extra_rule_candidates}
        out: dict[str, Any] = {
            "schema": SCHEMA,
            "generator": f"openacquis {__version__}",
            "created_at": self.created_at,
            "status": STATUS,
            "disclaimer": ("Machine suggestions for human review, not legal findings. A gap candidate means no "
                           "similar national provision was found in the acts searched; an extra-rule candidate "
                           "is a national provision that matched nothing in the directive."),
            "eu": {**self.eu_doc.meta(), "provisions": len(self.eu_doc)},
            "national": [{**d.meta(), "provisions": len(d)} for d in self.national_docs],
            "transposing_acts": self.transposing_acts,
            "method": self.method,
            "granularity": self.match.granularity,
            "k": self.k,
            "threshold": self.threshold,
            "judge": self.judge,
            "summary": self.summary(),
            "provisions": rows,
            "gap_candidates": [g.to_dict() for g in self.leftovers.gap_candidates] if self.leftovers else [
                {"eu_id": e, "status": STATUS} for e in self.unlinked],
            "extra_rule_candidates": self.extra_rule_candidates,
        }
        if self.leftovers is not None:
            lo = self.leftovers
            out["leftovers"] = {
                "gap_threshold": lo.gap_threshold,
                "extra_threshold": lo.extra_threshold,
                "gap_articles": lo.gap_articles(),
                "extra_rule_sections": lo.extra_rule_sections(),
                "eu_out_of_scope": lo.eu_out_of_scope,
                "eu_linked_out_of_scope": lo.eu_linked_out_of_scope,
                "gap_note": lo.gap_note,
                "eu_unlinked_not_gap": lo.eu_unlinked_not_gap,
                "national_boilerplate": lo.national_boilerplate,
                "national_excluded": lo.national_excluded,
                "extra_note": lo.extra_note,
                "extra_suppressed": lo.extra_suppressed,
            }
        out["national_texts"] = {nid: nat(nid) for nid in sorted(linked) if nid in nat_doc}
        return out

    def _gap_set(self) -> set[str]:
        return set(self.gap_candidates)

    def to_json(self, indent: int | None = 1, text_limit: int = 600) -> str:
        return json.dumps(self.to_dict(text_limit), ensure_ascii=False, indent=indent)


def _snippet(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if limit <= 0 or len(text) <= limit else text[: limit - 1].rstrip() + "…"


#: How subdivisions are cited, per language of the text: kind -> (prefix, suffix after the number).
_EU_WORDS: dict[str, dict[str, tuple[str, str]]] = {
    "cs": {"article": ("Čl. ", ""), "paragraph": ("odst. ", ""), "point": ("písm. ", ")"),
           "subpoint": ("bod ", ""), "numbered_point": ("bod ", ""), "subpoint_letter": ("písm. ", ")")},
    "sk": {"article": ("Čl. ", ""), "paragraph": ("ods. ", ""), "point": ("písm. ", ")"),
           "subpoint": ("bod ", ""), "numbered_point": ("bod ", ""), "subpoint_letter": ("písm. ", ")")},
    "de": {"article": ("Art. ", ""), "paragraph": ("Abs. ", ""), "point": ("Buchst. ", ""),
           "subpoint": ("Ziff. ", ""), "numbered_point": ("Nr. ", ""), "subpoint_letter": ("Buchst. ", "")},
}
_NATIONAL_WORDS: dict[str, tuple[str, str]] = {
    "odst": ("odst. ", ""), "ods": ("ods. ", ""), "abs": ("Abs. ", ""),
    "pism": ("písm. ", ")"), "lit": ("lit. ", ""), "sublit": ("lit. ", ""), "buchst": ("Buchst. ", ""),
    "dbuchst": ("Doppelbuchst. ", ""), "z": ("Z ", ""), "nr": ("Nr. ", ""),
}
_STRUCTURAL = {"cast", "hlava", "dil", "oddil", "pododdil", "diel", "oddiel", "pododdiel", "teil", "unterteil",
               "hauptstueck", "abschnitt", "unterabschnitt", "kapitel", "unterkapitel", "buch", "titel",
               "untertitel", "gliederung"}


def path_label(doc: Document, pid: str) -> str:
    """Printable reference as lawyers cite it in the text's language.

    EU: ``Čl. 23 odst. 4 písm. a)`` (cs), ``Čl. 23 ods. 4 písm. a)`` (sk), ``Art. 23 Abs. 4 Buchst. a``
    (de), ``Article 23 (4) (a)`` (other languages). National: ``§ 16 odst. 1`` (cz),
    ``§ 12 ods. 3 písm. b)`` (sk), ``§ 32 Abs. 4 lit. a`` (at), ``§ 30 Abs. 1 Nr. 2 Buchst. a`` (de).
    """
    chain = []
    p = doc.get(pid)
    while p is not None:
        chain.append(p)
        p = doc.get(p.parent_id) if p.parent_id else None
    parts: list[str] = []
    eu = doc.source == "eu"
    words = _EU_WORDS.get(doc.language) if eu else None
    segments = set(pid.rsplit(":", 1)[-1].split("."))
    for q in reversed(chain):
        if q.kind in _STRUCTURAL:
            continue
        if not eu and q.id != pid and q.kind in ("cl", "art") and q.id.rsplit(":", 1)[-1].rsplit(".", 1)[-1] not in segments:
            continue  # a "Čl. I" wrapping the §§ of a Slovak act is not part of the citation (§ 24 ods. 3)
        if eu and words is None:  # other languages: printed labels, e.g. "Article 23 (4) (a)"
            parts.append(q.label if q.kind in ("article", "annex") else f"({q.number})")
        elif eu:
            if q.kind == "article":
                pre, suf = words["article"]
                parts.append(f"{pre}{q.number}{suf}")
            elif q.kind == "paragraph":
                pre, suf = words["paragraph"]
                parts.append(f"{pre}{q.number}{suf}")
            elif q.kind == "point":
                # numbered points (e.g. the definitions in Art. 6) are cited "bod 1" / "Nr. 1"
                pre, suf = words["numbered_point"] if q.number.isdigit() else words["point"]
                parts.append(f"{pre}{q.number}{suf}")
            elif q.kind == "subpoint":
                if parts and parts[-1].startswith(words["numbered_point"][0]) and not q.number.isdigit():
                    pre, suf = words["subpoint_letter"]  # a letter under a numbered point: "bod 5 písm. a)"
                else:
                    pre, suf = words["subpoint"]
                parts.append(f"{pre}{q.number}{suf}")
            else:
                parts.append(q.label)
        elif q.kind == "bod":
            parts.append(f"bod {q.number}")
        elif q.kind in _NATIONAL_WORDS:
            pre, suf = _NATIONAL_WORDS[q.kind]
            parts.append(f"{pre}{q.number}{suf}")
        else:
            parts.append(q.label)
    return " ".join(x for x in parts if x)


# --- pipeline ----------------------------------------------------------------------


def align(
    eu_doc: Document,
    national_docs: Document | Sequence[Document],
    *,
    embedder: Embedder | None = None,
    judge: Judge | None = None,
    k: int = 5,
    threshold: float | None = None,
    links_per_eu: int = 1,
    judge_k: int = 3,
    judge_min_score: float | None = None,
    transposing_acts: Sequence[str] | None = None,
    matcher: Matcher | None = None,
    gap_margin: float | None = None,
    exclude_notices: bool = True,
    extra_rules: bool = True,
    extra_note: str | None = None,
    extra_max_share: float | None = DEFAULT_EXTRA_MAX_SHARE,
    log: Callable[[str], None] = lambda msg: None,
    now: datetime | None = None,
) -> AlignmentResult:
    """Run matching (and optionally judging) and collect gap and extra-rule candidates.

    ``threshold`` defaults to :func:`default_threshold` for the embedder, ``gap_margin`` to
    :func:`default_gap_margin` (see :mod:`openacquis.leftovers`).
    ``transposing_acts`` (act IDs) limits the extra-rule candidates; default: the first
    national document (the main transposing act). With a ``judge``, the top ``judge_k`` candidates of
    each EU unit scoring at least ``judge_min_score`` (default: no minimum) are judged,
    and the related ones become the predicted links.

    ``exclude_notices`` keeps national units that only cite the directive or say that the act
    transposes it (:func:`openacquis.leftovers.transposition_notice`) out of the candidates; a
    transposition clause stays a candidate of the directive's "Transposition" article only
    (:func:`openacquis.leftovers.is_transposition_article`), which it answers. They are listed
    in ``leftovers.national_excluded``. ``extra_rules=False`` (registry
    ``"extra_rules": false``) and ``extra_max_share`` (default :data:`DEFAULT_EXTRA_MAX_SHARE`,
    ``None`` = no cap) suppress the extra-rule list for acts broader than the directive;
    ``extra_note`` replaces the default reason given with ``extra_rules=False``.
    """
    if isinstance(national_docs, Document):
        national_docs = [national_docs]
    national_docs = list(national_docs)
    m = matcher or Matcher(embedder if embedder is not None else LexicalEmbedder(), k=k)
    log(f"matching {eu_doc.act_id} against {', '.join(d.act_id for d in national_docs)} ({m.embedder.name})")
    mr = m.match(eu_doc, national_docs)
    excluded: dict[str, str] = {}
    if exclude_notices:
        num = directive_number(eu_doc.act_id)
        for u in mr.national_units:
            why = transposition_notice(mr._nat_doc[u.id], u.id, num)
            if why:
                excluded[u.id] = why
        if excluded:
            # a transposition clause stays a candidate of the directive's "Transposition" article (the
            # official tables pair the two), and only of it; a unit citing the directive is no candidate
            final = [u.id for u in mr.eu_units if is_transposition_article(eu_doc, u.id)]
            notes = [n for n, why in excluded.items() if why == "transposition_note"]
            log(f"{len(excluded)} national units only cite the directive or announce its transposition: "
                "not candidates (transposition clauses: of the Transposition article only)")
            mr = mr.masked([n for n in excluded if n not in notes]).masked(notes, keep_rows=final)
    t = default_threshold(mr.method) if threshold is None else threshold
    by_eu: dict[str, list[Candidate]] = {}
    for c in mr.candidates(k):
        by_eu.setdefault(c.eu_id, []).append(c)

    nat_doc = {p.id: d for d in national_docs for p in d}
    provisions: list[AlignedProvision] = []
    for u in mr.eu_units:
        cands = by_eu.get(u.id, [])
        a = AlignedProvision(u.id, cands)
        if judge is None:
            a.links = [c.national_id for c in cands[:links_per_eu] if c.score >= t]
        provisions.append(a)

    if judge is not None:
        todo = []
        for a in provisions:
            for c in a.candidates[:judge_k]:
                if judge_min_score is None or c.score >= judge_min_score:
                    todo.append((a, c))
        log(f"judging {len(todo)} pairs with {getattr(judge, 'model', type(judge).__name__)}")
        pairs = [
            (Passage.from_provision(eu_doc.by_id(a.eu_id), eu_doc),
             Passage.from_provision(nat_doc[c.national_id].by_id(c.national_id), nat_doc[c.national_id]))
            for a, c in todo
        ]
        judgments = judge.judge_many(pairs)
        for (a, _c), j in zip(todo, judgments):
            a.judgments.append(j)
        for a in provisions:
            label, _best = provision_status(a.judgments)
            a.label = label
            a.links = [j.national_id for j in sorted(a.judgments, key=lambda j: -j.confidence) if j.related]

    acts = list(transposing_acts) if transposing_acts else [national_docs[0].act_id] if national_docs else []
    gaps = not rank_only(mr.method)
    if not gaps:
        log(f"warning: {RANK_ONLY_NOTE}")
    lo = find_leftovers(mr, {a.eu_id: a.links for a in provisions}, threshold=t, acts=acts,
                        gap_margin=default_gap_margin(mr.method) if gap_margin is None else gap_margin,
                        gaps=gaps, gap_note=None if gaps else RANK_ONLY_NOTE,
                        extra_rules=extra_rules, extra_note=extra_note, extra_max_share=extra_max_share,
                        national_excluded=excluded)
    extra = [x.to_dict() for x in lo.extra_rule_candidates]
    created = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return AlignmentResult(eu_doc, national_docs, acts, mr, provisions, t, k,
                           getattr(judge, "model", None) if judge is not None else None, extra, created, lo)


def extra_rule_candidates(
    mr: MatchResult,
    provisions: Sequence[AlignedProvision],
    threshold: float,
    acts: Sequence[str],
) -> list[dict]:
    """The v0.3 extra-rule list, kept for comparison: national units of ``acts`` that no EU unit
    links to and that score below ``threshold`` everywhere (no boilerplate or structure filter;
    :func:`openacquis.leftovers.find_leftovers` is what :func:`align` uses).

    Each item: ``national_id``, ``best_eu_id`` and ``best_score`` (its closest EU unit), ``status``.
    """
    linked = {nid for a in provisions for nid in a.links}
    out = []
    for j, u in enumerate(mr.national_units):
        if _act_of(u.id) not in acts or u.id in linked:
            continue
        best_i, best = -1, float("-inf")
        for i, row in enumerate(mr.scores):
            if row and row[j] > best:
                best_i, best = i, row[j]
        if best >= threshold:
            continue
        out.append({
            "national_id": u.id,
            "best_eu_id": mr.eu_units[best_i].id if best_i >= 0 else None,
            "best_score": round(best, 6) if best_i >= 0 else None,
            "status": STATUS,
        })
    return out


def article_of(doc: Document, provision_id: str) -> str:
    """Enclosing article (EU) or § (CZ)."""
    return level_id(doc, provision_id, "article")


def make_embedder(name: str) -> Embedder:
    """``lexical`` | ``e5`` | ``e5-sym`` | ``bge-m3`` | any sentence-transformers model name."""
    from .match import SentenceTransformerEmbedder

    if name == "lexical":
        return LexicalEmbedder()
    if name == "e5":
        return SentenceTransformerEmbedder("intfloat/multilingual-e5-base")
    if name == "e5-sym":  # "query: " on both sides (symmetric similarity)
        e = SentenceTransformerEmbedder("intfloat/multilingual-e5-base", prefixes=("query: ", "query: "))
        e.name += ":sym"
        return e
    if name == "bge-m3":
        return SentenceTransformerEmbedder("BAAI/bge-m3")
    return SentenceTransformerEmbedder(name)


def make_judge(name: str, country: str = "cz") -> Judge | None:
    """``none`` | ``rule`` | ``anthropic`` (with the prompt for ``country``: cz, sk, at, de)."""
    from .judge import AnthropicJudge, RuleJudge

    if name in ("none", "", None):
        return None
    if name == "rule":
        return RuleJudge()
    if name == "anthropic":
        return AnthropicJudge(country=country)
    raise ValueError(f"unknown judge {name!r}; expected none, rule or anthropic")


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
