"""
7_fade_stager.py — прогоняет уже размеченный book-data.json и проставляет
каждому слову-вхождению "fadeStage" (1/2/3) для режима постепенного
замещения (macaronic fade): слово появляется по-русски -> иврит с
подсказкой -> чистый иврит, по мере накопления повторов в тексте.

Никаких обращений к Gemini/LLM — чисто детерминированный подсчёт по уже
готовому тексту. Список целевых слов ("что фейдим") задаётся заранее,
отдельным JSON-файлом, до генерации истории — не выводится постфактум.

Правило стадии (пропорционально ОБЩЕМУ числу появлений слова в книге, а
не по абсолютному номеру — так слово с 4 повторами и слово с 12 повторами
одинаково честно проходят все три стадии):
    появления 1..ceil(n*STAGE1_FRAC)        -> стадия 1 (русское слово)
    появления ..ceil(n*STAGE2_FRAC)         -> стадия 2 (иврит + подсказка)
    остальные                                -> стадия 3 (чистый иврит)

Если слово встречается реже MIN_REPS раз за всю книгу — фейдить нечего
(нет пространства для повторения), такое слово целиком идёт стадией 2
(иврит сразу показываем, но с подсказкой, а не тратим "слот повторения"
на несуществующий прогресс).

Базовая (уже "известная") лексика — топ-500 частотных слов Dicta — всегда
стадия 3, фейд-режим их не касается вообще, даже если они случайно
совпали с чьей-то леммой в target-words (это защита от опечатки во
входном списке).

Запуск:
    python 7_fade_stager.py book-data.json --target-words targets.json \
        --out book-data-faded.json

Формат targets.json — два типа записей:
    {"lemma": "רָחוֹק", "tr": "далеко"}
        — обычное самостоятельное слово.
    {"phrase": ["גַּן", "חַיּוֹת"], "tr": "зоопарк"}
        — фиксированное словосочетание (смихут/идиома), которое ВСЕГДА
        фейдится/показывается ЦЕЛИКОМ, как одна единица, а не по словам
        отдельно. Список лемм — в порядке, как они идут в тексте. Это
        решает проблему "разорванного" смихута (см. CHANGELOG про
        חַיָּה/גַּן חַיּוֹת) не эвристикой, а на этапе курации: если слово
        обычно живёт внутри устойчивого сочетания — оформи его как
        phrase, а не как отдельный lemma.
"""

import argparse
import json
import math
import sys

from illustrate_book import (
    strip_nikud,
    clean_surface,
    fetch_json,
    FREQ_500_URL,
)

STAGE1_FRAC = 0.34
STAGE2_FRAC = 0.67
MIN_REPS = 3


def load_targets(path):
    data = json.load(open(path, encoding="utf-8"))
    lemma_targets = {}
    phrases = []
    for item in data:
        if "phrase" in item:
            phrases.append({"lemmas": item["phrase"], "tr": item.get("tr")})
        else:
            lemma_targets[item["lemma"]] = item.get("tr")
    return lemma_targets, phrases


def stage_for_occurrence(k, n):
    if n < MIN_REPS:
        return 2
    if k <= math.ceil(n * STAGE1_FRAC):
        return 1
    if k <= math.ceil(n * STAGE2_FRAC):
        return 2
    return 3


def find_phrase_matches(words, phrases):
    """Жадно, слева направо, без пересечений: возвращает список
    (start_idx, end_idx_excl, phrase) для каждого найденного в
    предложении вхождения одной из целевых фраз (по последовательности
    лемм)."""
    matches = []
    i = 0
    while i < len(words):
        matched = None
        for phrase in phrases:
            n = len(phrase["lemmas"])
            if i + n > len(words):
                continue
            if all(words[i + j]["lemma"] == phrase["lemmas"][j] for j in range(n)):
                matched = phrase
                break
        if matched:
            matches.append((i, i + len(matched["lemmas"]), matched))
            i += len(matched["lemmas"])
        else:
            i += 1
    return matches


