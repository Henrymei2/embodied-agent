"""Turn a click on a room object into something the agent can respond to.

Until now the room was one-directional: the agent walked around and used its
furniture, and the user could only watch or type. Pointing at the bookshelf and
tapping it is the most natural thing a person will try, and having nothing
happen makes the room read as scenery rather than a shared space.

The prompt is composed here, from the *stored* stage document — never from text
the client sends. A click therefore carries an object id and nothing else, so
this channel cannot be used to put words in the user's mouth.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from .scene_state import get_scene_reachable_objects, load_scene_state
from .world_model import effective_label

# Long enough that a double-click or an excited tap is one turn, short enough
# that pointing at two objects in a row still reads as two remarks.
CLICK_COOLDOWN_SECONDS = 3.0
_MAX_LABEL = 80


def describe_click(
    conf_uid: str,
    object_id: str,
    scene_mode: str = "normal",
) -> dict[str, Any] | None:
    """Build the prompt for a click, or None when the object is not in the room.

    Returns the user-facing text and the prompt handed to the model, which
    differ: the transcript should read like something the user did, while the
    model needs the spatial situation as well.
    """
    if not isinstance(object_id, str) or not object_id or len(object_id) > _MAX_LABEL:
        return None

    try:
        stage = load_scene_state(conf_uid, scene_mode)
    except Exception as exc:  # noqa: BLE001 - a broken stage is not a crash here
        logger.warning(f"Object interaction: could not load the stage: {exc}")
        return None

    node = next(
        (
            item
            for item in stage.get("nodes", [])
            if isinstance(item, dict) and item.get("id") == object_id
        ),
        None,
    )
    if node is None:
        logger.info(f"Object interaction: no object named {object_id!r} in the room.")
        return None

    # The name the gate matches on, so "you are standing near the X" agrees
    # with whether the tools at X are actually open.
    label = (effective_label(node) or object_id)[:_MAX_LABEL]
    is_avatar = node.get("type") == "avatar"

    try:
        reachable = get_scene_reachable_objects(conf_uid, mode=scene_mode)
    except Exception:  # noqa: BLE001
        reachable = []
    near = label in reachable

    if is_avatar:
        display = "*taps you on the shoulder*"
        prompt = (
            "The user just tapped you, in your room. Nothing else was said. "
            "React the way you would to someone catching your attention: "
            "briefly, in character, and without inventing what they want."
        )
    else:
        display = f"*points at the {label}*"
        where = (
            f"You are standing near the {label}."
            if near
            else f"You are not near the {label} at the moment."
        )
        prompt = (
            f"The user just pointed at the {label} in your room. Nothing else "
            f"was said. {where} React briefly and in character — say something "
            f"about the {label}, or about what you have been doing with it. "
            "Walk over first if you want to use it, and do not invent a request "
            "the user did not make."
        )

    return {
        "object_id": object_id,
        "label": label,
        "display_text": display,
        "prompt": prompt,
        "near": near,
    }
