"""Weather tools for the embodied companion.

These tools belong to the `window` object in the room: the agent has to walk to
the window to look outside.

Data comes from Open-Meteo, a public API that needs no account or key. Only the
two fixed Open-Meteo hosts are ever contacted, and a caller-supplied location is
passed as an encoded query parameter — never as part of the host or path.

Run standalone for a quick sanity check:
    uv run python mcp_servers/weather_tools.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from mcp_servers.fixtures import cached_json, fixture_key

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT_SECONDS = 10.0
_MAX_RESPONSE_BYTES = 512 * 1024
_MAX_LOCATION = 120
_MAX_FORECAST_DAYS = 7
# Candidates to weigh when the caller narrowed a place name by region.
_GEOCODE_CANDIDATES = 10
_LATLON_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

# WMO weather interpretation codes, as returned by Open-Meteo.
_WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "light snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with light hail",
    99: "thunderstorm with heavy hail",
}

mcp = FastMCP("weather")


def _describe_code(code: Any) -> str:
    return _WEATHER_CODES.get(code, f"unknown conditions (code {code})")


def _fetch_json_sync(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch from Open-Meteo, or from a recording when the demo needs it to."""
    endpoint = "geocode" if url == _GEOCODE_URL else "forecast"
    key = fixture_key(
        endpoint, *(f"{name}={value}" for name, value in sorted(params.items()))
    )
    return cached_json("weather", key, lambda: _request_json(url, params))


