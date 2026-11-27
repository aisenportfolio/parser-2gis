"""Разбор отзывов и вердикт «стоит ли писать» — под продажу сайтов с онлайн-записью.

Всё на правилах (ключевые слова), без ИИ: бесплатно, мгновенно, без интернета.
"""
import re
from datetime import datetime, timezone

ANALYSIS_V = 2  # растёт при изменении формата: старые карточки при дозборе пересобираются


def _rx(*parts):
    return re.compile("|".join(parts))


# Клиенты не могут связаться / записаться — главный повод для сайта с онлайн-записью
PAIN_BOOKING = _rx(
    r"не\s+(?:могу|смог\w*|получается|удалось)\s+дозвон", r"не\s+дозвон", r"дозвониться\s+(?:невозможно|нереально|сложно|трудно)",
    r"(?:невозможно|нереально|сложно|трудно)\s+дозвон", r"не\s+бер[еу]т\s+трубк", r"трубку\s+не\s+бер",
    r"не\s+отвеча\w*\s+(?:на\s+)?(?:звонк|телефон|сообщен|в\s+ватсап|в\s+whatsapp|в\s+вотсап)",
    r"(?:ватсап|whatsapp|вотсап|телеграм|директ)\w*[^.!?]{0,30}не\s+(?:ответ|отвеча|прочит)",
    r"не\s+перезвон", r"обещали\s+перезвон", r"автоответчик", r"игнорир\w*\s+(?:звонк|сообщ)",
    r"(?:не|невозможно|сложно|трудно|нельзя)\s+(?:было\s+)?записат", r"запис\w*[^.!?]{0,40}(?:проблем|невозможно|сложно|не\s+получ)",
    r"перепутал\w*\s+(?:время|запис|день)", r"(?:забыли|потеряли)\s+(?:про\s+|о\s+|мою\s+)?запис", r"нет\s+(?:моей\s+)?записи",
    r"не\s+было\s+(?:в\s+)?(?:записи|журнале)", r"записал\w*\s+на\s+(?:другое|не\s+то)", r"(?:нет|отсутствует)\s+онлайн[\s-]*запис",
    r"онлайн[\s-]*запись\s+не\s+работ", r"живая\s+очередь", r"прождал\w*", r"ждал\w*\s+(?:больше|более|около)?\s*(?:часа|\d+\s*мин)",
)
# Клиенты не находят информацию — повод для удобного сайта
PAIN_INFO = _rx(
    r"цен\w*\s+(?:не\s+указан|нигде\s+нет|отлича|выше,?\s+чем|на\s+сайте)", r"(?:не\s+нашл?\w*|не\s+смогл?\w*\s+найти|нигде\s+нет)\s+(?:цен|прайс|информац|сайт|расписан)",
    r"нет\s+(?:сайта|прайса|цен)", r"(?:сайт|прайс)\w*\s+(?:устарел|не\s+работ|не\s+открыва)",
    r"(?:график|часы|время|режим)\s+работы[^.!?]{0,40}(?:не\s+соответ|неправильн|неверн|другое|не\s+совпад)",
    r"(?:было\s+)?закрыто,?\s+хотя", r"(?:адрес|местоположен)\w*[^.!?]{0,30}(?:неправильн|неверн|не\s+соответ)", r"долго\s+искал\w*",
)
# Работа с этой компанией будет токсичной
RED_IN_REVIEW = _rx(r"угрож", r"угроз", r"(?:руководител|директор|владел|хозя)\w*[^.!?]{0,40}(?:хам|оскорб|нагруб|орал|крич)")
RED_IN_ANSWER = _rx(r"угрож", r"угроз", r"(?:вы\s+)?сами\s+виноват", r"не\s+нравится[^.!?]{0,25}не\s+(?:приходите|ходите)",
                    r"неадекват", r"обратимся\s+в\s+(?:суд|полиц)", r"клевет")

PAIN_TITLES = {"booking": "Не дозвониться / не записаться", "info": "Не найти цены и информацию"}


