"""Проверка иврита книги перед публикацией (блоки B и C планки запуска).

    python tools/audit_book_hebrew.py books/robot-cat-alef/book-data.json
    python tools/audit_book_hebrew.py --all            # все books/*/book-data.json
    python tools/audit_book_hebrew.py --all --no-nakdan  # только локальные проверки

Что проверяет:
  * огласовку каждого слова — сверкой с Dicta Nakdan (независимый
    детерминированный огласовщик, «второе мнение» к Gemini). Слово, чьей
    огласовки нет ни среди вариантов Nakdan, ни среди «почти совпадений»
    (разница только в дагеше/метеге), попадает в отчёт;
  * лемму — у Nakdan у каждого варианта есть словарная форма; если наша
    лемма совпадает со словоформой, а Nakdan видит в слове приставку
    (ה/ו/ב/ל/מ/ש/כ), лемма «с приставкой» попадает в отчёт;
  * удвоенные соседние слова (баг quest-gimel: «הָעֵמֶק הָעֵמֶק»), кроме
    законных повторов (סוף סוף, לאט לאט...);
  * чужие алфавиты: арабица, тайский, CJK, латиница/кириллица в ивритских
    полях и иврит в русских полях (кроме оставленных намеренно את/של в literal);
  * аудио: файл существует, у предложения с аудио есть пословные тайминги;
  * пустые tr/pos/lemma.

Ответы Nakdan кэшируются по хешу текста предложения (повторный прогон
не ходит в сеть, пока текст не изменился). Отчёты пишутся в --out
(по умолчанию E:\\NEW BIG PROJ\\reports\\hebrew_audit\\): <slug>.md на книгу
и summary.json по всем. Код выхода 1, если есть находки.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time
import unicodedata

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = r"E:\NEW BIG PROJ\reports\hebrew_audit"
NAKDAN_URL = "https://nakdan-2-0.loadbalancer.dicta.org.il/api"

HEB_LETTERS = re.compile(r"[\u05D0-\u05EA]")
HEB_ANY = re.compile(r"[\u0591-\u05C7\u05D0-\u05EA\u05F0-\u05F4\uFB1D-\uFB4F]")
NIQQUD = re.compile(r"[\u0591-\u05C7]")
FOREIGN = {
    "арабица": re.compile(r"[\u0600-\u06FF\u0750-\u077F]"),
    "тайский": re.compile(r"[\u0E00-\u0E7F]"),
    "CJK": re.compile(r"[\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF]"),
    "кириллица": re.compile(r"[\u0400-\u04FF]"),
    "латиница": re.compile(r"[A-Za-z]"),
}
# Поля, где должен быть только иврит (+знаки), и где только русский.
HEBREW_FIELDS = ("t", "lemma", "root")
RUSSIAN_FIELDS = ("tr", "pos", "extra")
LEGIT_REPEATS = {"סוף", "לאט", "מעט", "רגע", "שוב", "כן", "לא", "עוד", "יותר", "מאוד",
                 "צעד", "אט", "טוב", "די", "הנה", "בום", "טיק", "טוק", "הא", "חה"}
PREFIX_LETTERS = set("הובלמשכ")
DAGESH_ETC = re.compile(r"[\u05BC\u05BD\u05BF\u05C4\u05C5]")  # дагеш, метег, рафе, точки сверху/снизу


def nfc(s):
    return unicodedata.normalize("NFC", s)


def bare(s):
    """Только буквы иврита, без огласовки и знаков."""
    return "".join(HEB_LETTERS.findall(s or ""))


def heb_word(s):
    """Слово как оно в тексте, но без пунктуации вокруг (огласовка остаётся)."""
    return nfc("".join(ch for ch in (s or "") if HEB_ANY.match(ch)))


def loose(s):
    return DAGESH_ETC.sub("", nfc(s))


HOLAM, QUBUTS, DAGESH, QAMATS = "\u05B9", "\u05BB", "\u05BC", "\u05B8"
VOWELS = re.compile("[\u05B0-\u05BB\u05C7]")  # шва…кубуц, камац катан (без дагеша и точек шин/син)
HIRIQ_TSERE_SEGOL = re.compile("[\u05B4\u05B5\u05B6]")


def clusters(s):
    out = []
    for ch in nfc(s):
        if HEB_LETTERS.match(ch) or not out:
            out.append([ch, ""])
        else:
            out[-1][1] += ch
    return out


def to_haser(s):
    """Огласованное ктив мале → то же слово в ктив хасер, чтобы сравнивать
    с Nakdan, который огласовывает по традиционной орфографии:
    בּוֹ → בֹּ (холам на вав переходит на предыдущую букву), בוּ → בֻ (шурук →
    кубуц), йуд без своего знака (матрес: כִּיסֵּא, עַכְשָׁיו) выпадает.
    Наша норма — ктив мале с огласовками, это не ошибка, а разная запись."""
    cl = clusters(s)
    res = []
    for i, (letter, marks) in enumerate(cl):
        if res and letter == "ו" and marks == HOLAM:
            res[-1][1] += HOLAM
            continue
        if res and letter == "ו" and marks == DAGESH and not VOWELS.search(res[-1][1]):
            res[-1][1] += QUBUTS
            continue
        # ктив мале удваивает согласные йуд/вав (חַיָּיב, הָאֲוָויר, תִּקְוָוה):
        # голая вторая буква рядом с такой же — это та же согласная.
        if res and letter in "יו" and marks == "" and (res[-1][0] == letter or (i + 1 < len(cl) and cl[i + 1][0] == letter)):
            continue
        # йуд без знака — матрес, только если перед ним хирик/цере/сегол
        # (כִּיסֵּא, בֵּית) или это суффикс ָיו (עַכְשָׁיו); иначе он согласный (מְיוּזָּע).
        if res and letter == "י" and marks == "" and (
                re.search(HIRIQ_TSERE_SEGOL, res[-1][1])
                or (QAMATS in res[-1][1] and i + 1 < len(cl) and cl[i + 1][0] == "ו" and i + 2 == len(cl))):
            continue
        res.append([letter, marks])
    return nfc("".join(l + m for l, m in res))


def qq(s):
    """to_haser, где холам, перенесённый с вав, считается камацем (катан)."""
    return to_haser(s).replace(HOLAM, QAMATS)


def partial_ok(ours, voc):
    """Частичная огласовка (подсказки на трудных буквах, стиль ульпана):
    слово верно, если буквы те же, а каждый наш знак есть и у варианта Nakdan
    на той же букве (отсутствие знака — не ошибка)."""
    a, b = clusters(ours), clusters(voc)
    if [x[0] for x in a] != [x[0] for x in b]:
        a, b = clusters(to_haser(ours)), clusters(to_haser(voc))
        if [x[0] for x in a] != [x[0] for x in b]:
            return False
    return all(set(ma) <= set(mb) for (_, ma), (_, mb) in zip(a, b))


def no_matres(s):
    """Для сравнения лемм ктив мале ↔ ктив хасер: без ו/י."""
    return bare(s).replace("ו", "").replace("י", "")


def tech_problems(word):
    """Технический мусор в огласованном слове, который не зависит от Nakdan:
    рафе (טֶלֶפֿוֹן), повтор одного знака на букве (מִישֵׁׁשׁ, שָָׁרְרָה),
    две гласные под одной буквой, ש без точки шин/син в огласованном слове."""
    out = []
    w = heb_word(word)
    if not NIQQUD.search(w):
        return out
    if "\u05BF" in w:
        out.append("знак рафе")
    for letter, marks in clusters(w):
        if len(set(marks)) != len(marks):
            out.append(f"повтор знака на {letter}")
        if len(VOWELS.findall(marks)) > 1:
            out.append(f"две гласные на {letter}")
        if letter == "ש" and not re.search("[\u05C1\u05C2]", marks):
            out.append("ש без точки шин/син")
    return out


# ---- Nakdan -------------------------------------------------------------

class Nakdan:
    def __init__(self, cache_path, enabled=True):
        self.cache_path = cache_path
        self.enabled = enabled
        self.cache = {}
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                self.cache = json.load(f)
        self.dirty = 0
        self.calls = 0

    def save(self):
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False)
        os.replace(tmp, self.cache_path)
        self.dirty = 0

    def analyze(self, text):
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if key in self.cache:
            return self.cache[key]
        if not self.enabled:
            return None
        import requests
        body = {"task": "nakdan", "data": text, "genre": "modern", "addmorph": True,
                "keepqq": False, "nodageshdefmem": False, "patachma": False, "keepnikud": False}
        for attempt in range(4):
            try:
                r = requests.post(NAKDAN_URL, json=body, timeout=60)
                if r.status_code == 200:
                    # В кэш — только то, что нужно сверке: слово и до 8 вариантов
                    # (огласовка + леммы). Полный ответ в ~20 раз больше.
                    res = [{"word": t.get("word", ""),
                            "options": [[o[0], sorted({m[1] for m in (o[1] or []) if isinstance(m, list) and len(m) > 1})]
                                        for o in (t.get("options") or [])[:8] if isinstance(o, list) and o]}
                           for t in r.json() if not t.get("sep")]
                    self.cache[key] = res
                    self.dirty += 1
                    self.calls += 1
                    if self.dirty >= 25:
                        self.save()
                    time.sleep(0.3)
                    return res
            except Exception as e:  # сеть/таймаут — повторим
                last = e
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Nakdan не ответил на: {text[:60]}")


def nakdan_options(tok):
    """[(огласовка без '|', есть_приставка, [леммы])] в порядке ранжирования."""
    out = []
    for opt in tok.get("options") or []:
        if not isinstance(opt, list) or not opt:
            continue
        voc = opt[0]
        lemmas = [m[1] if isinstance(m, list) else m for m in (opt[1] or [])
                  if isinstance(m, str) or (isinstance(m, list) and len(m) > 1)]
        out.append((nfc(voc.replace("|", "")), "|" in voc, lemmas))
    return out


def align(words, toks):
    """Сопоставляет наши слова токенам Nakdan по согласным (Nakdan может
    дробить слово по макафу или пунктуации). Возвращает список списков
    токенов на каждое наше слово (или None, если не сошлось)."""
    out, j = [], 0
    for w in words:
        target = bare(w)
        if not target:
            out.append([])
            continue
        acc, group = "", []
        while j < len(toks) and len(acc) < len(target):
            b = bare(toks[j].get("word", ""))
            if b:
                acc += b
                group.append(toks[j])
            j += 1
        if acc != target:
            return None
        out.append(group)
    return out


def plain_text(texts):
    return " ".join(re.sub(NIQQUD, "", t) for t in texts if bare(t))


def nakdan_groups(sents, nak, chunk_chars=1200):
    """Огласовка Nakdan для всей книги пачками по ~15 предложений (контекст
    Nakdan видит тот же, а запросов в 15 раз меньше). Возвращает
    {id предложения: группы токенов на каждое слово}. Если пачка не
    сопоставилась, её предложения переспрашиваются по одному."""
    items = []
    for s in sents:
        texts = [heb_word(w.get("t")) for w in s.get("words", [])]
        if any(bare(t) for t in texts):
            items.append((s.get("id"), texts))
    out = {}

    def run(batch):
        try:
            toks = nak.analyze("\n".join(plain_text(t) for _, t in batch))
        except RuntimeError as e:  # Nakdan недоступен — пачка непроверена, прогон идёт дальше
            print("  ! Nakdan:", str(e)[:80], file=sys.stderr)
            return False
        if toks is None:
            return False
        all_words = [w for _, t in batch for w in t]
        groups = align(all_words, toks)
        if groups is None:
            return False
        k = 0
        for sid, t in batch:
            out[sid] = groups[k:k + len(t)]
            k += len(t)
        return True

    batch, size = [], 0
    for it in items + [None]:
        if it is not None and (not batch or size + len(plain_text(it[1])) <= chunk_chars):
            batch.append(it)
            size += len(plain_text(it[1])) + 1
            continue
        if batch and not run(batch) and len(batch) > 1:
            for one in batch:
                run([one])
        batch, size = ([it], len(plain_text(it[1]))) if it is not None else ([], 0)
    return out


# ---- проверки -----------------------------------------------------------

def check_book(path, nak, rel_root, text_only=False, partial=False):
    data = json.load(open(path, encoding="utf-8"))
    sents = data.get("sentences", [])
    f = {k: [] for k in ("niqqud", "lemma_prefix", "lemma_nakdan", "double", "script", "tech",
                         "audio_missing", "timings", "empty", "align")}
    stats = {"sentences": len(sents), "words": 0, "nakdan_top": 0, "nakdan_alt": 0,
             "nakdan_male": 0, "nakdan_loose": 0, "nakdan_checked": 0}

    nak_groups = nakdan_groups(sents, nak)

    for s in sents:
        sid = s.get("id")
        words = s.get("words", [])
        stats["words"] += len(words)

        # чужие алфавиты
        for i, w in enumerate(words):
            for fld in HEBREW_FIELDS:
                v = w.get(fld) or ""
                for name, rx in FOREIGN.items():
                    if rx.search(v):
                        f["script"].append((sid, i, fld, name, v))
            for fld in RUSSIAN_FIELDS:
                v = w.get(fld) or ""
                # в extra законно пишут биньян на иврите (פָּעַל, нифъаль...)
                if (HEB_LETTERS.search(v) and fld != "extra") or FOREIGN["арабица"].search(v) or FOREIGN["тайский"].search(v) or FOREIGN["CJK"].search(v):
                    f["script"].append((sid, i, fld, "не-русское", v))
            for prob in tech_problems(w.get("t")):
                f["tech"].append((sid, i, w.get("t"), prob))
            for fld in (() if text_only else ("tr", "pos", "lemma")):
                if not (w.get(fld) or "").strip():
                    f["empty"].append((sid, i, fld, w.get("t")))
        for fld in ("fluent",):
            v = s.get(fld) or ""
            if HEB_LETTERS.search(v) or FOREIGN["арабица"].search(v) or FOREIGN["CJK"].search(v):
                f["script"].append((sid, None, fld, "не-русское", v))

        # удвоенные соседние слова
        for i in range(1, len(words)):
            a, b = bare(words[i - 1].get("t")), bare(words[i].get("t"))
            if a and a == b and a not in LEGIT_REPEATS:
                f["double"].append((sid, i, words[i].get("t"), " ".join(x.get("t", "") for x in words)))

        # аудио и тайминги
        audio = s.get("audio")
        if audio:
            if not os.path.exists(os.path.join(rel_root, audio)):
                f["audio_missing"].append((sid, audio))
            spoken = [w for w in words if bare(w.get("t"))]
            if spoken and any(w.get("start") is None or w.get("end") is None for w in spoken):
                f["timings"].append((sid,))

        # Nakdan
        texts = [heb_word(w.get("t")) for w in words]
        groups = nak_groups.get(sid)
        if groups is None:
            if any(bare(t) for t in texts) and nak.enabled:
                f["align"].append((sid, " ".join(texts)))
            continue
        for i, (w, ours, grp) in enumerate(zip(words, texts, groups)):
            if len(grp) != 1 or not bare(ours):
                continue  # слово с макафом/дробное — пропускаем, их мало
            if "имя собств" in (w.get("pos") or ""):
                continue  # огласовку имён (רֶקְס, מַיָּה) Nakdan не знает — сверять не с чем
            opts = nakdan_options(grp[0])
            if not opts:
                continue
            stats["nakdan_checked"] += 1
            vocs = [o[0] for o in opts]
            if ours == vocs[0]:
                stats["nakdan_top"] += 1
                match = opts[0]
            elif ours in vocs:
                stats["nakdan_alt"] += 1
                match = opts[vocs.index(ours)]
            elif to_haser(ours) in [to_haser(v) for v in vocs]:
                stats["nakdan_male"] += 1
                match = opts[[to_haser(v) for v in vocs].index(to_haser(ours))]
            elif qq(ours) in [qq(v) for v in vocs]:
                # камац катан в ктив мале пишется через вав (עוֹצְמָה, בְּחוֹזְקָה) —
                # у Nakdan на этом месте камац: та же гласная «о»
                stats["nakdan_male"] += 1
                match = opts[[qq(v) for v in vocs].index(qq(ours))]
            elif loose(to_haser(ours)) in [loose(to_haser(v)) for v in vocs]:
                stats["nakdan_loose"] += 1
                match = opts[[loose(to_haser(v)) for v in vocs].index(loose(to_haser(ours)))]
            elif partial and any(partial_ok(ours, v) for v in vocs):
                stats["nakdan_male"] += 1
                match = opts[0]
            else:
                f["niqqud"].append((sid, i, ours, vocs[0], vocs[1:4], w.get("tr")))
                match = opts[0]
            # лемма
            if text_only:
                continue
            lemma = w.get("lemma") or ""
            pos = w.get("pos") or ""
            if "имя" in pos or not match[2]:
                continue
            # У нас лемма глагола — инфинитив (לַעֲבוֹד), у Nakdan — форма
            # настоящего времени/корень: разные соглашения, не ошибка.
            if "гл." in pos and not (match[1] and bare(lemma) == bare(ours)):
                continue
            # инфинитив в тексте (לִקְרוֹא) и есть словарная форма: его ל —
            # не приставка, хоть Nakdan и режет его как «ל|»
            if "гл." in pos and bare(lemma) == bare(ours) and bare(lemma)[:1] == "ל":
                continue
            nk_lemmas = {bare(l) for l in match[2] if bare(l)}
            if match[1] and bare(lemma) == bare(ours) and bare(lemma) not in nk_lemmas \
                    and bare(lemma)[:1] in PREFIX_LETTERS:
                f["lemma_prefix"].append((sid, i, ours, lemma, sorted({l for l in match[2]})[:3], w.get("tr")))
            elif no_matres(lemma) not in {no_matres(l) for l in match[2]}:
                # лемма не совпала ни с одной леммой выбранного варианта — мягкий сигнал
                f["lemma_nakdan"].append((sid, i, ours, lemma, sorted({l for l in match[2]})[:3], w.get("tr")))
    return f, stats


SECTIONS = [
    ("double", "Удвоенные слова (звучат в аудио дважды)",
     lambda x: f"{x[0]} · слово #{x[1]} `{x[2]}` — {x[3]}"),
    ("tech", "Технический мусор в огласовке", lambda x: f"{x[0]} · #{x[1]} `{x[2]}` — {x[3]}"),
    ("script", "Чужие алфавиты", lambda x: f"{x[0]} · #{x[1]} · {x[2]} · {x[3]}: `{x[4]}`"),
    ("niqqud", "Огласовка расходится с Nakdan",
     lambda x: f"{x[0]} · #{x[1]} наше `{x[2]}` · Nakdan `{x[3]}`" + (f" (ещё: {', '.join(x[4])})" if x[4] else "") + f" — «{x[5]}»"),
    ("lemma_prefix", "Лемма с приставкой (артикль/союз/предлог)",
     lambda x: f"{x[0]} · #{x[1]} `{x[2]}` лемма `{x[3]}` → Nakdan: {', '.join(x[4])} — «{x[5]}»"),
    ("lemma_nakdan", "Лемма не совпала с Nakdan (мягкий сигнал)",
     lambda x: f"{x[0]} · #{x[1]} `{x[2]}` лемма `{x[3]}` → Nakdan: {', '.join(x[4])} — «{x[5]}»"),
    ("audio_missing", "Аудиофайл не найден", lambda x: f"{x[0]} · {x[1]}"),
    ("timings", "Аудио без пословных таймингов", lambda x: f"{x[0]}"),
    ("empty", "Пустые поля", lambda x: f"{x[0]} · #{x[1]} {x[2]} пусто у `{x[3]}`"),
    ("align", "Не удалось сопоставить с Nakdan (проверить вручную)", lambda x: f"{x[0]} · {x[1]}"),
]
BLOCKING = ("double", "script", "tech", "niqqud", "lemma_prefix", "audio_missing", "timings", "empty")


def write_report(slug, f, stats, out_dir):
    lines = [f"# {slug}", "",
             f"Предложений {stats['sentences']}, слов {stats['words']}. "
             f"Nakdan проверил {stats['nakdan_checked']} слов: совпало с первым вариантом "
             f"{stats['nakdan_top']}, с другим вариантом {stats['nakdan_alt']}, "
             f"как ктив мале того же варианта {stats['nakdan_male']}, "
             f"с точностью до дагеша/метега {stats['nakdan_loose']}.", ""]
    for key, title, fmt in SECTIONS:
        items = f[key]
        if not items:
            continue
        lines += [f"## {title} — {len(items)}", ""]
        lines += [f"- {fmt(x)}" for x in items]
        lines.append("")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{slug}.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-nakdan", action="store_true")
    ap.add_argument("--partial-niqqud", action="store_true",
                    help="огласовка частичная (подсказки): ошибка — только знак, противоречащий Nakdan")
    ap.add_argument("--text-only", action="store_true",
                    help="только текст (огласовка, мусор, дубли, алфавиты) — для корпусов без лемм/переводов слов, напр. тренажёра")
    args = ap.parse_args()

    paths = args.paths or []
    if args.all:
        paths += sorted(glob.glob(os.path.join(ROOT, "books", "*", "book-data.json")))
    if not paths:
        ap.error("укажи book-data.json или --all")

    nak = Nakdan(os.path.join(args.out, "nakdan_cache.json"), enabled=not args.no_nakdan)
    summary = {}
    total_blocking = 0
    try:
        for p in paths:
            base = os.path.basename(p)
            slug = (os.path.basename(os.path.dirname(os.path.abspath(p))) if base == "book-data.json"
                    else os.path.splitext(base)[0])  # book-data-epic-chNN.json лежат плоско в корне
            f, stats = check_book(p, nak, ROOT, text_only=args.text_only, partial=args.partial_niqqud)
            nak.save()
            write_report(slug, f, stats, args.out)
            counts = {k: len(v) for k, v in f.items()}
            blocking = sum(counts[k] for k in BLOCKING)
            total_blocking += blocking
            summary[slug] = {"stats": stats, "counts": counts, "blocking": blocking}
            print(f"{slug:28s} слов {stats['words']:5d}  находок {blocking:4d}  " +
                  " ".join(f"{k}={v}" for k, v in counts.items() if v))
    finally:
        nak.save()
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(f"\nВсего блокирующих находок: {total_blocking}. Запросов к Nakdan: {nak.calls}. Отчёты: {args.out}")
    sys.exit(1 if total_blocking else 0)


if __name__ == "__main__":
    main()
