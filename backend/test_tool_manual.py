import os
import time
from google import genai
from google.genai import types

from dotenv import load_dotenv
load_dotenv('../.env')

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

def dummy_tool(x: int):
    """Returns x + 1"""
    return x + 1

try:
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents="Call dummy_tool with 5",
        config=types.GenerateContentConfig(
            tools=[dummy_tool],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
    )
    print("SUCCESS", response.text)
    if response.function_calls:
        print("Function calls:", [(c.name, c.args) for c in response.function_calls])
except Exception as e:
    print("ERROR", repr(e))
