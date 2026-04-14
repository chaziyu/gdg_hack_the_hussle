import os
import json
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from google import genai
from dotenv import load_dotenv

# Setup paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Constants
KB_FILE = os.path.join(BASE_DIR, 'local_storage', 'knowledge_base.json')
MODEL = "gemini-3.1-flash-lite-preview"
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Initialize Gemini Client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

def _get_kb():
    if not os.path.exists(KB_FILE):
        return {"event_planning": {}, "timeline_tasks": []}
    try:
        with open(KB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"event_planning": {}, "timeline_tasks": []}

def _save_kb(kb):
    try:
        # Simple atomic-ish save
        temp_file = KB_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(kb, f, indent=4)
        os.replace(temp_file, KB_FILE)
        return True
    except Exception as e:
        print(f"Error saving knowledge base: {e}")
        return False

async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    chat_type = update.message.chat.type
    
    is_private = chat_type == 'private'
    is_tagged = '@DriveBot' in text
    
    # Only react if it's a private chat or the bot is tagged in a group
    if is_private or is_tagged:
        kb = _get_kb()
        tasks = kb.get('timeline_tasks', [])
        
        # 1. Pass the chat to Gemini to determine the intent
        prompt = f"""
        You are an expert Project Manager agent for 'DriveBot'. 
        The user said: "{text}"
        
        Current project tasks:
        {json.dumps(tasks, indent=2)}
        
        INSTRUCTIONS:
        - If the user is requesting a status update for a task (e.g., 'mark task X as completed', 'I finished Y'), update the 'status' field.
        - The valid status values are usually 'To Do', 'In Progress', or 'Completed'.
        - If the user's message does not imply a task update or status change, return "NO_ACTION".
        - If an update is needed, return ONLY a valid JSON array containing the ENTIRE updated task list.
        - Do not include any explanations or markdown formatting outside the JSON unless returning "NO_ACTION".
        """
        
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt
            )
            
            resp_text = response.text.strip()
            
            if "NO_ACTION" in resp_text:
                if is_private: # Only reply in private if there's no action to avoid spam in groups
                    await update.message.reply_text("I noted that, but no task updates were needed.")
                return

            # Clean JSON response
            if "```json" in resp_text:
                resp_text = resp_text.split("```json")[1].split("```")[0].strip()
            elif "```" in resp_text:
                resp_text = resp_text.split("```")[1].strip()

            new_tasks = json.loads(resp_text)
            
            # Simple validation: ensure it's a list
            if isinstance(new_tasks, list):
                kb['timeline_tasks'] = new_tasks
                if _save_kb(kb):
                    await update.message.reply_text("✅ DriveBot has updated the timeline based on our chat!")
                else:
                    await update.message.reply_text("⚠️ I tried to update the timeline, but there was a database error.")
            else:
                print(f"DEBUG: Unexpected AI response format: {resp_text}")
                if is_private:
                    await update.message.reply_text("I understood your request but had trouble formatting the update.")

        except Exception as e:
            print(f"ERROR: Telegram Listener/Gemini Error: {e}")
            if is_private:
                await update.message.reply_text(f"Sorry, I encountered an error: {str(e)}")

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not found in environment.")
        exit(1)
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_chat))
    
    print("🎧 DriveBot Listener is now active...")
    print(f"DEBUG: Using model {MODEL}")
    print(f"DEBUG: Monitoring {'private messages & ' if True else ''}@DriveBot tags")
    
    app.run_polling()
