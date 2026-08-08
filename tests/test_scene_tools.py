from __future__ import annotations

import io
import json

from mcp_servers import bridge_client, scene_tools
from src.open_llm_vtuber.stage_state_manager import get_default_stage, world_from_stage
from src.open_llm_vtuber.vr_stage_state_manager import get_default_vr_stage


def test_scene_reads_session_bound_character(monkeypatch):
    requested: list[str] = []

    def load(conf_uid: str) -> dict:
        requested.append(conf_uid)
        return {"nodes": [{"id": "calendar", "label": "calendar", "x": 12, "y": 34}]}

    monkeypatch.setenv("EMBODIED_AGENT_CONF_UID", "character-42")
    monkeypatch.setattr(scene_tools, "load_stage_state", load)

    assert scene_tools.get_object_position("calendar") == "calendar is at x=12, y=34."
    assert requested == ["character-42"]


def test_frontend_bridge_request_carries_session_identity(monkeypatch):
    captured: dict = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("EMBODIED_AGENT_CLIENT_UID", "client-42")
    monkeypatch.setenv("EMBODIED_AGENT_CONF_UID", "character-42")
    monkeypatch.setenv("EMBODIED_AGENT_BRIDGE_TOKEN", "secret")
    monkeypatch.setattr(bridge_client.urllib.request, "urlopen", urlopen)

    assert bridge_client.post_frontend_action_sync("move_to", {"x": 1, "y": 2}) == (
        True,
        None,
    )
    assert captured["body"] == {
        "action": "move_to",
        "args": {"x": 1, "y": 2},
        "timeout": bridge_client.ACTION_TIMEOUT_SECONDS,
        "client_uid": "client-42",
        "conf_uid": "character-42",
    }
    assert captured["headers"]["X-bridge-token"] == "secret"
    assert captured["url"].endswith("/internal/frontend-action")


def test_invalid_bridge_response_is_reported(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return io.BytesIO(b"not json").read()

    monkeypatch.setattr(
        bridge_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    ok, error = bridge_client.post_frontend_action_sync("move_to", {})

    assert ok is False
    assert "could not reach backend" in error


async def test_place_object_uses_validated_stage_patch(monkeypatch):
    stage = get_default_stage("character")
    captured: dict = {}

    async def post(action, args):
        captured["action"] = action
        captured["args"] = args
        return True, None

    monkeypatch.setenv("EMBODIED_AGENT_CONF_UID", "character")
    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action", post)

    result = await scene_tools.place_object(
        "plant_stand",
        300,
        220,
        image_url="./stage-assets/chair.png",
    )

    assert result == "OK: placed plant_stand at x=300, y=220."
    assert captured["action"] == "stage_patch_state"
    node = captured["args"]["patch"]["nodes"][0]
    assert node["id"] == "plant_stand"
    assert node["texture"]["src"] == "./stage-assets/chair.png"


async def test_place_object_rejects_unsafe_assets_before_frontend(monkeypatch):
    stage = get_default_stage("character")

    async def should_not_post(_action, _args):
        raise AssertionError("invalid objects must not reach the frontend")

    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action", should_not_post)

    result = await scene_tools.place_object(
        "unsafe",
        100,
        100,
        image_url="javascript:alert(1)",
    )

    assert result.startswith("Error: object was rejected:")


async def test_place_prop_creates_a_vr_only_non_capability_entity(monkeypatch):
    stage = get_default_vr_stage("character")
    captured: dict = {}

    async def post(action, args):
        captured["action"] = action
        captured["args"] = args
        return True, None

    monkeypatch.setattr(scene_tools, "_get_scene_mode", lambda: "vr")
    monkeypatch.setattr(scene_tools, "_load_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action", post)

    assert await scene_tools.place_prop("blue_token", 450, 450) == (
        "OK: placed grabbable prop blue_token."
    )
    node = captured["args"]["patch"]["nodes"][0]
    assert node["type"] == "prop"
    assert node["interaction"]["grabbable"] is True
    assert node["support_id"] == "floor"
    assert "reach" not in node


async def test_physical_actions_remain_independent_frontend_affordances(monkeypatch):
    captured: list[tuple[str, dict]] = []

    async def post(action, args):
        captured.append((action, args))
        return True, None, {"summary": "Panel created."}

    monkeypatch.setattr(scene_tools, "_get_scene_mode", lambda: "vr")
    monkeypatch.setattr(scene_tools, "_post_frontend_action_result", post)

    assert await scene_tools.show_vr_panel("Plan", ["One", "Two"]) == "Panel created."
    assert captured == [("show_vr_panel", {"title": "Plan", "blocks": ["One", "Two"]})]


async def test_remove_object_protects_avatar(monkeypatch):
    stage = get_default_stage("character")
    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)

    assert await scene_tools.remove_object("avatar") == (
        "Error: the avatar cannot be removed from its own room."
    )


async def test_remove_object_protects_objects_that_gate_tools(monkeypatch):
    """Removing these would hide the tools only reachable at them, for good."""
    stage = get_default_stage("character")
    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)

    async def should_not_post(_action, _args):
        raise AssertionError("protected furniture must not reach the frontend")

    monkeypatch.setattr(scene_tools, "_post_frontend_action", should_not_post)

    for name in ("computer", "design board"):
        assert "is fixed furniture" in await scene_tools.remove_object(name)


