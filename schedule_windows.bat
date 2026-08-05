@echo off
REM Registers a daily task that renders and publishes whatever is due.
REM Run this once. Re-running it updates the existing task.
setlocal

set TASKNAME=ReelFactory
set /p RUNTIME="Time to check the queue each day (24h, e.g. 09:00): "
if "%RUNTIME%"=="" set RUNTIME=09:00

set HERE=%~dp0
set LOGDIR=%HERE%out\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM cmd /c so the log is written even when the run fails.
set CMD=cmd /c cd /d "%HERE%" ^&^& python -m reelfactory run ^>^> "%LOGDIR%\run.log" 2^>^&1

schtasks /create /tn "%TASKNAME%" /tr "%CMD%" /sc daily /st %RUNTIME% /f
if errorlevel 1 (
  echo.
  echo [X] Could not create the task. Try running this file as Administrator.
  pause & exit /b 1
)

echo.
echo === Scheduled ===
echo   Runs every day at %RUNTIME%
echo   Log: %LOGDIR%\run.log
echo.
echo Useful commands:
echo   schtasks /query /tn "%TASKNAME%"      see the task
echo   schtasks /run   /tn "%TASKNAME%"      run it right now
echo   schtasks /delete /tn "%TASKNAME%" /f  remove it
echo.
echo The computer must be switched on at that time. A missed day is caught up
echo on the next run, as long as it is within the 48 hour grace window.
echo.
pause
