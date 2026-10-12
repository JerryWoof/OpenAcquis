#!/usr/bin/env python3
"""Align every directive x country of the registry and write the site's static alignment copies.

For each directive in ``data/directives.json`` (:mod:`openacquis.directives`) and each
country with an adapter (cz, sk, at, de) this fetches the directive (in the country's
language) and the main transposing act(s), runs :func:`openacquis.align.align` with the
stdlib ``hybrid-lex`` embedder (``--embedder lexical`` also works without extras) and writes

    site/static-data/<CELEX>_<cc>.json          alignment page data (as scripts/build_site.py)
    site/static-data/<CELEX>_<cc>.review.json   review queue data (as scripts/build_site.py)

in exactly the format ``build_site.py --write-static`` writes for NIS2: the shaping is
``build_site.alignment_page_data`` / ``review_page_data`` themselves. Two keys are added to
the alignment file: ``registry`` (status, note, where the acts came from, per-act fetch
report incl. any cap) and ``format`` (output format version, see below).

NIS2 (32022L2555) is the seed built by ``build_site.py`` and is skipped here unless
``--out`` points elsewhere.

Politeness and speed
    Countries run in parallel (one process each, ``--jobs``); within a country the jobs run
    one after another, so every national portal sees at most one request at a time, with the
    adapters' own delay (1 s), User-Agent and on-disk cache under ``$OPENACQUIS_CACHE``.
    EUR-Lex/CELLAR, shared by all countries, is serialised across processes with a lock file
    and a minimum gap between requests. Cached responses are reused for ``--ttl-days``
    (default 7), so a rerun costs little.

    Austria's RIS serves one § per request: ``--at-max-sections`` (default 400; registry
    ``max_sections`` per act) caps the § documents fetched per act. A capped act is recorded
    (``registry.acts[].capped``) and its later §§ are simply not searched.

Resumable
    Each job's outcome is stored in ``<state>/<CELEX>_<cc>.json`` (default
    ``$OPENACQUIS_CACHE/build_alignments``). A job that finished and whose files exist is
    skipped on the next run (``--force`` redoes it); failed jobs are retried. Failures are
    also appended to ``<state>/failures.log``; the run ends with a table per job and the size
    of the static data (``<state>/report.json``).

Output format (``format``)
    1 = full texts (``--text-limit`` characters per provision, as build_site.py).
    2 = compact (``--compact``): national and EU texts are dropped from both files and the
    page fetches them on demand from the portal links (``url``) kept in place; each text
    entry keeps ``label``/``url`` and gets ``"text": ""`` plus ``"text_omitted": true``.
    Use it when the static data outgrows ``--budget-mb`` (default 60).

Everything written is a machine suggestion: a gap candidate means "nothing similar was
found in the acts searched", never that a country failed to transpose.

Usage::

    python scripts/build_alignments.py                       # all directives x countries
    python scripts/build_alignments.py --only 32019L1937 --countries cz de
    python scripts/build_alignments.py --list                # registry and job status
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import sys
import time
import traceback
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "site" / "static-data"
SEED = {"32022L2555"}  # NIS2: built by build_site.py, its static copies are that script's
FORMAT_FULL = 1
FORMAT_COMPACT = 2
DEFAULT_EMBEDDER = "hybrid-lex"
DEFAULT_AT_MAX_SECTIONS = 400
DEFAULT_BUDGET_MB = 60.0
#: ``extra_note`` of a baseline pair (status ``baseline``: the act was not notified for the directive).
EXTRA_NOTE_BASELINE = ("no extra-rule list: the act aligned is the existing national law, not a notified "
                       "transposition, so provisions that match nothing in the directive say nothing about going beyond it")
EURLEX_GAP = 1.0  # seconds between EUR-Lex / CELLAR requests across all processes


def _load_build_site():
    """``scripts/build_site.py`` as a module (its shaping functions define the page format)."""
    if "build_site" in sys.modules:
        return sys.modules["build_site"]
    spec = importlib.util.spec_from_file_location("build_site", Path(__file__).resolve().parent / "build_site.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_site"] = mod  # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


def default_state_dir() -> Path:
    """``$OPENACQUIS_CACHE/build_alignments``, else ``~/.cache/openacquis/build_alignments``."""
    root = os.environ.get("OPENACQUIS_CACHE")
    base = Path(root).expanduser() if root else Path.home() / ".cache" / "openacquis"
    return base / "build_alignments"


def slug(celex: str, cc: str) -> str:
    return f"{celex}_{cc}"


# --- politeness: one request at a time per shared host, across processes --------------------


class HostGate:
    """Serialise requests to one host across processes (``fcntl`` lock file) with a minimum gap.

    On platforms without ``fcntl`` it only enforces the gap within the process.
    """

    def __init__(self, lock_path: Path, gap: float = EURLEX_GAP, sleep: Callable[[float], None] = time.sleep):
        self.lock_path = Path(lock_path)
        self.gap = gap
        self.sleep = sleep

    @contextlib.contextmanager
    def __call__(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+") as f:
            try:
                import fcntl

                fcntl.flock(f, fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover - Windows
                fcntl = None  # type: ignore[assignment]
            try:
                f.seek(0)
                last = float(f.read().strip() or 0)
                wait = last + self.gap - time.time()
                if wait > 0:
                    self.sleep(wait)
                yield
            finally:
                f.seek(0)
                f.truncate()
                f.write(str(time.time()))
                f.flush()
                if fcntl is not None:
                    fcntl.flock(f, fcntl.LOCK_UN)


def gated(get: Callable, gate: HostGate) -> Callable:
    def wrapped(*args, **kwargs):
        with gate():
            return get(*args, **kwargs)

    return wrapped


def make_eurlex(state_dir: Path):
    from openacquis.eurlex import EurLexClient, http_get

    return EurLexClient(get=gated(http_get, HostGate(state_dir / "eurlex.lock")))


def make_national_client(cc: str, ttl_days: float = 7.0):
    """The adapter's client with its own polite fetcher (cache kept ``ttl_days``)."""
    from openacquis.adapters import get_adapter

    a = get_adapter(cc)
    mod = a.load()
    try:
        fetch = mod.HttpFetcher(ttl=ttl_days * 86400)
    except (AttributeError, TypeError):
        return a.make_client()
    return a.make_client(fetch)


