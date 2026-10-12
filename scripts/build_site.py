#!/usr/bin/env python3
"""Build the JSON data behind the OpenAcquis website (``site/``).

For a curated list of directives this fetches, from CELLAR, the directive
metadata and the national measures each member state has notified, and writes:

    site/data/index.json          one entry per directive, with counts per country
    site/data/<CELEX>.json        the same plus the full list of measures

The site itself is static HTML/JS that reads these files; nothing else is needed.

Usage::

    python scripts/build_site.py              # live, from the CELLAR SPARQL endpoint
    python scripts/build_site.py --offline    # from bundled samples (scripts/sample_data/)

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
SCHEMA_VERSION = 1


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


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory (default: site/data)")
    p.add_argument("--offline", action="store_true", help="build from bundled samples, no network")
    p.add_argument("--only", nargs="+", metavar="CELEX", help="restrict to these directives")
    p.add_argument("--cache", type=Path, metavar="DIR", help="cache SPARQL responses in DIR (24 h)")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--pause", type=float, default=2.0, help="seconds between directives (live)")
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
    failed = [r["celex"] for r in records if r["error"]]
    log(f"Done: {len(records) - len(failed)} ok, {len(failed)} failed {failed or ''}")
    return 1 if len(failed) == len(records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
