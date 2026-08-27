"""
2_generate_story.py — генерирует оригинальный (без авторских прав) рассказ
на иврите под заданный уровень (алеф/бет/гимель/далет) через Gemini API,
затем отдельным вызовом того же API расставляет огласовки (никуд).

На выходе — .txt файл, готовый как вход для 5_gemini_pipeline.py (тот
режет текст на предложения и размечает перевод/грамматику/корни), и
сайдкар .meta.json с кратким описанием сюжета и ключевыми словами для
будущих иллюстраций/мнемоники.

Про никуд: идеологически для этого шага подошёл бы специализированный
инструмент вроде Nakdan (Dicta) — общие языковые модели известны тем,
что путают огласовки в неоднозначных словах. На момент написания скрипта
не удалось найти/подтвердить рабочий бесплатный API Nakdan (публичный
"быстрый" накдан на nakdan.dicta.org.il работает только в браузере,
целиком на JS, без обращения к серверу; у "профессиональной" версии
на nakdanpro.dicta.org.il вероятно есть бэкенд, но добраться до его
контракта не вышло). Поэтому здесь никуд расставляет тоже Gemini,
отдельным сфокусированным вызовом — если найдёшь подтверждённый
Nakdan-эндпоинт, эту функцию (add_nikud) легко заменить.

Установка:
    pip install google-genai --break-system-packages

Получить бесплатный API-ключ:
    https://aistudio.google.com/apikey

Запуск:
    export GEMINI_API_KEY=твой_ключ
    python3 2_generate_story.py alef "робот-уборщик находит на балконе кота" --out story-alef.txt
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Нужно: pip install google-genai --break-system-packages", file=sys.stderr)
    sys.exit(1)


LEVEL_PROFILES = {
    "alef": {
        "name": "א (алеф, начальный)",
        "default_words": 150,
        "constraints": (
            "- Только настоящее время (הווה), биньян פעל.\n"
            "- ~300-500 самых базовых слов: семья, дом, еда, числа, дни недели.\n"
            "- Короткие предложения, 5-8 слов, без придаточных.\n"
            "- Без сложных метафор и абстрактных понятий."
        ),
    },
    "bet": {
        "name": "ב (бет, начинающий продолжающий)",
        "default_words": 300,
        "constraints": (
            "- + прошедшее время (עבר), базовое будущее (עתיד), биньян פיעל.\n"
            "- ~800-1200 слов, простые союзы (כי, אבל, כש-, אז).\n"
            "- Предложения до ~12 слов.\n"
            "- Можно немного диалогов."
        ),
    },
    "gimel": {
        "name": "ג (гимель, средний)",
        "default_words": 500,
        "constraints": (
            "- Все основные биньяны, включая הפעיל и התפעל.\n"
            "- ~2000+ слов, идиомы и устойчивые выражения — можно.\n"
            "- Придаточные предложения, более богатое описание.\n"
            "- Активный диалог между персонажами."
        ),
    },
    "dalet": {
        "name": "ד (далет, продвинутый)",
        "default_words": 800,
        "constraints": (
            "- Страдательный залог (פועל, הופעל), сложный синтаксис.\n"
            "- Почти без ограничений лексики, литературные обороты.\n"
            "- Абстрактные понятия, эмоциональные нюансы.\n"
            "- Свободная структура повествования."
        ),
    },
}

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "story_text": {
            "type": "string",
            "description": "Текст истории на иврите БЕЗ огласовок (никуда), "
                            "разбитый на абзацы — между абзацами пустая строка.",
        },
        "summary_ru": {
            "type": "string",
            "description": "1-2 предложения с кратким описанием сюжета на русском",
        },
        "keywords": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma": {"type": "string", "description": "словарная форма слова, с огласовками"},
                    "translation": {"type": "string", "description": "перевод на русский"},
                },
                "required": ["lemma", "translation"],
            },
        },
    },
    "required": ["story_text", "summary_ru", "keywords"],
}

NIQUD_SCHEMA = {
    "type": "object",
    "properties": {
        "vocalized_text": {
            "type": "string",
            "description": "Тот же текст, слово в слово, с добавленными огласовками (никуд). "
                            "Ни одно слово, знак препинания или разбивка на абзацы не меняются.",
        },
    },
    "required": ["vocalized_text"],
}

COVER_SCHEMA = {
    "type": "object",
    "properties": {
        "cover_svg": {
            "type": "string",
            "description": "Полный код SVG-иконки для обложки книги, от <svg> до </svg>",
        },
    },
    "required": ["cover_svg"],
}

STORY_PROMPT = """Ты — автор оригинальных коротких историй на иврите для изучающих язык в ульпане.

