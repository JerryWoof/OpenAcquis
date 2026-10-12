"""Score candidate matching and predicted links against an evaluation set of gold links.

The gold links (:mod:`openacquis.evalset`, e.g. ``data/eval/nis2_cz.jsonl``) pair EU
provisions with national provisions as an official correlation table lists them.
:func:`evaluate` reports three things:

**Retrieval: recall@k.** For every distinct gold EU provision *E*, rank all national
units by their best score against any EU unit inside *E* (*E* itself or its points),
then ask whether a gold national provision of *E* is among the first *k*. Four levels:

=====================  ==========================================  ==============================
level                  EU side                                     national side (ranked, deduped)
=====================  ==========================================  ==============================
``provision``          gold EU provision                           units (odstavec, písmeno ...)
``paragraph``          gold EU provision                           odstavce (§ without odstavce)
``section``            gold EU provision                           §§
``article``            EU article (all gold links under it)        §§
=====================  ==========================================  ==============================

A ranked national ID counts as a hit when it lies on the same path as a gold ID (one is
the other or contains it): the table often cites a whole paragraph where the matcher
ranks its points, and the other way round. ``recall@k`` is the share of gold EU
provisions with at least one hit in the top *k*; ``link_recall@k`` the share of gold
(EU, national) pairs found; ``mrr`` the mean reciprocal rank of the first hit (0 if
none in the top ``max(k)``).

**Predicted links: precision / recall / F1 at a threshold.** The pipeline's own links
(:mod:`openacquis.align`: the best candidate per EU unit at or above the threshold, or
the pairs a judge labels as related). A predicted link (e, n) is correct if some gold
link (E, G) has e equal to E or inside it, and n on the same path as G (``provision``),
or n in the same § as G (``section``). A link on an ancestor of E (an article lead-in,
a paragraph whose points the table lists) does not count for E. Precision is reported twice: over all
predictions, and over predictions for EU units the table covers (``in_scope``); the
table only covers the Cybersecurity Act, so predictions for articles transposed
elsewhere, or addressed to the Commission, cannot be confirmed by it.

**Coverage.** How many gold EU provisions get a predicted link at all (on the provision
itself or one of its parts), and how the
gap and extra-rule candidates relate to the table (a gap candidate the table lists as
transposed is a false alarm).

Scores measure agreement with one government's table of a *bill*, which is itself not
error-free (see ``data/eval/README.md``). They say nothing about whether Czech law
transposes NIS2 correctly.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from .align import RANK_ONLY_NOTE, AlignedProvision, AlignmentResult, align, default_gap_margin, rank_only
from .leftovers import Leftovers, find_leftovers
from .evalset import GoldLink, load_jsonl
from .goldsets import BY_PAIR, GOLD_SETS, GoldSet
from .match import MatchResult, level_id
from .provisions import Document

LEVELS = ("provision", "paragraph", "section", "article")
DEFAULT_KS = (1, 5, 10)


def on_path(a: str, b: str) -> bool:
    """Same provision, or one contains the other (``par16`` / ``par16.odst1``)."""
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def _any_on_path(x: str, ys: Iterable[str]) -> bool:
    return any(on_path(x, y) for y in ys)


def within(e: str, E: str) -> bool:
    """``e`` is ``E`` or lies inside it (``art6.pt3`` within ``art6``, not the other way round).

    A predicted link credits a gold EU provision only from the provision itself or its parts:
    a link on an article lead-in says nothing about each of the article's points.
    """
    return e == E or e.startswith(E + ".")


def load_gold(path: str | Path, acts: Iterable[str] | None = None) -> list[GoldLink]:
    """Gold links from a JSONL file. Records with no national provision (the Slovak sets'
    negative records: "nothing transposes this") are left out; with ``acts``, so are links
    into other national acts (``cz:264/2025:par1`` belongs to ``264/2025``)."""
    links = [g for g in load_jsonl(path) if g.national_id]
    if acts is not None:
        keep = set(acts)
        links = [g for g in links if g.national_id.split(":")[1] in keep]
    return links


# --- retrieval ------------------------------------------------------------------------


@dataclass
class _Ranking:
    """Ranked national IDs (deduplicated at a level) for one gold EU item."""

    eu_key: str
    gold: set[str]
    ranked: list[str]
    first_hit: int | None  # 1-based rank of the first hit
    hits_at: dict[int, int]  # k -> number of gold IDs on the path of a top-k ID


def _rows_under(mr: MatchResult, eu_id: str) -> list[int]:
    return [i for i, u in enumerate(mr.eu_units) if u.id == eu_id or u.id.startswith(eu_id + ".")]


def _max_scores(mr: MatchResult, rows: Sequence[int]) -> list[float]:
    n = len(mr.national_units)
    best = [float("-inf")] * n
    for i in rows:
        r = mr.scores[i]
        for j in range(n):
            if r[j] > best[j]:
                best[j] = r[j]
    return best


def _nat_level(mr: MatchResult, level: str) -> list[str]:
    docs = mr._nat_doc
    if level == "provision":
        return [u.id for u in mr.national_units]
    lv = "paragraph" if level == "paragraph" else "article"
    return [level_id(docs[u.id], u.id, lv) for u in mr.national_units]


def _gold_at(national_docs: dict[str, Document], nid: str, level: str) -> str:
    d = national_docs.get(nid)
    if level == "provision" or d is None:
        return nid
    return level_id(d, nid, "paragraph" if level == "paragraph" else "article")


def _rank(scores: Sequence[float], keys: Sequence[str], depth: int) -> list[str]:
    order = sorted(range(len(scores)), key=lambda j: (-scores[j], j))
    out: list[str] = []
    seen: set[str] = set()
    for j in order:
        if scores[j] == float("-inf"):
            break
        key = keys[j]
        if key not in seen:
            seen.add(key)
            out.append(key)
            if len(out) >= depth:
                break
    return out


def retrieval(
    mr: MatchResult,
    gold: Sequence[GoldLink],
    ks: Sequence[int] = DEFAULT_KS,
    levels: Sequence[str] = LEVELS,
) -> dict[str, dict]:
    """recall@k, link_recall@k and MRR per level (see module docstring)."""
    ks = sorted(set(ks))
    depth = max(ks)
    nat_docs = {u.id: mr._nat_doc[u.id] for u in mr.national_units}
    for d in mr.national_docs:
        for p in d:
            nat_docs.setdefault(p.id, d)
    gold_by_eu: dict[str, set[str]] = {}
    for g in gold:
        gold_by_eu.setdefault(g.eu_id, set()).add(g.national_id)
    out: dict[str, dict] = {}
    for level in levels:
        keys = _nat_level(mr, level)
        if level == "article":
            groups: dict[str, set[str]] = {}
            for e, ns in gold_by_eu.items():
                art = level_id(mr.eu_doc, e, "article") if mr.eu_doc.get(e) else e
                groups.setdefault(art, set()).update(ns)
        else:
            groups = gold_by_eu
        rankings: list[_Ranking] = []
        no_unit = 0
        for e, ns in groups.items():
            g = {_gold_at(nat_docs, n, level) for n in ns}
            rows = _rows_under(mr, e)
            if not rows:
                no_unit += 1
                rankings.append(_Ranking(e, g, [], None, {k: 0 for k in ks}))
                continue
            ranked = _rank(_max_scores(mr, rows), keys, depth)
            first = next((r for r, x in enumerate(ranked, 1) if _any_on_path(x, g)), None)
            hits_at = {k: sum(1 for y in g if _any_on_path(y, ranked[:k])) for k in ks}
            rankings.append(_Ranking(e, g, ranked, first, hits_at))
        n = len(rankings)
        n_links = sum(len(r.gold) for r in rankings)
        res: dict[str, Any] = {"items": n, "gold_links": n_links, "no_eu_unit": no_unit}
        for k in ks:
            res[f"recall@{k}"] = _r(sum(1 for r in rankings if r.first_hit and r.first_hit <= k) / n if n else 0)
            res[f"link_recall@{k}"] = _r(sum(r.hits_at[k] for r in rankings) / n_links if n_links else 0)
        res["mrr"] = _r(sum(1 / r.first_hit for r in rankings if r.first_hit) / n if n else 0)
        res["_rankings"] = rankings
        out[level] = res
    return out


def _r(x: float) -> float:
    """Metrics are stored unrounded (rounding twice shifts the third decimal); format at display time."""
    return float(x)


# --- predicted links ---------------------------------------------------------------


def _prf(tp_pred: int, n_pred: int, tp_gold: int, n_gold: int) -> dict:
    p = tp_pred / n_pred if n_pred else 0.0
    r = tp_gold / n_gold if n_gold else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": _r(p), "recall": _r(r), "f1": _r(f), "predicted": n_pred, "correct": tp_pred,
            "gold": n_gold, "found": tp_gold}


def link_scores(
    predicted: Sequence[tuple[str, str]],
    gold: Sequence[GoldLink],
    eu_doc: Document,
    national_docs: Sequence[Document],
    level: str = "provision",
    eu_scope: Sequence[str] | None = None,
) -> dict:
    """Precision / recall / F1 of predicted (eu_id, national_id) links against gold.

    ``level``: ``provision`` (national IDs on the same path) or ``section`` (same §).
    Gold pairs are deduplicated. ``in_scope`` restricts predictions to EU units on the
    path of a gold EU provision (or to ``eu_scope`` when given).
    """
    nat = {p.id: d for d in national_docs for p in d}

    def sec(n: str) -> str:
        return level_id(nat[n], n, "article") if n in nat else n

    gpairs = sorted({(g.eu_id, g.national_id) for g in gold})
    if level == "section":
        gpairs = sorted({(e, sec(n)) for e, n in gpairs})
        predicted = [(e, sec(n)) for e, n in predicted]
    gold_eu = sorted({e for e, _ in gpairs}) if eu_scope is None else list(eu_scope)
    by_eu: dict[str, list[str]] = {}
    for e, n in gpairs:
        by_eu.setdefault(e, []).append(n)

    def correct(e: str, n: str) -> bool:
        return any(within(e, E) and _any_on_path(n, Gs) for E, Gs in by_eu.items())

    pred = sorted(set(predicted))
    ok = [correct(e, n) for e, n in pred]
    pred_scope = [(x, c) for x, c in zip(pred, ok) if _any_on_path(x[0], gold_eu)]
    found = sum(1 for E, G in gpairs if any(within(e, E) and on_path(n, G) for e, n in pred))
    out = _prf(sum(ok), len(pred), found, len(gpairs))
    ins = _prf(sum(1 for _, c in pred_scope if c), len(pred_scope), found, len(gpairs))
    out["in_scope"] = {k: ins[k] for k in ("precision", "f1", "predicted", "correct")}
    # Share of gold EU provisions with at least one correct predicted link: one link per EU
    # unit can never recall all pairs of a row that lists 20 national provisions.
    eu_found = sum(1 for E, Gs in by_eu.items()
                   if any(within(e, E) and _any_on_path(n, Gs) for e, n in pred))
    out["eu_recall"] = _r(eu_found / len(by_eu)) if by_eu else 0.0
    out["eu_found"] = eu_found
    return out


def threshold_view(result: AlignmentResult, threshold: float, per_eu: int = 1) -> AlignmentResult:
    """The same alignment with links from the score threshold only (judgments ignored)."""
    provs = [AlignedProvision(a.eu_id, a.candidates,
                              links=[c.national_id for c in a.candidates[:per_eu] if c.score >= threshold])
             for a in result.provisions]
    gaps = not rank_only(result.method)
    lo = find_leftovers(result.match, {a.eu_id: a.links for a in provs}, threshold=threshold,
                        acts=result.transposing_acts, gap_margin=default_gap_margin(result.method),
                        gaps=gaps, gap_note=None if gaps else RANK_ONLY_NOTE,
                        national_excluded=result.leftovers.national_excluded if result.leftovers else None)
    return replace(result, provisions=provs, judge=None, threshold=threshold, leftovers=lo,
                   extra_rule_candidates=[x.to_dict() for x in lo.extra_rule_candidates])


def predicted_links(result: AlignmentResult) -> list[tuple[str, str]]:
    return [(a.eu_id, n) for a in result.provisions for n in a.links]


def links_at(mr: MatchResult, threshold: float, per_eu: int = 1) -> list[tuple[str, str]]:
    """Top ``per_eu`` candidates per EU unit with score >= ``threshold`` (no judge)."""
    out = []
    for c in mr.candidates(per_eu):
        if c.score >= threshold:
            out.append((c.eu_id, c.national_id))
    return out


def sweep(
    mr: MatchResult,
    gold: Sequence[GoldLink],
    thresholds: Sequence[float],
    per_eu: int = 1,
    level: str = "provision",
) -> list[dict]:
    """Link scores at each threshold (``in_scope`` precision used for F1 in ``f1_in_scope``)."""
    rows = []
    for t in thresholds:
        s = link_scores(links_at(mr, t, per_eu), gold, mr.eu_doc, mr.national_docs, level)
        p, r = s["in_scope"]["precision"], s["recall"]
        rows.append({"threshold": round(t, 4), **{k: s[k] for k in ("precision", "recall", "f1", "predicted")},
                     "precision_in_scope": p, "f1_in_scope": s["in_scope"]["f1"]})
    return rows


def default_grid(mr: MatchResult, n: int = 40) -> list[float]:
    """Thresholds spanning the range of top-1 scores."""
    tops = sorted(max(r) for r in mr.scores if r)
    if not tops:
        return [0.0]
    lo, hi = tops[0], tops[-1]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def cross_validated_threshold(mr: MatchResult, gold: Sequence[GoldLink], grid: Sequence[float]) -> dict:
    """Two folds by EU article number (odd / even): pick the best in-scope-F1 threshold on one
    fold, score the other; returns the pooled scores. A less optimistic estimate than the best
    threshold on the whole set."""
    eu = mr.eu_doc

    def fold(eu_id: str) -> int:
        art = eu.by_id(level_id(eu, eu_id, "article")) if eu.get(eu_id) else None
        try:
            return int(art.number) % 2 if art is not None else 0
        except ValueError:
            return 0

    gold_f = [[g for g in gold if fold(g.eu_id) == f] for f in (0, 1)]
    chosen = []
    preds: list[tuple[str, str]] = []
    for train, test in ((0, 1), (1, 0)):
        best = max(grid, key=lambda t: sweep(mr, gold_f[train], [t])[0]["f1_in_scope"])
        chosen.append(round(best, 4))
        preds += [(e, n) for e, n in links_at(mr, best) if fold(e) == test]
    s = link_scores(preds, gold, eu, mr.national_docs)
    return {"thresholds": chosen, **{k: s[k] for k in ("precision", "recall", "f1")},
            "precision_in_scope": s["in_scope"]["precision"], "f1_in_scope": s["in_scope"]["f1"]}


# --- coverage ---------------------------------------------------------------------------


def coverage(result: AlignmentResult, gold: Sequence[GoldLink]) -> dict:
    gold_eu = sorted({g.eu_id for g in gold})
    gold_nat = sorted({g.national_id for g in gold})
    units = [a.eu_id for a in result.provisions]
    linked = {a.eu_id for a in result.provisions if a.links}
    covered_units = [u for u in units if _any_on_path(u, gold_eu)]
    gold_with_link = sum(1 for e in gold_eu if any(within(u, e) for u in linked))
    gaps = result.gap_candidates
    gap_in_gold = [g for g in gaps if _any_on_path(g, gold_eu)]
    extras = [x["national_id"] for x in result.extra_rule_candidates]
    extra_in_gold = [x for x in extras if _any_on_path(x, gold_nat)]
    nat_units = [u.id for u in result.match.national_units if u.id.split(":")[1] in result.transposing_acts]
    unlinked = result.unlinked
    lo = result.leftovers
    return {
        "gap_note": lo.gap_note if lo is not None else None,
        "eu_units": len(units),
        "eu_units_covered_by_table": len(covered_units),
        "eu_units_with_link": len(linked),
        "gold_eu_provisions": len(gold_eu),
        "gold_eu_provisions_with_link": gold_with_link,
        "gold_eu_coverage": _r(gold_with_link / len(gold_eu)) if gold_eu else 0.0,
        "unlinked_eu_units": len(unlinked),
        "unlinked_listed_in_table": sum(1 for g in unlinked if _any_on_path(g, gold_eu)),
        "gap_candidates": len(gaps),
        "gap_candidates_listed_in_table": len(gap_in_gold),
        "national_units": len(nat_units),
        "national_units_in_table": sum(1 for u in nat_units if _any_on_path(u, gold_nat)),
        "extra_rule_candidates": len(extras),
        "extra_rule_candidates_listed_in_table": len(extra_in_gold),
    }


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for ``k`` successes out of ``n`` (95 % by default)."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (_r(max(0.0, c - h)), _r(min(1.0, c + h)))


def _article_number(eu_doc: Document, article_id: str) -> int | None:
    art = eu_doc.get(article_id)
    try:
        return int(art.number) if art is not None else None
    except (TypeError, ValueError):
        return None


def gap_by_article(lo: Leftovers, gold_eu: Sequence[str]) -> list[dict]:
    """Gap candidates per EU article: ``{article_id, candidates, listed_in_table}``, largest first.

    Candidates of one article are not independent (a whole article the act transposes
    elsewhere, or not at all, gives many at once), so a list's precision can rest on one
    cluster; this shows how much.
    """
    out: dict[str, dict] = {}
    for g in lo.gap_candidates:
        a = out.setdefault(g.article_id, {"article_id": g.article_id, "candidates": 0, "listed_in_table": 0})
        a["candidates"] += 1
        a["listed_in_table"] += _any_on_path(g.eu_id, gold_eu)
    return sorted(out.values(), key=lambda a: -a["candidates"])


def _leftover_scores(lo: Leftovers, gold_eu: Sequence[str], gold_nat: Sequence[str]) -> dict:
    gaps = [g.eu_id for g in lo.gap_candidates]
    extras = [x.national_id for x in lo.extra_rule_candidates]
    gi = sum(1 for g in gaps if _any_on_path(g, gold_eu))
    xi = sum(1 for x in extras if _any_on_path(x, gold_nat))
    secs = lo.extra_rule_sections()
    si = sum(1 for x in secs if _any_on_path(x["section_id"], gold_nat))
    arts = gap_by_article(lo, gold_eu)
    top = arts[0] if arts else None
    rest_n = len(gaps) - (top["candidates"] if top else 0)
    rest_i = gi - (top["listed_in_table"] if top else 0)
    return {
        "gap_candidates": len(gaps),
        "gap_listed_in_table": gi,
        "gap_precision": _r(1 - gi / len(gaps)) if gaps else None,
        "gap_precision_ci": wilson(len(gaps) - gi, len(gaps)),
        "gap_articles": len(lo.gap_articles()),
        # the article with most candidates, and precision without it (cluster sensitivity)
        "gap_top_article": top["article_id"] if top else None,
        "gap_top_article_candidates": top["candidates"] if top else 0,
        "gap_precision_outside_top_article": _r(1 - rest_i / rest_n) if rest_n else None,
        "gap_candidates_outside_top_article": rest_n,
        "extra_rule_candidates": len(extras),
        "extra_cited_in_table": xi,
        "extra_precision": _r(1 - xi / len(extras)) if extras else None,
        "extra_rule_sections": len(secs),
        "extra_sections_cited_in_table": si,
        # unlinked EU units the scope filter set aside, and how many of them the table maps
        "eu_set_aside": len(lo.eu_out_of_scope),
        "eu_set_aside_listed_in_table": sum(1 for e in lo.eu_out_of_scope if _any_on_path(e, gold_eu)),
        "eu_linked_out_of_scope": len(lo.eu_linked_out_of_scope),
    }


def default_margins(method: str) -> list[float]:
    """Gap margins for the ablation and the margin CV: multiples of the method's default margin."""
    m = default_gap_margin(method)
    return [0.0] + [round(m * f, 6) for f in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)]


