"""The open dataset: transposition links with their sources, as CSV, JSON lines and a Data Package.

``openacquis dataset build`` writes ``dataset/``::

    dataset/links.csv            one row per (EU provision, national provision) link
    dataset/links.jsonl          the same rows as JSON lines
    dataset/reviews.jsonl        every human review record (provenance, including rejections)
    dataset/datapackage.json     Frictionless Data Package: licence (CC-BY-4.0), sources, field schema
    dataset/README.md            what is in it, how to cite it

Rows come from two kinds of sources, kept apart by ``source``:

* ``official_correlation_table``: links a government asserted in a correlation table (for now
  NIS2 x Czechia, the *rozdílová tabulka* of sněmovní tisk 759/0; ``data/eval/``);
* ``human_review``: machine suggestions a reviewer confirmed or corrected (``data/reviews/``,
  :mod:`openacquis.review`). Rejected and disputed suggestions are not links; they stay in
  ``reviews.jsonl``. A review of a *baseline* pair (registry status ``baseline``: the act aligned
  was not notified for the directive) confirms that existing national law covers the EU
  provision, not that it transposes it: such rows have ``relation = existing_law``.

Unreviewed machine suggestions are **not** part of the dataset. Nothing in it says whether a
provision is correctly transposed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .directives import RegistryError, load_registry
from .goldsets import GOLD_SETS
from .review import REVIEWS_DIR, load_reviews, merge

REPO_ROOT = Path(__file__).resolve().parents[2]
LICENSE = {"name": "CC-BY-4.0", "path": "https://creativecommons.org/licenses/by/4.0/",
           "title": "Creative Commons Attribution 4.0 International"}

#: Official correlation tables included: (CELEX, country) -> metadata (from :mod:`openacquis.goldsets`).
OFFICIAL_TABLES: dict[tuple[str, str], dict] = {
    gs.key: {"name": gs.name, "path": gs.path, "title": gs.title, "url": gs.url, "history_url": gs.history_url,
             "publisher": gs.publisher, "licence_note": gs.licence_note, "national_version": gs.version}
    for gs in GOLD_SETS.values()
}

#: Where the provision texts and IDs come from, by ID prefix.
TEXT_SOURCES = [
    {"title": "EUR-Lex / CELLAR (EU Publications Office)", "path": "https://eur-lex.europa.eu",
     "description": "Directive texts. © European Union, https://eur-lex.europa.eu; reuse under Commission "
                    "Decision 2011/833/EU."},
    {"title": "e-Sbírka (Czech Collection of Laws)", "path": "https://e-sbirka.gov.cz",
     "description": "Czech national law (IDs cz:...). Official works."},
    {"title": "Slov-Lex (Slovak Collection of Laws)", "path": "https://www.slov-lex.sk",
     "description": "Slovak national law (IDs sk:...). Official works."},
    {"title": "RIS, Rechtsinformationssystem des Bundes (Austria)", "path": "https://www.ris.bka.gv.at",
     "description": "Austrian federal law (IDs at:...), open government data (CC BY 4.0)."},
    {"title": "gesetze-im-internet.de (Germany)", "path": "https://www.gesetze-im-internet.de",
     "description": "German federal law (IDs de:...). Official works (§ 5 UrhG)."},
]

FIELDS: list[tuple[str, str, str]] = [
    ("link_id", "string", "Stable ID of the row: hash of directive, EU provision, national provision and source"),
    ("directive", "string", "CELEX number of the directive, e.g. 32022L2555"),
    ("country", "string", "ISO 3166-1 alpha-2 code, lower case"),
    ("eu_provision", "string", "Provision of the directive, language-independent: art23.par4.pt_a"),
    ("eu_id", "string", "OpenAcquis ID of the EU provision in the language version used (eu:CELEX:lang:...)"),
    ("national_act", "string", "National act as the adapter names it (264/2025, 69/2018, 20013065, bsig_2025)"),
    ("national_id", "string", "OpenAcquis ID of the national provision (cc:act:...)"),
    ("source", "string", "official_correlation_table | human_review"),
    ("relation", "string", "transposition (the national provision is cited as transposing the EU provision) | "
                           "existing_law (human review of an act not notified for the directive: existing law that "
                           "covers the EU provision, not a notified transposition)"),
    ("status", "string", "listed (official table, no compatibility stated) | fully_compatible | partially_compatible | "
                         "incompatible (official table with a compatibility column, as the government stated it) | "
                         "confirmed | corrected (human review)"),
    ("source_title", "string", "Correlation table or 'OpenAcquis review'"),
    ("source_url", "string", "Where the source can be checked"),
    ("reviewers", "string", "GitHub handles of the reviewers, ';'-separated (human_review only)"),
    ("review_date", "string", "Date of the latest review (human_review only)"),
    ("machine_score", "number", "Score of the machine suggestion that was reviewed (human_review only)"),
    ("machine_method", "string", "Matcher that made the suggestion (human_review only)"),
    ("raw_eu_ref", "string", "EU reference as printed in the official table"),
    ("raw_national_ref", "string", "National reference as printed in the official table"),
    ("note", "string", "Qualifiers (sentence, subparagraph), renumbering, reviewer notes"),
]


@dataclass
class DatasetSummary:
    rows: int
    official: int
    reviewed: int
    review_records: int
    by_pair: dict[str, int]
    files: list[str]


def _eu_local(eu_id: str) -> str:
    return eu_id.split(":", 3)[3] if eu_id.count(":") >= 3 else eu_id


def _act(nid: str) -> str:
    parts = nid.split(":")
    return parts[1] if len(parts) > 2 else ""


def _link_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _baseline_pairs() -> set[tuple[str, str]]:
    """(CELEX, country) pairs whose registry status is ``baseline`` (empty if the registry is unreadable)."""
    try:
        return {(d.celex, cc) for d in load_registry() for cc, c in d.countries.items() if c.status == "baseline"}
    except (RegistryError, OSError):
        return set()


def official_rows(root: Path = REPO_ROOT) -> list[dict]:
    """Rows from every correlation table in :data:`OFFICIAL_TABLES`."""
    out = []
    for (celex, cc), meta in OFFICIAL_TABLES.items():
        path = root / meta["path"]
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            g = json.loads(line)
            if not g.get("national_id"):
                continue  # a negative record (Slovak tables: "nothing transposes this"), not a link
            out.append({
                "link_id": _link_id(celex, _eu_local(g["eu_id"]), g["national_id"], "official"),
                "directive": celex, "country": cc,
                "eu_provision": _eu_local(g["eu_id"]), "eu_id": g["eu_id"],
                "national_act": _act(g["national_id"]), "national_id": g["national_id"],
                "source": "official_correlation_table", "relation": "transposition",
                "status": g.get("status") or "listed",
                "source_title": meta["title"], "source_url": g.get("source_url") or meta["url"],
                "reviewers": "", "review_date": "", "machine_score": None, "machine_method": "",
                "raw_eu_ref": g.get("raw_eu_ref", ""), "raw_national_ref": g.get("raw_national_ref", ""),
                "note": g.get("note", ""),
            })
    return out


def reviewed_rows(records: Sequence[dict], baseline: set[tuple[str, str]] | None = None) -> list[dict]:
    """Links supported by merged reviews (confirmed or corrected; not rejected or disputed).

    ``baseline``: (CELEX, country) pairs aligned against existing law not notified for the directive
    (default: the registry's ``baseline`` entries); their rows get ``relation = existing_law``."""
    baseline = _baseline_pairs() if baseline is None else baseline
    out = []
    for d in merge(records):
        for eu_id, nid in d.links():
            out.append({
                "link_id": _link_id(d.directive, _eu_local(eu_id), nid, "review"),
                "directive": d.directive, "country": d.country,
                "eu_provision": _eu_local(eu_id), "eu_id": eu_id,
                "national_act": _act(nid), "national_id": nid,
                "source": "human_review",
                "relation": "existing_law" if (d.directive, d.country) in baseline else "transposition",
                "status": d.status,
                "source_title": "OpenAcquis review",
                "source_url": f"https://github.com/JerryWoof/OpenAcquis/blob/main/data/reviews/{d.directive}_{d.country}.jsonl",
                "reviewers": ";".join(d.reviewers), "review_date": d.last_date,
                "machine_score": d.machine.get("score"), "machine_method": d.machine.get("method") or "",
                "raw_eu_ref": "", "raw_national_ref": "",
                "note": " | ".join(d.notes),
            })
    return out


def _dedupe(rows: Iterable[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        if r["link_id"] in seen:
            continue
        seen.add(r["link_id"])
        out.append(r)
    return out


#: A spreadsheet treats a cell starting with one of these as a formula.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> object:
    """A CSV cell: ``None`` -> empty; a string a spreadsheet would run as a formula gets a ``'`` prefix.

    Only links.csv is guarded; links.jsonl keeps the values unchanged.
    """
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _csv(rows: Sequence[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=[f for f, _, _ in FIELDS], lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: _cell(r.get(k)) for k, _, _ in FIELDS})
    return buf.getvalue()


def datapackage(rows: Sequence[dict], review_records: int, now: datetime) -> dict:
    tables = [{"title": m["title"], "path": m["url"], "description": m["licence_note"]}
              for m in OFFICIAL_TABLES.values()]
    return {
        # Not "tabular-data-package": that profile requires every resource to be tabular, and the
        # two JSON-lines resources are not.
        "profile": "data-package",
        "name": "openacquis-transposition-links",
        "title": "OpenAcquis: EU directive provisions and the national provisions that transpose them",
        "description": ("Provision-level links between EU directives and national law, from official correlation "
                        "tables and from human review of machine suggestions. Reference data, not legal advice: "
                        "a link says that a source paired two provisions, not that the transposition is correct."),
        "version": __version__,
        "created": now.isoformat().replace("+00:00", "Z"),
        "homepage": "https://jerrywoof.github.io/OpenAcquis/",
        "licenses": [LICENSE],
        "contributors": [{"title": "OpenAcquis contributors", "path": "https://github.com/JerryWoof/OpenAcquis",
                          "role": "author"}],
        "sources": TEXT_SOURCES + tables,
        "resources": [
            {
                "name": "links", "path": "links.csv", "profile": "tabular-data-resource", "format": "csv",
                "mediatype": "text/csv", "encoding": "utf-8", "count": len(rows),
                "schema": {"fields": [{"name": n, "type": t, "description": d} for n, t, d in FIELDS],
                           "primaryKey": ["link_id"]},
            },
            {"name": "links-jsonl", "path": "links.jsonl", "format": "jsonl", "mediatype": "application/jsonl",
             "description": "The same rows as links.csv, one JSON object per line"},
            {"name": "reviews", "path": "reviews.jsonl", "format": "jsonl", "mediatype": "application/jsonl",
             "count": review_records,
             "description": "Every human review record (confirm / reject / correct), see docs/review.md"},
        ],
    }


def _readme(summary: DatasetSummary, now: datetime) -> str:
    pairs = "\n".join(f"| {k} | {v} |" for k, v in sorted(summary.by_pair.items())) or "| (none) | 0 |"
    return f"""# OpenAcquis dataset

Built {now.date().isoformat()} by openacquis {__version__}. Licence: **CC-BY-4.0**
(https://creativecommons.org/licenses/by/4.0/). Cite as: *OpenAcquis contributors, OpenAcquis transposition
links, version {__version__}, https://github.com/JerryWoof/OpenAcquis*.

| File | Content |
|---|---|
| `links.csv`, `links.jsonl` | {summary.rows} links: {summary.official} from official correlation tables, {summary.reviewed} from human review |
| `reviews.jsonl` | {summary.review_records} review records (including rejections) |
| `datapackage.json` | Frictionless Data Package: field descriptions, licence, sources |

| Directive × country | Links |
|---|---:|
{pairs}

**What a row means.** `source = official_correlation_table`: a government listed the pair in the correlation
table attached to its bill (`status = listed`; the table states no compatibility). `source = human_review`: an
OpenAcquis reviewer confirmed a machine suggestion or corrected it. `relation = existing_law` marks reviews of
an act that was not notified for the directive (registry status `baseline`): existing law that covers the EU
provision, not a notified transposition. Neither source says whether a provision is correctly transposed.
Unreviewed machine suggestions are not included.

**Sources and attribution.** EU texts: © European Union, https://eur-lex.europa.eu (reuse under Commission
Decision 2011/833/EU). National texts: e-Sbírka (CZ), Slov-Lex (SK), RIS (AT, open government data),
gesetze-im-internet.de (DE). Czech correlation table: sněmovní tisk 759/0, psp.cz, an official work.
"""


def build_dataset(
    out_dir: Path | str = REPO_ROOT / "dataset",
    *,
    reviews_dir: Path | str = REVIEWS_DIR,
    root: Path = REPO_ROOT,
    now: datetime | None = None,
) -> DatasetSummary:
    """Write the dataset files to ``out_dir`` and return what was written."""
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records = load_reviews(reviews_dir) if Path(reviews_dir).exists() else []
    off = official_rows(root)
    rev = reviewed_rows(records)
    rows = _dedupe(off + rev)
    rows.sort(key=lambda r: (r["directive"], r["country"], r["source"], r["eu_provision"], r["national_id"]))
    by_pair: dict[str, int] = {}
    for r in rows:
        key = f"{r['directive']} × {r['country'].upper()}"
        by_pair[key] = by_pair.get(key, 0) + 1
    (out / "links.csv").write_text(_csv(rows), encoding="utf-8")
    (out / "links.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows), encoding="utf-8")
    (out / "reviews.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in records), encoding="utf-8")
    summary = DatasetSummary(len(rows), sum(r["source"] == "official_correlation_table" for r in rows),
                             sum(r["source"] == "human_review" for r in rows), len(records), by_pair,
                             ["links.csv", "links.jsonl", "reviews.jsonl", "datapackage.json", "README.md"])
    (out / "datapackage.json").write_text(json.dumps(datapackage(rows, len(records), now), ensure_ascii=False,
                                                     indent=1) + "\n", encoding="utf-8")
    (out / "README.md").write_text(_readme(summary, now), encoding="utf-8")
    return summary
