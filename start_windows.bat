@echo off
REM Starts everything needed to use Reel Factory in one go:
REM   1. the local model server (Ollama), for --script local
REM   2. the app itself -- one Flask process serves both the web UI
REM      (frontend) and the build/render logic (backend); there is no
REM      separate frontend to start.
REM Double-click this file, or run it from a terminal.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo === Starting Reel Factory ===
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python is not installed. Run setup_windows.bat first.
  pause & exit /b 1
)

REM ------------------------------------------------------------- local model
where ollama >nul 2>nul
if errorlevel 1 (
  REM Freshly installed but PATH not refreshed in this session yet -- check
  REM the default install location before giving up on it.
  if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
  )
)
where ollama >nul 2>nul
if errorlevel 1 (
  echo [!] Ollama is not installed -- "Script: local" will not be available.
  echo     Install it with:  winget install --id Ollama.Ollama -e
  echo     Everything else ^(template / Gemini / Grok scripts^) still works.
  goto :app
)

curl -s -o nul -m 2 http://127.0.0.1:11434/api/version
if errorlevel 1 (
  REM Nothing answered on Ollama's port -- but the port itself may still be
  REM held by a stuck process from a previous run, which would make a fresh
  REM "ollama serve" fail to bind. Clear it first.
  call :free_port 11434 "Ollama"
  if errorlevel 1 (
    pause
    exit /b 1
  )
  echo [..] Starting Ollama...
  powershell -NoProfile -Command "Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden"
  set ready=
  for /l %%i in (1,1,15) do (
    if not defined ready (
      curl -s -o nul -m 1 http://127.0.0.1:11434/api/version 2>nul
      if not errorlevel 1 set ready=1
      if not defined ready ping -n 2 127.0.0.1 >nul
    )
  )
  if not defined ready (
    echo [!] Ollama did not come up in time -- continuing without it.
    goto :app
  )
)
echo [ok] Ollama running

ollama list | findstr /c:"llama3.2:3b" >nul
if errorlevel 1 (
  echo [..] Pulling the local model llama3.2:3b ^(about 2GB, one-time^)...
  ollama pull llama3.2:3b
  if errorlevel 1 (
    echo [!] Model download failed -- continuing without local writing.
    goto :app
  )
)
echo [ok] Local model ready

REM ------------------------------------------------------------------- app
:app
REM Restart only a recognised Reel Factory listener, so stale code is replaced
REM without stopping an unrelated program using the same port.
call :free_port 5000 "the Reel Factory web UI"
if errorlevel 1 (
  pause
  exit /b 1
)
echo [..] Launching the Reel Factory web UI in its own window...
start "Reel Factory" cmd /k python -m reelfactory serve

set appready=
for /l %%i in (1,1,20) do (
  if not defined appready (
    curl -s -o nul -m 1 http://127.0.0.1:5000/ 2>nul
    if not errorlevel 1 set appready=1
    if not defined appready ping -n 2 127.0.0.1 >nul
  )
)

if not defined appready (
  echo [X] The web UI did not become ready. Check the Reel Factory window and logs/reelfactory.log.
  pause
  exit /b 1
)
start http://127.0.0.1:5000/
echo.
echo === Reel Factory is running ===
echo   Web UI:  http://127.0.0.1:5000/
echo   Errors are saved in logs/reelfactory.log. Close the "Reel Factory" window, or Ctrl+C
echo   inside it, to stop the app. Ollama keeps running in the background
echo   so you do not need to start it again.
echo.
pause
exit /b 0

REM ------------------------------------------------------------ subroutines

:free_port
setlocal
set "_rfservice=reelfactory"
if "%~1"=="11434" set "_rfservice=ollama"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart_listener.ps1" -Port %~1 -Service %_rfservice%
set "_rfresult=%errorlevel%"
endlocal & exit /b %_rfresult%
