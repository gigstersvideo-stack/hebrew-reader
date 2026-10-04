"""Применяет сплошную смысловую вычитку книг (2026-10-03).

    python tools/apply_book_proof.py <dir>                    # сухой прогон
    python tools/apply_book_proof.py <dir> --write --revoice  # записать + переозвучить

<dir>/<slug>.json — список ТОЛЬКО исправленных предложений:
    [{"id": "s12", "words": [{"t", "lemma", "pos", "tr", "extra", "root"}, ...],   # необязательно
      "literal": "...", "fluent": "...", "why": "..."}]
Если words нет — правятся только переводы. Если последовательность слов (t) не
изменилась — правятся только поля слов, тайминги аудио сохраняются. Если текст
изменился — слова заменяются целиком и предложение переозвучивается (тот же
движок и правила, что apply_review.py: подмены, Azure IPA, gTTS-исключения).
"""
import argparse
import asyncio
import json
import os
import sys
import unicodedata

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import apply_review as AR  # noqa: E402

nfc = lambda s: unicodedata.normalize("NFC", s or "")
WORD_KEYS = ("t", "lemma", "pos", "tr", "extra", "root")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--revoice", action="store_true")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    m = AR.load_audio_tool() if a.revoice else None
    tot = {"text": 0, "fields": 0, "tr_only": 0, "missing": [], "bad": []}
    for fn in sorted(os.listdir(a.dir)):
        if not fn.endswith(".json"):
            continue
        slug = fn[:-5]
        if a.only and slug not in a.only:
            continue
        path = os.path.join(ROOT, "books", slug, "book-data.json")
        if not os.path.exists(path):
            tot["missing"].append((slug, "нет книги"))
            continue
        data = AR.load(path)
        by_id = {s["id"]: s for s in data["sentences"]}
        fixes = AR.load(os.path.join(a.dir, fn))
        revoice, n = set(), {"text": 0, "fields": 0, "tr_only": 0}
        for fx in fixes:
            s = by_id.get(fx["id"])
            if s is None:
                tot["missing"].append((slug, fx["id"]))
                continue
            for k in ("literal", "fluent"):
                if fx.get(k):
                    s[k] = fx[k]
            if not fx.get("words"):
                n["tr_only"] += 1
                continue
            new = []
            for w in fx["words"]:
                if not w.get("t") or not w.get("tr"):
                    tot["bad"].append((slug, fx["id"], "пустое t/tr"))
                nw = {k: (nfc(w[k]) if k in ("t", "lemma") else w[k]) for k in WORD_KEYS if k in w}
                new.append(nw)
            old_t = [nfc(w["t"]) for w in s["words"]]
            if [w["t"] for w in new] == old_t:
                for ow, nw in zip(s["words"], new):
                    for k, v in nw.items():
                        ow[k] = v
                n["fields"] += 1
            else:
                s["words"] = new
                revoice.add(s["id"])
                n["text"] += 1
        print(f"{slug:26s} текст {n['text']:3d}  поля слов {n['fields']:3d}  только перевод {n['tr_only']:3d}")
        for k in n:
            tot[k] += n[k]
        if a.write:
            if a.revoice and revoice:
                asyncio.run(AR.revoice(data, revoice, slug, m))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
    print(f"\nИтого: текст изменён {tot['text']}, поля слов {tot['fields']}, только перевод {tot['tr_only']}")
    for x in tot["missing"][:20]:
        print("  НЕТ", *x)
    for x in tot["bad"][:20]:
        print("  [!]", *x)
    if not a.write:
        print("Сухой прогон — ничего не записано.")


if __name__ == "__main__":
    main()
