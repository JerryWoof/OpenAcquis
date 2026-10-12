#!/usr/bin/env python3
"""Build the JSON data behind the OpenAcquis website (``site/``).

For a curated list of directives this fetches, from CELLAR, the directive
metadata and the national measures each member state has notified, and writes:

    site/data/index.json          one entry per directive, with counts per country
    site/data/<CELEX>.json        the same plus the full list of measures
    site/data/align/<CELEX>_<cc>.json
                                  provision-level alignment pages (NIS2 x CZ, SK, AT, DE): the top
                                  machine suggestion per paragraph (next to the official correlation
                                  table where one exists), gap and extra-rule candidates
    site/data/review/<CELEX>_<cc>.json
                                  the review queue: every machine suggestion with its texts and
                                  whether it has been reviewed (data/reviews/)
    site/data/matrix/<CELEX>.json article x country matrix (from the alignment pages)

When ``data/judgments/<CELEX>_<cc>.jsonl`` exists, each judged machine candidate on the alignment page
gets the LLM judge's label, confidence, rationale and verified quotes (``judge``), and the matrix
counts ``stricter`` / ``different_value`` labels per article. They are merged at build time, so the
committed ``site/static-data/`` copies do not change.
    site/data/dataset/            the open dataset (links.csv, links.jsonl, datapackage.json ...)

The site itself is static HTML/JS that reads these files; nothing else is needed.

Usage::

    python scripts/build_site.py              # live, from the CELLAR SPARQL endpoint
    python scripts/build_site.py --offline    # from bundled samples (scripts/sample_data/)
    python scripts/build_site.py --write-static
                                  # also refresh site/static-data/ (commit it): the
                                  # alignment the builder falls back to when texts
                                  # cannot be fetched

Alignment pages are computed with the stdlib ``hybrid-lex`` embedder (no heavy dependencies).
They need the directive text (CELLAR) and the national act (e-Sbírka, Slov-Lex, RIS,
gesetze-im-internet.de); if either cannot be fetched, or with ``--offline``, the
committed ``site/static-data/`` copy is used instead and the page says when it was
computed.

A failing directive never stops the build: its error is recorded in the data and
shown on the site. The exit code is non-zero only if *every* directive failed,
so a broken endpoint does not deploy an empty site.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from openacquis import __version__
from openacquis.cellar import (
    ENDPOINT,
    CellarClient,
    Fetcher,
    _http_fetch,
    count_by_country,
)
from openacquis.countries import MEMBER_STATES

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "site" / "data"
SAMPLE_DIR = Path(__file__).resolve().parent / "sample_data"
STATIC_DIR = ROOT / "site" / "static-data"
#: LLM judge results (``data/judgments/<celex>_<cc>.jsonl``, from openacquis.judge_batch or
#: scripts/judge_session.py), shown on the alignment pages next to the candidates they judge.
JUDGMENTS_DIR = ROOT / "data" / "judgments"
SCHEMA_VERSION = 1
ALIGNMENT_SCHEMA = 2


@dataclass(frozen=True)
class Entry:
    """A directive shown on the site."""

    celex: str
    short: str
    topic: str
    rank: int | None = None  # registry order; None = act-level only (not in the registry)


#: Shown act-level only (notified measures): directives on the site before the registry that it
#: does not cover. CELEX numbers verified against CELLAR (2026-10).
ACT_LEVEL_ONLY: list[Entry] = [
    Entry("32022L2464", "CSRD", "Corporate sustainability reporting"),
    Entry("32019L1158", "Work-Life Balance Directive", "Employment"),
]


@dataclass(frozen=True)
class AlignmentEntry:
    """A provision-level alignment page: one directive x one country."""

    celex: str
    short: str
    country: str  # ISO alpha-3, as in the measures data
    country_code: str  # adapter code: "cz"
    country_name: str
    act_label: str  # how the main act is cited on the page: "264/2025 Sb.", "NISG 2026"
    gold: str = ""  # evaluation set (relative to the repository root); "" = no official table
    gold_label: str = ""
    gold_url: str = ""
    gold_history_url: str = ""
    status: str = "ok"  # registry status: ok (notified transposition) | baseline (not notified; existing law)
    note: str = ""  # registry note shown on the page (public wording, see public_note)
    override: bool = False  # acts set by hand in the registry
    extra_rules: bool = True  # registry: extra-rule candidates are meaningful (not a general code, not a baseline)
    act_labels: tuple[tuple[str, str], ...] = ()  # registry: how secondary acts are cited (ref -> label)

    @property
    def slug(self) -> str:
        return f"{self.celex}_{self.country_code}"

    @property
    def href(self) -> str:
        return f"alignment.html?id={self.slug}"


@dataclass(frozen=True)
class CountryState:
    """A registry country with nothing to align (status ``none``): shown as such, not as missing data."""

    celex: str
    country: str  # ISO alpha-3
    country_code: str
    country_name: str
    note: str = ""


#: Clauses of a registry note that are developer notes (mapper statuses, code identifiers), not for the public.
_INTERNAL_NOTE = re.compile(
    r"not_found|no_reference|\bmaps? (?:as|to)\b|\bmapped\b|\b[a-z]+_\d{4}\b|'[^']*' /")
#: Said instead when every clause of the note of a hand-set act was internal.
HAND_SET_NOTE = "act chosen by hand: CELLAR's records do not link the notified measures to it"


#: ``extra_note`` of a baseline pair (status ``baseline``: the act was not notified for the directive).
EXTRA_NOTE_BASELINE = ("no extra-rule list: the act aligned is the existing national law, not a notified "
                       "transposition, so provisions that match nothing in the directive say nothing about going beyond it")


def public_note(note: str, *, override: bool = False) -> str:
    """The public part of a registry note: ``;``-separated clauses that read as developer notes
    (``not_found``, ``maps to``, slugs such as ``bgrembg_2015``) are dropped. A registry entry may give
    ``public_note`` instead (used as is)."""
    clauses = [c.strip() for c in (note or "").split(";") if c.strip()]
    kept = [c for c in clauses if not _INTERNAL_NOTE.search(c)]
    if not kept and clauses and override:
        return HAND_SET_NOTE
    return "; ".join(kept)


def _note_of(c) -> str:
    pub = getattr(c, "public_note", None)
    return pub if isinstance(pub, str) and pub else public_note(c.note, override=bool(getattr(c, "override", False)))


def _from_registry() -> tuple[list[Entry], list[AlignmentEntry], list[CountryState]]:
    """Directives, alignment pages and "nothing to align" states from ``data/directives.json``
    (:mod:`openacquis.directives`), with the official correlation tables of :mod:`openacquis.goldsets`."""
    from openacquis.adapters import get_adapter
    from openacquis.directives import COUNTRIES, load_registry
    from openacquis.goldsets import BY_PAIR

    reg = load_registry()
    entries, aligns, states = [], [], []
    for d in sorted(reg, key=lambda x: (x.rank is None, x.rank or 0)):
        entries.append(Entry(d.celex, d.short, d.topic, d.rank))
        for cc in COUNTRIES:
            c = d.country(cc)
            a = get_adapter(cc)
            if not c.alignable:
                states.append(CountryState(d.celex, a.alpha3, cc, a.name, _note_of(c)))
                continue
            gs = BY_PAIR.get((d.celex, cc))
            aligns.append(AlignmentEntry(
                d.celex, d.short, a.alpha3, cc, a.name, c.label or f"{c.acts[0]}{a.act_suffix}",
                gs.path if gs else "", gs.title if gs else "", gs.url if gs else "", gs.history_url if gs else "",
                status=c.status, note=_note_of(c), override=bool(c.override),
                extra_rules=c.reports_extra_rules, act_labels=c.act_labels))
    return entries, aligns, states


_REG_ENTRIES, ALIGNMENTS, COUNTRY_STATES = _from_registry()
#: The directives shown on the site: the registry (data/directives.json), then the act-level-only ones.
DIRECTIVES: list[Entry] = _REG_ENTRIES + [e for e in ACT_LEVEL_ONLY if e.celex not in {x.celex for x in _REG_ENTRIES}]
#: Directives whose alignment pages are computed live by default (the others use site/static-data/,
#: refreshed by scripts/build_alignments.py): NIS2, the seed.
SEED = {"32022L2555"}

#: Directives with an article x country matrix page.
MATRICES = sorted({a.celex for a in ALIGNMENTS})
#: Embedder of the published pages: stdlib only, so the Pages workflow needs no extra.
SITE_EMBEDDER = "hybrid-lex"
REVIEW_SCHEMA = 1
MATRIX_SCHEMA = 1


# --- fetchers -----------------------------------------------------------------


def with_retries(
    fetch: Fetcher,
    retries: int = 3,
    backoff: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda msg: None,
) -> Fetcher:
    """Wrap ``fetch`` so transient failures are retried with exponential backoff."""

    def wrapped(query: str) -> dict:
        for attempt in range(retries + 1):
            try:
                return fetch(query)
            except Exception as exc:  # network, HTTP 5xx, timeouts, bad JSON
                if attempt == retries:
                    raise
                wait = backoff * 2**attempt
                log(f"    retry {attempt + 1}/{retries} in {wait:.0f}s ({exc})")
                sleep(wait)
        raise AssertionError("unreachable")

    return wrapped


def with_cache(fetch: Fetcher, cache_dir: Path, ttl_hours: float = 24.0) -> Fetcher:
    """Cache SPARQL responses on disk, keyed by query hash (handy for local reruns)."""
    cache_dir.mkdir(parents=True, exist_ok=True)

    def wrapped(query: str) -> dict:
        path = cache_dir / (hashlib.sha256(query.encode()).hexdigest()[:24] + ".json")
        if path.exists() and time.time() - path.stat().st_mtime < ttl_hours * 3600:
            return json.loads(path.read_text(encoding="utf-8"))
        result = fetch(query)
        path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result

    return wrapped


def sample_fetcher(sample_dir: Path = SAMPLE_DIR) -> Fetcher:
    """Answer queries from saved SPARQL results in ``sample_dir/<CELEX>.json``.

    Each file holds ``{"directive": <sparql json>, "measures": <sparql json>}``.
    """

    def fetch(query: str) -> dict:
        m = re.search(r'resource_legal_id_celex "(3\d{4}L\d{4})"', query)
        if not m:
            raise LookupError("offline sample fetcher: no CELEX in query")
        path = sample_dir / f"{m.group(1)}.json"
        if not path.exists():
            raise LookupError(f"no offline sample for {m.group(1)}")
        data = json.loads(path.read_text(encoding="utf-8"))
        key = "measures" if "measure_national_implementing" in query else "directive"
        return data[key]

    return fetch


def sample_celexes(sample_dir: Path = SAMPLE_DIR) -> set[str]:
    return {p.stem for p in sample_dir.glob("3*.json")}


# --- data shaping -------------------------------------------------------------


def _measure_row(m) -> dict:
    return {
        "country": m.country,
        "reference": m.reference,
        "title": m.title,
        "act_type": m.act_type,
        "journal": m.journal,
        "journal_date": m.journal_date,
        "notified": m.notified,
        "uri": m.uri,
    }


def build_directive(client: CellarClient, entry: Entry, fetch_measures: bool = True) -> dict:
    """Fetch one directive. Never raises: errors are returned in ``"error"``.

    ``fetch_measures=False`` (an offline build without a bundled sample for it): the record has no
    CELLAR data and ``measures_available`` is false; the page says so instead of showing zeros."""
    record: dict = {
        "celex": entry.celex,
        "short": entry.short,
        "topic": entry.topic,
        "title": None,
        "date": None,
        "transposition_deadlines": [],
        "eurlex_url": f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{entry.celex}",
        "eurlex_nim_url": f"https://eur-lex.europa.eu/legal-content/EN/NIM/?uri=CELEX:{entry.celex}",
        "total_measures": 0,
        "countries_with_measures": 0,
        "counts": {code: 0 for code in MEMBER_STATES},
        "measures": [],
        "alignments": [
            {"country": a.country, "country_name": a.country_name, "country_code": a.country_code, "href": a.href,
             "official": bool(a.gold), "review_href": f"review.html?id={a.slug}", "act": a.act_label,
             "status": a.status, "note": a.note}
            for a in ALIGNMENTS if a.celex == entry.celex
        ],
        # registry countries with nothing to align (e.g. no statutory minimum wage): shown as such
        "not_aligned": [
            {"country": c.country, "country_name": c.country_name, "country_code": c.country_code, "note": c.note}
            for c in COUNTRY_STATES if c.celex == entry.celex
        ],
        "provision_level": entry.rank is not None,
        "matrix_href": f"matrix.html?celex={entry.celex}" if entry.celex in MATRICES else None,
        "measures_available": fetch_measures,
        "error": None,
    }
    if not fetch_measures:
        return record
    try:
        d = client.directive(entry.celex)
        measures = client.national_measures(entry.celex)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    counts = count_by_country(measures)
    record.update(
        title=d.title,
        date=d.date,
        transposition_deadlines=d.transposition_deadlines,
        total_measures=len(measures),
        countries_with_measures=sum(1 for c in counts if c in MEMBER_STATES),
        measures=sorted(
            (_measure_row(m) for m in measures),
            key=lambda r: (r["country"], r["journal_date"] or "", r["reference"] or ""),
        ),
    )
    # Keep all 27 states (zeros included) in a fixed order; keep any non-member
    # codes CELLAR might return (e.g. historic GBR) rather than silently dropping them.
    record["counts"] = {code: counts.get(code, 0) for code in MEMBER_STATES} | {
        c: n for c, n in counts.items() if c not in MEMBER_STATES
    }
    return record


def index_entry(record: dict) -> dict:
    """The per-directive summary stored in index.json (no measure list)."""
    return {k: v for k, v in record.items() if k != "measures"}


def build_site_data(
    client: CellarClient,
    entries: list[Entry],
    *,
    mode: str = "live",
    now: datetime | None = None,
    log: Callable[[str], None] = lambda msg: None,
    pause: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    skip: set[str] | None = None,
) -> tuple[dict, list[dict]]:
    """Return ``(index, records)`` for ``entries`` (CELLAR is not asked about the CELEX numbers in ``skip``)."""
    records = []
    for i, entry in enumerate(entries):
        if i and pause:
            sleep(pause)  # be gentle with the public endpoint
        log(f"  {entry.celex} {entry.short} ...")
        rec = build_directive(client, entry, fetch_measures=entry.celex not in (skip or set()))
        log(
            f"    error: {rec['error']}"
            if rec["error"]
            else f"    {rec['total_measures']} measures from {rec['countries_with_measures']} member states"
            if rec["measures_available"] else "    measures not in this build (no offline sample)"
        )
        records.append(rec)

    built = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    index = {
        "schema": SCHEMA_VERSION,
        "built_at": built.isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "generator": f"openacquis {__version__}",
        "source": ENDPOINT,
        "member_states": MEMBER_STATES,
        "directives": [index_entry(r) for r in records],
    }
    return index, records


def write_site_data(out_dir: Path, index: dict, records: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json"):
        old.unlink()  # drop directives removed from the curated list
    meta = {k: index[k] for k in ("schema", "built_at", "mode", "source")}
    for rec in records:
        (out_dir / f"{rec['celex']}.json").write_text(
            json.dumps(meta | rec, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# --- alignment pages (provision level) -------------------------------------------

LANGUAGE_NAMES = {"cs": "Czech", "sk": "Slovak", "de": "German", "en": "English"}


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _on_path(a: str, b: str) -> bool:
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def _local(eu_id: str) -> str:
    """``eu:32022L2555:cs:art23.par4`` -> ``art23.par4`` (the same in every language)."""
    return eu_id.split(":", 3)[3] if eu_id.count(":") >= 3 else eu_id


def _secondary_label(entry: AlignmentEntry, act_id: str) -> str:
    """The registry's ``act_labels`` entry for a secondary act (``urhg`` -> ``UrhG``), else ``""``."""
    from openacquis.directives import base_ref

    return next((lab for ref, lab in entry.act_labels if base_ref(ref) == base_ref(act_id)), "")


