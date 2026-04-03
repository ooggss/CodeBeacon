from anthropic import types as anthropic_types
from typing import List, Dict


async def chat_completion(model: str, messages: List[Dict]):
    # TODO: add the generation logic here

    return {
        "messages": messages,
        "text": text_content,
        "reasoning": thinking_content,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
        },
    }
