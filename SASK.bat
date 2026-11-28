@echo off
rem SASK local command center launcher (repo-local).
rem Prefers the repo virtualenv created by `pip install -e .`, then falls back
rem to a system Python with this directory on PYTHONPATH.
setlocal EnableExtensions EnableDelayedExpansion
set "PYTHONUNBUFFERED=1"
set "PYTHONUTF8=1"
set "PYEXE=%~dp0.venv\Scripts\python.exe"
if exist "%PYEXE%" (
  "%PYEXE%" -m sask_cli %*
  exit /b !ERRORLEVEL!
)
where python >nul 2>nul
if errorlevel 1 (
  echo [SASK] no repo virtualenv and no "python" on PATH.
  echo [SASK] create it: python -m venv .venv ^&^& .venv\Scripts\pip install -e .
  exit /b 9009
)
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m sask_cli %*
exit /b !ERRORLEVEL!
