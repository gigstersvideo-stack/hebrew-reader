"""
Автоматический пайплайн: текст книги -> book-data.json через Gemini API
со SCHEMA-ограниченным выводом (structured output). В отличие от ручного
копипаста в чате NotebookLM, здесь валидность JSON гарантирована самим
API, а не аккуратностью модели — весь класс багов с неэкранированными
кавычками, который мы столько чинили руками, здесь просто не может
возникнуть.

Работает полностью автоматически: скрипт сам режет текст на куски,
шлёт их по одному в API, сохраняет прогресс после каждого куска (если
прервётся на середине — можно продолжить с того же места через --resume,
ничего не потеряется), и на выходе даёт готовый book-data.json.

Установка:
    pip install google-genai --break-system-packages

Получить бесплатный API-ключ:
    https://aistudio.google.com/apikey
    (тот же аккаунт Google, что и для NotebookLM)

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 5_gemini_pipeline.py chapter1.txt --out book-data.json

Если прервётся (сеть моргнула, комп заснул и т.п.) — просто повтори
с флагом --resume, он сам увидит, сколько уже готово, и продолжит:
    python3 5_gemini_pipeline.py chapter1.txt --out book-data.json --resume

Проверить на небольшом куске перед тем, как гнать всю книгу:
    python3 5_gemini_pipeline.py chapter1.txt --out test.json --batch-size 10 --max-sentences 30

Про модель по умолчанию: изначально здесь стояла gemini-2.5-flash — держали
специально ОТДЕЛЬНОЙ от gemini-3.6-flash (у той в 2_generate_story.py жёсткий
бесплатный лимит 20 запросов/день), чтобы разметка целой книги (сотни
запросов при батче по 30 предложений) не упиралась в этот лимит. К 2026-08-27
Google полностью снял gemini-2.5-flash с бесплатного доступа (404 "no longer
available to new users"), а в тексте самой ошибки рекомендует переходить
именно на gemini-3.6-flash — то есть Google сводит оба сценария на одну и ту
же модель с той же тесной квотой.

Пробовали временно заменить на gemini-flash-lite-latest как формально отдельную
от 3.6-flash линейку — 2026-08-29 поймали на живой разметке реальный брак:
битый JSON ("Invalid \\uXXXX escape") и мусор в содержимом (переставленные
буквы в иврите вроде "רָוּנָי" вместо "רוני", посторонние кириллические
символы вроде "маһенький" вместо "маленький"). Не единичный сбой — совпало
с уже известной ненадёжностью lite-моделей на нишевых лингвистических
задачах (см. память проекта про gemini-3.5-flash-lite и огласовку). Дефолт
вернули на gemini-3.6-flash — надёжность важнее теоретически отдельной
квоты, которая на практике оказалась нерабочей. Для разметки ОГРОМНОЙ книги
(сотни запросов) это означает реальное упирание в лимит 20/день — тогда
единственный проверенный выход — растягивать по дням через --resume, либо
включить биллинг на этом же аккаунте, либо самому проверить вживую
gemini-flash-latest (основной алиас, на момент проверки отвечал 503
"высокая нагрузка" — не факт, что вообще брак, а не временная перегрузка)
на небольшом куске перед тем, как гнать всю книгу.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# сколько раз пересылать батч заново, если модель вернула не столько
# предложений, сколько было отправлено (см. комментарий у места вызова)
MISMATCH_RETRIES = 3

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Нужно: pip install google-genai --break-system-packages", file=sys.stderr)
    sys.exit(1)

from niqud_fixes import fix_deficient_spelling_standalone, is_false_positive


SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+')

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "literal": {"type": "string",
                                "description": "Дословный перевод через тире, порядок слов как в оригинале"},
                    "fluent": {"type": "string",
                               "description": "Естественный литературный перевод на русский, своя формулировка"},
                    "words": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "t": {"type": "string", "description": "слово ТОЧНО как в оригинале, с огласовками и пунктуацией"},
                                "lemma": {"type": "string", "description": "словарная форма с огласовками, ПОЛНОЕ написание (כתיב מלא) — с буквами вав/יод там, где они пишутся без огласовок, огласовка добавляется поверх них (напр. בּוֹקֶר, не בֹּקֶר; לִקְרוֹא, не לִקְרֹא)"},
                                "pos": {"type": "string", "description": "сущ./гл./прил./мест./предл./частица/нареч./числ./имя собств."},
                                "tr": {"type": "string", "description": "краткий перевод этой словоформы"},
                                "extra": {"type": "string", "description": "биньян+время для глаголов, род/число для сущ/прил, пусто если нечего добавить"},
                                "root": {"type": "string", "description": "корень через дефис ивритскими буквами, или —"},
                            },
                            "required": ["t", "lemma", "pos", "tr", "extra", "root"],
                        },
                    },
                },
                "required": ["id", "literal", "fluent", "words"],
            },
        },
    },
    "required": ["sentences"],
}

PROMPT_TEMPLATE = """Ты — лингвист-эксперт по ивриту, помогаешь готовить материал для изучения языка.

