"""Быстрая проверка сайта компании: открывается ли, есть ли онлайн-запись, адаптирован ли под телефон."""
import re
from concurrent.futures import ThreadPoolExecutor

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept-Language": "ru-RU,ru;q=0.9"}

# сервисы онлайн-записи, которые встраивают на сайты
BOOKING_SERVICES = {
    "yclients": "YCLIENTS", "altegio": "Altegio", "dikidi": "DIKIDI", "sonline": "Sonline", "easyweek": "EasyWeek",
    "rubitime": "Rubitime", "bookon": "Bookon", "medflex": "MedFlex", "prodoctorov": "ПроДокторов", "napopravku": "НаПоправку",
    "infoclinica": "Инфоклиника", "1c-umi": "1С", "arnica": "Arnica", "beautybox": "BeautyBox", "salonzapis": "SalonZapis",
    "simplybook": "SimplyBook", "zapisi.online": "Записи.онлайн", "booking.dikidi": "DIKIDI", "uniqom": "UniQom",
    "clinicon": "Clinicon", "denta-pro": "Дента-Про", "identa": "iDent", "yandex.ru/maps/org": None,
}
BOOKING_TEXT = re.compile(r"онлайн[\s-]*запис|записаться\s+онлайн|запись\s+онлайн|online[\s-]*booking|book\s+now|"
                          r"выбрать\s+(?:время|дату|мастера|врача)|свободн\w+\s+(?:время|окн)", re.I)
BOOKING_DOMAINS = ("yclients", "dikidi", "altegio", "sonline", "easyweek", "rubitime", "simplybook", "bookon")


def check(domain: str, timeout: float = 8.0) -> dict:
    res = {"domain": domain, "reachable": False, "booking": None, "booking_via": None, "mobile": None,
           "https": None, "year": None}
    for scheme in ("https://", "http://"):
        try:
            resp = requests.get(scheme + domain, headers=UA, timeout=timeout, allow_redirects=True)
        except Exception:
            continue
        if resp.status_code >= 400:
            continue
        html = resp.text[:600_000]
        low = html.lower()
        res["reachable"] = True
        res["https"] = resp.url.startswith("https://")
        res["mobile"] = bool(re.search(r'<meta[^>]+name=["\']?viewport', low))
        via = next((name for key, name in BOOKING_SERVICES.items() if key in low and name), None)
        res["booking_via"] = via
        res["booking"] = bool(via) or bool(BOOKING_TEXT.search(low))
        res["callback_only"] = not res["booking"] and "записат" in low  # есть «Записаться», но это заявка/звонок
        years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–—]\s*)?(20\d\d)", low)]
        res["year"] = max(years) if years else None
        break
    return res


def check_many(domains, workers=8):
    domains = list(dict.fromkeys(d for d in domains if d))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(zip(domains, ex.map(check, domains)))


if __name__ == "__main__":
    import json, sys
    print(json.dumps(check_many(sys.argv[1:]), ensure_ascii=False, indent=1))
