import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

model = "gemini-2.5-flash"

def my_func(arg1: str):
    return f"Got {arg1}"

config = types.GenerateContentConfig(
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    tools=[my_func]
)

chat = client.chats.create(model=model, config=config)
resp = chat.send_message("Call my_func with arg1='hello'")

try: text1 = resp.text or ""
except ValueError: text1 = ""
print("First Response Text:", text1)

if resp.function_calls:
    for call in resp.function_calls:
        print("Function call:", call.name)
        
    responses = []
    for call in resp.function_calls:
        responses.append(
            types.Part(
                function_response=types.FunctionResponse(
                    name=call.name,
                    response={"result": "It is sunny and 25 degrees"}
                )
            )
        )
    
    print("Sending function response...")
    resp2 = chat.send_message(responses)
    try: text2 = resp2.text or ""
    except ValueError: text2 = ""
    print("Second Response Text:", text2)
