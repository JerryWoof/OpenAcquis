#!/usr/bin/env python3
"""Build the JSON data behind the OpenAcquis website (``site/``).

For a curated list of directives this fetches, from CELLAR, the directive
metadata and the national measures each member state has notified, and writes:

    site/data/index.json          one entry per directive, with counts per country
    site/data/<CELEX>.json        the same plus the full list of measures
    site/data/align/<CELEX>_<cc>.json
                                  provision-level alignment pages (NIS2 x Czechia): the official
                                  correlation table next to the top machine suggestion

The site itself is static HTML/JS that reads these files; nothing else is needed.

Usage::

    python scripts/build_site.py              # live, from the CELLAR SPARQL endpoint
    python scripts/build_site.py --offline    # from bundled samples (scripts/sample_data/)
    python scripts/build_site.py --write-static
                                  # also refresh site/static-data/ (commit it): the
                                  # alignment the builder falls back to when texts
                                  # cannot be fetched

Alignment pages are computed with the stdlib lexical embedder (no heavy dependencies).
They need the directive text (CELLAR) and the national act (e-Sbírka); if either
cannot be fetched, or with ``--offline``, the committed ``site/static-data/`` copy is
used instead and the page says when it was computed.

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
SCHEMA_VERSION = 1
ALIGNMENT_SCHEMA = 1


@dataclass(frozen=True)
class Entry:
    """A directive shown on the site."""

    celex: str
    short: str
    topic: str


# Curated list. All CELEX numbers verified against CELLAR (2026-10).
DIRECTIVES: list[Entry] = [
    Entry("32022L2555", "NIS2", "Cybersecurity"),
    Entry("32019L0882", "European Accessibility Act", "Accessibility"),
    Entry("32023L0970", "Pay Transparency Directive", "Employment"),
    Entry("32022L2464", "CSRD", "Corporate sustainability reporting"),
    Entry("32019L1937", "Whistleblower Directive", "Whistleblower protection"),
    Entry("32022L2557", "Critical Entities Resilience (CER)", "Critical infrastructure"),
    Entry("32019L0790", "Copyright in the Digital Single Market", "Copyright"),
    Entry("32019L1158", "Work-Life Balance Directive", "Employment"),
    Entry("32022L2041", "Adequate Minimum Wages Directive", "Employment"),
    Entry("32018L1972", "European Electronic Communications Code", "Telecommunications"),
]


@dataclass(frozen=True)
class AlignmentEntry:
    """A provision-level alignment page: one directive x one country."""

    celex: str
    short: str
    country: str  # ISO alpha-3, as in the measures data
    country_code: str  # adapter code: "cz"
    country_name: str
    gold: str  # evaluation set (relative to the repository root)
    gold_label: str
    gold_url: str
    gold_history_url: str

    @property
    def slug(self) -> str:
        return f"{self.celex}_{self.country_code}"

    @property
    def href(self) -> str:
        return f"alignment.html?id={self.slug}"


ALIGNMENTS: list[AlignmentEntry] = [
    AlignmentEntry(
        "32022L2555", "NIS2", "CZE", "cz", "Czechia", "data/eval/nis2_cz.jsonl",
        "Rozdílová tabulka, government bill of the Cybersecurity Act (sněmovní tisk 759/0, 2024)",
        "https://www.psp.cz/sqw/text/orig2.sqw?idd=244067",
        "https://www.psp.cz/sqw/historie.sqw?o=9&t=759",
    ),
]


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


def build_directive(client: CellarClient, entry: Entry) -> dict:
    """Fetch one directive. Never raises: errors are returned in ``"error"``."""
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
            {"country": a.country, "country_name": a.country_name, "href": a.href}
            for a in ALIGNMENTS if a.celex == entry.celex
        ],
        "error": None,
    }
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
) -> tuple[dict, list[dict]]:
    """Return ``(index, records)`` for ``entries``."""
    records = []
    for i, entry in enumerate(entries):
        if i and pause:
            sleep(pause)  # be gentle with the public endpoint
        log(f"  {entry.celex} {entry.short} ...")
        rec = build_directive(client, entry)
        log(
            f"    error: {rec['error']}"
            if rec["error"]
            else f"    {rec['total_measures']} measures from {rec['countries_with_measures']} member states"
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


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _on_path(a: str, b: str) -> bool:
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


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
    """Shape an :class:`openacquis.align.AlignmentResult` and gold links into the page's JSON.

    One row per EU paragraph (or per article without paragraphs, or annex). Each row
    has the national provisions the official correlation table lists for it and the
    top machine suggestions (best score of any EU unit inside the row, per national
    unit). Texts are plain strings; the page inserts them with textContent.
    """
    from openacquis.align import path_label
    from openacquis.evaluate import retrieval
    from openacquis.match import level_id

    eu = result.eu_doc
    mr = result.match
    nat_doc = {p.id: d for d in result.national_docs for p in d}
    # rows: EU paragraphs, or the article / annex itself when it has no paragraphs
    articles: list[dict] = []
    row_ids: list[str] = []
    art_of_row: dict[str, dict] = {}
    for p in eu.roots():
        if p.kind not in ("article", "annex"):
            continue
        paras = [c for c in eu.children(p.id) if c.kind == "paragraph"]
        art = {"id": p.id, "label": p.label, "heading": p.heading or "", "url": p.url,
               "official": [], "rows": []}
        for r in paras or [p]:
            row_ids.append(r.id)
            art_of_row[r.id] = art
            art["rows"].append({"id": r.id, "label": path_label(eu, r.id), "url": r.url,
                                "text": _clip(eu.full_text(r.id), text_limit),
                                "official": [], "official_eu": [], "machine": [], "agreement": None})
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

    # "article_level": the table cites only the whole article (e.g. "Čl. 35"), which covers this
    # paragraph too, but names no provision for it alone, so there is nothing to compare row by row.
    counts = {"match": 0, "same_section": 0, "differs": 0, "article_level": 0, "no_table_entry": 0}
    for rid, r in rows.items():
        if not r["official"]:
            r["agreement"] = "article_level" if art_of_row[rid]["official"] else "no_table_entry"
        elif not r["machine"]:
            r["agreement"] = "differs"
        else:
            m = r["machine"][0]["id"]
            if any(_on_path(m, o) for o in r["official"]):
                r["agreement"] = "match"
            elif section(m) in {section(o) for o in r["official"]}:
                r["agreement"] = "same_section"
            else:
                r["agreement"] = "differs"
        counts[r["agreement"]] += 1

    used = {x for r in rows.values() for x in r["official"]} | {m["id"] for r in rows.values() for m in r["machine"]}
    used |= {x for a in articles for x in a["official"]}
    national = {}
    for nid in sorted(used):
        d = nat_doc.get(nid)
        if d is None:
            national[nid] = {"label": nid.split(":", 2)[-1], "act": nid.split(":")[1], "text": "", "url": None}
            continue
        national[nid] = {"label": path_label(d, nid), "act": d.act_id, "url": d.by_id(nid).url,
                         "text": _clip(d.full_text(nid), text_limit)}
    ret = retrieval(mr, gold, (1, 5), levels=("provision", "section"))
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
        "country_name": entry.country_name,
        "eu": {"title": eu.title, "url": eu.url, "language": eu.language},
        "national": [{"act_id": d.act_id, "title": d.title, "url": d.url} for d in result.national_docs],
        "method": result.method,
        "threshold": result.threshold,
        "official_source": {"label": entry.gold_label, "url": entry.gold_url, "history_url": entry.gold_history_url,
                            "links": len(gold)},
        "summary": {
            "rows": len(rows),
            "rows_with_official": len(rows) - counts["no_table_entry"],
            **counts,
            "recall_at_1": ret["provision"]["recall@1"],
            "recall_at_5": ret["provision"]["recall@5"],
            "section_recall_at_5": ret["section"]["recall@5"],
        },
        "articles": articles,
        "national_texts": national,
    }


def build_alignment(
    entry: AlignmentEntry,
    *,
    log: Callable[[str], None] = lambda msg: None,
    now: datetime | None = None,
    eurlex=None,
    national_client=None,
) -> dict:
    """Fetch the texts, align with the lexical embedder and shape the page data (raises on failure)."""
    from openacquis.align import align, load_documents
    from openacquis.evaluate import load_gold
    from openacquis.match import LexicalEmbedder

    eu, docs = load_documents(entry.celex, entry.country_code, eurlex=eurlex, national_client=national_client,
                              log=log)
    result = align(eu, docs, embedder=LexicalEmbedder(), log=log, now=now)
    gold = load_gold(ROOT / entry.gold)
    return alignment_page_data(entry, result, gold, now=now, mode="live")


def write_alignments(
    out_dir: Path,
    entries: list[AlignmentEntry],
    *,
    offline: bool = False,
    write_static: bool = False,
    static_dir: Path = STATIC_DIR,
    log: Callable[[str], None] = lambda msg: None,
    build: Callable[..., dict] = build_alignment,
) -> list[str]:
    """Write ``out_dir/align/<slug>.json`` for each entry. Live first, then the committed
    static copy. Returns the slugs that have no data at all (neither worked)."""
    align_dir = out_dir / "align"
    align_dir.mkdir(parents=True, exist_ok=True)
    missing = []
    for e in entries:
        data = None
        if not offline:
            log(f"  alignment {e.slug} ...")
            try:
                data = build(e, log=lambda m: log("    " + m))
            except Exception as exc:
                log(f"    could not compute live ({type(exc).__name__}: {exc}); using the static copy")
        if data is not None and write_static:
            static_dir.mkdir(parents=True, exist_ok=True)
            (static_dir / f"{e.slug}.json").write_text(
                json.dumps({**data, "mode": "static"}, ensure_ascii=False, indent=None, separators=(",", ":")),
                encoding="utf-8")
            log(f"    wrote {static_dir / (e.slug + '.json')}")
        if data is None:
            path = static_dir / f"{e.slug}.json"
            if not path.exists():
                log(f"    no static copy at {path}")
                missing.append(e.slug)
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            data["mode"] = "static"
        (align_dir / f"{e.slug}.json").write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        s = data["summary"]
        log(f"    {e.slug}: {s['rows']} rows, {s['rows_with_official']} with an official correlation ({data['mode']})")
    return missing


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
    p.add_argument("--write-static", action="store_true",
                   help="after a live alignment build, refresh site/static-data/ (commit the result)")
    args = p.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    entries = DIRECTIVES
    if args.only:
        wanted = {c.upper() for c in args.only}
        entries = [e for e in DIRECTIVES if e.celex in wanted]
    if args.offline:
        have = sample_celexes()
        entries = [e for e in entries if e.celex in have]
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
        CellarClient(fetch), entries, mode=mode, log=log, pause=pause
    )
    write_site_data(args.out, index, records)
    if not args.no_alignment:
        wanted_align = [a for a in ALIGNMENTS if not args.only or a.celex in {c.upper() for c in args.only}]
        missing = write_alignments(args.out, wanted_align, offline=args.offline, write_static=args.write_static,
                                   log=log)
        if missing:
            log(f"Alignment pages without data: {missing}")
    failed = [r["celex"] for r in records if r["error"]]
    log(f"Done: {len(records) - len(failed)} ok, {len(failed)} failed {failed or ''}")
    return 1 if len(failed) == len(records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
