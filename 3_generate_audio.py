"""
3_generate_audio.py — озвучивает book-data.json через бесплатный edge-tts,
для книг без готовой аудиокниги (нет Whisper, нет отдельного шага
выравнивания — тайминги слов идут прямо из событий синтеза речи).

Использование:
    python 3_generate_audio.py <input.json> <output.json> <audio_dir> [--voice he-IL-AvriNeural]

Ожидаемый формат input.json: { "sentences": [ { "id": "s1", "words": [ {"t": "..."} , ... ] }, ... ] }
Каждому предложению дописывается "audio": "<audio_dir>/<id>.mp3", каждому
слову — "start"/"end" в секундах (относительно начала клипа этого предложения).
"""

import argparse
import asyncio
import json
import os
import sys

import edge_tts

TICKS_PER_SECOND = 10_000_000  # WordBoundary offset/duration units


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


async def main(args):
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    os.makedirs(args.audio_dir, exist_ok=True)
    audio_dir_name = os.path.basename(os.path.normpath(args.audio_dir))

    mismatches = 0
    for i, sentence in enumerate(data["sentences"]):
        words = sentence["words"]
        text = " ".join(w["t"] for w in words)
        clip_name = f"{sentence['id']}.mp3"
        out_path = os.path.join(args.audio_dir, clip_name)

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
          f"Расхождений по числу слов: {mismatches}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("audio_dir")
    parser.add_argument("--voice", default="he-IL-AvriNeural")
    asyncio.run(main(parser.parse_args()))
