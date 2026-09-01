"""
illustrate_book.py — добавляет ровно ОДНУ иллюстрацию на каждую страницу
(PAGE_SIZE=20 предложений, как в reader-prototype.html) уже размеченного
book-data.json: слово выбирается по РЕАЛЬНОЙ частотности (списки Dicta,
top_500/top_20000) в пределах этой страницы, а не наугад; заимствования
(רובוט и т.п., которые и так звучат как в русском) исключены из отбора.

На каждую иллюстрацию — две картинки:
  - атмосферная (по умолчанию видна читателю): строго соответствует
    происходящему в предложении, единый стиль на всю книгу — стиль
    задаётся фиксированной строкой в коде (ATMOSPHERE_STYLE), а не
    отдельным решением модели на каждое слово, иначе картинки книги
    не будут похожи друг на друга.
  - мнемоническая (видна при включённом тумблере "Мнемоника"): созвучие
    + абсурдная гротескная сценка, свобода образа не ограничена сюжетом.

Мнемоника и обе картинки пишутся/рисуются через Gemini (текст) и
Cloudflare Workers AI (@cf/black-forest-labs/flux-1-schnell, бесплатно
в рамках дневного лимита нейронов) — SVG для этого не годится, он не
умеет достоверно рисовать конкретное действие/сценку.

Установка:
    pip install google-genai requests --break-system-packages

Нужны:
    GEMINI_API_KEY       — https://aistudio.google.com/apikey
    CF_API_TOKEN          — Cloudflare API-токен с правом Workers AI
    CF_ACCOUNT_ID         — id аккаунта Cloudflare

Запуск:
    python illustrate_book.py book-data.json --out book-data-illustrated.json \
        --images-dir illustrations_mybook
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

# должно совпадать с PAGE_SIZE в reader-prototype.html — одна страница
# книги = одна иллюстрация
PAGE_SIZE = 20

# фиксированный стиль атмосферных картинок — единый на всю книгу.
# Дописывается кодом к промту, а не оставляется на усмотрение модели,
# иначе от картинки к картинке стиль будет плыть.
ATMOSPHERE_STYLE = (
    "Art style: soft cinematic photography, warm natural lighting, shallow "
    "depth of field, muted realistic color grading, no text, no speech "
    "bubbles, no letters, no writing anywhere in the image."
)

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


def score_candidates(sentences, top500, top20000):
    candidates = {}
    for s in sentences:
        for w in s["words"]:
            if w.get("pos") in SKIP_POS or is_loanword(w):
                continue
            surface = clean_surface(w["t"])
            if not surface:
                continue
            if surface in top500:
                # входит в топ-500 — надёжный сигнал "базовое слово", пропускаем
                # его безусловно. top_20000_partial.json (файл сам по себе
                # неполный) иногда не содержит слово, которое ЕСТЬ в топ-500 —
                # такое расхождение раньше давало ложную "редкость" базовым
                # словам вроде "עושה" (делает).
                continue
            rarity = 2 if surface not in top20000 else 1
            # предпочитаем конкретные существительные — их проще изобразить
            # осмысленно, чем абстрактный глагол вроде "делать"/"быть"
            if w.get("pos") == "сущ.":
                rarity += 1
            entry = candidates.setdefault(w["lemma"], {
                "lemma": w["lemma"], "tr": w["tr"], "pos": w.get("pos"),
                "rarity": rarity, "occurrences": 0, "sentence_id": s["id"],
            })
            entry["occurrences"] += 1
            entry["rarity"] = max(entry["rarity"], rarity)
    return candidates


def select_words_per_page(data, top500, top20000):
    """Одна иллюстрация на страницу (PAGE_SIZE предложений) — не наугад,
    самое редкое+значимое слово именно в пределах этой страницы."""
    sentences = data["sentences"]
    selected = []
    for start in range(0, len(sentences), PAGE_SIZE):
        page_sentences = sentences[start:start + PAGE_SIZE]
        candidates = score_candidates(page_sentences, top500, top20000)
        if not candidates:
            continue  # на этой странице не нашлось ни одного подходящего слова
        best = max(candidates.values(), key=lambda e: (e["rarity"], e["occurrences"]))
        selected.append(best)
    return selected


MNEMONIC_SCHEMA = {
    "type": "object",
    "properties": {
        "mnemonic_ru": {"type": "string", "description": "Мнемоника на русском: созвучие + абсурдная запоминающаяся сцена, 2-3 предложения"},
        "cap_tr": {"type": "string", "description": "Транслитерация слова латиницей"},
        "mnemonic_image_prompt": {"type": "string", "description": "Описание сцены НА АНГЛИЙСКОМ для генерации яркой гротескной картинки-мнемоники: комикс-стиль, конкретное действие"},
        "atmosphere_image_prompt": {"type": "string", "description": "ТОЛЬКО описание содержимого кадра НА АНГЛИЙСКОМ — те же персонажи/место/действие, что буквально в контекстном предложении, без художественных вольностей. Про стиль/освещение/палитру НЕ писать — это добавляется отдельно."},
    },
    "required": ["mnemonic_ru", "cap_tr", "mnemonic_image_prompt", "atmosphere_image_prompt"],
}

MNEMONIC_PROMPT = """{book_summary_block}Слово на иврите: {lemma} ({tr}) — часть речи: {pos}.
Контекст: слово встречается в истории со следующим предложением: "{sentence_context}"

