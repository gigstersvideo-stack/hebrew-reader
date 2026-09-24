"""
audit_corpus.py — единая точка входа для ретроактивной проверки ВСЕГО
существующего контента читалки, а не только той книги/песни, которую
затронул последний прогон генерации.

Зачем: расследование бага с 792 оторванными приставками в quest-gimel/
house-dalet (см. ROADMAP.md) показало, что merge_prefix_fragments() в
5_gemini_pipeline.py ловит только КНИГИ, СГЕНЕРИРОВАННЫЕ ПОСЛЕ появления
самого фиксера (обе затронутые книги старше его на 2-4 дня) — уже
готовый корпус ретроактивно не проверялся никогда. Этот скрипт — общий
способ закрыть именно такой пробел на будущее: гоняет ВСЕ проверки
против ВСЕГО существующего контента, и его можно (нужно) перезапускать
вручную каждый раз, когда в hebrew_spelling_rules.py добавляется новое
правило — тогда старые нарушения находятся сразу, а не через следующий
случайный баг-репорт.

Правила homoglyph/stray_control берутся из общего E:\\ivrit\\
hebrew_spelling_rules.py (та же копия, синхронизируется вручную при
правках — как и другая переиспользуемая логика между этими двумя
проектами). ktiv_chaser и gemination из того же модуля НАРОЧНО не
используются здесь — проверено эмпирически на реальном корпусе
читалки (2026-09-18): оба правила кодируют не общую орфографию иврита,
а ПЕДАГОГИЧЕСКУЮ КОНВЕНЦИЮ конкретно курса "Корни" в тренажёре (חֹדֶשׁ/
רֹאשׁ/כֹּל без вав — стандартное, правильное написание в обычной прозе,
не нарушение; מְקַוָּה с одним ו+дагеш — тоже стандартно верно, курс
"Корни" нарочно требует писать двумя буквами ו для наглядности корня,
это не общее правило иврита). На реальных книгах читалки эти два
правила дали сотни ложных срабатываний на абсолютно правильном тексте
— поэтому не подключены на весь корпус. Точечно (не на весь корпус)
ktiv_chaser всё же используется — см. audit_prep_paradigms_spelling():
там заранее известно, что "опасных" слов (חדש/ראש/כל) нет, безопасно.
Остальное — специфика читалки: висящие приставки, манифест, битый
coverSvg, TTS-кросс-сверка.

Запуск: python audit_corpus.py
Ненулевой exit code, если найдено хоть одно неподтверждённое нарушение.
"""

import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import hebrew_spelling_rules as rules

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "spelling_verified_ok.json")

def _report(cache, rule, path, word, extra, total, confirmed_noise):
    if rules.is_verified(cache, rule, path, word):
        return total, confirmed_noise + 1
    print(f"  {rule} · {path} · {word!r}{extra}", file=sys.stderr)
    return total + 1, confirmed_noise


def _lines_key(data):
    if "sentences" in data:
        return "sentences"
    if "lines" in data:
        return "lines"
    return None


# Постоянные проверки для трёх классов испорченной lemma, найденных и
# исправленных вручную 2026-09-22 (см. scratchpad/lemma_cleanup.py той сессии
# и CHANGELOG v1.87.1) — чтобы то же самое не пролезло незамеченным в новых
# книгах. Гереш/гершайм/дефис/maqaf НЕ считаются "краевой пунктуацией" — они
# бывают частью самого слова (буквенная нумерация "א'", составные через
# дефис). pos 'пунктуация'/'пункт.' — легитимные отдельные слова-пунктуация
# (другой, известный и намеренно не проверяемый здесь класс, см. ROADMAP).
_EDGE_PUNCT = ".,!?;:\"'()«»[]{}…“”„‘’"
_EDGE_PAT = re.compile(r"^[" + re.escape(_EDGE_PUNCT) + r"]+|[" + re.escape(_EDGE_PUNCT) + r"]+$")
_HAS_HEBREW = re.compile(r"[֐-׿]")
_LATIN_CYR = re.compile(r"[a-zA-ZЀ-ӿ]")
_CYR_POS_LABEL = re.compile(r"^[Ѐ-ӿ]+\.?$")
_LOOKALIKE = re.compile(r"[рР]")  # кириллическая р/Р среди иврита


