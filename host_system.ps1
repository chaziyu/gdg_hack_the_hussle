# Host System Script for Project DriveBot
# This script builds the React frontend and starts the unified Flask server.

Write-Host "--- Project DriveBot Unified Hosting ---" -ForegroundColor Cyan

# 1. Install Backend Dependencies
Write-Host "[1/3] Checking backend dependencies..." -ForegroundColor Yellow
pip install -r backend/requirements.txt

# 2. Build Frontend
Write-Host "[2/3] Building React frontend (Vite)..." -ForegroundColor Yellow
npm run build

# 3. Start Server
Write-Host "[3/3] Starting production-ready server (Waitress) on port 5000..." -ForegroundColor Yellow
$env:PYTHONPATH = "backend"
python backend/app.py
