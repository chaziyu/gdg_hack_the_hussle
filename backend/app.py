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
import re

# Load environment variables
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'dist'), static_url_path='/')
# Enable CORS for the React frontend
CORS(app)

# Initialize the Gemini client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Initialize Google Sheets & Calendar constants
SHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "service_account.json")
SCOPES_SHEETS = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

def get_sheets_client():
    if not os.path.exists(SERVICE_ACCOUNT_FILE): return None
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Error authorizing Google Sheets: {e}")
        return None

# Initialize Telegram constants
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")

# ── Model Configuration ────────────────────────────────────────────────────────
MODEL = "gemini-3.1-flash-lite-preview"
FALLBACK_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-3-flash-preview"]

# ── Structured Output Schema (Raw Dictionary to bypass Pydantic bug) ──
EVENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "event_name": {"type": "STRING", "description": "The overarching macro-event name (e.g., Mental Health Week 2025)"},
        "event_summary": {"type": "STRING", "description": "A holistic summary of the entire event"},
        "sub_events": {
            "type": "ARRAY",
            "description": "An array of all distinct sub-programs or activities.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "sub_event_name": {"type": "STRING", "description": "Name of the specific program/activity"},
                    "description": {"type": "STRING", "description": "What this specific sub-event is about"},
                    "associated_files": {
                        "type": "ARRAY",
                        "description": "List the exact filenames that contained information about this sub-event",
                        "items": {"type": "STRING"}
                    },
                    "tasks": {
                        "type": "ARRAY",
                        "description": "Tasks specifically belonging to this sub-event",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "task_name": {"type": "STRING", "description": "The name of the task"},
                                "status": {"type": "STRING", "description": "Current status (e.g., Pending, In Progress, Completed)"},
                                "time": {"type": "STRING", "description": "General schedule time (e.g., 7:00 AM)"},
                                "assigned_to": {"type": "STRING", "description": "The person or team responsible for the task"},
                                "deadline": {"type": "STRING", "description": "Specific deadline date/time (if any)"}
                            },
                            "required": ["task_name", "status"]
                        }
                    }
                },
                "required": ["sub_event_name", "description", "tasks"]
            }
        }
    },
    "required": ["event_name", "event_summary", "sub_events"]
}

# ── File paths ─────────────────────────────────────────────────────────────────
DATA_DIR            = os.path.join(BASE_DIR, 'local_storage')
CHAT_LOGS_FILE      = os.path.join(DATA_DIR, 'chat_logs.txt')
KNOWLEDGE_BASE_FILE = os.path.join(DATA_DIR, 'knowledge_base.json')
ARCHIVES_DIR        = os.path.join(DATA_DIR, 'archives')
HISTORY_DIR         = os.path.join(DATA_DIR, 'history')
CHAT_HISTORY_FILE   = os.path.join(HISTORY_DIR, 'chat_history.json')
FILE_INDEX_FILE     = os.path.join(ARCHIVES_DIR, 'file_index.json')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(ARCHIVES_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)

# ── Long-Context Caching Metadata ──────────────────────────────────────────────
CACHE_METADATA_FILE = os.path.join(DATA_DIR, 'cache_metadata.json')