ЗАДАЧА: напиши полностью оригинальный рассказ — без существующих
персонажей, сеттингов или сюжетов из чужих произведений (книг, фильмов,
игр). Сюжет и все имена придумай сам.

Уровень иврита: {level_name}
Завязка/тема: {premise}
Целевой объём: примерно {word_count} слов

ОГРАНИЧЕНИЯ ПО УРОВНЮ (важнее красоты слога — не выходи за них):
{constraints}

ТРЕБОВАНИЯ К ФОРМАТУ:
- story_text — только сам текст истории на иврите, БЕЗ огласовок.
- Раздели на короткие абзацы (2-4 предложения), между абзацами — пустая строка.
- Естественный порядок слов и идиоматичный иврит, не калька с русского/английского.
- summary_ru — краткое описание сюжета на русском, 1-2 предложения.
- keywords — 8-12 слов истории, которые стоит подсветить учащемуся
  (лемма с огласовками + перевод), подходящих для иллюстраций/мнемоники.
"""

COVER_PROMPT = """Нарисуй простую минималистичную SVG-иконку для обложки книги.

Сюжет книги: {summary_ru}

Требования к стилю (важно соблюсти точно):
- viewBox="0 0 300 140" (широкий формат, как обложка-баннер)
- Только тонкие контурные линии (stroke), без заливок фотографического типа
- Цвета ТОЛЬКО через CSS-переменные: stroke="var(--ink)" для основных контуров,
  fill/stroke="var(--gold)" для мелких акцентов (точки, звёзды, блики)
- stroke-width около 2, stroke-linecap="round", fill="none" на контурах
  (кроме мелких акцентных элементов — их можно заливать var(--gold))
- Простые геометрические формы, никаких градиентов, теней, фотореализма
- Никакого текста внутри SVG
- Композиция должна читаться на широком прямоугольнике, не квадрате

Верни только cover_svg — код SVG целиком, от <svg> до </svg>.
"""

NIQUD_PROMPT = """Расставь огласовки (никуд) в этом тексте на иврите.

ТРЕБОВАНИЯ:
- Не меняй ни одного слова, порядок слов или пунктуацию — только добавь никуд.
- Огласовки должны быть грамматически и семантически корректны для контекста
  связного рассказа (не разрозненные слова — учитывай смысл соседних фраз).
- Сохрани разбивку на абзацы как в оригинале.

Текст:
{text}
"""


CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "chapter_text": {
            "type": "string",
            "description": "Текст ЭТОЙ главы на иврите БЕЗ огласовок, разбитый на абзацы "
                            "(между абзацами пустая строка). Без заголовка/номера главы.",
        },
        "summary_so_far": {
            "type": "string",
            "description": "Краткое описание сюжета КНИГИ ЦЕЛИКОМ на русском с учётом этой "
                            "главы (не только этой главы) — 2-4 предложения.",
        },
        "new_keywords": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma": {"type": "string", "description": "словарная форма слова, с огласовками"},
                    "translation": {"type": "string", "description": "перевод на русский"},
                },
                "required": ["lemma", "translation"],
            },
            "description": "3-6 слов, впервые значимых именно в этой главе.",
        },
    },
    "required": ["chapter_text", "summary_so_far", "new_keywords"],
}

CHAPTER_PROMPT = """Ты — автор оригинальных коротких историй на иврите для изучающих язык в ульпане.

Ты пишешь книгу по частям. Эта генерация — ГЛАВА {chapter_num} из {total_chapters}.

ЗАДАЧА: полностью оригинальная история — без существующих персонажей, сеттингов
или сюжетов из чужих произведений (книг, фильмов, игр).

Уровень иврита: {level_name}
Общая завязка/тема книги: {premise}
Целевой объём ЭТОЙ главы: примерно {chapter_words} слов
(вся книга — около {total_words} слов, всего глав — {total_chapters})

ОГРАНИЧЕНИЯ ПО УРОВНЮ (важнее красоты слога — не выходи за них):
{constraints}
{context_block}
ТРЕБОВАНИЯ К ФОРМАТУ:
- chapter_text — только текст ЭТОЙ главы на иврите, БЕЗ огласовок, без заголовка
  или номера главы.
- Раздели на короткие абзацы (2-4 предложения), между абзацами — пустая строка.
- Естественный порядок слов и идиоматичный иврит, не калька с русского/английского.
- summary_so_far — краткое описание сюжета КНИГИ ЦЕЛИКОМ (с учётом этой главы) на
  русском, 2-4 предложения — это уйдёт в контекст следующей главы и на обложку.
