@echo off
rem Launches Mediatheque. On first run, sets up its private Python environment.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Setting up Mediatheque for the first time. This takes a minute...
    python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
rem Packages added after first setup (e.g. for Suggest) are installed on the next launch.
".venv\Scripts\python.exe" -c "import anthropic, keyring" 2>nul || ".venv\Scripts\python.exe" -m pip install -r requirements.txt
start "" ".venv\Scripts\pythonw.exe" -m mediatheque