def _bare_letters(s):
    return re.sub(r"[^א-ת]", "", s or "")


_COLON_GLUED_QUOTE = re.compile(r'.:["“‘\']$')  # двоеточие + кавычка(и) без пробела перед прямой речью


def find_lemma_corruption_violations(w, base_path):
    lem = w.get("lemma")
    if not lem:
        return []
    out = []
    if _CYR_POS_LABEL.fullmatch(lem):
        out.append((base_path, lem, "лемма — русское обозначение части речи, а не само слово"))
    elif _HAS_HEBREW.search(lem) and _LOOKALIKE.search(lem):
        out.append((base_path, lem, "похожая по контуру кириллическая буква внутри ивритской леммы"))
    elif (
        _HAS_HEBREW.search(lem)
        and _EDGE_PAT.search(lem)
        and w.get("pos") not in ("пунктуация", "пункт.")
        and len(_bare_letters(_EDGE_PAT.sub("", lem))) >= 2
    ):
        out.append((base_path, lem, "пунктуация приклеена к лемме по краю"))
    t = w.get("t") or ""
    core = re.sub(r"^\W+|\W+$", "", t)
    if core and _LATIN_CYR.search(core) and not _HAS_HEBREW.search(core):
        out.append((base_path, t, "в слове вообще нет ивритских букв (кириллица/латиница просочилась в текст)"))
    return out


def find_structural_word_violations(w, base_path):
    """Найдено и исправлено 2026-09-23 (см. CHANGELOG): пунктуация как
    отдельное "слово" в words[] (ломает счёт TTS word-boundary — 186
    предложений в 3 книгах озвучивались БЕЗ таймингов вообще) и двоеточие+
    кавычка, приклеенные к слову без пробела перед прямой речью (та же
    причина, ещё 4 предложения). Постоянная проверка — чтобы не вернулось."""
    out = []
    if w.get("pos") in ("пунктуация", "пункт."):
        out.append((base_path, w.get("t"), "пунктуация как отдельное слово в words[] — ломает счёт TTS word-boundary"))
    t = w.get("t") or ""
    if _COLON_GLUED_QUOTE.search(t):
        out.append((base_path, t, "двоеточие+кавычка приклеены к слову без пробела перед прямой речью"))
    return out


_EXTRA_SPLIT = re.compile(r"[\s+/]+")


def find_lookalike_letter_violations(w, base_path):
    """Найдено и исправлено 2026-09-23 (см. CHANGELOG): кириллица/латиница
    похожая по контуру на ивритскую букву — не в самом слове (см.
    lemma_corruption выше), а в служебных полях root (1811 случаев по
    всему корпусу, например 'ג-д-ל' вместо 'ג-ד-ל') и extra (5554 слов,
    например 'м.ר. ед.ч.' вместо 'м.р. ед.ч.' — токен, где кириллица и
    иврит смешаны ВНУТРИ одного слова между пробелом/+//; легитимный
    сплошной иврит в extra, вроде названий биньянов פעל/הפעיל или терминов
    זכר יחיד, — не нарушение, проверяется только смешанный токен).
    Аналогично проверяется pos (1292 слов, например 'предל.' вместо
    'предл.', 'ארטיקל' — целиком ивритскими буквами вместо русского слова
    "артикль"). Токен длиннее 12 символов не считается: в epic-ch01.json
    (другой проект)
    после запятых/точек нет пробелов, и несколько нормальных слов подряд
    без пробела ложно похожи на "смешанный токен", хотя порчи букв там
    нет — настоящие испорченные токены все короче. Постоянная проверка —
    чтобы не вернулось."""
    out = []
    root = w.get("root") or ""
    if root and root != "—" and _LATIN_CYR.search(root):
        out.append((base_path, root, "кириллица/латиница вместо похожей ивритской буквы в root"))
    extra = w.get("extra") or ""
    if extra:
        for chunk in _EXTRA_SPLIT.split(extra):
            if chunk and len(chunk) <= 12 and _HAS_HEBREW.search(chunk) and _LATIN_CYR.search(chunk):
                out.append((base_path, extra, f"смешанный токен {chunk!r} в extra — кириллица+иврит вперемешку"))
                break
    pos = w.get("pos") or ""
    if pos and _HAS_HEBREW.search(pos) and _LATIN_CYR.search(pos):
        out.append((base_path, pos, "кириллица/латиница вместо похожей ивритской буквы (или целиком иврит) в pos"))
    return out


