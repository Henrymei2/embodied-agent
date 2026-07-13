from typing import (
    AsyncIterator,
    List,
    Dict,
    Any,
    Callable,
    Literal,
    Union,
    Optional,
)
import json
from ...workspace import build_workspace_prompt_context
from loguru import logger
from .agent_interface import AgentInterface
from ..output_types import SentenceOutput, DisplayText
from ..stateless_llm.stateless_llm_interface import StatelessLLMInterface
from ..stateless_llm.claude_llm import AsyncLLM as ClaudeAsyncLLM
from ..stateless_llm.openai_compatible_llm import AsyncLLM as OpenAICompatibleAsyncLLM
from ...chat_history_manager import get_history
from ..transformers import (
    sentence_divider,
    actions_extractor,
    tts_filter,
    display_processor,
)
from ...config_manager import TTSPreprocessorConfig
from ..input_types import BatchInput, TextSource
from prompts import prompt_loader
from ...mcpp.tool_manager import ToolManager
from ...mcpp.json_detector import StreamJSONDetector
from ...mcpp.types import ToolCallObject
from ...mcpp.tool_executor import ToolExecutor
from ...memory import MemoryManager


SPEAK_TOOL_NAME = "speak"
SPEAK_TOOL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": (
                "Brief plain text to speak directly to the user. "
                "Do not use Markdown or multiline formatting."
            ),
        }
    },
    "required": ["text"],
}
SPEAK_TOOL_DESCRIPTION = (
    "Speak brief plain text directly to the user. Use only when user-facing "
    "speech is necessary; do not narrate every intermediate step."
)
SPEAK_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": SPEAK_TOOL_NAME,
        "description": SPEAK_TOOL_DESCRIPTION,
        "parameters": {
            **SPEAK_TOOL_INPUT_SCHEMA,
            "additionalProperties": False,
        },
    },
}
SPEAK_TOOL_CLAUDE = {
    "name": SPEAK_TOOL_NAME,
    "description": SPEAK_TOOL_DESCRIPTION,
    "input_schema": SPEAK_TOOL_INPUT_SCHEMA,
}
SPEAK_OUTPUT_REQUIREMENT = """\
## Built-in Speak Tool Requirement

You have a built-in tool named `speak`.

When you want to say anything directly to the user, you must call `speak` with brief plain text.
Do not put user-facing spoken content in normal assistant text.
Normal assistant text is display-only and will not be converted to speech.
Use normal assistant text only for non-spoken status or tool progress when needed.
Do not call `speak` for every intermediate step.
"""


