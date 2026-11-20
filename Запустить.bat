@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import playwright" 2>nul || (echo Installing dependencies... & pip install -r requirements.txt)
python server.py
pause
