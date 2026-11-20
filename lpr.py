"""Поиск упоминаний ЛПР (руководитель, директор, владелец...) в отзывах и ответах компании."""
import re

ROLE = (
    r"(?:ген(?:еральн\w*)?\.?\s*)?директор\w*|гендиректор\w*|руководител\w*|руководств\w*|"
    r"владел\w*|владелиц\w*|хозя\w*|собственни\w*|основател\w*|учредител\w*|"
    r"управляющ\w*|шеф\w*|начальни\w*|главн\w* врач\w*|главврач\w*|ип\b"
)

FIRST_NAMES = set("""
александр алексей анатолий андрей антон аркадий арсений артем артём артур борис вадим валентин валерий василий виктор
виталий владимир владислав вячеслав геннадий георгий глеб григорий давид даниил данил денис дмитрий евгений егор
иван игорь илья кирилл константин лев леонид максим марат марк михаил никита николай олег павел петр пётр роман
руслан сергей станислав степан тимур тимофей федор фёдор филипп эдуард юрий ярослав айсен айдар азат ринат рустам
ильдар ильнур эльдар камиль шамиль заур аслан алан мурат тагир магомед ахмед ибрагим расул саид арсен гамлет ашот
араик тигран вардан самвел рафаэль рафаэл нурлан ерлан айтал айхал эрчим ньургун туйгун
александра алёна алена алина алла анастасия ангелина анна антонина валентина валерия вера вероника виктория галина
дарья диана евгения екатерина елена елизавета жанна зоя инна ирина карина кристина ксения лариса лидия любовь людмила
маргарита марина мария милана надежда наталья наталия нина оксана олеся ольга полина регина светлана софия софья
тамара татьяна ульяна эльвира юлия яна гульнара гульназ айгуль альбина лилия лиана диляра сардана айыына туйаара
""".split())

SURNAME_RE = re.compile(
    r"^[А-ЯЁ][а-яё]+(?:ов|ев|ёв|ин|ын|ск|цк|ян|енко|ук|юк)(?:а|у|ым|ой|ого|ому|ий|ая|ую|ым|ых)?$")
PATRONYMIC_RE = re.compile(r"^[А-ЯЁ][а-яё]+(?:ович|евич|ьич|овн|евн|ичн)\w{0,3}$")
ROLE_RE = re.compile(ROLE, re.IGNORECASE)
CAP_SEQ = re.compile(r"[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){0,2}")
STOP_BETWEEN = re.compile(r"[.!?\n]")  # имя и роль должны быть в одном предложении
WINDOW = 60


def _is_name_word(w: str) -> bool:
    lw = w.lower()
    if lw in FIRST_NAMES or SURNAME_RE.match(w) or PATRONYMIC_RE.match(w):
        return True
    return any(len(n) > 3 and lw.startswith(n[:-1]) and len(lw) - len(n) <= 2 for n in FIRST_NAMES)


def _clean_name(raw: str) -> str | None:
    good = []
    for w in raw.split():
        if _is_name_word(w):
            good.append(w)
        else:
            break
    # одиночная фамилия без имени даёт много ложных срабатываний — нужно имя или отчество рядом
    if len(good) == 1 and not (good[0].lower() in FIRST_NAMES or
                               any(len(n) > 3 and good[0].lower().startswith(n[:-1]) for n in FIRST_NAMES)):
        return None
    return " ".join(good) if good else None


def find_lpr(text: str) -> list[dict]:
    if not text:
        return []
    out = []
    for rm in ROLE_RE.finditer(text):
        role = rm.group(0).strip().lower()
        # после роли: «директор клиники "Х", Иван Петров»
        after = text[rm.end(): rm.end() + WINDOW]
        cut = STOP_BETWEEN.search(after)
        after = after[: cut.start()] if cut else after
        # до роли: «Юрию Андреевичу Цветкову. Это главный врач» — разрешаем одну точку
        before = text[max(0, rm.start() - WINDOW): rm.start()]
        found = None
        for cm in CAP_SEQ.finditer(after):
            if cm.start() == 0 and after[:1].isupper():
                continue
            found = _clean_name(cm.group(0))
            if found:
                break
        if not found:
            cands = [c for c in CAP_SEQ.finditer(before)]
            for cm in reversed(cands):
                tail = before[cm.end():]
                if len(STOP_BETWEEN.findall(tail)) > 1:
                    break
                found = _clean_name(cm.group(0))
                if found:
                    break
        if found:
            out.append({"role": role, "name": found, "snippet": _snip(text, rm)})
    return out


def _snip(text, m, pad=70):
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return ("…" if a else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


def summarize(mentions: list[dict]) -> list[dict]:
    """Склеить повторы: одно имя -> роль, сколько раз упомянуто, пример."""
    groups: list[dict] = []
    for x in mentions:
        stems = {w.lower()[:5] for w in x["name"].split()}
        g = next((g for g in groups if g["_stems"] & stems), None)
        if not g:
            g = {**x, "count": 0, "_stems": set()}
            groups.append(g)
        g["_stems"] |= stems
        g["count"] += 1
        # предпочитаем полное ФИО в именительном падеже (как в подписи компании)
        if len(x["name"].split()) > len(g["name"].split()) or x["name"].split()[0].endswith(("ов", "ев", "ин")):
            g["name"], g["snippet"] = x["name"], x["snippet"]
    for g in groups:
        g.pop("_stems")
    return sorted(groups, key=lambda v: -v["count"])


if __name__ == "__main__":
    t = ("Но Руководитель Токарев Алексей устроил цирк. Спасибо директору Ивану! "
         "Хозяин Арсен всегда на месте. С уважением, управляющий Мария.")
    for r in summarize(find_lpr(t)):
        print(r)
