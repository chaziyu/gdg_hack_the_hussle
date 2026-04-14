import os
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Load environment variables
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Ensure TELEGRAM_BOT_TOKEN is set in your environment variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_LOGS_FILE = 'chat_logs.txt'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Silently listens to text messages and appends them to chat_logs.txt.
    Does not reply to the chat.
    """
    if not update.message or not update.message.text:
        return

    user = update.message.from_user
    name = user.first_name if user else "Unknown"
    if user and user.last_name:
        name += f" {user.last_name}"
        
    message_text = update.message.text

    # Format: [Name]: [Message]
    log_entry = f"[{name}]: {message_text}\n"

    # Append to the chat logs file
    try:
        with open(CHAT_LOGS_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry)
        print(f"Logged message from {name} (Chat ID: {update.message.chat_id})")
    except Exception as e:
        print(f"Error writing to log file: {e}")

def main():
    """Starts the Telegram bot listener."""
    if not TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN environment variable not set.")
        return

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # Listen to all text messages (filters.TEXT) and ignore commands (~filters.COMMAND)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Telegram listener started. Waiting for messages...")
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