def alignment_page_data(
    entry: AlignmentEntry,
    result,
    gold: list,
    *,
    now: datetime | None = None,
    mode: str = "live",
    text_limit: int = 2500,
    top: int = 3,
) -> dict:
    """Shape an :class:`openacquis.align.AlignmentResult` (and gold links, if any) into the page's JSON.

    One row per EU paragraph (or per article without paragraphs, or annex). Each row
    has the national provisions the official correlation table lists for it (empty
    without a table), the top machine suggestions (best score of any EU unit inside the
    row, per national unit), and the row's gap candidates. Texts are plain strings; the
    page inserts them with textContent.
    """
    from openacquis.adapters import get_adapter
    from openacquis.align import path_label
    from openacquis.evaluate import retrieval
    from openacquis.match import level_id

    eu = result.eu_doc
    mr = result.match
    nat_doc = {p.id: d for d in result.national_docs for p in d}
    lo = result.leftovers
    gap_ids = set(result.gap_candidates)
    out_of_scope = dict(lo.eu_out_of_scope) if lo else {}
    linked_units = {a.eu_id for a in result.provisions if a.links}
    unit_ids = [u.id for u in mr.eu_units]
    # rows: EU paragraphs, or the article / annex itself when it has no paragraphs
    articles: list[dict] = []
    row_ids: list[str] = []
    art_of_row: dict[str, dict] = {}
    for p in eu.roots():
        if p.kind not in ("article", "annex"):
            continue
        paras = [c for c in eu.children(p.id) if c.kind == "paragraph"]
        art = {"id": p.id, "key": _local(p.id), "label": p.label, "heading": p.heading or "", "url": p.url,
               "official": [], "rows": []}
        for r in paras or [p]:
            row_ids.append(r.id)
            art_of_row[r.id] = art
            units = [u for u in unit_ids if u == r.id or u.startswith(r.id + ".")]
            art["rows"].append({
                "id": r.id, "label": path_label(eu, r.id), "url": r.url,
                "text": _clip(eu.full_text(r.id), text_limit),
                "official": [], "official_eu": [], "machine": [], "agreement": None,
                "units": len(units), "linked_units": sum(1 for u in units if u in linked_units),
                "gaps": [path_label(eu, u) for u in units if u in gap_ids],
                "out_of_scope": sorted({out_of_scope[u] for u in units if u in out_of_scope}),
            })
        articles.append(art)
    rows = {r["id"]: r for a in articles for r in a["rows"]}

    # official correlation table -> rows
    for g in gold:
        target = next((rid for rid in row_ids if g.eu_id == rid or g.eu_id.startswith(rid + ".")), None)
        if target is not None:
            r = rows[target]
            if g.national_id not in r["official"]:
                r["official"].append(g.national_id)
            sub = path_label(eu, g.eu_id) if eu.get(g.eu_id) else g.eu_id
            if g.eu_id != target and sub not in r["official_eu"]:
                r["official_eu"].append(sub)  # the table cites only these parts of the paragraph
            continue
        art_id = level_id(eu, g.eu_id, "article") if eu.get(g.eu_id) else None
        art = next((a for a in articles if a["id"] == art_id), None)
        if art is not None and g.national_id not in art["official"]:
            art["official"].append(g.national_id)

    # machine suggestions per row
    n = len(mr.national_units)
    for rid, r in rows.items():
        idx = [i for i, u in enumerate(mr.eu_units) if u.id == rid or u.id.startswith(rid + ".")]
        if not idx or not n:
            continue
        best = [max(mr.scores[i][j] for i in idx) for j in range(n)]
        order = sorted(range(n), key=lambda j: (-best[j], j))[:top]
        r["machine"] = [{"id": mr.national_units[j].id, "score": round(best[j], 3),
                         "above_threshold": best[j] >= result.threshold} for j in order]

    def section(nid: str) -> str:
        return level_id(nat_doc[nid], nid, "article") if nid in nat_doc else nid

    searched = {d.act_id for d in result.national_docs}

    def in_searched(nid: str) -> bool:
        return nid.count(":") >= 2 and nid.split(":")[1] in searched

    # "article_level": the table cites only the whole article (e.g. "Čl. 35"), which covers this
    # paragraph too, but names no provision for it alone, so there is nothing to compare row by row.
    # "other_act": every provision the table names is in an act that was not searched, so the machine
    # suggestion cannot match it; such rows count neither as matches nor as differences.
    counts = {"match": 0, "same_section": 0, "differs": 0, "other_act": 0, "article_level": 0, "no_table_entry": 0}
    for rid, r in rows.items():
        offs = [o for o in r["official"] if in_searched(o)]
        if not r["official"]:
            r["agreement"] = "article_level" if art_of_row[rid]["official"] else "no_table_entry"
        elif not offs:
            r["agreement"] = "other_act"
        elif not r["machine"]:
            r["agreement"] = "differs"
        else:
            m = r["machine"][0]["id"]
            if any(_on_path(m, o) for o in offs):
                r["agreement"] = "match"
            elif section(m) in {section(o) for o in offs}:
                r["agreement"] = "same_section"
            else:
                r["agreement"] = "differs"
        counts[r["agreement"]] += 1

    # extra-rule candidates, grouped by national §
    extra_sections = []
    if lo is not None:
        for sec in lo.extra_rule_sections():
            sid = sec["section_id"]
            d = nat_doc.get(sid)
            sp = d.get(sid) if d is not None else None
            extra_sections.append({
                "id": sid, "label": path_label(d, sid) if sp else sid,
                "heading": (sp.heading or "") if sp else "", "units": sec["units"],
                "whole_section": d is not None and all(
                    u.id in set(sec["units"]) for u in mr.national_units
                    if nat_doc.get(u.id) is d and level_id(d, u.id, "article") == sid),
            })
    extras_best = {x["national_id"]: x for x in result.extra_rule_candidates}
    gap_articles = []
    if lo is not None:
        for ga in lo.gap_articles():
            a = eu.get(ga["article_id"])
            gap_articles.append({"id": ga["article_id"], "key": _local(ga["article_id"]),
                                 "label": a.label if a else ga["article_id"],
                                 "units": [{"id": u, "label": path_label(eu, u)} for u in ga["units"]],
                                 "whole_article": ga["whole_article"]})

    used = {x for r in rows.values() for x in r["official"]} | {m["id"] for r in rows.values() for m in r["machine"]}
    used |= {x for a in articles for x in a["official"]}
    used |= {u for sec in extra_sections for u in sec["units"]}
    national = {}
    for nid in sorted(used):
        d = nat_doc.get(nid)
        if d is None:  # cited by the official table in an act that was not searched: no text here
            national[nid] = {"label": nid.split(":", 2)[-1], "act": nid.split(":")[1], "text": "", "url": None,
                             "not_searched": not in_searched(nid)}
            continue
        national[nid] = {"label": path_label(d, nid), "act": d.act_id, "url": d.by_id(nid).url,
                         "text": _clip(d.full_text(nid), text_limit),
                         "section": path_label(d, level_id(d, nid, "article"))}
        if nid in extras_best:
            x = extras_best[nid]
            national[nid]["closest_eu"] = path_label(eu, x["best_eu_id"]) if x.get("best_eu_id") else None
            national[nid]["closest_score"] = round(x["best_score"], 3) if x.get("best_score") is not None else None
    summary = {
        "rows": len(rows),
        "rows_with_official": len(rows) - counts["no_table_entry"] if gold else 0,
        **counts,
        "rows_with_link": sum(1 for r in rows.values() if r["linked_units"]),
        "eu_units": len(mr.eu_units),
        "eu_units_with_link": len(linked_units),
        "gap_candidates": len(gap_ids),
        "gap_articles": len(gap_articles),
        "eu_units_out_of_scope": len(out_of_scope),  # unlinked units set aside (not gap candidates)
        "eu_units_linked_out_of_scope": len(lo.eu_linked_out_of_scope) if lo else 0,
        "extra_rule_candidates": len(result.extra_rule_candidates),
        "extra_rule_sections": len(extra_sections),
        "extra_rule_candidates_suppressed": lo.extra_suppressed if lo else 0,
        "main_act_units": sum(1 for u in mr.national_units if u.id.split(":")[1] in result.transposing_acts),
    }
    if gold:
        ret = retrieval(mr, gold, (1, 5), levels=("provision", "section"))
        summary.update(recall_at_1=ret["provision"]["recall@1"], recall_at_5=ret["provision"]["recall@5"],
                       section_recall_at_5=ret["section"]["recall@5"])
    adapter = get_adapter(entry.country_code)
    built = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    return {
        "schema": ALIGNMENT_SCHEMA,
        "built_at": built.isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "generator": f"openacquis {__version__}",
        "id": entry.slug,
        "celex": entry.celex,
        "short": entry.short,
        "country": entry.country,
        "country_code": entry.country_code,
        "country_name": entry.country_name,
        "eu": {"title": eu.title, "url": eu.url, "language": eu.language,
               "language_name": LANGUAGE_NAMES.get(eu.language, eu.language)},
        "national_language": adapter.language,
        "national": [{"act_id": d.act_id, "title": d.title, "url": d.url, "date": d.date,
                      "label": entry.act_label if i == 0 else
                      (_secondary_label(entry, d.act_id) or f"{d.act_id}{adapter.act_suffix}")}
                     for i, d in enumerate(result.national_docs)],
        "portal": adapter.portal,
        "method": result.method,
        "threshold": result.threshold,
        "gap_threshold": lo.gap_threshold if lo else None,
        "gap_note": lo.gap_note if lo else None,  # set when the method gives no gap list (hybrid-rrf)
        "extra_note": lo.extra_note if lo else None,  # set when no extra-rule list is reported (general code, baseline)
        "has_official": bool(gold),
        "official_source": ({"label": entry.gold_label, "url": entry.gold_url, "history_url": entry.gold_history_url,
                             "links": len(gold)} if gold else None),
        "summary": summary,
        "articles": articles,
        "gap_articles": gap_articles,
        "extra_rule_sections": extra_sections,
        "national_texts": national,
    }


