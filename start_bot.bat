@echo off
cd /d "%~dp0"

echo Starting Trading Bot...

REM Clear stale heartbeat from previous session
if exist db\heartbeat.txt del db\heartbeat.txt

REM Activate venv and start the main bot in a new window
start "Trading Bot" cmd /k ".venv\Scripts\activate.bat && python scheduler/main.py"

REM Wait 30 seconds for bot to connect and write first heartbeat, then start watchdog
echo Waiting 30 seconds for bot to connect before starting watchdog...
timeout /t 30 /nobreak >nul
start "Watchdog" cmd /k ".venv\Scripts\activate.bat && python scheduler/watchdog.py"

echo Both windows started. Check Telegram for session start alert.
