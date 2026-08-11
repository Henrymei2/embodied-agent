import abc
from typing import AsyncIterator, List, Dict, Any, Literal


class StatelessLLMInterface(metaclass=abc.ABCMeta):
    """
    Interface for a stateless language model.

    The word "stateless" means that the language model does not store memory,
    system prompts, or user messages, which is most of the LLM. If we send a
    message to the LLM, its response will be based on the message parameter alone.

    The StatelessLLMInterface class provides a method for generating chat
    completions asynchronously.

    We use StatelessLLMs to initialize Agents, which pack the StatelessLLM with
    memory, system prompts, and other features.

    """

    def get_tool_mode(self) -> Literal["OpenAI", "Claude"] | None:
        """Describe the native tool protocol implemented by this model.

        Wrappers should delegate this capability to the model they wrap. Keeping
        it on the interface avoids brittle ``isinstance`` checks that stop
        working as soon as resilience, tracing, or routing decorators are added.
        """
        return None

    async def json_completion(
        self,
        messages: List[Dict[str, Any]],
        system: str = None,
    ) -> AsyncIterator[Any]:
        """Stream a JSON-only completion, with provider tuning when available.

        Providers without a dedicated structured-output mode retain the normal
        chat behavior. Implementations may override this to disable expensive
        hidden reasoning or request a native JSON response format.
        """
        async for event in self.chat_completion(messages, system=system):
            yield event

    @abc.abstractmethod
    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        system: str = None,
        tools: List[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """
        Generates a chat completion asynchronously and return an iterator to the response.
        This function does not store memory or user messages.

        Parameters:
        - messages (List[Dict[str, Any]]): The list of messages to send to the API.
        - system (str, optional): System prompt to use for this completion.
        - tools (List[Dict[str, str]], optional): List of tools to use for this completion.
            - Each tool should follow the format:
            {
                "name": "tool_name",
                "description": "tool_description",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param1": {
                            "type": "string",
                            "description": "Description of param1"
                        },
                        "param2": {
                            "type": "integer",
                            "description": "Description of param2"
                        }
                    },
                    "required": ["param1"]
                }
            }

        Yields:
        - str: The content of each chunk from the API response.

        Raises:
        - APIConnectionError: When the server cannot be reached
        - RateLimitError: When a 429 status code is received
        - APIError: For other API-related errors
        """
        raise NotImplementedError
