"""Description: This file contains the implementation of the `AsyncLLM` class.
This class is responsible for handling asynchronous interaction with OpenAI API compatible
endpoints for language generation.
"""

from typing import AsyncIterator, List, Dict, Any
from openai import (
    AsyncStream,
    AsyncOpenAI,
    APIError,
    APIConnectionError,
    RateLimitError,
    NotGiven,
    NOT_GIVEN,
)
from openai.types.chat import ChatCompletionChunk
from loguru import logger

from .stateless_llm_interface import StatelessLLMInterface
from .request_logging import log_llm_request
from ...mcpp.types import ToolCallObject
from ...mcpp.dsml_parser import DSMLStreamParser


class AsyncLLM(StatelessLLMInterface):
    def __init__(
        self,
        model: str,
        base_url: str,
        llm_api_key: str = "z",
        organization_id: str = "z",
        project_id: str = "z",
        temperature: float = 1.0,
    ):
        """
        Initializes an instance of the `AsyncLLM` class.

        Parameters:
        - model (str): The model to be used for language generation.
        - base_url (str): The base URL for the OpenAI API.
        - organization_id (str, optional): The organization ID for the OpenAI API. Defaults to "z".
        - project_id (str, optional): The project ID for the OpenAI API. Defaults to "z".
        - llm_api_key (str, optional): The API key for the OpenAI API. Defaults to "z".
        - temperature (float, optional): What sampling temperature to use, between 0 and 2. Defaults to 1.0.
        """
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.client = AsyncOpenAI(
            base_url=base_url,
            organization=organization_id,
            project=project_id,
            api_key=llm_api_key,
        )
        self.support_tools = True

        logger.info(
            f"Initialized AsyncLLM with the parameters: {self.base_url}, {self.model}"
        )

    def get_tool_mode(self):
        return "OpenAI"

    def _prepare_messages_for_request(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Complete DeepSeek thinking-mode tool history without mutating callers.

        DeepSeek requires ``reasoning_content`` on every replayed assistant
        tool-call message, including turns where the streamed response omitted
        or the SDK did not expose any reasoning tokens. Other compatible APIs
        may reject this vendor field, so the repair is deliberately scoped to
        DeepSeek endpoints/models.
        """
        provider_hint = f"{self.base_url} {self.model}".lower()
        if "deepseek" not in provider_hint:
            return messages
        prepared: List[Dict[str, Any]] = []
        repaired = 0
        for message in messages:
            if (
                message.get("role") == "assistant"
                and message.get("tool_calls")
                and message.get("reasoning_content") is None
            ):
                copied = dict(message)
                copied["reasoning_content"] = ""
                prepared.append(copied)
                repaired += 1
            else:
                prepared.append(message)
        if repaired:
            logger.debug(
                "DeepSeek reasoning replay repaired {} assistant tool turns",
                repaired,
            )
        return prepared

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        system: str = None,
        tools: List[Dict[str, Any]] | NotGiven = NOT_GIVEN,
        *,
        _json_mode: bool = False,
    ) -> AsyncIterator[Any]:
        """
        Generates a chat completion using the OpenAI API asynchronously.

        Parameters:
        - messages (List[Dict[str, Any]]): The list of messages to send to the API.
        - system (str, optional): System prompt to use for this completion.
        - tools (List[Dict[str, str]], optional): List of tools to use for this completion.

        Yields:
        - str: The content of each chunk from the API response.
        - List[ChoiceDeltaToolCall]: The tool calls detected in the response.

        Raises:
        - APIConnectionError: When the server cannot be reached
        - RateLimitError: When a 429 status code is received
        - APIError: For other API-related errors
        """
        stream = None
        # Tool call related state variables
        accumulated_tool_calls = {}
        in_tool_call = False
        pending_dsml_calls: List[ToolCallObject] | None = None
        native_tool_calls_seen = False

        try:
            # If system prompt is provided, add it to the messages
            request_messages = self._prepare_messages_for_request(messages)
            messages_with_system = request_messages
            if system:
                messages_with_system = [
                    {"role": "system", "content": system},
                    *request_messages,
                ]

            available_tools = tools if self.support_tools else NOT_GIVEN
            # Some OpenAI-compatible DeepSeek endpoints serialize tool calls as
            # DSML inside `content` even when schemas were supplied. Enable the
            # compatibility parser only for a non-empty tool set so ordinary
            # assistant text is never interpreted as an action by a tool-less
            # request.
            dsml_parser = (
                DSMLStreamParser()
                if self.support_tools
                and available_tools is not NOT_GIVEN
                and bool(available_tools)
                else None
            )
            provider_hint = f"{self.base_url} {self.model}".lower()
            request_options: Dict[str, Any] = {}
            if _json_mode and "deepseek" in provider_hint:
                # DeepSeek V4 defaults to thinking mode. Agenda planners need a
                # small structured decision, not a long hidden reasoning pass.
                request_options = {
                    "response_format": {"type": "json_object"},
                    "extra_body": {"thinking": {"type": "disabled"}},
                }
            log_llm_request(
                provider="openai_compatible",
                model=self.model,
                endpoint=self.base_url,
                messages=messages_with_system,
                system=system,
                tools=available_tools,
                extra={
                    "stream": True,
                    "temperature": self.temperature,
                    "support_tools": self.support_tools,
                    "json_mode": _json_mode,
                    "thinking_mode": (
                        "disabled" if request_options.get("extra_body") else "default"
                    ),
                },
            )

            stream: AsyncStream[
                ChatCompletionChunk
            ] = await self.client.chat.completions.create(
                messages=messages_with_system,
                model=self.model,
                stream=True,
                temperature=self.temperature,
                tools=available_tools,
                **request_options,
            )

            async for chunk in stream:
                # Guard against chunks with missing choices field (e.g., from OpenWebUI)
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                reasoning_content = getattr(delta, "reasoning_content", None)
                if reasoning_content is None:
                    model_extra = getattr(delta, "model_extra", None)
                    if isinstance(model_extra, dict):
                        reasoning_content = model_extra.get("reasoning_content")
                if reasoning_content:
                    # Thinking-capable OpenAI-compatible APIs require this exact
                    # assistant field to be replayed after a tool call. Keep it
                    # internal: consumers persist it in the protocol message but
                    # never surface it as assistant text.
                    yield {
                        "type": "reasoning_content",
                        "text": str(reasoning_content),
                    }

                if self.support_tools:
                    has_tool_calls = (
                        hasattr(chunk.choices[0].delta, "tool_calls")
                        and chunk.choices[0].delta.tool_calls
                    )

                    if has_tool_calls:
                        native_tool_calls_seen = True
                        pending_dsml_calls = None
                        if dsml_parser is not None:
                            visible, dsml_calls = dsml_parser.finish()
                            if visible:
                                yield visible
                            if dsml_calls:
                                logger.info(
                                    "Discarding DSML compatibility calls because "
                                    "the provider also returned native tool calls"
                                )
                            dsml_parser = None
                        logger.debug(
                            f"Tool calls detected in chunk: {chunk.choices[0].delta.tool_calls}"
                        )
                        in_tool_call = True
                        # Process tool calls in the current chunk
                        for tool_call in chunk.choices[0].delta.tool_calls:
                            index = (
                                tool_call.index if hasattr(tool_call, "index") else 0
                            )

                            # Initialize tool call for this index if needed
                            if index not in accumulated_tool_calls:
                                accumulated_tool_calls[index] = {
                                    "index": index,
                                    "id": getattr(tool_call, "id", None),
                                    "type": getattr(tool_call, "type", None),
                                    "function": {"name": "", "arguments": ""},
                                }

                            # Update tool call information
                            if hasattr(tool_call, "id") and tool_call.id:
                                accumulated_tool_calls[index]["id"] = tool_call.id
                            if hasattr(tool_call, "type") and tool_call.type:
                                accumulated_tool_calls[index]["type"] = tool_call.type

                            # Update function information
                            if hasattr(tool_call, "function"):
                                if (
                                    hasattr(tool_call.function, "name")
                                    and tool_call.function.name
                                ):
                                    accumulated_tool_calls[index]["function"][
                                        "name"
                                    ] = tool_call.function.name
                                if (
                                    hasattr(tool_call.function, "arguments")
                                    and tool_call.function.arguments
                                ):
                                    accumulated_tool_calls[index]["function"][
                                        "arguments"
                                    ] += tool_call.function.arguments

                        continue

                    # If we were in a tool call but now we're not, yield the tool call result
                    elif in_tool_call and not has_tool_calls:
                        in_tool_call = False
                        # Convert accumulated tool calls to the required format and output
                        logger.info(f"Complete tool calls: {accumulated_tool_calls}")

                        # Use the from_dict method to create a ToolCallObject instance from a dictionary
                        complete_tool_calls = [
                            ToolCallObject.from_dict(tool_data)
                            for tool_data in accumulated_tool_calls.values()
                        ]

                        yield complete_tool_calls
                        accumulated_tool_calls = {}  # Reset for potential future tool calls

                # Process regular content chunks
                if len(chunk.choices) == 0:
                    logger.info("Empty chunk received")
                    continue
                elif chunk.choices[0].delta.content is None:
                    chunk.choices[0].delta.content = ""
                content = chunk.choices[0].delta.content
                if dsml_parser is None:
                    yield content
                else:
                    visible, dsml_calls = dsml_parser.feed(content)
                    if visible:
                        yield visible
                    if dsml_calls:
                        logger.info(
                            "Detected DSML text tool calls: "
                            + ", ".join(call.function.name for call in dsml_calls)
                        )
                        # Keep consuming the provider stream. Some endpoints
                        # emit both DSML and native tool_calls; native protocol
                        # data is authoritative and must not execute twice.
                        pending_dsml_calls = dsml_calls
                        dsml_parser = None

            # If stream ends while still in a tool call, make sure to yield the tool call
            if in_tool_call and accumulated_tool_calls:
                logger.info(f"Final tool call at stream end: {accumulated_tool_calls}")

                # Create a ToolCallObject instance from a dictionary using the from_dict method.
                complete_tool_calls = [
                    ToolCallObject.from_dict(tool_data)
                    for tool_data in accumulated_tool_calls.values()
                ]

                yield complete_tool_calls
                native_tool_calls_seen = True
                pending_dsml_calls = None

            if dsml_parser is not None:
                visible, dsml_calls = dsml_parser.finish()
                if visible:
                    yield visible
                if dsml_calls:
                    pending_dsml_calls = dsml_calls

            if pending_dsml_calls and not native_tool_calls_seen:
                logger.info(
                    "Converted final DSML text to native tool calls: "
                    + ", ".join(call.function.name for call in pending_dsml_calls)
                )
                yield pending_dsml_calls

        except APIConnectionError as e:
            logger.error(
                f"Error calling the chat endpoint: Connection error. Failed to connect to the LLM API. \nCheck the configurations and the reachability of the LLM backend. \nSee the logs for details. \nTroubleshooting with documentation: https://open-llm-vtuber.github.io/docs/faq#%E9%81%87%E5%88%B0-error-calling-the-chat-endpoint-%E9%94%99%E8%AF%AF%E6%80%8E%E4%B9%88%E5%8A%9E \n{e.__cause__}"
            )
            yield {
                "type": "error",
                "message": "The language model connection failed.",
                "error_kind": "connection",
            }

        except RateLimitError as e:
            logger.error(
                f"Error calling the chat endpoint: Rate limit exceeded: {e.response}"
            )
            yield {
                "type": "error",
                "message": "The language model rate limit was exceeded.",
                "error_kind": "rate_limit",
            }

        except APIError as e:
            if "does not support tools" in str(e):
                self.support_tools = False
                logger.warning(
                    f"{self.model} does not support tools. Disabling tool support."
                )
                yield "__API_NOT_SUPPORT_TOOLS__"
                return
            logger.error(f"LLM API: Error occurred: {e}")
            logger.info(f"Base URL: {self.base_url}")
            logger.info(f"Model: {self.model}")
            logger.info(
                "Message count: {}; roles: {}",
                len(messages),
                [str(message.get("role", "")) for message in messages],
            )
            logger.info(f"temperature: {self.temperature}")
            yield {
                "type": "error",
                "message": "The language model rejected the request.",
                "error_kind": "api_error",
            }

        finally:
            # make sure the stream is properly closed
            # so when interrupted, no more tokens will being generated.
            if stream:
                logger.debug("Chat completion finished.")
                await stream.close()
                logger.debug("Stream closed.")

    async def json_completion(
        self,
        messages: List[Dict[str, Any]],
        system: str = None,
    ) -> AsyncIterator[Any]:
        """Use the provider's fast structured path for planner decisions."""
        async for event in self.chat_completion(
            messages,
            system=system,
            _json_mode=True,
        ):
            yield event
