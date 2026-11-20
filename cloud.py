"""Отправка результатов на сайт (Vercel). Настройки — в cloud.json рядом:
{"url": "https://....vercel.app", "password": "общий пароль"}

Отправить все сохранённые сборы вручную:  python cloud.py
"""
import json
import urllib.request
from pathlib import Path

from paths import CLOUD as CONF, RESULTS


def config():
    if not CONF.exists():
        return None
    c = json.loads(CONF.read_text("utf8"))
    return c if c.get("url") and c.get("password") else None


def upload(path: Path) -> str:
    c = config()
    if not c:
        return "облако не настроено (нет cloud.json)"
    req = urllib.request.Request(
        c["url"].rstrip("/") + "/api/results",
        data=path.read_bytes(),
        headers={"Content-Type": "application/json", "x-pass": c["password"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
        return f"отправлено на сайт ({res['count']} компаний)"
    except urllib.error.HTTPError as e:
        return f"сайт ответил ошибкой {e.code}: {e.read().decode('utf8', 'ignore')[:200]}"
    except Exception as e:
        return f"не удалось отправить: {e}"


if __name__ == "__main__":
    for f in sorted(RESULTS.glob("*__*.json")):
        print(f.name, "->", upload(f))