def stage_book(data, lemma_targets, phrases, top500):
    # первый проход: находим все вхождения фраз + считаем общее число
    # появлений каждой целевой единицы (фразы или самостоятельной леммы)
    sentence_phrase_matches = []
    phrase_totals = {}
    lemma_totals = {}

    for s in data["sentences"]:
        matches = find_phrase_matches(s["words"], phrases)
        sentence_phrase_matches.append(matches)
        covered = set()
        for start, end, phrase in matches:
            key = tuple(phrase["lemmas"])
            phrase_totals[key] = phrase_totals.get(key, 0) + 1
            covered.update(range(start, end))
        for idx, w in enumerate(s["words"]):
            if idx in covered:
                continue
            lemma = w["lemma"]
            if lemma in lemma_targets and clean_surface(w["t"]) not in top500:
                lemma_totals[lemma] = lemma_totals.get(lemma, 0) + 1

    # второй проход: проставляем стадию по номеру текущего появления
    phrase_counters = {}
    lemma_counters = {}
    stats = {}
    stats.update({f"phrase:{' '.join(k)}": {"total": n, "stage1": 0, "stage2": 0, "stage3": 0}
                   for k, n in phrase_totals.items()})
    stats.update({lemma: {"total": n, "stage1": 0, "stage2": 0, "stage3": 0}
                   for lemma, n in lemma_totals.items()})

    for s, matches in zip(data["sentences"], sentence_phrase_matches):
        covered = set()
        for start, end, phrase in matches:
            key = tuple(phrase["lemmas"])
            phrase_counters[key] = phrase_counters.get(key, 0) + 1
            k = phrase_counters[key]
            n = phrase_totals[key]
            stage = stage_for_occurrence(k, n)
            group_id = f"{s['id']}:{start}"
            for j, idx in enumerate(range(start, end)):
                w = s["words"][idx]
                w["fadeStage"] = stage
                w["fadeGroup"] = group_id
                w["fadeGroupRole"] = "first" if j == 0 else "rest"
                if stage == 1 and j == 0 and phrase["tr"]:
                    w["fadeTr"] = phrase["tr"]
                if stage == 2 and j == 0 and phrase["tr"]:
                    w["fadeHint"] = phrase["tr"]
                covered.add(idx)
            stats[f"phrase:{' '.join(key)}"][f"stage{stage}"] += 1

        for idx, w in enumerate(s["words"]):
            if idx in covered:
                continue
            lemma = w["lemma"]
            if lemma not in lemma_totals or clean_surface(w["t"]) in top500:
                w["fadeStage"] = 3
                continue
            lemma_counters[lemma] = lemma_counters.get(lemma, 0) + 1
            k = lemma_counters[lemma]
            n = lemma_totals[lemma]
            stage = stage_for_occurrence(k, n)
            w["fadeStage"] = stage
            if stage == 1 and lemma_targets.get(lemma):
                w["fadeTr"] = lemma_targets[lemma]
            stats[lemma][f"stage{stage}"] += 1

    data["mode"] = "fade"
    data["fadeTargetWords"] = sorted(lemma_totals.keys())
    data["fadeTargetPhrases"] = [" ".join(k) for k in phrase_totals.keys()]
    return stats


def print_curve(data, window=5):
    print("\n--- Кривая % иврита по окнам предложений ---", file=sys.stderr)
    sentences = data["sentences"]
    for i in range(0, len(sentences), window):
        chunk = sentences[i:i + window]
        total = sum(len(s["words"]) for s in chunk)
        hebrew = sum(1 for s in chunk for w in s["words"] if w["fadeStage"] != 1)
        pct = round(100 * hebrew / total) if total else 0
        bar = "#" * (pct // 5)
        print(f"  s{i+1:>4}-s{i+len(chunk):<4} {pct:>3}% {bar}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book_data")
    ap.add_argument("--target-words", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    data = json.load(open(args.book_data, encoding="utf-8"))
    lemma_targets, phrases = load_targets(args.target_words)

    print("Загружаю частотный список Dicta...", file=sys.stderr)
    top500 = set(fetch_json(FREQ_500_URL).keys())

    stats = stage_book(data, lemma_targets, phrases, top500)

    print("\n--- Статистика по целевым словам/фразам ---", file=sys.stderr)
    for key, st in sorted(stats.items(), key=lambda kv: -kv[1]["total"]):
        print(f"  {key}: всего {st['total']}, "
              f"стадия1={st['stage1']} стадия2={st['stage2']} стадия3={st['stage3']}",
              file=sys.stderr)

    missing_lemmas = set(lemma_targets) - {k for k in stats if not k.startswith("phrase:")}
    missing_phrases = [" ".join(p["lemmas"]) for p in phrases
                        if f"phrase:{' '.join(p['lemmas'])}" not in stats]
    if missing_lemmas or missing_phrases:
        print(f"\n!! Целевые единицы не найдены в тексте вообще: "
              f"слова={sorted(missing_lemmas)}, фразы={missing_phrases}",
              file=sys.stderr)

    print_curve(data)

    out_path = args.out or args.book_data.replace(".json", "-faded.json")
    json.dump(data, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nСохранено -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
