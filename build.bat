@echo off
cd /d "%~dp0"
pip install -r requirements.txt
python -m PyInstaller --noconfirm --onefile --windowed --name Parser2GIS --icon icon.ico --add-data "index.html;." --collect-all playwright --collect-submodules webview app.py
echo.
echo Done: dist\Parser2GIS.exe
pause
