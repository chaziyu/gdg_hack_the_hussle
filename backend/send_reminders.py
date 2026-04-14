import os
import json
import asyncio
from telegram import Bot
from dotenv import load_dotenv

# Load environment variables
# Load environment variables
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
TASKS_FILE = 'timeline_tasks.json'

async def send_reminders():
    if not TOKEN or not CHAT_ID:
        print("Error: Bot token or Admin Chat ID not set in .env.")
        return

    if not os.path.exists(TASKS_FILE):
        print(f"Error: {TASKS_FILE} not found.")
        return

    try:
        with open(TASKS_FILE, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
    except Exception as e:
        print(f"Error reading tasks: {e}")
        return

    if not tasks:
        print("No tasks found to send.")
        return

    bot = Bot(token=TOKEN)

    # Header
    message = "📅 **PROJECT DRIVEBOT: TASK REMINDERS**\n"
    message += "------------------------------------------\n\n"

    # Group tasks by Status or just list them
    not_started = [t for t in tasks if t.get('status') == 'Not Started']
    
    if not_started:
        message += "⚡ **UPCOMING / NOT STARTED:**\n"
        for i, t in enumerate(not_started, 1):
            title = t.get('title', 'Untitled Task')
            deadline = t.get('deadline', 'No deadline')
            assigned = t.get('assigned_to', 'Unassigned')
            message += f"{i}. **{title}**\n   ⏰ Time: {deadline}\n   👤 Assigned: {assigned}\n\n"
    else:
        message += "✅ All tasks are currently in progress or completed!"

    message += "------------------------------------------\n"
    message += "💡 *Please update your status via the Intelligence Hub.*"

    try:
        # Split message if it's too long (Telegram limit is ~4096 chars)
        if len(message) > 4000:
            # Simple split for now
            parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
            for p in parts:
                await bot.send_message(chat_id=CHAT_ID, text=p, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
        
        print("Task reminders sent successfully to Telegram.")
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

if __name__ == "__main__":
    asyncio.run(send_reminders())
