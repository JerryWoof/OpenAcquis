# Roadmap

## v0.1: act-level map (done)
- [x] CELLAR SPARQL client: directive metadata and notified national measures
- [x] CLI: `summary`, `measures` (table / JSON / CSV)

## v0.2: texts in
- [ ] Fetch directive text per language from EUR-Lex (Formex/XHTML) and split into provisions (article / paragraph / point)
- [ ] Czech adapter: consolidated texts from e-Sbírka, split into § / odst. / písm.
- [ ] Common provision data model with stable IDs (ELI where available)

## v0.3: first alignment (NIS2 × Czechia)
- [ ] Evaluation set from Czech *rozdílové tabulky* (official correlation tables attached to government bills, psp.cz)
- [ ] Candidate matching with multilingual embeddings (Czech directive text vs Czech law)
- [ ] LLM judge: same / partial / different parameter / stricter / missing / option chosen
- [ ] Report precision and recall against the evaluation set

## v0.4: four countries
- [ ] Adapters for Slovakia (Slov-Lex), Austria (RIS OGD API), Germany (gesetze-im-internet.de / NeuRIS)
- [ ] Gap and gold-plating candidates from unmatched provisions
- [ ] Review queue for human reviewers; reviewed results exported as an open CC-BY dataset

## v1.0: public observatory
- [ ] Website: article × country matrix with side-by-side source texts
- [ ] Public API and dataset releases
- [ ] 10–20 high-profile directives (NIS2, Accessibility Act, Pay Transparency, CSRD, Whistleblower)
- [ ] Alerts when a national law or directive changes

## Later
- Legislative footprint: trace wording from public consultation responses and EP amendments into final law
- All directives, all member states; candidate countries tracking alignment with the acquis
