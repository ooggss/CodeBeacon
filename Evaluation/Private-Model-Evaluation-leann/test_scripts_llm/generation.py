
def generate_with_messages(model_name, messages, prompt_id, tags=[], args={}):
    # TODO: add the generation logic here
    result = {
        "messages": messages,
        "reasoning": reasoning,
        "text": text,
        "usage_info": usage_info,
        "model_name": model_name
    }
    return result

def generate_with_private_messages(model_name, messages, prompt_id, tags=[]):
    # TODO: add the generation logic here
    result = {
        "messages": messages,
        "reasoning": reasoning_content,
        "text": text,
        "usage_info": usage_info,
        "model_name": model_name
    }    
    return result
