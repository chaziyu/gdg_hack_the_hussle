import os
import json
import asyncio
import requests
import tempfile
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Setup paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Constants
KB_FILE = os.path.join(BASE_DIR, 'local_storage', 'knowledge_base.json')
MODEL = "gemini-flash-lite-latest"
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Initialize Gemini Client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Message Buffer for Batching
message_buffer = {}
MESSAGE_LIMIT = 50
WEBHOOK_URL = os.environ.get("GOOGLE_WEBHOOK_URL")

def _get_kb():
    if not os.path.exists(KB_FILE):
        return {"event_planning": {}, "timeline_tasks": []}
    try:
        with open(KB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"event_planning": {}, "timeline_tasks": []}

def _sync_to_sheets(tasks):
    if not WEBHOOK_URL:
        return
    try:
        # Convert list of dicts to list of lists for Google Sheets
        if not tasks:
            return
        
        # Normalize keys and collect headers
        normalized_tasks = []
        headers_set = set()
        
        for t in tasks:
            norm_t = {}
            for k, v in t.items():
                clean_key = str(k).replace('_', ' ').title()
                if clean_key == 'Task Name': clean_key = 'Task'
                norm_t[clean_key] = v
                headers_set.add(clean_key)
            normalized_tasks.append(norm_t)
            
        # Standard column order
        standard_order = ['Task', 'Assignee', 'Status', 'Deadline', 'Time', 'Notes']
        headers = []
        for h in standard_order:
            if h in headers_set:
                headers.append(h)
                headers_set.remove(h)
        headers.extend(sorted(list(headers_set)))
        
        rows = [headers]
        for t in normalized_tasks:
            rows.append([str(t.get(h, '')) for h in headers])
            
        requests.post(WEBHOOK_URL, json=rows, timeout=15)
    except Exception as e:
        print(f"Error syncing to Google Sheets: {e}")

def _save_kb(kb):
    try:
        # Simple atomic-ish save
        temp_file = KB_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(kb, f, indent=4)
        os.replace(temp_file, KB_FILE)
        
        # Sync to sheets
        _sync_to_sheets(kb.get('timeline_tasks', []))
        return True
    except Exception as e:
        print(f"Error saving knowledge base: {e}")
        return False

