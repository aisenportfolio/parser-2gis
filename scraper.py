"""Сбор организаций из 2ГИС через обычный браузер (Edge).

Парсер открывает страницы 2gis.ru как человек и читает данные, которые сайт
сам подгружает (window.initialState и ответ с отзывами). Если 2ГИС покажет
капчу — парсер ставит паузу и ждёт, пока вы решите её в окне браузера.
"""
import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from playwright.async_api import async_playwright

from insights import ANALYSIS_V, analyze, compact_reviews, hook, verdict
from lpr import find_lpr, summarize
from sitecheck import BOOKING_DOMAINS, check_many

from paths import PROFILE as PROFILE_DIR, RESULTS as CACHE_DIR

SOCIAL_DOMAINS = ("vk.com", "vk.ru", "instagram.com", "taplink", "t.me", "wa.me", "ok.ru",
                  "facebook.com", "youtube.com", "tiktok.com", "max.ru", "linktr.ee", "dzen.ru")

GET_PROFILES = """(()=>{const p=window.initialState?.data?.entity?.profile||{};
return JSON.stringify(Object.values(p).map(x=>x.data))})()"""
GET_SEARCH = """(()=>{const s=window.initialState?.data?.search?.profile||{};
const v=Object.values(s)[0]; return v?JSON.stringify({total:v.data.total,pages:v.data.pages,
ids:(v.data.items||v.data.ids||[])}):null})()"""
GET_REVIEWS = """(()=>{const q=(window.__REACT_QUERY_STATE__?.queries||[])
.find(q=>q.queryKey&&q.queryKey[0]==='fetchEntityReviews');
const pages=q?.state?.data?.pages||[]; return JSON.stringify(pages.flatMap(p=>p.items||[]))})()"""


