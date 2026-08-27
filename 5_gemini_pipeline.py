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
же модель с той же тесной квотой. Дефолт здесь переставлен на
gemini-flash-lite-latest (алиас, отдельная от 3.6-flash линейка на момент
проверки) как наименее плохой доступный вариант, но реальная дневная квота
под него не проверена вживую на объёме — если разметка большой книги
упрётся в 429 быстрее, чем раньше, это не баг скрипта, это факт о текущих
лимитах Google.
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
                                "lemma": {"type": "string", "description": "словарная форма с огласовками"},
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

Присваивай id последовательно: s{start_id}, s{start_id_plus_1}, ...

Предложения (по одному на строку, в оригинальном порядке):
{sentences_block}
"""


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
    ap.add_argument("--model", default="gemini-flash-lite-latest")
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
    while i < len(sentences):
        batch = sentences[i:i + args.batch_size]
        print(f"[{i+1}-{i+len(batch)} / {len(sentences)}] обрабатываю...", file=sys.stderr)

        batch_result = process_batch(client, args.model, batch, i)
        if batch_result is None:
            print(f"\n!! Не удалось обработать этот кусок после нескольких попыток.\n"
                  f"Прогресс до предложения {i} сохранён в {args.out}.\n"
                  f"Перезапусти с флагом --resume, чтобы продолжить с этого места.",
                  file=sys.stderr)
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

    print(f"\nГотово -> {args.out}, всего предложений в файле: {len(result_sentences)}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
