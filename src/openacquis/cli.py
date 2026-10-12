"""Command-line interface.

``openacquis summary|measures <CELEX>``: which national acts implement a directive (CELLAR).
``openacquis text <CELEX> --lang cs``: a directive's text split into provisions (CELLAR/EUR-Lex).
``openacquis law cz 264/2025``: a national act split into provisions (adapters: cz, sk, at, de).
``openacquis align 32022L2555 sk``: candidate links, gap and extra-rule candidates (v0.3, v0.4).
``openacquis eval nis2-cz``: score the alignment against the official correlation table (v0.3).
``openacquis review validate FILE``: check human review records (v0.4).
``openacquis dataset build``: export the open dataset (CSV, JSON lines, Data Package; v0.4).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from typing import Any

from . import __version__
from .adapters import ADAPTERS, get_adapter
from .align import EMBEDDERS
from .cellar import CellarClient, count_by_country
from .countries import MEMBER_STATES, normalize_country
from .eurlex import EurLexClient, counts, validate_celex
from .provisions import Document, make_id, roman_to_int

#: Countries with a national-law adapter (ISO alpha-3 -> adapter code).
LAW_ADAPTERS = {a.alpha3: a.code for a in ADAPTERS.values()}
_HAVE = ", ".join(a.code for a in ADAPTERS.values())

#: Evaluation sets: name -> (CELEX, country).
EVALSETS = {"nis2-cz": ("32022L2555", "cz")}
JUDGES = ["none", "rule", "anthropic"]


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
    if args.map:
        return _print_mapped(args, client, measures)
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


def _print_mapped(args: argparse.Namespace, client: CellarClient, measures: list) -> int:
    """``measures --map``: how each notified measure maps to an act the country's adapter can fetch."""
    from .adapters import map_measures

    if not args.country:
        raise ValueError("--map needs --country (a country with an adapter: " + _HAVE + ")")
    a = get_adapter(normalize_country(args.country))
    rows = map_measures(a.code, measures, celex=validate_celex(args.celex), cellar=client)
    if args.format == "json":
        json.dump([r.to_dict() for r in rows], sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    if args.format == "csv":
        w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].to_dict()) if rows else ["uri"])
        w.writeheader()
        for r in rows:
            w.writerow({k: (";".join(v) if isinstance(v, list) else v) for k, v in r.to_dict().items()})
        return 0
    for r in rows:
        extra = f" ({', '.join(r.sections)})" if r.sections else ""
        print(f"{r.status:<12} {(r.ref or '-') + extra:<34} {_short(r.title, 90)}")
    n = sum(r.status == "mapped" for r in rows)
    print(f"\n{n} of {len(rows)} measures mapped to {a.portal} (openacquis law {a.code} <ref>)")
    return 0


def cmd_law(args: argparse.Namespace, client: Any) -> int:
    a = get_adapter(normalize_country(args.country))
    if args.version_date and not a.versions:
        raise ValueError(f"{a.portal} publishes only the current text; --version-date is not supported for {a.code}")
    doc = client.document(args.ref, args.version_date)
    pid = args.id
    if args.section:
        pid = make_id(doc.source, doc.act_id, [("par", args.section)])
        if a.code == "at" and doc.get(pid) is None:
            # §§ inside an Artikel (DSG: Art. 2 § 4) have IDs like art2.par4
            from .adapters import at

            pid = at.provision_id(doc, pid.rsplit(":", 1)[1]) or pid
    elif args.article:
        if a.article_kind == "cl":
            # článek IDs use the number (cl1, cl2), labels are printed in roman ("Čl. I")
            number = roman_to_int(args.article) or args.article
        else:
            number = args.article
        pid = make_id(doc.source, doc.act_id, [(a.article_kind, number)])
    _print_document(_select(doc, pid), args.format)
    return 0


# --- alignment and evaluation (v0.3, v0.4) ----------------------------------------
def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _judge(name: str):
    from .align import make_judge

    if name == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise ValueError("--judge anthropic needs ANTHROPIC_API_KEY and pip install 'openacquis[llm]'; "
                         "use --judge rule for the keyless heuristic baseline")
    return make_judge(name)


