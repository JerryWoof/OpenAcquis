# Data sources

| Scope | Source | Access | Status in OpenAcquis |
|---|---|---|---|
| EU acts: metadata; national implementing measures | EU Publications Office CELLAR, SPARQL endpoint `https://publications.europa.eu/webapi/rdf/sparql` | Public, no key | **Used** (v0.1): `summary`, `measures`, website |
| EU acts: text, 24 languages | CELLAR content negotiation: `GET https://publications.europa.eu/resource/celex/{CELEX}` with `Accept: application/xhtml+xml` and `Accept-Language: {lang}` (falls back to `text/html` for older acts; `300 Multiple Choices` = several `DOC_n` files, all fetched) | Public, no key | **Used** (v0.2): `text`, `openacquis.eurlex` |
| EU act pages | EUR-Lex, e.g. `https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32022L2555` | Public, but scripted requests get an AWS WAF challenge (`202`, `x-amzn-waf-action: challenge`, empty body) | Fallback only (detected and reported, never parsed); linked from every provision |
| Czechia: laws | e-Sbírka (official Collection of Laws), `https://e-sbirka.gov.cz` (formerly www.e-sbirka.cz): the JSON API behind the web app, `GET /sbr-cache/dokumenty-sbirky/{staleUrl}` (metadata) and `.../{staleUrl}/fragmenty?cisloStranky=N` (text, 1000 fragments per page) | Public, no key or registration; **undocumented**, may change | **Used** (v0.2): `law cz`, `openacquis.adapters.cz` |
| Czechia: bulk | e-Sbírka open data, `https://opendata.eselpoint.gov.cz/datove-sady-esbirka/` (nightly JSON dumps; fragments ~1.2 GB gzip) and SPARQL | Public | Documented; not used yet (fallback if the API changes) |
| Czechia: correlation tables | Government bills on psp.cz (*rozdílové tabulky*), e.g. `https://www.psp.cz/sqw/text/orig2.sqw?idd=244067` (tisk 759/0, Word `.doc`); ISAP | Public documents (official works) | **Used** (v0.3): evaluation set `data/eval/nis2_cz.jsonl`, `openacquis.evalset`, cached under `~/.cache/openacquis/psp/` |
| Embedding models (optional) | Hugging Face Hub via sentence-transformers, e.g. `intfloat/multilingual-e5-base` (~1.1 GB, downloaded once into `~/.cache/huggingface`) | Public | **Used** (v0.3) with `openacquis[embeddings]`; vectors cached in `~/.cache/openacquis/embeddings/` |
| LLM judge (optional) | Anthropic Messages API | API key (`ANTHROPIC_API_KEY`), paid | Implemented (v0.3), not yet run; results cached in `~/.cache/openacquis/judge/` |
| Slovakia: laws | Slov-Lex, static version of the portal: `GET https://static.slov-lex.sk/static/SK/ZZ/{year}/{number}/` (version history: in-force dates and amending act of every version) and `.../{year}/{number}/{version}.html` (one version's full text, structured HTML with anchors) | Public, no key | **Used** (v0.4): `law sk`, `openacquis.adapters.sk`, cached under `slovlex/` |
| Slovakia: search, bulk | `api-gateway.slov-lex.sk/vyhladavanie/predpisZbierky/znenie?zodpovedajucaUcinnost=…&predpis=/SK/ZZ/…` (JSON: version in force on a date); ZIP exports at `/static/exporty/index.portal` | Public | Documented; not needed |
| Austria: laws | RIS OGD: search API `GET https://data.bka.gv.at/ris/api/v2.6/Bundesrecht?Applikation=BrKons&Gesetzesnummer=N&Fassung.FassungVom=YYYY-MM-DD&DokumenteProSeite=OneHundred` (one hit per §, Artikel, Anlage; `Kundmachungsorgannummer=94/2025` finds laws by BGBl number) and `GET https://ogd.ris.bka.gv.at/Dokumente/Bundesnormen/{NOR}/{NOR}.xml` (RIS XML of one §) | Public open government data (CC BY 4.0), no key. TLS chains to HARICA TLS RSA Root CA 2021, missing from some OpenSSL bundles: `pip install 'openacquis[tls]'` (certifi) or set `SSL_CERT_FILE` | **Used** (v0.4): `law at`, `openacquis.adapters.at`, cached under `ris/`. Federal law only (`BrKons`) |
| Germany: laws | gesetze-im-internet.de: `GET /gii-toc.xml` (about 6,100 acts) and `GET /<slug>/xml.zip` (current consolidated text, `gii-norm.dtd`) | Public, no key; current text only, no history | **Used** (v0.4): `law de`, `openacquis.adapters.de`, cached under `gii/`. The table of contents also resolves abbreviations NeuRIS lacks (EnWG → `enwg_2005`, TKG → `tkg_2021`), with or without NeuRIS |
| Germany: metadata | NeuRIS, `https://testphase.rechtsinformationen.bund.de/v1/legislation?risAbbreviation=…` / `?eli=eli/bund/bgbl-1/2025/301` and `/v1/legislation/eli/…/deu` (ELI, in-force date, § eIds) | Public test phase (due 2028), incomplete (no BGB, TKG, EnWG when checked) | **Used** (v0.4) for ELIs, dates and mapping BGBl citations to acts; optional (a failure only drops ELIs), cached under `neuris/` |
| Germany: Land law | Land portals | Varies | Not covered: 17 of the 20 NIS2 measures Germany notified are Land measures |
| Other member states | N-Lex gateway and national official journals; ELI adoption varies | Varies | Help wanted |
| Lobbying extension | Have Your Say consultations; EP amendments (Parltrack); EU Transparency Register | Public | Later |

## Politeness

Every request sends the User-Agent `OpenAcquis/0.4 (+https://github.com/JerryWoof/OpenAcquis)` and has a timeout.
Downloads are cached on disk under `~/.cache/openacquis/`:

- `eurlex/`: EU texts, with no expiry. A published OJ text does not change; `openacquis text --refresh`
  downloads it again.
- `esbirka/`: e-Sbírka JSON, kept for 24 h.
- `slovlex/`, `ris/`, `gii/`, `neuris/`: the Slovak, Austrian and German adapters.

Set `OPENACQUIS_CACHE` to move the cache. The national clients wait at least 1 s between requests. The
website build (`scripts/build_site.py`) pauses 2 s between directives and retries with backoff.

## Which notified measures can be fetched (NIS2, October 2026)

`openacquis measures 32022L2555 -c SK --map` shows how each measure CELLAR lists maps to an act the
country's adapter can fetch (`openacquis.adapters.map_measures`). `openacquis align ... --measures` searches
all of them.

| Country | Measures | Mapped to a fetchable act | How CELLAR identifies them |
|---|---:|---:|---|
| CZ | 83 | 83 mapped (amending acts parse as their amendment points) | `reference` = `264/2025`; the number is also read from the title |
| SK | 9 | 9 | `reference` = `69/2018` or `69/2018-366/2024` (the act consolidated as amended by 366/2024: the latest version that amendment produced which is already in force, since an amending act can take effect in stages) |
| AT | 8 | 8 (2 repealed on 2026-10-01; they need a version date) | `reference` is empty; the citation is in the German title (abbreviation, Stammfassung, cited §§, also "§§ 3 und 5" and "§§ 3 bis 5"; for large acts only the cited §§ are fetched, returned in document order). Laws promulgated before 2004 have no ELI on RIS, so `Document.eli` is `None` for them |
| DE | 20 | 1 (the federal law; the BSIG it re-enacts) | No `reference`; gazette number and page queried separately, then NeuRIS. 17 are Land measures (Land names and adjectives such as "Hamburgisches" or "Berliner" are recognised), 1 not found (a joint *Runderlass*: Runderlass, Ministerialblatt, Verwaltungsvorschrift and Amtsblatt titles are reported as administrative measures without a BGBl lookup), 1 explanatory document |

## Example: fetch a directive text by hand

```bash
curl -sL -H 'Accept: application/xhtml+xml' -H 'Accept-Language: cs' \
  https://publications.europa.eu/resource/celex/32022L2555 | head
```

## Example: e-Sbírka

```bash
# metadata of the current consolidated version of Act 264/2025 Sb.
curl -s 'https://e-sbirka.gov.cz/sbr-cache/dokumenty-sbirky/%2Fsb%2F2025%2F264'
# first page of its text (use the dated staleUrl from the metadata)
curl -s 'https://e-sbirka.gov.cz/sbr-cache/dokumenty-sbirky/%2Fsb%2F2025%2F264%2F2025-11-01/fragmenty?cisloStranky=0'
```

## Example SPARQL

National measures Czechia notified for NIS2:

```sparql
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT ?ref ?title WHERE {
  ?dir cdm:resource_legal_id_celex "32022L2555"^^<http://www.w3.org/2001/XMLSchema#string> .
  ?m cdm:measure_national_implementing_implements_resource_legal ?dir ;
     cdm:measure_national_implementing_implemented_by_country
       <http://publications.europa.eu/resource/authority/country/CZE> ;
     cdm:measure_national_implementing_reference_member-state ?ref .
  OPTIONAL { ?e cdm:expression_belongs_to_work ?m ; cdm:expression_title ?title }
}
```

## Notes
- Notified measures reflect what member states report; they can be incomplete or include broadly related acts.
- Reuse of EUR-Lex data: Commission Decision 2011/833/EU; credit "© European Union, https://eur-lex.europa.eu".
