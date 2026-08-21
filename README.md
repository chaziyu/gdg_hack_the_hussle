# DriveBot: The Telegram-Native Event Planning Agent

DriveBot is an autonomous, AI-powered project management agent that lives directly inside your team's Telegram group chat. It solves the "coordination tax" by passively listening to messy team conversations, reading uploaded documents, and automatically turning them into structured project intelligence synced directly to Google Sheets.

No separate websites. No manual data entry. Just add the bot to your group and start planning.

## 🌟 The Hackathon Problem & Solution

**The Problem:** Student projects stall because information stays scattered across chat threads, PDFs, and separate web portals. When students get busy, they stop manually updating external task managers.
**The Solution:** DriveBot brings the project manager *to* the users. By operating entirely within Telegram, friction is reduced to zero. DriveBot uses **Google Gemini** (with Long-Context Caching and Structured Outputs) to understand conversations, read multimodal documents (PDFs, TXT), and execute agentic actions (updating databases and spreadsheets).

## 🚀 Key Features

- **Telegram-Native:** Operates entirely within your group chat. Zero UI to learn, zero websites to visit.
- **Intelligent Batching:** Passively collects messages in a bucket and processes them in batches (every 50 messages) to extract action items without hitting rate limits or spamming the chat.
- **Multimodal Ingestion:** Drop a `.pdf` syllabus or `.txt` meeting transcript directly into the chat. DriveBot will read it and update the event plan.
- **Structured Outputs:** Uses strict Pydantic schemas to ensure Gemini extracts data perfectly every time.
- **Google Sheets Sync:** While Telegram handles the chat, DriveBot pushes all extracted timelines and tasks to a Google Sheet for easy, centralized viewing.
- **On-Demand Summaries:** Type `/summarize` at any time to force DriveBot to analyze recent chat history and update the task board.

## 🛠 Tech Stack

- **AI Engine:** Google Gemini (via `google-genai` SDK) utilizing Flash models for speed.
- **Bot Framework:** `python-telegram-bot` for robust group chat listening and command handling.
- **Data Enforcement:** Pydantic for rigid JSON schema generation (Structured Outputs).
- **Integrations:** Google Sheets API.
- **Persistence:** Local JSON for rapid prototyping, easily swappable to SQLite.

## 🏗 Architecture & Program Flow

1. **The Bucket (Ingestion):** DriveBot listens to the Telegram group. Normal chat messages are buffered in memory. Direct tags (`@DriveBot`) or file uploads are processed immediately.
2. **The Trigger:** When the message buffer hits 50 messages, or someone types `/summarize`, the batch is sent to Gemini.
3. **The Brain (Extraction):** Gemini reads the batch or uploaded file. Using predefined Pydantic schemas, it determines if new tasks were assigned or if event details changed.
4. **The Action (Function Calling):** Gemini outputs perfect JSON, which triggers Python tools to update local storage and push changes to Google Sheets.
5. **The Feedback:** DriveBot replies to the Telegram group: *"✅ I've updated the timeline in Google Sheets!"*

## ⚙️ Setup & Installation

1. Install dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```
2. Set up your `.env` file in the root directory:
   ```env
   GEMINI_API_KEY=your_gemini_api_key
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_ADMIN_CHAT_ID=your_chat_id
   SPREADSHEET_ID=your_google_sheet_id
   ```
3. (Important) Disable Privacy Mode for your bot via `@BotFather` on Telegram so it can read group messages.
4. Run the agent!
   ```bash
   python backend/telegram_listener.py
   ```

*(Note: The React frontend in `src/` is deprecated in favor of this zero-friction Telegram-only architecture).*

## 🔮 Future Roadmap
- **Google Drive Integration:** Automatically monitor a shared Google Drive folder for new files, in addition to Telegram uploads.
- **Calendar Injection:** Use Google Calendar API to automatically schedule deadlines extracted from the chat.
- **Long-Context Memory:** Fully implement Gemini's Context Caching to remember weeks of chat history at a 90% discount on token costs.

## Link
Google AppScript Link: https://script.google.com/u/0/home/projects/1Ly9pE6YutUJHdKlA1xj3KKVFnWfPJaVbVg5PZL7MDYm17X_ZgJYDPmls/edit

Google Sheet Link: https://docs.google.com/spreadsheets/d/1DpXHD8i7Kj0A8bMdfQswaXz2ZHkmwiJmI9ddc3eegUM/edit?gid=0#gid=0 
