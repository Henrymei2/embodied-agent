from src.open_llm_vtuber.conversations.agent_activity import (
    AgentActivityTracker,
    tool_batch_activity,
)
from src.open_llm_vtuber.mcpp.types import ToolCallObject


def test_tool_batch_activity_preserves_order_without_inputs():
    calls = [
        {
            "id": "first",
            "name": "read_email",
            "input": {"secret": "must not be exposed"},
        },
        {
            "id": "second",
            "function": {"name": "get_scene_status", "arguments": "{}"},
        },
    ]

    event = tool_batch_activity(calls)

    assert event == {
        "type": "agent_activity",
        "phase": "tools",
        "tools": [
            {
                "tool_id": "first",
                "tool_name": "read_email",
                "status": "pending",
            },
            {
                "tool_id": "second",
                "tool_name": "get_scene_status",
                "status": "pending",
            },
        ],
    }
    assert "secret" not in str(event)


def test_tool_batch_activity_supports_native_tool_objects():
    call = ToolCallObject.from_dict(
        {
            "id": "native",
            "type": "function",
            "index": 0,
            "function": {"name": "search_calendar", "arguments": "{}"},
        }
    )

    event = tool_batch_activity([call])

    assert event["tools"][0]["tool_name"] == "search_calendar"


def test_tracker_updates_tools_and_preserves_completed_batch():
    tracker = AgentActivityTracker()
    tracker.replace_tools(
        [
            {"tool_id": "one", "tool_name": "first_task", "status": "pending"},
            {"tool_id": "two", "tool_name": "second_task", "status": "pending"},
        ]
    )

    running = tracker.update_tool({"tool_id": "one", "status": "running"})
    completed = tracker.update_tool({"tool_id": "one", "status": "completed"})
    failed = tracker.update_tool({"tool_id": "two", "status": "error"})

    assert running["tools"][0]["status"] == "running"
    assert completed["tools"][0]["status"] == "completed"
    assert failed["tools"] == [
        {"tool_id": "one", "tool_name": "first_task", "status": "completed"},
        {"tool_id": "two", "tool_name": "second_task", "status": "error"},
    ]


def test_tracker_has_explicit_thinking_and_idle_events():
    assert AgentActivityTracker.thinking() == {
        "type": "agent_activity",
        "phase": "thinking",
        "tools": [],
    }
    assert AgentActivityTracker.idle() == {
        "type": "agent_activity",
        "phase": "idle",
        "tools": [],
    }
    assert AgentActivityTracker.planning() == {
        "type": "agent_activity",
        "phase": "planning",
        "tools": [],
    }
