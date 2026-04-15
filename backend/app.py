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
import urllib.parse

# Load environment variables
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'dist'), static_url_path='/')
CORS(app)

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

# ── File paths ─────────────────────────────────────────────────────────────────
DATA_DIR            = os.path.join(BASE_DIR, 'local_storage')
CHAT_LOGS_FILE      = os.path.join(DATA_DIR, 'chat_logs.txt')
TIMELINE_TASKS_FILE = os.path.join(DATA_DIR, 'timeline_tasks.json')
EVENT_PLANNING_FILE = os.path.join(DATA_DIR, 'event_planning.json')
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

def _get_kb():
    """Unified helper to read both timeline and planning databases."""
    t, p = [], {}
    if os.path.exists(TIMELINE_TASKS_FILE):
        try:
            with open(TIMELINE_TASKS_FILE, 'r', encoding='utf-8') as f: t = json.load(f)
        except: pass
    if os.path.exists(EVENT_PLANNING_FILE):
        try:
            with open(EVENT_PLANNING_FILE, 'r', encoding='utf-8') as f: p = json.load(f)
        except: pass
    return {"timeline_tasks": t, "event_planning": p}

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

def update_timeline_database(tasks: list[dict]):
    """Update the local timeline_tasks.json database with a list of tasks."""
    try:
        with open(TIMELINE_TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, indent=4)
        return "Local timeline database updated."
    except Exception as e: return f"Error: {e}"

