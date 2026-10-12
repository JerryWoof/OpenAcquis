"""Client for the EU Publications Office CELLAR SPARQL endpoint.

CELLAR is the database behind EUR-Lex. It records, for every directive, the
national measures each member state has *notified* as transposing it. This is
act-level information ("law X implements directive Y"); OpenAcquis exists to
add the missing provision-level layer on top of it.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from .countries import AUTHORITY, country_from_uri, normalize_country

ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
LANG_AUTHORITY = "http://publications.europa.eu/resource/authority/language/"
USER_AGENT = "OpenAcquis/0.3 (+https://github.com/JerryWoof/OpenAcquis)"

CELEX_DIRECTIVE = re.compile(r"^3\d{4}L\d{4}$")

PREFIXES = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

Fetcher = Callable[[str], dict]


@dataclass
class Directive:
    celex: str
    uri: str
    title: str | None
    date: str | None
    transposition_deadlines: list[str] = field(default_factory=list)

    @property
    def eurlex_url(self) -> str:
        return f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{self.celex}"

    @property
    def eurlex_nim_url(self) -> str:
        return f"https://eur-lex.europa.eu/legal-content/EN/NIM/?uri=CELEX:{self.celex}"


@dataclass
class NationalMeasure:
    uri: str
    country: str
    reference: str | None
    title: str | None
    act_type: str | None
    journal: str | None
    journal_date: str | None
    notified: str | None
    commission_ref: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def validate_directive_celex(celex: str) -> str:
    c = celex.strip().upper()
    if not CELEX_DIRECTIVE.match(c):
        raise ValueError(
            f"{celex!r} is not a directive CELEX number (expected e.g. 32022L2555 for NIS2)"
        )
    return c


def _http_fetch(query: str, timeout: float = 90.0) -> dict:
    body = urllib.parse.urlencode({"query": query}).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _rows(result: dict) -> list[dict[str, str]]:
    return [
        {k: v["value"] for k, v in binding.items()}
        for binding in result.get("results", {}).get("bindings", [])
    ]


class CellarClient:
    """Small, dependency-free SPARQL client. Pass ``fetch`` to stub the network in tests."""

    def __init__(self, fetch: Fetcher | None = None):
        self._fetch = fetch or _http_fetch

    def query(self, sparql: str) -> list[dict[str, str]]:
        return _rows(self._fetch(PREFIXES + sparql))

    def directive(self, celex: str, lang: str = "ENG") -> Directive:
        celex = validate_directive_celex(celex)
        rows = self.query(
            f"""
SELECT ?w ?title ?date ?deadline WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^xsd:string .
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?w cdm:directive_date_transposition ?deadline }}
  OPTIONAL {{
    ?e cdm:expression_belongs_to_work ?w ;
       cdm:expression_uses_language <{LANG_AUTHORITY}{lang.upper()}> ;
       cdm:expression_title ?title .
  }}
}}"""
        )
        if not rows:
            raise LookupError(f"No act with CELEX {celex} found in CELLAR")
        deadlines = sorted({r["deadline"] for r in rows if r.get("deadline")})
        first = rows[0]
        return Directive(
            celex=celex,
            uri=first["w"],
            title=first.get("title"),
            date=first.get("date"),
            transposition_deadlines=deadlines,
        )

    def national_measures(
        self, celex: str, country: str | None = None
    ) -> list[NationalMeasure]:
        """List the national measures member states notified for a directive."""
        celex = validate_directive_celex(celex)
        country_filter = ""
        if country:
            country_filter = (
                f"FILTER(?c = <{AUTHORITY}{normalize_country(country)}>)"
            )
        rows = self.query(
            f"""
SELECT ?m ?c (SAMPLE(?ref) AS ?reference) (SAMPLE(?t) AS ?title)
       (SAMPLE(?type) AS ?act_type) (SAMPLE(?oj) AS ?journal)
       (SAMPLE(?ojdate) AS ?journal_date) (SAMPLE(?notif) AS ?notified)
       (SAMPLE(?cref) AS ?commission_ref)
WHERE {{
  ?dir cdm:resource_legal_id_celex "{celex}"^^xsd:string .
  ?m cdm:measure_national_implementing_implements_resource_legal ?dir ;
     cdm:measure_national_implementing_implemented_by_country ?c .
  {country_filter}
  OPTIONAL {{ ?m cdm:measure_national_implementing_reference_member-state ?ref }}
  OPTIONAL {{ ?m cdm:measure_national_implementing_type_act ?type }}
  OPTIONAL {{ ?m cdm:measure_national_implementing_name_official_journal ?oj }}
  OPTIONAL {{ ?m cdm:measure_national_implementing_date_official_journal ?ojdate }}
  OPTIONAL {{ ?m cdm:measure_national_implementing_date_notification ?notif }}
  OPTIONAL {{ ?m cdm:measure_national_implementing_reference_commission ?cref }}
  OPTIONAL {{ ?e cdm:expression_belongs_to_work ?m ; cdm:expression_title ?t }}
}}
GROUP BY ?m ?c
ORDER BY ?c DESC(?journal_date)"""
        )
        return [
            NationalMeasure(
                uri=r["m"],
                country=country_from_uri(r["c"]),
                reference=r.get("reference"),
                title=r.get("title"),
                act_type=r.get("act_type"),
                journal=r.get("journal"),
                journal_date=r.get("journal_date"),
                notified=r.get("notified"),
                commission_ref=r.get("commission_ref"),
            )
            for r in rows
        ]


def count_by_country(measures: list[NationalMeasure]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in measures:
        counts[m.country] = counts.get(m.country, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
