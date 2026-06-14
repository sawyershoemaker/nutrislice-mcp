# nutrislice-mcp

an incredibly lightweight and hyperoptimized mcp server for querying Nutrislice school menus and nutritional info. the goal of this is to be linked with an ai assistant that can find either a specific food somewhere on campus, help plan meals, or help find meals that fit within macros.

python 3.11+ is required. dependencies: `fastmcp`, `uvicorn`, `httpx[http2]`, `cachetools`, `orjson`

# setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

by default, the server listens on 0.0.0.0 at port 8000 but can be changed through env vars

```bash
HOST=127.0.0.1 PORT=9000 python server.py
```

the mcp endpoint is available at `http://<host>:<port>/mcp/`

```json
{
  "mcpServers": {
    "nutrislice": {
      "transport": { "type": "streamable-http", "url": "http://localhost:8000/mcp/" }
    }
  }
}
```

# undocumented endpoint (yay)

school nutrislice links follow this pattern:
```https://{district}.nutrislice.com/menu/{school}```

however, UofSC, for example, uses sc as their {disctrict} and {school} is the location of food spots on-campus. 

once you have `district`, the agent can utilize `list_location` which uses an undocumented endpoint i discovered to see all school slugs.

# tools

### `list_locations(district)`

returns every dining location in the district with its `slug` and available `menu_types`.

```json
[
  { "name": "Garnet Station", "slug": "garnet-station", "menu_types": ["breakfast", "lunch", "dinner", "all-day"], "address": "..." },
  ...
]
```

### `find_item(district, date, item_name)`

searches every location and meal type in the district for items matching `item_name` (case-insensitive partial match) on the given day. fear not, optimization fans, for it fans out in parallel with `asyncio.gather` and all fetched data is cached, so repeat calls on the same day are free!

### `get_weekly_menu(district, school, menu_type, date)`

returns the full week of menu items (name, category, icons) for one location. `date` can be any day within the target week. nutrition detail is excluded, use `get_item_nutrition` for that.

### `get_daily_menu(district, school, menu_type, date)`

same as above but filtered to a single day.

### `get_item_nutrition(district, school, menu_type, date, item_name)`

returns the full nutrition breakdown for the first item whose name contains `item_name` (case-insensitive partial match). fear not, this also reuses cached week data, so there's no extra HTTP request if the week is already loaded. (yay!)