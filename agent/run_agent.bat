@echo off
REM Usage:  run_agent.bat <frontend-url> [extra args]
REM Example: run_agent.bat http://localhost:5173
REM          run_agent.bat https://demobank.example.org --headed --slowmo 250
if "%~1"=="" (
  echo Usage: run_agent.bat ^<frontend-url^> [extra args]
  exit /b 1
)
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m qa_agent --url %* --prd ..\test-website\PRD.md --out runs