class BasicMemoryAgent(AgentInterface):
    """Agent with basic chat memory and tool calling support."""

    _system: str = "You are a helpful assistant."

    def __init__(
        self,
        llm: StatelessLLMInterface,
        system: str,
        tts_preprocessor_config: TTSPreprocessorConfig = None,
        faster_first_response: bool = True,
        segment_method: str = "pysbd",
        use_mcpp: bool = False,
        interrupt_method: Literal["system", "user"] = "user",
        tool_prompts: Dict[str, str] = None,
        tool_manager: Optional[ToolManager] = None,
        tool_executor: Optional[ToolExecutor] = None,
        mcp_prompt_string: str = "",
        memory_manager: Optional[MemoryManager] = None,
    ):
        """Initialize agent with LLM and configuration."""
        super().__init__()
        self._memory = []
        # Persistent three-layer memory (contextual/daily/long-term). Optional:
        # when present the agent records turns, triggers compaction, and injects
        # remembered context into the system prompt.
        self._memory_manager = memory_manager
        # System prompt actually used for the current turn (base prompt plus the
        # memory block). Set per-turn in the chat pipeline; falls back to base.
        self._active_system: Optional[str] = None
        self._tts_preprocessor_config = tts_preprocessor_config
        self._faster_first_response = faster_first_response
        self._segment_method = segment_method
        self._use_mcpp = use_mcpp
        self.interrupt_method = interrupt_method
        self._tool_prompts = tool_prompts or {}
        self._interrupt_handled = False
        self.prompt_mode_flag = False

        self._tool_manager = tool_manager
        self._tool_executor = tool_executor
        self._mcp_prompt_string = mcp_prompt_string
        self._json_detector = StreamJSONDetector()

        self._formatted_tools_openai = []
        self._formatted_tools_claude = []
        if self._tool_manager:
            self._formatted_tools_openai = self._tool_manager.get_formatted_tools(
                "OpenAI"
            )
            self._formatted_tools_claude = self._tool_manager.get_formatted_tools(
                "Claude"
            )
            logger.debug(
                f"Agent received pre-formatted tools - OpenAI: {len(self._formatted_tools_openai)}, Claude: {len(self._formatted_tools_claude)}"
            )
            logger.info(
                "Agent OpenAI tool names: "
                f"{[tool.get('function', {}).get('name') for tool in self._formatted_tools_openai]}"
            )
            logger.info(
                "Agent Claude tool names: "
                f"{[tool.get('name') for tool in self._formatted_tools_claude]}"
            )
        else:
            logger.debug(
                "ToolManager not provided, agent will not have pre-formatted tools."
            )

        self._formatted_tools_openai = self._with_internal_speak_tool(
            self._formatted_tools_openai,
            "OpenAI",
        )
        self._formatted_tools_claude = self._with_internal_speak_tool(
            self._formatted_tools_claude,
            "Claude",
        )
        logger.info("Agent built-in tool names: ['speak']")
        logger.info(
            "Agent OpenAI tool names after built-in injection: "
            f"{[tool.get('function', {}).get('name') for tool in self._formatted_tools_openai]}"
        )
        logger.info(
            "Agent Claude tool names after built-in injection: "
            f"{[tool.get('name') for tool in self._formatted_tools_claude]}"
        )

        self._set_llm(llm)
        self.set_system(system if system else self._system)

        if self._use_mcpp and not all(
            [
                self._tool_manager,
                self._tool_executor,
                self._json_detector,
            ]
        ):
            logger.warning(
                "use_mcpp is True, but some MCP components are missing in the agent. Tool calling might not work as expected."
            )
        elif not self._use_mcpp and any(
            [
                self._tool_manager,
                self._tool_executor,
                self._json_detector,
            ]
        ):
            logger.warning(
                "use_mcpp is False, but some MCP components were passed to the agent."
            )

        logger.info("BasicMemoryAgent initialized.")

    def _with_internal_speak_tool(
        self,
        tools: List[Dict[str, Any]],
        mode: Literal["Claude", "OpenAI"],
    ) -> List[Dict[str, Any]]:
        """Return formatted tools with the reserved local speak tool included."""
        tool_names = {
            tool.get("function", {}).get("name")
            if mode == "OpenAI"
            else tool.get("name")
            for tool in tools
        }
        if SPEAK_TOOL_NAME in tool_names:
            logger.warning(
                "An external tool named 'speak' is already registered. "
                "Replacing it with the reserved internal speak tool."
            )

        internal_tool = SPEAK_TOOL_OPENAI if mode == "OpenAI" else SPEAK_TOOL_CLAUDE
        filtered_tools = [
            tool
            for tool in tools
            if (
                tool.get("function", {}).get("name")
                if mode == "OpenAI"
                else tool.get("name")
            )
            != SPEAK_TOOL_NAME
        ]
        return [*filtered_tools, internal_tool]

    def _is_speak_tool_call(self, call: Dict[str, Any] | ToolCallObject) -> bool:
        """Return whether a tool call targets the reserved local speak tool."""
        if isinstance(call, ToolCallObject):
            return call.function.name == SPEAK_TOOL_NAME
        if isinstance(call, dict):
            return (
                call.get("name") == SPEAK_TOOL_NAME
                or call.get("tool") == SPEAK_TOOL_NAME
            )
        return False

    def _extract_speak_text(self, call: Dict[str, Any] | ToolCallObject) -> str:
        """Extract and normalize text from a reserved speak tool call."""
        tool_input: Any = {}

        if isinstance(call, ToolCallObject):
            try:
                tool_input = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                logger.error("Failed to decode speak tool arguments.")
                return ""
        elif isinstance(call, dict):
            tool_input = call.get("input", call.get("args", call.get("arguments", {})))
            if isinstance(tool_input, str):
                try:
                    tool_input = json.loads(tool_input)
                except json.JSONDecodeError:
                    tool_input = {"text": tool_input}

        if isinstance(tool_input, dict):
            text = str(tool_input.get("text", ""))
        else:
            text = str(tool_input)

        return " ".join(text.split())

    def _make_spoken_event(self, text: str) -> Dict[str, str] | None:
        """Create a reserved speak event for non-empty text."""
        text = " ".join(text.split())
        if not text:
            return None

        return {"type": "speak_text", "text": text}

    def _make_agent_text_event(self, text: str) -> Dict[str, str] | None:
        """Create a non-spoken display event for assistant text."""
        if not text:
            return None

        return {"type": "agent_text", "content": text}

    def _format_speak_tool_result(
        self,
        caller_mode: Literal["Claude", "OpenAI", "Prompt"],
        tool_id: str,
    ) -> Dict[str, Any]:
        """Format a successful reserved speak tool result for the LLM."""
        if caller_mode == "Claude":
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": "Spoken to the user.",
                "is_error": False,
            }
        if caller_mode == "OpenAI":
            return {
                "role": "tool",
                "tool_call_id": tool_id,
                "content": "Spoken to the user.",
            }
        return {
            "tool_id": tool_id,
            "content": "Spoken to the user.",
            "is_error": False,
        }

    def _tool_call_name_and_args(
        self, call: Dict[str, Any] | ToolCallObject
    ) -> tuple[Optional[str], Dict[str, Any]]:
        """Extract (tool_name, args_dict) from any supported tool-call shape."""
        name: Optional[str] = None
        args: Any = {}
        if isinstance(call, ToolCallObject):
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
        elif isinstance(call, dict):
            name = call.get("name") or call.get("tool")
            args = call.get("input", call.get("args", call.get("arguments", {})))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
        if not isinstance(args, dict):
            args = {}
        return name, args

    def _describe_tool_action(
        self, call: Dict[str, Any] | ToolCallObject
    ) -> Optional[str]:
        """Human-readable description of an embodied/tool action for memory.

        The scope treats "what the agent was doing in the embodied environment"
        as first-class contextual memory, so scene/navigation tools get natural
        phrasing and other tools get a generic fallback. Returns None for the
        reserved speak tool (that is captured as assistant speech instead).
        """
        name, args = self._tool_call_name_and_args(call)
        if not name or name == SPEAK_TOOL_NAME:
            return None

        target = (
            args.get("target")
            or args.get("name")
            or args.get("object")
            or args.get("label")
        )
        if name == "move_to":
            return f"moved to {target}" if target else "moved through the scene"
        if name == "get_object_position":
            return (
                f"checked where {target} is"
                if target
                else "checked an object's position"
            )
        if name == "list_objects":
            return "looked around at nearby objects"
        if name == "get_scene_status":
            return "checked its own position in the scene"
        return f"used the {name} tool"

    def _record_tool_actions(
        self, calls: List[Dict[str, Any]] | List[ToolCallObject]
    ) -> None:
        """Record embodied/tool actions into contextual memory (best-effort)."""
        if not self._memory_manager:
            return
        for call in calls:
            try:
                description = self._describe_tool_action(call)
                if description:
                    self._memory_manager.record_action(description)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Memory: failed recording tool action: {exc}")

    def _build_builtin_tool_prompt_string(self) -> str:
        """Build prompt-mode instructions for agent-local built-in tools."""
        return "\n".join(
            [
                "Server: builtin",
                "    Tools:",
                "        speak:",
                f"            Description: {SPEAK_TOOL_DESCRIPTION}",
                "            Parameters:",
                "                text:",
                "                    Type: string",
                "                    Description: Brief plain text to speak directly to the user. Do not use Markdown or multiline formatting.",
                "            Required: text",
                "",
                'Use mcp_server "builtin" when calling agent built-in tools.',
            ]
        )

    def _build_speak_output_requirement(self, base_system_prompt: str) -> str:
        """Append the speak requirement to the system prompt once."""
        if "## Built-in Speak Tool Requirement" in base_system_prompt:
            return base_system_prompt
        return f"{base_system_prompt}\n\n{SPEAK_OUTPUT_REQUIREMENT}"

    def _set_llm(self, llm: StatelessLLMInterface):
        """Set the LLM for chat completion."""
        self._llm = llm
        self.chat = self._chat_function_factory()

    def set_system(self, system: str):
        """Set the system prompt."""
        logger.debug(f"Memory Agent: Setting system prompt: '''{system}'''")

        if self.interrupt_method == "user":
            system = f"{system}\n\nIf you received `[interrupted by user]` signal, you were interrupted."

        self._system = self._build_speak_output_requirement(system)

    def _build_prompt_mode_system_prompt(self, base_system_prompt: str) -> str:
        """Build prompt-mode tool instructions from the MCP prompt template."""
        builtin_prompt = self._build_builtin_tool_prompt_string()
        combined_tool_prompt = (
            f"{builtin_prompt}\n\n{self._mcp_prompt_string}"
            if self._mcp_prompt_string
            else builtin_prompt
        )

        try:
            prompt_template = prompt_loader.load_util("mcp_prompt")
        except FileNotFoundError:
            logger.warning(
                "MCP prompt template not found. Falling back to raw MCP tool listing."
            )
            prompt_template = "[<insert_mcp_servers_with_tools>]"
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to load MCP prompt template: {exc}")
            prompt_template = "[<insert_mcp_servers_with_tools>]"

        mcp_prompt = prompt_template.replace(
            "[<insert_mcp_servers_with_tools>]", combined_tool_prompt
        )
        return f"{base_system_prompt}\n\n{mcp_prompt}"

    def _build_active_system(self) -> str:
        """Build the per-turn system prompt with persistent memory context."""
        if not self._memory_manager:
            return self._system

        try:
            memory_context = self._memory_manager.build_memory_context()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Memory: failed building prompt context: {exc}")
            return self._system

        if not memory_context:
            return self._system

        return (
            f"{self._system}\n\n"
            "Use the following memory as background context. "
            "It may be incomplete or outdated, so prefer the user's latest message "
            "when there is a conflict.\n\n"
            f"{memory_context}"
        )

    def _build_system_prompt_with_workspace_context(
        self,
        messages: List[Dict[str, Any]],
        base_system_prompt: str,
        include_core_files: bool = False,
    ) -> str:
        latest_user_message = ""

        for message in reversed(messages):
            if message.get("role") == "user":
                latest_user_message = str(message.get("content", ""))
                break

        workspace_context = build_workspace_prompt_context(
            user_message=latest_user_message,
            include_core_files=include_core_files,
        )

        if not workspace_context:
            return base_system_prompt

        logger.debug(
            f"Injected query-specific workspace context. "
            f"Length: {len(workspace_context)}"
        )

        return f"{base_system_prompt}\n\n{workspace_context}"

    def _add_message(
        self,
        message: Union[str, List[Dict[str, Any]]],
        role: str,
        display_text: DisplayText | None = None,
        skip_memory: bool = False,
    ):
        """Add message to memory."""
        if skip_memory:
            return

        text_content = ""
        if isinstance(message, list):
            for item in message:
                if item.get("type") == "text":
                    text_content += item["text"] + " "
            text_content = text_content.strip()
        elif isinstance(message, str):
            text_content = message
        else:
            logger.warning(
                f"_add_message received unexpected message type: {type(message)}"
            )
            text_content = str(message)

        if not text_content and role == "assistant":
            return

        message_data = {
            "role": role,
            "content": text_content,
        }

        if display_text:
            if display_text.name:
                message_data["name"] = display_text.name
            if display_text.avatar:
                message_data["avatar"] = display_text.avatar

        if (
            self._memory
            and self._memory[-1]["role"] == role
            and self._memory[-1]["content"] == text_content
        ):
            return

        self._memory.append(message_data)

    def set_memory_from_history(self, conf_uid: str, history_uid: str) -> None:
        """Load memory from chat history."""
        messages = get_history(conf_uid, history_uid)

        self._memory = []
        for msg in messages:
            role = "user" if msg["role"] == "human" else "assistant"
            content = msg["content"]
            if isinstance(content, str) and content:
                self._memory.append(
                    {
                        "role": role,
                        "content": content,
                    }
                )
            else:
                logger.warning(f"Skipping invalid message from history: {msg}")
        logger.info(f"Loaded {len(self._memory)} messages from history.")

    def handle_interrupt(self, heard_response: str) -> None:
        """Handle user interruption."""
        if self._interrupt_handled:
            return

        self._interrupt_handled = True

        if self._memory and self._memory[-1]["role"] == "assistant":
            if not self._memory[-1]["content"].endswith("..."):
                self._memory[-1]["content"] = heard_response + "..."
            else:
                self._memory[-1]["content"] = heard_response + "..."
        else:
            if heard_response:
                self._memory.append(
                    {
                        "role": "assistant",
                        "content": heard_response + "...",
                    }
                )

        interrupt_role = "system" if self.interrupt_method == "system" else "user"
        self._memory.append(
            {
                "role": interrupt_role,
                "content": "[Interrupted by user]",
            }
        )
        logger.info(f"Handled interrupt with role '{interrupt_role}'.")

    def _to_text_prompt(self, input_data: BatchInput) -> str:
        """Format input data to text prompt."""
        message_parts = []

        for text_data in input_data.texts:
            if text_data.source == TextSource.INPUT:
                message_parts.append(text_data.content)
            elif text_data.source == TextSource.CLIPBOARD:
                message_parts.append(
                    f"[User shared content from clipboard: {text_data.content}]"
                )

        if input_data.images:
            message_parts.append("\n[User has also provided images]")

        return "\n".join(message_parts).strip()

    def _to_messages(self, input_data: BatchInput) -> List[Dict[str, Any]]:
        """Prepare messages for LLM API call."""
        messages = self._memory.copy()
        user_content = []
        text_prompt = self._to_text_prompt(input_data)
        if text_prompt:
            user_content.append({"type": "text", "text": text_prompt})

        if input_data.images:
            image_added = False
            for img_data in input_data.images:
                if isinstance(img_data.data, str) and img_data.data.startswith(
                    "data:image"
                ):
                    user_content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": img_data.data, "detail": "auto"},
                        }
                    )
                    image_added = True
                else:
                    logger.error(
                        f"Invalid image data format: {type(img_data.data)}. Skipping image."
                    )

            if not image_added and not text_prompt:
                logger.warning(
                    "User input contains images but none could be processed."
                )

        if user_content:
            user_message = {"role": "user", "content": user_content}
            messages.append(user_message)

            skip_memory = False
            if input_data.metadata and input_data.metadata.get("skip_memory", False):
                skip_memory = True

            if not skip_memory:
                self._add_message(
                    text_prompt if text_prompt else "[User provided image(s)]", "user"
                )
        else:
            logger.warning("No content generated for user message.")

        return messages

    async def _claude_tool_interaction_loop(
        self,
        initial_messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """Handle Claude interaction loop with tool support."""
        messages = initial_messages.copy()
        current_turn_text = ""
        pending_tool_calls = []
        current_assistant_message_content = []
        base_system = self._active_system or self._system

        while True:
            current_system_prompt = self._build_system_prompt_with_workspace_context(
                messages,
                base_system,
            )

            stream = self._llm.chat_completion(
                messages,
                current_system_prompt,
                tools=tools,
            )
            pending_tool_calls.clear()
            current_assistant_message_content.clear()

            async for event in stream:
                if event["type"] == "text_delta":
                    text = event["text"]
                    current_turn_text += text
                    text_event = self._make_agent_text_event(text)
                    if text_event:
                        yield text_event
                    if (
                        not current_assistant_message_content
                        or current_assistant_message_content[-1]["type"] != "text"
                    ):
                        current_assistant_message_content.append(
                            {"type": "text", "text": text}
                        )
                    else:
                        current_assistant_message_content[-1]["text"] += text
                elif event["type"] == "tool_use_complete":
                    tool_call_data = event["data"]
                    logger.info(
                        f"Tool request: {tool_call_data['name']} (ID: {tool_call_data['id']})"
                    )
                    pending_tool_calls.append(tool_call_data)
                    current_assistant_message_content.append(
                        {
                            "type": "tool_use",
                            "id": tool_call_data["id"],
                            "name": tool_call_data["name"],
                            "input": tool_call_data["input"],
                        }
                    )
                # elif event["type"] == "message_delta":
                #     if event["data"]["delta"].get("stop_reason"):
                #         stop_reason = event["data"]["delta"].get("stop_reason")
                elif event["type"] == "message_stop":
                    break
                elif event["type"] == "error":
                    logger.error(f"LLM API Error: {event['message']}")
                    error_event = self._make_agent_text_event(
                        f"[Error from LLM: {event['message']}]"
                    )
                    if error_event:
                        yield error_event
                    return

            if pending_tool_calls:
                filtered_assistant_content = [
                    block
                    for block in current_assistant_message_content
                    if not (
                        block.get("type") == "text"
                        and not block.get("text", "").strip()
                    )
                ]

                if filtered_assistant_content:
                    messages.append(
                        {"role": "assistant", "content": filtered_assistant_content}
                    )
                    assistant_text_for_memory = "".join(
                        [
                            c["text"]
                            for c in filtered_assistant_content
                            if c["type"] == "text"
                        ]
                    ).strip()
                    if assistant_text_for_memory:
                        self._add_message(assistant_text_for_memory, "assistant")

                tool_results_for_llm = []
                if not self._tool_executor:
                    non_speak_calls = [
                        call
                        for call in pending_tool_calls
                        if not self._is_speak_tool_call(call)
                    ]
                    if non_speak_calls:
                        logger.error(
                            "Claude Tool interaction requested but ToolExecutor is not available."
                        )
                        error_event = self._make_agent_text_event(
                            "[Error: ToolExecutor not configured]"
                        )
                        if error_event:
                            yield error_event
                        return

                external_tool_calls = []
                spoke_this_turn = False
                for tool_call in pending_tool_calls:
                    if self._is_speak_tool_call(tool_call):
                        speak_text = self._extract_speak_text(tool_call)
                        speak_event = self._make_spoken_event(speak_text)
                        if speak_event:
                            spoke_this_turn = True
                            self._add_message(speak_text, "assistant")
                            yield speak_event
                        tool_results_for_llm.append(
                            self._format_speak_tool_result(
                                "Claude",
                                tool_call.get("id", "speak"),
                            )
                        )
                    else:
                        external_tool_calls.append(tool_call)

                if spoke_this_turn and not external_tool_calls:
                    return

                if external_tool_calls:
                    self._record_tool_actions(external_tool_calls)
                    tool_executor_iterator = self._tool_executor.execute_tools(
                        tool_calls=external_tool_calls,
                        caller_mode="Claude",
                    )
                    try:
                        while True:
                            update = await anext(tool_executor_iterator)
                            if update.get("type") == "final_tool_results":
                                tool_results_for_llm.extend(update.get("results", []))
                                break
                            else:
                                yield update
                    except StopAsyncIteration:
                        logger.warning(
                            "Tool executor finished without final results marker."
                        )

                if tool_results_for_llm:
                    messages.append({"role": "user", "content": tool_results_for_llm})

                # stop_reason = None
                continue
            else:
                if current_turn_text:
                    self._add_message(current_turn_text, "assistant")
                return

    async def _openai_tool_interaction_loop(
        self,
        initial_messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """Handle OpenAI interaction with tool support."""
        messages = initial_messages.copy()
        current_turn_text = ""
        pending_tool_calls: Union[List[ToolCallObject], List[Dict[str, Any]]] = []
        base_system = self._active_system or self._system
        current_system_prompt = base_system

        while True:
            if self.prompt_mode_flag:
                current_system_prompt = self._build_prompt_mode_system_prompt(
                    base_system
                )
                tools_for_api = None
            else:
                current_system_prompt = base_system
                tools_for_api = tools

            current_system_prompt = self._build_system_prompt_with_workspace_context(
                messages,
                current_system_prompt,
            )

            stream = self._llm.chat_completion(
                messages, current_system_prompt, tools=tools_for_api
            )
            pending_tool_calls.clear()
            current_turn_text = ""
            assistant_message_for_api = None
            detected_prompt_json = None
            goto_next_while_iteration = False

            async for event in stream:
                if self.prompt_mode_flag:
                    if isinstance(event, str):
                        current_turn_text += event
                        if self._json_detector:
                            potential_json = self._json_detector.process_chunk(event)
                            if potential_json:
                                try:
                                    if isinstance(potential_json, list):
                                        detected_prompt_json = potential_json
                                    elif isinstance(potential_json, dict):
                                        detected_prompt_json = [potential_json]

                                    if detected_prompt_json:
                                        break
                                except Exception as e:
                                    logger.error(f"Error parsing detected JSON: {e}")
                                    if self._json_detector:
                                        self._json_detector.reset()
                                    error_event = self._make_agent_text_event(
                                        f"[Error parsing tool JSON: {e}]"
                                    )
                                    if error_event:
                                        yield error_event
                                    goto_next_while_iteration = True
                                    break
                        text_event = self._make_agent_text_event(event)
                        if text_event:
                            yield text_event
                else:
                    if event == "__API_NOT_SUPPORT_TOOLS__":
                        logger.warning(
                            f"LLM {getattr(self._llm, 'model', '')} has no native tool support. Switching to prompt mode."
                        )
                        self.prompt_mode_flag = True
                        if self._json_detector:
                            self._json_detector.reset()
                        goto_next_while_iteration = True
                        break
                    elif isinstance(event, str):
                        current_turn_text += event
                        text_event = self._make_agent_text_event(event)
                        if text_event:
                            yield text_event
                    elif isinstance(event, list) and all(
                        isinstance(tc, ToolCallObject) for tc in event
                    ):
                        pending_tool_calls = event
                        assistant_message_for_api = {
                            "role": "assistant",
                            "content": current_turn_text if current_turn_text else None,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": tc.type,
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in pending_tool_calls
                            ],
                        }
                        break
            if goto_next_while_iteration:
                continue

            if detected_prompt_json:
                logger.info("Processing tools detected via prompt mode JSON.")
                self._add_message(current_turn_text, "assistant")

                if not self._tool_executor:
                    non_speak_items = [
                        item
                        for item in detected_prompt_json
                        if not self._is_speak_tool_call(item)
                    ]
                    if non_speak_items:
                        logger.error(
                            "Prompt Tool interaction requested but ToolExecutor/MCPClient is not available."
                        )
                        error_event = self._make_agent_text_event(
                            "[Error: ToolExecutor/MCPClient not configured for prompt mode]"
                        )
                        if error_event:
                            yield error_event
                        continue

                speak_prompt_items = [
                    item
                    for item in detected_prompt_json
                    if self._is_speak_tool_call(item)
                ]
                prompt_tool_results_for_llm = []
                for item in speak_prompt_items:
                    speak_text = self._extract_speak_text(item)
                    speak_event = self._make_spoken_event(speak_text)
                    if speak_event:
                        self._add_message(speak_text, "assistant")
                        yield speak_event
                    prompt_tool_results_for_llm.append(
                        self._format_speak_tool_result(
                            "Prompt",
                            item.get("id", "speak"),
                        )
                    )

                parsed_tools = []
                if self._tool_executor:
                    parsed_tools = self._tool_executor.process_tool_from_prompt_json(
                        [
                            item
                            for item in detected_prompt_json
                            if not self._is_speak_tool_call(item)
                        ]
                    )
                if parsed_tools:
                    tool_results_for_llm = []

                    self._record_tool_actions(parsed_tools)
                    tool_executor_iterator = self._tool_executor.execute_tools(
                        tool_calls=parsed_tools,
                        caller_mode="Prompt",
                    )
                    try:
                        while True:
                            update = await anext(tool_executor_iterator)
                            if update.get("type") == "final_tool_results":
                                tool_results_for_llm = update.get("results", [])
                                break
                            else:
                                yield update
                    except StopAsyncIteration:
                        logger.warning(
                            "Prompt mode tool executor finished without final results marker."
                        )

                    prompt_tool_results_for_llm.extend(tool_results_for_llm)

                if prompt_tool_results_for_llm:
                    result_strings = [
                        res.get("content", "Error: Malformed result")
                        for res in prompt_tool_results_for_llm
                    ]
                    combined_results_str = "\n".join(result_strings)
                    messages.append({"role": "user", "content": combined_results_str})
                continue

            elif pending_tool_calls and assistant_message_for_api:
                messages.append(assistant_message_for_api)
                if current_turn_text:
                    self._add_message(current_turn_text, "assistant")

                tool_results_for_llm = []
                if not self._tool_executor:
                    non_speak_calls = [
                        call
                        for call in pending_tool_calls
                        if not self._is_speak_tool_call(call)
                    ]
                    if non_speak_calls:
                        logger.error(
                            "OpenAI Tool interaction requested but ToolExecutor/MCPClient is not available."
                        )
                        error_event = self._make_agent_text_event(
                            "[Error: ToolExecutor/MCPClient not configured for OpenAI mode]"
                        )
                        if error_event:
                            yield error_event
                        continue

                external_tool_calls = []
                spoke_this_turn = False
                for tool_call in pending_tool_calls:
                    if self._is_speak_tool_call(tool_call):
                        speak_text = self._extract_speak_text(tool_call)
                        speak_event = self._make_spoken_event(speak_text)
                        if speak_event:
                            spoke_this_turn = True
                            self._add_message(speak_text, "assistant")
                            yield speak_event
                        tool_results_for_llm.append(
                            self._format_speak_tool_result(
                                "OpenAI",
                                tool_call.id,
                            )
                        )
                    else:
                        external_tool_calls.append(tool_call)

                if spoke_this_turn and not external_tool_calls:
                    return

                if external_tool_calls:
                    self._record_tool_actions(external_tool_calls)
                    tool_executor_iterator = self._tool_executor.execute_tools(
                        tool_calls=external_tool_calls,
                        caller_mode="OpenAI",
                    )
                    try:
                        while True:
                            update = await anext(tool_executor_iterator)
                            if update.get("type") == "final_tool_results":
                                tool_results_for_llm.extend(update.get("results", []))
                                break
                            else:
                                yield update
                    except StopAsyncIteration:
                        logger.warning(
                            "OpenAI tool executor finished without final results marker."
                        )

                if tool_results_for_llm:
                    messages.extend(tool_results_for_llm)
                continue

            else:
                if current_turn_text:
                    self._add_message(current_turn_text, "assistant")
                return

    def _chat_function_factory(
        self,
    ) -> Callable[[BatchInput], AsyncIterator[Union[SentenceOutput, Dict[str, Any]]]]:
        """Create the chat pipeline function."""

        @tts_filter(self._tts_preprocessor_config)
        @display_processor()
        @actions_extractor()
        @sentence_divider(
            faster_first_response=self._faster_first_response,
            segment_method=self._segment_method,
            valid_tags=["think"],
        )
        async def chat_with_memory(
            input_data: BatchInput,
        ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
            """Process chat with memory and tools."""
            self.reset_interrupt()
            self.prompt_mode_flag = False

            # Persistent memory: let the prior context compact (topic change /
            # buffer full) before recording the new user turn, then refresh the
            # memory block injected into the system prompt for this turn.
            if self._memory_manager:
                user_text = self._to_text_prompt(input_data)
                if user_text:
                    try:
                        await self._memory_manager.maybe_compact_context(user_text)
                        self._memory_manager.add_user_message(user_text)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"Memory: failed recording user turn: {exc}")
            self._active_system = self._build_active_system()
            assistant_chunks: List[str] = []

            messages = self._to_messages(input_data)
            tools = None
            tool_mode = None
            llm_supports_native_tools = False

            if isinstance(self._llm, ClaudeAsyncLLM):
                tool_mode = "Claude"
                tools = self._formatted_tools_claude
                llm_supports_native_tools = True
            elif isinstance(self._llm, OpenAICompatibleAsyncLLM):
                tool_mode = "OpenAI"
                tools = self._formatted_tools_openai
                llm_supports_native_tools = True

            if self._use_mcpp and self._tool_manager:
                tools = None

                if isinstance(self._llm, ClaudeAsyncLLM):
                    tool_mode = "Claude"
                    tools = self._tool_manager.get_formatted_tools("Claude")
                    tools = self._with_internal_speak_tool(tools, "Claude")
                    llm_supports_native_tools = True
                elif isinstance(self._llm, OpenAICompatibleAsyncLLM):
                    tool_mode = "OpenAI"
                    tools = self._tool_manager.get_formatted_tools("OpenAI")
                    tools = self._with_internal_speak_tool(tools, "OpenAI")
                    llm_supports_native_tools = True
                else:
                    logger.warning(
                        f"LLM type {type(self._llm)} not explicitly handled for tool mode determination."
                    )

                if llm_supports_native_tools and not tools:
                    logger.warning(
                        f"No tools available/formatted for '{tool_mode}' mode, despite MCP being enabled."
                    )
                elif llm_supports_native_tools:
                    logger.info(
                        f"Using {len(tools)} formatted tools for {tool_mode} mode."
                    )

            if tool_mode == "Claude" and tools:
                logger.debug(
                    f"Starting Claude tool interaction loop with {len(tools)} tools."
                )
                async for output in self._claude_tool_interaction_loop(
                    messages, tools if tools else []
                ):
                    if isinstance(output, str):
                        assistant_chunks.append(output)
                    elif isinstance(output, dict):
                        if output.get("type") == "agent_text":
                            assistant_chunks.append(output.get("content", ""))
                        elif output.get("type") == "speak_text":
                            assistant_chunks.append(output.get("text", ""))
                    yield output
                self._record_assistant_memory(assistant_chunks)
                return
            elif tool_mode == "OpenAI" and tools:
                logger.debug(
                    f"Starting OpenAI tool interaction loop with {len(tools)} tools."
                )
                async for output in self._openai_tool_interaction_loop(
                    messages, tools if tools else []
                ):
                    if isinstance(output, str):
                        assistant_chunks.append(output)
                    elif isinstance(output, dict):
                        if output.get("type") == "agent_text":
                            assistant_chunks.append(output.get("content", ""))
                        elif output.get("type") == "speak_text":
                            assistant_chunks.append(output.get("text", ""))
                    yield output
                self._record_assistant_memory(assistant_chunks)
                return
            else:
                logger.info("Starting simple chat completion.")
                base_system = self._active_system or self._system
                current_system_prompt = (
                    self._build_system_prompt_with_workspace_context(
                        messages,
                        base_system,
                    )
                )
                token_stream = self._llm.chat_completion(
                    messages, current_system_prompt
                )
                complete_response = ""
                async for event in token_stream:
                    text_chunk = ""
                    if isinstance(event, dict) and event.get("type") == "text_delta":
                        text_chunk = event.get("text", "")
                    elif isinstance(event, str):
                        text_chunk = event
                    else:
                        continue
                    if text_chunk:
                        text_event = self._make_agent_text_event(text_chunk)
                        if text_event:
                            yield text_event
                        complete_response += text_chunk
                if complete_response:
                    self._add_message(complete_response, "assistant")
                    self._record_assistant_memory([complete_response])

        return chat_with_memory

    async def run_memory_startup(self) -> None:
        """Promote past daily memory into long-term memory at startup.

        Drives the Daily -> Long-term (and Long-term -> compact) flows once when
        the agent comes up, so they run without an external scheduler.
        """
        if not self._memory_manager:
            return
        try:
            self._memory_manager.note_session_start()
            await self._memory_manager.startup_compaction()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Memory: startup compaction failed: {exc}")

    def _record_assistant_memory(self, chunks: List[str]) -> None:
        """Record the assistant's reply into persistent memory (best-effort)."""
        if not self._memory_manager:
            return
        text = "".join(chunks).strip()
        if not text:
            return
        try:
            self._memory_manager.add_assistant_message(text)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Memory: failed recording assistant turn: {exc}")

    async def chat(
        self,
        input_data: BatchInput,
    ) -> AsyncIterator[Union[SentenceOutput, Dict[str, Any]]]:
        """Run chat pipeline."""
        chat_func_decorated = self._chat_function_factory()
        async for output in chat_func_decorated(input_data):
            yield output

    def reset_interrupt(self) -> None:
        """Reset interrupt flag."""
        self._interrupt_handled = False

    def start_group_conversation(
        self, human_name: str, ai_participants: List[str]
    ) -> None:
        """Start a group conversation."""
        if not self._tool_prompts:
            logger.warning("Tool prompts dictionary is not set.")
            return

        other_ais = ", ".join(name for name in ai_participants)
        prompt_name = self._tool_prompts.get("group_conversation_prompt", "")

        if not prompt_name:
            logger.warning("No group conversation prompt name found.")
            return

        try:
            group_context = prompt_loader.load_util(prompt_name).format(
                human_name=human_name, other_ais=other_ais
            )
            self._memory.append({"role": "user", "content": group_context})
        except FileNotFoundError:
            logger.error(f"Group conversation prompt file not found: {prompt_name}")
        except KeyError as e:
            logger.error(f"Missing formatting key in group conversation prompt: {e}")
        except Exception as e:
            logger.error(f"Failed to load group conversation prompt: {e}")
