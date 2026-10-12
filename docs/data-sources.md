# Data sources

| Scope | Source | Access | Status in OpenAcquis |
|---|---|---|---|
| EU acts, all languages; national implementing measures | EU Publications Office CELLAR, SPARQL endpoint `https://publications.europa.eu/webapi/rdf/sparql` | Public, no key | Used (v0.1) |
| EU act pages | EUR-Lex, e.g. `https://eur-lex.europa.eu/legal-content/EN/NIM/?uri=CELEX:32022L2555` | Public | Linked |
| Czechia: laws | e-Sbírka (official Collection of Laws) | Open data dumps (JSON); REST API after registration | Planned (v0.2) |
| Czechia: correlation tables | Government bills on psp.cz (*rozdílové tabulky*); ISAP | Public documents | Planned (v0.3, evaluation) |
| Slovakia | Slov-Lex | Public | Planned |
| Austria | RIS (Rechtsinformationssystem des Bundes), Open Government Data API | Public API | Planned |
| Germany | gesetze-im-internet.de (XML); NeuRIS / rechtsinformationen.bund.de (in testing, due 2028) | Public | Planned |
| Other member states | N-Lex gateway and national official journals; ELI adoption varies | Varies | Help wanted |
| Lobbying extension | Have Your Say consultations; EP amendments (Parltrack); EU Transparency Register | Public | Later |

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