def review_page_data(entry: AlignmentEntry, result, *, reviews: list[dict] | None = None,
                     now: datetime | None = None, mode: str = "live", text_limit: int = 1500) -> dict:
    """Every machine suggestion of an alignment, with the texts a reviewer needs (review.html).

    Suggestions: each predicted link (``link``), each gap candidate (``gap``) and each
    extra-rule candidate (``extra_rule``), with :func:`openacquis.review.suggestion_id`.
    ``reviews`` (records for this directive and country) mark suggestions already reviewed.
    """
    from openacquis.align import path_label
    from openacquis.review import merge, suggestion_id

    eu = result.eu_doc
    nat_doc = {p.id: d for d in result.national_docs for p in d}
    by_eu = {a.eu_id: a for a in result.provisions}
    sugg = []
    for a in result.provisions:
        cands = {c.national_id: c for c in a.candidates}
        for nid in a.links:
            c = cands.get(nid)
            sugg.append({"id": suggestion_id("link", a.eu_id, nid), "kind": "link", "eu_id": a.eu_id,
                         "national_id": nid, "score": round(c.score, 4) if c else None, "rank": c.rank if c else None,
                         "alternatives": [x.national_id for x in a.candidates if x.national_id != nid][:3]})
    if result.leftovers is not None:
        for g in result.leftovers.gap_candidates:
            sugg.append({"id": suggestion_id("gap", g.eu_id, None), "kind": "gap", "eu_id": g.eu_id,
                         "national_id": None, "score": g.best_score, "rank": None,
                         "alternatives": [c.national_id for c in by_eu[g.eu_id].candidates[:3]] if g.eu_id in by_eu else []})
        for x in result.leftovers.extra_rule_candidates:
            sugg.append({"id": suggestion_id("extra_rule", None, x.national_id), "kind": "extra_rule", "eu_id": None,
                         "national_id": x.national_id, "score": x.best_score, "rank": None,
                         "alternatives": [x.best_eu_id] if x.best_eu_id else []})
    eu_ids = {s["eu_id"] for s in sugg if s["eu_id"]} | {e for s in sugg if s["kind"] == "extra_rule" for e in s["alternatives"]}
    nat_ids = {s["national_id"] for s in sugg if s["national_id"]} | {
        n for s in sugg if s["kind"] != "extra_rule" for n in s["alternatives"]}
    eu_texts = {e: {"label": path_label(eu, e), "url": eu.by_id(e).url, "text": _clip(eu.full_text(e), text_limit)}
                for e in sorted(eu_ids) if eu.get(e)}
    nat_texts = {}
    for nid in sorted(nat_ids):
        d = nat_doc.get(nid)
        if d is not None:
            nat_texts[nid] = {"label": path_label(d, nid), "url": d.by_id(nid).url,
                              "text": _clip(d.full_text(nid), text_limit)}
    from openacquis.adapters import get_adapter

    reviewed = {d.suggestion_id: {"status": d.status, "reviewers": d.reviewers}
                for d in merge(reviews or [])}
    built = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    return {
        "schema": REVIEW_SCHEMA,
        "built_at": built.isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "generator": f"openacquis {__version__}",
        "id": entry.slug,
        "celex": entry.celex,
        "short": entry.short,
        "country_code": entry.country_code,
        "country_name": entry.country_name,
        "eu_language": eu.language,
        "national_language": get_adapter(entry.country_code).language,
        "method": result.method,
        "threshold": result.threshold,
        "suggestions": sugg,
        "eu_texts": eu_texts,
        "national_texts": nat_texts,
        "reviewed": reviewed,
    }