# Находка №2 (см. ROADMAP), решение владельца 2026-09-24: канонична голая
# форма предлога для всех слитных предлог+местоимение форм. Омографы
# исключены явно (не форма предлога, другое слово): "לו" с pos:'частица'
# (לוּ "если бы"), "לכן"/"מכן" — устойчивые "поэтому"/"с тех пор", "אלי" с
# pos:'имя собств.' — имя "Эли".
_BARE_TO_PREP_LEMMA = {
    "לי": "לְ", "לך": "לְ", "לו": "לְ", "לה": "לְ", "לנו": "לְ",
    "לכם": "לְ", "להם": "לְ", "להן": "לְ",
    "אותי": "אֶת", "אותך": "אֶת", "אותו": "אֶת", "אותה": "אֶת",
    "אותנו": "אֶת", "אתכם": "אֶת", "אתכן": "אֶת", "אותם": "אֶת", "אותן": "אֶת",
    "בי": "בְּ", "בך": "בְּ", "בו": "בְּ", "בה": "בְּ",
    "בנו": "בְּ", "בכם": "בְּ", "בכן": "בְּ", "בהם": "בְּ", "בהן": "בְּ",
    "שלי": "שֶׁל", "שלך": "שֶׁל", "שלו": "שֶׁל", "שלה": "שֶׁל",
    "שלנו": "שֶׁל", "שלכם": "שֶׁל", "שלכן": "שֶׁל", "שלהם": "שֶׁל", "שלהן": "שֶׁל",
    "עלי": "עַל", "עליי": "עַל", "עליך": "עַל", "עליו": "עַל", "עליה": "עַל",
    "עלינו": "עַל", "עליכם": "עַל", "עליכן": "עַל", "עליהם": "עַל", "עליהן": "עַל",
    "אליי": "אֶל", "אליך": "אֶל", "אליו": "אֶל", "אליה": "אֶל",
    "אלינו": "אֶל", "אליכם": "אֶל", "אליכן": "אֶל", "אליהם": "אֶל", "אליהן": "אֶל",
    "עמי": "עִם", "עימי": "עִם", "עמך": "עִם", "עימך": "עִם",
    "עמו": "עִם", "עימו": "עִם", "עמה": "עִם", "עימה": "עִם",
    "עמנו": "עִם", "עימנו": "עִם", "עמכם": "עִם", "עמהם": "עִם",
    "ממני": "מִן", "ממך": "מִן", "ממנו": "מִן", "ממנה": "מִן",
    "מאיתנו": "מִן", "מאתנו": "מִן", "מכם": "מִן", "מהם": "מִן", "מהן": "מִן",
    "כמוני": "כְּמוֹ", "כמוך": "כְּמוֹ", "כמוהו": "כְּמוֹ", "כמוה": "כְּמוֹ",
    "כמונו": "כְּמוֹ", "כמוכם": "כְּמוֹ", "כמוהם": "כְּמוֹ",
    # вторая партия (2026-09-24, из вопроса владельца "давай для всех") —
    # תוך с ב/ל/מ — три РАЗНЫХ предлога (תוך сам по себе не употребляется).
    # "את/אתי/אתו" как редкая архаичная форма "с" (омограф с אֶת) сознательно
    # не включены — см. ROADMAP, там же примечание.
    "לפניי": "לִפְנֵי", "לפני": "לִפְנֵי", "לפניך": "לִפְנֵי", "לפניו": "לִפְנֵי",
    "לפניה": "לִפְנֵי", "לפנינו": "לִפְנֵי", "לפניכם": "לִפְנֵי", "לפניכן": "לִפְנֵי",
    "לפניהם": "לִפְנֵי", "לפניהן": "לִפְנֵי",
    "אחריי": "אַחֲרֵי", "אחרי": "אַחֲרֵי", "אחריך": "אַחֲרֵי", "אחריו": "אַחֲרֵי",
    "אחריה": "אַחֲרֵי", "אחרינו": "אַחֲרֵי", "אחריכם": "אַחֲרֵי", "אחריכן": "אַחֲרֵי",
    "אחריהם": "אַחֲרֵי", "אחריהן": "אַחֲרֵי",
    "ביני": "בֵּין", "בינך": "בֵּין", "בינו": "בֵּין", "בינה": "בֵּין",
    "בינינו": "בֵּין", "ביניכם": "בֵּין", "ביניכן": "בֵּין", "ביניהם": "בֵּין", "ביניהן": "בֵּין",
    "עבורי": "עֲבוּר", "עבורך": "עֲבוּר", "עבורו": "עֲבוּר", "עבורה": "עֲבוּר",
    "עבורנו": "עֲבוּר", "עבורכם": "עֲבוּר", "עבורכן": "עֲבוּר", "עבורם": "עֲבוּר", "עבורן": "עֲבוּר",
    "סביבי": "סָבִיב", "סביבך": "סָבִיב", "סביבו": "סָבִיב", "סביבה": "סָבִיב",
    "סביבנו": "סָבִיב", "סביבכם": "סָבִיב", "סביבכן": "סָבִיב", "סביבם": "סָבִיב", "סביבן": "סָבִיב",
    "מולי": "מוּל", "מולך": "מוּל", "מולו": "מוּל", "מולה": "מוּל",
    "מולנו": "מוּל", "מולכם": "מוּל", "מולכן": "מוּל", "מולם": "מוּל", "מולן": "מוּל",
    "מאחוריי": "מֵאֲחוֹרֵי", "מאחורי": "מֵאֲחוֹרֵי", "מאחוריך": "מֵאֲחוֹרֵי", "מאחוריו": "מֵאֲחוֹרֵי",
    "מאחוריה": "מֵאֲחוֹרֵי", "מאחורינו": "מֵאֲחוֹרֵי", "מאחוריכם": "מֵאֲחוֹרֵי",
    "מאחוריהם": "מֵאֲחוֹרֵי", "מאחוריהן": "מֵאֲחוֹרֵי",
    "אצלי": "אֵצֶל", "אצלך": "אֵצֶל", "אצלו": "אֵצֶל", "אצלה": "אֵצֶל",
    "אצלנו": "אֵצֶל", "אצלכם": "אֵצֶל", "אצלכן": "אֵצֶל", "אצלם": "אֵצֶל", "אצלן": "אֵצֶל",
    "מעליי": "מֵעַל", "מעלי": "מֵעַל", "מעליך": "מֵעַל", "מעליו": "מֵעַל",
    "מעליה": "מֵעַל", "מעלינו": "מֵעַל", "מעליכם": "מֵעַל", "מעליהם": "מֵעַל", "מעליהן": "מֵעַל",
    "בתוכי": "בְּתוֹךְ", "בתוכך": "בְּתוֹךְ", "בתוכו": "בְּתוֹךְ", "בתוכה": "בְּתוֹךְ",
    "בתוכנו": "בְּתוֹךְ", "בתוככם": "בְּתוֹךְ", "בתוכם": "בְּתוֹךְ", "בתוכן": "בְּתוֹךְ",
    "לתוכי": "לְתוֹךְ", "לתוכך": "לְתוֹךְ", "לתוכו": "לְתוֹךְ", "לתוכה": "לְתוֹךְ",
    "לתוכנו": "לְתוֹךְ", "לתוכם": "לְתוֹךְ", "לתוכן": "לְתוֹךְ",
    "מתוכי": "מִתּוֹךְ", "מתוכך": "מִתּוֹךְ", "מתוכו": "מִתּוֹךְ", "מתוכה": "מִתּוֹךְ",
    "מתוכנו": "מִתּוֹךְ", "מתוכם": "מִתּוֹךְ", "מתוכן": "מִתּוֹךְ",
}
_BARE_ONLY = re.compile(r"[^א-ת]")


