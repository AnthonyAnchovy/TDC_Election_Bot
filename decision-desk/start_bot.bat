@echo off
title Decision Desk Bot
cd /d "%~dp0"

rem ---- first run: build the private Python setup and install libraries ----
if not exist ".venv\Scripts\python.exe" (
    echo First-time setup, this takes about a minute...
    py -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Could not find Python. Install it from python.org and tick "Add python.exe to PATH".
        pause
        exit /b
    )
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt

if not exist ".env" (
    echo.
    echo No .env file found. Copy .env.example to .env and paste in your bot token.
    pause
    exit /b
)

rem ---- run the bot; if it ever stops, restart it after 10 seconds ----
:run
echo.
echo Starting the bot. Keep this window open. Don't click inside it.
echo To stop the bot, close this window.
echo.
".venv\Scripts\python.exe" bot.py
echo.
echo The bot stopped. Restarting in 10 seconds (close this window to cancel)...
timeout /t 10 >nul
goto run
