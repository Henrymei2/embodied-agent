from __future__ import annotations

import json
import urllib.parse

import pytest

from mcp_servers import weather_tools


@pytest.fixture
def fake_open_meteo(monkeypatch):
    """Capture outgoing requests and serve canned Open-Meteo payloads."""
    calls: list[tuple[str, dict[str, str]]] = []
    responses: dict[str, dict] = {
        weather_tools._GEOCODE_URL: {
            "results": [
                {
                    "name": "Madison",
                    "admin1": "Wisconsin",
                    "country": "United States",
                    "latitude": 43.07,
                    "longitude": -89.4,
                }
            ]
        },
        weather_tools._FORECAST_URL: {
            "current": {
                "time": "2026-07-21T09:00",
                "temperature_2m": 74.1,
                "apparent_temperature": 75.9,
                "relative_humidity_2m": 61,
                "wind_speed_10m": 6.2,
                "weather_code": 2,
                "is_day": 1,
            },
            "daily": {
                "time": ["2026-07-21", "2026-07-22"],
                "weather_code": [61, 0],
                "temperature_2m_max": [79.0, 82.4],
                "temperature_2m_min": [61.2, 63.1],
                "precipitation_probability_max": [55, 5],
            },
        },
    }

    def fake_fetch(url: str, params: dict) -> dict:
        calls.append((url, {key: str(value) for key, value in params.items()}))
        return responses[url]

    monkeypatch.setattr(weather_tools, "_fetch_json_sync", fake_fetch)
    return calls


async def test_current_weather_reads_named_place(fake_open_meteo):
    result = json.loads(await weather_tools.get_current_weather("Madison, Wisconsin"))

    assert result["place"] == "Madison, Wisconsin, United States"
    assert result["conditions"] == "partly cloudy"
    assert result["temperature"] == "74.1°F"
    assert result["wind"] == "6.2 mph"
    assert result["is_daylight"] is True


async def test_location_is_sent_as_an_encoded_parameter(fake_open_meteo):
    await weather_tools.get_current_weather("Zürich/../etc")

    geocode_url, params = fake_open_meteo[0]
    assert geocode_url == weather_tools._GEOCODE_URL
    assert params["name"] == "Zürich/../etc"

    # The place is escaped into the query string, so it can never alter the
    # host or path of the request.
    url = f"{geocode_url}?{urllib.parse.urlencode(params)}"
    assert "Z%C3%BCrich%2F..%2Fetc" in url
    assert urllib.parse.urlsplit(url).path == "/v1/search"


async def test_region_qualifier_picks_between_same_named_places(monkeypatch):
    """ "Zurich, Montana" must not resolve to Zurich, Switzerland."""
    calls: list[dict] = []
    candidates = [
        {
            "name": "Zurich",
            "admin1": "Canton of Zurich",
            "country": "Switzerland",
            "latitude": 47.4,
            "longitude": 8.5,
        },
        {
            "name": "Zurich",
            "admin1": "Montana",
            "country": "United States",
            "latitude": 48.6,
            "longitude": -108.1,
        },
    ]

    def fake_fetch(url: str, params: dict) -> dict:
        calls.append(params)
        if url == weather_tools._GEOCODE_URL:
            return {"results": candidates}
        return {"current": {"temperature_2m": 60.0, "weather_code": 0, "is_day": 1}}

    monkeypatch.setattr(weather_tools, "_fetch_json_sync", fake_fetch)
    result = json.loads(await weather_tools.get_current_weather("Zurich, Montana"))

    # Only the bare place name is searched; the qualifier filters the results.
    assert calls[0]["name"] == "Zurich"
    assert calls[0]["count"] > 1
    assert result["place"] == "Zurich, Montana, United States"
    assert calls[1]["latitude"] == 48.6


async def test_unmatched_qualifier_is_reported_rather_than_guessed(monkeypatch):
    monkeypatch.setattr(
        weather_tools,
        "_fetch_json_sync",
        lambda url, params: {
            "results": [
                {
                    "name": "Madison",
                    "admin1": "Wisconsin",
                    "country": "United States",
                    "latitude": 43.07,
                    "longitude": -89.4,
                }
            ]
        },
    )

    assert await weather_tools.get_current_weather("Madison, Atlantis") == (
        "Error: no place matching 'Madison, Atlantis' was found"
    )


async def test_coordinates_skip_the_geocoder(fake_open_meteo):
    result = json.loads(await weather_tools.get_current_weather("43.07,-89.40"))

    assert result["place"] == "43.07, -89.4"
    assert [url for url, _ in fake_open_meteo] == [weather_tools._FORECAST_URL]


async def test_celsius_switches_both_units(fake_open_meteo):
    result = json.loads(
        await weather_tools.get_current_weather("43.07,-89.40", units="celsius")
    )

    _url, params = fake_open_meteo[0]
    assert params["temperature_unit"] == "celsius"
    assert params["wind_speed_unit"] == "kmh"
    assert result["temperature"].endswith("°C")


async def test_forecast_returns_requested_days(fake_open_meteo):
    result = json.loads(
        await weather_tools.get_weather_forecast("43.07,-89.40", days=2)
    )

    assert [day["date"] for day in result["forecast"]] == ["2026-07-21", "2026-07-22"]
    assert result["forecast"][0]["conditions"] == "light rain"
    assert result["forecast"][0]["high"] == "79.0°F"
    assert result["forecast"][0]["precipitation_chance_percent"] == 55


async def test_invalid_arguments_are_rejected(fake_open_meteo):
    assert await weather_tools.get_current_weather("  ") == (
        "Error: location cannot be empty"
    )
    assert await weather_tools.get_current_weather("x" * 200) == (
        "Error: location cannot exceed 120 characters"
    )
    assert await weather_tools.get_current_weather("43.07,-89.40", units="kelvin") == (
        "Error: units must be 'fahrenheit' or 'celsius'"
    )
    assert await weather_tools.get_weather_forecast("43.07,-89.40", days=30) == (
        "Error: days must be between 1 and 7"
    )
    assert await weather_tools.get_current_weather("91.0,0.0") == (
        "Error: latitude must be -90..90 and longitude -180..180"
    )
    assert fake_open_meteo == []


async def test_unknown_place_reports_cleanly(monkeypatch):
    monkeypatch.setattr(
        weather_tools,
        "_fetch_json_sync",
        lambda url, params: {"results": []},
    )

    assert await weather_tools.get_current_weather("Atlantis") == (
        "Error: no place matching 'Atlantis' was found"
    )


async def test_oversized_response_is_refused(monkeypatch):
    class FakeResponse:
        def read(self, size):
            return b"x" * size

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        weather_tools.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(),
    )

    assert await weather_tools.get_current_weather("43.07,-89.40") == (
        "Error: weather service response was too large"
    )