def find_dative_lemma_violations(w, base_path):
    bare = _BARE_ONLY.sub("", w.get("t") or "")
    if bare == "לו" and w.get("pos") == "частица":
        return []
    if bare == "אלי" and w.get("pos") == "имя собств.":
        return []
    target = _BARE_TO_PREP_LEMMA.get(bare) or ("אֶל" if bare == "אלי" else None)
    if target and w.get("lemma") != target:
        return [(base_path, w.get("lemma"), f"слитная форма предлога {target!r} лемматизирована непоследовательно")]
    return []


_PREP_PARADIGMS_RE = re.compile(r"const PREP_PARADIGMS = (\{.*?\n\});", re.S)


def audit_prep_paradigms_spelling():
    """2026-09-24 (владелец, после проверки לפני/אחרי/מאחורי на слух):
    формы в PREP_PARADIGMS (см. reader-prototype.html, находка №2/карточка
    предлогов) попали в код через одноразовый скрипт-экстрактор из
    корпуса, МИМО штатного add_nikud_checked — эта проверка закрывает
    именно такой пробел на будущее, а не разбор "на глаз" по запросу.
    ktiv_chaser НАРОЧНО не подключён на весь корпус (см. докстринг файла —
    ложные срабатывания на חֹדֶשׁ/רֹאשׁ/כֹּל), но здесь безопасно: список
    предлогов не содержит этих слов, проверено вручную при подключении."""
    html_path = os.path.join(HERE, "reader-prototype.html")
    raw = open(html_path, encoding="utf-8").read()
    m = _PREP_PARADIGMS_RE.search(raw)
    if not m:
        print("  prep_paradigms_missing · PREP_PARADIGMS не найден в reader-prototype.html", file=sys.stderr)
        return 1
    data = json.loads(m.group(1))
    obj = {prep: [r["form"] for r in info["rows"]] for prep, info in data.items()}
    violations = rules.find_ktiv_chaser_violations(obj)
    for p, word, reason in violations:
        print(f"  prep_paradigm_spelling · {p} · {word!r} ({reason})", file=sys.stderr)
    return len(violations)


