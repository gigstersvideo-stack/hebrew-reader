"""
8_audio_qa.py — прогоняет уже сгенерированные аудиоклипы книги обратно
через локальный Whisper (офлайн, бесплатно, без API-ключа) и сверяет
распознанный текст с ожидаемым — ловит случаи вроде "קוֹף" -> "kevof" из
tts_known_bad_words.json, но БЕЗ необходимости заранее знать, какое
слово сломано. Не чинит автоматически — только помечает подозрительные
предложения для проверки на слух, автоматика тут слишком рискованна
(Whisper и сам иногда ошибается на редких словах/именах собственных, это
не всегда значит, что виноват TTS).

Метрика сравнения — доля букв, не совпавших между ожидаемым текстом (без
огласовок) и тем, что распознал Whisper, после нормализации (посимвольный
diff через difflib, а не point-blank равенство строк — Whisper почти
никогда не транскрибирует идеально, даже когда TTS произносит всё верно:
он путает похожие по звучанию буквы алеф/аин, финальные формы и т.п. —
поэтому порог REVIEW_THRESHOLD подобран так, чтобы отсекать грубые
расхождения ("kevof" вместо "kof" даёт совсем другую букву в середине), а
не придираться к мелочи).

Запуск:
    python 8_audio_qa.py book-data.json audio_dir [--model small] [--threshold 0.35]

Модель Whisper по умолчанию "small" — компромисс скорости/точности для
иврита на CPU; "base" быстрее, но чаще путает похожие буквы у РЕАЛЬНО
верно озвученных слов (больше ложных срабатываний), "medium" точнее, но
существенно медленнее на CPU — на весь прогон книги может уйти много
времени, обычно не стоит того при первом проходе.
"""

import argparse
import difflib
import json
import os
import re
import sys

NIKUD_RE = re.compile(r"[֑-ׇ]")
NON_HEBREW_RE = re.compile(r"[^א-ת]")


def strip_to_letters(s):
    return NON_HEBREW_RE.sub("", NIKUD_RE.sub("", s))


def char_diff_ratio(expected, got):
    if not expected:
        return 0.0
    sm = difflib.SequenceMatcher(None, expected, got, autojunk=False)
    return 1.0 - sm.ratio()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book_data")
    ap.add_argument("audio_dir")
    ap.add_argument("--model", default="small")
    ap.add_argument("--threshold", type=float, default=0.35,
                     help="доля несовпадения символов, выше которой предложение попадает в отчёт")
    ap.add_argument("--out", default=None, help="куда сохранить отчёт (по умолчанию <book_data>.audio-qa.json)")
    args = ap.parse_args()

    try:
        import whisper
    except ImportError:
        print("Нужно: pip install openai-whisper (и ffmpeg в PATH).", file=sys.stderr)
        sys.exit(1)

    data = json.load(open(args.book_data, encoding="utf-8"))
    print(f"Загружаю модель Whisper ({args.model})...", file=sys.stderr)
    model = whisper.load_model(args.model)

    suspects = []
    total = len(data["sentences"])
    for i, s in enumerate(data["sentences"]):
        clip_path = os.path.join(os.path.dirname(args.book_data) or ".", s.get("audio", ""))
        if not s.get("audio") or not os.path.exists(clip_path):
            continue
        expected = strip_to_letters("".join(w["t"] for w in s["words"]))
        result = model.transcribe(clip_path, language="he", fp16=False)
        got = strip_to_letters(result["text"])
        ratio = char_diff_ratio(expected, got)
        flag = " !!" if ratio > args.threshold else ""
        print(f"[{i+1}/{total}] {s['id']}: несовпадение {ratio:.2f}{flag}", file=sys.stderr)
        if ratio > args.threshold:
            suspects.append({
                "id": s["id"],
                "ratio": round(ratio, 3),
                "expected": "".join(w["t"] for w in s["words"]),
                "whisper_heard": result["text"],
                "audio": s["audio"],
            })

    out_path = args.out or args.book_data.replace(".json", ".audio-qa.json")
    json.dump({"threshold": args.threshold, "model": args.model, "suspects": suspects},
              open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\nГотово. Подозрительных предложений: {len(suspects)} из {total}.", file=sys.stderr)
    if suspects:
        print("Список ID для прослушивания вручную:", ", ".join(s["id"] for s in suspects), file=sys.stderr)
    print(f"Полный отчёт -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
