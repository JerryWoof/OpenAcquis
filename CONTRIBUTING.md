# Contributing to OpenAcquis

Thanks for your interest! The project needs programmers, lawyers and people who know their country's legal databases.

## Ways to help
- **Country adapter**: write a module that downloads consolidated national laws for one member state and splits them into provisions. Open an issue titled `adapter: <country>` first so work isn't duplicated.
- **Legal review**: check AI-suggested matches. No coding needed.
- **Evaluation data**: convert official correlation tables (for example Czech *rozdílové tabulky*) into the test format.
- **Docs and translations**.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -m "not live"   # offline tests
pytest -m live         # hits the real servers (CELLAR, e-Sbírka)
python scripts/build_site.py --offline && python -m http.server 8000 -d site   # website preview (docs/website.md)
```

Guidelines:
- Keep the core dependency-free where practical; adapters may add optional extras.
- Every output that comes from AI must keep its evidence (source IDs and text spans) and a confidence label.
- Be polite to public servers: cache, rate-limit and set a descriptive User-Agent.

## Writing a country adapter

National-law adapters live in `src/openacquis/adapters/`, one module per country, named after the
ISO 3166-1 alpha-2 code (`cz.py`, `sk.py`, `at.py` …). The Czech adapter (`adapters/cz.py`, e-Sbírka) is the
reference implementation; read it alongside [docs/provisions.md](docs/provisions.md).

An adapter must:

1. **Return a `Document`** (`openacquis.provisions`) with `source` = the lowercase alpha-2 code, `act_id` =
   the national reference as people cite it (`264/2025`, `BGBl. I Nr. 94/2025` …), `language`, `title`,
   `date` (say in the docstring which date it is: adoption, version in force from …), and `eli`/`url` where the
   portal has them.
2. **Split the text into provisions** in document order, with `parent_id` links, `label` as printed and
   `text` holding only the provision's own text. Use the country's own kinds (`odst`, `pism`, `Abs`, `Z` …)
   and build IDs with `make_id(source, act_id, [(kind, number), …])`. Anchor IDs at the unit lawyers cite
   (§ or article), not at structural headings, so that IDs stay short and stable. `Document.add` rejects
   duplicate IDs.
3. **Prefer consolidated texts** and let the caller pick a version by date where the portal supports it.
4. **Take an injectable fetcher** so that tests never touch the network. In `cz.py` that is
   `ESbirkaClient(fetch=...)`, where `fetch(url) -> dict`. The default fetcher sends
   `cellar.USER_AGENT`, uses a timeout, waits between requests and caches responses under
   `$OPENACQUIS_CACHE/<portal>` (default `~/.cache/openacquis/<portal>`).
5. **Use only the standard library** (`urllib`, `html.parser`, `xml.etree`, `json`, `re`). If a portal really
   needs a third-party package, make it an optional extra in `pyproject.toml`.
6. **Expose a simple entry point**, `fetch_act(ref, version=None, fetch=None) -> Document`, and a
   `parse_reference(ref)` that accepts the usual citation forms and portal URLs.

Then:

- Register it in `src/openacquis/adapters/__init__.py`, and in `LAW_ADAPTERS` and `_client` in
  `src/openacquis/cli.py` so that `openacquis law <country> <ref>` works.
- **Tests**: save a few small, real API responses under `tests/fixtures/<cc>_*` (trim them; a few tens of KB
  each), and write offline tests that cover reference parsing, IDs, the hierarchy, headings, annexes, paging and
  error responses. Add one end-to-end test marked `@pytest.mark.live`.
- **Docs**: add the endpoints you actually use to [docs/data-sources.md](docs/data-sources.md), and include the
  live counts you checked (provisions per kind for one real act) in your pull request.

## Principles
- **Suggestions, not verdicts.** We never state that a country breached EU law.
- **Traceable.** Every claim links to the official source.
- **Open.** Code MIT, data CC-BY-4.0.
