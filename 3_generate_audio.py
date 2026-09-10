"""
3_generate_audio.py — озвучивает book-data.json через бесплатный edge-tts,
для книг без готовой аудиокниги (нет Whisper, нет отдельного шага
выравнивания — тайминги слов идут прямо из событий синтеза речи).

Использование:
    python 3_generate_audio.py <input.json> <output.json> <audio_dir> [--voice he-IL-AvriNeural]

Ожидаемый формат input.json: { "sentences": [ { "id": "s1", "words": [ {"t": "..."} , ... ] }, ... ] }
Каждому предложению дописывается "audio": "<audio_dir>/<id>.mp3", каждому
слову — "start"/"end" в секундах (относительно начала клипа этого предложения).

Точечный fallback на gTTS (2026-09-10): у edge-tts (оба голоса,
he-IL-AvriNeural и he-IL-HilaNeural) нашлись отдельные слова, которые он
стабильно ломает независимо от огласовок, контекста или пунктуации —
пойманы вживую на книге zoo-alef: קוֹף читалось "kevof" вместо "kof",
שְׂמֵחָה — с лишним слогом, תּוֹדָה — как "tevada". SSML `<phoneme>` для
явной фонетики бесплатный эндпоинт edge-tts не поддерживает вообще (не
только для иврита — тот же тег на английском тоже отклоняется). На тех же
словах проверили gTTS (движок Google Translate, тоже бесплатный, без
API-ключа) — звучит верно. Он не даёт таймингов по словам (в отличие от
edge-tts), поэтому это НЕ замена основного движка — только точечный обход
для отдельных предложений, отобранных вручную на слух: если в
book-data.json предложение помечено "ttsEngine": "gtts", отдельно
синтезируем его через gTTS без стартов/окончаний слов — читалка уже умеет
работать без них (estimated-timer фоллбэк для несовпадения числа границ,
см. highlightWordAtTime в reader-prototype.html — просто не подсвечивает
слова этого предложения при проигрывании, ничего не ломает).

Заодно нашли по пути: у последнего предложения книги zoo-alef к первому
слову прямой речи (תודה) была приклеена открывающая кавычка без пробела
(осталось от разметки "תודה, אבא!" в исходном тексте) — она целиком летит
в синтезатор как часть слова. build_tts_text() ниже обрезает кавычки на
границах собранного текста перед отправкой в любой из движков (не трогает
сами "t" в данных — это только для звука, отображаемый текст не меняется).

Автоматический список известных проблемных слов (2026-09-10, следом за
находкой выше): те же три слова (קוֹף/שָׂמֵחַ/תּוֹדָה) обнаружились ещё в 7
из 10 остальных опубликованных книг — тот же баг edge-tts, не специфичный
для zoo-alef, просто раньше никто не дослушал именно эти книги до конца.
Починены той же ручной заплаткой (см. коммит "Retroactively fix known
mispronounced words..."). Чтобы это не всплывало заново в каждой новой
книге по одному репорту за раз, tts_known_bad_words.json рядом со
скриптом — общий растущий список лемм с этим багом; ЛЮБОЕ предложение,
содержащее слово с такой леммой, теперь автоматически идёт через gTTS,
даже без ручной пометки "ttsEngine". Дополняй этот файл по мере того, как
будут находиться новые проблемные слова (вживую на слух или через
Whisper-сверку, см. 8_audio_qa.py).
"""

import argparse
import asyncio
import json
import os
import re
import sys

import edge_tts

try:
    from gtts import gTTS
except ImportError:
    gTTS = None

TICKS_PER_SECOND = 10_000_000  # WordBoundary offset/duration units

KNOWN_BAD_WORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_known_bad_words.json")


def load_known_bad_lemmas(path=KNOWN_BAD_WORDS_PATH):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {item["lemma"] for item in json.load(f)}

# Straight, curly open/close, Hebrew gershayim — a quote glued directly to
# a Hebrew letter with no space (e.g. a word opening quoted dialogue) is
# almost always a stray markup artifact, not something a TTS needs to
# read; a properly spaced quote elsewhere in the sentence is left alone.
_STRAY_QUOTE_RE = re.compile(r'(?<=[א-ת])["“”״]|["“”״](?=[א-ת])')


def build_tts_text(words):
    """Собирает текст предложения для синтеза, вычищая кавычки, приклеенные
    без пробела прямо к ивритской букве (см. докстринг модуля) — не трогает
    сами w["t"], те остаются как в данных для отображения."""
    text = " ".join(w["t"] for w in words)
    return _STRAY_QUOTE_RE.sub("", text)


async def synthesize_sentence(text, voice, out_path):
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    boundaries = []
    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                boundaries.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / TICKS_PER_SECOND,
                    "end": (chunk["offset"] + chunk["duration"]) / TICKS_PER_SECOND,
                })
    return boundaries


def synthesize_sentence_gtts(text, out_path):
    """Точечный fallback — см. докстринг модуля. gTTS не даёт таймингов по
    словам, поэтому всегда возвращает пустой список границ (заведомое
    несовпадение с числом слов — reader-prototype.html уже понимает этот
    случай и просто не подсвечивает слова этого предложения при
    проигрывании)."""
    if gTTS is None:
        raise RuntimeError("gTTS не установлен — pip install gtts")
    gTTS(text=text, lang="iw").save(out_path)
    return []


async def main(args):
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    os.makedirs(args.audio_dir, exist_ok=True)
    audio_dir_name = os.path.basename(os.path.normpath(args.audio_dir))
    bad_lemmas = load_known_bad_lemmas()

    mismatches = 0
    auto_gtts = 0
    for i, sentence in enumerate(data["sentences"]):
        words = sentence["words"]
        text = build_tts_text(words)
        clip_name = f"{sentence['id']}.mp3"
        out_path = os.path.join(args.audio_dir, clip_name)

        needs_gtts = sentence.get("ttsEngine") == "gtts" or any(w.get("lemma") in bad_lemmas for w in words)
        if needs_gtts:
            if sentence.get("ttsEngine") != "gtts":
                auto_gtts += 1
                print(f"[i] {sentence['id']}: содержит слово из tts_known_bad_words.json — "
                      f"автоматически через gTTS.", file=sys.stderr)
            sentence["ttsEngine"] = "gtts"
            boundaries = synthesize_sentence_gtts(text, out_path)
        else:
            boundaries = await synthesize_sentence(text, args.voice, out_path)

        if len(boundaries) == len(words):
            for w, b in zip(words, boundaries):
                w["start"] = round(b["start"], 3)
                w["end"] = round(b["end"], 3)
        else:
            mismatches += 1
            print(f"[!] {sentence['id']}: {len(boundaries)} границ слов vs {len(words)} слов — "
                  f"тайминги не проставлены, будет работать через оценочный таймер.", file=sys.stderr)

        sentence["audio"] = f"{audio_dir_name}/{clip_name}"
        print(f"[{i+1}/{len(data['sentences'])}] {sentence['id']} -> {out_path}")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nГотово. Озвучено предложений: {len(data['sentences'])}. "
          f"Расхождений по числу слов: {mismatches}. "
          f"Автоматически на gTTS (известные проблемные слова): {auto_gtts}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("audio_dir")
    parser.add_argument("--voice", default="he-IL-AvriNeural")
    asyncio.run(main(parser.parse_args()))
