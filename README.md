# DriveBot

DriveBot is an AI-powered event planning assistant that turns unstructured event documents into a structured, searchable knowledge base. It extracts the main event overview, timeline tasks, and planning details from uploaded files and makes them available through an AI-driven chat interface.

## What DriveBot Does

- Upload event planning documents: PDF, Word, Excel, CSV, text files, and media (MP3, MP4).
- Extract the event name, date, venue, summary, and timeline tasks.
- Store event knowledge locally in `local_storage/`.
- Answer questions about the event through AI chat.
- Sync extracted timeline data to Google Sheets.
- Send Telegram alerts for event summaries and reminders.
- Clear chat, knowledge, and file history for fresh runs.

## How It Works

- **Frontend**: React + Vite.
- **Backend**: Flask served by Waitress.
- **AI Core**: Google GenAI with `gemini-3.1-flash-lite-preview` and fallback models.
- **Persistence**: Local JSON storage under `local_storage/`.
- **Integrations**: Google Sheets, Telegram, and Google service account authentication.

## Key Features

- **Document ingestion**: upload supported documents and parse them with AI.
- **Event intelligence**: build a unified event plan and timeline task list.
- **Conversational AI**: query the extracted knowledge via chat.
- **Google Sheets sync**: push timeline rows to a spreadsheet.
- **Telegram notifications**: broadcast summaries and reminders.
- **Resilient AI fallback**: automatic model failover on quota/API issues.

## Supported Upload Types

- `.pdf`
- `.doc`, `.docx`
- `.xls`, `.xlsx`
- `.csv`
- `.txt`
- `.mp3`, `.wav` (audio)
- `.mp4`, `.mov`, `.webm`, `.avi` (video)

## Backend API Endpoints

- `POST /api/generate` — upload files and sync knowledge.
- `POST /api/chat` — ask questions about the event.
- `GET /api/knowledge` — fetch extracted event and timeline data.
- `GET /api/files` — list indexed uploaded files.
- `POST /api/settings/event` — update the event name.
- `GET|POST /api/settings/calendar` — get or set the calendar ID.
- `GET /api/actions/summarize` — send an event summary to Telegram.
- `GET /api/actions/reminders` — send task reminders to Telegram.
- `POST /api/chat/clear` — clear chat history.
- `POST /api/knowledge/clear` — clear extracted knowledge.
- `POST /api/files/clear` — clear file logs and indexes.

## Requirements

- Node.js 18+
- Python 3.10+
- `service_account.json` placed in the project root.

## Setup

1. Install frontend dependencies:

```powershell
npm install
```

2. Install backend dependencies:

```powershell
pip install -r backend/requirements.txt
```

3. Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_gemini_api_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_ADMIN_CHAT_ID=your_telegram_chat_id
SPREADSHEET_ID=your_google_sheet_id
GOOGLE_CALENDAR_ID=optional_google_calendar_id
```

4. Place `service_account.json` in the project root.

## Run Locally

Start the app using the host script:

```powershell
./host_system.ps1
```

This script installs backend dependencies, builds the React frontend, and starts the Flask server on `http://localhost:5000`.

## Manual Run

If you want to run the backend manually:

```powershell
npm run build
python backend/app.py
```

## Project Structure

- `backend/` — Flask backend, AI orchestration, and integration code.
- `src/` — React frontend source.
- `local_storage/` — local JSON databases for event planning and timeline tasks.
- `service_account.json` — Google service account credentials.
- `host_system.ps1` — build and run script.

## Notes

- Extracted event data is stored in `local_storage/event_planning.json` and `local_storage/timeline_tasks.json`.
- Frontend currently supports document uploads and chat interaction.
- Google Sheets and Telegram features require valid environment configuration.

## Suggested Improvements

- Add a `.env.example` for faster onboarding.
- Add frontend validation for missing environment variables.
- Document exact file size limits and supported formats.

---

Built to turn event planning documents into actionable intelligence. 🚀
