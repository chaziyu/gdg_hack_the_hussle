import os
import json
import base64
import tempfile
import requests
import gspread
import pandas as pd
from docx import Document as DocxDocument
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types
from dotenv import load_dotenv
import datetime
from pydantic import BaseModel, Field

# Load environment variables
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'dist'), static_url_path='/')
CORS(app)

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# ── File paths & Constants ──────────────────────────────────────────────────────
DATA_DIR            = os.path.join(BASE_DIR, 'local_storage')
TIMELINE_TASKS_FILE = os.path.join(DATA_DIR, 'timeline_tasks.json')
EVENT_PLANNING_FILE = os.path.join(DATA_DIR, 'event_planning.json')
ARCHIVES_DIR        = os.path.join(DATA_DIR, 'archives')
HISTORY_DIR         = os.path.join(DATA_DIR, 'history')
CHAT_HISTORY_FILE   = os.path.join(HISTORY_DIR, 'chat_history.json')
FILE_INDEX_FILE     = os.path.join(ARCHIVES_DIR, 'file_index.json')

for d in [DATA_DIR, ARCHIVES_DIR, HISTORY_DIR]: os.makedirs(d, exist_ok=True)

MODEL = "gemini-3.1-flash-lite-preview"
FALLBACK_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]

# ── Structured Output Schemas (The Nested Hierarchy) ───────────────────────────

class TaskItem(BaseModel):
    task_name: str = Field(description="The name of the task")
    status: str = Field(description="Current status (e.g., Pending, Completed)")
    time: str = Field(description="Deadline or schedule time")

class SubEvent(BaseModel):
    sub_event_name: str = Field(description="Name of the specific program/activity (e.g., 'Colour Run', 'Station Game')")
    description: str = Field(description="What this specific sub-event is about")
    associated_files: list[str] = Field(description="List the exact filenames that contained information about this sub-event")
    tasks: list[TaskItem] = Field(description="Tasks specifically belonging to this sub-event")

class EventExtractionSchema(BaseModel):
    event_name: str = Field(description="The overarching macro-event name (e.g., Mental Health Week 2025)")
    event_summary: str = Field(description="A holistic summary of the entire event")
    sub_events: list[SubEvent] = Field(description="An array of all distinct sub-programs or activities.")

# ── Agent Tools (For Chat Interface) ──────────────────────────────────────────

def send_telegram_alert(message: str):
    """Send an immediate notification to the event committee via Telegram."""
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
    if not token or not chat_id: return "Telegram alert skipped: Config missing."
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": f"⚡ AGENT ALERT:\n{message}"}, timeout=10)
        return "Telegram alert sent successfully."
    except Exception as e: return f"Error: {e}"

def summarize_and_share_event():
    """Generate an event summary and broadcast it to Telegram."""
    if not os.path.exists(EVENT_PLANNING_FILE): return "Error: No planning data."
    try:
        with open(EVENT_PLANNING_FILE, 'r') as f: p = json.load(f)
        msg = (f"📢 **EVENT SUMMARY: {p.get('event_name', 'Event').upper()}**\n\n"
               f"📝 **Overview:**\n{p.get('event_summary', '')}\n\n"
               f"🔍 **Sub-Events Detected:** {len(p.get('sub_events', []))}")
        return send_telegram_alert(msg)
    except Exception as e: return f"Error: {e}"

AGENT_TOOLS = [send_telegram_alert, summarize_and_share_event]

# ── Core Routes ───────────────────────────────────────────────────────────────

