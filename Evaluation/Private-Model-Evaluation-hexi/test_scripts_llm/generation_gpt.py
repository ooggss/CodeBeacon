from typing import Dict, List

async def chat_completion(model, messages: List[Dict]):
    # TODO: add the generation logic here
    return {
        "messages": messages,
        "text": text,
        "reasoning": "",
        "usage_info": usage_info,
    }