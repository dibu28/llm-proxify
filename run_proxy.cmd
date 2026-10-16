@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m llm_proxify %*
) else (
  py -3 -m llm_proxify %*
)
exit /b %errorlevel%
