@echo off
REM One-time setup for Reel Factory on Windows.
echo.
echo === Reel Factory setup ===
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python is not installed. Get it from https://python.org/downloads
  echo     Tick "Add Python to PATH" during installation, then run this again.
  pause & exit /b 1
)
echo [ok] Python found

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [..] FFmpeg missing, installing with winget...
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  echo.
  echo [!] Close this window, open a NEW terminal, and run setup_windows.bat again.
  pause & exit /b 1
)
echo [ok] FFmpeg found

echo [..] Installing Python packages...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 ( echo [X] Package install failed & pause & exit /b 1 )
echo [ok] Packages installed

echo.
echo === Setup complete ===
echo.
echo Next:
echo   1. Edit brand.yaml with your client's name, phone and colours.
echo   2. For Hindi text, install Noto Sans Devanagari (fonts.google.com).
echo   3. Try it:  python -m reelfactory build products\sample-iron-shelf
echo.
pause