def _embedder(name: str, language: str = "cs"):
    """The embedder, checked before any text is fetched (dense models need an optional extra)."""
    from .align import choose_embedder

    emb = choose_embedder(name, language)
    if name == "auto":
        _log(f"embedder: {emb.name} (--embedder auto)")
    return emb


def _documents(args: argparse.Namespace, clients: dict, celex: str, country: str):
    from .align import load_documents

    a = get_adapter(normalize_country(country))
    version = args.version_date if args.version_date else "default"
    return load_documents(validate_celex(celex), a.code, args.acts or None, version=version,
                          eurlex=clients.get("eurlex"), national_client=clients.get(a.code),
                          measures=getattr(args, "measures", False), cellar=clients.get("cellar"), log=_log)


def cmd_align(args: argparse.Namespace, clients: dict) -> int:
    from .align import align

    a = get_adapter(normalize_country(args.country))
    embedder = _embedder(args.embedder, a.language)
    judge = _judge(args.judge)
    eu, national = _documents(args, clients, args.celex, args.country)
    result = align(eu, national, embedder=embedder, judge=judge, k=args.k, threshold=args.threshold,
                   judge_k=args.judge_k, gap_margin=args.gap_margin, log=_log)
    out = result.to_json(text_limit=args.text_limit)
    if args.output and args.output != "-":
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out + "\n")
    else:
        print(out)
    s = result.summary()
    _log(f"{eu.act_id} x {', '.join(d.act_id for d in national)} ({result.method}, threshold {result.threshold}): "
         f"{s['eu_units']} EU units, {s['eu_units_with_link']} with a link, {s['gap_candidates']} gap candidates "
         f"in {s.get('gap_articles', 0)} articles, {s['extra_rule_candidates']} extra-rule candidates in "
         f"{s.get('extra_rule_sections', 0)} sections of {s['transposing_act_units']} units of the main act "
         f"{result.transposing_acts[0] if result.transposing_acts else ''}"
         + (f" -> {args.output}" if args.output and args.output != "-" else ""))
    _log("Machine suggestions for review, not findings.")
    return 0


def _ks(text: str) -> list[int]:
    try:
        ks = sorted({int(x) for x in text.split(",") if x.strip()})
    except ValueError:
        raise ValueError(f"--k expects comma-separated integers, e.g. 1,5,10 (got {text!r})") from None
    if not ks or ks[0] < 1:
        raise ValueError("--k values must be positive")
    return ks


def cmd_eval(args: argparse.Namespace, clients: dict) -> int:
    from .align import gold_path
    from .evaluate import errors, evaluate, format_report, load_gold, public

    celex, country = EVALSETS[args.dataset]
    path = args.gold or gold_path(celex, country)
    if path is None:
        raise LookupError(f"evaluation set for {args.dataset} not found; pass --gold data/eval/nis2_cz.jsonl")
    gold = load_gold(path)
    embedder = _embedder(args.embedder, get_adapter(country).language)
    judge = _judge(args.judge)
    eu, national = _documents(args, clients, celex, country)
    report = evaluate(eu, national, gold, embedder=embedder, judge=judge,
                      ks=_ks(args.k), threshold=args.threshold, judge_k=args.judge_k, log=_log)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(public(report), f, ensure_ascii=False, indent=1)
            f.write("\n")
    if args.format == "json":
        print(json.dumps(public(report), ensure_ascii=False, indent=1))
        return 0
    print(format_report(report))
    if args.errors:
        print(f"\nGold EU provisions with no gold national provision in the top 5 (first {args.errors}):")
        for e in errors(report, limit=args.errors):
            short = [x.split(":", 2)[-1] for x in e["top"]]
            gold_ids = [x.split(":", 2)[-1] for x in e["gold"]]
            more = f" (+{len(gold_ids) - 4})" if len(gold_ids) > 4 else ""
            print(f"  {e['eu_id'].split(':')[-1]:<18} gold {', '.join(gold_ids[:4])}{more}")
            print(f"  {'':<18} top  {', '.join(short)}")
    return 0


