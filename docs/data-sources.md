# Data sources

| Scope | Source | Access | Status in OpenAcquis |
|---|---|---|---|
| EU acts: metadata; national implementing measures | EU Publications Office CELLAR, SPARQL endpoint `https://publications.europa.eu/webapi/rdf/sparql` | Public, no key | **Used** (v0.1): `summary`, `measures`, website |
| EU acts: text, 24 languages | CELLAR content negotiation: `GET https://publications.europa.eu/resource/celex/{CELEX}` with `Accept: application/xhtml+xml` and `Accept-Language: {lang}` (falls back to `text/html` for older acts; `300 Multiple Choices` = several `DOC_n` files, all fetched) | Public, no key | **Used** (v0.2): `text`, `openacquis.eurlex` |
| EU act pages | EUR-Lex, e.g. `https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32022L2555` | Public, but scripted requests get an AWS WAF challenge (`202`, `x-amzn-waf-action: challenge`, empty body) | Fallback only (detected and reported, never parsed); linked from every provision |
| Czechia: laws | e-Sbírka (official Collection of Laws), `https://e-sbirka.gov.cz` (formerly www.e-sbirka.cz): the JSON API behind the web app, `GET /sbr-cache/dokumenty-sbirky/{staleUrl}` (metadata) and `.../{staleUrl}/fragmenty?cisloStranky=N` (text, 1000 fragments per page) | Public, no key or registration; **undocumented**, may change | **Used** (v0.2): `law cz`, `openacquis.adapters.cz` |
| Czechia: bulk | e-Sbírka open data, `https://opendata.eselpoint.gov.cz/datove-sady-esbirka/` (nightly JSON dumps; fragments ~1.2 GB gzip) and SPARQL | Public | Documented; not used yet (fallback if the API changes) |
| Czechia: correlation tables | Government bills on psp.cz (*rozdílové tabulky*); ISAP | Public documents | Planned (v0.3, evaluation) |
| Slovakia | Slov-Lex | Public | Planned (v0.4) |
| Austria | RIS (Rechtsinformationssystem des Bundes), Open Government Data API | Public API | Planned (v0.4) |
| Germany | gesetze-im-internet.de (XML); NeuRIS / rechtsinformationen.bund.de (in testing, due 2028) | Public | Planned (v0.4) |
| Other member states | N-Lex gateway and national official journals; ELI adoption varies | Varies | Help wanted |
| Lobbying extension | Have Your Say consultations; EP amendments (Parltrack); EU Transparency Register | Public | Later |

## Politeness

Every request sends the User-Agent `OpenAcquis/0.2 (+https://github.com/JerryWoof/OpenAcquis)` and has a timeout.
Downloads are cached on disk under `~/.cache/openacquis/`: `eurlex/` holds EU texts with no expiry (a published
OJ text does not change; `openacquis text --refresh` downloads it again), `esbirka/` holds e-Sbírka JSON for 24 h.
Set `OPENACQUIS_CACHE` to move it. The e-Sbírka client waits at least 1 s between requests. The website build
(`scripts/build_site.py`) pauses 2 s between directives and retries with backoff.

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
