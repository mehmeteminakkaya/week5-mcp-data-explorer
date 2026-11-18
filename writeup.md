# Week 5 writeup — Data Explorer MCP server

**Assignment:** Advanced Context Management — build a custom MCP server.
**Server:** `server.py`, `FastMCP`, STDIO transport, two tools.
**Verification:** `pytest` → 33 tests pass, offline. Transcripts below are copy-pasted
from real runs, not hand-written.

## How the transcript was produced

Claude Desktop is a GUI; to keep the transcript reproducible I drove the same protocol from
a script. `scripts/mcp_client_demo.py` starts `server.py` over STDIO with the official MCP
client, initializes the session, lists the tools and calls them:

```bash
python scripts/mcp_client_demo.py           # happy path + bad input
python scripts/mcp_client_demo.py --errors  # upstream down
```

What the client prints is byte-for-byte what an MCP-aware agent receives as tool output.

## Transcript — both tools working

`server` and `tool` lines are the discovery handshake; the `> ` lines are tool calls.

```
### Happy-path session

server : data-explorer 1.30.0
tool   : search_countries(query: string, limit: integer)
tool   : get_country_details(name: string)

> search_countries({"query": "turk", "limit": 3})
  isError=False
      [
        {"name": "Turkmenistan", "capital": "Ashgabat", "region": "Asia",
         "subregion": "Central Asia", "cca3": "TKM"},
        {"name": "Turks and Caicos Islands", "capital": "Cockburn Town", "region": "Americas",
         "subregion": "Caribbean", "cca3": "TCA"}
      ]

> search_countries({"query": "   "})
  isError=False
      {"error": "query must be a non-empty string, e.g. 'nige'"}

> search_countries({"query": "atlantis"})
  isError=False
      []

> get_country_details({"name": "Türkiye"})
  isError=False
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
          {"cca3": "ARM", "name": "Armenia"}, {"cca3": "AZE", "name": "Azerbaijan"},
          {"cca3": "BGR", "name": "Bulgaria"}, {"cca3": "GEO", "name": "Georgia"},
          {"cca3": "GRC", "name": "Greece"}, {"cca3": "IRN", "name": "Iran"},
          {"cca3": "IRQ", "name": "Iraq"}, {"cca3": "SYR", "name": "Syria"}
        ],
        "flag_url": "https://raw.githubusercontent.com/mledoze/countries/master/data/TUR.svg",
        "area": 783562
      }

> get_country_details({"name": "TUR"})          # cca3 works too; same payload as above
  isError=False

> get_country_details({"name": "nige"})
  isError=False
      {
        "error": "'nige' is ambiguous: 2 countries match",
        "candidates": [{"name": "Niger", "cca3": "NER"}, {"name": "Nigeria", "cca3": "NGA"}],
        "hint": "call again with the exact name or the cca3 code"
      }

> get_country_details({"name": "Atlantis"})
  isError=False
      {
        "error": "no country matched 'Atlantis'",
        "hint": "call search_countries with a shorter query to find the exact name"
      }
```

The full untrimmed output is reproducible with the command above.

### What the agent does with it

Two calls, two different jobs. `search_countries` is the *cheap* call: five fields, many
rows, short strings — safe to put in context while the agent scans. `get_country_details`
is the *expensive* call: it is only worth its tokens once the agent knows exactly which
country it means. The ambiguity response is what glues them together: instead of guessing
between Niger and Nigeria, the server hands back both candidates and a hint, and the agent
picks one and retries. That is context control in the literal sense — the server decides
how much of a country record enters the model's window, and when.

## Transcript — the upstream is down

Second run with `DATA_EXPLORER_DATA_URL=http://127.0.0.1:9/countries.json` (the discard
port refuses connections, so this is a genuine transport failure, not a mock):

```
### Error-mode session (DATA_EXPLORER_DATA_URL -> 127.0.0.1:9)

server : data-explorer 1.30.0
tool   : search_countries(query: string, limit: integer)
tool   : get_country_details(name: string)

> search_countries({"query": "nige"})
  isError=False
      {"error": "country dataset request failed (ConnectError); check connectivity"}

> get_country_details({"name": "Turkey"})
  isError=False
      {"error": "country dataset request failed (ConnectError); check connectivity"}
```

Note `isError=False`: the tool *succeeded* in explaining the problem. The MCP error channel
is reserved for protocol failures; a domain failure comes back as data the model can read
and act on ("try again", "check connectivity") instead of an exception string.

## How errors are handled

Every tool returns a dict (or list of dicts) and never raises. The dataset load is the only
fallible step, and it is funnelled through one helper that converts each exception class
into a message with a next action:

| Failure | Caught as | Returned |
| --- | --- | --- |
| Timeout (15 s) | `httpx.TimeoutException` | `"...timed out after 15s; try again in a moment"` |
| HTTP 4xx/5xx | `httpx.HTTPStatusError` | `"...failed with HTTP 503"` |
| DNS/refused/TLS | `httpx.HTTPError` | `"...failed (ConnectError); check connectivity"` |
| Garbage payload | `json.JSONDecodeError` / `ValueError` | `"...could not be parsed: ..."` |
| Wrong shape (not a list) | `ValueError` (explicit check) | `"...could not be parsed: ..."` |

Input validation happens before any I/O: empty or non-string `query`/`name`, non-integer or
out-of-range `limit` (1–25) all return immediately. No matches is deliberately *not* an
error for search (`[]`), while detail lookup returns an error plus a hint, because a missing
detail usually means the agent's name assumption was wrong.

Coverage: `test_server.py` has 33 offline tests — fixture data for the happy paths and a
faked `httpx.get` for each failure branch — including a test that the dataset is fetched
exactly once for three calls (the cache), and one that a non-list payload is rejected
rather than iterated over.

## What I learned

An MCP tool is not an API wrapper; it is a **context contract**. The same data served three
ways — one row per match, one record per detail, one error with candidates — changes how
many tokens the agent spends and how often it guesses. Returning `{"error": ...}` with a
next action instead of raising kept the agent's loop unbroken: in the down-upstream run the
model can say "the country service is unreachable" and stop, rather than retrying a tool
that always throws. The most instructive moment was the dataset itself: `"Turkey"` no longer
matches, because the upstream renamed it `"Türkiye"`. The tool could not fix that for the
agent — but it could say so precisely and point at `search_countries`, which is exactly the
kind of nudge that keeps a stale assumption from silently poisoning the session.
