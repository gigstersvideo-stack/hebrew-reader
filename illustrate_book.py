"""
illustrate_book.py — добавляет 2-3 иллюстрации-мнемоники к уже размеченному
book-data.json: слово выбирается по РЕАЛЬНОЙ частотности (списки Dicta,
top_500/top_20000), а не наугад; мнемоника (созвучие + абсурдная сценка)
пишется через Gemini; картинка рисуется настоящей диффузионной моделью
через Cloudflare Workers AI (@cf/black-forest-labs/flux-1-schnell,
бесплатно в рамках дневного лимита нейронов) — SVG для этого не годится,
он не умеет достоверно рисовать конкретное действие/сценку.

Установка:
    pip install google-genai requests --break-system-packages

Нужны:
    GEMINI_API_KEY       — https://aistudio.google.com/apikey
    CF_API_TOKEN          — Cloudflare API-токен с правом Workers AI
    CF_ACCOUNT_ID         — id аккаунта Cloudflare

Запуск:
    python illustrate_book.py book-data.json --out book-data-illustrated.json \
        --images-dir illustrations_mybook --count 3
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

from google import genai
from google.genai import types
from google.genai import errors as genai_errors


def with_retries(fn, retries=4, delay=5):
    for attempt in range(retries):
        try:
            return fn()
        except genai_errors.ServerError as e:
            if attempt == retries - 1:
                raise
            print(f"  Gemini перегружен ({e}), жду {delay}с и повторяю "
                  f"({attempt+1}/{retries})...", file=sys.stderr)
            time.sleep(delay)

FREQ_500_URL = "https://nakdan.dicta.org.il/top_500_words.json"
FREQ_20000_URL = "https://nakdan.dicta.org.il/top_20000_partial.json"

# части речи, которые не стоит иллюстрировать, даже если формально "редкие"
# (частицы/предлоги/союзы/местоимения не несут отдельного образа)
SKIP_POS = {"частица", "предл.", "союз", "мест.", "числ.", "имя собств."}

NIKUD_RE = re.compile(r"[֑-ׇ]")
PUNCT_STRIP_RE = re.compile(r"^[^֐-׿]*|[^֐-׿]*$")


def strip_nikud(s):
    return NIKUD_RE.sub("", s)


def clean_surface(t):
    return PUNCT_STRIP_RE.sub("", strip_nikud(t))


def fetch_json(url):
    with urllib.request.urlopen(url) as r:
        return json.load(r)


def load_freq_sets():
    print("Загружаю частотные списки Dicta...", file=sys.stderr)
    top500 = set(fetch_json(FREQ_500_URL).keys())
    top20000 = set(fetch_json(FREQ_20000_URL).keys())
    return top500, top20000


def is_loanword(w):
    # заимствования (רובוט, טלפון и т.п.) размечены 5_gemini_pipeline.py
    # без корня и/или с пометкой "заимствование" в extra — мнемоника для
    # слова, которое и так звучит как в русском, бессмысленна
    if w.get("root") in ("—", "-", ""):
        return True
    if "заимств" in (w.get("extra") or "").lower():
        return True
    return False


def select_words(data, top500, top20000, count):
    candidates = {}
    for s in data["sentences"]:
        for w in s["words"]:
            if w.get("pos") in SKIP_POS or is_loanword(w):
                continue
            surface = clean_surface(w["t"])
            if not surface:
                continue
            rarity = 0
            if surface not in top500:
                rarity += 1
            if surface not in top20000:
                rarity += 2
            if rarity == 0:
                continue  # входит в топ-500 — слишком базовое, иллюстрировать не нужно
            entry = candidates.setdefault(w["lemma"], {
                "lemma": w["lemma"], "tr": w["tr"], "pos": w.get("pos"),
                "rarity": rarity, "occurrences": 0, "sentence_id": s["id"],
            })
            entry["occurrences"] += 1
            entry["rarity"] = max(entry["rarity"], rarity)

    ranked = sorted(candidates.values(), key=lambda e: (-e["rarity"], -e["occurrences"]))
    return ranked[:count]


MNEMONIC_SCHEMA = {
    "type": "object",
    "properties": {
        "mnemonic_ru": {"type": "string", "description": "Мнемоника на русском: созвучие + абсурдная запоминающаяся сцена, 2-3 предложения"},
        "cap_tr": {"type": "string", "description": "Транслитерация слова латиницей"},
        "mnemonic_image_prompt": {"type": "string", "description": "Описание сцены НА АНГЛИЙСКОМ для генерации яркой гротескной картинки-мнемоники: комикс-стиль, конкретное действие"},
        "atmosphere_image_prompt": {"type": "string", "description": "Описание НА АНГЛИЙСКОМ спокойной, атмосферной картинки того же момента истории — без гротеска и мнемонического образа, просто передаёт настроение сцены"},
    },
    "required": ["mnemonic_ru", "cap_tr", "mnemonic_image_prompt", "atmosphere_image_prompt"],
}

MNEMONIC_PROMPT = """Слово на иврите: {lemma} ({tr}) — часть речи: {pos}.
Контекст: слово встречается в истории со следующим предложением: "{sentence_context}"

