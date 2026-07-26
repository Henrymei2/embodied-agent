from __future__ import annotations

import json

import pytest

from mcp_servers import library_tools
from src.open_llm_vtuber import furniture_state


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    return tmp_path


def _shelve(title: str = "Frankenstein", source_id: int | None = 84) -> dict:
    payload = json.loads(
        library_tools.shelve_book(
            title, author="Mary Shelley", text_source_id=source_id
        )
    )
    return payload["book"]


async def test_list_books_annotates_readable_results(monkeypatch):
    def fetch(url, params):
        if url == library_tools._OPEN_LIBRARY_URL:
            assert params["q"] == "frankenstein"
            return {
                "docs": [
                    {
                        "title": "Frankenstein",
                        "author_name": ["Mary Shelley"],
                        "first_publish_year": 1818,
                    },
                    {"title": "A Modern Sequel", "author_name": ["Someone"]},
                ]
            }
        return {
            "results": [
                {
                    "id": 84,
                    "title": "Frankenstein; Or, The Modern Prometheus",
                    "formats": {
                        "text/plain; charset=us-ascii": "https://www.gutenberg.org/files/84/84-0.txt"
                    },
                }
            ]
        }

    monkeypatch.setattr(library_tools, "_fetch_json", fetch)

    payload = json.loads(await library_tools.list_books("frankenstein"))

    assert payload["count"] == 2
    # The two services disagree about the subtitle — Open Library lists
    # "Frankenstein" where Gutendex has "Frankenstein; Or, The Modern
    # Prometheus" — and the match has to survive that, or the best-known book
    # in the demo comes back unreadable.
    assert payload["books"][0]["readable"] is True
    assert payload["books"][0]["text_source_id"] == 84
    # An unrelated title must not be swept in by the same relaxation.
    assert payload["books"][1]["readable"] is False


async def test_list_books_matches_titles_across_services(monkeypatch):
    monkeypatch.setattr(
        library_tools,
        "_fetch_json",
        lambda url, params: (
            {"docs": [{"title": "The Time Machine", "author_name": ["H. G. Wells"]}]}
            if url == library_tools._OPEN_LIBRARY_URL
            else {
                "results": [
                    {
                        "id": 35,
                        "title": "The Time Machine",
                        "formats": {
                            "text/plain; charset=utf-8": "https://www.gutenberg.org/ebooks/35.txt.utf-8"
                        },
                    }
                ]
            }
        ),
    )

    payload = json.loads(await library_tools.list_books("time machine"))

    assert payload["books"][0]["readable"] is True
    assert payload["books"][0]["text_source_id"] == 35


async def test_list_books_still_browses_when_full_text_service_fails(monkeypatch):
    def fetch(url, _params):
        if url == library_tools._OPEN_LIBRARY_URL:
            return {"docs": [{"title": "Dune", "author_name": ["Frank Herbert"]}]}
        raise ValueError("gutendex is down")

    monkeypatch.setattr(library_tools, "_fetch_json", fetch)

    payload = json.loads(await library_tools.list_books("dune"))

    assert payload["books"][0]["title"] == "Dune"
    assert payload["books"][0]["readable"] is False


def test_shelving_rejects_duplicates_and_stores_the_book():
    book = _shelve()

    assert book["title"] == "Frankenstein"
    assert json.loads(library_tools.list_shelf())["count"] == 1
    assert "already on the shelf" in library_tools.shelve_book("frankenstein")


def test_remove_from_shelf_closes_an_open_session(monkeypatch):
    book = _shelve()
    library = furniture_state.load_library()
    library["sessions"].append(
        {
            "book_id": book["book_id"],
            "title": book["title"],
            "status": "reading",
            "offset": 10,
            "updated_at": "2026-07-25T00:00:00+00:00",
        }
    )
    furniture_state.save_library(library)

    library_tools.remove_from_shelf(book["book_id"])

    library = furniture_state.load_library()
    assert library["shelf"] == []
    assert library["sessions"][0]["status"] == "abandoned"


