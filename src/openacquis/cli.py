"""Command-line interface: ``openacquis summary|measures <CELEX>``."""

from __future__ import annotations

import argparse
import csv
import json
import sys

from . import __version__
from .cellar import CellarClient, count_by_country
from .countries import MEMBER_STATES


def _short(text: str | None, width: int) -> str:
    if not text:
        return ""
    return text if len(text) <= width else text[: width - 1] + "…"


def cmd_summary(args: argparse.Namespace, client: CellarClient) -> int:
    d = client.directive(args.celex)
    measures = client.national_measures(args.celex)
    counts = count_by_country(measures)
    print(f"{d.celex}  {_short(d.title, 160)}")
    if d.transposition_deadlines:
        print(f"Transposition deadline: {', '.join(d.transposition_deadlines)}")
    print(f"National measures notified: {len(measures)} from {len(counts)} member states")
    print(f"EUR-Lex: {d.eurlex_nim_url}\n")
    for code, name in sorted(MEMBER_STATES.items(), key=lambda kv: (-counts.get(kv[0], 0), kv[0])):
        n = counts.get(code, 0)
        bar = "█" * min(n, 60)
        print(f"  {code} {name:<12} {n:>4} {bar}" if n else f"  {code} {name:<12}    0  (none notified)")
    return 0


def cmd_measures(args: argparse.Namespace, client: CellarClient) -> int:
    measures = client.national_measures(args.celex, country=args.country)
    if args.format == "json":
        json.dump([m.to_dict() for m in measures], sys.stdout, ensure_ascii=False, indent=2)
        print()
    elif args.format == "csv":
        fields = list(measures[0].to_dict()) if measures else ["uri"]
        w = csv.DictWriter(sys.stdout, fieldnames=fields)
        w.writeheader()
        for m in measures:
            w.writerow(m.to_dict())
    else:
        for m in measures:
            print(
                f"{m.country}  {m.journal_date or '':<10}  {m.reference or '':<14}  "
                f"{(m.act_type or ''):<12}  {_short(m.title, 90)}"
            )
        print(f"\n{len(measures)} measures")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="openacquis",
        description="Explore how EU directives are transposed into national law.",
    )
    p.add_argument("--version", action="version", version=f"openacquis {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("summary", help="Directive title, deadline and measures per country")
    s.add_argument("celex", help="Directive CELEX number, e.g. 32022L2555 (NIS2)")
    s.set_defaults(func=cmd_summary)

    m = sub.add_parser("measures", help="List notified national measures")
    m.add_argument("celex", help="Directive CELEX number, e.g. 32022L2555 (NIS2)")
    m.add_argument("-c", "--country", help="Member state, e.g. CZ or CZE")
    m.add_argument("-f", "--format", choices=["table", "json", "csv"], default="table")
    m.set_defaults(func=cmd_measures)
    return p


def main(argv: list[str] | None = None, client: CellarClient | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, client or CellarClient())
    except (ValueError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # e.g. `openacquis measures ... | head`
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
