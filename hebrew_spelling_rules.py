"""
hebrew_spelling_rules.py — единое место для всех механических проверок
честности/правописания курса «Корни» (полное написание כתיב מלא — см.
раздел "Правила честности" в PROMPT_TEMPLATE generate_root_theory.py,
это та же логика, но исполняемая программно, не полагаясь на модель).

Три независимых правила, каждое найдено отдельно по ходу проекта, в
разное время, разными способами — раньше жили в трёх разных файлах
(generate_root_theory.py, fix_arabic_contamination.py, fix_gemination.py),
которые теперь просто импортируют их отсюда:

  1. ktiv_chaser  — голый холам без вав (חֹק вместо חוֹק), кубуц вместо
                     шурук (קֻם вместо קוּם). Найдено вручную на мокапе
                     ק-ו-ם, первое правило в проекте.
  2. homoglyph    — буква/огласовка из ДРУГОГО алфавита (не только
                     арабского — латиница, кириллица, армянский, тайский,
                     полноширинные CJK-формы) внутри ивритского (или
                     русского) слова. Найдено при разборе отзыва про
                     дубли в derived_words корня ה-י-ה.
  3. gemination   — недвоённая средняя корневая буква ו/י в биньянах
                     פִּעֵל/הִתְפַּעֵל/פֻּעַל/הֻפְעַל (מְהַוָּה вместо
                     מְהַוָּוה) — найдено по прямому вопросу пользователя,
                     "но я же просил всегда пишем מלא огласовки".

find_all_violations(obj, root=None) прогоняет все три сразу; каждое
правило также доступно по отдельности (find_ktiv_chaser_violations,
find_homoglyph_violations, find_gemination_violations) для скриптов,
которым нужно только одно из них.

Запуск как скрипт — построчный отчёт по обоим файлам курса:
    python3 hebrew_spelling_rules.py
"""

import datetime
import hashlib
import json
import os
import re
import sys
import unicodedata

# ---- общая утилита: кластеры (буква + все её огласовки) ----
# Нужно, потому что некоторые буквы несут СРАЗУ два огласовочных знака
# (напр. каф с дагешем И холамом в הַכֹּל — "всё") — проверка по одному
# символу назад ошибочно принимает дагеш за "не вав перед холамом".
# Кластеры решают это: смотрим на БАЗОВУЮ букву кластера, не на
# непосредственно предыдущий символ.
def split_clusters(word):
    clusters = []
    for ch in word:
        if clusters and unicodedata.category(ch) == "Mn":
            clusters[-1] += ch
        else:
            clusters.append(ch)
    return clusters


