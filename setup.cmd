@echo off
setlocal EnableExtensions
REM One-command setup + launch for AIO (Windows cmd / PowerShell).
REM Member (default):  .\setup.cmd
REM Host first-time:   .\setup.cmd --host

set ROOT=%~dp0
cd /d "%ROOT%"

set HOST=0
set NO_LAUNCH=0
:parse
if "%~1"=="" goto parsed
if /I "%~1"=="--host" set HOST=1
if /I "%~1"=="--no-launch" set NO_LAUNCH=1
if /I "%~1"=="-h" goto help
if /I "%~1"=="--help" goto help
shift
goto parse
:help
echo Usage: .\setup.cmd [--host] [--no-launch]
echo   (default)  create venv, install deps, launch aio
echo   --host     also Playwright Chromium, .env, seed DB
echo   --no-launch  install only (do not open the app)
exit /b 0
:parsed

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  set PY=py -3
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Python not found. Install Python 3.11+ and retry.
    exit /b 1
  )
  set PY=python
)

if not exist ".venv\Scripts\python.exe" (
  echo -^> creating .venv
  %PY% -m venv .venv
  if errorlevel 1 exit /b 1
)

echo -^> pip install -r requirements.txt
".venv\Scripts\python.exe" -m pip install -U pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

if "%HOST%"=="1" (
  echo -^> Playwright Chromium ^(Outlook invites^)
  ".venv\Scripts\python.exe" -m playwright install chromium
  if not exist ".env" (
    copy /Y .env.example .env >nul
    echo -^> created .env from .env.example — add your API keys, then restart the API later
  )
  if not exist "aio.db" (
    echo -^> seeding demo DB
    ".venv\Scripts\python.exe" -m app.cli_pkg.main seed
  )
  echo.
  echo Host: keep these running in other terminals:
  echo   T1  uvicorn app.main:app --host 0.0.0.0 --port 8000
  echo   T2  cloudflared tunnel --url http://127.0.0.1:8000
  echo       -^> paste https://….trycloudflare.com into .env as INVITE_APP_URL=
  echo   T3  .\aio.cmd outlook-login   ^(once^)
  echo.
)

if "%NO_LAUNCH%"=="1" (
  echo Setup done. Run: .\aio.cmd
  exit /b 0
)

echo -^> launching aio
call "%ROOT%aio.cmd"
exit /b %ERRORLEVEL%
