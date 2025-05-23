import openai
import os

def get_openai_response(messages):
    openai.api_key = os.getenv('OPENAI_API_KEY')
    kwargs = {
        "model": "gpt-4.1-2025-04-14",
        "messages": messages,
    }
    response = openai.chat.completions.create(**kwargs)
    return response.choices[0].message.content