def audit_content_file(fname, cache, total, confirmed_noise):
    data = json.load(open(fname, encoding="utf-8"))
    key = _lines_key(data)
    if key is None:
        return total, confirmed_noise
    sentences = data[key]
    name = os.path.basename(fname)

    for p, word in rules.find_hanging_prefix_violations(sentences, path=f"[{name}]"):
        total, confirmed_noise = _report(
            cache, "hanging_prefix", p, word, "", total, confirmed_noise
        )

    for i, sent in enumerate(sentences):
        for j, w in enumerate(sent.get("words", [])):
            base_path = f"[{name}] {key}[{i}].words[{j}]"
            for p, word in rules.find_homoglyph_violations(w, base_path):
                total, confirmed_noise = _report(
                    cache, "homoglyph", p, word, "", total, confirmed_noise
                )
            for p, word, reason in rules.find_stray_control_char_violations(w, base_path):
                total, confirmed_noise = _report(
                    cache, "stray_control", p, word, f" ({reason})", total, confirmed_noise
                )
            for p, word, reason in find_lemma_corruption_violations(w, base_path):
                total, confirmed_noise = _report(
                    cache, "lemma_corruption", p, word, f" ({reason})", total, confirmed_noise
                )
            for p, word, reason in find_structural_word_violations(w, base_path):
                total, confirmed_noise = _report(
                    cache, "structural_word", p, word, f" ({reason})", total, confirmed_noise
                )
            for p, word, reason in find_lookalike_letter_violations(w, base_path):
                total, confirmed_noise = _report(
                    cache, "lookalike_letter", p, word, f" ({reason})", total, confirmed_noise
                )
            for p, word, reason in find_dative_lemma_violations(w, base_path):
                total, confirmed_noise = _report(
                    cache, "dative_lemma", p, word, f" ({reason})", total, confirmed_noise
                )
    return total, confirmed_noise


def audit_all_content(cache, total, confirmed_noise):
    files = sorted(glob.glob(os.path.join(HERE, "book-data-*.json"))) + sorted(
        glob.glob(os.path.join(HERE, "song-data-*.json"))
    )
    print(f"\n=== контент: {len(files)} файлов (book-data-*/song-data-*) ===", file=sys.stderr)
    for f in files:
        total, confirmed_noise = audit_content_file(f, cache, total, confirmed_noise)
    return total, confirmed_noise