def _walk_strings(obj, path=""):
    """Рекурсивно обходит JSON-объект, отдавая (path, string) для каждой
    строки — общий каркас для всех трёх find_*_violations ниже."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


# ============================================================
# Правило 1: ktiv chaser (голый холам, кубуц)
# ============================================================

CHOLAM = "ֹ"   # холам (гласная "о")
KUBUTZ = "ֻ"   # кубуц (гласная "у", неполное написание)
DAGESH = "ּ"   # дагеш (для шурук: вав + дагеш)
VAV = "ו"
YOD = "י"

# короткие служебные слова, где холам без вав — стандартное написание,
# не нарушение כתיב מלא (двух-трёхбуквенные слова этого типа в него не
# разворачиваются ни в каком стиле письма)
KTIV_CHASER_ALLOWLIST = {"לֹא", "כֹּה", "זֹאת", "פֹּה"}
_STRIP_PUNCT = ".,!?;:\"'()«»־-־"


def _bare_consonants(word):
    return "".join(c for c in word if unicodedata.category(c) != "Mn")


def _matches_allowlist(word):
    """Проверяет и голое слово, и слово с приставками (ו/ה/ב/כ/ל/מ/ש +
    дагеш удвоения) — וְזֹאת/הַזֹּאת и т.п. не должны считаться ошибкой
    так же, как и голое זֹאת: сравниваем по буквам без огласовок, по
    суффиксу, а не только точным совпадением всего слова."""
    if word in KTIV_CHASER_ALLOWLIST:
        return True
    bare = _bare_consonants(word)
    return any(bare.endswith(_bare_consonants(allowed)) for allowed in KTIV_CHASER_ALLOWLIST)


def find_ktiv_chaser_violations(obj, path=""):
    """Рекурсивно ищет в распарсенном JSON-ответе огласованные ивритские
    слова с явными признаками неполного написания (כתיב חסר):
    - холам не на вав (напр. חֹק вместо חוֹק);
    - кубуц вместо шурук (напр. קֻם вместо קוּם).
    Не ловит все возможные случаи כתיב חסר (это лингвистически не всегда
    однозначно формализуемо), но ловит два самых частых и однозначных."""
    violations = []
    for p, s in _walk_strings(obj, path):
        for word in s.split():
            clean = word.strip(_STRIP_PUNCT)
            if _matches_allowlist(clean):
                continue
            for cl in split_clusters(clean):
                if CHOLAM in cl and cl[0] != VAV:
                    violations.append((p, clean, "холам без вав — похоже на כתיב חסר, нужно וֹ"))
                elif KUBUTZ in cl:
                    violations.append((p, clean, "кубуц вместо шурук — в современном написании обычно וּ"))
    return violations


def _fix_ktiv_chaser_word(word):
    """Механически чинит ОДНО слово: холам не на вав -> вставить вав
    перед холамом; кубуц -> заменить на вав+дагеш (шурук). Оба
    преобразования однозначны по построению (не требуют понимания
    смысла слова), поэтому чиним программно вместо того, чтобы просить
    модель повторить — некоторые паттерны (напр. биньян פֻּעַל с
    кубуцем: מְיֻחָד) модель воспроизводит раз за разом независимо от
    того, какая модель — повтор просто тратит квоту впустую."""
    out = []
    for cl in split_clusters(word):
        base, marks = cl[0], cl[1:]
        if CHOLAM in marks and base != VAV:
            out.append(base + marks.replace(CHOLAM, ""))
            out.append(VAV + CHOLAM)
        elif KUBUTZ in marks:
            out.append(base + marks.replace(KUBUTZ, ""))
            out.append(VAV + DAGESH)
        else:
            out.append(cl)
    return "".join(out)


def fix_ktiv_chaser(obj):
    """Рекурсивно применяет _fix_ktiv_chaser_word по всему JSON-объекту,
    сохраняя пробелы/пунктуацию на границах слов и не трогая слова из
    KTIV_CHASER_ALLOWLIST."""
    if isinstance(obj, dict):
        return {k: fix_ktiv_chaser(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fix_ktiv_chaser(v) for v in obj]
    elif isinstance(obj, str):
        tokens = re.split(r"(\s+)", obj)
        for idx, tok in enumerate(tokens):
            if not tok or tok.isspace():
                continue
            core = tok.strip(_STRIP_PUNCT)
            if not core or _matches_allowlist(core):
                continue
            start = tok.index(core)
            tokens[idx] = tok[:start] + _fix_ktiv_chaser_word(core) + tok[start + len(core):]
        return "".join(tokens)
    return obj


# ============================================================
# Правило 2: посторонний алфавит внутри ивритского/русского слова
# ============================================================

HEBREW_LETTERS_RE = re.compile(r"[א-ת]")
HEBREW_NIQUD_RE = re.compile(r"[֑-ׇ]")
CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
# обратный апостроф — используется в грамматических пометках как знак
# гортанной ע (напр. "hиф`иль") наравне с латинской h — легитимная
# конвенция, не порча (найдено при консолидации QA на корпусе читалки,
# 2026-09-18: без него h из-за соседнего ` ошибочно считался порчей).
ALLOWED_CHARS = set(" \t\n.,!?;:\"'()«»—–?%/0123456789`")
WORD_SPLIT_RE = re.compile(r"[\s\-־–—]+")
# осознанные цитаты настоящих арабских слов-когнатов (этимология) — не
# порча, единственное реальное исключение, найденное при разборе всего
# корпуса
HOMOGLYPH_LEGIT_EXCLUDE = {"(قام)", "عالم", "موت)."}


def is_corrupted_word(word):
    """Слово (ивритское ИЛИ русское — есть ивритская согласная либо
    кириллица), в которое затесалась буква из ДРУГОГО алфавита — любого,
    не только арабского. Дефисы уже не долетают до этой функции
    (WORD_SPLIT_RE рвёт по ним раньше), так что намеренные двуязычные
    конструкции вроде 'шлемим-корень' не ловятся как одно слово."""
    if not word or word in HOMOGLYPH_LEGIT_EXCLUDE:
        return False
    has_hebrew = HEBREW_LETTERS_RE.search(word)
    has_cyrillic = CYRILLIC_RE.search(word)
    if not has_hebrew and not has_cyrillic:
        return False
    # Латинская "h"/"H" внутри русского слова — принятая в этом корпусе
    # транслитерация звука, которого нет в кириллице (hитпаэль,
    # Hитпаэль, hифъиль, Hамиша) — легитимная конвенция, не порча.
    # Только внутри кириллического слова (рядом с ивритскими буквами
    # это уже другое дело — тот класс порчи мы и ищем).
    if has_cyrillic and not has_hebrew:
        foreign = [ch for ch in word if not (CYRILLIC_RE.match(ch) or ch in ALLOWED_CHARS)]
        if foreign and all(ch in "hH" for ch in foreign):
            return False
    for ch in word:
        if HEBREW_LETTERS_RE.match(ch) or HEBREW_NIQUD_RE.match(ch) or CYRILLIC_RE.match(ch):
            continue
        if ch in ALLOWED_CHARS:
            continue
        if ch.isalpha():
            return True
    return False


def is_corrupted(s):
    if not isinstance(s, str):
        return False
    return any(is_corrupted_word(w) for w in WORD_SPLIT_RE.split(s))


def find_homoglyph_violations(obj, path=""):
    """Возвращает список (path, string) для строк с порчей чужим
    алфавитом."""
    violations = []
    for p, s in _walk_strings(obj, path):
        if is_corrupted(s):
            violations.append((p, s))
    return violations


# ============================================================
# Правило 3: недвоённая средняя корневая буква ו/י (Piel-family)
# ============================================================

def has_gemination(word, middle):
    """True, если средняя буква корня (middle, 'ו' или 'י') стоит с
    дагешем+огласовкой и НЕ примыкает к другой копии той же буквы,
    которая делает пару уже правильно удвоенной. Три паттерна, все
    реально встречаются в данных:
      1. голая буква ПЕРЕД дагешированной (לְהִתְבַּייֵּת — י, затем יֵּ)
      2. дагешированная ПЕРЕД голой (חַיִּים — יִּ, затем י)
      3. рядом с шуруком той же буквы — וּ (дагеш БЕЗ доп. огласовки,
         сам по себе гласная "у") плюс отдельно геминированная וָּ
         (מְרוּוָּח — וּ, затем וָּ); шурук — другое явление (сам звук
         "у"), не считается "второй копией удвоения", но и не мешает
         быть таковым, если геминированная копия ПОРЯДОМ дальше.
    Без всех трёх проверок уже верные слова ложно считаются "надо
    чинить", и повторный прогон фикса добавляет ТРЕТЬЮ букву поверх
    верной формы — реальный баг, пойманный на первом прогоне (см.
    FEEDBACK_LOG.md, разбор от 2026-09-13)."""
    clean = word.strip(_STRIP_PUNCT)
    clusters = split_clusters(clean)
    for i, cl in enumerate(clusters):
        if cl[0] == middle and DAGESH in cl[1:] and len(cl) > 2:
            neighbors = []
            if i > 0:
                neighbors.append(clusters[i - 1])
            if i < len(clusters) - 1:
                neighbors.append(clusters[i + 1])
            already_doubled = any(
                n == middle or (n[0] == middle and DAGESH in n[1:] and len(n) == 2)
                for n in neighbors
            )
            if already_doubled:
                continue
            return True
    return False


def max_letter_run(word, middle):
    """Максимальное число ПОДРЯД идущих кластеров с базовой буквой middle
    (независимо от огласовок) — 3+ значит слово переудвоено (реальный
    баг, не цель); используется как жёсткая защита от переисправления
    при валидации результата модели, не только как эвристика поиска."""
    clean = word.strip(_STRIP_PUNCT)
    run = maxrun = 0
    for cl in split_clusters(clean):
        if cl[0] == middle:
            run += 1
            maxrun = max(maxrun, run)
        else:
            run = 0
    return maxrun


def root_middle_letter(root):
    """root — строка вида 'ה-י-ה'. Возвращает 'ו'/'י', если средняя
    буква этого трёхбуквенного корня — одна из них (единственный случай,
    когда правило 3 вообще применимо), иначе None."""
    letters = root.split("-")
    if len(letters) == 3 and letters[1] in (VAV, YOD):
        return letters[1]
    return None


def _single_insertion_ok(orig_word, fixed_word, middle):
    """True если fixed_word получено из orig_word вставкой РОВНО одного
    кластера с базовой буквой middle рядом с уже существующим кластером
    той же базовой буквы — единственное изменение, которое допустимо
    называть 'исправлением удвоения'.

    Сравнивает ЦЕЛЫЕ кластеры (буква+огласовки), а не только базовые
    буквы — иначе проходит и такой "фикс", где модель заодно меняет
    огласовку на соседней букве (напр. patach -> tsere) или вовсе теряет
    её, оставляя голую букву без единого значка (רַוַּח -> רווַח, ר
    осталась вовсе без огласовки) — прямое нарушение принципа проекта
    "всегда полная огласовка". Поймано вручную на живом прогоне
    2026-09-14, старая версия (сравнение только базовых букв) это
    пропускала."""
    orig_clusters = split_clusters(orig_word.strip(_STRIP_PUNCT))
    fixed_clusters = split_clusters(fixed_word.strip(_STRIP_PUNCT))
    if len(fixed_clusters) != len(orig_clusters) + 1:
        return False
    for i in range(len(orig_clusters) + 1):
        if fixed_clusters[:i] != orig_clusters[:i]:
            continue
        if fixed_clusters[i + 1:] != orig_clusters[i:]:
            continue
        inserted = fixed_clusters[i]
        if inserted[0] != middle:
            continue
        before = orig_clusters[i - 1] if i > 0 else None
        after = orig_clusters[i] if i < len(orig_clusters) else None
        if (before is not None and before[0] == middle) or (after is not None and after[0] == middle):
            return True
    return False


def is_safe_gemination_fix(original, fixed, middle):
    """Жёсткий guard на результат модели: True только если fixed
    отличается от original РОВНО вставкой одной копии буквы middle рядом
    с её существующей копией, слово за словом (WORD_SPLIT_RE игнорирует
    пунктуацию/пробелы) — отклоняет любое другое изменение (лишние
    буквы, переписанный текст, другое слово). Добавлено после реального
    случая, когда модель при ротации придумала буквы, не относящиеся к
    корню вообще (לְצָרֵף -> לְצַוּוֵרֵף — в корне צ-ר-פ нет ו), а старые
    guard'ы (разница длины + max_letter_run) это пропустили.

    Дополнительно требует, чтобы КАЖДОЕ изменённое слово само по себе
    было нарушением по has_gemination ДО фикса — иначе модель может
    "поправить" слово, которое вообще не было отмечено (напр. מִין,
    у которого дагеша нет вовсе), просто потому что оно похоже на
    настоящее целевое слово в том же тексте (см. FEEDBACK_LOG.md,
    разбор от 2026-09-13)."""
    orig_words = [w for w in WORD_SPLIT_RE.split(original) if w]
    fixed_words = [w for w in WORD_SPLIT_RE.split(fixed) if w]
    if len(orig_words) != len(fixed_words):
        return False
    for ow, fw in zip(orig_words, fixed_words):
        if ow == fw:
            continue
        if not has_gemination(ow, middle):
            return False
        if not _single_insertion_ok(ow, fw, middle):
            return False
    return True


def find_gemination_violations(obj, root, path=""):
    """root — строка корня этой записи (e.g. 'ה-י-ה'); правило
    неприменимо (возвращает []), если средняя буква корня не ו/י."""
    middle = root_middle_letter(root)
    if not middle:
        return []
    violations = []
    for p, s in _walk_strings(obj, path):
        for word in WORD_SPLIT_RE.split(s):
            if has_gemination(word, middle):
                violations.append((p, word))
    return violations


# ============================================================
# Правило 4: текст не в канонической NFC-нормализации
# ============================================================
# ВАЖНО (проверено эмпирически при консолидации QA, 2026-09-18): для
# ивритского текста с никудом в этом корпусе unicodedata.normalize('NFC',
# s) != s почти ВСЕГДА означает лишь другой порядок комбинирующих знаков
# (дагеш/огласовка) внутри одного кластера — канонически эквивалентно,
# визуально и по смыслу идентично, НЕ баг (прогон по всему
# root_theory_all.json+root_sentences_all.json: 3531 срабатываний, из
# них 3531 — тот же набор символов той же длины, просто переставлены;
# 0 — с реальным добавлением/потерей символа). Поэтому эта функция
# НАМЕРЕННО не вызывается из find_all_violations — как блокирующая
# проверка "весь корпус должен быть NFC" она создаёт только шум. Она
# остаётся отдельной утилитой для точечных, штучных проверок, где
# сравниваются ДВЕ строки, которые обязаны совпадать буквально (не через
# нормализацию по буквам, как это теперь сделано в
# find_cloze_mismatch_violations ниже).

def find_non_nfc_violations(obj, path=""):
    violations = []
    for p, s in _walk_strings(obj, path):
        if unicodedata.normalize("NFC", s) != s:
            violations.append((p, s))
    return violations


# ============================================================
# Правило 5 (НЕ включено в find_all_violations — см. предупреждение):
# заявленный корень не является подпоследовательностью согласных слова
# ============================================================
# ЗАДУМЫВАЛОСЬ как дешёвая защита от "утечки" полей между соседними
# записями батч-генерации. ПРОВЕРЕНО ЭМПИРИЧЕСКИ на реальном корпусе
# (2026-09-18) и ОТКЛОНЕНО как небезопасное для "слабых" корней (גזרות
# — ע"ו/ע"י, פ"נ, כפולים и т.п.): у них производные слова регулярно НЕ
# содержат буквально все согласные корня — слабая буква ассимилируется,
# выпадает или заменяется другой (напр. корень ק-ו-ם -> לְהָקִים,
# кайам קַיָּם — ו пропадает вовсе; ר-ב-ב -> רוֹב — удвоенная ב
# схлопывается в одну). На реальном корпусе это дало 190 срабатываний,
# из них ни одно не оказалось реальной ошибкой — все объясняются
# нормальной морфологией слабых корней. Без отдельной модели/списка
# исключений по типу גזרה это не отличить от настоящей утечки полей
# простой строковой проверкой, поэтому функция НЕ вызывается из
# find_all_violations и не участвует в audit_corpus.py — оставлена как
# задел, если тема "проверка корня" будет исследована отдельно и
# глубже (см. ROADMAP тренажёра).

_HEADWORD_FIELD_NAMES = {"word", "lemma", "t"}


def find_root_mismatch_violations(obj, root, path=""):
    if not root:
        return []
    root_letters = [c for c in root.split("-") if c]
    if not root_letters:
        return []
    violations = []
    for p, s in _walk_strings(obj, path):
        field = p.rsplit(".", 1)[-1].split("[")[0]
        if field not in _HEADWORD_FIELD_NAMES:
            continue
        bare = _bare_consonants(s)
        it = iter(bare)
        if not all(letter in it for letter in root_letters):
            violations.append((p, s))
    return violations


# ============================================================
# Правило 6: посторонние управляющие/комбинирующие символы
# ============================================================
# bidi-метки (LRM/RLM/изоляты) и кантилляционные знаки (те'амим) не
# принадлежат современному огласованному тексту курса — спекулятивный
# класс ошибок (не пойман вживую), но чёрный список дёшев, включаю на
# всякий случай.

_STRAY_CONTROL_RE = re.compile(
    "[\u200e\u200f\u2066-\u2069\u0591-\u05af]"
)


def find_stray_control_char_violations(obj, path=""):
    violations = []
    for p, s in _walk_strings(obj, path):
        m = _STRAY_CONTROL_RE.search(s)
        if m:
            violations.append((p, s, f"посторонний символ U+{ord(m.group()):04X}"))
    return violations


# ============================================================
# Правило 7: cloze_token не встречается как слово в своём предложении
# ============================================================
# Раньше — разовые скрипты по факту находки (7 случаев, 2026-09-14, см.
# ROADMAP.md/FEEDBACK_LOG.md), теперь постоянно переиспользуемая функция.
#
# Сравнение идёт по СОГЛАСНЫМ БЕЗ ОГЛАСОВОК (_bare_consonants), а не по
# буквальному '==' — огласовки/дагеш могут храниться в другом порядке
# (канонически эквивалентно, см. Правило 4) без реального рассинхрона.
# Учтено два реальных, НЕ баговых случая, оба нашлись только на живом
# прогоне по всему корпусу (2026-09-18), наивная версия давала на них
# сотни/десятки ложных срабатываний:
#   1. слитный предлог-частица (ה/ו/ב/כ/ל/מ/ש) спереди слова —
#      'שנייה' в тексте как 'השנייה' — не баг, нормальная связность речи
#      (то же явление, что hanging-prefix в читалке, тут частица уже
#      правильно слита, а не оторвана).
#   2. cloze_token — МНОГОСЛОВНАЯ фраза (напр. 'אֶת הַסֵּפֶר', 'בִּלְתִּי
#      צָפוּי') — ищем её как последовательность слов подряд в he, а не
#      одним токеном целиком.

def _word_matches_token_part(word_bare, part_bare):
    if not part_bare or word_bare == part_bare:
        return True
    if word_bare.endswith(part_bare):
        prefix = word_bare[: len(word_bare) - len(part_bare)]
        if 0 < len(prefix) <= 3 and all(c in _PREFIX_LETTERS for c in prefix):
            return True
    return False


def find_cloze_mismatch_violations(entry, path=""):
    violations = []
    for i, sent in enumerate(entry.get("sentences", []) if isinstance(entry, dict) else []):
        he = sent.get("he", "")
        token = sent.get("cloze_token", "")
        if not token:
            continue
        token_parts = [_bare_consonants(t) for t in token.split()]
        he_words = [_bare_consonants(w.strip(_STRIP_PUNCT)) for w in he.split()]
        found = False
        for start in range(len(he_words) - len(token_parts) + 1):
            if all(
                _word_matches_token_part(he_words[start + k], token_parts[k])
                for k in range(len(token_parts))
            ):
                found = True
                break
        if not found:
            p = f"{path}.sentences[{i}]" if path else f"sentences[{i}]"
            violations.append((p, token, he))
    return violations


# ============================================================
# Правило 8 (структурное, читалка): оторванная приставка-обрубок,
# оставшаяся отдельным словом-объектом вместо слияния со следующим словом
# ============================================================
# Детект вынесен сюда из E:\hebrew-reader\5_gemini_pipeline.py, чтобы им
# могли пользоваться и генерирующий пайплайн (форвард-guard), и
# ретроактивный аудит всего существующего корпуса (см. ROADMAP —
# 792 таких случая нашлись в двух книгах, созданных ДО того, как
# фиксер появился в пайплайне 2026-09-12).

_PREFIX_LETTERS = "הבוכלמש"


def _bare_letters_for_prefix_check(t):
    return re.sub(r"[^א-ת]", "", re.sub(r"[֑-ׇ]", "", t or ""))


def _is_prefix_fragment(w):
    b = _bare_letters_for_prefix_check(w.get("t", ""))
    return len(b) == 1 and b in _PREFIX_LETTERS


def find_hanging_prefix_violations(sentences, path=""):
    """sentences — список {"words": [{"t": ...}, ...]} (формат
    book-data-*.json/song-data-*.json читалки). Возвращает список
    (path, слово) для каждого найденного оторванного фрагмента-приставки,
    независимо от того, есть ли за ним следующее слово для слияния (в
    отличие от merge_prefix_fragments, который молча пропускает
    приставку в самом конце массива — тут это тоже репортим, раз это
    аудит, а не автофикс)."""
    violations = []
    for i, s in enumerate(sentences):
        ws = s.get("words", [])
        for j, w in enumerate(ws):
            if _is_prefix_fragment(w):
                p = f"{path}.sentences[{i}].words[{j}]" if path else f"sentences[{i}].words[{j}]"
                violations.append((p, w.get("t", "")))
    return violations


# ============================================================
# Обобщённый кэш подтверждённых не-нарушений (не только геминация) —
# теперь с хэшем содержимого, чтобы не быть уязвимым к тихой устарелости,
# если текст в этом месте поменяется позже (старая версия кэша была
# привязана только к (rule, root, path), без хэша).
# ============================================================

def _text_hash(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def load_verified_cache(cache_path):
    if os.path.exists(cache_path):
        return json.load(open(cache_path, encoding="utf-8"))
    return {}


def save_verified_cache(cache, cache_path):
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _cache_key(rule, path):
    return f"{rule}::{path}"


def is_verified(cache, rule, path, text):
    entry = cache.get(_cache_key(rule, path))
    return bool(entry) and entry.get("hash") == _text_hash(text)


def mark_verified(cache, rule, path, text):
    cache[_cache_key(rule, path)] = {
        "hash": _text_hash(text),
        "verified_at": datetime.date.today().isoformat(),
    }


# ============================================================
# Единая точка входа
# ============================================================

def find_all_violations(obj, root=None, path=""):
    """Прогоняет все правила разом (три исходных плюс non_nfc/
    stray_control, добавленные при консолидации QA — см. ROADMAP).
    root (корень записи, e.g. 'ה-י-ה') нужен для gemination и
    root_mismatch — без него оба просто пропускаются (напр. для
    контента, не привязанного к одному корню). cloze_mismatch и
    hanging_prefix требуют своей особой структуры записи (sentences[]/
    words[]), поэтому НЕ включены сюда — вызываются отдельно в
    audit_corpus.py, где эта структура точно есть.
    Возвращает список словарей {rule, path, word, reason?} — единый
    формат вместо разных форм кортежей у отдельных find_*."""
    out = []
    for p, word, reason in find_ktiv_chaser_violations(obj, path):
        out.append({"rule": "ktiv_chaser", "path": p, "word": word, "reason": reason})
    for p, word in find_homoglyph_violations(obj, path):
        out.append({"rule": "homoglyph", "path": p, "word": word})
    for p, word, reason in find_stray_control_char_violations(obj, path):
        out.append({"rule": "stray_control", "path": p, "word": word, "reason": reason})
    if root:
        for p, word in find_gemination_violations(obj, root, path):
            out.append({"rule": "gemination", "path": p, "word": word})
        # find_root_mismatch_violations сознательно НЕ подключено — см.
        # предупреждение у самой функции (слабые корни дают шум).
    return out


def _main():
    here = os.path.dirname(os.path.abspath(__file__))
    theory_path = os.path.join(here, "root_theory_all.json")
    sentences_path = os.path.join(here, "root_sentences_all.json")
    # fix_gemination.py кэширует (тип, корень, путь/индекс), для которых
    # МОДЕЛЬ уже подтвердила "не нарушение" (has_gemination сканирует
    # ЦЕЛОЕ поле/предложение, а не только слово нужного корня — регулярно
    # цепляет чужие слова с дагешем по другой причине). Без учёта кэша
    # здесь "Всего нарушений" вводит в заблуждение — считает подтверждённый
    # шум как нерешённые нарушения.
    verified_ok_path = os.path.join(here, "gemination_verified_ok.json")
    verified_ok = set()
    if os.path.exists(verified_ok_path):
        verified_ok = set(tuple(x) for x in json.load(open(verified_ok_path, encoding="utf-8")))

    total = 0
    confirmed_noise = 0
    if os.path.exists(theory_path):
        theory = json.load(open(theory_path, encoding="utf-8"))
        for entry in theory:
            v = find_all_violations(entry, root=entry.get("root"))
            for item in v:
                if item["rule"] == "gemination" and ("theory", entry["root"], item["path"]) in verified_ok:
                    confirmed_noise += 1
                    continue
                total += 1
                print(f"[theory:{entry['root']}] {item['rule']} · {item['path']} · {item['word']!r}", file=sys.stderr)

    if os.path.exists(sentences_path):
        sentences = json.load(open(sentences_path, encoding="utf-8"))
        for entry in sentences:
            v = find_all_violations(entry, root=entry.get("root"))
            for item in v:
                m = re.match(r"sentences\[(\d+)\]", item["path"])
                idx = int(m.group(1)) if m else None
                if item["rule"] == "gemination" and idx is not None and ("sentence", entry["root"], idx) in verified_ok:
                    confirmed_noise += 1
                    continue
                total += 1
                print(f"[sentences:{entry['root']}] {item['rule']} · {item['path']} · {item['word']!r}", file=sys.stderr)

    print(f"\nВсего нарушений: {total} (плюс {confirmed_noise} подтверждённых моделью не-нарушений, исключены)", file=sys.stderr)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    _main()
