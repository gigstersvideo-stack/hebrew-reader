"""
niqud_fixes.py — механические исправления самого частого брака огласовки
(модель убирает вав/йод вместо того, чтобы добавить огласовку поверх них),
вынесенные в отдельный модуль после того, как один и тот же набор правок
пришлось два раза подряд делать вручную (книги "swarm-bet" и "amnesia-gimel",
~60-130 точечных фиксов на книгу). Используется и 2_generate_story.py
(где есть исходный текст без огласовок — можно проверить строго, слово
в слово), и 5_gemini_pipeline.py (где исходника уже нет — там работает
только самодостаточная часть: холам/кубуц без вав перед ними).

Все правила проверены вручную на реальных книгах, ничего не придумано
абстрактно:
  - кубуц (ֻ) без вав перед собой — ВСЕГДА дефект в כתיב מלא (нет случаев,
    где это законно), чинится: кубуц снимается с буквы, вставляется вав
    с дагешем (וּ) вместо него.
  - холам (ֹ) без вав перед собой — почти всегда тот же дефект, ЗА
    ИСКЛЮЧЕНИЕМ короткого списка слов, где так пишут всегда, в любом
    стиле (FALSE_POSITIVE_ROOTS ниже).
  - пропавший йod — почти всегда просто вставляется "немая" (без своей
    огласовки) буква йод на нужное место; на буквах-соседях никаких
    огласовок двигать не нужно (в отличие от вав), это подтверждено на
    ~20 разных случаях за две книги (חִיכְּתָה, הַיָּדַיִים, הָיְיתָה и т.д.)
"""

import difflib
import re

HOLAM = "ֹ"      # ֹ
QUBUTS = "ֻ"      # ֻ
DAGESH = "ּ"      # ּ (тот же символ, что и mapiq)
VAV = "ו"         # ו
YOD = "י"         # י

NIQUD_CHAR_RE = re.compile(r"[֑-ׇ]")

# корни, где холам законно стоит без вав в ЛЮБОМ стиле написания — не баг.
# Проверено вживую на двух книгах (~170 предупреждений суммарно) — за
# пределами этого списка ни разу не нашлось ложного срабатывания.
FALSE_POSITIVE_ROOTS = {
    "לא", "זאת", "כל", "כח", "ראש", "כה", "פה", "איפה", "זה",
}
_PREFIX_LETTERS = "ובכלמשה"
_SUFFIX_VARIANTS = ("נו", "כם", "כן", "ה", "ך", "ו", "י", "ם", "ן")


def strip_niqud(text):
    return NIQUD_CHAR_RE.sub("", text)


def _bare_variants(word_with_niqud):
    """Все правдоподобные "голые корни" слова — само слово без огласовок,
    плюс варианты после снятия 1-2 однобуквенных префиксов и известных
    суффиксов-местоимений — чтобы לְלֹא / שֶׁלֹּא / בְּרֹאשָׁהּ и т.п. тоже
    находили совпадение с базовым корнем в FALSE_POSITIVE_ROOTS."""
    bare = strip_niqud(word_with_niqud).strip(".,:;!?\"'()“”")
    variants = {bare}
    stripped = bare
    for _ in range(2):
        if len(stripped) > 1 and stripped[0] in _PREFIX_LETTERS:
            stripped = stripped[1:]
            variants.add(stripped)
        else:
            break
    for v in list(variants):
        for suf in _SUFFIX_VARIANTS:
            if v.endswith(suf) and len(v) > len(suf):
                variants.add(v[: -len(suf)])
    return variants


def is_false_positive(word_with_niqud):
    return bool(_bare_variants(word_with_niqud) & FALSE_POSITIVE_ROOTS)


def _split_marks(vocalized):
    """[(буква, огласовки-после-неё), ...] — каждая "единица" это одна
    согласная буква плюс все комбинируемые значки сразу после неё."""
    units = []
    i = 0
    while i < len(vocalized):
        base = vocalized[i]
        i += 1
        marks = ""
        while i < len(vocalized) and NIQUD_CHAR_RE.match(vocalized[i]):
            marks += vocalized[i]
            i += 1
        units.append([base, marks])
    return units


def _join(units):
    return "".join(b + m for b, m in units)


def mechanical_fix_word(orig, vocalized):
    """orig — слово БЕЗ огласовок (как было до простановки никуда).
    vocalized — то, что получилось (возможно, с потерянной буквой).
    Если ровно ОДНА буква (вав или йод) пропала и её можно безопасно
    вставить обратно — возвращает исправленное слово. Иначе None (пусть
    решает человек — не гадаем на неоднозначных случаях)."""
    units = _split_marks(vocalized)
    bases = [u[0] for u in units]
    if "".join(bases) == orig:
        return vocalized

    sm = difflib.SequenceMatcher(a=bases, b=list(orig), autojunk=False)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"]
    if len(ops) != 1 or ops[0][0] != "insert":
        return None
    _, i1, _, j1, j2 = ops[0]
    if j2 - j1 != 1:
        return None
    missing = orig[j1]

    if missing == YOD:
        # неоднозначность: когда буква повторяется дважды подряд (двойной йод,
        # напр. ниקוד "ה-ת-י-י-ש-ב" из "התיישבה"), difflib иногда выравнивает
        # существующую букву с ВТОРЫМ вхождением в оригинале и просит вставить
        # "недостающую" ПЕРЕД ней — это даёт немую букву перед вокализованной
        # вместо принятого порядка (вокализованная, потом немая). Если то, что
        # мы бы поставили прямо ПОСЛЕ вставки, — тоже йод с непустой огласовкой,
        # правильнее вставить именно после него.
        if (i1 < len(units) and units[i1][0] == YOD and units[i1][1]
                and not (i1 > 0 and units[i1 - 1][0] == YOD and units[i1 - 1][1])):
            i1 += 1
        units.insert(i1, [YOD, ""])
        return _join(units)

    if missing == VAV:
        if i1 == 0:
            return None
        prev_marks = units[i1 - 1][1]
        if HOLAM in prev_marks:
            units[i1 - 1][1] = prev_marks.replace(HOLAM, "")
            units.insert(i1, [VAV, HOLAM])
            return _join(units)
        if QUBUTS in prev_marks:
            units[i1 - 1][1] = prev_marks.replace(QUBUTS, "")
            units.insert(i1, [VAV, DAGESH])
            return _join(units)
        return None

    return None


def fix_deficient_spelling_standalone(word):
    """Для случаев без исходника без огласовок (уже размеченный book-data):
    холам/кубуц прямо на согласной (не на вставленном ею вав) почти всегда
    значит, что вав потерялся. Кубуц чинится всегда, холам — только если
    слово не входит в FALSE_POSITIVE_ROOTS. Возвращает (слово, было_ли_
    исправление)."""
    if is_false_positive(word):
        return word, False
    units = _split_marks(word)
    changed = False
    i = 0
    while i < len(units):
        base, marks = units[i]
        if base != VAV:
            if QUBUTS in marks:
                units[i][1] = marks.replace(QUBUTS, "")
                units.insert(i + 1, [VAV, DAGESH])
                changed = True
                i += 1
            elif HOLAM in marks:
                units[i][1] = marks.replace(HOLAM, "")
                units.insert(i + 1, [VAV, HOLAM])
                changed = True
                i += 1
        i += 1
    if not changed:
        return word, False
    return _join(units), True
