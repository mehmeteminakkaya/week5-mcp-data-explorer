"""Offline tests for the Data Explorer MCP server.

The dataset is replaced with a small fixture, so these tests never touch the network.
Separate tests fake the transport to prove the error paths return structured messages
instead of raising.
"""

import httpx
import pytest

import server

COUNTRIES = [
    {
        "name": {"common": "Turkey", "official": "Republic of Türkiye"},
        "cca3": "TUR",
        "capital": ["Ankara"],
        "region": "Asia",
        "subregion": "Western Asia",
        "currencies": {"TRY": {"name": "Turkish lira", "symbol": "₺"}},
        "languages": {"tur": "Turkish"},
        "borders": ["ARM", "AZE", "BGR", "GEO", "GRC", "IRN", "IRQ", "SYR"],
        "flag_url": "x",
        "population": 84339067,
        "area": 783562.0,
    },
    {
        "name": {"common": "Nigeria", "official": "Federal Republic of Nigeria"},
        "cca3": "NGA",
        "capital": ["Abuja"],
        "region": "Africa",
        "subregion": "Western Africa",
        "currencies": {"NGN": {"name": "Nigerian naira", "symbol": "₦"}},
        "languages": {"eng": "English"},
        "borders": ["BEN", "CMR", "TCD", "NER"],
        "population": 206139589,
        "area": 923768.0,
    },
    {
        "name": {"common": "Niger", "official": "Republic of Niger"},
        "cca3": "NER",
        "capital": ["Niamey"],
        "region": "Africa",
        "subregion": "Western Africa",
        "currencies": {"XOF": {"name": "West African CFA franc", "symbol": "Fr"}},
        "languages": {"fra": "French"},
        "borders": ["DZA", "BEN", "BFA", "TCD", "LBY", "MLI", "NGA"],
        "population": 24206644,
        "area": 1267000.0,
    },
    {
        "name": {"common": "Germany", "official": "Federal Republic of Germany"},
        "cca3": "DEU",
        "capital": ["Berlin"],
        "region": "Europe",
        "subregion": "Western Europe",
        "currencies": {"EUR": {"name": "Euro", "symbol": "€"}},
        "languages": {"deu": "German"},
        "borders": ["AUT", "BEL", "CZE", "DNK", "FRA", "LUX", "NLD", "POL", "CHE"],
        "population": 83240525,
        "area": 357114.0,
    },
]


@pytest.fixture(autouse=True)
def dataset(monkeypatch):
    """Serve the fixture and clear the cache after each test."""
    monkeypatch.setattr(server, "_countries", COUNTRIES)
    yield COUNTRIES
    monkeypatch.setattr(server, "_countries", None)


# ----------------------------------------------------------------- search_countries


def test_search_returns_compact_rows(dataset):
    rows = server.search_countries("nige")
    assert {row["name"] for row in rows} == {"Niger", "Nigeria"}
    assert set(rows[0]) == {"name", "capital", "region", "subregion", "cca3"}


def test_search_respects_limit(dataset):
    assert len(server.search_countries("nige", limit=1)) == 1


def test_search_shorter_name_first(dataset):
    # "Niger" is shorter than "Nigeria", so it should be scanned first.
    assert server.search_countries("nige")[0]["name"] == "Niger"


def test_search_is_case_insensitive(dataset):
    assert server.search_countries("TURKEY")[0]["name"] == "Turkey"


def test_search_matches_official_name(dataset):
    rows = server.search_countries("Türkiye", limit=10)
    assert [row["cca3"] for row in rows] == ["TUR"]


@pytest.mark.parametrize("query", ["", "   ", None, 42])
def test_search_rejects_empty_or_non_string_query(dataset, query):
    rows = server.search_countries(query)
    assert len(rows) == 1 and "error" in rows[0]


@pytest.mark.parametrize("limit", [0, 26, -3, "many", None])
def test_search_rejects_bad_limit(dataset, limit):
    rows = server.search_countries("nige", limit=limit)
    assert len(rows) == 1 and "error" in rows[0]


def test_search_no_match_is_empty_list(dataset):
    assert server.search_countries("atlantis") == []


def test_search_limit_is_clamped_at_max(dataset):
    assert len(server.search_countries("a", limit=25)) <= 25


# ------------------------------------------------------------------- get_country_details


def test_details_returns_required_shape(dataset):
    details = server.get_country_details("Turkey")
    assert details["cca3"] == "TUR"
    assert details["currencies"] == [
        {"code": "TRY", "name": "Turkish lira", "symbol": "₺"}
    ]
    assert details["languages"] == ["Turkish"]
    assert details["flag_url"].endswith("/tur.svg")
    assert {"cca3": "GRC", "name": None} in details["borders"]


