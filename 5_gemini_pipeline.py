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

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Нужно: pip install google-genai --break-system-packages", file=sys.stderr)
    sys.exit(1)


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
# слова, где холам/кубуц законно стоит без вав в ЛЮБОМ стиле написания — не баг
DEFICIENT_SPELLING_FALSE_POSITIVES = {
    "לֹא", "אֵיפֹה", "כֹּל", "זֹאת", "כֹּחַ", "רֹאשׁ", "זֹה", "מִכֹּל", "הַכֹּל",
}


def warn_deficient_spelling(sentences):
    """Эвристика (не строгая проверка, как у add_nikud_checked в 2_generate_story.py —
    тут нет исходного текста без огласовок для точного сравнения): холам/кубуц без
    вав перед ними обычно значит модель убрала букву в пользу огласовки вместо того,
    чтобы добавить её поверх. Не блокирует — просто предупреждает, чтобы не разошлось
    незамеченным (см. книгу бет 2026-08-29: то же самое находили в lemma отдельно от t)."""
    seen = set()
    for s in sentences:
        for w in s.get("words", []):
            for field in ("t", "lemma"):
                val = w.get(field, "")
                bare = val.rstrip('.,:;!?"“”')
                if bare in DEFICIENT_SPELLING_FALSE_POSITIVES or val in seen:
                    continue
                if DEFICIENT_SPELLING_RE.search(bare):
                    seen.add(val)
                    print(f"  ⚠ похоже на неполное написание в {field}: {val!r} "
                          f"(слово {w.get('t')!r}) — проверь вручную", file=sys.stderr)
    if seen:
        print(f"\n⚠ Похожих случаев: {len(seen)}. Это эвристика, не всегда точная "
              f"(некоторые слова законно без вав), но стоит свериться глазами.",
              file=sys.stderr)


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
        start_idx = len(result_sentences)
        print(f"Продолжаю с предложения {start_idx + 1} "
              f"(уже готово {start_idx} из {len(sentences)}).", file=sys.stderr)

    i = start_idx
    incomplete = False
    while i < len(sentences):
        batch = sentences[i:i + args.batch_size]
        print(f"[{i+1}-{i+len(batch)} / {len(sentences)}] обрабатываю...", file=sys.stderr)

        batch_result = process_batch(client, args.model, batch, i)
        if batch_result is None:
            print(f"\n!! Не удалось обработать этот кусок после нескольких попыток.\n"
                  f"Прогресс до предложения {i} сохранён в {args.out}.\n"
                  f"Перезапусти с флагом --resume, чтобы продолжить с этого места.",
                  file=sys.stderr)
            incomplete = True
            break

        got = batch_result.get("sentences", [])
        if len(got) != len(batch):
            print(f"  ⚠ ожидал {len(batch)} предложений в ответе, получил {len(got)} — "
                  f"стоит выборочно свериться с оригиналом на этом участке",
                  file=sys.stderr)

        result_sentences.extend(got)

        # Сохраняем после каждого куска, а не в конце — если прервётся
        # на середине книги, ничего не потеряется.
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"sentences": result_sentences}, f, ensure_ascii=False, indent=2)

        i += args.batch_size
        time.sleep(1)  # вежливая пауза между запросами

    warn_deficient_spelling(result_sentences)

    print(f"\nГотово -> {args.out}, всего предложений в файле: {len(result_sentences)}",
          file=sys.stderr)

    if incomplete:
        # раньше здесь молча выходили кодом 0, даже если реально обработали
        # только часть книги — retry-цикл видел "успех" и останавливался,
        # думая, что всё готово (нашли это вживую на 359 из 589 предложений)
        sys.exit(1)


if __name__ == "__main__":
    main()