async def test_move_object_patches_the_existing_node(monkeypatch):
    stage = get_default_stage("character")
    captured: dict = {}

    async def post(action, args):
        captured["action"] = action
        captured["args"] = args
        return True, None

    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action", post)

    result = await scene_tools.move_object("bookshelf", 300, 380)

    assert result == "OK: moved bookshelf to x=300, y=380."
    node = captured["args"]["patch"]["nodes"][0]
    assert (node["id"], node["x"], node["y"]) == ("bookshelf", 300, 380)
    # The patch must carry the node's assets, not just its new position.
    assert node["texture"]["src"].endswith("bookshelf.png")


async def test_vr_furniture_move_carries_supported_props_atomically(monkeypatch):
    stage = get_default_vr_stage("character")
    shelf = next(node for node in stage["nodes"] if node["id"] == "bookshelf")
    prop = {
        "id": "shelf_token", "type": "prop", "label": "shelf token",
        "x": shelf["x"], "y": shelf["y"], "width": 10, "depth": 10,
        "height": 10, "elevation": shelf["elevation"] + shelf["height"] / 2 + 5,
        "yaw": 0, "support_id": "bookshelf", "color": "#7c3aed",
        "interaction": {"on_click": True, "grabbable": True},
    }
    stage["nodes"].append(prop)
    captured: dict = {}

    async def post(_action, args):
        captured.update(args)
        return True, None

    monkeypatch.setattr(scene_tools, "_get_scene_mode", lambda: "vr")
    monkeypatch.setattr(scene_tools, "_load_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action", post)

    result = await scene_tools.move_object("bookshelf", shelf["x"] + 10, shelf["y"])
    assert result.startswith("OK: moved bookshelf")
    patched = {node["id"]: node for node in captured["patch"]["nodes"]}
    assert patched["shelf_token"]["x"] == prop["x"] + 10


async def test_move_object_rejects_positions_outside_the_canvas(monkeypatch):
    stage = get_default_stage("character")

    async def should_not_post(_action, _args):
        raise AssertionError("invalid moves must not reach the frontend")

    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action", should_not_post)

    assert await scene_tools.move_object("bookshelf", 5000, 10) == (
        "Error: x and y must be inside the stage canvas."
    )
    assert await scene_tools.move_object("avatar", 100, 100) == (
        "Error: use move_to to move the avatar."
    )


def test_describe_room_lists_objects_and_marks_fixed_furniture(monkeypatch):
    stage = get_default_stage("character")
    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)

    described = scene_tools.describe_room()

    assert "Room is 960x540 units (100 units per metre)." in described
    assert "computer (label=computer" in described
    assert "fixed" in described
    assert "avatar" not in described
    # A bed is not an hourglass: each object says how close is close enough.
    assert "reach 150" in described
    assert "reach 105" in described


