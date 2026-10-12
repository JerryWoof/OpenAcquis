"""Alignment pipeline: directive text + national acts -> candidates -> (judgments) -> gaps and extras.

One call runs the v0.3 steps end to end for one directive and one country::

    from openacquis.align import align, load_documents

    eu, national = load_documents("32022L2555", "cz")          # NIS2 in Czech + Act 264/2025 Sb.
    result = align(eu, national)                               # lexical candidates, no judge
    result.gap_candidates[:3]                                   # EU provisions with nothing above the threshold
    open("nis2_cz.json", "w").write(result.to_json())

Steps

1. **Candidates** (:mod:`openacquis.match`): for every EU unit (paragraph lead-ins,
   points, ...) the ``k`` most similar national units.
2. **Judgments** (optional, :mod:`openacquis.judge`): the top ``judge_k`` candidates
   of every EU unit whose score reaches ``judge_min_score`` are labelled by a judge
   (``RuleJudge`` without a key, ``AnthropicJudge`` with one).
3. **Predicted links**: without a judge, the best ``links_per_eu`` candidates with a
   score at or above ``threshold``; with a judge, the candidates it labels as related.
4. **Leftovers**:

   * *gap candidates*: EU units with no predicted link;
   * *extra-rule candidates*: units of the transposing act(s) that are not the
     predicted link of any EU unit and whose best score against the whole directive
     stays below ``threshold``.

Everything here is a **machine suggestion** (``status="candidate"``): a gap candidate
means "nothing similar enough was found in the acts searched", not that the
directive was left untransposed, and an extra-rule candidate may well be ordinary
national procedure rather than gold-plating. Each item keeps the provision IDs,
scores, and (in the JSON export) the texts and URLs to check it against.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .judge import Judge, Judgment, Passage, provision_status
from .match import Candidate, Embedder, LexicalEmbedder, Matcher, MatchResult, level_id
from .provisions import Document

STATUS = "candidate"  # every output of this module is an unreviewed suggestion

#: Transposing acts searched by default, per (CELEX, country). The evaluation set's
#: own references are added to these (see :func:`default_acts`).
DEFAULT_ACTS: dict[tuple[str, str], list[str]] = {
    ("32022L2555", "cz"): ["264/2025"],
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
}

#: Default evaluation sets per (CELEX, country), relative to the repository root.
GOLD_FILES: dict[tuple[str, str], str] = {
    ("32022L2555", "cz"): "data/eval/nis2_cz.jsonl",
}

REPO_ROOT = Path(__file__).resolve().parents[2]


def default_threshold(method: str) -> float:
    """Threshold for an embedder ``name`` / :attr:`Candidate.method` (``lexical:char3-5`` -> lexical)."""
    base = method.split("+", 1)[0]
    if base in DEFAULT_THRESHOLDS:
        return DEFAULT_THRESHOLDS[base]
    return DEFAULT_THRESHOLDS.get(base.split(":", 1)[0], DEFAULT_THRESHOLDS["lexical"])


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


def default_acts(celex: str, country: str, gold: str | Path | None = None) -> list[str]:
    """National acts to search: :data:`DEFAULT_ACTS` plus every act the evaluation set references."""
    celex, country = celex.upper(), country.lower()
    acts = list(DEFAULT_ACTS.get((celex, country), []))
    path = Path(gold) if gold else gold_path(celex, country)
    if path is not None and path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    act = _act_of(json.loads(line).get("national_id", ""))
                    if act and act not in acts:
                        acts.append(act)
    return acts


def load_documents(
    celex: str,
    country: str,
    acts: Sequence[str] | None = None,
    *,
    version: str | None = "default",
    language: str | None = None,
    eurlex: Any = None,
    national_client: Any = None,
    log: Callable[[str], None] = lambda msg: None,
) -> tuple[Document, list[Document]]:
    """Fetch the directive (in the country's language) and the national acts.

    ``acts`` defaults to :func:`default_acts`. An act the adapter cannot fetch is
    skipped with a log message; if none can be fetched, ``LookupError``.
    ``version="default"`` uses :data:`DEFAULT_VERSIONS` (``None`` = current text).
    Clients are injectable for tests (``EurLexClient`` / ``ESbirkaClient`` shape).
    """
    country = country.lower()
    if country != "cz":
        raise ValueError(f"no national-law adapter for {country.upper()} yet (available: CZ)")
    from .adapters.cz import ESbirkaClient
    from .eurlex import EurLexClient

    celex = celex.upper()
    lang = language or {"cz": "cs"}[country]
    if version == "default":
        version = DEFAULT_VERSIONS.get((celex, country))
    acts = list(acts) if acts else default_acts(celex, country)
    if not acts:
        raise LookupError(f"no default transposing acts for {celex} in {country.upper()}; pass acts explicitly")
    eu = (eurlex or EurLexClient()).document(celex, lang)
    client = national_client or ESbirkaClient()
    docs: list[Document] = []
    for ref in acts:
        try:
            docs.append(client.document(ref, version))
        except Exception as exc:  # unknown act, network, unexpected API answer
            log(f"skipping {ref}: {type(exc).__name__}: {exc}")
    if not docs:
        raise LookupError(f"none of the acts {acts} could be fetched")
    return eu, docs


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

    @property
    def method(self) -> str:
        return self.match.method

    @property
    def gap_candidates(self) -> list[str]:
        return [a.eu_id for a in self.provisions if a.gap]

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
            "eu_units_with_link": n - gaps,
            "gap_candidates": gaps,
            "transposing_act_units": nat_units,
            "extra_rule_candidates": len(self.extra_rule_candidates),
            "predicted_links": sum(len(a.links) for a in self.provisions),
        }
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
                "gap_candidate": a.gap,
                "status": a.status,
            }
            if self.judge:
                row["label"] = a.label
                row["judgments"] = [j.to_dict() for j in a.judgments]
            rows.append(row)
        linked = {nid for a in self.provisions for nid in a.links} | {
            c.national_id for a in self.provisions for c in a.candidates
        }
        return {
            "schema": 1,
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
            "gap_candidates": self.gap_candidates,
            "extra_rule_candidates": self.extra_rule_candidates,
            "national_texts": {nid: nat(nid) for nid in sorted(linked)},
        }

    def to_json(self, indent: int | None = 1, text_limit: int = 600) -> str:
        return json.dumps(self.to_dict(text_limit), ensure_ascii=False, indent=indent)


def _snippet(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if limit <= 0 or len(text) <= limit else text[: limit - 1].rstrip() + "…"


def path_label(doc: Document, pid: str) -> str:
    """Printable reference: ``Čl. 23 odst. 4 písm. a)`` / ``§ 16 odst. 1``."""
    chain = []
    p = doc.get(pid)
    while p is not None:
        chain.append(p)
        p = doc.get(p.parent_id) if p.parent_id else None
    parts = []
    for q in reversed(chain):
        if q.kind in ("cast", "hlava", "dil", "oddil", "pododdil"):
            continue
        if q.kind == "article":
            parts.append(f"Čl. {q.number}" if doc.language == "cs" else q.label)
        elif q.kind == "paragraph":
            parts.append(f"odst. {q.number}" if doc.language == "cs" else f"({q.number})")
        elif q.kind in ("point", "pism"):
            if doc.language != "cs":
                parts.append(f"({q.number})")
            elif q.number.isdigit():  # numbered points (e.g. the definitions in Čl. 6) are cited "bod 1"
                parts.append(f"bod {q.number}")
            else:
                parts.append(f"písm. {q.number})")
        elif q.kind == "odst":
            parts.append(f"odst. {q.number}")
        elif q.kind in ("subpoint", "bod"):
            if doc.language != "cs":
                parts.append(f"({q.number})")
            elif q.kind == "subpoint" and parts and parts[-1].startswith("bod "):
                parts.append(f"písm. {q.number})")  # a letter under a numbered point: "bod 5 písm. a)"
            else:
                parts.append(f"bod {q.number}")
        else:
            parts.append(q.label)
    return " ".join(parts)


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
    log: Callable[[str], None] = lambda msg: None,
    now: datetime | None = None,
) -> AlignmentResult:
    """Run matching (and optionally judging) and collect gap and extra-rule candidates.

    ``threshold`` defaults to :func:`default_threshold` for the embedder.
    ``transposing_acts`` (act IDs) limits the extra-rule candidates; default: all
    national documents passed. With a ``judge``, the top ``judge_k`` candidates of
    each EU unit scoring at least ``judge_min_score`` (default: no minimum) are judged,
    and the related ones become the predicted links.
    """
    if isinstance(national_docs, Document):
        national_docs = [national_docs]
    national_docs = list(national_docs)
    m = matcher or Matcher(embedder if embedder is not None else LexicalEmbedder(), k=k)
    log(f"matching {eu_doc.act_id} against {', '.join(d.act_id for d in national_docs)} ({m.embedder.name})")
    mr = m.match(eu_doc, national_docs)
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

    acts = list(transposing_acts) if transposing_acts else [d.act_id for d in national_docs]
    extra = extra_rule_candidates(mr, provisions, t, acts)
    created = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return AlignmentResult(eu_doc, national_docs, acts, mr, provisions, t, k,
                           getattr(judge, "model", None) if judge is not None else None, extra, created)


def extra_rule_candidates(
    mr: MatchResult,
    provisions: Sequence[AlignedProvision],
    threshold: float,
    acts: Sequence[str],
) -> list[dict]:
    """National units of ``acts`` that no EU unit links to and that score below ``threshold`` everywhere.

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


def make_judge(name: str) -> Judge | None:
    """``none`` | ``rule`` | ``anthropic``."""
    from .judge import AnthropicJudge, RuleJudge

    if name in ("none", "", None):
        return None
    if name == "rule":
        return RuleJudge()
    if name == "anthropic":
        return AnthropicJudge()
    raise ValueError(f"unknown judge {name!r}; expected none, rule or anthropic")


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