def test_flag_url_uses_lowercase_cca3(dataset):
    """The dataset's flag files are lowercase: data/TUR.svg is a 404, data/tur.svg is 200."""
    assert server.get_country_details("TUR")["flag_url"].endswith("/tur.svg")


def test_details_accepts_cca3_code(dataset):
    assert server.get_country_details("deu")["name"] == "Germany"


def test_details_accepts_official_name(dataset):
    assert server.get_country_details("Federal Republic of Nigeria")["cca3"] == "NGA"


def test_details_resolves_unique_partial_name(dataset):
    assert server.get_country_details("german")["cca3"] == "DEU"


def test_details_ambiguous_name_returns_candidates(dataset):
    result = server.get_country_details("nige")
    assert "error" in result
    assert {c["cca3"] for c in result["candidates"]} == {"NGA", "NER"}


def test_details_unknown_country_returns_error(dataset):
    result = server.get_country_details("Atlantis")
    assert "error" in result
    # "search with a shorter query" would not find Türkiye from "Turkey"; the hint has to
    # offer a path that actually works.
    assert "cca3" in result["hint"] and "Türkiye" in result["hint"]


@pytest.mark.parametrize("name", ["", "   ", None, 3])
def test_details_rejects_empty_or_non_string_name(dataset, name):
    assert "error" in server.get_country_details(name)


def test_details_empty_borders_is_a_list(dataset, monkeypatch):
    island = {
        "name": {"common": "Iceland", "official": "Iceland"},
        "cca3": "ISL",
        "capital": ["Reykjavík"],
        "region": "Europe",
        "currencies": {"ISK": {"name": "Icelandic króna", "symbol": "kr"}},
        "languages": {"isl": "Icelandic"},
        "borders": [],
    }
    monkeypatch.setattr(server, "_countries", [island])
    assert server.get_country_details("Iceland")["borders"] == []


# ------------------------------------------------------------------------ error paths


def _break_dataset(monkeypatch, exc):
    monkeypatch.setattr(server, "_countries", None)

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(server.httpx, "get", boom)


def test_connect_error_becomes_structured_error(monkeypatch):
    _break_dataset(monkeypatch, httpx.ConnectError("connection refused"))
    rows = server.search_countries("nige")
    assert "error" in rows[0] and "ConnectError" in rows[0]["error"]


def test_timeout_becomes_structured_error(monkeypatch):
    _break_dataset(monkeypatch, httpx.ReadTimeout("timed out"))
    result = server.get_country_details("Turkey")
    assert "timed out" in result["error"]


def test_http_status_error_becomes_structured_error(monkeypatch):
    request = httpx.Request("GET", server.DATA_URL)
    _break_dataset(
        monkeypatch,
        httpx.HTTPStatusError(
            "server error",
            request=request,
            response=httpx.Response(500, request=request),
        ),
    )
    assert "HTTP 500" in server.get_country_details("Turkey")["error"]


def test_invalid_json_becomes_structured_error(monkeypatch):
    _break_dataset(monkeypatch, ValueError("expecting value"))
    assert "could not be parsed" in server.get_country_details("Turkey")["error"]


def test_non_list_payload_is_rejected(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"unexpected": "shape"}

    monkeypatch.setattr(server, "_countries", None)
    monkeypatch.setattr(server.httpx, "get", lambda *a, **k: FakeResponse())
    result = server.search_countries("nige")
    assert "could not be parsed" in result[0]["error"]


def test_null_only_payload_becomes_structured_error(monkeypatch):
    """[null] is a non-empty list, but caching it would crash every later tool call."""

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [None]

    monkeypatch.setattr(server, "_countries", None)
    monkeypatch.setattr(server.httpx, "get", lambda *a, **k: FakeResponse())
    rows = server.search_countries("nige")
    assert "error" in rows[0] and "no usable country records" in rows[0]["error"]
    assert server._countries is None  # nothing poisoned the cache


def test_non_dict_records_are_dropped_and_cache_stays_usable(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [None, COUNTRIES[0]]

    monkeypatch.setattr(server, "_countries", None)
    monkeypatch.setattr(server.httpx, "get", lambda *a, **k: FakeResponse())
    rows = server.search_countries("turk")
    assert [row["cca3"] for row in rows] == ["TUR"]
    assert server._countries == [COUNTRIES[0]]


def test_cache_is_used_after_first_load(monkeypatch):
    monkeypatch.setattr(server, "_countries", None)
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return COUNTRIES

    def counting_get(*args, **kwargs):
        calls.append(1)
        return FakeResponse()

    monkeypatch.setattr(server.httpx, "get", counting_get)
    server.search_countries("nige")
    server.search_countries("german")
    server.get_country_details("Turkey")
    assert len(calls) == 1
