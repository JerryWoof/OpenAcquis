# Change alerts

OpenAcquis checks every week whether the legal sources behind the site have changed. A change can be a new
national measure notified for a directive, an amendment or corrigendum of the directive, a new version of a
national act, or a new implementing decree. Each changed item gets one GitHub issue (label `change-alert`)
and an entry in the Atom feed at <https://jerrywoof.github.io/OpenAcquis/feed.xml>.

An alert says that a source's record changed. It is a hint for a reviewer, not a finding about how a country
transposed a directive. Check the source before acting on it.

Code: `src/openacquis/alerts.py` (stdlib only), `scripts/check_changes.py` (also `openacquis alerts check|feed`, run from the repository root or with `--root DIR`), `.github/workflows/alerts.yml`. The website build renders `site/feed.xml` from the state file, and every page links to it.
State: `data/alerts/state.json`.

## What is watched

- **The watch list** is `data/directives.json`, the directive registry. It gives every directive's CELEX
  number and, per country, `acts` (adapter references, first = main act). If the file does not exist, the
  built-in fallback is NIS2 and its main act in each adapter country: CZ 264/2025 Sb., SK 69/2018 Z. z.,
  AT NISG 2026 (Gesetzesnummer 20013065) and DE BSIG (`bsig_2025`).
- **CELLAR:** every directive on the list, plus its notified national measures in all 27 member states.
- **National acts:** every act in the registry whose country has an adapter (CZ, SK, AT, DE). Acts in other
  countries are skipped. For AT the reference must be a Gesetzesnummer; an abbreviation produces a warning
  and is skipped.

## Signals

Each item is checked with one or two small requests. Its **fingerprint** is a hash of what the source says
about it. Fields that only steer the next request (`md5`, `etag`, `last_modified`) are left out of the
hash.

| Item key | Source and request | Fingerprint | Alert kinds |
|---|---|---|---|
| `eu:32022L2555` | CELLAR SPARQL, one query for all directives | `(relation, CELEX)` pairs: amending acts, corrigenda, consolidated versions | `eu_amended`, `eu_corrigendum`, `eu_consolidated` |
| `nim:32022L2555:DEU` | CELLAR SPARQL, one query for all directives | `(measure id, notification date)` pairs for one member state | `new_measure`, `measure_removed`, `measure_updated` |
| `cz:264/2025` | e-Sbírka `/sbr-cache/dokumenty-sbirky/{/sb/2025/264}/historie` and `/souvislosti` | versions (`staleUrl`, `typZneni`, amending acts), including future (`BUDOUCI`) ones; acts that amend it (`JE_MENEN`), implement it (`JE_PROVADEN`) or repeal it (`JE_ZRUSEN`) | `new_version`, `version_in_force`, `amending_act`, `implementing_decree`, `repealed` |
| `sk:69/2018` | Slov-Lex: `HEAD` on `static.slov-lex.sk/static/SK/ZZ/2018/69/`; the page is fetched only when `content-md5` changes | version table rows (IRI, in force from and to, amending acts) | `new_version`, `version_dates`, `amending_act` |
| `at:20013065` | RIS OGD `Bundesrecht?Applikation=BrKons&Gesetzesnummer=…` (100 norms per page) | every norm version (ID, §, `Geaendert`, in force from and to), and the BGBl acts in the § 0 `Aenderung` field | `new_version`, `norm_changed`, `amending_act` |
| `de:bsig_2025` | gesetze-im-internet.de `xml.zip` with `If-None-Match` and `If-Modified-Since` (304 when unchanged) | `builddate` and the `Stand` / `Hinweis` notes | `new_build`, `amending_act`, `pending_amendment` ("textlich nachgewiesen") |

The CELLAR measures query uses no `GROUP BY` or `SAMPLE`/`MAX`, and deduplication is done in Python. With
aggregates, this endpoint returned different notification dates on each run, which produced false alerts.

If an alignment page was built on a changed act, its issue names the page and the date of the text it was built
on, and says the page may need a rebuild. Pages are read from `site/data/align/*.json` when a site build is
present, else from the committed `site/static-data/*.json` (review and headings files are skipped); the
workflow passes `--align-dir site/static-data`.

## Robustness

- **New items are a silent baseline.** An item seen for the first time raises no alert: this covers the
  first run and any act or directive newly added to the registry. One exception: if a member state notifies
  its first measure for a directive that was already watched, that is an alert.
- **Outages are warnings, not alerts.** Retries with backoff follow 429, 5xx and network errors. If a source
  still fails, or returns an empty or unexpected answer, its items keep their previous fingerprint and the
  run's summary lists a warning.
  - If the amendments side or the notified-measures side of the CELLAR answer comes back empty for every
    watched directive while the state had items there, it counts as a CELLAR failure (a partial outage): every
    previous fingerprint is kept and no issue opens.
  - If a single record disappears (one member state's measures, or one directive's amendments and
    consolidations), its old fingerprint is kept and a warning is logged, without counting as a source
    failure. Only after 3 consecutive runs without it is the removal reported as a change (`measure_removed`
    for a member state, which then leaves the state).
- **One health issue at most.** After 3 consecutive failed runs of a source, the workflow opens one
  `alerts-health` issue and updates it each run. It closes that issue when every source answers again.
- **Fetched content is never executed.** JSON is parsed with `json`, HTML and XML with regular expressions,
  and the GII zip with `zipfile` (only `.xml` members up to 30 MB are read).
- **Issue text is made safe.** Text taken from a source is shown only as inline code spans, after control
  characters and bidi characters are stripped and backticks replaced, so it cannot create mentions, links or
  markup. The workflow passes titles and bodies to `gh` as files and arguments, never inside a script.
