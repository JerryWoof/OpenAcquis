# Public API and releases

OpenAcquis publishes its data in two ways:

- **A static, read-only JSON API** on GitHub Pages under `https://jerrywoof.github.io/OpenAcquis/api/v1/`.
  It is rebuilt with the website: weekly, and on every push to `main`.
- **Immutable snapshots** attached to each tagged GitHub Release, and archived on Zenodo with a DOI.

Everything is plain files. There is no server, no key and no rate limit beyond GitHub's.

Machine suggestions in the API (`alignment`, `candidates`, `matrix`) are **candidates for human review,
not findings**. They say nothing about whether a member state transposed a directive correctly. Dataset links
(`links`) come only from official correlation tables or from human review ([dataset.md](dataset.md)).

## Quick start

```bash
B=https://jerrywoof.github.io/OpenAcquis/api/v1
curl -s $B/index.json | jq '.data.counts'
curl -s $B/directives/32022L2555/cz/links.json | jq '.data.links[0]'
curl -s $B/directives/32022L2555/sk/candidates.json | jq '.data.candidates[] | select(.kind=="gap")'
```

```js
// Browser: a plain GET, with no custom headers (see "HTTP behaviour")
const r = await fetch("https://jerrywoof.github.io/OpenAcquis/api/v1/directives/32022L2555/de/alignment.json");
if (!r.ok) throw new Error(r.status);   // a 404 has an HTML body
const { data, mode, built_at } = await r.json();
```

```python
import json, urllib.request
B = "https://jerrywoof.github.io/OpenAcquis/api/v1/"
idx = json.load(urllib.request.urlopen(B + "index.json"))["data"]
for p in idx["pairs"]:
    print(p["directive"], p["country"], p["mode"], p["links_count"], p["candidates_count"])
```

Start from `index.json`. It lists every directive × country pair and every act, with the relative path of
each file, so a client never has to build URLs itself.

## Files

All paths are relative to `api/v1/`.

| Path | Content | Schema |
|---|---|---|
| `index.json` | Versions, counts, every pair and act with its paths, links to the dataset, releases and DOI | `index` |
| `openapi.yaml` | OpenAPI 3.1 description of every path | |
| `schemas/<name>.schema.json` | JSON Schema 2020-12 for each file | |
| `schemas/index.json` | List of the schema files (name, file); the index's `links.schemas` points here | |
| `directives.json` | The curated directives: title, deadlines, totals, countries with an alignment | `directives` |
| `directives/<CELEX>.json` | One directive: notified measures per member state (alpha-3, zeros included), pairs, EU languages | `directive` |
| `directives/<CELEX>/measures.json` | National measures notified for the directive (CELLAR), all member states | `measures` |
| `directives/<CELEX>/matrix.json` | Article × country matrix: the most frequent top machine candidate §, the §§ the official table lists, linked units, gap candidates | `matrix` |
| `directives/<CELEX>/eu/<lang>/provisions.json` | The directive's provisions in one language: ID, label, heading, EUR-Lex URL, text | `provisions` |
| `directives/<CELEX>/<cc>/alignment.json` | Per EU paragraph: official correlation (where a table exists), top 3 machine candidates with scores, agreement, gap candidates; extra-rule candidate §§. **No texts** | `alignment` |
| `directives/<CELEX>/<cc>/links.json` | Dataset rows for this pair (official table and human review) | `links` → `link` |
| `directives/<CELEX>/<cc>/candidates.json` | Every machine suggestion: `link`, `gap` and `extra_rule` candidates, with `status: "candidate"` and the review status if someone reviewed it | `candidates` |
| `directives/<CELEX>/<cc>/reviews.json` | Human review records, including rejections | `reviews` → `review` |
| `acts/<cc>/<act_slug>.json` | National act: title, URL, version date, directives it is aligned with | `act` |
| `acts/<cc>/<act_slug>/provisions.json` | National provisions with text, stored once per act | `act-provisions` |
| `dataset/links.csv`, `links.jsonl`, `reviews.jsonl`, `datapackage.json`, `README.md` | The open dataset ([dataset.md](dataset.md)); one `link` / `review` object per JSON line | `link`, `review` |

The `alignment` and `candidates` files reference provisions by ID only. Their texts are in the provision
files, so each text is published once rather than repeated in every alignment and review file. Measured
on 43 directive × country pairs, the pair and text files total 28 MB, against 34 MB for the website's
alignment and review files they are built from.

### Identifiers

