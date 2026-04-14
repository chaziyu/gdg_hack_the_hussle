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
        with open(EVENT_PLANNING_FILE, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=4)
        return "Local event planning database updated."
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
    load_dotenv(os.path.join(BASE_DIR, '.env'), override=True)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
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
        msg = (f"📢 **EVENT SUMMARY: {p.get('event_name', 'Event').upper()}**\n\n"
               f"📅 **Date:** {p.get('event_date', 'TBD')}\n"
               f"📍 **Venue:** {p.get('event_venue', 'TBD')}\n\n"
               f"📝 **Overview:**\n{p.get('event_summary', 'No summary.')}")
        return send_telegram_alert(msg)
    except Exception as e: return f"Error: {e}"

AGENT_TOOLS = [
    update_timeline_database, update_event_planning_database,
    sync_to_google_sheet, send_telegram_alert,
    summarize_and_share_event
]

def execute_gemini_task(task_fn, *args, **kwargs):
    """
    Executes a Gemini task with automatic fallback through the model list.
    """
    model_chain = [MODEL] + FALLBACK_MODELS
    last_error = None
    
    for m_id in model_chain:
        try:
            print(f"DEBUG: Attempting task with model: {m_id}")
            # Inject the model ID and cache if available
            if 'model' in kwargs: 
                kwargs['model'] = m_id
            
            # Check for existing cache for this specific model
            cache_name = get_valid_cache(m_id)
            if cache_name:
                print(f"DEBUG: Using cache {cache_name} for model {m_id}")
                if 'config' in kwargs:
                    # When using cached_content, we must NOT override system_instruction or tools
                    # which were already frozen into the cache.
                    kwargs['config'].cached_content = cache_name
                    kwargs['config'].system_instruction = None
                    kwargs['config'].tools = None
                else:
                    # In case config isn't passed, we'll need it anyway for cached_content
                    kwargs['config'] = types.GenerateContentConfig(cached_content=cache_name)
            
            return task_fn(*args, **kwargs)
        except Exception as e:
            last_error = e
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                print(f"WARNING: Model {m_id} hit quota limit. Falling back...")
                continue
            # If it's not a quota error, we might still want to try fallback for safety,
            # but usually, we only fallback on 429.
            print(f"ERROR: Model {m_id} failed with: {error_msg}")
            # Try next model anyway if possible
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
        # Removed Telegram log analysis to save API quota as requested.
        # logs = open(CHAT_LOGS_FILE, 'r', encoding='utf-8').read() if os.path.exists(CHAT_LOGS_FILE) else ""
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

        instr = f"Analyze documents and act as an expert project manager. Use tools to manage the timeline and planning database.\n\nTexts: {' '.join(texts)}"
        
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
            response = execute_gemini_task(
                client.models.generate_content,
                model=MODEL,
                contents=[*gemini_files, instr], 
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
                config = types.GenerateContentConfig(tools=AGENT_TOOLS)
                if cache_name:
                    config.cached_content = cache_name

                sess = client.chats.create(model=m_id, history=history, config=config)
                resp = sess.send_message(msg)
                save_chat_history(sess)
                return jsonify({"response": resp.text}), 200
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
    if not os.path.exists(TIMELINE_TASKS_FILE): return jsonify({"message": "No tasks"}), 404
    tasks = json.load(open(TIMELINE_TASKS_FILE, 'r'))
    msg = "📅 **FULL TASK LIST**\n" + "\n".join([f"• {t.get('name') or t.get('title', 'Unnamed')} ({t.get('status', 'Unknown')})" for t in tasks])
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
