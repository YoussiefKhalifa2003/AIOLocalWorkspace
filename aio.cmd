@echo off
REM Local Windows launcher - works even if editable install is flaky.
set ROOT=%~dp0
"%ROOT%.venv\Scripts\python.exe" -m app.cli_pkg.main %*