- `celex`: `^3\d{4}L\d{4}$`, e.g. `32022L2555`.
- `cc`: ISO 3166-1 alpha-2 in lower case (`cz`, `sk`, `at`, `de`). CELLAR's alpha-3 codes (`CZE`) appear
  only as values (`counts`, `measures[].country`).
- `lang`: the 2-letter EU language code of the text the alignment used (`cs`, `sk`, `de`).
- `act_slug`: the act ID made URL-safe. It is lower case, `/` and spaces become `-`, and only
  `[a-z0-9_-]` is kept: `264/2025` → `264-2025`, `bsig_2025` → `bsig_2025`. Every record also carries the
  raw `act_id`.
- Provision IDs are the OpenAcquis IDs ([provisions.md](provisions.md)): `eu:32022L2555:cs:art23.par4.pt_a`,
  `cz:264/2025:par16.odst1`. An `alignment` row and a `link` carry the full ID. A `link` also has the
  language-neutral `eu_provision` (`art23.par4.pt_a`).

### Envelope

Every JSON file except the bulk dataset files has the same envelope:

```json
{
  "$schema": "https://jerrywoof.github.io/OpenAcquis/api/v1/schemas/alignment.schema.json",
  "api_version": "1",
  "dataset_version": "0.4.0",
  "built_at": "2026-10-12T17:32:01Z",
  "generator": "openacquis 0.4.0",
  "mode": "static",
  "license": "CC-BY-4.0",
  "data": { }
}
```

`mode` says where the data came from:
- `live`: computed in this build.
- `static`: the committed fallback copy (`site/static-data/`), used when a portal could not be reached.
  `data.computed_at` in `alignment` and `matrix` says when that copy was computed.
- `offline-sample`: bundled samples.

`index.json` → `data.sources_mode` summarises it.

### Texts

Texts come from the website's alignment and review files. They are clipped there, to 2,500 characters per
EU paragraph and 1,500 characters per suggestion text. A clipped text ends in `…` and has
`text_truncated: true`. Only provisions that appear in an alignment or a review queue are published. Use the
`url` of each provision, or `openacquis text` / `openacquis law`, for the full official text.

## HTTP behaviour (GitHub Pages)

Measured on `jerrywoof.github.io`:

- **CORS**: every response has `Access-Control-Allow-Origin: *`, so any origin can `GET`. Preflight
  `OPTIONS` requests get **405**. Send simple GETs only: no custom headers, and no `Content-Type` on a GET.
- **Caching**: `Cache-Control: max-age=600` (not configurable), `ETag` with `If-None-Match` → 304, gzip,
  `Range` requests.
- **Errors**: a missing file is a **404 with an HTML body**. Check the status before parsing. Overload
  gives a 429, also with an HTML body.
- **No query strings**: a static host ignores them, so there is no server-side filtering or paging.
  The data is split into files instead.
- `.jsonl` may be served as `application/octet-stream`; do not rely on its Content-Type.
- Release assets on `github.com/.../releases/download/...` send **no CORS headers**. They are for `curl`,
  Python and R, not for browser apps. Browsers should use the Pages API.

## Versioning

| What | Format | Changes when |
|---|---|---|
| API | `v1` in the path; `api_version` | Only on a breaking change: a field removed or renamed, a type or ID scheme changed. New fields may appear within v1 (envelopes and most records allow extra properties; `link` and `review` records are strict and change only with a new version). An old version stays at least 6 months, with `"deprecated": true` in its `index.json` |
| Dataset | SemVer = package version; tag `vX.Y.Z` | Major: link semantics change. Minor: new directive, country or source. Patch: corrections |
| Weekly rebuilds | `built_at` | Every Pages build; not a release |

Pages always serves the latest build. Fixed snapshots are the release assets and the Zenodo records.

## Building and validating locally

```bash
python scripts/build_site.py --offline      # site/data (or without --offline: live, with static fallback)
python scripts/build_api.py --check         # site/api/v1, then validate every file against its schema
python scripts/build_api.py --check-only    # validate an existing build
python scripts/build_api.py --require-live  # fail if a pair computed live (the seed, NIS2) fell back to the static copy
python scripts/build_api.py --require-live all  # every pair, after build_site.py --live-alignments all
```

The Pages workflow runs `build_api.py --check` right after `build_site.py`, so a schema error stops the deploy; the site's `data.html` links the API and shows its counts. `build_api.py` fetches nothing. It reads `site/data/` (built by `build_site.py`) and `site/static-data/`
for the pairs the website lists, and `data/eval/` and `data/reviews/` for the dataset (the same
`openacquis.dataset.build_dataset`). It writes `site/api/v1/`, replacing the previous build. If
`site/data/index.json` is missing (no CELLAR build), `directives.json` falls back to the curated list
without measures, and each entry has an `error` saying so.