def _walking_room(monkeypatch, *, arrive: bool):
    """A room whose avatar moves only if the frontend actually walks it there."""
    stage = get_default_stage("character")
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    sent: dict = {}

    async def post(action, args):
        sent["action"] = action
        sent["args"] = args
        if arrive:
            avatar["x"], avatar["y"] = args["x"], args["y"]
        return True, None, {"ok": True, "arrived": arrive, "duration_ms": 400}

    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action_result", post)
    return stage, sent


async def test_move_to_sends_a_path_and_confirms_arrival(monkeypatch):
    stage, sent = _walking_room(monkeypatch, arrive=True)
    bookshelf = next(node for node in stage["nodes"] if node["id"] == "bookshelf")

    result = await scene_tools.move_to("bookshelf")

    assert result == "OK: arrived at bookshelf (x=600, y=370)."
    assert sent["action"] == "move_to"
    # A path, not just a destination: the renderer walks the legs it is given.
    assert sent["args"]["waypoints"] == [{"x": bookshelf["x"], "y": bookshelf["y"]}]
    assert (sent["args"]["x"], sent["args"]["y"]) == (bookshelf["x"], bookshelf["y"])


async def test_move_to_reports_a_walk_that_did_not_finish(monkeypatch):
    """An accepted request is not an arrival; the room is what decides."""
    _walking_room(monkeypatch, arrive=False)

    result = await scene_tools.move_to("bookshelf")

    assert result.startswith("Error: the walk to bookshelf did not finish")
    assert "x=60, y=515" in result


async def test_move_to_an_unknown_object_never_reaches_the_frontend(monkeypatch):
    stage = get_default_stage("character")

    async def should_not_post(_action, _args):
        raise AssertionError("an unknown target must not reach the frontend")

    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action_result", should_not_post)

    assert await scene_tools.move_to("trapdoor") == (
        "Error: no object found with label 'trapdoor'; cannot move there."
    )


async def test_vr_move_to_rechecks_tool_reach_from_saved_scene(monkeypatch):
    stage = get_default_vr_stage("character")
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")

    async def post(_action, args):
        avatar["x"], avatar["y"] = args["x"], args["y"]
        return True, None, {"ok": True, "arrived": True}

    monkeypatch.setattr(scene_tools, "_get_scene_mode", lambda: "vr")
    monkeypatch.setattr(scene_tools, "_load_state", lambda _conf_uid: stage)
    monkeypatch.setattr(
        scene_tools, "_get_world", lambda _conf_uid: world_from_stage(stage)
    )
    monkeypatch.setattr(scene_tools, "_post_frontend_action_result", post)

    assert (await scene_tools.move_to("window")).startswith("OK: arrived at window")


async def test_vr_move_to_reports_post_save_reach_mismatch(monkeypatch):
    stage = get_default_vr_stage("character")
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    window = next(node for node in stage["nodes"] if node["id"] == "window")

    async def post(_action, args):
        avatar["x"], avatar["y"] = args["x"], args["y"]
        window["reach"] = 1
        return True, None, {"ok": True, "arrived": True}

    monkeypatch.setattr(scene_tools, "_get_scene_mode", lambda: "vr")
    monkeypatch.setattr(scene_tools, "_load_state", lambda _conf_uid: stage)
    monkeypatch.setattr(
        scene_tools, "_get_world", lambda _conf_uid: world_from_stage(stage)
    )
    monkeypatch.setattr(scene_tools, "_post_frontend_action_result", post)

    result = await scene_tools.move_to("window")
    assert "tools remain unavailable" in result
    assert "center_distance=" in result
    assert "effective_reach=1.0" in result


def test_the_bridge_hands_back_what_the_frontend_reported(monkeypatch):
    """Verifying an outcome needs the frontend's answer, not just its receipt."""

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"ok": true, "result": {"arrived": true, "duration_ms": 620}}'

    monkeypatch.setattr(
        bridge_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    ok, error, result = bridge_client.post_frontend_action_result_sync("move_to", {})

    assert (ok, error) == (True, None)
    assert result["duration_ms"] == 620
    # The older two-value call still works for tools that only need success.
    assert bridge_client.post_frontend_action_sync("move_to", {}) == (True, None)
