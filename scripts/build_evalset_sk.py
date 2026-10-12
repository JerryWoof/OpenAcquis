#!/usr/bin/env python3
"""Build Slovak evaluation sets from the official correlation tables (*tabuľky zhody*).

Each government bill sent to the National Council (NR SR) carries one ``.docx`` table per
directive it transposes. This script parses the table, maps the references to OpenAcquis
provision IDs (EU: the Slovak language version from CELLAR; national: Slov-Lex, in the
version produced by the amendment), checks them against the texts and writes the set.

    python scripts/build_evalset_sk.py                       # all sets (nis2, cer, pay)
    python scripts/build_evalset_sk.py nis2                  # one set
    python scripts/build_evalset_sk.py nis2 --table t.docx   # a local copy of the table
    python scripts/build_evalset_sk.py --no-check            # do not fetch texts

Writes ``data/eval/{key}_sk.jsonl`` (positive links and negative records, one per line)
and ``data/eval/{key}_sk_summary.json``. Downloads are cached under ``OPENACQUIS_CACHE``
(``nrsr/`` for the tables, ``slovlex/`` and ``eurlex/`` for the texts).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from openacquis.adapters.sk import SlovLexClient, parse_reference
from openacquis.eurlex import EurLexClient
from openacquis.evalset import download
from openacquis.evalset_sk import (
    bill_articles,
    candidate_acts,
    check_sk_links,
    default_cache_dir,
    links_from_sk_rows,
    read_sk_table,
    save_jsonl,
    summarize_sk,
)

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "eval"
PRINT_URL = "https://www.nrsr.sk/web/Default.aspx?sid=zakony/cpt&ZakZborID=13&CisObdobia={term}&ID={print_}"
TABLE_URL = "https://www.nrsr.sk/web/Dynamic/Download.aspx?DocID={doc_id}"


@dataclass(frozen=True)
class SkSet:
    key: str
    celex: str
    term: int
    print_: int
    doc_id: int
    final_act: str  # the act the bill became
    in_force: str  # day the amendment took effect: other acts are checked in the version of that day

    @property
    def table_url(self) -> str:
        return TABLE_URL.format(doc_id=self.doc_id)

    @property
    def print_url(self) -> str:
        return PRINT_URL.format(term=self.term, print_=self.print_)


SETS = {
    s.key: s for s in (
        # NIS2: print 508 (9th term), act 366/2024 Z. z. amending 69/2018 Z. z.
        SkSet("nis2", "32022L2555", 9, 508, 553679, "366/2024", "2025-01-01"),
        # CER: print 516, new act 367/2024 Z. z. on critical infrastructure
        SkSet("cer", "32022L2557", 9, 516, 553726, "367/2024", "2025-01-01"),
        # Pay Transparency: print 1142, new act 76/2026 Z. z. on equal pay
        SkSet("pay", "32023L0970", 9, 1142, 583022, "76/2026", "2026-06-07"),
    )
}


def national_docs(links, rows, bills, cfg: SkSet, client: SlovLexClient, log) -> dict:
    """Fetch every act the links point into (and their candidate acts), in the version they need."""
    docs: dict = {}
    wanted: dict[str, str] = {}
    amended = {a for a in bills.values() if a and a != cfg.final_act}
    by_row = {r.index: r for r in rows}
    for lk in links:
        if lk.negative or not lk.national_act:
            continue
        if lk.act_version == "vyhlasene_znenie":
            wanted[f"{lk.national_act}@vyhlasene_znenie"] = "0000-00-00"
        elif lk.act_version.startswith("amended_by:"):
            wanted.setdefault(lk.national_act, lk.act_version)
        else:
            wanted.setdefault(lk.national_act, cfg.in_force)
        if lk.act_version != "vyhlasene_znenie":
            for act in candidate_acts(lk, by_row.get(lk.row), bills, cfg.final_act):
                wanted.setdefault(act, f"amended_by:{cfg.final_act}" if act in amended else cfg.in_force)
    for key, how in sorted(wanted.items()):
        act = key.split("@")[0]
        try:
            if how.startswith("amended_by:"):
                try:
                    doc = client.document(parse_reference(f"{act}-{how.split(':', 1)[1]}"))
                except LookupError:
                    doc = client.document(act, version=cfg.in_force)
            else:
                doc = client.document(act, version=how)
        except (LookupError, ValueError, OSError) as e:
            log(f"  {key}: not fetched ({e})")
            continue
        docs[key] = doc
        log(f"  {key}: {len(doc)} provisions, version {doc.date or 'as promulgated'}")
    return docs


def build(cfg: SkSet, table: str | None, check: bool, out_dir: Path) -> dict:
    def log(msg: str) -> None:
        print(msg, file=sys.stderr)

    data = Path(table).read_bytes() if table else download(cfg.table_url, cache_dir=default_cache_dir())
    rows = read_sk_table(data)
    client = SlovLexClient(today=None)
    # the articles of the amending act -> the acts they amend (one Slov-Lex request, cached)
    try:
        promulgated = client.document(cfg.final_act, version="0000-00-00")
        bills = bill_articles(promulgated)
    except (LookupError, OSError) as e:
        log(f"{cfg.key}: {cfg.final_act} as promulgated not fetched ({e}); Čl. I taken as the act itself")
        promulgated, bills = None, {1: cfg.final_act}
    log(f"{cfg.key}: {len(rows)} table rows; bill articles {bills}")
    links = links_from_sk_rows(rows, cfg.celex, cfg.final_act, bills, source_url=cfg.table_url)
    log(f"{cfg.key}: {sum(not lk.negative for lk in links)} links, {sum(lk.negative for lk in links)} negatives")
    if check:
        eu = EurLexClient().document(cfg.celex, "sk")
        docs = national_docs(links, rows, bills, cfg, client, log)
        if promulgated is not None:
            docs[f"{cfg.final_act}@vyhlasene_znenie"] = promulgated
        check_sk_links(links, eu, docs, rows=rows, bills=bills, final_act=cfg.final_act)
    out = out_dir / f"{cfg.key}_sk.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    save_jsonl(links, out)
    summary = {
        "source": {"print": cfg.print_url, "table": cfg.table_url, "celex": cfg.celex,
                   "final_act": cfg.final_act, "in_force": cfg.in_force, "bill_articles": bills},
        **summarize_sk(links, rows),
    }
    out.with_name(out.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sets", nargs="*", default=[], help=f"sets to build: {', '.join(SETS)} (default: all)")
    ap.add_argument("--table", help="local .docx of the table (only with one set)")
    ap.add_argument("--out-dir", default=str(OUT_DIR), help="output directory (default data/eval)")
    ap.add_argument("--no-check", action="store_true", help="do not fetch texts to verify IDs")
    args = ap.parse_args(argv)
    keys = args.sets or list(SETS)
    unknown = [k for k in keys if k not in SETS]
    if unknown:
        ap.error(f"unknown set(s) {', '.join(unknown)}; choose from {', '.join(SETS)}")
    if args.table and len(keys) != 1:
        ap.error("--table needs exactly one set")
    for key in keys:
        s = build(SETS[key], args.table, not args.no_check, Path(args.out_dir))
        brief = {k: v for k, v in s.items() if k not in ("eu_unresolved", "national_unresolved", "unparsed_refs")}
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
