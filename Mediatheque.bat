@echo off
rem Launches Mediatheque. On first run, sets up its private Python environment.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Setting up Mediatheque for the first time. This takes a minute...
    python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
rem Packages added since the last launch (by an update, say) are installed now; otherwise this is quick.
".venv\Scripts\python.exe" -m mediatheque.reqcheck || (".venv\Scripts\python.exe" -m pip install -r requirements.txt && ".venv\Scripts\python.exe" -m mediatheque.reqcheck --mark)
start "" ".venv\Scripts\pythonw.exe" -m mediatheque
