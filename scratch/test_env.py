import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # Assuming run from root
# If app.py does dirname(dirname(abspath(__file__))), it goes up two levels from backend/app.py
# Which is the root.

dotenv_path = os.path.join(BASE_DIR, '.env')
print(f"Checking .env at: {dotenv_path}")
print(f"Exists: {os.path.exists(dotenv_path)}")

load_dotenv(dotenv_path)

print(f"BOT_TOKEN: {os.environ.get('TELEGRAM_BOT_TOKEN')}")
print(f"CHAT_ID: {os.environ.get('TELEGRAM_ADMIN_CHAT_ID')}")
