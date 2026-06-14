from __future__ import annotations
import asyncio
from datetime import date as _Date, timedelta
from typing import Any
import httpx
import orjson
from cachetools import TTLCache

_FOOD_FIELDS = frozenset({
    "name",
    "description",
    "food_category",
    "serving_size_amount",
    "serving_size_unit",
    "nutrition_info",
    "ingredients",
})

_cache: TTLCache = TTLCache(maxsize=128, ttl=14_400)
_schools_cache: TTLCache = TTLCache(maxsize=32, ttl=86_400)
_inflight: dict[tuple, asyncio.Task] = {}
_http: httpx.AsyncClient | None = None

def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            limits=httpx.Limits(max_keepalive_connections=30, max_connections=60),
            headers={"Accept": "application/json"},
            follow_redirects=True,
            http2=True,
        )
    return _http

async def close() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None

def _week_monday(d: _Date) -> _Date:
    return d - timedelta(days=d.weekday())

def _build_url(district: str, school: str, menu_type: str, d: _Date) -> str:
    return (
        f"https://{district}.api.nutrislice.com/menu/api/weeks"
        f"/school/{school}/menu-type/{menu_type}"
        f"/{d.year}/{d.month:02d}/{d.day:02d}/"
    )

def _slim_food(food: dict[str, Any]) -> dict[str, Any]:
    slim = {k: v for k in _FOOD_FIELDS if (v := food.get(k)) not in (None, "")}
    raw_icons = food.get("icons")
    if isinstance(raw_icons, list):
        slim["icons"] = [ic["name"] for ic in raw_icons if ic.get("name")]
    return slim

def _slim_day(day: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in day.get("menu_items", []):
        if item.get("is_section_title") or not item.get("food"):
            continue
        slimmed = _slim_food(item["food"])
        if slimmed.get("name"):
            items.append(slimmed)
    return {"date": day["date"], "items": items}

async def _do_fetch(url: str, cache_key: tuple) -> list[dict[str, Any]]:
    try:
        response = await _get_http().get(url)
        response.raise_for_status()
        data: dict[str, Any] = orjson.loads(response.content)
        slim_days = [_slim_day(d) for d in data.get("days", [])]
        _cache[cache_key] = slim_days
        return slim_days
    except Exception:
        _cache.pop(cache_key, None)
        raise

async def fetch_week(
    district: str,
    school: str,
    menu_type: str,
    for_date: _Date,
) -> list[dict[str, Any]]:
    monday = _week_monday(for_date)
    cache_key = (district, school, menu_type, monday.isoformat())
    if cache_key in _cache:
        return _cache[cache_key]
    if cache_key in _inflight:
        return await _inflight[cache_key]
    task = asyncio.create_task(_do_fetch(_build_url(district, school, menu_type, for_date), cache_key))
    _inflight[cache_key] = task
    try:
        return await task
    finally:
        _inflight.pop(cache_key, None)


def _slim_school(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": s.get("name"),
        "slug": s.get("slug"),
        "menu_types": [mt["slug"] for mt in s.get("active_menu_types", []) if mt.get("slug")],
        "address": s.get("address") or None,
    }


async def list_schools(district: str) -> list[dict[str, Any]]:
    """fetch and cache the list of dining locations for a district"""
    if district in _schools_cache:
        return _schools_cache[district]

    url = f"https://{district}.api.nutrislice.com/menu/api/schools/"
    response = await _get_http().get(url)
    response.raise_for_status()
    data = orjson.loads(response.content)

    raw: list[dict[str, Any]] = data if isinstance(data, list) else data.get("value", [])
    schools = [_slim_school(s) for s in raw if s.get("slug")]
    _schools_cache[district] = schools
    return schools


async def search_all_locations(
    district: str,
    for_date: _Date,
    query: str,
) -> list[dict[str, Any]]:
    """search every location and meal type for items matching query (case-insensitive partial match). returns a list of match dicts: {location, menu_type, date, item}."""
    schools = await list_schools(district)
    needle = query.lower()
    date_iso = for_date.isoformat()

    async def _search_one(slug: str, menu_type: str) -> list[dict[str, Any]]:
        try:
            days = await fetch_week(district, slug, menu_type, for_date)
        except Exception:
            return []
        for day in days:
            if day["date"] == date_iso:
                return [
                    {"location": slug, "menu_type": menu_type, "item": item}
                    for item in day["items"]
                    if needle in (item.get("name") or "").lower()
                ]
        return []

    tasks = [
        _search_one(s["slug"], mt)
        for s in schools
        for mt in s.get("menu_types", [])
    ]
    results: list[list[dict[str, Any]]] = await asyncio.gather(*tasks)
    return [hit for group in results for hit in group]
