@echo off
REM Starts everything needed to use Reel Factory in one go:
REM   1. the local model server (Ollama), for --script local
REM   2. the app itself -- one Flask process serves both the web UI
REM      (frontend) and the build/render logic (backend); there is no
REM      separate frontend to start.
REM Double-click this file, or run it from a terminal.
setlocal enabledelayedexpansion

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
  echo [..] Starting Ollama...
  start "Ollama" /min ollama serve
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
)
echo [ok] Local model ready

REM ------------------------------------------------------------------- app
:app
REM Always clear port 5000 before launching, even if something there is
REM already answering -- otherwise a leftover instance from an earlier run
REM keeps serving stale code/templates while looking like a normal start.
call :free_port 5000 "the Reel Factory web UI"
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

start http://127.0.0.1:5000/
echo.
echo === Reel Factory is running ===
echo   Web UI:  http://127.0.0.1:5000/
echo   Server logs are in the "Reel Factory" window -- close it, or Ctrl+C
echo   inside it, to stop the app. Ollama keeps running in the background
echo   ^(it is a normal Windows service^) so you do not need to start it again.
echo.
pause
exit /b 0

REM ------------------------------------------------------------ subroutines

:free_port
REM Frees a TCP port by stopping whatever process is listening on it, so the
REM service we are about to start gets a clean bind instead of failing with
REM "address already in use" (or, for the app, silently talking to a stale
REM instance still holding the port from an earlier run).
REM   %1 = port number      %2 = name to print for what is about to use it
REM
REM Delayed expansion (!var!) is deliberately OFF in here: this subroutine's
REM own messages use a literal "[!]" prefix, like the rest of this file, and
REM a literal "!" together with real "!var!" references on the same line get
REM mispaired and garbled once delayed expansion is on -- plain %var% is all
REM that is needed since nothing here is set and read within the same block.
setlocal disabledelayedexpansion
set "_port=%~1"
set "_for=%~2"
set "_killed="
for /f %%P in ('powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %_port% -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique"') do (
  for /f "delims=" %%N in ('powershell -NoProfile -Command "(Get-Process -Id %%P -ErrorAction SilentlyContinue).ProcessName"') do (
    echo [!] Port %_port% is already in use by %%N ^(PID %%P^) -- stopping it so %_for% can use it.
  )
  powershell -NoProfile -Command "Stop-Process -Id %%P -Force -ErrorAction SilentlyContinue" >nul 2>nul
  set "_killed=1"
)
REM Give Windows a moment to actually release the socket before we bind to it.
REM (a ping-based delay, not "timeout", since timeout refuses to run at all
REM when stdin is not a real interactive console -- e.g. Task Scheduler)
if defined _killed ping -n 2 127.0.0.1 >nul
endlocal
goto :eof