def _key_label(key: str) -> str:
    """``art23`` -> ``Art. 23``, ``anx_iii`` -> ``Annex III``."""
    if key.startswith("art"):
        return "Art. " + key[3:]
    if key.startswith("anx_"):
        return "Annex " + key[4:].upper()
    if key.startswith("anx"):
        return "Annex " + key[3:]
    return key


def directive_headings(celex: str, eurlex=None, lang: str = "en") -> dict[str, str]:
    """Article and annex headings of a directive (English by default), keyed ``art23`` / ``anx_i``."""
    from openacquis.eurlex import EurLexClient

    doc = (eurlex or EurLexClient()).document(celex, lang)
    return {_local(p.id): p.heading or "" for p in doc.roots() if p.kind in ("article", "annex")}


def matrix_data(celex: str, pages: list[dict], *, headings: dict[str, str] | None = None,
                now: datetime | None = None, not_aligned: list[dict] | None = None) -> dict:
    """Article x country matrix from the alignment pages of one directive (no recomputation).

    A cell: the machine's most frequent top § for the article's paragraphs (with the best
    score), the §§ the official table lists (where there is a table), how many EU units have
    a link, and how many are gap candidates. Rows follow the directive; columns follow ``pages``.
    """
    rows: dict[str, dict] = {}
    countries = []
    for d in pages:
        nat = d.get("national_texts", {})
        countries.append({"code": d["country_code"], "name": d["country_name"], "id": d["id"],
                          "method": d["method"], "built_at": d["built_at"], "mode": d["mode"],
                          "has_official": d.get("has_official", False),
                          "act": (d.get("national") or [{}])[0].get("label", ""),
                          "status": (d.get("registry") or {}).get("status", "ok")})

        def sec_label(nid: str) -> str:
            n = nat.get(nid) or {}
            return n.get("section") or n.get("label") or nid.split(":", 2)[-1]

        for a in d["articles"]:
            key = a.get("key") or _local(a["id"])
            row = rows.setdefault(key, {"key": key, "label": _key_label(key),
                                        "heading": (headings or {}).get(key) or a["heading"], "cells": {}})
            tops: dict[str, list[float]] = {}
            official: list[str] = []
            units = linked = gaps = 0
            oos = 0
            judged: dict[str, int] = {"related": 0, "stricter": 0, "different_value": 0}
            for r in a["rows"]:
                for m in r["machine"]:
                    lab = (m.get("judge") or {}).get("label")
                    if lab and lab != "unrelated":
                        judged["related"] += 1
                        if lab in judged:
                            judged[lab] += 1
                units += r.get("units", 0)
                linked += r.get("linked_units", 0)
                gaps += len(r.get("gaps", []))
                oos += 1 if r.get("out_of_scope") and not r.get("linked_units") else 0
                if r["machine"] and r["machine"][0]["above_threshold"]:
                    m = r["machine"][0]
                    tops.setdefault(sec_label(m["id"]), []).append(m["score"])
                for o in r["official"]:
                    lab = sec_label(o)
                    if lab not in official:
                        official.append(lab)
            for o in a.get("official", []):
                lab = sec_label(o)
                if lab not in official:
                    official.append(lab)
            top = max(tops.items(), key=lambda kv: (len(kv[1]), max(kv[1])))[0] if tops else None
            row["cells"][d["country_code"]] = {
                "top": top, "top_score": round(max(tops[top]), 3) if top else None,
                "others": sorted(k for k in tops if k != top)[:4],
                "official": official if d.get("has_official") else None,
                "units": units, "linked_units": linked, "gaps": gaps,
                "href": f"alignment.html?id={d['id']}&art={a['id']}",
            }
            if d.get("judge"):  # LLM judge labels of the article's candidates (unreviewed)
                row["cells"][d["country_code"]]["judge"] = judged
    built = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    short = pages[0]["short"] if pages else celex
    return {
        "schema": MATRIX_SCHEMA,
        "built_at": built.isoformat().replace("+00:00", "Z"),
        "generator": f"openacquis {__version__}",
        "celex": celex,
        "short": short,
        "countries": countries,
        "not_aligned": not_aligned or [],
        "articles": list(rows.values()),
    }


