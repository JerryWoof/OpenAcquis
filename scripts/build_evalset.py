#!/usr/bin/env python3
"""Build Czech evaluation sets from the official correlation tables of government bills.

Each set pairs one directive with the act that transposes it, using the table attached to
the government bill (Chamber of Deputies print, *sněmovní tisk*, attachment "Kompatibilita
s právem EU", a *rozdílová tabulka*). Pinned sets:

    nis2           32022L2555  tisk 9/759 -> Act 264/2025 Sb.   data/eval/nis2_cz.jsonl
    cer            32022L2557  tisk 9/947 -> Act 266/2025 Sb.   data/eval/32022L2557_cz.jsonl
    eaa            32019L0882  tisk 9/539 -> Act 424/2023 Sb.   data/eval/32019L0882_cz.jsonl
    whistleblower  32019L1937  tisk 9/352 -> Act 171/2023 Sb.   data/eval/32019L1937_cz.jsonl

    python scripts/build_evalset.py                        # all pinned sets
    python scripts/build_evalset.py cer eaa                # some of them
    python scripts/build_evalset.py nis2 --table t0759c0.doc   # a local copy of the table
    python scripts/build_evalset.py --no-check             # skip fetching the texts
    python scripts/build_evalset.py --celex 32019L0882 --tisk 9/539   # any bill: finds the
        # table on the print's page and the act number in the bill history (not pinned)

Writes ``<out>.jsonl`` (one gold link per line) and ``<out>_summary.json`` (counts,
resolution rates, unresolved IDs) per set. Checking fetches the directive in Czech from
CELLAR and the act from e-Sbírka (cached under ``OPENACQUIS_CACHE``); psp.cz pages and
tables are cached under ``OPENACQUIS_CACHE/psp``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openacquis.adapters.cz import ESbirkaClient
from openacquis.eurlex import EurLexClient
from openacquis.evalset import (
    check_links,
    download,
    find_table_files,
    history_url,
    links_from_rows,
    published_act,
    read_table,
    save_jsonl,
    summarize,
    table_file_url,
    table_rows,
    tisk_url,
)

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "data" / "eval"

#: Pinned sets. ``act_version``: e-Sbírka version checked against (for reproducibility);
#: ``idd``: the table file on psp.cz (found with ``find_table_files`` on the print's page).
SETS: dict[str, dict] = {
    "nis2": {
        "celex": "32022L2555", "term": 9, "tisk": 759, "idd": 244067, "file": "t0759c0.doc",
        "act": "264/2025", "act_version": "2025-11-01", "out": "nis2_cz.jsonl",
    },
    "cer": {
        "celex": "32022L2557", "term": 9, "tisk": 947, "idd": 253085, "file": "t0947d0.docx",
        "act": "266/2025", "act_version": "2025-08-19", "out": "32022L2557_cz.jsonl",
    },
    "eaa": {
        "celex": "32019L0882", "term": 9, "tisk": 539, "idd": 232281, "file": "t0539b0.docx",
        "act": "424/2023", "act_version": "2025-07-01", "out": "32019L0882_cz.jsonl",
    },
    "whistleblower": {
        "celex": "32019L1937", "term": 9, "tisk": 352, "idd": 221044, "file": "t0352a0.doc",
        "act": "171/2023", "act_version": "2025-02-15", "out": "32019L1937_cz.jsonl",
    },
}


def discover(celex: str, tisk: str, act: str | None) -> dict:
    """A set definition for any bill: the table and the act number are looked up on psp.cz."""
    term, _, number = tisk.partition("/")
    if not number:
        raise SystemExit(f"--tisk expects TERM/NUMBER, e.g. 9/947 (got {tisk!r})")
    t, n = int(term), int(number)
    files = [f for f in find_table_files(download(tisk_url(t, n))) if not f["name"].lower().endswith(".pdf")]
    if not files:
        raise SystemExit(f"print {tisk} has no Word file under 'Kompatibilita s právem EU' ({tisk_url(t, n)})")
    act = act or published_act(download(history_url(t, n), cache_dir=False))
    if not act:
        raise SystemExit(f"print {tisk}: no published act yet; pass --act N/YYYY to check against another act")
    return {"celex": celex.upper(), "term": t, "tisk": n, "idd": int(files[0]["idd"]), "file": files[0]["name"],
            "act": act, "act_version": None, "out": f"{celex.upper()}_cz.jsonl"}


def build(spec: dict, table: str | None, out: Path, check: bool, act_version: str | None,
          eurlex: EurLexClient | None = None, esbirka: ESbirkaClient | None = None) -> dict:
    url = table_file_url(spec["idd"])
    data = Path(table).read_bytes() if table else download(url)
    rows = read_table(data)
    trows = table_rows(rows, celex=spec["celex"], section_acts=spec.get("section_acts"))
    links = links_from_rows(trows, spec["act"], source_url=url)
    print(f"{spec['celex']}: table {spec['file']}: {len(rows)} rows, {len(trows)} for {spec['celex']}, "
          f"{len(links)} links", file=sys.stderr)
    version = act_version or spec.get("act_version")
    if check:
        eu = (eurlex or EurLexClient()).document(spec["celex"], "cs")
        cz = (esbirka or ESbirkaClient()).document(spec["act"], version=version)
        check_links(links, eu, cz, rows=trows)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_jsonl(links, out)
    source = {"tisk": history_url(spec["term"], spec["tisk"]), "table": url, "act": spec["act"],
              "act_version": version, "celex": spec["celex"]}
    summary = {"source": source, **summarize(links)}
    flags: dict[str, int] = {}
    for lk in links:
        for f in lk.flags:
            flags[f] = flags.get(f, 0) + 1
    if flags:  # only for tables that need them (keeps the NIS2 summary as it was)
        summary["flags"] = dict(sorted(flags.items()))
    out.with_name(out.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sets", nargs="*", metavar="SET",
                    help=f"pinned sets to build: {', '.join(SETS)} (default: all)")
    ap.add_argument("--celex", help="build a set for this directive (with --tisk) instead of a pinned one")
    ap.add_argument("--tisk", help="government bill print as TERM/NUMBER, e.g. 9/947 (with --celex)")
    ap.add_argument("--act", help="with --celex/--tisk: act to check against (default: from the bill history)")
    ap.add_argument("--table", help="local .doc/.docx of the table instead of downloading it (one set only)")
    ap.add_argument("--out", help="output JSONL (one set only; default data/eval/<name>)")
    ap.add_argument("--no-check", action="store_true", help="do not fetch texts to verify IDs")
    ap.add_argument("--act-version", help="e-Sbírka version date of the act (default: the pinned one)")
    args = ap.parse_args(argv)

    unknown = [s for s in args.sets if s not in SETS]
    if unknown:
        ap.error(f"unknown set(s) {', '.join(unknown)}; choose from {', '.join(SETS)}")
    if args.celex or args.tisk:
        if not (args.celex and args.tisk) or args.sets:
            ap.error("--celex and --tisk go together, without set names")
        specs = {args.celex.upper(): discover(args.celex, args.tisk, args.act)}
    else:
        specs = {name: SETS[name] for name in (args.sets or SETS)}
    if len(specs) > 1 and (args.table or args.out or args.act_version):
        ap.error("--table, --out and --act-version need a single set")

    eurlex = esbirka = None
    if not args.no_check:
        eurlex, esbirka = EurLexClient(), ESbirkaClient()
    for name, spec in specs.items():
        out = Path(args.out) if args.out else EVAL / spec["out"]
        summary = build(spec, args.table, out, not args.no_check, args.act_version, eurlex, esbirka)
        brief = {k: v for k, v in summary.items() if k not in ("eu_unresolved", "national_unresolved", "unparsed_refs")}
        print(json.dumps({"set": name, **brief}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