def get_cache_metadata():
    if not os.path.exists(CACHE_METADATA_FILE): return {}
    try:
        with open(CACHE_METADATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return {}

def save_cache_metadata(metadata):
    try:
        with open(CACHE_METADATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=4)
    except Exception as e:
        print(f"Error saving cache metadata: {e}")

def get_valid_cache(model_id):
    metadata = get_cache_metadata()
    cache_info = metadata.get(model_id)
    if not cache_info: return None
    
    # Check if expired
    expiry = datetime.datetime.fromisoformat(cache_info['expiry'])
    if datetime.datetime.now() > expiry:
        return None
    return cache_info['name']

# ── Allowed file types ─────────────────────────────────────────────────────────
ALLOWED_EXTENSIONS = {
    '.pdf': 'application/pdf', '.doc': 'application/msword', 
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.txt': 'text/plain', '.csv': 'text/csv',
    '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm', '.avi': 'video/x-msvideo',
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
}
GEMINI_UPLOADABLE_MIME = {
    'application/pdf', 'text/plain', 'video/mp4', 'video/quicktime', 
    'video/webm', 'video/x-msvideo', 'audio/mpeg', 'audio/wav'
}
EXCEL_EXTENSIONS = {'.xls', '.xlsx', '.csv'}
DOCX_EXTENSIONS  = {'.doc', '.docx'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm', '.avi'}
AUDIO_EXTENSIONS = {'.mp3', '.wav'}

# ── Agent Tools (CRITICAL: NO DEFAULT VALUES IN SIGNATURES) ───────────────────

def _get_kb():
    if not os.path.exists(KNOWLEDGE_BASE_FILE): return {"event_planning": {}, "timeline_tasks": []}
    try:
        with open(KNOWLEDGE_BASE_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {"event_planning": {}, "timeline_tasks": []}

def _save_kb(kb):
    try:
        with open(KNOWLEDGE_BASE_FILE, 'w', encoding='utf-8') as f: json.dump(kb, f, indent=4)
        return True
    except: return False

def update_timeline_database(tasks: list[dict]):
    """Update the local timeline database with a list of tasks. 
    Expected schema for each task:
    {
        'task_name': str,    # e.g., 'Participant Registration' (The action/activity)
        'assigned_to': str,  # e.g., 'Logistics Team' or 'Zi Yu' (The person/group responsible)
        'status': str,       # e.g., 'To Do', 'In Progress', 'Completed'
        'time': str,         # e.g., '7:00 AM - 7:30 AM'
        'deadline': str      # e.g., '7:30 AM'
    }
    """
    kb = _get_kb()
    kb['timeline_tasks'] = tasks
    if _save_kb(kb): return "Local timeline database updated."
    return "Error updating timeline database."

def update_event_planning_database(plan: dict):
    """Update the local event planning database with overall event details.
    Expected schema: {'event_name': str, 'event_date': str, 'event_venue': str, 'event_summary': str, 'objectives': str}
    """
    kb = _get_kb()
    existing = kb.get('event_planning', {})
    
    new_name = plan.get('event_name')
    existing_name = existing.get('event_name')
    
    # Merge the new plan into existing data
    existing.update(plan)
    
    # Ensure event_name is not lost if the agent didn't provide it
    if not new_name and existing_name:
        existing['event_name'] = existing_name
        
    kb['event_planning'] = existing
    if _save_kb(kb): return "Local event planning database updated."
    return "Error updating event planning database."

def get_event_planning_database():
    """Retrieve the current overall event planning details from the database."""
    kb = _get_kb()
    return json.dumps(kb.get('event_planning', {}))


def sync_timeline_to_sheets():
    """Exports the current task timeline directly to the committee's Google Sheet."""
    if not SHEET_ID: return "Google Sheets Sync skipped: SPREADSHEET_ID missing from .env"
    gc = get_sheets_client()
    if not gc: return "Google Sheets Sync failed: Auth error (check service_account.json)."
    
    try:
        sh = gc.open_by_key(SHEET_ID)
        # Use simple sheet access as per blueprint, but with safety fallback
        try: worksheet = sh.sheet1
        except: worksheet = sh.get_worksheet(0)
        
        # Load local database
        kb = _get_kb()
        tasks = kb.get('timeline_tasks', [])
        
        # Format for Sheets (2D Array)
        # Header: Assigned To, Task Name, Status, Time/Deadline
        rows = [["Responsible Party", "Task / Activity", "Status", "Schedule / Deadline"]]
        for t in tasks:
            # Map existing KB keys to spreadsheet columns
            rows.append([
                t.get('assigned_to', 'Unassigned'),
                t.get('task_name', 'Unnamed Task'),
                t.get('status', 'To Do'),
                t.get('time') or t.get('deadline') or 'TBD'
            ])
            
        worksheet.clear() # Wipe old data
        worksheet.update('A1', rows) # Write new data
        
        return "Successfully synced the latest timeline to Google Sheets."
    except Exception as e:
        return f"Failed to sync to sheets: {str(e)}"

def sync_to_google_sheet(rows: list[list[str]], worksheet_name: str):
    """Generic tool to sync arbitrary data directly to a Google Sheet."""
    # Handle default value internally
    target_sheet = worksheet_name if worksheet_name else "Live_Data"
    if not SHEET_ID: return "Google Sheets Sync skipped: SPREADSHEET_ID missing."
    gc = get_sheets_client()
    if not gc: return "Google Sheets Sync failed: Auth error."
    try:
        sh = gc.open_by_key(SHEET_ID)
        try: worksheet = sh.worksheet(target_sheet)
        except gspread.WorksheetNotFound: worksheet = sh.add_worksheet(title=target_sheet, rows="100", cols="10")
        worksheet.clear()
        worksheet.update('A1', rows)
        return f"Synced to Google Sheet '{target_sheet}'."
    except Exception as e: return f"Error: {e}"

def send_telegram_alert(message: str):
    """Send an immediate notification to the event committee via Telegram."""
    load_dotenv(os.path.join(BASE_DIR, '.env'), override=True)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
    if not token or not chat_id: return "Telegram alert skipped: Config missing."
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id, 
            "text": message,
            "parse_mode": "Markdown"
        }
        r = requests.post(url, json=payload, timeout=10)
        return "Telegram alert sent successfully." if r.ok else f"Telegram error: {r.text}"
    except Exception as e: return f"Error: {e}"

def summarize_and_share_event():
    """Generate a high-level event summary and broadcast it to Telegram."""
    kb = _get_kb()
    p = kb.get('event_planning', {})
    if not p: return "Error: No planning data."
    
    e_name = p.get('event_name') or 'Event'
    e_date = p.get('event_date') or p.get('date') or 'TBD'
    e_venue = p.get('event_venue') or p.get('venue') or 'TBD'
    e_summary = p.get('event_summary') or p.get('summary') or 'No summary recorded.'
    
    msg = (f"⚡ *AGENT NOTIFICATION* ⚡\n"
           f"━━━━━━━━━━━━━━━━━━━━\n"
           f"🏆 *{e_name.upper()} UPDATE*\n"
           f"━━━━━━━━━━━━━━━━━━━━\n\n"
           f"📅 *Phase:* Event Overview\n"
           f"📍 *Venue:* {e_venue}\n"
           f"🗓️ *Date:* {e_date}\n\n"
           f"📝 *Overview:*\n"
           f"{e_summary}\n\n"
           f"✨ _Knowledge Database Synced_")
    return send_telegram_alert(msg)

AGENT_TOOLS = [
    update_timeline_database, update_event_planning_database, get_event_planning_database,
    sync_to_google_sheet, sync_timeline_to_sheets, send_telegram_alert,
    summarize_and_share_event
]

def execute_gemini_task(task_fn, *args, **kwargs):
    """
    Executes a Gemini task with automatic fallback through the model list.
    Automatically disables auto function calling and manually executes the tools.
    """
    model_chain = [MODEL] + FALLBACK_MODELS
    last_error = None
    
    for m_id in model_chain:
        try:
            print(f"DEBUG: Attempting task with model: {m_id}")
            if 'model' in kwargs: 
                kwargs['model'] = m_id
            
            cache_name = get_valid_cache(m_id)
            if 'config' in kwargs and hasattr(kwargs['config'], 'automatic_function_calling'):
                kwargs['config'].automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
            elif 'config' in kwargs:
                kwargs['config'].automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
            
            if cache_name:
                print(f"DEBUG: Using cache {cache_name} for model {m_id}")
                if 'config' in kwargs:
                    kwargs['config'].cached_content = cache_name
                    kwargs['config'].system_instruction = None
                    # Do NOT set tools to None here; they are needed for the model to act on the cache
                else:
                    kwargs['config'] = types.GenerateContentConfig(cached_content=cache_name, tools=AGENT_TOOLS, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
            
            # Initialize turn counter to prevent infinite loops
            turn_limit = 10
            current_turn = 0
            resp_text = ""
            
            # Initial generation
            resp = task_fn(*args, **kwargs)
            
            while current_turn < turn_limit:
                current_turn += 1
                
                # Try to extract text from the current response
                try:
                    if resp.text:
                        resp_text += ("\n" + resp.text).strip()
                except ValueError:
                    pass # Part might not contain text

                if not resp.function_calls:
                    break
                
                # Execute all function calls in parallel
                tool_responses = []
                for call in resp.function_calls:
                    found_tool = False
                    for tool in AGENT_TOOLS:
                        if tool.__name__ == call.name:
                            found_tool = True
                            try:
                                print(f"DEBUG: Executing tool {call.name} with args {call.args}")
                                res = tool(**call.args)
                                resp_text += f"\n[Executed {call.name}]"
                            except Exception as e:
                                print(f"ERROR: Tool {call.name} failed: {e}")
                                res = f"Error: {e}"
                                resp_text += f"\n[Failed {call.name}: {e}]"
                            
                            tool_responses.append(
                                types.Part(function_response=types.FunctionResponse(
                                    name=call.name, response={"result": res}
                                ))
                            )
                            break
                    if not found_tool:
                        resp_text += f"\n[Tool {call.name} not found]"

                if tool_responses:
                    # Send tool results back to the model for the next turn
                    # Note: For simple generate_content calls (non-chat), we might need a different approach
                    # if the model doesn't support manual multi-turn. 
                    # But for most modern Gemini models, we can continue the turn if we have the session/history.
                    # Since execute_gemini_task is used for non-chat too, we'll try to handle it.
                    if 'contents' in kwargs:
                        # Add assistant parts (calls) and tool parts (responses) to contents for next call
                        kwargs['contents'].append(types.Content(role="model", parts=resp.candidates[0].content.parts))
                        kwargs['contents'].append(types.Content(role="user", parts=tool_responses))
                        resp = task_fn(*args, **kwargs)
                    else:
                        # If no contents/history, we can't easily continue a standalone call
                        # but usually generate_knowledge uses a session which handles this.
                        break
                else:
                    break

            class DummyResp:
                def __init__(self, t): self.text = t
            return DummyResp(resp_text.strip() or "Process completed.")
            
        except Exception as e:
            last_error = e
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                print(f"WARNING: Model {m_id} hit quota limit. Falling back...")
                continue
            print(f"ERROR: Model {m_id} failed with: {error_msg}")
            continue
            
    raise last_error if last_error else Exception("All models in fallback chain failed.")

# ── Persistence Helpers ──────────────────────────────────────────────────────

def load_chat_history():
    """Load chat history and reconstruct full Part objects (text only).
    
    IMPORTANT: Model turns containing function_call parts are intentionally skipped.
    Replaying function_call parts to a thinking model requires a valid thought_signature
    (bytes generated by the model at call time). Without it, the API returns a 400
    INVALID_ARGUMENT error. We therefore only replay plain text turns, which is safe
    and still provides conversational context.
    """
    if not os.path.exists(CHAT_HISTORY_FILE): return []
    try:
        with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
            history = []
            for item in raw[-30:]:
                role = item.get('role')
                raw_parts = item.get('parts', [])

                # Skip any model turn that contains function_call parts.
                # These cannot be replayed without a matching thought_signature.
                has_function_call = any('function_call' in p for p in raw_parts)
                has_function_response = any('function_response' in p for p in raw_parts)
                if has_function_call or has_function_response:
                    continue

                parts = []
                for p in raw_parts:
                    if 'text' in p:
                        parts.append(types.Part(text=p['text']))
                    # Thought parts are skipped as they are internal model reasoning.

                if parts:
                    history.append(types.Content(role=role, parts=parts))
            return history
    except Exception as e:
        print(f"History load error: {e}")
        return []

def save_chat_history(session):
    """Save the entire conversation history including thoughts and tool interactions."""
    try:
        history = session.get_history()
        serializable = []
        for content in history:
            if content.role not in ["user", "model"]: continue
            parts = []
            for part in content.parts:
                p_dict = {}
                # Extract known parts safely
                if part.text: p_dict['text'] = part.text
                if part.thought: p_dict['thought'] = part.thought
                if part.function_call:
                    p_dict['function_call'] = {
                        "name": part.function_call.name,
                        "args": part.function_call.args
                    }
                    if hasattr(part.function_call, 'thought_signature') and part.function_call.thought_signature:
                        # Base64-encode bytes so they survive JSON serialization
                        p_dict['function_call']['thought_signature'] = base64.b64encode(part.function_call.thought_signature).decode('ascii')
                if part.function_response:
                    p_dict['function_response'] = {
                        "name": part.function_response.name,
                        "response": part.function_response.response
                    }
                if p_dict: parts.append(p_dict)
            if parts:
                serializable.append({"role": content.role, "parts": parts})
        
        with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, indent=4)
    except Exception as e:
        print(f"History save error: {e}")

@app.route('/api/generate', methods=['POST'])
def generate_knowledge():
    files = request.files.getlist('files')
    if not files: return jsonify({"error": "No files"}), 400
    temp_paths, gemini_files, texts = [], [], []
    try:
        for f in files:
            ext = os.path.splitext(f.filename)[1].lower()
            mime = ALLOWED_EXTENSIONS.get(ext)
            if not mime: continue
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                f.save(tmp.name)
                path = tmp.name
            temp_paths.append(path)
            if ext in EXCEL_EXTENSIONS:
                try:
                    df_m = pd.read_excel(path, sheet_name=None) if ext != '.csv' else {'S1': pd.read_csv(path)}
                    texts.append(f"File: {f.filename}\n" + "\n".join([f"Sheet {k}:\n{v.to_csv()}" for k, v in df_m.items()]))
                except: pass
            elif ext in DOCX_EXTENSIONS:
                try: texts.append(f"File: {f.filename}\n" + "\n".join([p.text for p in DocxDocument(path).paragraphs]))
                except: pass
            elif mime in GEMINI_UPLOADABLE_MIME:
                gemini_files.append(client.files.upload(path=path, config=types.UploadFileConfig(mime_type=mime)))
            else:
                try: texts.append(f"File: {f.filename}\n{open(path, 'r', errors='ignore').read()}")
                except: pass

        instr = (
            "You are a Master Event Architect. Your objective is to digest all uploaded documents and construct a UNIFIED, holistic event knowledge database.\n\n"
            "STRICT RULES FOR DATA EXTRACTION:\n"
            "1. NEVER put a person's name or team name in the 'task_name' field. The 'task_name' must only be the activity (e.g., 'Station 1 Management').\n"
            "2. Always put the responsible party in the 'assigned_to' field.\n"
            "3. If a document mentions 'Zi Yu, PA Team - 7:30 AM - Briefing', then:\n"
            "   task_name: 'Warm-up & Briefing'\n"
            "   assigned_to: 'Zi Yu, PA Team'\n"
            "   time: '7:30 AM'\n\n"
            "Execute your analysis in two strict phases synchronously:\n\n"
            "PHASE 1: THE MACRO-EVENT\n"
            "Extract overarching event details (name, date, venue, summary). Call 'update_event_planning_database' to store this. "
            "The 'event_summary' must be comprehensive and top-down.\n\n"
            "PHASE 2: THE MICRO-EVENTS\n"
            "Extract all specific tasks, deadlines, and responsibilities. Call 'update_timeline_database' to log these. "
            "Ensure every task fits the macro-context of Phase 1.\n"
        )
        
        # Prepare contents for the session
        session_contents = []
        for gf in gemini_files:
            session_contents.append(types.Part(file_data=types.FileData(mime_type=gf.mime_type, file_uri=gf.uri)))
        if texts:
            session_contents.append(types.Part(text="--- EXTRACTED TEXT CONTENT ---\n" + "\n\n".join(texts)))
        
        session_contents.append(types.Part(text="Process all these documents. Run Phase 1 and Phase 2. Ensure NO data is missed from any file."))

        # Perform Structured Extraction
        print("DEBUG: Requesting structured extraction from Gemini...")
        config = types.GenerateContentConfig(
            system_instruction=instr,
            response_mime_type="application/json",
            response_schema=EVENT_SCHEMA,
            temperature=0.1
        )
        
        # Use execute_gemini_task for model fallback support
        response = execute_gemini_task(
            client.models.generate_content,
            model=MODEL,
            contents=session_contents,
            config=config
        )
        
        # Parse and Update Database
        try:
            data = json.loads(response.text)
            kb = _get_kb()
            kb['event_planning']['event_name'] = data.get('event_name', kb['event_planning'].get('event_name', 'Event'))
            kb['event_planning']['event_summary'] = data.get('event_summary', kb['event_planning'].get('event_summary', ''))
            
            # Update tasks (Flattening the hierarchy for compatibility)
            all_tasks = []
            for se in data.get('sub_events', []):
                se_name = se.get('sub_event_name', 'General')
                for t in se.get('tasks', []):
                    # Optionally prepend sub-event name to keep context in flat view
                    if se_name != 'General':
                         t['task_name'] = f"{t.get('task_name')} [{se_name}]"
                    all_tasks.append(t)
            
            if all_tasks:
                kb['timeline_tasks'] = all_tasks
            
            _save_kb(kb)
            print("DEBUG: Structured extraction successfully saved to KB.")
        except Exception as pe:
            print(f"ERROR Parsing Gemini JSON: {pe}")
            return jsonify({"error": f"Failed to parse extraction result: {str(pe)}"}), 500
        
        # Write to file index
        file_index = []
        if os.path.exists(FILE_INDEX_FILE):
            try:
                with open(FILE_INDEX_FILE, 'r', encoding='utf-8') as fi: file_index = json.load(fi)
            except: pass
        for uf in files:
            file_index.append({"original_name": uf.filename, "timestamp": datetime.datetime.now().isoformat()})
        with open(FILE_INDEX_FILE, 'w', encoding='utf-8') as fi: json.dump(file_index, fi, indent=4)
        
        return jsonify({"message": "Extraction complete", "response": response.text}), 200
    except Exception as e: return jsonify({"error": str(e)}), 500
    finally:
        for p in temp_paths: 
            if os.path.exists(p): os.remove(p)

@app.route('/api/chat', methods=['POST'])
def chat():
    msg = request.get_json().get('message')
    if not msg: return jsonify({"error": "No message"}), 400
    try:
        history = load_chat_history()
        model_chain = [MODEL] + FALLBACK_MODELS
        last_error = None

        for m_id in model_chain:
            try:
                print(f"DEBUG: Chat attempt with model: {m_id}")
                cache_name = get_valid_cache(m_id)
                config = types.GenerateContentConfig(
                    tools=AGENT_TOOLS,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
                )
                if cache_name:
                    config.cached_content = cache_name

                sess = client.chats.create(model=m_id, history=history, config=config)
                resp = sess.send_message(msg)
                
                # Safely get text which might fail if it's only function calls
                try:
                    resp_text = resp.text or ""
                except ValueError:
                    resp_text = ""
                
                # Manually execute tools and send response back to the model
                # Manually execute tools and send response back to the model in a loop
                turn_limit = 10
                current_turn = 0
                
                while current_turn < turn_limit:
                    current_turn += 1
                    
                    try:
                        if resp.text:
                            resp_text += ("\n\n" + resp.text).strip()
                    except ValueError:
                        pass
                    
                    if not resp.function_calls:
                        break
                    
                    tool_responses = []
                    for call in resp.function_calls:
                        for tool in AGENT_TOOLS:
                            if tool.__name__ == call.name:
                                try:
                                    print(f"DEBUG: Executing tool {call.name} in chat")
                                    res = tool(**call.args)
                                except Exception as e:
                                    print(f"ERROR: Tool {call.name} failed in chat: {e}")
                                    res = f"Error: {e}"
                                tool_responses.append(
                                    types.Part(function_response=types.FunctionResponse(
                                        name=call.name, response={"result": res}
                                    ))
                                )
                    
                    if tool_responses:
                        # Send responses back to allow the model to continue its logic
                        resp = sess.send_message(tool_responses)
                    else:
                        break

                if not resp_text.strip():
                    resp_text = "Action completed."

                save_chat_history(sess)
                return jsonify({"response": resp_text.strip()}), 200
            except Exception as e:
                last_error = e
                error_msg = str(e)
                print(f"ERROR: Chat model {m_id} failed: {error_msg}")
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                    continue  # quota — try next model
                # For thought_signature or other errors, also try next model
                continue

        raise last_error if last_error else Exception("All chat models failed.")
    except Exception as e: 
        print(f"CHAT CRASH: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/history', methods=['GET'])
def get_history():
    if not os.path.exists(CHAT_HISTORY_FILE): return jsonify([]), 200
    return jsonify(json.load(open(CHAT_HISTORY_FILE, 'r'))), 200

@app.route('/api/knowledge', methods=['GET'])
def get_knowledge():
    return jsonify(_get_kb()), 200

@app.route('/api/files', methods=['GET'])
def get_files():
    if not os.path.exists(FILE_INDEX_FILE): return jsonify([]), 200
    return jsonify(json.load(open(FILE_INDEX_FILE, 'r'))), 200

@app.route('/api/settings/event', methods=['POST'])
def manage_event_name():
    name = request.get_json().get('event_name')
    if not name: return jsonify({"error": "Event name required"}), 400
    kb = _get_kb()
    kb['event_planning']['event_name'] = name
    _save_kb(kb)
    return jsonify({"message": "Event name updated"}), 200

@app.route('/api/settings/calendar', methods=['GET', 'POST'])
def manage_calendar_id():
    if request.method == 'POST':
        data = request.get_json()
        cid = data.get('calendar_id') if data else ""
        env_path = os.path.join(BASE_DIR, '.env')
        lines = open(env_path, 'r').readlines() if os.path.exists(env_path) else []
        with open(env_path, 'w') as f:
            found = False
            for l in lines:
                if l.startswith("GOOGLE_CALENDAR_ID="): f.write(f"GOOGLE_CALENDAR_ID={cid}\n"); found = True
                else: f.write(l)
            if not found: f.write(f"GOOGLE_CALENDAR_ID={cid}\n")
        os.environ["GOOGLE_CALENDAR_ID"] = cid
        return jsonify({"message": "Calendar ID updated"}), 200
    return jsonify({"calendar_id": os.environ.get("GOOGLE_CALENDAR_ID", "")}), 200

@app.route('/api/actions/summarize', methods=['GET'])
def api_summarize(): return jsonify({"message": summarize_and_share_event()})

@app.route('/api/actions/reminders', methods=['GET'])
def api_reminders():
    kb = _get_kb()
    tasks = kb.get('timeline_tasks', [])
    if not tasks: 
        return jsonify({
            "message": "No tasks found in your knowledge base. Please upload documents that contain a timeline or specific tasks, then click 'Analyse & Sync Knowledge' again. If you've already done this, try asking the AI in chat to 'extract tasks from the event summary'."
        }), 404
    
    e_name = kb['event_planning'].get('event_name', 'Event')
    
    # Group tasks by status
    categorized = {
        "🔴 TO DO": [],
        "🟡 IN PROGRESS": [],
        "🟢 COMPLETED": []
    }
    
    for t in tasks:
        # Support both new and legacy keys
        t_name = t.get('task_name') or t.get('title') or t.get('name') or 'Task'
        t_status = (t.get('status') or 'To Do').upper()
        t_time = t.get('time') or t.get('deadline') or ''
        t_assign = t.get('assigned_to') or 'Unassigned'
        
        # If the task name was accidentally swapped with assignee in legacy data, try to recover
        if t_assign == 'Unassigned' and t.get('name') and t.get('title'):
             t_name = t.get('title')
             t_assign = t.get('name')

        task_str = f"• *{t_name}*\n  └ 👤 {t_assign} {' | 🕒 ' + t_time if t_time else ''}"
        
        if "COMPLETED" in t_status or "DONE" in t_status:
            categorized["🟢 COMPLETED"].append(task_str)
        elif "PROGRESS" in t_status:
            categorized["🟡 IN PROGRESS"].append(task_str)
        else:
            categorized["🔴 TO DO"].append(task_str)

    # Build the message sections
    details_sections = []
    for status, task_list in categorized.items():
        if task_list:
            details_sections.append(f"*{status}*\n" + "\n".join(task_list))
    
    msg = (f"⚡ *AGENT NOTIFICATION* ⚡\n"
           f"━━━━━━━━━━━━━━━━━━━━\n"
           f"📋 *TASK STATUS: {e_name.upper()}*\n"
           f"━━━━━━━━━━━━━━━━━━━━\n\n"
           + "\n\n".join(details_sections) + 
           f"\n\n✨ _Data Logged in Timeline_")
    
    return jsonify({"message": send_telegram_alert(msg)})

# Calendar sync route removed as requested.

@app.route('/api/chat/clear', methods=['POST'])
def clear_history():
    if os.path.exists(CHAT_HISTORY_FILE): os.remove(CHAT_HISTORY_FILE)
    return jsonify({"message": "History cleared"}), 200

@app.route('/api/knowledge/clear', methods=['POST'])
def api_clear_knowledge():
    kb = {"event_planning": {}, "timeline_tasks": []}
    _save_kb(kb)
    return jsonify({"message": "Knowledge database reset"}), 200

@app.route('/api/files/clear', methods=['POST'])
def api_clear_files():
    if os.path.exists(CHAT_LOGS_FILE): open(CHAT_LOGS_FILE, 'w').close()
    if os.path.exists(FILE_INDEX_FILE):
        with open(FILE_INDEX_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
    return jsonify({"message": "File history and logs cleared"}), 200

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    from waitress import serve as ws
    print("🚀 Unified Server starting on http://localhost:5000")
    ws(app, host='0.0.0.0', port=5000)