def build_alignment(
    entry: AlignmentEntry,
    *,
    log: Callable[[str], None] = lambda msg: None,
    now: datetime | None = None,
    eurlex=None,
    national_client=None,
) -> dict:
    """Fetch the texts, align with the stdlib hybrid embedder and shape the page data (raises on failure).

    Returns ``{"alignment": page data, "review": review queue data}``.
    """
    from openacquis.adapters import get_adapter
    from openacquis.align import DEFAULT_ACTS, align, choose_embedder, load_documents, registry_acts
    from openacquis.evaluate import load_gold

    lang = get_adapter(entry.country_code).language
    # the main act(s) only: not every act an official table cites (load_documents' default adds those)
    acts = DEFAULT_ACTS.get((entry.celex, entry.country_code)) or registry_acts(entry.celex, entry.country_code)
    eu, docs = load_documents(entry.celex, entry.country_code, acts or None, eurlex=eurlex,
                              national_client=national_client, log=log)
    result = align(eu, docs, embedder=choose_embedder(SITE_EMBEDDER, lang), log=log, now=now,
                   extra_rules=entry.extra_rules, extra_note=None if entry.status == "ok" else EXTRA_NOTE_BASELINE)
    gold = load_gold(ROOT / entry.gold) if entry.gold else []
    return {"alignment": alignment_page_data(entry, result, gold, now=now, mode="live"),
            "review": review_page_data(entry, result, reviews=_reviews_for(entry), now=now, mode="live")}