class Scraper:
    def __init__(self, log=print, progress=lambda **k: None):
        self.log = log
        self.progress = progress
        self.stop_flag = False
        self._pw = self._ctx = None

    # ---------- браузер ----------
    async def start(self):
        self._pw = await async_playwright().start()
        kw = dict(user_data_dir=str(PROFILE_DIR), headless=False, locale="ru-RU",
                  viewport={"width": 1280, "height": 860})
        try:
            self._ctx = await self._pw.chromium.launch_persistent_context(channel="msedge", **kw)
        except Exception:
            self._ctx = await self._pw.chromium.launch_persistent_context(**kw)

    async def close(self):
        try:
            if self._ctx:
                await self._ctx.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def _goto(self, page, url, settle=2500):
        for attempt in range(3):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                break
            except Exception as e:
                self.log(f"Повтор загрузки ({attempt + 1}): {e.__class__.__name__}")
                await asyncio.sleep(3)
        await self._wait_captcha(page)
        await page.wait_for_timeout(settle)

    async def _wait_captcha(self, page):
        warned = False
        while "captcha" in (await page.title()).lower():
            if not warned:
                self.log("⚠ 2ГИС показал капчу — реши её в окне браузера, парсер подождёт.")
                self.progress(captcha=True)
                await page.bring_to_front()
                warned = True
            if self.stop_flag:
                return
            await asyncio.sleep(2)
        if warned:
            self.progress(captcha=False)
            self.log("Капча пройдена, продолжаю.")

    # ---------- город ----------
    async def resolve_city(self, page, city: str) -> dict:
        cache = CACHE_DIR / "cities.json"
        known = json.loads(cache.read_text("utf8")) if cache.exists() else {}
        if city.lower() in known:
            return known[city.lower()]
        await self._goto(page, "https://2gis.ru/search/" + quote(city))
        items = json.loads(await page.evaluate(GET_PROFILES))
        cities = [d for d in items if d.get("type") == "adm_div" and d.get("city_alias")]
        if not cities:
            cities = [d for d in items if d.get("type") == "adm_div"]
        if not cities:
            raise ValueError(f"Не нашёл город «{city}» в 2ГИС")
        c = cities[0]
        alias = c.get("city_alias")
        if not alias:  # посёлок внутри другого проекта — берём алиас проекта из adm_div
            alias = next((a.get("city_alias") for a in c.get("adm_div", []) if a.get("city_alias")), None)
        res = {"name": c["name"], "alias": alias, "id": c["id"]}
        known[city.lower()] = res
        cache.write_text(json.dumps(known, ensure_ascii=False, indent=1), "utf8")
        return res

    # ---------- поиск ----------
    async def search(self, page, alias: str, niche: str, max_pages: int = 40):
        firms, seen = [], set()
        for n in range(1, max_pages + 1):
            if self.stop_flag:
                break
            url = f"https://2gis.ru/{alias}/search/{quote(niche)}" + (f"/page/{n}" if n > 1 else "")
            await self._goto(page, url, settle=1500)
            raw = await page.evaluate(GET_SEARCH)
            meta = json.loads(raw) if raw else {}
            new = 0
            for d in json.loads(await page.evaluate(GET_PROFILES)):
                if d.get("type") != "branch" or d["id"] in seen:
                    continue
                seen.add(d["id"])
                firms.append(d)
                new += 1
            pages = meta.get("pages") or 1
            self.log(f"Страница {n}/{pages}: +{new} (всего {len(firms)} из {meta.get('total', '?')})")
            self.progress(stage="search", done=n, total=pages)
            if new == 0 or n >= pages:
                break
        return firms

    # ---------- карточка + отзывы ----------
    async def firm_details(self, page, alias: str, firm_id: str, scrolls: int = 2):
        extra = []

        async def on_resp(r):
            if f"/branches/{firm_id}/reviews" in r.url and "rated=false" not in r.url:
                try:
                    extra.extend((await r.json()).get("reviews", []))
                except Exception:
                    pass

        page.on("response", on_resp)
        try:
            await self._goto(page, f"https://2gis.ru/{alias}/firm/{firm_id}/tab/reviews", settle=1500)
            profiles = json.loads(await page.evaluate(GET_PROFILES))
            reviews = json.loads(await page.evaluate(GET_REVIEWS) or "[]")
            for _ in range(scrolls):  # прокрутка панели подгружает ещё отзывы
                await page.mouse.move(250, 600)
                await page.mouse.wheel(0, 6000)
                await page.wait_for_timeout(900)
        finally:
            page.remove_listener("response", on_resp)
        seen = {r.get("id") for r in reviews}
        reviews += [r for r in extra if r.get("id") not in seen]
        prof = next((d for d in profiles if d.get("id") == firm_id), profiles[0] if profiles else {})
        return prof, reviews

    # ---------- основной сценарий ----------
    async def run(self, city: str, niche: str, min_rating=0.0, min_reviews=0,
                  only_city=True, limit=0, workers=2):
        # больше 3 вкладок: браузер и 2ГИС не успевают, страницы падают по таймауту
        workers = max(1, min(int(workers or 2), 3))
        await self.start()
        try:
            page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
            c = await self.resolve_city(page, city)
            self.log(f"Город: {c['name']} → 2gis.ru/{c['alias']}")
            raw = await self.search(page, c["alias"], niche)
            rows = [basic_row(d) for d in raw]
            if only_city:
                rows = [r for r in rows if not r["city"] or r["city"] == c["name"]]
            rows = [r for r in rows if (r["rating"] or 0) >= min_rating and r["reviews"] >= min_reviews]
            if limit:
                rows = rows[:limit]
            self.log(f"После предфильтра: {len(rows)} компаний. Собираю контакты и отзывы…")

            # дозбор: карточки, уже проверенные в прошлый раз, не открываем заново
            fname = CACHE_DIR / f"{c['alias']}__{slug(niche)}.json"
            old = {}
            if fname.exists():
                try:
                    old = {x["id"]: x for x in json.loads(fname.read_text("utf8"))["items"] if is_checked(x)}
                except Exception:
                    old = {}
            for i, r in enumerate(rows):
                if r["id"] in old:
                    rows[i] = {**old[r["id"]], "checked": True}
            todo = [r for r in rows if not r.get("checked")]
            if old:
                self.log(f"Уже проверено раньше: {len(rows) - len(todo)}, осталось открыть: {len(todo)}")

            queue = asyncio.Queue()
            for r in todo:
                queue.put_nowait(r)
            done = 0
            t0 = time.time()

            async def worker(pg):
                nonlocal done
                while not queue.empty() and not self.stop_flag:
                    r = queue.get_nowait()
                    try:
                        prof, reviews = await self.firm_details(pg, c["alias"], r["id"])
                        enrich(r, prof, reviews)
                        r["checked"] = True
                    except Exception as e:
                        r["checked"] = False
                        msg = str(e)
                        if "closed" in msg:  # окно браузера закрыто/упало — дальше смысла нет
                            if not self.stop_flag:
                                self.log("❌ Браузер закрылся — останавливаю сбор, проверенное сохраню.")
                            self.stop_flag = True
                        else:
                            r["error"] = msg[:200]
                    done += 1
                    eta = (time.time() - t0) / done * (len(todo) - done)
                    self.progress(stage="details", done=done, total=len(todo), eta=int(eta))
                    if done % 5 == 0 or done == len(todo):
                        self.log(f"Карточки: {done}/{len(todo)}")

            pages = [page] + [await self._ctx.new_page() for _ in range(max(0, workers - 1))]
            await asyncio.gather(*(worker(p) for p in pages))

            need = [r for r in rows if is_checked(r) and r.get("site") and not r.get("site_check")]
            if need:
                self.log(f"Проверяю сайты: {len(need)}…")
                self.progress(stage="sites", done=0, total=len(need))
                checks = await asyncio.to_thread(check_many, [r["site"][0] for r in need])
                for r in need:
                    r["site_check"] = checks.get(r["site"][0])
            finalize(rows, niche)
            hot = sum(1 for r in rows if r.get("verdict") == "hot")
            self.log(f"Горячих: {hot}, можно писать: {sum(1 for r in rows if r.get('verdict') == 'ok')}, "
                     f"не тратить время: {sum(1 for r in rows if r.get('verdict') == 'skip')}")

            for r in rows:
                r.setdefault("checked", False)  # до этих карточек сбор не дошёл (стоп)
            # не теряем проверенные раньше компании, которые не попали в этот запуск (лимит/фильтр)
            current = {r["id"] for r in rows}
            rows += [{**x, "checked": True} for i, x in old.items() if i not in current]
            unchecked = sum(1 for r in rows if not r["checked"])
            if unchecked:
                self.log(f"Не проверено карточек: {unchecked} — запусти сбор ещё раз, чтобы дособрать.")
            result = {"city": c["name"], "alias": c["alias"], "niche": niche,
                      "created": time.strftime("%Y-%m-%d %H:%M"), "items": rows}
            fname.write_text(json.dumps(result, ensure_ascii=False, indent=1), "utf8")
            self.log(f"Готово: {len(rows)} компаний, сохранено в results/{fname.name}")
            return result
        finally:
            await self.close()


