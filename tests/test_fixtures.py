from __future__ import annotations

import asyncio
import json

import pytest

from mcp_servers import fixtures, library_tools, weather_tools


@pytest.fixture(autouse=True)
def fixture_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES_DIR", str(tmp_path / "fixtures"))
    monkeypatch.delenv("EMBODIED_AGENT_FIXTURES", raising=False)
    return tmp_path / "fixtures"


def test_off_by_default_calls_the_real_service():
    calls = []

    result = fixtures.cached_json("weather", "key", lambda: calls.append(1) or {"a": 1})

    assert result == {"a": 1}
    assert calls == [1]


def test_record_then_replay_returns_the_same_payload(monkeypatch, fixture_dir):
    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "record")
    fixtures.cached_json("weather", "madison", lambda: {"temperature": 21})
    assert (fixture_dir / "weather" / "madison.json").exists()

    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "replay")

    def must_not_run():
        raise AssertionError("replay must not touch the network")

    assert fixtures.cached_json("weather", "madison", must_not_run) == {
        "temperature": 21
    }


def test_replay_without_a_recording_fails_as_a_tool_error(monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "replay")

    with pytest.raises(fixtures.FixtureMissingError) as caught:
        fixtures.cached_json("weather", "nowhere", dict)

    # Tools already convert ValueError into an "Error: ..." reply, so a missing
    # recording surfaces as a normal tool failure rather than a crash.
    assert isinstance(caught.value, ValueError)
    assert "record" in str(caught.value)


def test_an_unknown_mode_falls_back_to_live_calls(monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "sideways")

    assert fixtures.mode() == fixtures.MODE_OFF


def test_keys_stay_readable_and_unique():
    short = fixtures.fixture_key("geocode", "name=madison")
    assert short == "geocode-name-madison"

    long_a = fixtures.fixture_key("search", "q=" + "a" * 200)
    long_b = fixtures.fixture_key("search", "q=" + "b" * 200)
    assert len(long_a) <= 90
    assert long_a.startswith("search-q-")
    assert long_a != long_b

    # Path separators and traversal cannot escape the fixtures directory.
    assert "/" not in fixtures.fixture_key("../../etc/passwd")
    assert ".." not in fixtures.fixture_key("../../etc/passwd").strip("-")


def test_weather_replays_a_recorded_forecast(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "record")
    monkeypatch.setattr(
        weather_tools,
        "_request_json",
        lambda url, params: (
            {"results": [{"name": "Madison", "latitude": 43.1, "longitude": -89.4}]}
            if url == weather_tools._GEOCODE_URL
            else {
                "current": {
                    "time": "2026-07-25T20:30",
                    "weather_code": 2,
                    "temperature_2m": 21.0,
                    "apparent_temperature": 22.0,
                    "relative_humidity_2m": 60,
                    "wind_speed_10m": 5.0,
                    "is_day": 1,
                }
            }
        ),
    )
    recorded = asyncio.run(weather_tools.get_current_weather("Madison"))
    assert "Madison" in recorded

    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "replay")
    monkeypatch.setattr(
        weather_tools,
        "_request_json",
        lambda *_args, **_kwargs: pytest.fail("replay must not call Open-Meteo"),
    )

    assert asyncio.run(weather_tools.get_current_weather("Madison")) == recorded


def test_library_replays_a_recorded_search(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "record")
    monkeypatch.setattr(
        library_tools,
        "_request_json",
        lambda url, params: (
            {"docs": [{"title": "Frankenstein", "author_name": ["Mary Shelley"]}]}
            if url == library_tools._OPEN_LIBRARY_URL
            else {
                "results": [
                    {
                        "id": 84,
                        "title": "Frankenstein; Or, The Modern Prometheus",
                        "formats": {
                            "text/plain; charset=utf-8": "https://www.gutenberg.org/ebooks/84.txt.utf-8"
                        },
                    }
                ]
            }
        ),
    )
    recorded = json.loads(asyncio.run(library_tools.list_books("frankenstein")))
    assert recorded["books"][0]["readable"] is True

    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "replay")
    monkeypatch.setattr(
        library_tools,
        "_request_json",
        lambda *_args, **_kwargs: pytest.fail("replay must not call the book services"),
    )

    assert json.loads(asyncio.run(library_tools.list_books("frankenstein"))) == recorded


def test_book_titles_match_across_services_despite_subtitles():
    """Open Library drops the subtitle Gutendex keeps, and vice versa."""
    assert library_tools._title_keys("Frankenstein; Or, The Modern Prometheus") == [
        "frankenstein or the modern prometheus",
        "frankenstein",
    ]
    assert library_tools._title_keys("Frankenstein") == ["frankenstein"]
    # A different book that merely starts the same must not be matched.
    assert "frankenstein" not in library_tools._title_keys("Frankenstein in Baghdad")


def test_recorded_book_text_is_replayed_without_downloading(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "record")
    url = "https://www.gutenberg.org/ebooks/84.txt.utf-8"
    monkeypatch.setattr(library_tools, "_request_text", lambda _url: "Chapter one.")

    assert library_tools._download_text(url) == "Chapter one."

    monkeypatch.setenv("EMBODIED_AGENT_FIXTURES", "replay")
    monkeypatch.setattr(
        library_tools,
        "_request_text",
        lambda _url: pytest.fail("replay must not download the book"),
    )

    assert library_tools._download_text(url) == "Chapter one."