@app.route('/api/generate', methods=['POST'])
def generate_knowledge():
    """Extracts data using the Detective Prompt and Nested Hierarchy."""
    files = request.files.getlist('files')
    if not files: return jsonify({"error": "No files"}), 400
    temp_paths, gemini_files, texts = [], [], []
    
    try:
        for f in files:
            ext = os.path.splitext(f.filename)[1].lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                f.save(tmp.name)
                temp_paths.append(tmp.name)
            
            # Explicitly label every single file for the AI
            file_header = f"\n\n{'='*40}\nSTART OF FILE: {f.filename}\n{'='*40}\n"
            
            if ext in {'.pdf', '.mp4', '.mp3', '.wav'}: 
                # Upload media directly to Gemini
                uploaded_file = client.files.upload(path=tmp.name, display_name=f.filename)
                gemini_files.append(uploaded_file)
            elif ext in {'.txt', '.csv', '.md'}: 
                texts.append(file_header + open(tmp.name, 'r', errors='ignore').read())
            elif ext in {'.docx', '.doc'}:
                try:
                    doc_text = "\n".join([p.text for p in DocxDocument(tmp.name).paragraphs])
                    texts.append(file_header + doc_text)
                except: pass
            elif ext in {'.xlsx', '.xls'}:
                try:
                    df_m = pd.read_excel(tmp.name, sheet_name=None)
                    excel_text = "\n".join([f"Sheet {k}:\n{v.to_csv()}" for k, v in df_m.items()])
                    texts.append(file_header + excel_text)
                except: pass

        instruction = (
            "You are a Master Event Architect. The user has uploaded a chaotic batch of unstructured files. "
            "Your job is to act as a detective and re-organize this data into a structured hierarchy.\n\n"
            "INSTRUCTIONS:\n"
            "1. MACRO EVENT: Look at all files to figure out the overarching mega-event.\n"
            "2. SUB-EVENTS: Identify distinct activities or programs (e.g., 'Colour Run', 'DIY Art Making'). "
            "Create a SubEvent object for each.\n"
            "3. CROSS-REFERENCING: Group information logically. If you see an attendance CSV with names, and a Word Doc about a 'Station Game', "
            "and they share context, link them together in the same SubEvent using the 'associated_files' array.\n"
            "4. TASKS: Place every task, flow, or logistical requirement under its correct SubEvent.\n\n"
            "CRITICAL: Pay close attention to the 'START OF FILE:' headers to know where information came from."
        )
        
        contents = [instruction] + gemini_files + [types.Part(text="\n\n".join(texts))]

        print("DEBUG: Requesting structured extraction from Gemini...")
        response = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=EventExtractionSchema,
                temperature=0.2 
            )
        )
        
        extracted_data = json.loads(response.text)
        
        # 1. Save Holistic Event Data
        with open(EVENT_PLANNING_FILE, 'w', encoding='utf-8') as f:
            json.dump(extracted_data, f, indent=4)
            
        # 2. Extract a Flat Task List (For Backward Compatibility with the /reminders endpoint)
        flat_tasks = []
        for sub_event in extracted_data.get("sub_events", []):
            for task in sub_event.get("tasks", []):
                flat_tasks.append({
                    "SubEvent": sub_event.get("sub_event_name"),
                    "Task": task.get("task_name"),
                    "Status": task.get("status"),
                    "Time": task.get("time")
                })
        with open(TIMELINE_TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(flat_tasks, f, indent=4)

        # 3. Save to File Index
        file_index = json.load(open(FILE_INDEX_FILE, 'r')) if os.path.exists(FILE_INDEX_FILE) else []
        for uf in files: file_index.append({"original_name": uf.filename, "timestamp": datetime.datetime.now().isoformat()})
        with open(FILE_INDEX_FILE, 'w') as fi: json.dump(file_index, fi, indent=4)

        return jsonify({"message": "Knowledge Database unified.", "data": extracted_data}), 200

    except Exception as e: 
        print(f"GENERATION CRASH: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        for p in temp_paths: 
            if os.path.exists(p): os.remove(p)

@app.route('/api/chat', methods=['POST'])
def chat():
    """A clean chat endpoint that allows the AI to converse based on the new nested data."""
    msg = request.get_json().get('message')
    if not msg: return jsonify({"error": "No message"}), 400
    
    try:
        history = []
        if os.path.exists(CHAT_HISTORY_FILE):
            raw = json.load(open(CHAT_HISTORY_FILE, 'r'))
            for item in raw[-20:]:
                history.append(types.Content(role=item['role'], parts=[types.Part(text=p['text']) for p in item['parts'] if 'text' in p]))

        # Provide the full nested JSON to the chat context so the AI knows exactly how the event is structured
        event_db = json.load(open(EVENT_PLANNING_FILE)) if os.path.exists(EVENT_PLANNING_FILE) else {}
        current_context = f"\n[SYSTEM MEMORY: Current Event Database:\n{json.dumps(event_db, indent=2)}]"
        
        sess = client.chats.create(
            model=MODEL, 
            history=history,
            config=types.GenerateContentConfig(tools=AGENT_TOOLS)
        )
        
        response = sess.send_message(msg + current_context)
        resp_text = response.text or ""
        
        history_to_save = json.load(open(CHAT_HISTORY_FILE, 'r')) if os.path.exists(CHAT_HISTORY_FILE) else []
        history_to_save.append({"role": "user", "parts": [{"text": msg}]})
        history_to_save.append({"role": "model", "parts": [{"text": resp_text or "Action completed."}]})
        with open(CHAT_HISTORY_FILE, 'w') as f: json.dump(history_to_save, f, indent=4)

        return jsonify({"response": resp_text.strip() or "Action completed."}), 200

    except Exception as e: 
        return jsonify({"error": str(e)}), 500

# ── API Utility Routes ────────────────────────────────────────────────────────

@app.route('/api/actions/reminders', methods=['GET'])
def api_reminders():
    if not os.path.exists(TIMELINE_TASKS_FILE): return jsonify({"message": "No tasks"}), 404
    tasks = json.load(open(TIMELINE_TASKS_FILE, 'r'))
    
    formatted_tasks = []
    for t in tasks:
        task_str = f"[{t.get('SubEvent', 'General')}] • {t.get('Task', 'Unnamed')} ({t.get('Status', 'Pending')}) - {t.get('Time', '')}"
        formatted_tasks.append(task_str)
        
    msg = "📅 **FULL TASK LIST**\n" + "\n".join(formatted_tasks)
    send_telegram_alert(msg)
    return jsonify({"message": "Reminders sent!"})

@app.route('/api/knowledge', methods=['GET'])
def get_knowledge():
    t = json.load(open(TIMELINE_TASKS_FILE, 'r')) if os.path.exists(TIMELINE_TASKS_FILE) else []
    p = json.load(open(EVENT_PLANNING_FILE, 'r')) if os.path.exists(EVENT_PLANNING_FILE) else {}
    return jsonify({"timeline_tasks": t, "event_planning": p}), 200

@app.route('/api/chat/clear', methods=['POST'])
def clear_history():
    if os.path.exists(CHAT_HISTORY_FILE): os.remove(CHAT_HISTORY_FILE)
    return jsonify({"message": "History cleared"}), 200

@app.route('/api/knowledge/clear', methods=['POST'])
def api_clear_knowledge():
    for f, default in [(TIMELINE_TASKS_FILE, []), (EVENT_PLANNING_FILE, {})]:
        if os.path.exists(f): json.dump(default, open(f, 'w'))
    return jsonify({"message": "Databases reset"}), 200

if __name__ == '__main__':
    from waitress import serve as ws
    print("🚀 Unified Server starting on http://localhost:5000")
    ws(app, host='0.0.0.0', port=5000)