ЗАДАЧА 1 — мнемоника: придумай созвучие с РЕАЛЬНО СУЩЕСТВУЮЩИМ русским
словом или естественной фразой (метод "слово-связка" в мнемотехнике —
работает только если связка сама по себе узнаваема и знакома, а не
выдуманный набор звуков вроде "ласоут"; если из звучания ивритского
слова честно не складывается ни одно настоящее русское слово — возьми
самую близкую по звучанию ПОСЛЕДОВАТЕЛЬНОСТЬ из 2-3 настоящих русских
слов). Затем — гротескную, преувеличенную, абсурдную сцену, которая
свяжет эту связку со значением. Чем страннее и ярче образ — тем лучше
запоминается (эффект причудливости в мнемотехнике) — избегай спокойных,
буквальных сцен. Эта картинка НЕ обязана соответствовать сюжету книги —
она про звучание слова, а не про историю.
Опиши эту сцену как mnemonic_image_prompt — НА АНГЛИЙСКОМ, конкретно и
по-комиксовому: чёткое действие, преувеличенная мимика/поза, яркие
плоские цвета, юмор. Не описывай стиль абстрактно — опиши, что именно
нарисовано.

ЗАДАЧА 2 — atmosphere_image_prompt: опиши НА АНГЛИЙСКОМ, что БУКВАЛЬНО
происходит в контекстном предложении выше — используя общий контекст
книги (если он дан выше), чтобы верно передать, КТО именно действует
(например, если "он" — это конкретный персонаж книги, а не случайный
человек) и ГДЕ это происходит. Никаких выдуманных деталей, никакой
связи со звучанием слова (это не мнемоника, а иллюстрация сюжета).
Соответствие сюжету книги должно быть стопроцентным — это единственная
цель атмосферной картинки, если персонаж не человек (робот, животное,
существо) — так и опиши, не заменяй его человеком. НЕ пиши про стиль,
освещение или цветовую палитру — это добавляется отдельно и одинаково
для всех картинок книги, чтобы они были в одной стилистике.

