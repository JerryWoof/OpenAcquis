"""EU member-state codes used by the EU Publications Office (ISO 3166-1 alpha-3)."""

AUTHORITY = "http://publications.europa.eu/resource/authority/country/"

MEMBER_STATES: dict[str, str] = {
    "AUT": "Austria",
    "BEL": "Belgium",
    "BGR": "Bulgaria",
    "HRV": "Croatia",
    "CYP": "Cyprus",
    "CZE": "Czechia",
    "DNK": "Denmark",
    "EST": "Estonia",
    "FIN": "Finland",
    "FRA": "France",
    "DEU": "Germany",
    "GRC": "Greece",
    "HUN": "Hungary",
    "IRL": "Ireland",
    "ITA": "Italy",
    "LVA": "Latvia",
    "LTU": "Lithuania",
    "LUX": "Luxembourg",
    "MLT": "Malta",
    "NLD": "Netherlands",
    "POL": "Poland",
    "PRT": "Portugal",
    "ROU": "Romania",
    "SVK": "Slovakia",
    "SVN": "Slovenia",
    "ESP": "Spain",
    "SWE": "Sweden",
}

# Common two-letter aliases so users can type "cz" or "de".
ALPHA2: dict[str, str] = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "HR": "HRV", "CY": "CYP", "CZ": "CZE",
    "DK": "DNK", "EE": "EST", "FI": "FIN", "FR": "FRA", "DE": "DEU", "GR": "GRC",
    "EL": "GRC", "HU": "HUN", "IE": "IRL", "IT": "ITA", "LV": "LVA", "LT": "LTU",
    "LU": "LUX", "MT": "MLT", "NL": "NLD", "PL": "POL", "PT": "PRT", "RO": "ROU",
    "SK": "SVK", "SI": "SVN", "ES": "ESP", "SE": "SWE",
}


def normalize_country(code: str) -> str:
    """Return the alpha-3 code for a member state given alpha-2 or alpha-3 input."""
    c = code.strip().upper()
    if c in MEMBER_STATES:
        return c
    if c in ALPHA2:
        return ALPHA2[c]
    raise ValueError(f"Unknown EU member state code: {code!r}")


def country_from_uri(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]
