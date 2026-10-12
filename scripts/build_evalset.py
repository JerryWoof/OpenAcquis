#!/usr/bin/env python3
"""Build the NIS2 x Czechia evaluation set from the official correlation table.

Source: the government bill of the Cybersecurity Act (Act No. 264/2025 Sb.), Chamber of
Deputies print (sněmovní tisk) 759, 9th term, attachment "Kompatibilita s právem EU"
(t0759c0.doc, a *rozdílová tabulka* pairing bill provisions with NIS2 provisions).

    python scripts/build_evalset.py                      # download, parse, check, write
    python scripts/build_evalset.py --table t0759c0.doc  # use a local copy of the table
    python scripts/build_evalset.py --no-check           # skip fetching NIS2 / the act

Writes ``data/eval/nis2_cz.jsonl`` (one gold link per line) and
``data/eval/nis2_cz_summary.json`` (counts, resolution rates, unresolved IDs).
Checking fetches NIS2 in Czech from CELLAR and the act from e-Sbírka (cached under
``OPENACQUIS_CACHE``); the table download is cached under ``OPENACQUIS_CACHE/psp``.
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
    links_from_rows,
    read_table,
    save_jsonl,
    summarize,
    table_rows,
)

CELEX = "32022L2555"
ACT = "264/2025"
ACT_VERSION = "2025-11-01"  # consolidated version checked against (pinned for reproducibility)
TISK_URL = "https://www.psp.cz/sqw/historie.sqw?o=9&t=759"
TABLE_URL = "https://www.psp.cz/sqw/text/orig2.sqw?idd=244067"  # t0759c0.doc
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "eval" / "nis2_cz.jsonl"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--table", help="local .doc/.docx of the table instead of downloading it")
    ap.add_argument("--out", default=str(OUT), help=f"output JSONL (default {OUT.relative_to(ROOT)})")
    ap.add_argument("--no-check", action="store_true", help="do not fetch texts to verify IDs")
    ap.add_argument("--act-version", default=ACT_VERSION, help="e-Sbírka version date of the act")
    args = ap.parse_args(argv)

    data = Path(args.table).read_bytes() if args.table else download(TABLE_URL)
    rows = read_table(data)
    trows = table_rows(rows, celex=CELEX)
    links = links_from_rows(trows, ACT, source_url=TABLE_URL)
    print(f"table: {len(rows)} rows, {len(trows)} for {CELEX}, {len(links)} links", file=sys.stderr)

    if not args.no_check:
        eu = EurLexClient().document(CELEX, "cs")
        cz = ESbirkaClient().document(ACT, version=args.act_version)
        check_links(links, eu, cz, rows=trows)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_jsonl(links, out)
    summary = summarize(links)
    summary = {
        "source": {"tisk": TISK_URL, "table": TABLE_URL, "act": ACT, "act_version": args.act_version,
                   "celex": CELEX},
        **summary,
    }
    out.with_name(out.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    brief = {k: v for k, v in summary.items() if k not in ("eu_unresolved", "national_unresolved", "unparsed_refs")}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