# --- fetching -----------------------------------------------------------------------------


def fetch_at_capped(client, ref: str, version: str | None, max_sections: int | None) -> tuple[Any, dict]:
    """An Austrian act with at most ``max_sections`` § documents (one request each)."""
    from openacquis.adapters import at

    r = client.resolve(ref, version)
    norms = client.norms(r.act_id, r.version)
    title = [n for n in norms if n.is_title]
    body = [n for n in norms if not n.is_title]
    info = {"sections_total": len(body), "capped": False}
    if max_sections and len(body) > max_sections:
        body = body[:max_sections]
        info.update(capped=True, sections_fetched=len(body))
    texts = [(n, None) for n in title] + [(n, _fractional_levels(client.xml(n))) for n in body]
    return at.parse_norms(texts, act_id=r.act_id, version=r.version), info


_FRACTIONAL_EBENE = re.compile(r'ebene="(\d+)\.\d+"')


def _fractional_levels(xml: str) -> str:
    """RIS sometimes writes ``ebene="0.5"`` (MinBestG, AWG 2002), which the adapter reads with ``int()``:
    round it down so the act parses (a text closing a list at level 0.5 closes the list)."""
    return _FRACTIONAL_EBENE.sub(r'ebene="\1"', xml)


def fetch_acts(cc: str, client, res, *, at_max_sections: int | None = DEFAULT_AT_MAX_SECTIONS,
               log: Callable[[str], None] = lambda m: None) -> tuple[list, list[dict]]:
    """Fetch the resolved acts; a failing act is reported, not raised. Returns ``(docs, report)``."""
    docs, report = [], []
    for i, ref in enumerate(res.acts):
        t0 = time.monotonic()
        row: dict = {"ref": ref, "main": i == 0, "ok": False}
        try:
            if cc == "at":
                cap = res.max_sections or at_max_sections
                doc, info = fetch_at_capped(client, ref, res.version, cap)
                row.update(info)
                if info["capped"]:
                    log(f"capped {ref} at {info['sections_fetched']} of {info['sections_total']} § documents")
            else:
                doc = client.document(ref, res.version if cc != "de" else None)
            if any(d.act_id == doc.act_id for d in docs):
                row.update(ok=True, act_id=doc.act_id, provisions=len(doc), duplicate=True)
            else:
                docs.append(doc)
                row.update(ok=True, act_id=doc.act_id, title=doc.title, provisions=len(doc))
        except Exception as exc:  # unknown act, network, unexpected answer: record and go on
            row["error"] = f"{type(exc).__name__}: {exc}"
            log(f"could not fetch {ref}: {row['error']}")
        row["seconds"] = round(time.monotonic() - t0, 1)
        report.append(row)
    return docs, report