- new_keywords — 3-6 слов, впервые значимых именно в этой главе (лемма с
  огласовками + перевод).
{ending_instruction}
"""

CONTEXT_BLOCK_TEMPLATE = """
КОНТЕКСТ ПРЕДЫДУЩИХ ГЛАВ:
Краткое содержание сюжета до этой главы: {summary_so_far}

Конец предыдущей главы (для плавного продолжения — не повторяй эти события):
{prev_tail}

Продолжай сюжет естественно, без повторов и без противоречий с уже написанным.
"""

NOT_LAST_INSTRUCTION = (
    "Это НЕ последняя глава — не завершай сюжет и не пиши развязку, "
    "оставь естественный крючок для продолжения."
)
LAST_INSTRUCTION = (
    "Это ПОСЛЕДНЯЯ глава — заверши сюжет, дай развязку, история должна "
    "ощущаться завершённой."
)


def call_with_retry(fn, *args, retries=3, **kwargs):
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"    попытка {attempt+1}/{retries} не удалась ({e}), жду и повторяю...",
                  file=sys.stderr)
            time.sleep(3)


def chapter_tail(text, max_chars=500):
    tail = text.strip()[-max_chars:]
    # не обрезать посреди слова
    cut = tail.find(" ")
    return tail[cut + 1:] if cut != -1 and cut < 40 else tail


def generate_chapter(client, model, level, premise, chapter_num, total_chapters,
                      chapter_words, total_words, summary_so_far=None, prev_text=None):
    profile = LEVEL_PROFILES[level]
    if chapter_num == 1:
        context_block = ""
    else:
        context_block = "\n" + CONTEXT_BLOCK_TEMPLATE.format(
            summary_so_far=summary_so_far,
            prev_tail=chapter_tail(prev_text),
        )
    prompt = CHAPTER_PROMPT.format(
        chapter_num=chapter_num,
        total_chapters=total_chapters,
        level_name=profile["name"],
        premise=premise,
        chapter_words=chapter_words,
        total_words=total_words,
        constraints=profile["constraints"],
        context_block=context_block,
        ending_instruction=LAST_INSTRUCTION if chapter_num == total_chapters else NOT_LAST_INSTRUCTION,
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CHAPTER_SCHEMA,
        ),
    )
    return json.loads(response.text)


def load_progress(progress_path):
    if progress_path.exists():
        with open(progress_path, encoding="utf-8") as f:
            return json.load(f)
    return {"chapters": []}


def save_progress(progress_path, progress):
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def generate_story_chunked(client, model, level, premise, total_words, chapter_words,
                            out_path, resume, max_chapters=30):
    """Пишет книгу по главам с сохранением прогресса после каждой (--resume
    продолжит с последней недописанной), передавая в каждый следующий вызов
    только сжатое summary_so_far + хвост предыдущей главы — а не всю книгу
    целиком — чтобы контекст не разрастался пропорционально длине книги."""
    total_chapters = min(max_chapters, max(1, round(total_words / chapter_words)))
    progress_path = Path(str(out_path) + ".progress.json")

    progress = load_progress(progress_path) if resume else {"chapters": []}
    chapters = progress["chapters"]

    if chapters and len(chapters) >= total_chapters:
        print(f"Прогресс уже содержит {len(chapters)} из {total_chapters} глав — ничего генерировать не нужно.",
              file=sys.stderr)

    while len(chapters) < total_chapters:
        chapter_num = len(chapters) + 1
        print(f"Пишу главу {chapter_num}/{total_chapters} (~{chapter_words} слов)...", file=sys.stderr)

        prev = chapters[-1] if chapters else None
        try:
            chapter = call_with_retry(
                generate_chapter, client, model, level, premise,
                chapter_num, total_chapters, chapter_words, total_words,
                summary_so_far=prev["summary_so_far"] if prev else None,
                prev_text=prev["text"] if prev else None,
            )

            print(f"  Расставляю огласовки главы {chapter_num}...", file=sys.stderr)
            vocalized = call_with_retry(add_nikud, client, model, chapter["chapter_text"])
        except Exception as e:
            print(f"\n!! Глава {chapter_num} не удалась после нескольких попыток ({e}).\n"
                  f"Прогресс {len(chapters)} из {total_chapters} глав сохранён в {progress_path}.\n"
                  f"Перезапусти с тем же --out и флагом --resume, чтобы продолжить с этого места.",
                  file=sys.stderr)
            sys.exit(1)

        chapters.append({
            "text": chapter["chapter_text"],
            "vocalized": vocalized,
            "summary_so_far": chapter["summary_so_far"],
            "new_keywords": chapter["new_keywords"],
        })
        save_progress(progress_path, {"chapters": chapters})
        time.sleep(1)

    full_vocalized = "\n\n".join(c["vocalized"].strip() for c in chapters)
    final_summary = chapters[-1]["summary_so_far"]
    seen_lemmas = set()
    keywords = []
    for c in chapters:
        for kw in c["new_keywords"]:
            if kw["lemma"] not in seen_lemmas:
                seen_lemmas.add(kw["lemma"])
                keywords.append(kw)

    return {"story_text": full_vocalized, "summary_ru": final_summary, "keywords": keywords}, progress_path


def generate_story(client, model, level, premise, word_count):
    profile = LEVEL_PROFILES[level]
    prompt = STORY_PROMPT.format(
        level_name=profile["name"],
        premise=premise,
        word_count=word_count,
        constraints=profile["constraints"],
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=STORY_SCHEMA,
        ),
    )
    return json.loads(response.text)


def add_nikud(client, model, text):
    prompt = NIQUD_PROMPT.format(text=text)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=NIQUD_SCHEMA,
        ),
    )
    return json.loads(response.text)["vocalized_text"]


def generate_cover_svg(client, model, summary_ru):
    prompt = COVER_PROMPT.format(summary_ru=summary_ru)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=COVER_SCHEMA,
        ),
    )
    return json.loads(response.text)["cover_svg"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("level", choices=list(LEVEL_PROFILES.keys()), help="alef / bet / gimel / dalet")
    ap.add_argument("premise", help="завязка/тема истории (по-русски или на иврите)")
    ap.add_argument("--word-count", type=int, default=None,
                     help="целевой объём в словах (по умолчанию — типичный для уровня)")
    ap.add_argument("--out", default=None, help="куда сохранить .txt (по умолчанию story-<level>.txt)")
    ap.add_argument("--model", default="gemini-3.6-flash")
    ap.add_argument("--api-key", default=None, help="или задай переменную окружения GEMINI_API_KEY")
    ap.add_argument("--chapter-words", type=int, default=None,
                     help="писать книгу по главам примерно из стольки слов каждая, вместо "
                          "одного запроса на всю книгу — нужно для книг заметно больше "
                          "типичного объёма уровня (см. README, раздел про максимальный размер)")
    ap.add_argument("--resume", action="store_true",
                     help="продолжить главную генерацию с последней недописанной главы "
                          "(смотрит в <out>.progress.json); без --chapter-words не имеет смысла")
    ap.add_argument("--max-chapters", type=int, default=30,
                     help="защитный потолок числа глав при --chapter-words")
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Нужен API-ключ: флаг --api-key или переменная окружения GEMINI_API_KEY.", file=sys.stderr)
        print("Получить бесплатно: https://aistudio.google.com/apikey", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    word_count = args.word_count or LEVEL_PROFILES[args.level]["default_words"]
    out_path = args.out or f"story-{args.level}.txt"
    meta_path = out_path.rsplit(".", 1)[0] + ".meta.json"

    chunked = bool(args.chapter_words and args.chapter_words < word_count)

    if chunked:
        print(f"Пишу историю по главам (уровень {args.level}, ~{word_count} слов, "
              f"~{args.chapter_words} слов/глава)...", file=sys.stderr)
        story, progress_path = generate_story_chunked(
            client, args.model, args.level, args.premise, word_count,
            args.chapter_words, out_path, args.resume, args.max_chapters,
        )
        vocalized = story["story_text"]  # уже с огласовками — расставлены по главам
        print(f"Готово {len(story['keywords'])} новых ключевых слов из всех глав.", file=sys.stderr)
    else:
        progress_path = None
        print(f"Пишу историю (уровень {args.level}, ~{word_count} слов)...", file=sys.stderr)
        story = generate_story(client, args.model, args.level, args.premise, word_count)

        print("Расставляю огласовки...", file=sys.stderr)
        vocalized = add_nikud(client, args.model, story["story_text"])

    print("Рисую обложку...", file=sys.stderr)
    cover_svg = generate_cover_svg(client, args.model, story["summary_ru"])

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(vocalized)

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "level": args.level,
            "premise": args.premise,
            "word_count_target": word_count,
            "summary_ru": story["summary_ru"],
            "keywords": story["keywords"],
            "cover_svg": cover_svg,
        }, f, ensure_ascii=False, indent=2)

    if progress_path and progress_path.exists():
        progress_path.unlink()

    print(f"\nГотово:\n  текст -> {out_path}\n  метаданные -> {meta_path}", file=sys.stderr)
    print(f"\nДальше: python 5_gemini_pipeline.py {out_path} --out book-data-{args.level}.json", file=sys.stderr)


if __name__ == "__main__":
    main()
