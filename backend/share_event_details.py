import os
import asyncio
from telegram import Bot
from dotenv import load_dotenv

# Load environment variables
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")

async def share_event_details():
    if not TOKEN or not CHAT_ID:
        print("Error: Bot token or Admin Chat ID not set.")
        return

    message = (
        "📢 **EVENT DETAILS: COLOR RUN** 🏃‍♂️🌈\n\n"
        "* **Date:** Event Day\n"
        "* **Venue:** Starts at Block A, Finishes at Laman Woodball\n"
        "* **Summary:** A fun run event where participants are doused with colored powder at various stations along the route. Emphasizes safety during road crossings and provides entertainment and refreshments.\n"
        "* **Objectives:** To host a successful and enjoyable color run event, ensuring participant safety and a memorable experience.\n"
        "* **Departments Involved:** Logistics, Safety, Multimedia, Volunteers\n"
        "* **Overall Notes:** Strict adherence to safety protocols, especially during road crossings. Clear communication among committee members is crucial. Participants will receive t-shirts, rubber bands, water, and color powder. Entertainment includes Zumba and music."
    )

    bot = Bot(token=TOKEN)
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
        print("Event details shared successfully to Telegram.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(share_event_details())
