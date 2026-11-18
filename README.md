# Week 5 — Data Explorer MCP Server

An MCP server that gives an agent **controlled access to country data**. Two tools, STDIO
transport, no API key: it wraps the open
[`mledoze/countries`](https://github.com/mledoze/countries) dataset (the data REST Countries
was built on) and returns model-friendly payloads.

Built for the Week 5 assignment (*Advanced Context Management*).

## Setup

```bash
pip install -r requirements.txt
python -m pytest -q        # 36 tests, no network needed
```

Requires Python 3.10+ (`mcp<2`, `httpx`).

## Run

```bash
python server.py
```

It speaks MCP over STDIO: it prints nothing to stdout and waits for a client. That is
normal. Pull a real transcript without a desktop client:

```bash
python scripts/mcp_client_demo.py            # both tools + bad input
python scripts/mcp_client_demo.py --errors   # upstream failure handling
```

## Register in an MCP client

Claude Desktop → `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "data-explorer": {
      "command": "python",
      "args": ["C:\\absolute\\path\\to\\week5-mcp-data-explorer\\server.py"]
    }
  }
}
```

Restart the client; `data-explorer` appears in the tool picker. In an IDE, register the
same command in its MCP settings. Set `DATA_EXPLORER_DATA_URL` to point the server at a
mirror of the dataset (or at a dead URL to exercise the error paths).

## Tool reference

### `search_countries(query, limit=5)`

| Parameter | Type | Notes |
| --- | --- | --- |
| `query` | `string` (required) | Substring of the common or official name, case-insensitive. |
| `limit` | `integer` (default `5`) | 1–25; out-of-range values are rejected, not clamped silently. |

Returns a list of compact rows — `name`, `capital`, `region`, `subregion`, `cca3` — sorted
with shorter names first. **No match is not an error:** it returns `[]`. Empty or
non-string input returns a single `{"error": ...}` item.

```json
// search_countries({"query": "nige", "limit": 3})
[
  {"name": "Niger",   "capital": "Niamey", "region": "Africa", "subregion": "Western Africa", "cca3": "NER"},
  {"name": "Nigeria", "capital": "Abuja",  "region": "Africa", "subregion": "Western Africa", "cca3": "NGA"}
]
```

```json
// search_countries({"query": "   "})
[{"error": "query must be a non-empty string, e.g. 'nige'"}]
```

### `get_country_details(name)`

| Parameter | Type | Notes |
| --- | --- | --- |
| `name` | `string` (required) | Common or official name, or a `cca3` code. Unique partial names are accepted. |

Returns `name`, `official`, `cca3`, `capital`, `region`, `subregion`, `currencies`
(`[{code, name, symbol}]`), `languages`, `borders` (`[{cca3, name}]`) and `flag_url`.

```json
// get_country_details({"name": "TUR"})
{
  "name": "Türkiye",
  "official": "Republic of Türkiye",
  "cca3": "TUR",
  "capital": ["Ankara"],
  "region": "Asia",
  "subregion": "Western Asia",
  "currencies": [{"code": "TRY", "name": "Turkish lira", "symbol": "₺"}],
  "languages": ["Turkish"],
  "borders": [
    {"cca3": "ARM", "name": "Armenia"}, {"cca3": "GRC", "name": "Greece"},
    {"cca3": "IRN", "name": "Iran"},    {"cca3": "SYR", "name": "Syria"}
  ],
  "flag_url": "https://raw.githubusercontent.com/mledoze/countries/master/data/tur.svg"
}
```

Ambiguity is reported, not guessed:

```json
// get_country_details({"name": "nige"})
{
  "error": "'nige' is ambiguous: 2 countries match",
  "candidates": [{"name": "Niger", "cca3": "NER"}, {"name": "Nigeria", "cca3": "NGA"}],
  "hint": "call again with the exact name or the cca3 code"
}
```

## Error behaviour

Every failure returns a structured payload; the server never raises through MCP and never
prints a traceback at the model:

| Failure | Payload |
| --- | --- |
| Empty / non-string input | `{"error": "query must be a non-empty string, e.g. 'nige'"}` |
| `limit` out of range / not an integer | `{"error": "limit must be between 1 and 25"}` |
| Timeout (15 s) | `{"error": "country dataset request timed out after 15s; try again in a moment"}` |
| HTTP error | `{"error": "country dataset request failed with HTTP 503"}` |
| Transport error (DNS, refused) | `{"error": "country dataset request failed (ConnectError); check connectivity"}` |
| Malformed JSON | `{"error": "country dataset could not be parsed: ..."}` |
| Invalid records (e.g. a `null` entry) | `{"error": "country dataset could not be parsed: dataset payload contained no usable country records"}` |
| No match | `search_countries` → `[]`; `get_country_details` → `{"error": ..., "hint": ...}` |
| Ambiguous name | `{"error": ..., "candidates": [...], "hint": ...}` |

The dataset is fetched once and cached in memory, so one upstream call serves every tool
invocation.

## Notes from running it

- **The dataset moved on: "Turkey" is now "Türkiye".** `get_country_details("Turkey")`
  returns a clean *no match* error. The realistic flow is `search_countries` first, then
  details with the name the server just returned — which is exactly how an agent should
  handle stale assumptions.
- **Diacritics matter.** Searching `"turk"` does not match `"Türkiye"`; `"türk"` does.
- **Flag files are lowercase.** The dataset serves `data/tur.svg`; `data/TUR.svg` is a 404,
  so the tool lowercases the `cca3` only for the flag URL. The no-match hint tells the agent
  to try a different spelling or the `cca3` code, because "search a shorter query" does not
  get you from `"Turkey"` to `"Türkiye"`.
- `population` is absent from the upstream records and is therefore omitted, not returned
  as `null`.

## Files

```
server.py                    MCP server: both tools, caching, error handling
test_server.py               33 offline tests (fixtures + faked transport)
scripts/mcp_client_demo.py   stdio client that prints a transcript
writeup.md                   transcript, error handling, what I learned
```