# ---------- преобразование данных ----------
def slug(s):
    return re.sub(r"[^\wа-яё]+", "_", s.lower()).strip("_")


def is_checked(r: dict) -> bool:
    """Карточка реально открыта и разобрана (для старых файлов без поля checked — по наличию контактов)."""
    # карточки старого формата (без разбора отзывов) пересобираем заново
    return bool(r.get("checked")) and r.get("v", 0) >= ANALYSIS_V


def finalize(rows: list, niche: str = "") -> None:
    """Вердикт и первое сообщение. Можно вызывать повторно (при изменении правил)."""
    for r in rows:
        if not is_checked(r):
            continue
        if r.get("reviews_raw") is not None:
            r["insights"] = analyze(r["reviews_raw"])
        r["verdict"], r["score"], r["reasons"] = verdict(r)
        r["hook"] = hook(r, niche)


def basic_row(d: dict) -> dict:
    rv = d.get("reviews") or {}
    city = next((a["name"] for a in d.get("adm_div", []) if a.get("type") == "city"), "")
    return {
        "id": d["id"],
        "name": d.get("name", ""),
        "address": d.get("address_name") or d.get("full_name", ""),
        "city": city,
        "rubrics": ", ".join(r["name"] for r in d.get("rubrics", [])),
        "rating": rv.get("general_rating"),
        "reviews": rv.get("general_review_count") or 0,
        "branches": (d.get("org") or {}).get("branch_count", 1),
        "url": f"https://2gis.ru/firm/{d['id']}",
    }


