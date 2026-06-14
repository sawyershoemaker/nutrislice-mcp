from __future__ import annotations
import os
from contextlib import asynccontextmanager
from datetime import date as _Date
from typing import Annotated, Any
from fastmcp import FastMCP
from pydantic import Field
import client as _nutrislice

@asynccontextmanager
async def _lifespan(app: Any):
    yield
    await _nutrislice.close()

mcp = FastMCP(
    "nutrislice",
    lifespan=_lifespan,
    instructions=(
        "provides access to Nutrislice school and campus dining menus. "
        "recommended workflow: "
        "1. call list_locations(district) first if you don't know the school slug, it returns every dining location and the meal types each serves. "
        "2. use find_item to search all locations at once when the user asks where to find a specific food. "
        "3. use get_daily_menu or get_weekly_menu to browse a specific location's offerings. "
        "4. use get_item_nutrition only when the user explicitly wants calories, macros, or ingredient detail. "
        "the district slug is the subdomain of the school's Nutrislice URL. https://{district}.nutrislice.com. University of South Carolina uses district 'sc'."
    ),
)


def _parse_date(date_str: str) -> _Date:
    try:
        return _Date.fromisoformat(date_str)
    except ValueError as exc:
        raise ValueError(
            f"not a valid date '{date_str}'. expected ISO format YYYY-MM-DD."
        ) from exc


_LISTING_OMIT = frozenset({"nutrition_info", "ingredients", "serving_size_amount", "serving_size_unit"})


def _listing_item(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in _LISTING_OMIT}


def _clean_nutrition(info: Any) -> dict[str, Any]:
    if not isinstance(info, dict):
        return {}
    return {k: v for k, v in info.items() if v is not None and v != ""}

@mcp.tool()
async def list_locations(
    district: Annotated[str, Field(description="Nutrislice district slug (the subdomain of https://{district}.nutrislice.com). Example: 'sc' for University of South Carolina.")],
) -> list[dict[str, Any]]:
    """
    returns every dining location in a district with its slug and available meal types.
    ALWAYS call this first when the school slug is unknown.
    each result has: name, slug, menu_types (list of slugs), address.
    """
    return await _nutrislice.list_schools(district)


@mcp.tool()
async def find_item(
    district: Annotated[str, Field(description="Nutrislice district slug. example: 'sc'.")],
    date: Annotated[str, Field(description="date to search in YYYY-MM-DD format.")],
    item_name: Annotated[str, Field(description="partial or full food name, case-insensitive. 'pizza' matches 'Classic Cheese Pizza'.")],
) -> list[dict[str, Any]]:
    """
    searches every dining location and meal type in the district for a food item on a given day.
    use this when the user asks where to find something (e.g. 'where can I get pizza today?').
    returns a list of matches, each with: location (slug), menu_type, item (name, food_category, icons).
    returns an empty list if nothing matches.
    """
    return await _nutrislice.search_all_locations(district, _parse_date(date), item_name)


@mcp.tool()
async def get_weekly_menu(
    district: Annotated[str, Field(description="Nutrislice district slug. Example: 'sc'.")],
    school: Annotated[str, Field(description="dining location slug from list_locations(). example: 'garnet-station'.")],
    menu_type: Annotated[str, Field(description="meal type slug from list_locations(). common values: 'breakfast', 'lunch', 'dinner', 'all-day'.")],
    date: Annotated[str, Field(description="any date within the target week in YYYY-MM-DD format.")],
) -> list[dict[str, Any]]:
    """
    returns the full week of menu items for one dining location and meal type.
    use this for 'what's on the menu this week?' queries.
    returns a list of day objects ({date, items}), omitting days with no published menu.
    each item has: name, food_category, description, icons. nutrition detail is excluded, use get_item_nutrition for that.
    """
    days = await _nutrislice.fetch_week(district, school, menu_type, _parse_date(date))
    return [
        {"date": day["date"], "items": [_listing_item(i) for i in day["items"]]}
        for day in days if day["items"]
    ]


@mcp.tool()
async def get_daily_menu(
    district: Annotated[str, Field(description="Nutrislice district slug. Example: 'sc'.")],
    school: Annotated[str, Field(description="dining location slug from list_locations(). example: 'garnet-station'.")],
    menu_type: Annotated[str, Field(description="meal type slug from list_locations(). common values: 'breakfast', 'lunch', 'dinner', 'all-day'.")],
    date: Annotated[str, Field(description="the specific date to fetch in YYYY-MM-DD format.")],
) -> list[dict[str, Any]]:
    """
    returns the menu items for a single day at one dining location.
    use this for 'what's for lunch today?' queries.
    returns a flat list of items (name, food_category, description, icons). nutrition detail excluded.
    returns an empty list if no menu is published for that day.
    """
    parsed = _parse_date(date)
    date_iso = parsed.isoformat()
    days = await _nutrislice.fetch_week(district, school, menu_type, parsed)
    for day in days:
        if day["date"] == date_iso:
            return [_listing_item(i) for i in day["items"]]
    return []


@mcp.tool()
async def get_item_nutrition(
    district: Annotated[str, Field(description="Nutrislice district slug. example: 'sc'.")],
    school: Annotated[str, Field(description="dining location slug from list_locations(). example: 'garnet-station'.")],
    menu_type: Annotated[str, Field(description="meal type slug from list_locations(). common values: 'breakfast', 'lunch', 'dinner', 'all-day'.")],
    date: Annotated[str, Field(description="the date the item is served in YYYY-MM-DD format.")],
    item_name: Annotated[str, Field(description="partial or full food name, case-insensitive. matches the first item whose name contains this string.")],
) -> dict[str, Any] | None:
    """
    returns full nutritional detail for a specific menu item.
    use this only when the user explicitly asks about calories, macros, allergens, or ingredients.
    returns: name, nutrition_info (calories, fat, protein, etc. with zero/null fields stripped),
    serving_size_amount, serving_size_unit, ingredients, icons.
    returns null if no item matching item_name is found on that day.
    """
    parsed = _parse_date(date)
    date_iso = parsed.isoformat()
    days = await _nutrislice.fetch_week(district, school, menu_type, parsed)
    needle = item_name.lower()
    for day in days:
        if day["date"] == date_iso:
            for item in day["items"]:
                if needle in (item.get("name") or "").lower():
                    result = {
                        "name": item.get("name"),
                        "nutrition_info": _clean_nutrition(item.get("nutrition_info")),
                        "serving_size_amount": item.get("serving_size_amount"),
                        "serving_size_unit": item.get("serving_size_unit"),
                        "ingredients": item.get("ingredients"),
                        "icons": item.get("icons", []),
                    }
                    return {k: v for k, v in result.items() if v is not None}
    return None


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    mcp.run(transport="streamable-http", host=host, port=port)