ВАЖНО для обоих промтов: никаких надписей, слов, речевых пузырей, букв
на картинке — диффузионные модели рисуют текст нечитаемой кашей, явно
попроси в каждом промте "no text, no speech bubbles, no letters, no
writing anywhere in the image".
"""


def generate_mnemonic(client, model, lemma, tr, pos, sentence_context, book_summary=None):
    book_summary_block = f"Общий сюжет книги (для контекста персонажей): {book_summary}\n\n" if book_summary else ""
    prompt = MNEMONIC_PROMPT.format(
        book_summary_block=book_summary_block,
        lemma=lemma, tr=tr, pos=pos, sentence_context=sentence_context,
    )
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
    ap.add_argument("--summary", default=None,
                     help="краткое описание сюжета/персонажей книги (из .meta.json от "
                          "2_generate_story.py) — без него атмосферные картинки не будут "
                          "знать, кто такой 'он' в предложении, и могут нарисовать не того персонажа")
    ap.add_argument("--model", default="gemini-3.6-flash")
    ap.add_argument("--api-key", default=None, help="Gemini — или GEMINI_API_KEY")
    ap.add_argument("--cf-account-id", default=None, help="или CF_ACCOUNT_ID")
    ap.add_argument("--cf-api-token", default=None, help="или CF_API_TOKEN")
    ap.add_argument("--resume", action="store_true",
                     help="не стирать уже готовые иллюстрации — пропустить страницы, "
                          "где выбранное слово совпадает с уже сохранённым, и дописать "
                          "только недостающие. Нужно для больших книг, где дневной квоты "
                          "не хватает на все страницы за один прогон (без этого флага "
                          "скрипт по умолчанию стирает все иллюстрации и рисует заново)")
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    cf_account_id = args.cf_account_id or os.environ.get("CF_ACCOUNT_ID")
    cf_api_token = args.cf_api_token or os.environ.get("CF_API_TOKEN")
    if not api_key or not cf_account_id or not cf_api_token:
        print("Нужны: GEMINI_API_KEY, CF_ACCOUNT_ID, CF_API_TOKEN (флагами или переменными окружения).", file=sys.stderr)
        sys.exit(1)

    with open(args.book_data, encoding="utf-8") as f:
        data = json.load(f)

    if not args.resume:
        # чистим иллюстрации от прошлых прогонов — иначе при повторном запуске
        # (например, после правки промта) старые картинки на страницах, чьё
        # слово-кандидат сменилось, остаются висеть рядом с новыми.
        # С --resume, наоборот, сознательно НЕ стираем — см. ниже.
        for s in data["sentences"]:
            s.pop("illustration", None)

    top500, top20000 = load_freq_sets()
    words = select_words_per_page(data, top500, top20000)
    if not words:
        print("Не нашлось подходящих слов (все входят в топ-500 частотных).", file=sys.stderr)
        sys.exit(0)

    print(f"Выбраны слова: {[w['lemma'] for w in words]}", file=sys.stderr)

    os.makedirs(args.images_dir, exist_ok=True)
    client = genai.Client(api_key=api_key)
    sentences_by_id = {s["id"]: s for s in data["sentences"]}
    out_path = args.out or args.book_data

    for w in words:
        sid = w["sentence_id"]
        sentence = sentences_by_id[sid]

        existing = sentence.get("illustration")
        if args.resume and existing and existing.get("word") == w["lemma"]:
            print(f"[{sid}] {w['lemma']} — уже готово, пропускаю", file=sys.stderr)
            continue

        context = sentence.get("fluent") or sentence.get("literal") or ""
        print(f"[{sid}] {w['lemma']} ({w['tr']}) — мнемоника...", file=sys.stderr)
        m = generate_mnemonic(client, args.model, w["lemma"], w["tr"], w["pos"], context, args.summary)

        print(f"[{sid}] рисую атмосферную картинку...", file=sys.stderr)
        atmo_prompt = f"{m['atmosphere_image_prompt']} {ATMOSPHERE_STYLE}"
        atmo_bytes = generate_image(cf_account_id, cf_api_token, atmo_prompt)
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

        # сохраняем после КАЖДОЙ страницы, а не только в конце — если прервётся
        # (кончится дневная квота на середине книги), ничего не потеряется и
        # --resume сможет продолжить с этого места
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nГотово -> {out_path}, иллюстраций добавлено: {len(words)}", file=sys.stderr)


if __name__ == "__main__":
    main()
