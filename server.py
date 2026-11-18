"""Data Explorer — an MCP server that gives an agent controlled access to country data.

Week 5 assignment (Advanced Context Management). The server speaks MCP over STDIO and
exposes two tools:

- ``search_countries(query, limit)`` — fuzzy name search, compact rows for scanning.
- ``get_country_details(name)`` — currencies, languages, borders and the flag URL.

Data source: the open ``mledoze/countries`` dataset (the data REST Countries was built
on), one JSON file fetched over HTTPS on first use and cached in memory. No API key.

Every failure mode is converted into a structured ``{"error": ...}`` payload: a broken
upstream call must never take the server down or hand the model a traceback.

Run (STDIO):
    python server.py

Tests:
    pytest
"""

from __future__ import annotations

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_DATA_URL = (
    "https://raw.githubusercontent.com/mledoze/countries/master/countries.json"
)
DATA_URL = os.environ.get("DATA_EXPLORER_DATA_URL", DEFAULT_DATA_URL)
FLAG_URL = "https://raw.githubusercontent.com/mledoze/countries/master/data/{cca3}.svg"

REQUEST_TIMEOUT = 15.0
DEFAULT_LIMIT = 5
MAX_LIMIT = 25

mcp = FastMCP("data-explorer")

_countries: list[dict] | None = None  # in-memory cache of the dataset


# --------------------------------------------------------------------------- data


