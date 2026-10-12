#!/usr/bin/env python3
"""Build the static, read-only public API (``site/api/v1/``) from the website data.

Run after ``scripts/build_site.py``. It reads what is already there, fetches nothing, and writes
versioned JSON files that GitHub Pages serves as plain files::

    site/api/v1/index.json                              entry point: versions, counts, every path
    site/api/v1/openapi.yaml                            OpenAPI 3.1 description (copied from api/)
    site/api/v1/schemas/<name>.schema.json              JSON Schema 2020-12 of every file (copied from api/)
    site/api/v1/schemas/index.json                      list of the schema files (the index links here)
    site/api/v1/directives.json                         the curated directives
    site/api/v1/directives/<CELEX>.json                 one directive: metadata, counts, available pairs
    site/api/v1/directives/<CELEX>/measures.json        notified national measures (CELLAR)
    site/api/v1/directives/<CELEX>/matrix.json          article x country matrix
    site/api/v1/directives/<CELEX>/eu/<lang>/provisions.json
                                                        EU provisions (label, URL, text) in one language
    site/api/v1/directives/<CELEX>/<cc>/alignment.json  provision-level alignment without texts
    site/api/v1/directives/<CELEX>/<cc>/links.json      dataset links (official table + human review)
    site/api/v1/directives/<CELEX>/<cc>/candidates.json unreviewed machine suggestions (candidates)
    site/api/v1/directives/<CELEX>/<cc>/reviews.json    human review records
    site/api/v1/acts/<cc>/<act_slug>.json               national act metadata
    site/api/v1/acts/<cc>/<act_slug>/provisions.json    national provisions with text, stored once per act
    site/api/v1/dataset/                                the open dataset (links.csv, links.jsonl, ...)

Inputs: ``site/data/`` as written by build_site.py (CELLAR directives and measures, alignment and review
pages); where an alignment or review page is missing there, the committed ``site/static-data/`` copy;
``data/eval/`` (official correlation tables) and ``data/reviews/`` (human reviews) for the dataset.

Every JSON file except the bulk files has one envelope: ``$schema``, ``api_version``,
``dataset_version``, ``built_at``, ``generator``, ``mode`` (``live``, ``static`` or
``offline-sample``: where the data came from), ``license`` and ``data``.

Usage::

    python scripts/build_api.py                 # site/data + site/static-data -> site/api/v1
    python scripts/build_api.py --check         # also validate every file against its schema
    python scripts/build_api.py --check-only    # validate an existing build, write nothing

Machine suggestions in the API are candidates for human review, never findings about whether a
member state transposed a directive correctly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin

from openacquis import __version__

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
API_SRC = ROOT / "api"
API_VERSION = "1"
BASE_URL = "https://jerrywoof.github.io/OpenAcquis/api/v1/"
SCHEMA_BASE = BASE_URL + "schemas/"
LICENSE = "CC-BY-4.0"
RELEASES_URL = "https://github.com/JerryWoof/OpenAcquis/releases"
#: Zenodo concept DOI (all versions). Set it after the first archived release (docs/api.md).
CONCEPT_DOI: str | None = None
NOTICE = ("Machine suggestions (alignment, candidates) are candidates for human review, not findings: they "
          "say nothing about whether a provision is transposed correctly. Links come from official "
          "correlation tables or human review; see docs/dataset.md.")

_PAIR_FILE = re.compile(r"(3\d{4}L\d{4})_([a-z]{2})\.json")
_CELEX = re.compile(r"3\d{4}L\d{4}")


def _load_build_site():
    """``scripts/build_site.py`` as a module (curated lists, matrix builder)."""
    name = "build_site"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / "build_site.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


def act_slug(act_id: str) -> str:
    """URL-safe act ID: ``264/2025`` -> ``264-2025``; ``bsig_2025`` and ``20013065`` stay as they are."""
    s = re.sub(r"[^a-z0-9_-]", "", act_id.lower().replace("/", "-").replace(" ", "-"))
    return s or "act"


def _iso(now: datetime) -> str:
    return now.replace(microsecond=0).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _truncated(text: str | None) -> bool:
    return bool(text) and text.endswith("…")  # build_site._clip marks clipped texts with an ellipsis


def _local(eu_id: str) -> str:
    return eu_id.split(":", 3)[3] if eu_id.count(":") >= 3 else eu_id


def _act_of(national_id: str) -> str:
    parts = national_id.split(":")
    return parts[1] if len(parts) > 2 else ""


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class ApiSummary:
    out: Path
    files: int = 0
    bytes: int = 0
    directives: int = 0
    pairs: list[str] = field(default_factory=list)
    links: int = 0
    reviews: int = 0
    candidates: int = 0
    acts: int = 0
    eu_provisions: int = 0
    national_provisions: int = 0
    modes: dict[str, str] = field(default_factory=dict)


class _Writer:
    """Writes envelopes under ``out`` and counts what it wrote."""

    def __init__(self, out: Path, built_at: str, summary: ApiSummary):
        self.out, self.built_at, self.summary = out, built_at, summary

    def envelope(self, schema: str, data: Any, mode: str) -> dict:
        return {
            "$schema": SCHEMA_BASE + f"{schema}.schema.json",
            "api_version": API_VERSION,
            "dataset_version": __version__,
            "built_at": self.built_at,
            "generator": f"openacquis {__version__}",
            "mode": mode,
            "license": LICENSE,
            "data": data,
        }

    def write(self, rel: str, schema: str, data: Any, mode: str, *, indent: int | None = None) -> str:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(self.envelope(schema, data, mode), ensure_ascii=False, allow_nan=False,
                          indent=indent, separators=None if indent else (",", ":"))
        path.write_text(body + "\n", encoding="utf-8")
        self.summary.files += 1
        self.summary.bytes += len(body.encode("utf-8")) + 1
        return rel


# --- inputs ---------------------------------------------------------------------------------


def _pair_pages(site_data: Path, static_dir: Path) -> dict[str, dict]:
    """``{slug: {"alignment": page, "review": page | None}}`` for the pairs the website publishes
    (``build_site.ALIGNMENTS``, plus any other page in site/data/align): site/data first, then the
    committed static copy. Static copies of pairs the website does not list are not published."""
    slugs = {a.slug for a in _load_build_site().ALIGNMENTS}
    if (site_data / "align").is_dir():
        slugs |= {p.stem for p in (site_data / "align").glob("3*.json") if _PAIR_FILE.fullmatch(p.name)}
    pages = {}
    for slug in sorted(slugs):
        built = site_data / "align" / f"{slug}.json"
        if not built.is_file() and not (static_dir / f"{slug}.json").is_file():
            continue
        if built.is_file():
            align = _read(built)
            rpath = site_data / "review" / f"{slug}.json"
        else:
            align = _read(static_dir / f"{slug}.json")
            align["mode"] = "static"
            rpath = static_dir / f"{slug}.review.json"
        review = _read(rpath) if rpath.is_file() else None
        if review is not None and not built.is_file():
            review["mode"] = "static"
        pages[slug] = {"alignment": align, "review": review}
    return pages


def _directive_records(site_data: Path, celexes_needed: set[str]) -> tuple[dict | None, list[dict]]:
    """``(site index, records with measures)``; curated metadata only when site/data has no index."""
    bs = _load_build_site()
    index_path = site_data / "index.json"
    index = _read(index_path) if index_path.is_file() else None
    records = []
    if index is not None:
        for d in index.get("directives", []):
            p = site_data / f"{d['celex']}.json"
            rec = _read(p) if p.is_file() else dict(d)
            rec.setdefault("measures", None)
            records.append(rec)
    have = {r["celex"] for r in records}
    for e in bs.DIRECTIVES:
        if e.celex in have or (index is not None and e.celex not in celexes_needed):
            continue
        records.append({"celex": e.celex, "short": e.short, "topic": e.topic, "title": None, "date": None,
                        "transposition_deadlines": [],
                        "eurlex_url": f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{e.celex}",
                        "eurlex_nim_url": f"https://eur-lex.europa.eu/legal-content/EN/NIM/?uri=CELEX:{e.celex}",
                        "total_measures": None, "countries_with_measures": None, "counts": None,
                        "measures": None, "error": "national measures not built (no site/data/index.json)"})
    return index, records


# --- shaping --------------------------------------------------------------------------------


def _strip_alignment(page: dict, paths: dict) -> dict:
    """The alignment page without texts (they live in the provision files), with API paths added."""
    articles = []
    for a in page["articles"]:
        rows = [{k: v for k, v in r.items() if k != "text"} for r in a["rows"]]
        articles.append({**{k: v for k, v in a.items() if k != "rows"}, "rows": rows})
    national = []
    for n in page.get("national") or []:
        cc = page["country_code"]
        slug = act_slug(n["act_id"])
        national.append({**n, "act_slug": slug, "act": f"acts/{cc}/{slug}.json",
                         "provisions": f"acts/{cc}/{slug}/provisions.json"})
    closest = {nid: {"closest_eu": t.get("closest_eu"), "closest_score": t.get("closest_score")}
               for nid, t in (page.get("national_texts") or {}).items() if "closest_score" in t}
    keep = ("id", "celex", "short", "country", "country_code", "country_name", "national_language", "portal",
            "method", "threshold", "gap_threshold", "gap_note", "extra_note", "has_official", "official_source", "summary",
            "gap_articles")
    data = {k: page.get(k) for k in keep}
    if not data["national_language"]:  # alignment pages built before 0.4 have no language field
        from openacquis.adapters import get_adapter

        data["national_language"] = get_adapter(page["country_code"]).language
    eu = dict(page.get("eu") or {})
    eu["provisions"] = paths["eu_provisions"]
    data.update(
        directive=page["celex"],
        computed_at=page.get("built_at"),
        generator=page.get("generator"),
        eu=eu,
        national=national,
        articles=articles,
        extra_rule_sections=[{**s, "closest": {u: closest[u] for u in s["units"] if u in closest}}
                             for s in page.get("extra_rule_sections") or []],
        links=paths,
        notice=NOTICE,
    )
    return data


def _candidates(review: dict | None, decisions: dict[str, Any]) -> list[dict]:
    if review is None:
        return []
    out = []
    for s in review["suggestions"]:
        d = decisions.get(s["id"])
        out.append({**s, "status": "candidate", "method": review.get("method"),
                    "review_status": d.status if d else None,
                    "reviewers": list(d.reviewers) if d else []})
    return out


def _eu_provisions(pages: list[dict]) -> dict[str, dict]:
    """EU provisions from the alignment rows and the review texts, in document order."""
    out: dict[str, dict] = {}

    def put(pid: str, **kw) -> None:
        rec = out.setdefault(pid, {"id": pid, "provision": _local(pid), "label": None, "heading": None,
                                   "url": None, "text": None, "text_truncated": False})
        for k, v in kw.items():
            if v in (None, ""):
                continue
            if k == "text" and rec["text"] and (len(rec["text"]) >= len(v)):
                continue  # keep the longer (less clipped) text
            rec[k] = v
        rec["text_truncated"] = _truncated(rec["text"])

    for a in (p["alignment"] for p in pages):
        for art in a["articles"]:
            put(art["id"], label=art.get("label"), heading=art.get("heading"), url=art.get("url"))
            for r in art["rows"]:
                put(r["id"], label=r.get("label"), url=r.get("url"), text=r.get("text"))
    for p in pages:
        for pid, t in ((p["review"] or {}).get("eu_texts") or {}).items():
            put(pid, label=t.get("label"), url=t.get("url"), text=t.get("text"))
    return out


def _national_provisions(pages: list[dict]) -> dict[tuple[str, str], dict[str, dict]]:
    """``{(cc, act_id): {national_id: provision}}`` from the alignment and review texts."""
    out: dict[tuple[str, str], dict[str, dict]] = {}

    def put(nid: str, t: dict) -> None:
        cc, act = nid.split(":", 1)[0], _act_of(nid)
        rec = out.setdefault((cc, act), {}).setdefault(nid, {
            "id": nid, "act_id": act, "provision": nid.split(":", 2)[-1], "label": None, "section": None,
            "url": None, "text": None, "text_truncated": False})
        for k in ("label", "section", "url", "text"):
            v = t.get(k)
            if v in (None, ""):
                continue
            if k == "text" and rec["text"] and len(rec["text"]) >= len(v):
                continue
            rec[k] = v
        rec["text_truncated"] = _truncated(rec["text"])

    for p in pages:
        for nid, t in (p["alignment"].get("national_texts") or {}).items():
            put(nid, t)
        for nid, t in ((p["review"] or {}).get("national_texts") or {}).items():
            put(nid, t)
    for acts in out.values():
        for rec in acts.values():
            if not rec["text"]:
                rec["text"] = None
    return out


def _measure_row(m: dict) -> dict:
    return {k: m.get(k) for k in ("country", "reference", "title", "act_type", "journal", "journal_date",
                                  "notified", "uri")}


# --- build ----------------------------------------------------------------------------------


def build_api(
    out: Path = SITE / "api" / "v1",
    *,
    site_data: Path = SITE / "data",
    static_dir: Path = SITE / "static-data",
    reviews_dir: Path | None = None,
    root: Path = ROOT,
    api_src: Path = API_SRC,
    now: datetime | None = None,
    log: Callable[[str], None] = lambda msg: None,
) -> ApiSummary:
    """Write the API tree to ``out`` (replacing it) and return what was written."""
    from openacquis.dataset import build_dataset
    from openacquis.review import REVIEWS_DIR, load_reviews, merge

    bs = _load_build_site()
    reviews_dir = Path(reviews_dir or REVIEWS_DIR)
    now = now or datetime.now(timezone.utc)
    built_at = _iso(now)
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)  # drop pairs and acts that are no longer built
    out.mkdir(parents=True)
    summary = ApiSummary(out=out)
    w = _Writer(out, built_at, summary)

    # schemas and OpenAPI, as committed
    shutil.copytree(api_src / "schemas", out / "schemas")
    shutil.copy2(api_src / "openapi.yaml", out / "openapi.yaml")
    # a listing of the schema files, so the index's "schemas" link names a file (static hosts list no folders)
    names = sorted(p.name for p in (out / "schemas").glob("*.schema.json"))
    (out / "schemas" / "index.json").write_text(json.dumps(
        {"base_url": SCHEMA_BASE, "schemas": [{"name": n.removesuffix(".schema.json"), "href": n} for n in names]},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for p in [*(out / "schemas").glob("*.json"), out / "openapi.yaml"]:
        summary.files += 1
        summary.bytes += p.stat().st_size

    # dataset: the same builder as `openacquis dataset build` and the website
    ds = build_dataset(out / "dataset", reviews_dir=reviews_dir, root=root, now=now)
    for f in ds.files:
        summary.files += 1
        summary.bytes += (out / "dataset" / f).stat().st_size
    links = [json.loads(line) for line in (out / "dataset" / "links.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()]
    records = load_reviews(reviews_dir) if reviews_dir.exists() else []
    decisions = {d.suggestion_id: d for d in merge(records)}
    summary.links, summary.reviews = len(links), len(records)
    log(f"  dataset: {ds.rows} links ({ds.official} official, {ds.reviewed} reviewed), {len(records)} reviews")

    pages = _pair_pages(site_data, static_dir)
    pair_keys = {(s.split("_")[0], s.split("_")[1]) for s in pages}
    pair_keys |= {(r["directive"], r["country"]) for r in links}
    pair_keys |= {(r["directive"], r["country"]) for r in records}
    site_index, directive_recs = _directive_records(site_data, {c for c, _ in pair_keys})
    site_mode = (site_index or {}).get("mode", "static")
    rec_by_celex = {r["celex"]: r for r in directive_recs}
    for celex in sorted({c for c, _ in pair_keys} - set(rec_by_celex)):
        rec_by_celex[celex] = {"celex": celex, "short": celex, "topic": None, "title": None, "date": None,
                               "transposition_deadlines": [], "eurlex_url": None, "eurlex_nim_url": None,
                               "total_measures": None, "countries_with_measures": None, "counts": None,
                               "measures": None, "error": "not in the curated list"}
        directive_recs.append(rec_by_celex[celex])

    # EU provisions per directive x language, national provisions per act
    eu_sets: dict[tuple[str, str], list[dict]] = {}
    for slug, p in pages.items():
        a = p["alignment"]
        eu_sets.setdefault((a["celex"], (a.get("eu") or {}).get("language") or "xx"), []).append(p)
    eu_paths = {}
    for (celex, lang), ps in sorted(eu_sets.items()):
        provs = list(_eu_provisions(ps).values())
        mode = "live" if all(p["alignment"].get("mode") == "live" for p in ps) else "static"
        rel = w.write(f"directives/{celex}/eu/{lang}/provisions.json", "provisions",
                      {"directive": celex, "language": lang, "source": "EUR-Lex / CELLAR",
                       "title": (ps[0]["alignment"].get("eu") or {}).get("title"),
                       "count": len(provs), "provisions": provs}, mode)
        eu_paths[(celex, lang)] = rel
        summary.eu_provisions += len(provs)

    nat = _national_provisions(list(pages.values()))
    act_meta: dict[tuple[str, str], dict] = {}
    for p in pages.values():
        a = p["alignment"]
        for i, n in enumerate(a.get("national") or []):
            key = (a["country_code"], n["act_id"])
            meta = act_meta.setdefault(key, {**n, "used_by": [], "mode": a.get("mode", "static")})
            meta["used_by"].append({"directive": a["celex"], "role": "main" if i == 0 else "measure"})
    act_index = []
    for (cc, act_id), provs in sorted(nat.items()):
        meta = act_meta.get((cc, act_id), {"act_id": act_id, "title": None, "url": None, "date": None,
                                           "label": act_id, "used_by": [], "mode": "static"})
        slug = act_slug(act_id)
        rel_p = w.write(f"acts/{cc}/{slug}/provisions.json", "act-provisions",
                        {"country": cc, "act_id": act_id, "act_slug": slug, "version_date": meta.get("date"),
                         "count": len(provs), "provisions": list(provs.values())}, meta["mode"])
        act = {"country": cc, "act_id": act_id, "act_slug": slug, "label": meta.get("label") or act_id,
               "title": meta.get("title"), "url": meta.get("url"), "version_date": meta.get("date"),
               "used_by": meta["used_by"], "provisions_published": len(provs), "provisions": rel_p,
               "note": "Only provisions that appear in an alignment or review queue are published; texts "
                       "may be clipped (text_truncated). The full text is at url."}
        rel_a = w.write(f"acts/{cc}/{slug}.json", "act", act, meta["mode"])
        act_index.append({"country": cc, "act_id": act_id, "act_slug": slug, "label": act["label"],
                          "href": rel_a, "provisions": rel_p})
        summary.acts += 1
        summary.national_provisions += len(provs)

    # per directive x country
    pair_index = []
    pairs_by_celex: dict[str, list[dict]] = {}
    for celex, cc in sorted(pair_keys):
        slug = f"{celex}_{cc}"
        p = pages.get(slug)
        base = f"directives/{celex}/{cc}"
        mode = p["alignment"].get("mode", "static") if p else site_mode
        paths = {"links": f"{base}/links.json", "candidates": f"{base}/candidates.json",
                 "reviews": f"{base}/reviews.json"}
        if p:
            paths = {"alignment": f"{base}/alignment.json", **paths,
                     "eu_provisions": eu_paths[(celex, (p["alignment"].get("eu") or {}).get("language") or "xx")]}
        pair_links = [r for r in links if r["directive"] == celex and r["country"] == cc]
        pair_reviews = [r for r in records if r["directive"] == celex and r["country"] == cc]
        cands = _candidates(p["review"] if p else None, decisions)
        if p:
            w.write(paths["alignment"], "alignment", _strip_alignment(p["alignment"], paths), mode)
        w.write(paths["links"], "links", {"directive": celex, "country": cc, "count": len(pair_links),
                                          "links": pair_links}, mode)
        w.write(paths["candidates"], "candidates",
                {"directive": celex, "country": cc, "method": (p["review"] or {}).get("method") if p else None,
                 "threshold": (p["review"] or {}).get("threshold") if p else None,
                 "count": len(cands), "notice": NOTICE, "candidates": cands}, mode)
        w.write(paths["reviews"], "reviews", {"directive": celex, "country": cc, "count": len(pair_reviews),
                                              "reviews": pair_reviews}, mode)
        summary.candidates += len(cands)
        summary.pairs.append(slug)
        summary.modes[slug] = mode
        entry = {"directive": celex, "country": cc, "mode": mode,
                 "has_official": bool(p and p["alignment"].get("has_official")),
                 "links_count": len(pair_links), "candidates_count": len(cands), "reviews_count": len(pair_reviews),
                 **paths}
        pair_index.append(entry)
        pairs_by_celex.setdefault(celex, []).append(entry)

    # per directive
    directives = []
    for rec in directive_recs:
        celex = rec["celex"]
        base = f"directives/{celex}"
        measures = rec.get("measures")
        dpaths: dict[str, str | None] = {"directive": f"{base}.json", "measures": None, "matrix": None}
        if measures is not None:
            dpaths["measures"] = w.write(f"{base}/measures.json", "measures",
                                         {"directive": celex, "count": len(measures),
                                          "measures": [_measure_row(m) for m in measures]},
                                         rec.get("mode") or site_mode)
        mpages = [pages[f"{celex}_{e['country']}"]["alignment"] for e in pairs_by_celex.get(celex, [])
                  if f"{celex}_{e['country']}" in pages]
        if mpages:
            mpath = site_data / "matrix" / f"{celex}.json"
            matrix = _read(mpath) if mpath.is_file() else None
            if matrix is None or {c["code"] for c in matrix["countries"]} != {a["country_code"] for a in mpages}:
                hpath = static_dir / f"{celex}.headings.json"
                matrix = bs.matrix_data(celex, mpages, headings=_read(hpath) if hpath.is_file() else None, now=now)
            mmode = "live" if all(a.get("mode") == "live" for a in mpages) else "static"
            dpaths["matrix"] = w.write(f"{base}/matrix.json", "matrix",
                                       {"directive": celex, "short": matrix.get("short"),
                                        "computed_at": matrix.get("built_at"), "countries": matrix["countries"],
                                        "articles": matrix["articles"], "notice": NOTICE}, mmode)
        eu_langs = sorted({lang for (c, lang) in eu_paths if c == celex})
        summary_rec = {
            "celex": celex, "short": rec.get("short"), "topic": rec.get("topic"), "title": rec.get("title"),
            "date": rec.get("date"), "transposition_deadlines": rec.get("transposition_deadlines") or [],
            "eurlex_url": rec.get("eurlex_url"), "eurlex_nim_url": rec.get("eurlex_nim_url"),
            "total_measures": rec.get("total_measures"), "countries_with_measures": rec.get("countries_with_measures"),
            "error": rec.get("error"),
            "countries": sorted(e["country"] for e in pairs_by_celex.get(celex, [])),
            "href": dpaths["directive"],
        }
        detail = {**summary_rec, "counts": rec.get("counts"),
                  "eu_languages": {lang: eu_paths[(celex, lang)] for lang in eu_langs},
                  "pairs": pairs_by_celex.get(celex, []), "links": dpaths}
        w.write(dpaths["directive"], "directive", detail, rec.get("mode") or site_mode)
        directives.append(summary_rec)
    summary.directives = len(directives)
    w.write("directives.json", "directives", {"count": len(directives), "directives": directives}, site_mode,
            indent=1)

    modes = set(summary.modes.values())
    index = {
        "api_version": API_VERSION,
        "dataset_version": __version__,
        "base_url": BASE_URL,
        "deprecated": False,
        "notice": NOTICE,
        "sources_mode": {"directives": site_mode,
                         "alignments": modes.pop() if len(modes) == 1 else ("mixed" if modes else None)},
        "counts": {"directives": summary.directives, "alignment_pairs": sum(1 for s in summary.pairs if s in pages),
                   "pairs": len(summary.pairs), "links": summary.links, "reviews": summary.reviews,
                   "candidates": summary.candidates, "acts": summary.acts, "eu_provisions": summary.eu_provisions,
                   "national_provisions": summary.national_provisions},
        "links": {"directives": "directives.json", "openapi": "openapi.yaml", "schemas": "schemas/index.json",
                  "datapackage": "dataset/datapackage.json", "links_csv": "dataset/links.csv",
                  "links_jsonl": "dataset/links.jsonl", "reviews_jsonl": "dataset/reviews.jsonl",
                  "releases": RELEASES_URL, "doi": f"https://doi.org/{CONCEPT_DOI}" if CONCEPT_DOI else None},
        "pairs": pair_index,
        "acts": act_index,
    }
    w.write("index.json", "index", index, site_mode, indent=1)
    log(f"  api: {summary.files} files, {summary.bytes / 1e6:.1f} MB, {summary.directives} directives, "
        f"{len(summary.pairs)} pairs, {summary.acts} acts -> {out}")
    return summary


# --- validation (stdlib JSON Schema subset) ---------------------------------------------------

_TYPES: dict[str, Callable[[Any], bool]] = {
    "object": lambda x: isinstance(x, dict),
    "array": lambda x: isinstance(x, list),
    "string": lambda x: isinstance(x, str),
    "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
    "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
    "boolean": lambda x: isinstance(x, bool),
    "null": lambda x: x is None,
}

#: Keywords :func:`validate` understands; a schema using any other keyword is rejected by
#: :func:`unsupported_keywords` so that a check never passes by silently ignoring a rule.
SUPPORTED = {"$schema", "$id", "$ref", "$defs", "$comment", "title", "description", "examples", "default",
             "type", "enum", "const", "pattern", "minLength", "maxLength", "minimum", "maximum",
             "properties", "required", "additionalProperties", "patternProperties", "propertyNames",
             "items", "minItems", "maxItems", "uniqueItems", "allOf", "anyOf", "oneOf", "not", "format"}


class SchemaRegistry:
    """Schemas by ``$id``, resolving ``$ref`` (relative URLs and ``#/...`` pointers)."""

    def __init__(self, schemas: dict[str, dict]):
        self.by_id = schemas

    @classmethod
    def from_dir(cls, path: Path) -> "SchemaRegistry":
        out = {}
        for p in sorted(Path(path).glob("*.schema.json")):
            s = _read(p)
            out[s.get("$id") or (SCHEMA_BASE + p.name)] = s
        return cls(out)

    def resolve(self, ref: str, base: str) -> tuple[dict, str]:
        url, frag = urldefrag(urljoin(base, ref))
        doc = self.by_id.get(url)
        if doc is None:
            raise KeyError(f"unknown schema {url}")
        node: Any = doc
        for part in [p for p in frag.split("/") if p]:
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node, url


def unsupported_keywords(schema: Any, path: str = "#") -> list[str]:
    """Keywords in ``schema`` that :func:`validate` would ignore."""
    out = []
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k not in SUPPORTED:
                out.append(f"{path}/{k}")
            if k in ("properties", "patternProperties", "$defs"):
                for name, sub in v.items():
                    out += unsupported_keywords(sub, f"{path}/{k}/{name}")
            elif k in ("items", "additionalProperties", "not", "propertyNames") and isinstance(v, dict):
                out += unsupported_keywords(v, f"{path}/{k}")
            elif k in ("allOf", "anyOf", "oneOf"):
                for i, sub in enumerate(v):
                    out += unsupported_keywords(sub, f"{path}/{k}/{i}")
    return out


def validate(instance: Any, schema: dict, registry: SchemaRegistry, base: str = "", path: str = "$",
             limit: int = 20) -> list[str]:
    """Errors of ``instance`` against ``schema`` (a subset of JSON Schema 2020-12; see :data:`SUPPORTED`)."""
    errors: list[str] = []

    def err(msg: str) -> None:
        if len(errors) < limit:
            errors.append(f"{path}: {msg}")

    if schema is True or schema == {}:
        return errors
    if schema is False:
        return [f"{path}: not allowed"]
    base = schema.get("$id", base)
    if "$ref" in schema:
        target, tbase = registry.resolve(schema["$ref"], base)
        errors += validate(instance, target, registry, tbase, path, limit)
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](instance) for x in types):
            err(f"expected {'/'.join(types)}, got {type(instance).__name__}")
            return errors
    if "enum" in schema and instance not in schema["enum"]:
        err(f"{instance!r:.60} not in {schema['enum']}")
    if "const" in schema and instance != schema["const"]:
        err(f"must be {schema['const']!r}")
    if isinstance(instance, str):
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            err(f"{instance!r:.80} does not match {schema['pattern']}")
        if len(instance) < schema.get("minLength", 0):
            err("too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            err("too long")
    if _TYPES["number"](instance):
        if "minimum" in schema and instance < schema["minimum"]:
            err(f"{instance} < {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            err(f"{instance} > {schema['maximum']}")
    if isinstance(instance, dict):
        for k in schema.get("required", []):
            if k not in instance:
                err(f"missing {k!r}")
        props = schema.get("properties", {})
        pprops = schema.get("patternProperties", {})
        for k, v in instance.items():
            matched = False
            if k in props:
                matched = True
                errors += validate(v, props[k], registry, base, f"{path}.{k}", limit - len(errors))
            for pat, sub in pprops.items():
                if re.search(pat, k):
                    matched = True
                    errors += validate(v, sub, registry, base, f"{path}.{k}", limit - len(errors))
            if "propertyNames" in schema:
                errors += validate(k, schema["propertyNames"], registry, base, f"{path}.<{k}>", limit - len(errors))
            if not matched and "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                if ap is False:
                    err(f"unexpected property {k!r}")
                elif isinstance(ap, dict):
                    errors += validate(v, ap, registry, base, f"{path}.{k}", limit - len(errors))
            if len(errors) >= limit:
                break
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            err("too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            err("too many items")
        if schema.get("uniqueItems"):
            seen = [json.dumps(x, sort_keys=True) for x in instance]
            if len(seen) != len(set(seen)):
                err("items are not unique")
        if isinstance(schema.get("items"), dict):
            for i, x in enumerate(instance):
                errors += validate(x, schema["items"], registry, base, f"{path}[{i}]", limit - len(errors))
                if len(errors) >= limit:
                    break
    for sub in schema.get("allOf", []):
        errors += validate(instance, sub, registry, base, path, limit - len(errors))
    if "anyOf" in schema and not any(not validate(instance, s, registry, base, path, 1) for s in schema["anyOf"]):
        err("matches none of anyOf")
    if "oneOf" in schema:
        n = sum(1 for s in schema["oneOf"] if not validate(instance, s, registry, base, path, 1))
        if n != 1:
            err(f"matches {n} of oneOf (expected 1)")
    if "not" in schema and not validate(instance, schema["not"], registry, base, path, 1):
        err("matches a 'not' schema")
    return errors[:limit]


def check_api(out: Path = SITE / "api" / "v1", schemas_dir: Path | None = None) -> list[str]:
    """Validate every JSON file under ``out`` against the schema its ``$schema`` names, and every
    line of the bulk JSON-lines files against ``link`` / ``review``. Returns the errors (empty = valid)."""
    out = Path(out)
    reg = SchemaRegistry.from_dir(schemas_dir or out / "schemas")
    errors: list[str] = []
    for sid, s in reg.by_id.items():
        errors += [f"{sid}: unsupported keyword {k}" for k in unsupported_keywords(s)]
    if not (out / "index.json").is_file():
        return errors + [f"{out}/index.json: missing"]
    n = 0
    for p in sorted(out.rglob("*.json")):
        rel = p.relative_to(out).as_posix()
        if rel.startswith("schemas/") or rel == "dataset/datapackage.json":
            continue
        doc = _read(p)
        sid = doc.get("$schema") if isinstance(doc, dict) else None
        if sid not in reg.by_id:
            errors.append(f"{rel}: no known $schema ({sid!r})")
            continue
        errors += [f"{rel}: {e}" for e in validate(doc, reg.by_id[sid], reg, sid)]
        n += 1
    for name, schema in (("links.jsonl", "link"), ("reviews.jsonl", "review")):
        p = out / "dataset" / name
        if not p.is_file():
            errors.append(f"dataset/{name}: missing")
            continue
        sid = SCHEMA_BASE + f"{schema}.schema.json"
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                errors += [f"dataset/{name}:{i}: {e}" for e in validate(json.loads(line), reg.by_id[sid], reg, sid)]
    # every relative path the index and the pair entries point to exists
    index = _read(out / "index.json")["data"]
    refs = [v for v in index["links"].values() if v and not v.startswith("http")]
    refs += [v for e in index["pairs"] for k, v in e.items() if isinstance(v, str) and v.endswith(".json")]
    refs += [v for a in index["acts"] for v in (a["href"], a["provisions"])]
    errors += [f"index.json: {r} does not exist" for r in refs if not (out / r).exists()]
    if not n:
        errors.append(f"{out}: no API files")
    return errors


# --- CLI ------------------------------------------------------------------------------------


def not_live(modes: dict[str, str], expect: str = "seed") -> list[str]:
    """Pairs meant to be computed live that are not: those of ``build_site.SEED`` (``expect="seed"``) or every
    pair (``"all"``). Other pairs are served from site/static-data by design and never count as fallbacks."""
    seed = None if expect == "all" else set(_load_build_site().SEED)
    return sorted(k for k, m in modes.items() if m != "live" and (seed is None or k.split("_", 1)[0] in seed))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--site", type=Path, default=SITE, help="site directory (default: site/)")
    p.add_argument("--data", type=Path, help="website data built by build_site.py (default: <site>/data)")
    p.add_argument("--static", type=Path, help="committed alignment copies (default: <site>/static-data)")
    p.add_argument("--reviews", type=Path, help="review records (default: data/reviews)")
    p.add_argument("--out", type=Path, help="output directory (default: <site>/api/v1)")
    p.add_argument("--check", action="store_true", help="validate every file against its schema after building")
    p.add_argument("--check-only", action="store_true", help="validate an existing build; write nothing")
    p.add_argument("--require-live", nargs="?", const="seed", choices=["seed", "all"],
                   help="fail if an alignment pair that build_site.py computes live fell back to the static copy: "
                        "'seed' (default; the pairs of build_site.SEED, as with its default --live-alignments seed) "
                        "or 'all' (every pair, after build_site.py --live-alignments all)")
    args = p.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    out = args.out or args.site / "api" / "v1"
    if not args.check_only:
        log(f"Building API -> {out}")
        s = build_api(out, site_data=args.data or args.site / "data", static_dir=args.static or args.site / "static-data",
                      reviews_dir=args.reviews, log=log)
        if args.require_live:
            fell_back = not_live(s.modes, args.require_live)
            if fell_back:
                log(f"not live (fell back to the static copy): {fell_back}")
                return 1
    if args.check or args.check_only:
        errors = check_api(out)
        for e in errors[:50]:
            log("  " + e)
        if errors:
            log(f"API check: {len(errors)} errors")
            return 1
        log("API check: every file matches its schema")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