def _request_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            # Read one byte past the cap so an oversized body is rejected rather
            # than silently truncated into unparseable JSON.
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"weather service returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"could not reach the weather service: {exc}") from exc

    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("weather service response was too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("weather service returned malformed data") from exc
    if not isinstance(payload, dict):
        raise ValueError("weather service returned an unexpected payload")
    return payload


async def _fetch_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(_fetch_json_sync, url, params)


def _validate_location(location: str) -> str:
    if not isinstance(location, str):
        raise ValueError("location must be a string")
    cleaned = location.strip()
    if not cleaned:
        raise ValueError("location cannot be empty")
    if len(cleaned) > _MAX_LOCATION:
        raise ValueError(f"location cannot exceed {_MAX_LOCATION} characters")
    if any(character < " " for character in cleaned):
        raise ValueError("location cannot contain control characters")
    return cleaned


def _validate_units(units: str) -> tuple[str, str, str, str]:
    """Return (temperature_unit, wind_speed_unit, degree_label, speed_label)."""
    normalized = units.strip().lower() if isinstance(units, str) else ""
    if normalized == "celsius":
        return "celsius", "kmh", "°C", "km/h"
    if normalized == "fahrenheit":
        return "fahrenheit", "mph", "°F", "mph"
    raise ValueError("units must be 'fahrenheit' or 'celsius'")


def _match_place(results: list[Any], qualifiers: list[str]) -> dict[str, Any] | None:
    """Pick the candidate matching every region qualifier the caller gave."""
    wanted = [qualifier.casefold() for qualifier in qualifiers if qualifier]
    places = [place for place in results if isinstance(place, dict)]
    if not places:
        return None
    if not wanted:
        return places[0]

    for place in places:
        haystack = " ".join(
            str(place.get(key, ""))
            for key in ("admin1", "admin2", "country", "country_code")
        ).casefold()
        if all(term in haystack for term in wanted):
            return place
    return None


async def _resolve_location(location: str) -> dict[str, Any]:
    """Resolve a place name or "lat,lon" pair to coordinates."""
    coordinates = _LATLON_RE.match(location)
    if coordinates:
        latitude = float(coordinates.group(1))
        longitude = float(coordinates.group(2))
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("latitude must be -90..90 and longitude -180..180")
        return {
            "name": f"{latitude:g}, {longitude:g}",
            "latitude": latitude,
            "longitude": longitude,
        }

    # The geocoder only matches a bare place name: searching for
    # "Madison, Wisconsin" returns nothing at all. Split the qualified form and
    # use everything after the first comma to choose between the candidates,
    # which is also what keeps "Zurich, Montana" from landing in Switzerland.
    name, *qualifiers = (part.strip() for part in location.split(","))
    if not name:
        raise ValueError("location must start with a place name")

    payload = await _fetch_json(
        _GEOCODE_URL,
        {
            "name": name,
            "count": _GEOCODE_CANDIDATES if qualifiers else 1,
            "language": "en",
            "format": "json",
        },
    )
    results = payload.get("results")
    place = _match_place(results, qualifiers) if isinstance(results, list) else None
    if place is None:
        raise ValueError(f"no place matching '{location}' was found")

    name_parts = [place.get("name"), place.get("admin1"), place.get("country")]
    return {
        "name": ", ".join(part for part in name_parts if part),
        "latitude": place.get("latitude"),
        "longitude": place.get("longitude"),
    }


@mcp.tool()
async def get_current_weather(location: str, units: str = "fahrenheit") -> str:
    """Look out of the window: current conditions for a place.

    Requires standing near the window.

    Args:
        location: A place name such as "Madison, Wisconsin", or a
            "latitude,longitude" pair such as "43.07,-89.40".
        units: "fahrenheit" (default) or "celsius".
    """
    try:
        place = await _resolve_location(_validate_location(location))
        temperature_unit, wind_unit, degrees, speed = _validate_units(units)
        payload = await _fetch_json(
            _FORECAST_URL,
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": (
                    "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation,wind_speed_10m,weather_code,is_day"
                ),
                "temperature_unit": temperature_unit,
                "wind_speed_unit": wind_unit,
                "timezone": "auto",
            },
        )
        current = payload.get("current")
        if not isinstance(current, dict):
            raise ValueError("weather service returned no current conditions")

        return json.dumps(
            {
                "place": place["name"],
                "observed_at": current.get("time"),
                "conditions": _describe_code(current.get("weather_code")),
                "temperature": f"{current.get('temperature_2m')}{degrees}",
                "feels_like": f"{current.get('apparent_temperature')}{degrees}",
                "humidity_percent": current.get("relative_humidity_2m"),
                "wind": f"{current.get('wind_speed_10m')} {speed}",
                "is_daylight": bool(current.get("is_day")),
            },
            ensure_ascii=False,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
async def get_weather_forecast(
    location: str,
    days: int = 3,
    units: str = "fahrenheit",
) -> str:
    """Daily high, low, and rain chance for the next few days.

    Requires standing near the window.

    Args:
        location: A place name, or a "latitude,longitude" pair.
        days: How many days to return, 1 to 7 (default 3, starting today).
        units: "fahrenheit" (default) or "celsius".
    """
    try:
        if isinstance(days, bool) or not isinstance(days, int):
            raise ValueError("days must be an integer")
        if not 1 <= days <= _MAX_FORECAST_DAYS:
            raise ValueError(f"days must be between 1 and {_MAX_FORECAST_DAYS}")

        place = await _resolve_location(_validate_location(location))
        temperature_unit, wind_unit, degrees, _speed = _validate_units(units)
        payload = await _fetch_json(
            _FORECAST_URL,
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max"
                ),
                "forecast_days": days,
                "temperature_unit": temperature_unit,
                "wind_speed_unit": wind_unit,
                "timezone": "auto",
            },
        )
        daily = payload.get("daily")
        if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
            raise ValueError("weather service returned no forecast")

        def _at(key: str, index: int) -> Any:
            values = daily.get(key)
            if isinstance(values, list) and index < len(values):
                return values[index]
            return None

        forecast = [
            {
                "date": date,
                "conditions": _describe_code(_at("weather_code", index)),
                "high": f"{_at('temperature_2m_max', index)}{degrees}",
                "low": f"{_at('temperature_2m_min', index)}{degrees}",
                "precipitation_chance_percent": _at(
                    "precipitation_probability_max", index
                ),
            }
            for index, date in enumerate(daily["time"][:days])
        ]
        return json.dumps(
            {"place": place["name"], "forecast": forecast},
            ensure_ascii=False,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