# --- compaction ------------------------------------------------------------------------------


def compact(data: dict, review: dict | None) -> tuple[dict, dict | None]:
    """Format 2: drop provision texts (kept: labels, URLs); the page fetches texts on demand."""

    def strip(texts: dict) -> dict:
        return {k: {**v, "text": "", "text_omitted": True} for k, v in texts.items()}

    data = {**data, "format": FORMAT_COMPACT, "national_texts": strip(data.get("national_texts", {}))}
    data["articles"] = [{**a, "rows": [{**r, "text": "", "text_omitted": True} for r in a["rows"]]}
                        for a in data.get("articles", [])]
    if review is not None:
        review = {**review, "format": FORMAT_COMPACT, "eu_texts": strip(review.get("eu_texts", {})),
                  "national_texts": strip(review.get("national_texts", {}))}
    return data, review


def with_act_labels(data: dict, res) -> dict:
    """Secondary acts cited as the registry's ``act_labels`` say (``UrhG``), not by adapter slug (``urhg``)."""
    labels = dict(getattr(res, "act_labels", ()) or ())
    if not labels:
        return data
    by_act = {}
    for r in (data.get("registry") or {}).get("acts", []):
        if r.get("act_id"):
            by_act[r["act_id"]] = labels.get(r["ref"], "")
    nat = [{**n, "label": by_act.get(n.get("act_id"), "") or n.get("label", "")} if i else n
           for i, n in enumerate(data.get("national") or [])]
    return {**data, "national": nat}


def dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# --- one job -------------------------------------------------------------------------------


def job_summary(data: dict, review: dict | None) -> dict:
    """The numbers reported per directive x country."""
    s = data["summary"]
    units = s.get("eu_units", 0)
    return {
        "eu_units": units,
        "eu_units_with_link": s.get("eu_units_with_link", 0),
        "linked_share": round(s.get("eu_units_with_link", 0) / units, 3) if units else None,
        "rows": s.get("rows", 0),
        "rows_with_link": s.get("rows_with_link", 0),
        "gap_candidates": s.get("gap_candidates", 0),
        "gap_articles": s.get("gap_articles", 0),
        "extra_rule_candidates": s.get("extra_rule_candidates", 0),
        "main_act_units": s.get("main_act_units", 0),
        "suggestions": len(review["suggestions"]) if review else None,
    }