def cross_validated_gap_margin(
    result: AlignmentResult,
    gold: Sequence[GoldLink],
    margins: Sequence[float] | None = None,
) -> dict:
    """Out-of-sample precision of the gap list when the margin is chosen on held-out articles.

    The margin is chosen on the training articles by the Wilson lower bound of the gap
    precision (a precision of 2/2 does not beat 12/13), and scored on the test articles.
    Two splits: by EU article parity (``parity``, 2 folds) and leave-one-article-out
    (``by_article``: each article with candidates scored with the margin chosen on all
    others). Conditional on ``result``'s links: the link threshold and the embedder preset
    are **not** re-tuned per fold, so if they were tuned on the same evaluation set this
    is still optimistic. The result depends on ``margins`` (the precision is not monotonic
    in the margin): the grid is reported with it.
    """
    mr = result.match
    eu = mr.eu_doc
    links = {a.eu_id: a.links for a in result.provisions}
    gold_eu = sorted({g.eu_id for g in gold})
    margins = list(margins) if margins is not None else default_margins(result.method)
    # per margin: [(article_id, listed)] of the gap candidates
    cands: dict[float, list[tuple[str, bool]]] = {}
    for m in margins:
        lo = find_leftovers(mr, links, threshold=result.threshold, acts=result.transposing_acts, gap_margin=m)
        cands[m] = [(g.article_id, _any_on_path(g.eu_id, gold_eu)) for g in lo.gap_candidates]

    def lb(m: float, keep: Callable[[str], bool]) -> float:
        xs = [listed for a, listed in cands[m] if keep(a)]
        w = wilson(sum(1 for x in xs if not x), len(xs))
        return w[0] if w else -1.0

    def choose(keep: Callable[[str], bool]) -> float:
        # best lower bound; ties go to the smaller margin (the longer list)
        return max(margins, key=lambda m: (lb(m, keep), -m))

    def pooled(pairs: list[tuple[float, Callable[[str], bool]]]) -> dict:
        n = ok = 0
        for m, test in pairs:
            xs = [listed for a, listed in cands[m] if test(a)]
            n += len(xs)
            ok += sum(1 for x in xs if not x)
        return {"candidates": n, "not_listed": ok, "precision": _r(ok / n) if n else None, "ci": wilson(ok, n)}

    def parity(a: str) -> int | None:
        x = _article_number(eu, a)
        return x % 2 if x is not None else None

    folds = []
    for f in (0, 1):
        def train(a: str, f: int = f) -> bool:
            return parity(a) != f

        def test(a: str, f: int = f) -> bool:
            return parity(a) == f

        folds.append((choose(train), test))
    articles = sorted({a for m in margins for a, _ in cands[m]})
    loao = []
    for art in articles:
        loao.append((choose(lambda a, art=art: a != art), lambda a, art=art: a == art))
    return {
        "margins": margins,
        "threshold": result.threshold,
        "parity": {"chosen": [m for m, _ in folds], **pooled(folds)},
        "by_article": {"articles": len(articles), **pooled(loao)},
        "note": "conditional on the link threshold and preset (not re-tuned per fold)",
    }


