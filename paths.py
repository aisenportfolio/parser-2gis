"""Где лежат файлы программы и данные.

Из исходников всё лежит рядом со скриптами.
В собранном .exe файлы программы — внутри exe, а данные (результаты, профиль
браузера, cloud.json) — в %LOCALAPPDATA%\\Parser2GIS, чтобы переживали обновления exe.
"""
import os
import shutil
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
RES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Parser2GIS" if FROZEN else Path(__file__).parent

DATA.mkdir(parents=True, exist_ok=True)
RESULTS = DATA / "results"
RESULTS.mkdir(exist_ok=True)
PROFILE = DATA / "browser_profile"
CLOUD = DATA / "cloud.json"

# первый запуск exe: настройки сайта берём из встроенной копии
if FROZEN and not CLOUD.exists() and (RES / "cloud.json").exists():
    shutil.copy(RES / "cloud.json", CLOUD)