def _reviews_for(entry: AlignmentEntry, reviews_dir: Path | None = None) -> list[dict]:
    """Review records for this directive x country (``data/reviews/``); [] if none or invalid."""
    from openacquis.review import ReviewError, REVIEWS_DIR, load_reviews

    try:
        return [r for r in load_reviews(reviews_dir or REVIEWS_DIR)
                if r["directive"] == entry.celex and r["country"] == entry.country_code]
    except (ReviewError, OSError):
        return []


def _mark_reviewed(review: dict, entry: AlignmentEntry, reviews_dir: Path | None = None) -> dict:
    """Refresh the ``reviewed`` map of review data (also for a static copy built before the reviews)."""
    from openacquis.review import merge

    review = dict(review)
    review["reviewed"] = {d.suggestion_id: {"status": d.status, "reviewers": d.reviewers}
                          for d in merge(_reviews_for(entry, reviews_dir))}
    return review


def write_alignments(
    out_dir: Path,
    entries: list[AlignmentEntry],
    *,
    offline: bool = False,
    write_static: bool = False,
    static_dir: Path = STATIC_DIR,
    reviews_dir: Path | None = None,
    log: Callable[[str], None] = lambda msg: None,
    build: Callable[..., dict] = build_alignment,
    live: set[str] | None = None,
    states: list[CountryState] | None = None,
    judgments_dir: Path | None = JUDGMENTS_DIR,
) -> list[str]:
    """Write ``out_dir/align/<slug>.json`` and ``out_dir/review/<slug>.json`` for each entry, then
    ``out_dir/matrix/<CELEX>.json``. Live first, then the committed static copy. Returns the
    slugs that have no data at all (neither worked).

    ``live``: the directives computed live (``None`` = all); the others use the static copy
    directly. ``states``: registry countries with nothing to align, listed on the matrix.
    ``judgments_dir``: where LLM judge results are read from (``None``: none shown)."""
    align_dir = out_dir / "align"
    review_dir = out_dir / "review"
    matrix_dir = out_dir / "matrix"
    for d in (align_dir, review_dir, matrix_dir):
        d.mkdir(parents=True, exist_ok=True)
    missing = []
    pages: dict[str, list[dict]] = {}
    for e in entries:
        data = review = None
        if not offline and (live is None or e.celex in live):
            log(f"  alignment {e.slug} ...")
            try:
                built = build(e, log=lambda m: log("    " + m))
                data, review = built["alignment"], built.get("review")
            except Exception as exc:
                log(f"    could not compute live ({type(exc).__name__}: {exc}); using the static copy")
        if data is not None and write_static:
            static_dir.mkdir(parents=True, exist_ok=True)
            (static_dir / f"{e.slug}.json").write_text(
                json.dumps({**data, "mode": "static"}, ensure_ascii=False, indent=None, separators=(",", ":")),
                encoding="utf-8")
            if review is not None:
                (static_dir / f"{e.slug}.review.json").write_text(
                    json.dumps({**review, "mode": "static", "reviewed": {}}, ensure_ascii=False,
                               separators=(",", ":")), encoding="utf-8")
            log(f"    wrote {static_dir / (e.slug + '.json')}")
        if data is None:
            path = static_dir / f"{e.slug}.json"
            if not path.exists():
                log(f"    no static copy at {path}")
                missing.append(e.slug)
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format", 1) != 1:  # format 2 (texts dropped): the pages cannot fetch texts yet
                log(f"    {path.name}: format {data.get('format')} is not supported by the pages; skipped")
                missing.append(e.slug)
                continue
            data["mode"] = "static"
            rpath = static_dir / f"{e.slug}.review.json"
            review = json.loads(rpath.read_text(encoding="utf-8")) if rpath.exists() else None
            if review is not None:
                review["mode"] = "static"
        _add_languages(data, review, e)
        _add_registry(data, e)
        if judgments_dir is not None:
            _add_judgments(data, judgments_dir / f"{e.slug}.jsonl", log)
        (align_dir / f"{e.slug}.json").write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        if review is not None:
            review = _mark_reviewed(review, e, reviews_dir)
            # baseline pairs: the review page says the act was not notified and words its question for that
            review["registry"] = {"status": data["registry"]["status"], "note": data["registry"]["note"]}
            (review_dir / f"{e.slug}.json").write_text(
                json.dumps(review, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        pages.setdefault(e.celex, []).append(data)
        s = data["summary"]
        log(f"    {e.slug}: {s['rows']} rows, {s.get('rows_with_official', 0)} with an official correlation, "
            f"{s.get('gap_candidates', 0)} gap and {s.get('extra_rule_candidates', 0)} extra-rule candidates "
            f"({data['mode']})")
    for celex, ps in pages.items():
        headings = _headings(celex, offline=offline or (live is not None and celex not in live),
                             write_static=write_static, static_dir=static_dir, log=log)
        na = [{"code": c.country_code, "name": c.country_name, "note": c.note}
              for c in (states or []) if c.celex == celex]
        (matrix_dir / f"{celex}.json").write_text(
            json.dumps(matrix_data(celex, ps, headings=headings, not_aligned=na), ensure_ascii=False,
                       separators=(",", ":")),
            encoding="utf-8")
        log(f"  matrix {celex}: {len(ps)} countries")
    return missing


#: Fields of a judgment shown on the page (texts are inserted with textContent by app.js).
JUDGE_FIELDS = ("label", "confidence", "rationale", "evidence_eu", "evidence_national", "evidence_verified", "notes")


def _add_judgments(data: dict, path: Path, log: Callable[[str], None] = lambda msg: None) -> None:
    """Attach LLM judge results from ``path`` (JSONL) to the machine candidates they judged (in place).

    A judgment is matched to a candidate by (EU row ID, national ID); judgments of pairs that are not
    on the page (an alignment recomputed since the judge ran) are counted, not shown. ``data["judge"]``
    describes the run. Nothing happens when the file does not exist or cannot be read."""
    if not path.is_file():
        return
    try:
        recs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError) as exc:
        log(f"    judgments {path.name}: not read ({type(exc).__name__}: {exc})")
        return
    by_pair = {(r.get("eu_id"), r.get("national_id")): r for r in recs if isinstance(r, dict)}
    shown: dict[str, int] = {}
    for a in data.get("articles") or []:
        for r in a.get("rows") or []:
            for m in r.get("machine") or []:
                j = by_pair.get((r["id"], m["id"]))
                if j is None:
                    continue
                m["judge"] = {k: j.get(k) for k in JUDGE_FIELDS}
                lab = str(j.get("label"))
                shown[lab] = shown.get(lab, 0) + 1
    if not shown:
        log(f"    judgments {path.name}: none matches a candidate on the page; not shown")
        return
    data["judge"] = {
        "source": f"data/judgments/{path.name}",
        "models": sorted({str(r.get("model")) for r in recs if r.get("model")}),
        "prompt_versions": sorted({str(r.get("prompt_version")) for r in recs if r.get("prompt_version")}),
        "status": "unreviewed",
        "pairs": len(recs),
        "shown": sum(shown.values()),
        "related": sum(n for lab, n in shown.items() if lab != "unrelated"),
        "labels": dict(sorted(shown.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
    log(f"    judgments {path.name}: {data['judge']['shown']} of {len(recs)} shown, "
        f"{data['judge']['related']} related")


def _add_languages(data: dict, review: dict | None, entry: AlignmentEntry) -> None:
    """Language codes of the EU and national texts (for ``lang`` attributes), also on older static copies."""
    from openacquis.adapters import get_adapter

    nat_lang = get_adapter(entry.country_code).language
    eu_lang = (data.get("eu") or {}).get("language") or nat_lang
    data.setdefault("national_language", nat_lang)
    if review is not None:
        review.setdefault("eu_language", eu_lang)
        review.setdefault("national_language", nat_lang)


def _add_registry(data: dict, entry: AlignmentEntry) -> None:
    """Registry status and note on the page (``baseline``: aligned against law not notified for the
    directive); the official table's citation from :mod:`openacquis.goldsets` (also on older copies)."""
    reg = dict(data.get("registry") or {})
    reg.setdefault("status", entry.status)
    # only the public wording of the note (static copies carry the registry's raw note)
    reg["note"] = entry.note or public_note(reg.get("note") or "", override=entry.override)
    data["registry"] = reg
    src = data.get("official_source")
    if src and entry.gold_label:
        src.update(label=entry.gold_label, url=entry.gold_url, history_url=entry.gold_history_url)


def _headings(celex: str, *, offline: bool, write_static: bool, static_dir: Path,
              log: Callable[[str], None]) -> dict[str, str] | None:
    """English article headings for the matrix: live, else the committed static copy, else None."""
    path = static_dir / f"{celex}.headings.json"
    if not offline:
        try:
            h = directive_headings(celex)
            if write_static:
                static_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(h, ensure_ascii=False, indent=0), encoding="utf-8")
            return h
        except Exception as exc:
            log(f"    English headings unavailable ({type(exc).__name__}: {exc})")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def write_dataset(out_dir: Path, log: Callable[[str], None] = lambda msg: None) -> bool:
    """The open dataset under ``out_dir/dataset/`` (official correlation tables + reviewed links)."""
    from openacquis.dataset import build_dataset

    try:
        s = build_dataset(out_dir / "dataset")
    except Exception as exc:  # an invalid review record must not stop the site
        log(f"  dataset: not built ({type(exc).__name__}: {exc})")
        return False
    log(f"  dataset: {s.rows} links ({s.official} official, {s.reviewed} reviewed), {s.review_records} reviews")
    return True


def write_feed(path: Path, log: Callable[[str], None] = lambda msg: None,
               state: Path = ROOT / "data" / "alerts" / "state.json") -> bool:
    """The Atom feed of change alerts (``openacquis.alerts``) from the committed state file."""
    from openacquis.alerts import write_feed as _write

    try:
        n = _write(state, path)
    except Exception as exc:  # a bad state file must not stop the site
        log(f"  feed: not written ({type(exc).__name__}: {exc})")
        return False
    log(f"  feed: {n} entries -> {path}")
    return True


DATASET_FILES = ("links.csv", "links.jsonl", "datapackage.json")


def link_built_pages(out_dir: Path, index: dict, records: list[dict]) -> None:
    """Keep only links to pages whose data is in ``out_dir`` (in place).

    ``alignments``, their ``review_href`` and ``matrix_href`` are dropped when the file behind
    them was not written (``--no-alignment``, a country with neither live data nor a static
    copy), and ``index["dataset"]`` says whether the dataset downloads exist, so the site
    shows no dead links.
    """
    dataset = all((out_dir / "dataset" / f).is_file() for f in DATASET_FILES)
    index["dataset"] = dataset
    for rec in [*records, *index["directives"]]:
        kept = []
        for a in rec.get("alignments") or []:
            slug = a["href"].split("id=", 1)[-1]
            if not (out_dir / "align" / f"{slug}.json").is_file():
                continue
            if not (out_dir / "review" / f"{slug}.json").is_file():
                a = {**a, "review_href": None}
            kept.append(a)
        rec["alignments"] = kept
        if rec.get("matrix_href") and not (out_dir / "matrix" / f"{rec['celex']}.json").is_file():
            rec["matrix_href"] = None
        rec["dataset"] = dataset


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory (default: site/data)")
    p.add_argument("--offline", action="store_true", help="build from bundled samples, no network")
    p.add_argument("--only", nargs="+", metavar="CELEX", help="restrict to these directives")
    p.add_argument("--cache", type=Path, metavar="DIR", help="cache SPARQL responses in DIR (24 h)")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--pause", type=float, default=2.0, help="seconds between directives (live)")
    p.add_argument("--no-alignment", action="store_true", help="skip the provision-level alignment pages")
    p.add_argument("--countries", nargs="+", metavar="CC", help="only these alignment pages (cz sk at de)")
    p.add_argument("--write-static", action="store_true",
                   help="after a live alignment build, refresh site/static-data/ (commit the result)")
    p.add_argument("--no-dataset", action="store_true", help="skip the dataset downloads")
    p.add_argument("--live-alignments", choices=["seed", "all", "none"], default="seed",
                   help="which alignment pages to compute live: seed (default: NIS2; the other directives use "
                        "site/static-data/, refreshed by scripts/build_alignments.py), all, or none")
    p.add_argument("--no-feed", action="store_true", help="skip site/feed.xml (change alerts)")
    args = p.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    entries = DIRECTIVES
    if args.only:
        wanted = {c.upper() for c in args.only}
        entries = [e for e in DIRECTIVES if e.celex in wanted]
    skip: set[str] = set()
    if args.offline:
        # every directive is listed; those without a bundled sample have no measures in this build
        skip = {e.celex for e in entries} - sample_celexes()
        fetch: Fetcher = sample_fetcher()
        pause = 0.0
    else:
        fetch = with_retries(_http_fetch, retries=args.retries, log=log)
        if args.cache:
            fetch = with_cache(fetch, args.cache)
        pause = args.pause
    if not entries:
        log("nothing to build")
        return 2

    mode = "offline-sample" if args.offline else "live"
    log(f"Building {len(entries)} directives ({mode}) -> {args.out}")
    index, records = build_site_data(
        CellarClient(fetch), entries, mode=mode, log=log, pause=pause, skip=skip
    )
    if not args.no_alignment:
        wanted_align = [a for a in ALIGNMENTS if not args.only or a.celex in {c.upper() for c in args.only}]
        if args.countries:
            wanted_align = [a for a in wanted_align if a.country_code in {c.lower() for c in args.countries}]
        live = {"seed": SEED, "all": None, "none": set()}[args.live_alignments]
        missing = write_alignments(args.out, wanted_align, offline=args.offline, write_static=args.write_static,
                                   log=log, live=live, states=COUNTRY_STATES)
        if missing:
            log(f"Alignment pages without data: {missing}")
    if not args.no_dataset:
        write_dataset(args.out, log)
    link_built_pages(args.out, index, records)
    write_site_data(args.out, index, records)
    if not args.no_feed:
        write_feed(args.out.parent / "feed.xml", log)
    failed = [r["celex"] for r in records if r["error"]]
    log(f"Done: {len(records) - len(failed)} ok, {len(failed)} failed {failed or ''}")
    return 1 if failed and len(failed) == sum(1 for r in records if r["measures_available"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
