"""The static public API (scripts/build_api.py, api/): build from committed data, validate against the schemas."""

import copy
import importlib.util
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_api", ROOT / "scripts" / "build_api.py")
build_api = importlib.util.module_from_spec(_spec)
sys.modules["build_api"] = build_api
_spec.loader.exec_module(build_api)

from openacquis.dataset import OFFICIAL_TABLES  # noqa: E402

NOW = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)
SCHEMAS = ROOT / "api" / "schemas"
B = build_api.SCHEMA_BASE
def _official_links() -> int:
    from openacquis.dataset import official_rows

    return len({r["link_id"] for r in official_rows()})


OFFICIAL = _official_links()


def _review_record(tmp_path):
    """One confirm record for the first CZ link suggestion of the committed static copy."""
    from openacquis.review import make_record

    review = json.loads((ROOT / "site" / "static-data" / "32022L2555_cz.review.json").read_text(encoding="utf-8"))
    s = next(x for x in review["suggestions"] if x["kind"] == "link")
    rec = make_record(directive="32022L2555", country="cz", kind="link", eu_id=s["eu_id"], national_id=s["national_id"],
                      verdict="confirm", reviewer="octocat", machine={"label": "candidate", "score": s["score"]},
                      day="2026-10-12")
    d = tmp_path / "reviews"
    d.mkdir()
    (d / "32022L2555_cz.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return d, s


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """API built from site/static-data only (no site/data: no CELLAR build) plus one review record."""
    tmp = tmp_path_factory.mktemp("api")
    reviews, sugg = _review_record(tmp)
    static = tmp / "static-data"  # only the NIS2 copies, so other directives added later do not change the counts
    static.mkdir()
    for f in (ROOT / "site" / "static-data").glob("32022L2555*.json"):
        shutil.copy2(f, static / f.name)
    out = tmp / "site" / "api" / "v1"
    s = build_api.build_api(out, site_data=tmp / "site" / "data", static_dir=static,
                            reviews_dir=reviews, now=NOW)
    return out, s, sugg


def _load(out, rel):
    return json.loads((out / rel).read_text(encoding="utf-8"))


def test_build_validates_against_schemas(built):
    out, s, _ = built
    assert build_api.check_api(out) == []
    nis2 = ["32022L2555_at", "32022L2555_cz", "32022L2555_de", "32022L2555_sk"]
    assert sorted(p for p in s.pairs if p.startswith("32022L2555")) == nis2
    assert set(s.pairs) - set(nis2) <= {f"{c}_{cc}" for c, cc in OFFICIAL_TABLES}  # pairs with official links only
    assert set(s.modes.values()) == {"static"}
    assert s.links == OFFICIAL + 1 and s.reviews == 1  # official-table links + 1 reviewed
    idx = _load(out, "index.json")
    assert idx["$schema"] == B + "index.schema.json"
    assert idx["api_version"] == "1" and idx["license"] == "CC-BY-4.0" and idx["built_at"] == "2026-10-12T08:30:00Z"
    assert idx["data"]["counts"]["alignment_pairs"] == 4
    assert (out / "openapi.yaml").is_file() and (out / "schemas" / "link.schema.json").is_file()
    # the index links a file, not a folder (GitHub Pages lists no folders)
    assert idx["data"]["links"]["schemas"] == "schemas/index.json"
    listing = json.loads((out / "schemas" / "index.json").read_text(encoding="utf-8"))
    assert {"name": "link", "href": "link.schema.json"} in listing["schemas"]
    assert all((out / "schemas" / x["href"]).is_file() for x in listing["schemas"])


def test_alignment_has_no_texts_and_points_to_provision_files(built):
    out, _, _ = built
    a = _load(out, "directives/32022L2555/cz/alignment.json")["data"]
    assert "national_texts" not in a
    assert all("text" not in r for art in a["articles"] for r in art["rows"])
    assert a["national"][0]["act_slug"] == "264-2025"
    assert a["eu"]["provisions"] == "directives/32022L2555/eu/cs/provisions.json"
    assert "not findings" in a["notice"]
    acts = _load(out, a["national"][0]["provisions"])["data"]
    assert acts["act_id"] == "264/2025" and acts["count"] > 300
    ids = {p["id"] for p in acts["provisions"]}
    used = {m["id"] for art in a["articles"] for r in art["rows"] for m in r["machine"]}
    assert used <= ids  # every machine candidate has its text in the act file
    eu = _load(out, a["eu"]["provisions"])["data"]
    assert {r["id"] for art in a["articles"] for r in art["rows"]} <= {p["id"] for p in eu["provisions"]}
    assert any(p["text_truncated"] for p in eu["provisions"])  # clipped texts are flagged


def test_links_candidates_and_reviews(built):
    out, _, sugg = built
    links = _load(out, "directives/32022L2555/cz/links.json")["data"]
    assert links["count"] == 569
    assert {r["source"] for r in links["links"]} == {"official_correlation_table", "human_review"}
    assert _load(out, "directives/32022L2555/sk/links.json")["data"]["count"] == 469  # Slovak table, distinct pairs
    c = _load(out, "directives/32022L2555/cz/candidates.json")["data"]
    assert {x["status"] for x in c["candidates"]} == {"candidate"}
    hit = next(x for x in c["candidates"] if x["id"] == sugg["id"])
    assert hit["review_status"] == "confirmed" and hit["reviewers"] == ["octocat"]
    assert _load(out, "directives/32022L2555/cz/reviews.json")["data"]["count"] == 1
    assert sum(1 for line in (out / "dataset" / "reviews.jsonl").read_text().splitlines() if line) == 1


def test_curated_directives_without_cellar_build(built):
    out, _, _ = built
    d = _load(out, "directives.json")["data"]
    assert d["count"] == len(build_api._load_build_site().DIRECTIVES)  # the curated list, with an error saying measures were not built
    nis2 = next(x for x in d["directives"] if x["celex"] == "32022L2555")
    assert nis2["countries"] == ["at", "cz", "de", "sk"] and nis2["total_measures"] is None
    detail = _load(out, nis2["href"])["data"]
    assert detail["links"]["measures"] is None and detail["links"]["matrix"] == "directives/32022L2555/matrix.json"
    m = _load(out, "directives/32022L2555/matrix.json")["data"]
    assert [c["code"] for c in m["countries"]] == ["at", "cz", "de", "sk"]


def test_measures_from_offline_site_build(tmp_path):
    bs = sys.modules.get("build_site") or build_api._load_build_site()
    assert bs.main(["--offline", "--out", str(tmp_path / "data"), "--no-alignment", "--no-dataset"]) == 0
    out = tmp_path / "api"
    build_api.build_api(out, site_data=tmp_path / "data", static_dir=tmp_path / "none",
                        reviews_dir=tmp_path / "no-reviews", now=NOW)
    assert build_api.check_api(out) == []
    idx = _load(out, "index.json")
    assert idx["mode"] == "offline-sample" and idx["data"]["counts"]["alignment_pairs"] == 0
    assert idx["data"]["counts"]["pairs"] == len(OFFICIAL_TABLES)  # official links, no alignment page
    m = _load(out, "directives/32022L2555/measures.json")["data"]
    assert m["count"] > 0 and all(re.fullmatch(r"[A-Z]{3}", x["country"]) for x in m["measures"])
    assert "alignment" not in _load(out, "directives/32022L2555.json")["data"]["pairs"][0]


def test_validator_rejects_bad_records():
    reg = build_api.SchemaRegistry.from_dir(SCHEMAS)
    link_s = reg.by_id[B + "link.schema.json"]
    good = json.loads((ROOT / "data" / "eval" / "nis2_cz.jsonl").read_text(encoding="utf-8").splitlines()[0])
    from openacquis.dataset import official_rows

    row = official_rows(ROOT)[0]
    assert build_api.validate(row, link_s, reg, link_s["$id"]) == []
    assert build_api.validate({**row, "extra": 1}, link_s, reg, link_s["$id"])  # additionalProperties: false
    assert build_api.validate({**row, "country": "CZ"}, link_s, reg, link_s["$id"])
    assert build_api.validate({**row, "machine_score": True}, link_s, reg, link_s["$id"])  # bool is not a number
    assert build_api.validate({**row, "status": "compliant"}, link_s, reg, link_s["$id"])
    bad = dict(row)
    del bad["link_id"]
    assert any("link_id" in e for e in build_api.validate(bad, link_s, reg, link_s["$id"]))
    assert good["eu_id"] == row["eu_id"]


def test_validator_envelope_and_candidates(built):
    out, _, _ = built
    reg = build_api.SchemaRegistry.from_dir(out / "schemas")
    doc = _load(out, "directives/32022L2555/at/candidates.json")
    sid = doc["$schema"]
    assert build_api.validate(doc, reg.by_id[sid], reg, sid) == []
    broken = copy.deepcopy(doc)
    broken["data"]["candidates"][0]["status"] = "confirmed_breach"
    assert build_api.validate(broken, reg.by_id[sid], reg, sid)
    no_licence = {k: v for k, v in doc.items() if k != "license"}
    assert any("license" in e for e in build_api.validate(no_licence, reg.by_id[sid], reg, sid))


def test_check_only_cli_fails_on_a_broken_file(built, tmp_path):
    out, _, _ = built
    copy_dir = tmp_path / "v1"
    shutil.copytree(out, copy_dir)
    assert build_api.main(["--check-only", "--out", str(copy_dir)]) == 0
    p = copy_dir / "directives" / "32022L2555" / "cz" / "links.json"
    d = json.loads(p.read_text())
    d["data"]["links"][0]["link_id"] = "not-a-hash"
    p.write_text(json.dumps(d))
    assert build_api.main(["--check-only", "--out", str(copy_dir)]) == 1


def test_schemas_follow_the_python_field_lists():
    from openacquis.dataset import FIELDS as LINK_FIELDS
    from openacquis.review import FIELDS as REVIEW_FIELDS, MACHINE_FIELDS

    link = json.loads((SCHEMAS / "link.schema.json").read_text())
    review = json.loads((SCHEMAS / "review.schema.json").read_text())
    assert list(link["properties"]) == [f for f, _, _ in LINK_FIELDS] == link["required"]
    assert list(review["properties"]) == list(REVIEW_FIELDS)
    assert tuple(review["properties"]["machine"]["properties"]) == MACHINE_FIELDS
    for f, t, _ in LINK_FIELDS:
        types = link["properties"][f].get("type")
        if types is not None:
            assert t in (types if isinstance(types, list) else [types])


def test_schemas_are_well_formed_and_supported():
    reg = build_api.SchemaRegistry.from_dir(SCHEMAS)
    assert len(reg.by_id) == len(list(SCHEMAS.glob("*.schema.json")))
    for sid, s in reg.by_id.items():
        assert sid == B + Path(sid).name and s["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert build_api.unsupported_keywords(s) == [], sid
        for ref in re.findall(r'"\$ref": "([^"]+)"', json.dumps(s)):
            build_api.SchemaRegistry.resolve(reg, ref, sid)  # raises if dangling
    assert build_api.unsupported_keywords({"dependentRequired": {}}) == ["#/dependentRequired"]


def _template(path: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(path.lstrip("/"))) + "$")


def test_openapi_covers_every_published_file(built):
    out, _, _ = built
    text = (ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8")
    assert text.startswith("openapi: 3.1.0")
    paths = re.findall(r"^  (/[^:\s]+):\s*$", text, re.M)
    assert "/directives/{celex}/{cc}/alignment.json" in paths
    templates = [_template(p) for p in paths]
    for f in out.rglob("*"):
        rel = f.relative_to(out).as_posix()
        if f.is_dir() or rel.startswith("schemas/") or rel in ("openapi.yaml", "dataset/README.md"):
            continue
        assert any(t.match(rel) for t in templates), rel
    for ref in set(re.findall(r'\$ref: "(schemas/[^"]+)"', text)):
        assert (ROOT / "api" / ref).is_file(), ref


def test_openapi_parses_as_yaml():
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    assert spec["openapi"] == "3.1.0" and spec["servers"][0]["url"].endswith("/api/v1")
    for p, item in spec["paths"].items():
        for name in re.findall(r"\{([a-z_]+)\}", p):
            params = [x["$ref"].rsplit("/", 1)[-1] for x in item["get"].get("parameters", [])]
            assert name in params, (p, name)


def test_jsonschema_agrees(built):
    """Cross-check the stdlib validator with the reference implementation, when it is installed."""
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    out, _, _ = built
    reg = build_api.SchemaRegistry.from_dir(out / "schemas")
    registry = referencing.Registry().with_resources(
        [(sid, referencing.Resource.from_contents(s)) for sid, s in reg.by_id.items()])
    for p in sorted(out.rglob("*.json")):
        rel = p.relative_to(out).as_posix()
        if rel.startswith("schemas/") or rel == "dataset/datapackage.json":
            continue
        doc = json.loads(p.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(reg.by_id[doc["$schema"]], registry=registry).validate(doc)


def test_act_slug():
    assert build_api.act_slug("264/2025") == "264-2025"
    assert build_api.act_slug("69/2018") == "69-2018"
    assert build_api.act_slug("bsig_2025") == "bsig_2025"
    assert build_api.act_slug("20013065") == "20013065"
    assert build_api.act_slug("NISG 2026") == "nisg-2026"


def test_release_metadata_is_consistent():
    from openacquis import __version__

    zen = json.loads((ROOT / ".zenodo.json").read_text(encoding="utf-8"))
    assert zen["upload_type"] == "software" and zen["license"] == "mit" and zen["creators"]
    assert "version" not in zen  # Zenodo takes the version from the release tag
    cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(rf"^version: {re.escape(__version__)}$", cff, re.M), "bump CITATION.cff with the package"
    assert "cff-version: 1.2.0" in cff
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in wf and "scripts/build_api.py --check" in wf and "gh release create" in wf
    assert "CITATION.cff" in wf  # the release checks the citation version against the tag
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(__version__)}"$', pyproject, re.M)


def test_require_live_only_counts_pairs_meant_to_be_live():
    seed = sorted(build_api._load_build_site().SEED)[0]
    modes = {f"{seed}_cz": "live", f"{seed}_sk": "static", "32018L1972_cz": "static"}
    assert build_api.not_live(modes) == [f"{seed}_sk"]
    assert build_api.not_live(modes, "all") == sorted([f"{seed}_sk", "32018L1972_cz"])
    assert build_api.not_live({f"{seed}_cz": "live", "32018L1972_cz": "static"}) == []