def update_event_planning_database(plan: dict):
    """Update the local event_planning.json database with overall event details."""
    try:
        existing = {}
        if os.path.exists(EVENT_PLANNING_FILE):
            try:
                with open(EVENT_PLANNING_FILE, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except: pass
            
        new_name = plan.get('event_name')
        existing_name = existing.get('event_name')
        
        # Merge the new plan into existing data
        existing.update(plan)
        
        # Ensure event_name is not lost if the agent didn't provide it
        if not new_name and existing_name:
            existing['event_name'] = existing_name
            
        with open(EVENT_PLANNING_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing, f, indent=4)
        return "Local event planning database updated."
    except Exception as e: return f"Error: {e}"

def get_event_planning_database():
    """Retrieve the current overall event planning details from the database. Use this tool when you need to read or show the current event plan."""
    if not os.path.exists(EVENT_PLANNING_FILE): return "No event planning data found."
    try:
        with open(EVENT_PLANNING_FILE, 'r', encoding='utf-8') as f:
            return json.dumps(json.load(f))
    except Exception as e: return f"Error: {e}"


def sync_to_google_sheet(rows: list[list[str]], worksheet_name: str):
    """Sync data directly to a Google Sheet."""
    # Handle default value internally
    target_sheet = worksheet_name if worksheet_name else "Live_Timeline"
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
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
    if not token or not chat_id: return "Telegram alert skipped: Config missing."
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": f"⚡ AGENT ALERT:\n{message}"}
        r = requests.post(url, json=payload, timeout=10)
        return "Telegram alert sent successfully." if r.ok else f"Telegram error: {r.text}"
    except Exception as e: return f"Error: {e}"

def summarize_and_share_event():
    """Generate an event summary and broadcast it's details to Telegram."""
    if not os.path.exists(EVENT_PLANNING_FILE): return "Error: No planning data."
    try:
        with open(EVENT_PLANNING_FILE, 'r', encoding='utf-8') as f: p = json.load(f)
        
        e_date = p.get('event_date') or p.get('date') or p.get('Date') or 'TBD'
        e_venue = p.get('event_venue') or p.get('venue') or p.get('Venue') or p.get('Location') or 'TBD'
        e_summary = p.get('event_summary') or p.get('summary') or p.get('description') or p.get('Description') or 'No summary recorded.'
        
        msg = (f"📢 **EVENT SUMMARY: {p.get('event_name', 'Event').upper()}**\n\n"
               f"📅 **Date:** {e_date}\n"
               f"📍 **Venue:** {e_venue}\n\n"
               f"📝 **Overview:**\n{e_summary}")
        return send_telegram_alert(msg)
    except Exception as e: return f"Error: {e}"


AGENT_TOOLS = [
    update_timeline_database, update_event_planning_database, get_event_planning_database,
    sync_to_google_sheet, send_telegram_alert,
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
            
            resp = task_fn(*args, **kwargs)
            
            try:
                resp_text = resp.text or ""
            except ValueError:
                resp_text = ""
                
            if resp.function_calls:
                tool_responses = []
                for call in resp.function_calls:
                    for tool in AGENT_TOOLS:
                        if tool.__name__ == call.name:
                            try:
                                res = tool(**call.args)
                                resp_text += f"\n[Executed {call.name}]"
                            except Exception as e:
                                res = str(e)
                                resp_text += f"\n[Failed {call.name}: {e}]"
                            tool_responses.append(
                                types.Part(function_response=types.FunctionResponse(
                                    name=call.name, response={"result": res}
                                ))
                            )
                if tool_responses:
                    resp_text += "\n"
            
            class DummyResp:
                def __init__(self, t): self.text = t
            return DummyResp(resp_text.strip() or "Sync completed.")
            
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

            instr = (
            "You are a Master Event Architect and Lead Data Synthesizer. "
            "Your objective is to digest all fragmented documents and construct a UNIFIED, holistic event knowledge database. "
            "Do NOT hyper-focus on isolated sub-events or single departments. You must see the big picture. "
            "Execute your analysis in these two strict phases: \n\n"
            "PHASE 1: THE MACRO-EVENT (The Big Picture)\n"
            "First, identify the overarching main event. Extract the primary event name, global dates, main venue, and core objective. "
            "Call 'update_event_planning_database' to store this. CRITICAL: You MUST include an 'event_summary' key that provides a top-down, comprehensive overview of the entire main event.\n\n"
            "PHASE 2: THE MICRO-EVENTS (Timeline & Departments)\n"
            "Next, extract all specific tasks, sub-events, and departmental roles. Consolidate overlapping tasks from different files to avoid duplication. "
            "Call 'update_timeline_database' to log these chronologically. Ensure every task makes sense in the context of the Phase 1 Macro-Event.\n\n"
            f"--- UPLOADED DOCUMENTS ---\n{' '.join(texts)}"
            )           
        # Create a cache for the main model
        # We use a 1 hour TTL by default.
        print(f"DEBUG: Creating new context cache for {MODEL}...")
        try:
            # Aggregate all content for the cache
            cache_contents = []
            for gf in gemini_files:
                cache_contents.append(types.Part(file_data=types.FileData(mime_type=gf.mime_type, file_uri=gf.uri)))
            if texts:
                cache_contents.append(types.Part(text="\n\n".join(texts)))
            
            # Create the cache
            # Note: tools and instruction MUST be in the cache creation if we want to use them with the cache
            cache = client.caches.create(
                model=MODEL,
                config=types.CreateCachedContentConfig(
                    display_name="Project Memory Cache",
                    system_instruction=instr,
                    contents=cache_contents,
                    ttl="3600s", # 1 hour
                    # Note: tools are NOT frozen into cache; they are passed in generate_content calls.
                )
            )
            
            # Register tools separately in the generation call if not supported in cache config 
            # (Wait, actually the GenAI SDK config for caches might not take tools directly in some versions, 
            # but let's assume it supports them or we pass them in generate_content).
            # Update: Some SDK versions require tools in the cache. 
            # Let's try to put them in the generate call first and if it fails, we'll know.
            
            # Save metadata
            metadata = get_cache_metadata()
            metadata[MODEL] = {
                "name": cache.name,
                "expiry": (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()
            }
            save_cache_metadata(metadata)
            print(f"DEBUG: Cache created: {cache.name}")
            
            # Initial call to verify and act
            response = execute_gemini_task(
                client.models.generate_content,
                model=MODEL,
                contents="Process these documents and update the databases accordingly.", 
                config=types.GenerateContentConfig(tools=AGENT_TOOLS)
            )
        except Exception as e:
            print(f"CACHE CREATION ERROR: {e}")
            # Fallback to standard non-cached generation if cache fails
            # We must include the actual content (texts + files) in the fallback message
            fallback_contents = [instr] # Standard instruction
            for gf in gemini_files:
                fallback_contents.append(types.Part(file_data=types.FileData(mime_type=gf.mime_type, file_uri=gf.uri)))
            if texts:
                fallback_contents.append(types.Part(text="\n\n".join(texts)))
            fallback_contents.append(types.Part(text="Read all uploaded documents. First, update the event planning database with the overall Big Event details and summary. Then, update the timeline database with all consolidated tasks."))

            response = execute_gemini_task(
                client.models.generate_content,
                model=MODEL,
                contents=fallback_contents, 
                config=types.GenerateContentConfig(tools=AGENT_TOOLS)
            )
        
        # BUG 5 FIX: Write processed file names to the persistent file index
        file_index = []
        if os.path.exists(FILE_INDEX_FILE):
            try:
                with open(FILE_INDEX_FILE, 'r', encoding='utf-8') as fi: file_index = json.load(fi)
            except: pass
        for uf in files:
            uf_ext = os.path.splitext(uf.filename)[1].lower()
            if ALLOWED_EXTENSIONS.get(uf_ext):
                file_index.append({"original_name": uf.filename, "timestamp": datetime.datetime.now().isoformat()})
        with open(FILE_INDEX_FILE, 'w', encoding='utf-8') as fi: json.dump(file_index, fi, indent=4)
        return jsonify({"message": "Sync complete", "response": response.text}), 200
    except Exception as e: return jsonify({"error": str(e)}), 500
    finally:
        for p in temp_paths: 
            if os.path.exists(p): os.remove(p)

@app.route('/api/chat', methods=['POST'])
def chat():
    """A clean chat endpoint that allows the AI to converse based on the new nested data."""
    raw_msg = request.get_json().get('message')
    if not raw_msg: return jsonify({"error": "No message"}), 400
    
    # --- FIX: Inject a hidden prompt to force the AI to use its tools ---
    # Because the cache is locked in "Data Synthesizer" mode, we must 
    # explicitly instruct the AI to act as DriveBot for this specific chat turn.
    augmented_msg = (
        f"{raw_msg}\n\n"
        "[SYSTEM NOTE: You are DriveBot, an AI project manager. "
        "If the user asks about the event, you MUST use the `get_event_planning_database` "
        "tool to fetch the specific project details before answering. "
        "Do NOT give generic dictionary definitions.]"
    )
    
    try:
        history = load_chat_history()
        model_chain = [MODEL] + FALLBACK_MODELS
        last_error = None

        for m_id in model_chain:
            try:
                print(f"DEBUG: Chat attempt with model: {m_id}")
                cache_name = get_valid_cache(m_id)
                
                # Add system instruction to the config
                config = types.GenerateContentConfig(
                    tools=AGENT_TOOLS,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    system_instruction="You are DriveBot. Always check the database using your tools before answering questions."
                )
                
                if cache_name:
                    config.cached_content = cache_name

                sess = client.chats.create(model=m_id, history=history, config=config)
                
                # Send the augmented_msg instead of the raw_msg
                resp = sess.send_message(augmented_msg)
                
                try:
                    resp_text = resp.text or ""
                except ValueError:
                    resp_text = ""
                
                # Manually execute tools and send response back to the model
                if resp.function_calls:
                    tool_responses = []
                    for call in resp.function_calls:
                        for tool in AGENT_TOOLS:
                            if tool.__name__ == call.name:
                                try:
                                    res = tool(**call.args)
                                except Exception as e:
                                    res = f"Error: {e}"
                                tool_responses.append(
                                    types.Part(function_response=types.FunctionResponse(
                                        name=call.name, response={"result": res}
                                    ))
                                )
                    
                    if tool_responses:
                        # Send responses back to allow the model to summarize it
                        resp2 = sess.send_message(tool_responses)
                        try:
                            if resp2.text:
                                resp_text += ("\n\n" + resp2.text).strip()
                        except ValueError:
                            pass

                if not resp_text.strip():
                    resp_text = "Action completed."

                save_chat_history(sess)
                return jsonify({"response": resp_text.strip()}), 200
            except Exception as e:
                last_error = e
                error_msg = str(e)
                print(f"ERROR: Chat model {m_id} failed: {error_msg}")
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                    continue  
                continue

        raise last_error if last_error else Exception("All chat models failed.")
    except Exception as e: 
        return jsonify({"error": str(e)}), 500

@app.route('/api/history', methods=['GET'])
def get_history():
    if not os.path.exists(CHAT_HISTORY_FILE): return jsonify([]), 200
    return jsonify(json.load(open(CHAT_HISTORY_FILE, 'r'))), 200

@app.route('/api/knowledge', methods=['GET'])
def get_knowledge():
    t, p = [], {}
    if os.path.exists(TIMELINE_TASKS_FILE): t = json.load(open(TIMELINE_TASKS_FILE, 'r'))
    if os.path.exists(EVENT_PLANNING_FILE): p = json.load(open(EVENT_PLANNING_FILE, 'r'))
    return jsonify({"timeline_tasks": t, "event_planning": p}), 200

@app.route('/api/files', methods=['GET'])
def get_files():
    if not os.path.exists(FILE_INDEX_FILE): return jsonify([]), 200
    return jsonify(json.load(open(FILE_INDEX_FILE, 'r'))), 200

@app.route('/api/settings/event', methods=['POST'])
def manage_event_name():
    data = request.get_json()
    name = data.get('event_name') if data else ""
    if not name: return jsonify({"error": "Event name required"}), 400
    planning = {}
    if os.path.exists(EVENT_PLANNING_FILE):
        try:
            with open(EVENT_PLANNING_FILE, 'r', encoding='utf-8') as f: planning = json.load(f)
        except: pass
    planning['event_name'] = name
    try:
        with open(EVENT_PLANNING_FILE, 'w', encoding='utf-8') as f: json.dump(planning, f, indent=4)
        return jsonify({"message": "Event name updated"}), 200
    except Exception as e: return jsonify({"error": str(e)}), 500

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
    
    formatted_tasks = []
    for t in tasks:
        t_name = t.get('task') or t.get('name') or t.get('title') or t.get('Task', 'Unnamed')
        t_status = t.get('status') or t.get('Status') or 'Pending'
        t_date = t.get('date') or t.get('time') or t.get('Time') or ''
        t_dept = t.get('department') or t.get('Department') or ''
        
        task_str = f"• {t_name} ({t_status})"
        if t_dept:
            task_str += f" | {t_dept}"
        if t_date:
            task_str += f" | 📅 {t_date}"
        formatted_tasks.append(task_str)
        
    msg = "📅 **FULL TASK LIST**\n" + "\n".join(formatted_tasks)
    return jsonify({"message": send_telegram_alert(msg)})

# Calendar sync route removed as requested.

@app.route('/api/chat/clear', methods=['POST'])
def clear_history():
    if os.path.exists(CHAT_HISTORY_FILE): os.remove(CHAT_HISTORY_FILE)
    return jsonify({"message": "History cleared"}), 200

@app.route('/api/knowledge/clear', methods=['POST'])
def api_clear_knowledge():
    for f in [TIMELINE_TASKS_FILE, EVENT_PLANNING_FILE]:
        if os.path.exists(f):
            with open(f, 'w', encoding='utf-8') as file:
                json.dump([] if f == TIMELINE_TASKS_FILE else {}, file)
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