`--check` uses a small stdlib JSON Schema validator. It supports `type`, `enum`, `const`, `pattern`,
lengths, bounds, `properties`, `required`, `additionalProperties`, `patternProperties`, `items`,
`allOf` / `anyOf` / `oneOf` / `not` and `$ref` across the schema files. A schema that uses any other
keyword fails the check, so no rule is silently skipped. When `jsonschema` is installed, the tests
cross-check every file with the reference `Draft202012Validator`. The OpenAPI file also passes
`openapi-spec-validator`.

The schemas in `api/schemas/` are written by hand. Two tests keep them in line with the code:
- the `link` schema must list exactly the fields in `openacquis.dataset.FIELDS`;
- the `review` schema must list exactly the fields in `openacquis.review.FIELDS` and `MACHINE_FIELDS`.

## Releases

`.github/workflows/release.yml` runs on a tag `v*`:

1. It checks that the tag equals `openacquis.__version__` and the `version` in `pyproject.toml` and
   `CITATION.cff` (tag `v0.5.0` needs version `0.5.0` in all three).
2. It runs the offline tests.
3. It builds the site data (live CELLAR and portals, with static fallback) and the API, then runs
   `build_api.py --check`.
4. It attaches these assets to a GitHub Release, with release notes taken from the counts and the
   CHANGELOG section:
   - `openacquis-dataset-vX.Y.Z.zip`
   - `openacquis-api-vX.Y.Z.zip` (the whole `api/v1` tree)
   - `links.csv`, `links.jsonl`, `reviews.jsonl`, `datapackage.json`
   - `SHA256SUMS`

Run it by hand ("Run workflow") for a dry run. It does everything except the release and uploads the
files as a workflow artifact. Tick *require_live* to make it fail when a pair that is computed live (the
pairs of `build_site.SEED`, i.e. NIS2) fell back to the static copy. The other pairs come from
`site/static-data/` by design and never count as fallbacks.

To release:

```bash
# bump version in pyproject.toml, src/openacquis/__init__.py and CITATION.cff (+ date-released); update CHANGELOG.md
git tag v0.5.0 && git push origin v0.5.0
```

## DOI (Zenodo)

`.zenodo.json` describes the record: a software record under the MIT licence, with the dataset and API
linked as related identifiers. `CITATION.cff` gives GitHub's "Cite this repository" box. When
`.zenodo.json` exists, Zenodo ignores `CITATION.cff`, so keep the two consistent.

**One-time manual step (repository owner):**

1. Sign in at <https://zenodo.org> with GitHub. Open *Account → GitHub* and switch the toggle **on** for
   `JerryWoof/OpenAcquis`. Only an owner can do this; it cannot be automated.
2. Push the next tag. When the release is published, Zenodo archives the tag's source zip and mints two
   DOIs:
   - a **version DOI**, for that release;
   - a **concept DOI**, for all versions.
3. Put the concept DOI into the repository:
   - `CITATION.cff`: `doi:` (or `identifiers:`);
   - `scripts/build_api.py`: `CONCEPT_DOI`, which goes into `index.json` → `links.doi`;
   - the README badge.
4. After each release, check on Zenodo that a new version appeared. The integration can be switched off
   silently, for example when the webhook breaks.

Notes:
- DOIs cannot be reserved before the first release.
- Draft releases are ignored. **Pre-releases are archived**, so do not tag test releases on this repository
  while the toggle is on.
- Zenodo archives **only the source zip of the tag**, not the release assets. The dataset inputs are
  committed (`data/eval/` correlation tables, `data/reviews/` review records), and `openacquis dataset
  build` rebuilds the dataset from them. The built files and the API snapshot are on the GitHub Release.
- One record has one licence. The software record is MIT. A separate CC-BY-4.0 *dataset* record needs a
  Zenodo token and the REST deposit API (`ZENODO_TOKEN` secret, new version per release). That is not set
  up yet; see the design notes in the roadmap.

## Limitations

- The API serves whatever the last Pages build produced. If a portal was down, a pair is `static` and
  can be weeks old; check `mode` and `computed_at`.
- Texts are clipped (see "Texts"), and only provisions that took part in an alignment are published.
- An act aligned with several directives gets one provision file. It merges the provisions of each
  alignment, which may come from different version dates.
- There are no per-provision files, server-side filters or search. Download the per-pair files or the bulk
  dataset.