def build_job(
    entry,
    cc: str,
    *,
    out_dir: Path,
    eurlex,
    client,
    embedder: str = DEFAULT_EMBEDDER,
    text_limit: int = 2500,
    review_text_limit: int = 1500,
    fmt: int = FORMAT_FULL,
    at_max_sections: int | None = DEFAULT_AT_MAX_SECTIONS,
    mappings=None,
    now: datetime | None = None,
    log: Callable[[str], None] = lambda m: None,
) -> dict:
    """Align one directive x country and write its two files. Never raises: returns a status record."""
    from openacquis.adapters import get_adapter
    from dataclasses import replace

    from openacquis.align import align, choose_embedder, default_version, gold_path
    from openacquis.directives import resolve_main_acts
    from openacquis.evaluate import load_gold

    bs = _load_build_site()
    t0 = time.monotonic()
    sl = slug(entry.celex, cc)
    rec: dict = {"id": sl, "celex": entry.celex, "short": entry.short, "country": cc, "status": "failed",
                 "error": None, "acts": [], "seconds": None}
    try:
        res = resolve_main_acts(entry, cc, mappings)
        rec.update(registry_status=res.status, source=res.source, note=res.note)
        if not res.acts:
            rec.update(status="none", seconds=0.0)
            return rec
        if res.version is None:  # the version an evaluation set follows, so page and eval read the same text
            res = replace(res, version=default_version(entry.celex, cc))
        adapter = get_adapter(cc)
        lang = adapter.language
        eu = eurlex.document(entry.celex, lang)
        t_fetch = time.monotonic()
        docs, report = fetch_acts(cc, client, res, at_max_sections=at_max_sections, log=log)
        rec["acts"] = report
        rec["fetch_seconds"] = round(time.monotonic() - t_fetch, 1)
        if not docs:
            raise LookupError("none of the acts could be fetched: " + "; ".join(r.get("error", "") for r in report))
        if not report[0]["ok"]:
            log(f"main act {res.main} failed; extra-rule candidates come from {docs[0].act_id}")
        t_align = time.monotonic()
        result = align(eu, docs, embedder=choose_embedder(embedder, lang), now=now, extra_rules=res.extra_rules,
                       extra_note=None if res.status == "ok" else EXTRA_NOTE_BASELINE)
        rec["align_seconds"] = round(time.monotonic() - t_align, 1)
        from openacquis.goldsets import BY_PAIR

        gp = gold_path(entry.celex, cc)
        gold = load_gold(gp) if gp else []  # every positive link of the official table (negatives dropped)
        gs = BY_PAIR.get((entry.celex, cc)) if gold else None
        ae = bs.AlignmentEntry(entry.celex, entry.short, adapter.alpha3, cc, adapter.name,
                               res.label or f"{docs[0].act_id}{adapter.act_suffix}",
                               gs.path if gs else "", gs.title if gs else "", gs.url if gs else "",
                               gs.history_url if gs else "", status=res.status, note=res.note)
        data = bs.alignment_page_data(ae, result, gold, now=now, mode="live", text_limit=text_limit)
        review = bs.review_page_data(ae, result, reviews=None, now=now, mode="live", text_limit=review_text_limit)
        data = {**data, "mode": "static", "format": FORMAT_FULL,
                "registry": {"status": res.status, "source": res.source, "note": res.note,
                             "acts": [{k: v for k, v in r.items() if k != "title"} for r in report]}}
        data = with_act_labels(data, res)
        lo = result.leftovers
        if lo is not None and lo.extra_note:  # no extra-rule list for this act: say why on the page
            data["extra_note"] = lo.extra_note
            data["summary"] = {**data["summary"], "extra_rule_candidates_suppressed": lo.extra_suppressed}
        review = {**review, "mode": "static", "reviewed": {}, "format": FORMAT_FULL}
        if fmt == FORMAT_COMPACT:
            data, review = compact(data, review)
        out_dir.mkdir(parents=True, exist_ok=True)
        a_path, r_path = out_dir / f"{sl}.json", out_dir / f"{sl}.review.json"
        for path, obj in ((a_path, data), (r_path, review)):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(dumps(obj), encoding="utf-8")
            tmp.replace(path)
        rec.update(status="ok", format=fmt, method=result.method, threshold=result.threshold,
                   national_units=len(result.match.national_units),
                   capped=any(r.get("capped") for r in report),
                   failed_acts=[r["ref"] for r in report if not r["ok"]],
                   bytes={"alignment": a_path.stat().st_size, "review": r_path.stat().st_size},
                   **job_summary(data, review))
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc(limit=6)
    rec["seconds"] = round(time.monotonic() - t0, 1)
    return rec


# --- state (resumable runs) ------------------------------------------------------------------


def load_state(state_dir: Path, sl: str) -> dict | None:
    p = state_dir / f"{sl}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(state_dir: Path, rec: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    if rec["status"] == "failed" or rec.get("failed_acts"):
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with open(state_dir / "failures.log", "a", encoding="utf-8") as f:
            what = rec["error"] or f"acts not fetched: {', '.join(rec['failed_acts'])}"
            f.write(f"{stamp}\t{rec['id']}\t{what}\n")


def done(state_dir: Path, out_dir: Path, sl: str, fmt: int) -> bool:
    """Finished earlier, in the same format, and both files are still there."""
    st = load_state(state_dir, sl)
    if not st or st.get("status") not in ("ok", "none"):
        return False
    if st["status"] == "none":
        return True
    return (st.get("format", FORMAT_FULL) == fmt and (out_dir / f"{sl}.json").is_file()
            and (out_dir / f"{sl}.review.json").is_file())


# --- running a country --------------------------------------------------------------------