def leftover_ablation(
    result: AlignmentResult,
    gold: Sequence[GoldLink],
    margins: Sequence[float] | None = None,
) -> dict:
    """Gap and extra-rule candidates against the table, step by step (docs/evaluation.md).

    Precision here is the share of candidates the table does **not** list (gap) or cite
    (extra rule), i.e. agreement with the table's silence; ``base_rate`` is the same share
    over all EU units / all units of the transposing act, what a random list would get.
    Rows: v0.3 (all unlinked units), + scope / boilerplate filters, + structure, then the
    gap margin (the extra-rule margin stays 0); ``margins`` default to
    :func:`default_margins` (multiples of the method's default margin). Also reports the
    gap candidates per article at the default margin (``gap_by_article``) and the
    cross-validated margin (:func:`cross_validated_gap_margin`). Gap lists are measured
    even for rank-fusion scores, for which :func:`openacquis.align.align` reports none.
    """
    mr = result.match
    links = {a.eu_id: a.links for a in result.provisions}
    gold_eu = sorted({g.eu_id for g in gold})
    gold_nat = sorted({g.national_id for g in gold})
    acts = result.transposing_acts
    t = result.threshold
    margins = list(margins) if margins is not None else default_margins(result.method)
    default_m = default_gap_margin(result.method)
    eu_units = [u.id for u in mr.eu_units]
    nat_units = [u.id for u in mr.national_units if u.id.split(":")[1] in acts]
    rows = []
    excluded = result.leftovers.national_excluded if result.leftovers else {}

    def run(name: str, **kw) -> Leftovers:
        if kw.get("boilerplate_filter", True):
            kw["national_excluded"] = excluded  # set aside with the boilerplate
        lo = find_leftovers(mr, links, threshold=t, acts=acts, **kw)
        rows.append({"step": name, **_leftover_scores(lo, gold_eu, gold_nat)})
        return lo

    run("v0.3: every unlinked unit", gap_margin=0, scope_filter=False, boilerplate_filter=False, structure=False)
    run("+ scope and boilerplate filters", gap_margin=0, structure=False)
    run("+ structure", gap_margin=0)
    for m in margins:
        if m:
            run(f"+ gap margin {m:g}", gap_margin=m)
    lo_default = find_leftovers(mr, links, threshold=t, acts=acts, gap_margin=default_m, national_excluded=excluded)
    return {
        "threshold": t,
        "default_margin": default_m,
        "rank_only": rank_only(result.method),
        "base_rate_gap": _r(sum(1 for u in eu_units if not _any_on_path(u, gold_eu)) / len(eu_units)) if eu_units else None,
        "base_rate_extra": _r(sum(1 for u in nat_units if not _any_on_path(u, gold_nat)) / len(nat_units)) if nat_units else None,
        "rows": rows,
        "gap_by_article": gap_by_article(lo_default, gold_eu),
        "eu_set_aside_listed": sorted(e for e in lo_default.eu_out_of_scope if _any_on_path(e, gold_eu)),
        "margin_cv": cross_validated_gap_margin(result, gold, margins),
    }