Обработай нижеприведённые предложения (иврит), СТРОГО В ТОМ ЖЕ ПОРЯДКЕ,
ничего не пропуская и не объединяя. Для каждого предложения:
- "literal": дословный перевод через тире (слово-в-слово, порядок как в оригинале)
- "fluent": естественный грамотный перевод на русский — твоя собственная
  формулировка, не копируй ничей существующий опубликованный перевод
- "words": разбери КАЖДОЕ слово, включая короткие служебные (את, ה־, ו־ и т.п.)
- "lemma" каждого слова — ТОЛЬКО полное написание (כתיב מלא), как в современных
  израильских словарях/книгах для изучающих язык: НИКОГДА не убирай буквы
  ו (вав) или י (йод) в пользу самой огласовки, даже если "традиционные"
  библейские правила (כתיב חסר) в этом месте требуют их убрать. Примеры:
  בּוֹקֶר (не בֹּקֶר), לִקְרוֹא (не לִקְרֹא), מְיוּחָד (не מְיֻחָד),
  שׁוּלְחָן (не שֻׁלְחָן). Это касается и лемм, и текста "t" самого слова.

Присваивай id последовательно: s{start_id}, s{start_id_plus_1}, ...

Предложения (по одному на строку, в оригинальном порядке):
{sentences_block}
"""


DEFICIENT_SPELLING_RE = re.compile(r'(?<!ו)[ֹֻ]')  # холам/кубуц без вав перед ними


def warn_deficient_spelling(sentences):
    """Раньше это было чисто предупреждение (см. историю ниже) — теперь
    сначала пробует ПОЧИНИТЬ (niqud_fixes.fix_deficient_spelling_standalone,
    механически: кубуц без вав чинится всегда, холам — если слово не входит
    в список законных исключений типа כֹּחַ/פֹּה/הַכֹּל) прямо в w["t"]/w["lemma"],
    и только для того, что чинить не рискнули (после исключений), выводит
    предупреждение — таких должно остаться на порядок меньше, чем раньше.
    В отличие от add_nikud_checked в 2_generate_story.py, здесь нет исходного
    текста без огласовок для строгой сверки — поэтому это по-прежнему
    эвристика, не гарантия (см. книгу бет 2026-08-29: то же самое находили
    в lemma отдельно от t)."""
    fixed_count = 0
    warned = set()
    for s in sentences:
        for w in s.get("words", []):
            for field in ("t", "lemma"):
                val = w.get(field, "")
                if not val or not DEFICIENT_SPELLING_RE.search(val):
                    continue
                fixed_val, changed = fix_deficient_spelling_standalone(val)
                if changed:
                    w[field] = fixed_val
                    fixed_count += 1
                elif is_false_positive(val):
                    continue  # известное законное исключение — не шумим
                elif val not in warned:
                    warned.add(val)
                    print(f"  ⚠ похоже на неполное написание в {field}: {val!r} "
                          f"(слово {w.get('t')!r}) — проверь вручную", file=sys.stderr)
    if fixed_count:
        print(f"\n✓ Автоматически починено мест с пропавшей буквой (вав/кубуц): {fixed_count}.",
              file=sys.stderr)
    if warned:
        print(f"⚠ Похожих случаев, которые чинить не рискнули: {len(warned)}. "
              f"Это эвристика, не всегда точная, но стоит свериться глазами.",
              file=sys.stderr)


_PREFIX_LETTERS = "הבוכלמש"
_MAQAF = "־"


def _bare_letters(t):
    return re.sub(r"[^א-ת]", "", re.sub(r"[֑-ׇ]", "", t))


def _is_prefix_fragment(w):
    b = _bare_letters(w.get("t", ""))
    return len(b) == 1 and b in _PREFIX_LETTERS


def merge_prefix_fragments(sentences):
    """Изредка модель отдаёт неотделяемые приставки (ה/ו/ב/כ/ל/מ/ש) как
    самостоятельные "слова" — со своим словом-объектом и пробелом перед
    корнем, хотя в иврите они пишутся слитно (поймали вживую дважды:
    bridge-alef-bet — 132 случая из 862, seahouse-dalet — 429 из 5081).
    Склеивает подряд идущие фрагменты с последующим словом ДО отправки в
    озвучку/иллюстрации — там их наличие ломает и текст (лишние пробелы),
    и тайминги (лишние токены не совпадают с границами TTS)."""
    total_merged = 0
    for s in sentences:
        ws = s.get("words", [])
        new_ws = []
        i = 0
        while i < len(ws):
            if _is_prefix_fragment(ws[i]) and i + 1 < len(ws):
                j = i
                prefix_text = ""
                while j < len(ws) and _is_prefix_fragment(ws[j]) and j + 1 < len(ws):
                    prefix_text += ws[j]["t"].replace(_MAQAF, "")
                    j += 1
                base = dict(ws[j])
                base["t"] = prefix_text + base["t"]
                new_ws.append(base)
                total_merged += (j - i)
                i = j + 1
            else:
                new_ws.append(ws[i])
                i += 1
        s["words"] = new_ws
    if total_merged:
        print(f"\n✓ Склеено оторванных приставок-обрубков: {total_merged}.", file=sys.stderr)


def split_sentences(raw_text):
    paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
    out = []
    for para in paragraphs:
        joined = re.sub(r'\s*\n\s*', ' ', para).strip()
        if not joined:
            continue
        for chunk in SENTENCE_SPLIT_RE.split(joined):
            chunk = chunk.strip()
            if chunk:
                out.append(chunk)
    return out


def process_batch(client, model, batch, start_idx, retries=3):
    sentences_block = "\n".join(batch)
    prompt = PROMPT_TEMPLATE.format(
        start_id=start_idx + 1,
        start_id_plus_1=start_idx + 2,
        sentences_block=sentences_block,
    )
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"  попытка {attempt+1}/{retries} не удалась ({e}), жду и повторяю...",
                  file=sys.stderr)
            time.sleep(3)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text_path", help="локальный .txt файл с текстом книги (иврит)")
    ap.add_argument("--out", default="book-data.json")
    ap.add_argument("--batch-size", type=int, default=30,
                     help="сколько предложений отправлять за один запрос к API")
    ap.add_argument("--model", default="gemini-3.6-flash")
    ap.add_argument("--api-key", default=None,
                     help="или задай переменную окружения GEMINI_API_KEY")
    ap.add_argument("--resume", action="store_true",
                     help="продолжить с того места, где остановился --out файл")
    ap.add_argument("--max-sentences", type=int, default=None,
                     help="ограничить количество предложений — удобно для пробного прогона")
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Нужен API-ключ: флаг --api-key или переменная окружения GEMINI_API_KEY.",
              file=sys.stderr)
        print("Получить бесплатно: https://aistudio.google.com/apikey", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    with open(args.text_path, encoding="utf-8") as f:
        raw = f.read()

    sentences = split_sentences(raw)
    if args.max_sentences:
        sentences = sentences[:args.max_sentences]
    print(f"Всего предложений к обработке: {len(sentences)}", file=sys.stderr)

    result_sentences = []
    start_idx = 0
    out_path = Path(args.out)
    if args.resume and out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)
        result_sentences = existing.get("sentences", [])
        # next_index — точная позиция в ИСХОДНОМ массиве предложений, откуда
        # продолжать; НЕ то же самое, что len(result_sentences) — батч мог
        # вернуть больше/меньше предложений, чем просили (см. предупреждение
        # ниже), и тогда эти два числа расходятся. Раньше здесь стояло
        # len(result_sentences) — из-за этого при таком расхождении resume
        # либо задваивал уже готовые предложения, либо пропускал кусок
        # (поймали вживую: 3 задвоенных id и 2 пропуска в одной книге).
        # Файлы, сохранённые до этого фикса, next_index не имеют — тогда
        # используем len(result_sentences) как лучшее приближение.
        start_idx = existing.get("next_index", len(result_sentences))
        print(f"Продолжаю с предложения {start_idx + 1} "
              f"(готовых записей: {len(result_sentences)} из {len(sentences)}).", file=sys.stderr)

    i = start_idx
    incomplete = False
    while i < len(sentences):
        batch = sentences[i:i + args.batch_size]
        print(f"[{i+1}-{i+len(batch)} / {len(sentences)}] обрабатываю...", file=sys.stderr)

        batch_result = process_batch(client, args.model, batch, i)
        got = batch_result.get("sentences", []) if batch_result else []

        # Модель иногда возвращает МЕНЬШЕ предложений, чем в батче (поймали
        # вживую: 30 отправлено, 2 получено — раньше это просто молча
        # принималось как есть, теряя 28 предложений без единого явного
        # сообщения об ошибке, только тихое предупреждение в логе, которое
        # легко пропустить). Теперь — пересылаем весь батч ЗАНОВО до
        # MISMATCH_RETRIES раз, и только если ни один повтор не дал точного
        # совпадения — останавливаемся жёстко, как при полном отказе, а не
        # продолжаем с недостачей.
        mismatch_retries = 0
        while batch_result is not None and len(got) != len(batch) and mismatch_retries < MISMATCH_RETRIES:
            mismatch_retries += 1
            print(f"  ⚠ ожидал {len(batch)} предложений в ответе, получил {len(got)} — "
                  f"пересылаю батч заново ({mismatch_retries}/{MISMATCH_RETRIES})...",
                  file=sys.stderr)
            time.sleep(3)
            batch_result = process_batch(client, args.model, batch, i)
            got = batch_result.get("sentences", []) if batch_result else []

        if batch_result is None or len(got) != len(batch):
            print(f"\n!! Не удалось получить полный батч ({len(batch)} предложений) "
                  f"даже после {MISMATCH_RETRIES} повторов — получено {len(got)}.\n"
                  f"Прогресс до предложения {i} сохранён в {args.out}.\n"
                  f"Перезапусти с флагом --resume, чтобы продолжить с этого места.",
                  file=sys.stderr)
            incomplete = True
            break

        result_sentences.extend(got)
        i += args.batch_size

        # Сохраняем после каждого куска, а не в конце — если прервётся
        # на середине книги, ничего не потеряется. next_index = i (позиция в
        # исходном массиве), а не len(result_sentences) — см. комментарий выше.
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"sentences": result_sentences, "next_index": i}, f, ensure_ascii=False, indent=2)

        time.sleep(1)  # вежливая пауза между запросами

    # оба мутируют result_sentences прямо в w["t"]/w["lemma"]/w["words"] —
    # обязательно ДО финальной записи файла, иначе починка на диск не попадёт
    merge_prefix_fragments(result_sentences)
    warn_deficient_spelling(result_sentences)

    if not incomplete:
        # готово целиком — убираем служебное поле next_index из финального
        # файла, дальше по пайплайну (озвучка/иллюстрации/сайт) его не ждут
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"sentences": result_sentences}, f, ensure_ascii=False, indent=2)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"sentences": result_sentences, "next_index": i}, f, ensure_ascii=False, indent=2)

    print(f"\nГотово -> {args.out}, всего предложений в файле: {len(result_sentences)}",
          file=sys.stderr)

    if incomplete:
        # раньше здесь молча выходили кодом 0, даже если реально обработали
        # только часть книги — retry-цикл видел "успех" и останавливался,
        # думая, что всё готово (нашли это вживую на 359 из 589 предложений)
        sys.exit(1)


if __name__ == "__main__":
    main()