def run_country(cc: str, celexes: list[str], opts: dict) -> list[dict]:
    """All jobs of one country, one after another (runs in its own process)."""
    from openacquis.directives import load_registry

    state_dir, out_dir = Path(opts["state_dir"]), Path(opts["out_dir"])
    log_path = state_dir / "build.log"

    def log(msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')} {cc}] {msg}"
        print(line, file=sys.stderr, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    reg = load_registry(opts.get("registry"))
    eurlex = make_eurlex(state_dir)
    client = make_national_client(cc, opts.get("ttl_days", 7.0))
    cellar = None
    if opts.get("from_cellar"):
        from openacquis.cellar import CellarClient, _http_fetch

        gate = HostGate(state_dir / "cellar.lock", gap=2.0)
        cellar = CellarClient(gated(_http_fetch, gate))
    out = []
    for celex in celexes:
        entry = reg.get(celex)
        sl = slug(celex, cc)
        if not opts.get("force") and done(state_dir, out_dir, sl, opts["format"]):
            log(f"{sl}: done earlier, skipped (--force to redo)")
            out.append({**(load_state(state_dir, sl) or {}), "resumed": True})
            continue
        mappings = None
        if cellar is not None and not entry.country(cc).override and entry.country(cc).alignable:
            from openacquis.align import notified_measures

            try:
                mappings = notified_measures(celex, cc, cellar=cellar, national_client=client)
            except Exception as exc:
                log(f"{sl}: CELLAR mapping unavailable ({type(exc).__name__}: {exc}); using the registry acts")
        log(f"{sl} ({entry.short}) ...")
        rec = build_job(entry, cc, out_dir=out_dir, eurlex=eurlex, client=client, embedder=opts["embedder"],
                        text_limit=opts["text_limit"], fmt=opts["format"], at_max_sections=opts["at_max_sections"],
                        mappings=mappings, log=lambda m, sl=sl: log(f"  {sl}: {m}"))
        save_state(state_dir, rec)
        if rec["status"] == "ok":
            log(f"{sl}: {rec['eu_units']} EU units, {rec['national_units']} national units, "
                f"{rec['linked_share']:.0%} linked, {rec['gap_candidates']} gap candidates, "
                f"{sum(rec['bytes'].values()) / 1e6:.2f} MB, {rec['seconds']} s")
        elif rec["status"] == "none":
            log(f"{sl}: nothing to align ({rec.get('note')})")
        else:
            log(f"{sl}: FAILED {rec['error']}")
        out.append(rec)
    return out


# --- report ----------------------------------------------------------------------------------


def static_size(out_dir: Path) -> int:
    return sum(p.stat().st_size for p in out_dir.glob("*.json")) if out_dir.is_dir() else 0


def format_table(recs: list[dict]) -> str:
    head = (f"{'job':16} {'status':8} {'EU units':>8} {'nat units':>9} {'linked':>7} {'gaps':>5} "
            f"{'extras':>6} {'sec':>7} {'MB':>6}  notes")
    lines = [head, "-" * len(head)]
    for r in sorted(recs, key=lambda r: r["id"]):
        mb = sum((r.get("bytes") or {}).values()) / 1e6
        notes = []
        if r.get("registry_status") == "baseline":
            notes.append("baseline")
        if r.get("capped"):
            notes.append("capped")
        if r.get("failed_acts"):
            notes.append("failed acts: " + ",".join(r["failed_acts"]))
        if r["status"] == "failed":
            notes.append(str(r.get("error"))[:80])
        if r["status"] == "none":
            notes.append(str(r.get("note"))[:80])
        share = f"{r['linked_share']:.0%}" if r.get("linked_share") is not None else ""
        lines.append(f"{r['id']:16} {r['status']:8} {r.get('eu_units', ''):>8} {r.get('national_units', ''):>9} "
                     f"{share:>7} {r.get('gap_candidates', ''):>5} {r.get('extra_rule_candidates', ''):>6} "
                     f"{r.get('seconds') or 0:>7} {mb:>6.2f}  {'; '.join(notes)}")
    return "\n".join(lines)


# --- CLI ----------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from openacquis.directives import COUNTRIES, load_registry

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--registry", type=Path, help="registry file (default: data/directives.json)")
    p.add_argument("--out", type=Path, default=STATIC_DIR, help="output directory (default: site/static-data)")
    p.add_argument("--state", type=Path, help="state directory (default: $OPENACQUIS_CACHE/build_alignments)")
    p.add_argument("--only", nargs="+", metavar="CELEX", help="only these directives")
    p.add_argument("--countries", nargs="+", metavar="CC", help=f"only these countries ({' '.join(COUNTRIES)})")
    p.add_argument("--embedder", default=DEFAULT_EMBEDDER, choices=["hybrid-lex", "lexical", "bm25"],
                   help="stdlib embedder (default: hybrid-lex, as the website)")
    p.add_argument("--jobs", type=int, default=4, help="countries in parallel (default 4)")
    p.add_argument("--at-max-sections", type=int, default=DEFAULT_AT_MAX_SECTIONS,
                   help="Austria: max § documents fetched per act (0 = no cap)")
    p.add_argument("--text-limit", type=int, default=2500, help="characters per text on the page (format 1)")
    p.add_argument("--compact", action="store_true", help="format 2: no texts, fetched on demand by the page")
    p.add_argument("--budget-mb", type=float, default=DEFAULT_BUDGET_MB, help="warn above this static-data size")
    p.add_argument("--ttl-days", type=float, default=7.0, help="reuse cached portal responses this long")
    p.add_argument("--from-cellar", action="store_true",
                   help="resolve non-override acts from CELLAR's notified measures (else as recorded)")
    p.add_argument("--force", action="store_true", help="redo jobs that finished earlier")
    p.add_argument("--list", action="store_true", help="print the registry and job status, then exit")
    args = p.parse_args(argv)

    reg = load_registry(args.registry)
    state_dir = args.state or default_state_dir()
    out_dir = args.out
    fmt = FORMAT_COMPACT if args.compact else FORMAT_FULL
    celexes = [d.celex for d in reg if not args.only or d.celex in {c.upper() for c in args.only}]
    if out_dir.resolve() == STATIC_DIR.resolve():
        skipped = [c for c in celexes if c in SEED]
        celexes = [c for c in celexes if c not in SEED]
        if skipped:
            print(f"skipping {', '.join(skipped)}: built by scripts/build_site.py --write-static", file=sys.stderr)
    countries = [c.lower() for c in (args.countries or COUNTRIES)]

    if args.list:
        for d in reg:
            print(f"{d.rank or '':>2} {d.celex} {d.short} (deadline {d.deadline})")
            for cc in COUNTRIES:
                e = d.country(cc)
                st = load_state(state_dir, slug(d.celex, cc)) or {}
                print(f"     {cc} {e.status:8} {', '.join(e.acts) or '-':32} {'override' if e.override else '':8} "
                      f"[{st.get('status', 'not run')}] {e.note[:70]}")
        return 0
    if not celexes:
        print("nothing to build", file=sys.stderr)
        return 2

    opts = {"state_dir": str(state_dir), "out_dir": str(out_dir), "registry": str(args.registry or ""),
            "embedder": args.embedder, "text_limit": args.text_limit, "format": fmt,
            "at_max_sections": args.at_max_sections or None, "ttl_days": args.ttl_days,
            "from_cellar": args.from_cellar, "force": args.force}
    if not opts["registry"]:
        opts["registry"] = None
    state_dir.mkdir(parents=True, exist_ok=True)
    print(f"Aligning {len(celexes)} directives x {len(countries)} countries -> {out_dir} "
          f"({args.embedder}, format {fmt}, state {state_dir})", file=sys.stderr)
    t0 = time.monotonic()
    recs: list[dict] = []
    if args.jobs <= 1 or len(countries) == 1:
        for cc in countries:
            recs.extend(run_country(cc, celexes, opts))
    else:
        with ProcessPoolExecutor(max_workers=min(args.jobs, len(countries))) as ex:
            futs = {ex.submit(run_country, cc, celexes, opts): cc for cc in countries}
            for f in as_completed(futs):
                try:
                    recs.extend(f.result())
                except Exception as exc:  # a crashed worker: its jobs count as failed
                    cc = futs[f]
                    print(f"[{cc}] worker crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
                    recs.extend({"id": slug(c, cc), "celex": c, "country": cc, "status": "failed",
                                 "error": f"worker crashed: {exc}"} for c in celexes)
    size = static_size(out_dir)
    report = {"built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
              "seconds": round(time.monotonic() - t0, 1), "format": fmt, "embedder": args.embedder,
              "static_bytes": size, "budget_mb": args.budget_mb,
              "jobs": sorted(({k: v for k, v in r.items() if k != "traceback"} for r in recs),
                             key=lambda r: r["id"])}
    (state_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(format_table(recs))
    failed = [r["id"] for r in recs if r["status"] == "failed"]
    print(f"\n{len(recs)} jobs: {sum(r['status'] == 'ok' for r in recs)} ok, {len(failed)} failed "
          f"{failed or ''}, {sum(r['status'] == 'none' for r in recs)} nothing to align; "
          f"{out_dir} holds {size / 1e6:.1f} MB; {report['seconds']} s")
    if size > args.budget_mb * 1e6 and fmt == FORMAT_FULL:
        print(f"static data above the {args.budget_mb:.0f} MB budget: rerun with --compact --force "
              "(format 2, texts fetched on demand) or a smaller --text-limit", file=sys.stderr)
    return 1 if failed and len(failed) == len(recs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
