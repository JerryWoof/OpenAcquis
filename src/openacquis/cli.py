"""Command-line interface.

``openacquis summary|measures <CELEX>``: which national acts implement a directive (CELLAR).
``openacquis text <CELEX> --lang cs``: a directive's text split into provisions (CELLAR/EUR-Lex).
``openacquis law cz 264/2025``: a national act split into provisions (country adapters).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

from . import __version__
from .adapters.cz import ESbirkaClient
from .cellar import CellarClient, count_by_country
from .countries import MEMBER_STATES, normalize_country
from .eurlex import EurLexClient, counts, validate_celex
from .provisions import Document, make_id, roman_to_int

#: Countries with a national-law adapter (ISO alpha-3 -> printable code).
LAW_ADAPTERS = {"CZE": "cz"}


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


# --- provision texts ----------------------------------------------------------
def _select(doc: Document, provision_id: str | None) -> Document:
    """The whole document, or one provision with its descendants (``LookupError`` if absent)."""
    if not provision_id:
        return doc
    if ":" not in provision_id and doc.provisions:  # short form: "art23.par4"
        provision_id = f"{doc.provisions[0].id.rsplit(':', 1)[0]}:{provision_id}"
    if doc.get(provision_id) is None:
        raise LookupError(f"{provision_id} not found in {doc.act_id} ({len(doc)} provisions)")
    sub = Document(**doc.meta())
    for _, p in doc.walk(provision_id):
        sub.add(p)
    return sub


def _print_document(doc: Document, fmt: str) -> None:
    if fmt == "json":
        print(doc.to_json())
    elif fmt == "jsonl":
        sys.stdout.write(doc.to_jsonl())
    else:
        print(f"{doc.act_id}  {_short(doc.title, 160)}")
        if doc.url:
            print(doc.url)
        print()
        depth: dict[str, int] = {}
        for p in doc:  # document order; a selected subtree's top has no parent here
            depth[p.id] = depth[p.parent_id] + 1 if p.parent_id in depth else 0
            pad = "  " * depth[p.id]
            head = " ".join(x for x in (p.label, p.heading) if x)
            body = (p.text or "").replace("\n", "\n" + pad + "  ")
            if p.heading and body:
                print(f"{pad}{head}\n{pad}  {body}")
            else:
                print(f"{pad}{' '.join(x for x in (head, body) if x)}")
        c = counts(doc)
        print(f"\n{len(doc)} provisions (" + ", ".join(f"{k} {n}" for k, n in c.items()) + ")")


def cmd_text(args: argparse.Namespace, client: EurLexClient) -> int:
    celex = validate_celex(args.celex)
    doc = client.document(celex, args.lang, recitals=args.recitals, refresh=args.refresh)
    pid = args.id
    if args.article:
        pid = make_id("eu", celex, [("article", args.article)], doc.language)
    _print_document(_select(doc, pid), args.format)
    return 0


def cmd_law(args: argparse.Namespace, client: ESbirkaClient) -> int:
    doc = client.document(args.ref, args.version_date)
    pid = args.id
    if args.section:
        pid = make_id(doc.source, doc.act_id, [("par", args.section)])
    elif args.article:
        # článek IDs use the ELI number (cl1, cl2), labels are printed in roman ("Čl. I")
        number = roman_to_int(args.article) or args.article
        pid = make_id(doc.source, doc.act_id, [("cl", number)])
    _print_document(_select(doc, pid), args.format)
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

    t = sub.add_parser("text", help="Directive text in one language, split into provisions")
    t.add_argument("celex", help="CELEX number, e.g. 32022L2555 (NIS2)")
    t.add_argument("-l", "--lang", default="en", help="EU language code, e.g. en, cs, de (default: en)")
    t.add_argument("-a", "--article", help="Only this article and its paragraphs/points, e.g. 23")
    t.add_argument("--id", help="Only this provision, e.g. art23.par4 or a full ID")
    t.add_argument("--recitals", action="store_true", help="Include recitals")
    t.add_argument("--refresh", action="store_true", help="Ignore the on-disk cache")
    t.add_argument("-f", "--format", choices=["text", "json", "jsonl"], default="text")
    t.set_defaults(func=cmd_text, client="eurlex")

    w = sub.add_parser("law", help="National act split into provisions (countries: cz)")
    w.add_argument("country", help="Country with an adapter: CZ")
    w.add_argument("ref", help="Act reference, e.g. 264/2025 or an e-Sbírka URL")
    w.add_argument("-s", "--section", help="Only this § and its subdivisions, e.g. 12")
    w.add_argument("-a", "--article", help="Only this článek (Čl.), e.g. 1 or I")
    w.add_argument("--id", help="Only this provision, e.g. par12.odst2 or a full ID")
    w.add_argument(
        "--version-date",
        help="Consolidated version in force from this date (YYYY-MM-DD); 0000-00-00 = as promulgated",
    )
    w.add_argument("-f", "--format", choices=["text", "json", "jsonl"], default="text")
    w.set_defaults(func=cmd_law, client="law")
    return p


def _client(args: argparse.Namespace, cellar, eurlex, esbirka):
    kind = getattr(args, "client", "cellar")
    if kind == "eurlex":
        return eurlex or EurLexClient()
    if kind == "law":
        code = normalize_country(args.country)
        if code not in LAW_ADAPTERS:
            have = ", ".join(v.upper() for v in LAW_ADAPTERS.values())
            raise ValueError(f"no national-law adapter for {args.country} yet (available: {have})")
        return esbirka or ESbirkaClient()
    return cellar or CellarClient()


def main(
    argv: list[str] | None = None,
    client: CellarClient | None = None,
    eurlex: EurLexClient | None = None,
    esbirka: ESbirkaClient | None = None,
) -> int:
    """Run the CLI. The client arguments replace the network in tests."""
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, _client(args, client, eurlex, esbirka))
    except (ValueError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # e.g. `openacquis measures ... | head`
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