def _site_domain(c: dict) -> str:
    txt = c.get("text") or c.get("print_text") or c.get("value") or ""
    if "link.2gis" in txt or txt.startswith("http"):
        u = c.get("url") or txt
        m = re.search(r"https?%3A%2F%2F[^&]+|https?://[^&\s]+$", u)
        txt = unquote(m.group(0)) if m and "link.2gis" in u else txt
    host = urlparse(txt if "://" in txt else "http://" + txt).netloc or txt
    return host.lower().removeprefix("www.")


def enrich(r: dict, prof: dict, reviews: list):
    ch = {k: [] for k in ("phone", "whatsapp", "telegram", "max", "vk", "instagram", "email", "site", "social_site",
                          "booking")}
    for g in prof.get("contact_groups") or []:
        for c in g.get("contacts", []):
            t = (c.get("type") or "").lower()
            val = c.get("value") or c.get("url") or c.get("text") or ""
            low = (val + " " + (c.get("text") or "") + " " + (c.get("url") or "")).lower()
            if t == "phone":
                ch["phone"].append(c.get("value") or c.get("text"))
            elif t == "whatsapp" or "wa.me" in low:
                ch["whatsapp"].append(val)
            elif t == "telegram" or "t.me/" in low:
                ch["telegram"].append(val)
            elif t == "max" or "max.ru" in low:
                ch["max"].append(val)
            elif t in ("vkontakte", "vk") or "vk.com" in low:
                ch["vk"].append(val)
            elif t == "instagram" or "instagram" in low:
                ch["instagram"].append(val)
            elif t == "email":
                ch["email"].append(c.get("value") or c.get("text"))
            elif t == "website":
                dom = _site_domain(c)
                svc = next((b for b in BOOKING_DOMAINS if b in low), None)
                if svc or ("." not in dom and "запис" in dom):
                    ch["booking"].append(svc or dom)  # ссылка на онлайн-запись (YCLIENTS, DIKIDI…)
                elif "." not in dom or any(s in dom for s in SOCIAL_DOMAINS) or dom.endswith("2gis.biz"):
                    ch["social_site"].append(dom)  # соцсеть / мини-визитка 2ГИС — не настоящий сайт
                else:
                    ch["site"].append(dom)
    for k, v in ch.items():
        r[k] = list(dict.fromkeys(x for x in v if x))

    mentions, neg = [], 0
    for rv in reviews:
        txt = rv.get("text") or ""
        if (rv.get("rating") or 5) <= 2:
            neg += 1
        mentions += [{**m, "src": "отзыв"} for m in find_lpr(txt)]
        ans = (rv.get("official_answer") or {}).get("text") if rv.get("official_answer") else ""
        mentions += [{**m, "src": "ответ компании"} for m in find_lpr(ans or "")]
    r["neg_reviews"] = neg
    r["reviews_checked"] = len(reviews)
    r["lpr"] = summarize(mentions)
    r["reviews_raw"] = compact_reviews(reviews)
    r["insights"] = analyze(r["reviews_raw"])
    r["v"] = ANALYSIS_V
    if prof.get("reviews"):
        r["rating"] = prof["reviews"].get("general_rating", r["rating"])
        r["reviews"] = prof["reviews"].get("general_review_count", r["reviews"])
