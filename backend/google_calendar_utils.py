import os
import json
import datetime
import re
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Constants
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "service_account.json")
SCOPES_CALENDAR = ['https://www.googleapis.com/auth/calendar']

# Database file paths
DATA_DIR            = 'local_storage'
TIMELINE_TASKS_FILE = os.path.join(DATA_DIR, 'timeline_tasks.json')

def get_calendar_service():
    """Authorize and return the Google Calendar service."""
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        return None
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES_CALENDAR)
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        print(f"Error authorizing Google Calendar: {e}")
        return None

def add_tasks_to_google_calendar(event_date: str, calendar_email: str):
    """
    Sync all tasks from local database to a Google Calendar.
    
    Args:
        event_date (str): The primary date of the event (use YYYY-MM-DD format).
        calendar_email (str): The email address of the Google Calendar.
    """
    if not os.path.exists(TIMELINE_TASKS_FILE):
        return "Error: Timeline tasks database missing."
    
    service = get_calendar_service()
    if not service:
        return "Error: Google Calendar authorization failed."

    try:
        with open(TIMELINE_TASKS_FILE, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
            
        count = 0
        for t in tasks:
            title = t.get('title', 'Task')
            deadline = t.get('deadline', '09:00 AM')
            
            # Extract time part (e.g., "09:00 AM")
            m = re.search(r'(\d{1,2}:\d{2})\s*(AM|PM)?', deadline, re.IGNORECASE)
            time_part = f"{m.group(1)} {m.group(2) or 'AM'}" if m else "09:00 AM"
            
            try:
                dt_obj = datetime.datetime.strptime(time_part, "%I:%M %p")
                start_iso = f"{event_date}T{dt_obj.strftime('%H:%M:%S')}"
                end_iso = f"{event_date}T{(dt_obj + datetime.timedelta(hours=1)).strftime('%H:%M:%S')}"
                
                event = {
                    'summary': f"[DriveBot] {title}",
                    'description': t.get('description', ''),
                    'start': {'dateTime': start_iso, 'timeZone': 'Asia/Kuala_Lumpur'},
                    'end': {'dateTime': end_iso, 'timeZone': 'Asia/Kuala_Lumpur'},
                }
                service.events().insert(calendarId=calendar_email, body=event).execute()
                count += 1
            except:
                continue
            
        return f"Successfully synced {count} tasks to {calendar_email} for {event_date}."
    except Exception as e:
        return f"Error: {e}"