def audit_manifests(total, confirmed_noise):
    print("\n=== манифесты (books-manifest.json + songs-manifest.json) ===", file=sys.stderr)
    entries = []
    for mf, key in (("books-manifest.json", "books"), ("songs-manifest.json", "songs")):
        p = os.path.join(HERE, mf)
        if not os.path.exists(p):
            continue
        data = json.load(open(p, encoding="utf-8"))
        for e in data.get(key, []):
            entries.append((mf, e))

    seen_ids = {}
    seen_covers = {}
    for mf, e in entries:
        eid = e.get("id")
        if not eid:
            print(f"  manifest_invalid · {mf} · запись без id: {e!r}", file=sys.stderr)
            total += 1
            continue
        if eid in seen_ids:
            print(f"  manifest_duplicate_id · {mf} · id={eid!r} (уже был в {seen_ids[eid]})",
                  file=sys.stderr)
            total += 1
        else:
            seen_ids[eid] = mf

        fpath = e.get("file")
        if fpath and not os.path.exists(os.path.join(HERE, fpath)):
            print(f"  manifest_missing_file · {mf} · id={eid!r} file={fpath!r} не найден",
                  file=sys.stderr)
            total += 1

        svg = e.get("coverSvg")
        if svg:
            try:
                ET.fromstring(svg)
            except ET.ParseError as ex:
                print(f"  manifest_invalid_svg · {mf} · id={eid!r} · {ex}", file=sys.stderr)
                total += 1

        # v1.86.0: растровая обложка должна существовать, а обложки книг — не повторяться
        # (правило памятки для добавления книг «уникальные coverSvg» раньше не проверялось:
        # у 8 книг был один и тот же SVG). Эффективная обложка: файл cover, иначе coverSvg.
        cover = e.get("cover")
        if cover and not str(cover).startswith("data:"):
            if not os.path.exists(os.path.join(HERE, cover)):
                print(f"  manifest_missing_cover · {mf} · id={eid!r} cover={cover!r} не найден", file=sys.stderr)
                total += 1
        key = ("cover", cover) if cover else (("svg", svg) if svg else None)
        if key:
            if key in seen_covers:
                print(f"  manifest_duplicate_cover · {mf} · id={eid!r} повторяет обложку книги {seen_covers[key]!r}",
                      file=sys.stderr)
                total += 1
            else:
                seen_covers[key] = eid
    return total, confirmed_noise


def audit_tts_known_bad(total, confirmed_noise):
    """Мягкая, информационная сверка — НЕ хардфейл (список именно для
    того и существует, что эти слова остаются в корпусе с обходным
    произношением в другом месте пайплайна озвучки, а не как ошибка)."""
    tts_path = os.path.join(HERE, "tts_known_bad_words.json")
    if not os.path.exists(tts_path):
        return total, confirmed_noise
    bad_lemmas = {e["lemma"] for e in json.load(open(tts_path, encoding="utf-8")) if e.get("lemma")}
    if not bad_lemmas:
        return total, confirmed_noise
    print("\n=== кросс-сверка с tts_known_bad_words.json (информационно) ===", file=sys.stderr)
    hits = 0
    for f in sorted(glob.glob(os.path.join(HERE, "book-data-*.json"))) + sorted(
        glob.glob(os.path.join(HERE, "song-data-*.json"))
    ):
        data = json.load(open(f, encoding="utf-8"))
        key = _lines_key(data)
        if key is None:
            continue
        for sent in data[key]:
            for w in sent.get("words", []):
                if w.get("lemma") in bad_lemmas:
                    hits += 1
    print(f"  встречается известных проблемных для TTS лемм: {hits} "
          f"(из {len(bad_lemmas)} отслеживаемых) — не блокирует, для справки",
          file=sys.stderr)
    return total, confirmed_noise


def main():
    cache = rules.load_verified_cache(CACHE_PATH)
    total = 0
    confirmed_noise = 0
    total, confirmed_noise = audit_all_content(cache, total, confirmed_noise)
    total, confirmed_noise = audit_manifests(total, confirmed_noise)
    total, confirmed_noise = audit_tts_known_bad(total, confirmed_noise)
    print("\n=== карточка предлогов (PREP_PARADIGMS в reader-prototype.html): כתיב מלא ===", file=sys.stderr)
    total += audit_prep_paradigms_spelling()

    print(
        f"\nВсего нарушений: {total} "
        f"(плюс {confirmed_noise} подтверждённых ранее не-нарушений, исключены)",
        file=sys.stderr,
    )
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