async def test_read_book_advances_through_the_text(monkeypatch, workspace):
    book = _shelve()
    text = "\n\n".join(f"Paragraph {index}. " + "word " * 400 for index in range(4))
    (workspace / "library" / "texts").mkdir(parents=True)
    (workspace / "library" / "texts" / "84.txt").write_text(text, encoding="utf-8")

    async def no_frontend(_action, _args):
        return True, None

    monkeypatch.setattr(library_tools, "post_frontend_action", no_frontend)

    first = json.loads(await library_tools.read_book(book["book_id"]))
    second = json.loads(await library_tools.read_book())

    assert first["passages"][0]["section"] == 1
    assert second["passages"][0]["section"] == 2
    assert second["passages"][0]["text"] != first["passages"][0]["text"]
    assert 0 < first["progress_percent"] < second["progress_percent"]


async def test_read_book_refuses_a_book_with_no_text(monkeypatch):
    book = _shelve("Something Recent", source_id=None)

    async def no_frontend(_action, _args):
        return True, None

    monkeypatch.setattr(library_tools, "post_frontend_action", no_frontend)

    assert "has no readable text" in await library_tools.read_book(book["book_id"])


async def test_read_book_sits_down_in_the_chair(monkeypatch, workspace):
    book = _shelve()
    (workspace / "library" / "texts").mkdir(parents=True)
    (workspace / "library" / "texts" / "84.txt").write_text("a page", encoding="utf-8")
    captured: dict = {}

    async def post(action, args):
        captured["action"] = action
        captured["args"] = args
        return True, None

    monkeypatch.setattr(library_tools, "post_frontend_action", post)

    await library_tools.read_book(book["book_id"])

    assert captured["action"] == "stage_patch_state"
    assert captured["args"]["patch"]["nodes"] == [{"id": "avatar", "pose": "sitting"}]


def test_reading_notes_attach_to_the_open_session(monkeypatch):
    book = _shelve()
    library = furniture_state.load_library()
    library["sessions"].append(
        {
            "book_id": book["book_id"],
            "title": book["title"],
            "status": "reading",
            "section": 3,
            "offset": 10,
            "notes": [],
            "updated_at": "2026-07-25T00:00:00+00:00",
        }
    )
    furniture_state.save_library(library)

    payload = json.loads(
        library_tools.save_reading_note("The creature learns to read.")
    )

    assert payload["section"] == 3
    snapshot = furniture_state.reading_snapshot()
    assert snapshot["session"]["notes"][0]["summary"].startswith("The creature")


def test_reading_note_without_a_book_is_an_error():
    assert "no book has been read yet" in library_tools.save_reading_note("...")


def test_book_text_path_rejects_a_non_numeric_source():
    with pytest.raises(ValueError):
        furniture_state.book_text_path("../escape")


def test_only_gutenberg_hosts_may_serve_a_book_text():
    assert library_tools._is_allowed_text_url(
        "https://www.gutenberg.org/ebooks/35.txt.utf-8"
    )
    assert not library_tools._is_allowed_text_url("http://www.gutenberg.org/35.txt")
    assert not library_tools._is_allowed_text_url("https://evil.example.com/35.txt")
    assert not library_tools._is_allowed_text_url(
        "https://gutenberg.org.evil.example.com/35.txt"
    )


def test_downloaded_text_is_trimmed_to_the_book_itself(monkeypatch):
    raw = (
        "front matter\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK FRANKENSTEIN ***\n"
        "Chapter one.\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK FRANKENSTEIN ***\n"
        "license text\n"
    ).encode("utf-8")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return raw

    monkeypatch.setattr(
        library_tools.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    text = library_tools._download_text("https://www.gutenberg.org/files/84/84-0.txt")

    assert text == "Chapter one."