def load_countries() -> list[dict]:
    """Download the dataset once and cache it.

    Raises ``httpx.HTTPError`` on transport/HTTP failures and ``ValueError`` when the
    payload is not the expected JSON list.
    """
    global _countries
    if _countries is None:
        resp = httpx.get(DATA_URL, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            raise ValueError("dataset payload was not a non-empty JSON list")
        # A record that is not an object (e.g. a null entry) would crash every tool that
        # touches it and poison the cache for the rest of the process, so only dicts are
        # kept. If nothing usable is left, fail like any other bad payload.
        records = [record for record in data if isinstance(record, dict)]
        if not records:
            raise ValueError("dataset payload contained no usable country records")
        _countries = records
    return _countries


def _dataset() -> tuple[list[dict] | None, dict | None]:
    """Load the dataset, turning every failure mode into a model-friendly error."""
    try:
        return load_countries(), None
    except httpx.TimeoutException:
        return None, {
            "error": (
                f"country dataset request timed out after {REQUEST_TIMEOUT:.0f}s; "
                "try again in a moment"
            )
        }
    except httpx.HTTPStatusError as exc:
        return None, {
            "error": f"country dataset request failed with HTTP {exc.response.status_code}"
        }
    except httpx.HTTPError as exc:
        return None, {
            "error": f"country dataset request failed ({type(exc).__name__}); check connectivity"
        }
    except (json.JSONDecodeError, ValueError) as exc:
        return None, {"error": f"country dataset could not be parsed: {exc}"}


def _common(country: dict) -> str:
    return (country.get("name") or {}).get("common") or ""


def _official(country: dict) -> str:
    return (country.get("name") or {}).get("official") or ""


def _by_cca3(countries: list[dict], cca3: str) -> dict | None:
    for country in countries:
        if (country.get("cca3") or "").upper() == cca3.upper():
            return country
    return None


def _details(country: dict, countries: list[dict]) -> dict:
    """Project one raw dataset record into the shape the agent gets to see."""
    currencies = country.get("currencies") or {}
    languages = country.get("languages") or {}
    border_details = []
    for code in country.get("borders") or []:
        neighbour = _by_cca3(countries, code)
        border_details.append(
            {"cca3": code, "name": _common(neighbour) if neighbour else None}
        )
    details = {
        "name": _common(country),
        "official": _official(country),
        "cca3": country.get("cca3"),
        "capital": list(country.get("capital") or []),
        "region": country.get("region"),
        "subregion": country.get("subregion"),
        "currencies": [
            {
                "code": code,
                "name": (info or {}).get("name"),
                "symbol": (info or {}).get("symbol"),
            }
            for code, info in currencies.items()
        ],
        "languages": list(languages.values()),
        "borders": border_details,
        # The dataset names its flag files in lowercase; an uppercase cca3 is a 404.
        "flag_url": FLAG_URL.format(cca3=(country.get("cca3") or "").lower()),
    }
    # Optional fields the dataset only carries for some countries: omit rather than
    # hand the model a null it has to reason about.
    for key in ("population", "area"):
        if country.get(key) is not None:
            details[key] = country[key]
    return details


# -------------------------------------------------------------------------- tools


@mcp.tool()
def search_countries(query: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Search countries by name and return compact rows to scan.

    Args:
        query: Part of a country's common or official name, e.g. ``"nige"``.
        limit: How many rows to return, 1-25 (default 5).

    Returns:
        A list of ``{name, capital, region, subregion, cca3}`` dicts, or a single
        ``{"error": ...}`` item when the input or the upstream call is bad. No matches
        is not an error: it returns an empty list.
    """
    if not isinstance(query, str) or not query.strip():
        return [{"error": "query must be a non-empty string, e.g. 'nige'"}]
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        return [{"error": f"limit must be an integer between 1 and {MAX_LIMIT}"}]
    if not 1 <= limit_int <= MAX_LIMIT:
        return [{"error": f"limit must be between 1 and {MAX_LIMIT}"}]

    countries, error = _dataset()
    if error:
        return [error]

    needle = query.strip().casefold()
    matches = [
        c
        for c in countries
        if needle in _common(c).casefold() or needle in _official(c).casefold()
    ]
    # Narrower names first, then ones that start with the query: better scanning order.
    matches.sort(
        key=lambda c: (not _common(c).casefold().startswith(needle), len(_common(c)))
    )
    return [
        {
            "name": _common(c),
            "capital": (c.get("capital") or [None])[0],
            "region": c.get("region"),
            "subregion": c.get("subregion"),
            "cca3": c.get("cca3"),
        }
        for c in matches[:limit_int]
    ]


@mcp.tool()
def get_country_details(name: str) -> dict:
    """Look up one country and return currencies, languages, borders and its flag URL.

    Args:
        name: A country name (common or official) or a three-letter ``cca3`` code,
            e.g. ``"Turkey"`` or ``"TUR"``. Partial names work when they match exactly
            one country.

    Returns:
        ``{name, official, cca3, capital, region, subregion, currencies, languages,
        borders, flag_url, population, area}`` or ``{"error": ...}``. Ambiguous input
        returns ``candidates`` so the agent can pick one and retry.
    """
    if not isinstance(name, str) or not name.strip():
        return {"error": "name must be a non-empty string, e.g. 'Turkey' or 'TUR'"}

    countries, error = _dataset()
    if error:
        return error

    needle = name.strip().casefold()
    exact = [
        c
        for c in countries
        if needle
        in {
            _common(c).casefold(),
            _official(c).casefold(),
            (c.get("cca3") or "").casefold(),
        }
    ]
    if len(exact) == 1:
        return _details(exact[0], countries)

    partial = [
        c
        for c in countries
        if needle in _common(c).casefold() or needle in _official(c).casefold()
    ]
    if len(partial) == 1:
        return _details(partial[0], countries)

    if not partial:
        return {
            "error": f"no country matched {name!r}",
            "hint": (
                "try search_countries with a different spelling (the dataset uses local "
                "names, e.g. 'Türkiye'), or pass the three-letter cca3 code (e.g. 'TUR')"
            ),
        }

    partial.sort(key=lambda c: len(_common(c)))
    candidates = [{"name": _common(c), "cca3": c.get("cca3")} for c in partial[:5]]
    return {
        "error": f"{name!r} is ambiguous: {len(partial)} countries match",
        "candidates": candidates,
        "hint": "call again with the exact name or the cca3 code",
    }


if __name__ == "__main__":
    mcp.run()
