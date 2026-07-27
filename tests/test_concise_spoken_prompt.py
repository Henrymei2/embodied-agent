from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from prompts import prompt_loader
from src.open_llm_vtuber.service_context import ServiceContext
from src.open_llm_vtuber.workspace.workspace_manager import TTS_OUTPUT_RULES


ROOT = Path(__file__).resolve().parents[1]
PROMPT_NAME = "concise_style_prompt"


@pytest.mark.parametrize(
    "config_path",
    [ROOT / "conf.yaml", ROOT / "config_templates" / "conf.default.yaml"],
)
def test_concise_prompt_is_enabled_in_shipped_configs(config_path: Path) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["system_config"]["tool_prompts"][PROMPT_NAME] == PROMPT_NAME


def test_concise_prompt_contains_spoken_response_contract() -> None:
    prompt = prompt_loader.load_util(PROMPT_NAME)

    assert "Default to one or two short sentences" in prompt
    assert "Summarize collections by topic and significance" in prompt
    assert "You have one email about the project" in prompt
    assert "Preserve safety-critical information, warnings, deadlines" in prompt
    assert "Never use Markdown syntax in a user-facing response" in prompt
    assert "computer-connected writing or UI tool" in prompt
    assert "Structured MCP or native tool-call payloads" in prompt


@pytest.mark.asyncio
async def test_system_prompt_construction_appends_concise_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ServiceContext.__new__(ServiceContext)
    context.system_config = SimpleNamespace(
        tool_prompts={PROMPT_NAME: PROMPT_NAME}
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.service_context.build_workspace_prompt_context",
        lambda: "",
    )

    system_prompt = await context.construct_system_prompt("Persona.")

    assert system_prompt.startswith("Persona.")
    assert "Never use Markdown syntax in a user-facing response" in system_prompt
    assert "You have one email about the project" in system_prompt


def test_new_workspace_rules_match_concise_contract() -> None:
    assert "Default to one or two short sentences" in TTS_OUTPUT_RULES
    assert "Summarize collections by topic and significance" in TTS_OUTPUT_RULES
    assert "Never use Markdown syntax in a user-facing response" in TTS_OUTPUT_RULES
    assert "computer-connected writing or UI tool" in TTS_OUTPUT_RULES
    assert "Structured MCP or native tool-call payloads" in TTS_OUTPUT_RULES