# --- judge -----------------------------------------------------------------------------


def judge_scores(result: AlignmentResult, gold: Sequence[GoldLink]) -> dict:
    """How the judge's labels agree with the table on the pairs it judged."""
    gpairs = {(g.eu_id, g.national_id) for g in gold}
    gold_eu = {e for e, _ in gpairs}
    by_eu: dict[str, list[str]] = {}
    for e, n in gpairs:
        by_eu.setdefault(e, []).append(n)

    def in_gold(e: str, n: str) -> bool:
        return any(within(e, E) and _any_on_path(n, Gs) for E, Gs in by_eu.items())

    labels: dict[str, int] = {}
    pos_related = pos = neg_unrelated = neg = 0
    for a in result.provisions:
        if not _any_on_path(a.eu_id, gold_eu):
            continue
        for j in a.judgments:
            labels[j.label.value] = labels.get(j.label.value, 0) + 1
            if in_gold(j.eu_id, j.national_id):
                pos += 1
                pos_related += j.related
            else:
                neg += 1
                neg_unrelated += not j.related
    return {
        "judge": result.judge,
        "judged_pairs_in_scope": pos + neg,
        "labels_in_scope": dict(sorted(labels.items())),
        "table_pairs_judged": pos,
        "table_pairs_labelled_related": _r(pos_related / pos) if pos else None,
        "other_pairs_judged": neg,
        "other_pairs_labelled_unrelated": _r(neg_unrelated / neg) if neg else None,
    }