async def process_batch(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, is_manual: bool = False):
    messages = message_buffer.get(chat_id, [])
    if not messages:
        if is_manual:
            await context.bot.send_message(chat_id, "No new messages to summarize.")
        return

    chat_transcript = "\n".join(messages)
    
    kb = _get_kb()
    tasks = kb.get('timeline_tasks', [])

    prompt = f"""
    You are an expert Project Manager agent for 'DriveBot'. 
    Analyze this chat transcript and extract any decisions or new tasks.
    
    Chat Transcript:
    {chat_transcript}
    
    Current project tasks:
    {json.dumps(tasks, indent=2)}
    
    INSTRUCTIONS:
    - If the transcript requests a status update or implies a new task, update or append to the task list.
    - If the transcript does not imply a task update or new decision, return "NO_ACTION".
    - If an update is needed, return ONLY a valid JSON array containing the ENTIRE updated task list.
    - VERY IMPORTANT: Format the keys exactly as: "Task", "Assignee", "Status", "Deadline", "Notes". Do not use lowercase or other variations.
    - REFINEMENT RULES:
      1. Write tasks as clear, actionable commands starting with a verb (e.g., "Design poster" instead of "poster thing").
      2. Combine duplicate or highly similar tasks into a single comprehensive task.
      3. If an assignee or deadline is unknown, leave the string empty ("") rather than writing "TBC" or "Ongoing".
      4. Keep "Notes" extremely concise (max 1 sentence).
      5. Format any "Deadline" strictly as "YYYY-MM-DD" (e.g. "2024-08-23"). If the exact year is unknown, assume the current year. If no date is found, leave it empty ("").
    - Do not include any explanations or markdown formatting outside the JSON unless returning "NO_ACTION".
    """
    
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt
        )
        
        resp_text = response.text.strip()
        
        if "NO_ACTION" in resp_text:
            if is_manual: 
                await context.bot.send_message(chat_id, "Transcript processed, but no task updates were needed.")
        else:
            # Clean JSON response
            if "```json" in resp_text:
                resp_text = resp_text.split("```json")[1].split("```")[0].strip()
            elif "```" in resp_text:
                resp_text = resp_text.split("```")[1].strip()

            new_tasks = json.loads(resp_text)
            
            if isinstance(new_tasks, list):
                kb['timeline_tasks'] = new_tasks
                if _save_kb(kb):
                    await context.bot.send_message(chat_id, "✅ DriveBot has processed the batch and updated the timeline!")
                else:
                    await context.bot.send_message(chat_id, "⚠️ I tried to update the timeline, but there was a database error.")
            else:
                if is_manual:
                    await context.bot.send_message(chat_id, "I understood the chat but had trouble formatting the update.")

    except Exception as e:
        print(f"ERROR: Telegram Listener/Gemini Error: {e}")
        if is_manual:
            await context.bot.send_message(chat_id, f"Sorry, I encountered an error: {str(e)}")
            
    # Empty the bucket
    message_buffer[chat_id] = []

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    document = update.message.document
    
    if not document.file_name.lower().endswith(('.pdf', '.txt')):
        return
        
    await context.bot.send_message(chat_id, f"📄 Received {document.file_name}. Reading document...")
    
    # 1. Download file locally
    file = await context.bot.get_file(document.file_id)
    ext = os.path.splitext(document.file_name)[1]
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        temp_path = tmp.name
        
    await file.download_to_drive(temp_path)
    
    try:
        # 2. Upload to Gemini
        uploaded_file = client.files.upload(path=temp_path, display_name=document.file_name)
        
        # 3. Analyze document
        kb = _get_kb()
        
        prompt = f"""
        You are DriveBot, an expert event planner.
        Please read the attached document.
        
        Extract any overarching event details and all specific timeline tasks mentioned.
        Return ONLY a raw JSON object in this exact format:
        {{
            "event_planning": {{
                "event_name": "...",
                "event_summary": "..."
            }},
            "timeline_tasks": [
                {{
                    "Task": "...",
                    "Assignee": "...",
                    "Status": "...",
                    "Deadline": "...",
                    "Notes": "..."
                }}
            ]
        }}
        
        REFINEMENT RULES:
        1. Write tasks as clear, actionable commands starting with a verb (e.g., "Draft sponsorship proposal" instead of "sponsors").
        2. Combine duplicate or highly similar tasks.
        3. If an assignee, deadline, or status is unspecified, leave the string empty ("") instead of writing "TBC", "N/A", or "Ongoing".
        4. Keep "Notes" extremely concise (max 1 sentence).
        5. Format any "Deadline" strictly as "YYYY-MM-DD" (e.g. "2024-08-23"). If the exact year is unknown, assume the current year. If no date is found, leave it empty ("").
        """
        
        response = client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type),
                prompt
            ]
        )
        
        # 4. Clean JSON and update Knowledge Base
        resp_text = response.text.strip()
        if "```json" in resp_text:
            resp_text = resp_text.split("```json")[1].split("```")[0].strip()
        elif "```" in resp_text:
            resp_text = resp_text.split("```")[1].strip()
            
        new_data = json.loads(resp_text)
        
        if "timeline_tasks" in new_data:
            existing_tasks = kb.get('timeline_tasks', [])
            kb['timeline_tasks'] = existing_tasks + new_data['timeline_tasks']
            
        if "event_planning" in new_data:
            existing_plan = kb.get('event_planning', {})
            existing_plan.update(new_data['event_planning'])
            kb['event_planning'] = existing_plan
            
        if _save_kb(kb):
            await context.bot.send_message(chat_id, "✅ Document analyzed! Timeline updated in Google Sheets.")
        
    except Exception as e:
        print(f"Error processing document: {e}")
        await context.bot.send_message(chat_id, f"⚠️ Error processing document: {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    chat_type = update.message.chat.type
    chat_id = update.message.chat_id
    user = update.message.from_user.first_name if update.message.from_user else "User"
    
    is_private = chat_type == 'private'
    is_tagged = '@DriveBot' in text
    
    if chat_id not in message_buffer:
        message_buffer[chat_id] = []
        
    # Append message to buffer
    message_buffer[chat_id].append(f"{user}: {text}")
    
    # Process batch if threshold is met, or if it's a private chat/tag (for immediate response)
    if len(message_buffer[chat_id]) >= MESSAGE_LIMIT or is_private or is_tagged:
        await process_batch(update, context, chat_id, is_manual=is_private or is_tagged)

async def summarize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    await context.bot.send_message(chat_id, "Processing recent messages...")
    await process_batch(update, context, chat_id, is_manual=True)

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not found in environment.")
        exit(1)
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("summarize", summarize_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_chat))
    
    print("🎧 DriveBot Listener is now active...")
    print(f"DEBUG: Using model {MODEL}")
    print(f"DEBUG: Monitoring {'private messages & ' if True else ''}@DriveBot tags")
    
    app.run_polling()
