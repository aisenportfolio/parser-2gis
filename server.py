"""Локальный интерфейс парсера 2ГИС: http://127.0.0.1:8765"""
import asyncio
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scraper import CACHE_DIR, Scraper

from paths import DATA, RES

MARKS = DATA / "marks.json"  # статусы и заметки: {id: {"status", "note", "ts", "name"}}
MARKS_LOCK = threading.Lock()


def load_marks():
    try:
        return json.loads(MARKS.read_text("utf8"))
    except Exception:
        return {}
PORT = 8765
STATE = {"running": False, "logs": [], "progress": {}, "captcha": False, "last": None}
CURRENT = {"scraper": None}


def log(msg):
    try:
        print(msg)
    except Exception:  # в exe без консоли stdout может отсутствовать
        pass
    STATE["logs"] = (STATE["logs"] + [msg])[-200:]


def progress(**kw):
    if "captcha" in kw:
        STATE["captcha"] = kw.pop("captcha")
    STATE["progress"].update(kw)


def run_job(params):
    s = Scraper(log=log, progress=progress)
    CURRENT["scraper"] = s
    try:
        res = asyncio.run(s.run(
            params["city"].strip(), params["niche"].strip(),
            min_rating=float(params.get("min_rating") or 0),
            min_reviews=int(params.get("min_reviews") or 0),
            only_city=bool(params.get("only_city", True)),
            limit=int(params.get("limit") or 0),
            workers=int(params.get("workers") or 2),
        ))
        STATE["last"] = f"{res['alias']}__" + __import__("scraper").slug(res["niche"]) + ".json"
        import cloud
        if cloud.config():
            log("Сайт: " + cloud.upload(CACHE_DIR / STATE["last"]))
    except Exception as e:
        log(f"❌ Ошибка: {e}")
    finally:
        STATE["running"] = False
        STATE["captcha"] = False


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            body = (RES / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/status":
            self._json(STATE)
        elif u.path == "/api/results":
            files = sorted(CACHE_DIR.glob("*__*.json"), key=lambda p: -p.stat().st_mtime)
            out = []
            for f in files:
                try:
                    d = json.loads(f.read_text("utf8"))
                    out.append({"file": f.name, "city": d["city"], "niche": d["niche"],
                                "created": d["created"], "count": len(d["items"])})
                except Exception:
                    pass
            self._json(out)
        elif u.path == "/api/marks":
            self._json(load_marks())
        elif u.path == "/api/result":
            name = Path(parse_qs(u.query).get("file", [""])[0]).name
            f = CACHE_DIR / name
            self._json(json.loads(f.read_text("utf8")) if f.exists() else {"error": "нет файла"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        params = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/run":
            if STATE["running"]:
                return self._json({"error": "Уже идёт сбор"}, 409)
            if not params.get("city") or not params.get("niche"):
                return self._json({"error": "Укажи город и нишу"}, 400)
            STATE.update(running=True, logs=[], progress={}, captcha=False, last=None)
            threading.Thread(target=run_job, args=(params,), daemon=True).start()
            self._json({"ok": True})
        elif self.path == "/api/marks":
            fid = str(params.get("id") or "")
            if not fid:
                return self._json({"error": "нет id"}, 400)
            with MARKS_LOCK:
                marks = load_marks()
                m = {**marks.get(fid, {}), **{k: params[k] for k in ("status", "note", "name") if k in params}}
                m["ts"] = __import__("time").strftime("%Y-%m-%d %H:%M")
                if m.get("status", "new") == "new" and not m.get("note"):
                    marks.pop(fid, None)
                else:
                    marks[fid] = m
                MARKS.write_text(json.dumps(marks, ensure_ascii=False, indent=1), "utf8")
            self._json({"ok": True})
        elif self.path == "/api/stop":
            if CURRENT["scraper"]:
                CURRENT["scraper"].stop_flag = True
                log("Останавливаю… (уже собранное сохранится)")
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Парсер 2ГИС запущен: {url}  (закрой это окно, чтобы выключить)")
    threading.Timer(1, lambda: webbrowser.open(url)).start()
    srv.serve_forever()