# --- all together ----------------------------------------------------------------------


def evaluate(
    eu_doc: Document,
    national_docs: Sequence[Document],
    gold: Sequence[GoldLink],
    *,
    embedder: Any = None,
    judge: Any = None,
    ks: Sequence[int] = DEFAULT_KS,
    threshold: float | None = None,
    judge_k: int = 3,
    log: Callable[[str], None] = lambda msg: None,
) -> dict:
    """Run :func:`openacquis.align.align` and score it against ``gold``. Returns a JSON-able report."""
    k = max(max(ks), 5)
    result = align(eu_doc, national_docs, embedder=embedder, judge=judge, k=k, threshold=threshold,
                   judge_k=judge_k, log=log)
    mr = result.match
    t = result.threshold
    plain = threshold_view(result, t) if judge is not None else result
    ret = retrieval(mr, gold, ks)
    grid = default_grid(mr)
    sw = sweep(mr, gold, grid)
    best = max(sw, key=lambda r: r["f1_in_scope"]) if sw else None
    report: dict[str, Any] = {
        "date": date.today().isoformat(),
        "eu": eu_doc.act_id,
        "national": [d.act_id for d in national_docs],
        "method": mr.method,
        "gold_links": len(gold),
        "gold_pairs": len({(g.eu_id, g.national_id) for g in gold}),
        "eu_units": len(mr.eu_units),
        "national_units": len(mr.national_units),
        # units kept out of the candidates (they only cite the directive, see leftovers.transposition_notice)
        "national_excluded": dict(result.leftovers.national_excluded) if result.leftovers else {},
        "ks": list(sorted(set(ks))),
        "retrieval": {lv: {k2: v for k2, v in r.items() if not k2.startswith("_")} for lv, r in ret.items()},
        "threshold": t,
        "links": {
            "provision": link_scores(predicted_links(plain), gold, eu_doc, national_docs, "provision"),
            "section": link_scores(predicted_links(plain), gold, eu_doc, national_docs, "section"),
        },
        "best_threshold": best,
        "cross_validated": cross_validated_threshold(mr, gold, grid),
        "coverage": coverage(plain, gold),
        "leftovers": leftover_ablation(plain, gold),
        "sweep": sw,
    }
    if judge is not None:
        report["judge"] = {
            **judge_scores(result, gold),
            "links": link_scores(predicted_links(result), gold, eu_doc, national_docs, "provision"),
            "links_section": link_scores(predicted_links(result), gold, eu_doc, national_docs, "section"),
            "coverage": coverage(result, gold),
        }
    report["_result"] = result
    report["_rankings"] = ret
    return report


