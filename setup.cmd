@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist .venv\Scripts\python.exe goto verify
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m venv .venv
if errorlevel 1 exit /b 1
goto verify

:use_python
where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python 3.10 or newer is required.
  exit /b 1
)
python -m venv .venv
if errorlevel 1 exit /b 1

:verify
.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.10 or newer is required. Recreate .venv with a supported Python.
  exit /b 1
)
.venv\Scripts\python.exe -m pip install .
if errorlevel 1 exit /b 1
echo Ready. Run run_proxy.cmd --backends http://127.0.0.1:8088/v1
exit /b 0