- **Politeness.** Requests carry the OpenAcquis User-Agent and are spaced at least 1 s apart per host. Slov-Lex
  costs only a HEAD request and gesetze-im-internet.de usually answers 304. An optional cache under
  `$OPENACQUIS_CACHE/alerts` (`--cache-ttl`, off by default) avoids repeat requests when rerunning locally.

## Running it

```bash
python scripts/check_changes.py                      # check, update data/alerts/state.json, print a JSON summary
python scripts/check_changes.py check --dry-run --out-dir /tmp/alerts   # also write the issue texts; keep the state
python scripts/check_changes.py check --sources cz,sk                   # a subset of cellar,cz,sk,at,de
                                                                        # (the other sources' items are kept)
python scripts/check_changes.py feed --out site/feed.xml                # Atom feed from the state file (offline)
```

With `--out-dir`, the checker also writes:
- `changes.json`;
- `summary.md`;
- `issues/NNN/{key,title,body.md,comment.md}`;
- `health.md`, while a source keeps failing.

The state file is rewritten only when something other than the run timestamps changed, so a quiet week makes
no commit.

## State file

`data/alerts/state.json` has one line per item and one per event, so a change shows up as a one-line git
diff:

```
{
 "schema": 1, "generator": "openacquis.alerts", "watchlist": "data/directives.json", "checked_at": "…",
 "health": {"cz": {"failures": 0, "last_ok": "…"}, …},
 "items": {
  "cz:264/2025": {"source":"cz","label":"CZ 264/2025 Sb. (NIS2)","url":"…","fingerprint":"…","data":{…},"since":"…","changed_at":""},
  …
 },
 "events": [ {"key":…,"title":…,"kinds":[…],"lines":[…],"url":…,"detected":…,"fingerprint":…}, … ]
}
```

`events` keeps the newest 200 changes, and the feed shows the newest 50.

## Workflow (`.github/workflows/alerts.yml`)

The workflow runs weekly (Monday 05:41 UTC) and on demand (`workflow_dispatch`). Its top-level permissions
are `{}`, and each job gets only what it needs:

1. **`check`** (`contents: read`, no token left in `.git/config`) runs the checker. It writes the job summary
   and uploads the new state and the issue texts as an artifact.
2. **`commit-state`** (`contents: write`) runs no repository code. It checks that the artifact is a state
   file, copies it to `data/alerts/state.json` and pushes it to `main` as `github-actions[bot]`. A push
   rejected by a concurrent commit is retried after a rebase.
3. **`notify`** (`issues: write`, `actions: write`, no checkout) runs only once the state is saved, so a
   failed commit does not produce duplicate alerts the next week. It:
   - creates the `change-alert` and `alerts-health` labels if they are missing;
   - finds open issues by a hidden marker (`<!-- openacquis-alert:<key> -->`), then comments on the matching
     issue or opens a new one;
   - maintains the health issue;
   - when anything changed, dispatches `pages.yml` so the feed is rebuilt. This is needed because pushes made
     with `GITHUB_TOKEN` do not trigger workflows.

If `main` is protected so that the bot cannot push, the `commit-state` job fails, `notify` is skipped, and
the next run reports the same changes again. In that case, either allow `github-actions[bot]` to push to
`main`, or change the job to open a pull request.

## Feed at site build time

The feed is rendered from the committed state file by `openacquis.alerts.write_feed`. The site build has to
call it, and `index.html` should advertise it:

```python
# scripts/build_site.py, at the end of main()
from openacquis.alerts import write_feed
write_feed(ROOT / "data" / "alerts" / "state.json", ROOT / "site" / "feed.xml")
```

```html
<link rel="alternate" type="application/atom+xml" title="OpenAcquis change alerts" href="feed.xml">
```

Each entry's `id` is `tag:jerrywoof.github.io,2026:openacquis/alerts/<key>/<fingerprint>`, so each change
appears exactly once. Before the first change, the feed is valid but has no entries.

## Numbers (live, 2026-10-12)

These are from the run over `data/directives.json` (12 directives, 51 national acts):

| | Seeding run | Repeat run |
|---|---|---|
| Items | 365 (12 `eu`, 302 `nim`, 10 CZ, 14 SK, 11 AT, 16 DE) | 365 |
| Requests | 99 | 85 |
| Time | 117 s | 89 s |
| Changes | 0 (all items were new: baseline) | 0 (no false positives) |
| Warnings | 0 | 0 |

- **State file:** 447 KB. Notified measures account for 236 KB of it and RIS norm versions for 181 KB; the
  Austrian AktG alone has 998 norm versions, which take 10 pages.
- **Where the repeat run saved requests:** Slov-Lex needed only HEAD requests, and gesetze-im-internet.de
  answered 304 to every conditional GET.

## Limitations

- **CELLAR only shows what was notified.** A new national act appears there only after the member state
  notifies it, often months after it was adopted. The national signals catch amendments to acts already
  being watched, not new acts that nobody has added to the registry.
- **German texts lag behind.** gesetze-im-internet.de reports only the current consolidated text. Its
  "textlich nachgewiesen" notes give early warning of an amendment that has not yet been fully worked in.
  NeuRIS and the Federal Law Gazette are not watched yet.
- **RIS is fetched in full every run.** It has a cheaper pre-check (`ImRisSeit=…`), but the checker does not
  use it, because a missed run could hide a change. The cost is the full norm list, one request per 100
  norms. The `BgblAuth` signal for promulgated but not yet consolidated amendments is not used either.
- **e-Sbírka's API is undocumented.** The checker uses the `/sbr-cache` endpoints of the e-Sbírka web
  application, so a change in their shape becomes a warning, which the health issue reports after 3 runs.
- **EUR-Lex RSS is not used.** Its feeds cannot follow a single act without a My EUR-Lex account.