# «записаться без проблем», «проблем не составляет» — это похвала, а не жалоба
POSITIVE = re.compile(r"без\s+(?:проблем|труда|сложност)|проблем\s+(?:не|нет)|не\s+составля|никаких\s+проблем|"
                      r"легко|быстро|удобн|всегда\s+(?:можно|получается)|не\s+было\s+проблем")


def _first_real(rx, low):
    for m in rx.finditer(low):
        window = low[max(0, m.start() - 15): m.end() + 30]
        if not POSITIVE.search(window):
            return m
    return None


def _norm(t):
    return (t or "").lower().replace("ё", "е")


def _date(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _quote(text, m, pad=90):
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return ("…" if a else "") + text[a:b].replace("\n", " ").strip() + ("…" if b < len(text) else "")


def compact_reviews(reviews):
    """Короткая копия отзывов в файле: можно пересчитать вердикт без повторного сбора."""
    out = []
    for r in reviews:
        ans = r.get("official_answer") or {}
        out.append({"t": (r.get("text") or "")[:700], "r": r.get("rating"), "d": (r.get("date_created") or "")[:10],
                    "a": (ans.get("text") or "")[:400] if isinstance(ans, dict) else ""})
    return out


def analyze(reviews_c):
    """reviews_c — список из compact_reviews()."""
    now = datetime.now(timezone.utc)
    pains = {"booking": [], "info": []}
    red = []
    dates, ratings_by_date = [], []
    answered = neg = neg_answered = 0
    for rv in reviews_c:
        text, low = rv["t"], _norm(rv["t"])
        d = _date(rv["d"] + "T00:00:00+00:00") if rv.get("d") else None
        if d:
            dates.append(d)
            if rv.get("r"):
                ratings_by_date.append((d, rv["r"]))
        if rv.get("a"):
            answered += 1
        if rv.get("r") and rv["r"] <= 2:
            neg += 1
            neg_answered += bool(rv.get("a"))
        for key, rx in (("booking", PAIN_BOOKING), ("info", PAIN_INFO)):
            m = _first_real(rx, low)
            if m:
                pains[key].append({"q": _quote(text, m), "d": rv.get("d"), "r": rv.get("r")})
        m = RED_IN_REVIEW.search(low)
        if m:
            red.append({"q": _quote(text, m), "d": rv.get("d"), "src": "отзыв"})
        if rv.get("a"):
            m = RED_IN_ANSWER.search(_norm(rv["a"]))
            if m:
                red.append({"q": _quote(rv["a"], m), "d": rv.get("d"), "src": "ответ компании"})

    for v in pains.values():
        v.sort(key=lambda x: x.get("d") or "", reverse=True)
    last = max(dates) if dates else None
    ratings_by_date.sort(key=lambda x: x[0], reverse=True)
    recent = [r for _, r in ratings_by_date[:10]]
    allr = [r for _, r in ratings_by_date]
    trend = None
    if len(allr) >= 15 and recent:
        trend = round(sum(recent) / len(recent) - sum(allr) / len(allr), 2)
    n = len(reviews_c)
    return {
        "pains": {k: {"count": len(v), "quotes": v[:3]} for k, v in pains.items()},
        "red_flags": red[:3],
        "last_review": last.strftime("%Y-%m-%d") if last else None,
        "days_since_review": (now - last).days if last else None,
        "reviews_90d": sum(1 for d in dates if (now - d).days <= 90),
        "reply_rate": round(100 * answered / n) if n else None,
        "neg_total": neg,
        "neg_answered": neg_answered,
        "trend": trend,
    }


def verdict(r):
    """Возвращает (метка, баллы, причины). Метки: hot / ok / low / skip."""
    ins = r.get("insights") or {}
    site = r.get("site_check") or {}
    reasons, score = [], 0

    if ins.get("red_flags"):
        return "skip", -10, ["Красный флаг: конфликтный владелец/угрозы в отзывах"]
    dsr = ins.get("days_since_review")
    if dsr is not None and dsr > 365:
        score -= 4
        reasons.append(f"Новых отзывов нет {dsr // 30} мес. — проверь, работают ли ещё")

    has_booking = bool(r.get("booking")) or site.get("booking") is True
    if not r.get("site"):
        score += 3
        reasons.append("Нет своего сайта" + (" (только соцсети/визитка 2ГИС)" if r.get("social_site") else ""))
    elif site.get("reachable") is False:
        score += 3
        reasons.append("Сайт не открывается")
    else:
        if site.get("booking") is False and not r.get("booking"):
            score += 2
            reasons.append("На сайте нет онлайн-записи" + (" — только заявка/звонок" if site.get("callback_only") else ""))
        if site.get("mobile") is False:
            score += 2
            reasons.append("Сайт не адаптирован под телефон")
        if site.get("year") and site["year"] <= datetime.now().year - 3:
            score += 1
            reasons.append(f"Сайт давно не обновлялся (© {site['year']})")
    if has_booking:
        score -= 2
        reasons.append("Онлайн-запись уже есть" + (f" ({site['booking_via']})" if site.get("booking_via") else ""))

    pb = (ins.get("pains") or {}).get("booking", {}).get("count", 0)
    pi = (ins.get("pains") or {}).get("info", {}).get("count", 0)
    if pb:
        score += min(6, 2 * pb)
        reasons.append(f"Жалобы на дозвон/запись: {pb}")
    if pi:
        score += min(2, pi)
        reasons.append(f"Жалобы на цены/инфо: {pi}")
    if ins.get("neg_total", 0) >= 2 and ins.get("neg_answered", 0) == 0:
        score += 1
        reasons.append("На негатив не отвечают — репутацией никто не занимается")
    if (ins.get("reviews_90d") or 0) >= 3:
        score += 1
        reasons.append(f"Активный: {ins['reviews_90d']} отзывов за 3 мес.")
    if (r.get("reviews") or 0) >= 30:
        score += 1
    if ins.get("trend") is not None and ins["trend"] <= -0.5:
        score += 1
        reasons.append("Свежие отзывы хуже старых — сейчас проседают")

    label = "hot" if score >= 6 else "ok" if score >= 3 else "low"
    if label == "low" and not reasons:
        reasons.append("Явных поводов нет: сайт и запись в порядке")
    return label, score, reasons


def hook(r, niche=""):
    """Первое сообщение под главную найденную боль."""
    from lpr import FIRST_NAMES, PATRONYMIC_RE
    words = (r.get("lpr") or [{}])[0].get("name", "").split()
    # обращение: «Имя Отчество» или «Имя», только если имя в именительном падеже
    i = next((k for k, w in enumerate(words) if w.lower() in FIRST_NAMES), None)
    first = ""
    if i is not None:
        first = words[i]
        if i + 1 < len(words) and PATRONYMIC_RE.match(words[i + 1]) and words[i + 1].endswith(("ич", "на")):
            first += " " + words[i + 1]
    hi = f"Здравствуйте, {first}!" if first else "Здравствуйте!"
    name = re.split(r",", r.get("name", ""))[0].strip()
    ins, site = r.get("insights") or {}, r.get("site_check") or {}
    pb = (ins.get("pains") or {}).get("booking", {}).get("count", 0)
    offer = "Мы делаем удобные сайты с онлайн-записью: клиент сам выбирает услугу и время 24/7, без звонков."
    if pb:
        body = f"Увидел в 2ГИС отзывы о {name}: клиенты пишут, что сложно дозвониться и записаться."
    elif not r.get("site"):
        body = f"Нашёл {name} в 2ГИС — у вас отличные отзывы, но нет своего сайта: клиенты не могут посмотреть цены и записаться онлайн."
    elif site.get("booking") is False:
        body = f"Посмотрел сайт {name} — на нём нет онлайн-записи, клиентам приходится звонить."
    elif site.get("mobile") is False:
        body = f"Посмотрел сайт {name} — с телефона им неудобно пользоваться, а большинство клиентов заходит именно с телефона."
    else:
        body = f"Нашёл {name} в 2ГИС."
    return f"{hi} {body} {offer} Могу показать пример — интересно?"