# --- review and dataset (v0.4) -------------------------------------------------------
def cmd_review(args: argparse.Namespace, _client: Any = None) -> int:
    from .review import ReviewError, append_records, load_reviews, merge, records_from_text, validate_record

    if args.action == "stats":
        records = load_reviews(args.dir)
        decisions = merge(records)
        by: dict[str, int] = {}
        for d in decisions:
            by[d.status] = by.get(d.status, 0) + 1
        print(f"{len(records)} review records, {len(decisions)} suggestions reviewed: "
              + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
        return 0
    if not args.files:
        raise ValueError(f"review {args.action} needs one or more files ('-' = stdin)")
    records: list[dict] = []
    problems: list[str] = []
    for name in args.files:
        text = sys.stdin.read() if name == "-" else open(name, encoding="utf-8").read()
        try:
            found = records_from_text(text)
        except (ValueError, json.JSONDecodeError) as exc:
            problems.append(f"{name}: {exc}")
            continue
        for i, rec in enumerate(found, 1):
            try:
                records.append(validate_record(rec))
            except ReviewError as exc:
                problems.append(f"{name}: record {i}: {exc}")
                continue
            if args.author and rec["reviewer"].lower() != args.author.lower():
                problems.append(f"{name}: record {i}: reviewer {rec['reviewer']!r} is not {args.author!r}")
    if args.format == "json":
        print(json.dumps({"valid": not problems, "records": len(records), "errors": problems}, ensure_ascii=False))
    else:
        for msg in problems:
            print(f"invalid: {msg}", file=sys.stderr)
        if not problems:
            print(f"{len(records)} valid review record(s)")
    if problems:
        return 1
    if args.action == "add":
        for path in append_records(records, args.dir):
            print(f"appended to {path}")
    return 0


def cmd_dataset(args: argparse.Namespace, _client: Any = None) -> int:
    from .dataset import build_dataset

    s = build_dataset(args.out, reviews_dir=args.reviews)
    print(f"{s.rows} links ({s.official} from official correlation tables, {s.reviewed} from human review), "
          f"{s.review_records} review records -> {args.out}")
    for k, v in sorted(s.by_pair.items()):
        print(f"  {k}: {v}")
    return 0


def _add_alignment_options(a: argparse.ArgumentParser) -> None:
    a.add_argument("-e", "--embedder", choices=EMBEDDERS, default="auto",
                   help="auto (default: hybrid with openacquis[embeddings] installed, else hybrid-lex); "
                        "lexical, bm25, hybrid-lex (no dependencies); e5, e5-sym, bge-m3, hybrid, hybrid-rrf "
                        "(need openacquis[embeddings]; hybrid-rrf gives links only, no gap list)")
    a.add_argument("-j", "--judge", choices=JUDGES, default="none",
                   help="none (default), rule (keyless heuristics) or anthropic (needs ANTHROPIC_API_KEY)")
    a.add_argument("--threshold", type=float, help="minimum score for a predicted link (default per embedder)")
    a.add_argument("--judge-k", type=int, default=3, help="candidates per EU provision sent to the judge")
    a.add_argument("--acts", nargs="+", metavar="REF",
                   help="national acts to search, main transposing act first (default: per directive and country)")
    a.add_argument("--version-date", help="consolidated version of the national acts (format depends on the country)")


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
    m.add_argument("--map", action="store_true",
                   help=f"show which act each measure maps to in the country's adapter ({_HAVE})")
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

    examples = "; ".join(f"{a.code}: {a.example}" for a in ADAPTERS.values())
    w = sub.add_parser("law", help=f"National act split into provisions (countries: {_HAVE})")
    w.add_argument("country", help=f"Country with an adapter: {_HAVE.upper()}")
    w.add_argument("ref", help=f"Act reference ({examples}) or a portal URL")
    w.add_argument("-s", "--section", help="Only this § and its subdivisions, e.g. 12")
    w.add_argument("-a", "--article", help="Only this article: Czech/Slovak článok (1 or I), Austrian/German Artikel")
    w.add_argument("--id", help="Only this provision, e.g. par12.odst2 or a full ID")
    w.add_argument(
        "--version-date",
        help="Consolidated version: CZ YYYY-MM-DD (0000-00-00 = as promulgated); SK YYYY-MM-DD, YYYYMMDD or "
             "0000-00-00; AT YYYY-MM-DD (text in force on that day); DE not supported (current text only)",
    )
    w.add_argument("-f", "--format", choices=["text", "json", "jsonl"], default="text")
    w.set_defaults(func=cmd_law, client="law")

    a = sub.add_parser("align", help="Candidate links between a directive and national law (machine suggestions)")
    a.add_argument("celex", help="Directive CELEX number, e.g. 32022L2555 (NIS2)")
    a.add_argument("country", help=f"Country with an adapter: {_HAVE.upper()}")
    _add_alignment_options(a)
    a.add_argument("--measures", action="store_true",
                   help="also search every notified measure (CELLAR) the adapter can fetch")
    a.add_argument("--gap-margin", type=float,
                   help="gap candidates must score this far below the threshold (default 0.01; 0 = v0.3)")
    a.add_argument("-k", type=int, default=5, help="candidates kept per EU provision (default 5)")
    a.add_argument("--text-limit", type=int, default=600, help="characters of text kept per provision")
    a.add_argument("-o", "--output", help="write JSON here (default: stdout)")
    a.set_defaults(func=cmd_align, client="align")

    e = sub.add_parser("eval", help="Score the alignment against an official correlation table")
    e.add_argument("dataset", choices=sorted(EVALSETS), help="evaluation set")
    _add_alignment_options(e)
    e.add_argument("--k", default="1,5,10", help="cut-offs for recall@k (default 1,5,10)")
    e.add_argument("--gold", help="gold links JSONL (default: data/eval/nis2_cz.jsonl)")
    e.add_argument("--errors", type=int, default=0, metavar="N", help="also list N retrieval misses")
    e.add_argument("-f", "--format", choices=["text", "json"], default="text")
    e.add_argument("-o", "--output", help="also write the JSON report here")
    e.set_defaults(func=cmd_eval, client="align")

    r = sub.add_parser("review", help="Human review records: validate, add to data/reviews/, statistics")
    r.add_argument("action", choices=["validate", "add", "stats"],
                   help="validate: check files; add: check and append to data/reviews/; stats: summary")
    r.add_argument("files", nargs="*", help="JSONL files or GitHub issue bodies with ```json blocks ('-' = stdin)")
    r.add_argument("--dir", help="reviews directory (default: data/reviews)")
    r.add_argument("--author", metavar="LOGIN",
                   help="also require every record's reviewer to be this GitHub user (case-insensitive), "
                        "e.g. the author of the pull request that adds the records")
    r.add_argument("-f", "--format", choices=["text", "json"], default="text")
    r.set_defaults(func=cmd_review, client="none")

    d = sub.add_parser("dataset", help="Export the open dataset (CSV, JSON lines, Data Package, CC-BY-4.0)")
    d.add_argument("action", choices=["build"])
    d.add_argument("-o", "--out", default="dataset", help="output directory (default: dataset/)")
    d.add_argument("--reviews", default=None, help="reviews directory (default: data/reviews)")
    d.set_defaults(func=cmd_dataset, client="none")
    return p


def _client(args: argparse.Namespace, cellar, eurlex, national: dict[str, Any]):
    kind = getattr(args, "client", "cellar")
    if kind == "none":
        return None
    if kind == "align":
        return {"eurlex": eurlex, "cellar": cellar, **national}
    if kind == "eurlex":
        return eurlex or EurLexClient()
    if kind == "law":
        a = get_adapter(normalize_country(args.country))
        return national.get(a.code) or a.make_client()
    return cellar or CellarClient()


def main(
    argv: list[str] | None = None,
    client: CellarClient | None = None,
    eurlex: EurLexClient | None = None,
    esbirka: Any = None,
    slovlex: Any = None,
    ris: Any = None,
    gii: Any = None,
) -> int:
    """Run the CLI. The client arguments replace the network in tests (``esbirka`` = Czech,
    ``slovlex`` = Slovak, ``ris`` = Austrian, ``gii`` = German adapter client)."""
    args = build_parser().parse_args(argv)
    national = {k: v for k, v in {"cz": esbirka, "sk": slovlex, "at": ris, "de": gii}.items() if v is not None}
    if getattr(args, "dir", "unset") is None:
        args.dir = None
    if getattr(args, "reviews", "unset") is None:
        from .review import REVIEWS_DIR

        args.reviews = REVIEWS_DIR
    try:
        return args.func(args, _client(args, client, eurlex, national))
    except (ValueError, LookupError, ImportError, OSError) as exc:  # ImportError: a missing optional extra
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # e.g. `openacquis measures ... | head`
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