def public(report: dict) -> dict:
    """The report without the in-memory objects (for JSON)."""
    return {k: v for k, v in report.items() if not k.startswith("_")}


def errors(report: dict, level: str = "provision", k: int = 5, limit: int = 20) -> list[dict]:
    """Gold EU provisions with no hit in the top ``k`` (worst first by gold size)."""
    rankings = report["_rankings"][level]["_rankings"]
    misses = [r for r in rankings if not (r.first_hit and r.first_hit <= k)]
    misses.sort(key=lambda r: (-len(r.gold), r.eu_key))
    return [{"eu_id": r.eu_key, "gold": sorted(r.gold), "top": r.ranked[:k], "first_hit": r.first_hit}
            for r in misses[:limit]]


def format_report(report: dict) -> str:
    """Plain-text summary for the CLI."""
    ks = report["ks"]
    lines = [
        f"{report['eu']} x {', '.join(report['national'])}  method={report['method']}",
        f"gold: {report['gold_links']} links ({report['gold_pairs']} pairs); units: {report['eu_units']} EU, "
        f"{report['national_units']} national"
        + (f" ({len(report['national_excluded'])} more only cite the directive: not candidates)"
           if report.get("national_excluded") else ""),
        "",
        "Retrieval (share of gold EU items with a gold national provision in the top k)",
        f"  {'level':<34}" + "".join(f"{'R@' + str(k):>8}" for k in ks) + f"{'MRR':>8}{'items':>7}",
    ]
    names = {
        "provision": "EU provision -> national provision",
        "paragraph": "EU provision -> odstavec",
        "section": "EU provision -> §",
        "article": "EU article -> §",
    }
    for lv, r in report["retrieval"].items():
        lines.append(f"  {names.get(lv, lv):<34}" + "".join(f"{r[f'recall@{k}']:>8.3f}" for k in ks)
                     + f"{r['mrr']:>8.3f}{r['items']:>7}")
    lines.append("")
    lines.append(f"Predicted links (best candidate per EU unit, score >= {report['threshold']:.3f})")
    for lv, s in report["links"].items():
        lines.append(f"  {lv:<10} P={s['precision']:.3f} (in scope {s['in_scope']['precision']:.3f})  "
                     f"R={s['recall']:.3f}  F1={s['f1']:.3f} (in scope {s['in_scope']['f1']:.3f})  "
                     f"[{s['predicted']} predicted, {s['found']}/{s['gold']} gold pairs found; "
                     f"{s['eu_found']} gold EU provisions with a correct link]")
    b = report.get("best_threshold")
    if b:
        lines.append(f"  best threshold on this set: {b['threshold']:.3f} -> P(in scope)={b['precision_in_scope']:.3f} "
                     f"R={b['recall']:.3f} F1(in scope)={b['f1_in_scope']:.3f} (optimistic: tuned on the same data)")
    cv = report.get("cross_validated")
    if cv:
        lines.append(f"  2-fold by article parity: thresholds {cv['thresholds']} -> P(in scope)="
                     f"{cv['precision_in_scope']:.3f} R={cv['recall']:.3f} F1(in scope)={cv['f1_in_scope']:.3f}")
    c = report["coverage"]
    lines += [
        "",
        "Coverage",
        f"  gold EU provisions with a predicted link: {c['gold_eu_provisions_with_link']}/{c['gold_eu_provisions']} "
        f"({c['gold_eu_coverage']:.1%})",
        f"  EU units with a link: {c['eu_units_with_link']}/{c['eu_units']} "
        f"(the table covers {c['eu_units_covered_by_table']})",
        f"  EU units with no link: {c['unlinked_eu_units']} ({c['unlinked_listed_in_table']} of them listed in the table)",
        f"  gap candidates: {c['gap_candidates']} ({c['gap_candidates_listed_in_table']} of them listed in the table)"
        + (f" -- {c['gap_note']}" if c.get("gap_note") else ""),
        f"  extra-rule candidates: {c['extra_rule_candidates']} of {c['national_units']} national units "
        f"({c['extra_rule_candidates_listed_in_table']} of them listed in the table)",
    ]
    lv = report.get("leftovers")
    if lv:
        lines += [
            "",
            "Gap and extra-rule candidates, step by step (precision = share the table does not list / cite;"
            f" base rate {_pct(lv['base_rate_gap'])} / {_pct(lv['base_rate_extra'])})",
            f"  {'step':<34}{'gaps':>6}{'P':>8}{'extras':>8}{'P':>8}{'§§':>5}",
        ]
        for r in lv["rows"]:
            lines.append(f"  {r['step']:<34}{r['gap_candidates']:>6}{_pct(r['gap_precision']):>8}"
                         f"{r['extra_rule_candidates']:>8}{_pct(r['extra_precision']):>8}{r['extra_rule_sections']:>5}")
        if lv.get("rank_only"):
            lines.append("  (rank-fusion scores: align reports no gap list; the gap rows are for comparison only)")
        arts = lv.get("gap_by_article") or []
        if arts:
            top = arts[0]
            rest_n = sum(a["candidates"] for a in arts[1:])
            rest_ok = sum(a["candidates"] - a["listed_in_table"] for a in arts[1:])
            lines.append(f"  at the default margin {lv['default_margin']:g}: {top['candidates']} of "
                         f"{top['candidates'] + rest_n} gap candidates in {top['article_id'].split(':')[-1]} "
                         f"({top['candidates'] - top['listed_in_table']} not listed); outside it "
                         f"{rest_ok}/{rest_n} not listed")
        listed = lv.get("eu_set_aside_listed") or []
        if listed:
            lines.append(f"  unlinked EU units set aside by the scope filter that the table maps: "
                         f"{', '.join(x.split(':')[-1] for x in listed)}")
        mc = lv.get("margin_cv")
        if mc:
            for name, key in (("2-fold by article parity", "parity"), ("leave one article out", "by_article")):
                r = mc[key]
                ci = f" (95% CI {_pct(r['ci'][0])}-{_pct(r['ci'][1])})" if r.get("ci") else ""
                chosen = f", margins {r['chosen']}" if "chosen" in r else ""
                lines.append(f"  margin chosen out of sample, {name}{chosen}: {r['not_listed']}/{r['candidates']} "
                             f"not listed = {_pct(r['precision'])}{ci}")
            lines.append(f"    grid {mc['margins']}; {mc['note']}")
    j = report.get("judge")
    if j:
        lines += [
            "",
            f"Judge ({j['judge']}), pairs for EU provisions the table covers",
            f"  judged: {j['judged_pairs_in_scope']}  labels: {j['labels_in_scope']}",
            f"  table pairs labelled related: {_pct(j['table_pairs_labelled_related'])} of {j['table_pairs_judged']}",
            f"  other pairs labelled unrelated: {_pct(j['other_pairs_labelled_unrelated'])} of {j['other_pairs_judged']}",
            f"  links after judging: P={j['links']['precision']:.3f} (in scope "
            f"{j['links']['in_scope']['precision']:.3f})  R={j['links']['recall']:.3f}  F1={j['links']['f1']:.3f}  "
            f"gold EU provisions with a correct link: {j['links']['eu_found']}",
            f"  gap candidates after judging: {j['coverage']['gap_candidates']} "
            f"({j['coverage']['gap_candidates_listed_in_table']} listed in the table)",
        ]
    lines.append("")
    lines.append("Scores measure agreement with the government's correlation table, not legal correctness.")
    return "\n".join(lines)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def write_json(report: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(public(report), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