ЗАДАЧА 1 — мнемоника: придумай созвучие с русским словом/фразой +
гротескную, преувеличенную, абсурдную сцену, которая свяжет звучание со
значением. Чем страннее и ярче образ — тем лучше запоминается (эффект
причудливости в мнемотехнике) — избегай спокойных, буквальных сцен.
Опиши эту сцену как mnemonic_image_prompt — НА АНГЛИЙСКОМ, конкретно и
по-комиксовому: чёткое действие, преувеличенная мимика/поза, яркие
плоские цвета, юмор. Не описывай стиль абстрактно — опиши, что именно
нарисовано.

ЗАДАЧА 2 — atmosphere_image_prompt: НА АНГЛИЙСКОМ опиши спокойную,
атмосферную картинку того же момента истории (контекстного предложения
выше) — без гротеска, без мнемонического образа, просто передаёт
настроение и обстановку сцены книги. Мягкая палитра, кинематографичная
композиция, никакого юмора или преувеличения.

ВАЖНО для обоих промтов: никаких надписей, слов, речевых пузырей, букв
на картинке — диффузионные модели рисуют текст нечитаемой кашей, явно
попроси в каждом промте "no text, no speech bubbles, no letters, no
writing anywhere in the image".
"""


def generate_mnemonic(client, model, lemma, tr, pos, sentence_context):
    prompt = MNEMONIC_PROMPT.format(lemma=lemma, tr=tr, pos=pos, sentence_context=sentence_context)
    response = with_retries(lambda: client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MNEMONIC_SCHEMA,
        ),
    ))
    return json.loads(response.text)


def generate_image(cf_account_id, cf_api_token, prompt):
    url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    req = urllib.request.Request(
        url,
        data=json.dumps({"prompt": prompt}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cf_api_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    retries, delay = 4, 5
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req) as r:
                body = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries - 1:
                raise RuntimeError(f"Cloudflare AI HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e
            print(f"  Cloudflare AI перегружен ({e.code}), жду {delay}с и повторяю "
                  f"({attempt+1}/{retries})...", file=sys.stderr)
            time.sleep(delay)
    if not body.get("success", True) and "result" not in body:
        raise RuntimeError(f"Cloudflare AI error: {body}")
    import base64
    return base64.b64decode(body["result"]["image"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book_data", help="уже размеченный book-data.json")
    ap.add_argument("--out", default=None, help="куда сохранить (по умолчанию перезаписать вход)")
    ap.add_argument("--images-dir", default="illustrations", help="папка для картинок")
    ap.add_argument("--count", type=int, default=3, help="сколько слов иллюстрировать")
    ap.add_argument("--model", default="gemini-3.6-flash")
    ap.add_argument("--api-key", default=None, help="Gemini — или GEMINI_API_KEY")
    ap.add_argument("--cf-account-id", default=None, help="или CF_ACCOUNT_ID")
    ap.add_argument("--cf-api-token", default=None, help="или CF_API_TOKEN")
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    cf_account_id = args.cf_account_id or os.environ.get("CF_ACCOUNT_ID")
    cf_api_token = args.cf_api_token or os.environ.get("CF_API_TOKEN")
    if not api_key or not cf_account_id or not cf_api_token:
        print("Нужны: GEMINI_API_KEY, CF_ACCOUNT_ID, CF_API_TOKEN (флагами или переменными окружения).", file=sys.stderr)
        sys.exit(1)

    with open(args.book_data, encoding="utf-8") as f:
        data = json.load(f)

    top500, top20000 = load_freq_sets()
    words = select_words(data, top500, top20000, args.count)
    if not words:
        print("Не нашлось подходящих слов (все входят в топ-500 частотных).", file=sys.stderr)
        sys.exit(0)

    print(f"Выбраны слова: {[w['lemma'] for w in words]}", file=sys.stderr)

    os.makedirs(args.images_dir, exist_ok=True)
    client = genai.Client(api_key=api_key)
    sentences_by_id = {s["id"]: s for s in data["sentences"]}

    for w in words:
        sid = w["sentence_id"]
        sentence = sentences_by_id[sid]
        context = sentence.get("fluent") or sentence.get("literal") or ""
        print(f"[{sid}] {w['lemma']} ({w['tr']}) — мнемоника...", file=sys.stderr)
        m = generate_mnemonic(client, args.model, w["lemma"], w["tr"], w["pos"], context)

        print(f"[{sid}] рисую атмосферную картинку...", file=sys.stderr)
        atmo_bytes = generate_image(cf_account_id, cf_api_token, m["atmosphere_image_prompt"])
        atmo_path = os.path.join(args.images_dir, f"{sid}.jpg")
        with open(atmo_path, "wb") as f:
            f.write(atmo_bytes)

        print(f"[{sid}] рисую картинку-мнемонику...", file=sys.stderr)
        mnem_bytes = generate_image(cf_account_id, cf_api_token, m["mnemonic_image_prompt"])
        mnem_path = os.path.join(args.images_dir, f"{sid}-mnemonic.jpg")
        with open(mnem_path, "wb") as f:
            f.write(mnem_bytes)

        sentence["illustration"] = {
            "word": w["lemma"],
            "tr": w["tr"],
            "caption": w["lemma"],
            "capTr": m["cap_tr"],
            "mnemonic": m["mnemonic_ru"],
            "img": f"{args.images_dir}/{sid}.jpg",
            "imgMnemonic": f"{args.images_dir}/{sid}-mnemonic.jpg",
        }

    out_path = args.out or args.book_data
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nГотово -> {out_path}, иллюстраций добавлено: {len(words)}", file=sys.stderr)


if __name__ == "__main__":
    main()
