"""Автоматическая проверка произношения озвучки — без прослушивания.

    python tools/pronunciation_check.py books/quest-gimel/book-data.json [--sids s78 s79]
    python tools/pronunciation_check.py --all [--min-score 0.5]

Как работает:
  1. Ожидаемое произношение каждого слова выводится из огласовки правилами
     современного иврита (g2p ниже): дагеш в בגדכפת, точка шин/син, гласные,
     хатафы, вставной патах, матрес. Шва и непроизносимые ʔ/h считаются
     необязательными — в живой речи они часто выпадают.
  2. Модель wav2vec2-xlsr-53-espeak-cv-ft (локально, GPU) слушает mp3
     предложения и выдаёт фонемы IPA — то, что TTS РЕАЛЬНО произнёс.
  3. Ожидаемая цепочка всего предложения выравнивается с услышанной
     (взвешенный Левенштейн с необязательными звуками), ошибки
     раскладываются по словам. Слово с ошибкой в гласной или лишним слогом
     попадает в отчёт со счётом уверенности.

Проверено на известных случаях, найденных владельцем на слух (2026-10-03):
  כָּל «каль» (до v1.99.1) — слышится `kal`, после — `kol`;
  עָלַיִךְ / עֲבוּרֵךְ / אֵלַיִךְ / אוֹתָךְ с лишним «-ха» (до v1.99.5) — `…aɪxa`,
  после — `…aɪx`.
Ограничение: ударение модель почти не различает — ошибки ТОЛЬКО ударения
(тот же набор звуков) этим способом не ловятся.
"""
import argparse
import glob
import json
import os
import re
import sys
import unicodedata

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ---- огласовка -> фонемы ---------------------------------------------------

SHVA, HATAF_SEGOL, HATAF_PATAH, HATAF_QAMATS = "\u05B0", "\u05B1", "\u05B2", "\u05B3"
HIRIQ, TSERE, SEGOL, PATAH, QAMATS, HOLAM, HOLAM_HASER, QUBUTS = \
    "\u05B4", "\u05B5", "\u05B6", "\u05B7", "\u05B8", "\u05B9", "\u05BA", "\u05BB"
DAGESH, SHIN_DOT, SIN_DOT, QAMATS_QATAN = "\u05BC", "\u05C1", "\u05C2", "\u05C7"
VOWEL_OF = {HIRIQ: "i", TSERE: "e", SEGOL: "e", PATAH: "a", QAMATS: "a", HOLAM: "o", HOLAM_HASER: "o",
            QUBUTS: "u", HATAF_SEGOL: "e", HATAF_PATAH: "a", HATAF_QAMATS: "o", QAMATS_QATAN: "o"}
CONS = {"א": "ʔ", "ב": "v", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z", "ח": "x", "ט": "t", "י": "j",
        "כ": "x", "ך": "x", "ל": "l", "מ": "m", "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "ʔ", "פ": "f",
        "ף": "f", "צ": "ts", "ץ": "ts", "ק": "k", "ר": "r", "ש": "ʃ", "ת": "t"}
HARD = {"ב": "b", "כ": "k", "ך": "k", "פ": "p", "ף": "p"}
OPTIONAL = {"ʔ", "h", "ə"}  # могут выпасть в речи без ошибки


def clusters(word):
    out = []
    for ch in unicodedata.normalize("NFC", word):
        if "\u05D0" <= ch <= "\u05EA":
            out.append([ch, ""])
        elif out and "\u0591" <= ch <= "\u05C7":
            out[-1][1] += ch
    return out


def g2p(word):
    """Список фонем слова; необязательные помечены как ('ə') / ('ʔ') / ('h')."""
    cl = clusters(word)
    ph = []
    n = len(cl)
    for i, (L, m) in enumerate(cl):
        vowels = [VOWEL_OF[c] for c in m if c in VOWEL_OF]
        last = i == n - 1
        # вав: шурук / холам малэ / согласная
        if L == "ו" and not vowels and DAGESH in m and (i == 0 or not any(c in VOWEL_OF for c in cl[i - 1][1])):
            ph.append("u"); continue
        if L == "ו" and (HOLAM in m) and not [c for c in m if c in VOWEL_OF and c != HOLAM]:
            if i > 0 and not any(c in VOWEL_OF for c in cl[i - 1][1]):
                ph.append("o"); continue
        # йуд-матрес после и/е: молчит
        if L == "י" and not m and i > 0 and any(c in (HIRIQ, TSERE, SEGOL) for c in cl[i - 1][1]):
            continue
        # вав/йуд без знака рядом с такой же — удвоение ктив мале
        if L in "וי" and not m and ((i + 1 < n and cl[i + 1][0] == L) or (i > 0 and cl[i - 1][0] == L)):
            continue
        # конечная ה без мапика и א без огласовки — молчат
        if L == "ה" and last and DAGESH not in m and not vowels:
            continue
        if L == "א" and not vowels:
            continue
        # согласная
        if L == "ש":
            c = "s" if SIN_DOT in m else "ʃ"
        elif L in HARD and DAGESH in m:
            c = HARD[L]
        else:
            c = CONS[L]
        # вставной патах под конечными ח/ע/ה(мапик): гласная ПЕРЕД согласной
        if last and PATAH in m and L in "חעה":
            ph.append("a"); ph.append(c); continue
        ph.append(c)
        if vowels:
            v = vowels[0]
            # камац катан: в ктив мале «о» обычно пишется через вав, но не в
            # семье כָּל (כָּל/בְּכָל/מִכָּל…) и не перед хатаф-камацем (צָהֳרַיִים)
            if QAMATS in m and v == "a" and (
                    (L == "כ" and i + 1 < n and cl[i + 1][0] == "ל" and i + 2 == n
                     and all(c[0] in "ובלמשכה" for c in cl[:i]))  # только כָּל с приставками, не מִיכָל
                    or (i + 1 < n and HATAF_QAMATS in cl[i + 1][1])):
                v = "o"
            ph.append(v)
        elif SHVA in m and not last:
            ph.append("ə")  # шва: в речи то «е», то ничего — необязательная
        # конечная ךְ с камацем (לְךָ) уже дала «а»; ךְ со шва — без гласной
    # холам на предыдущей букве перед вав-согласной уже учтён; убираем ʔ в начале
    return ph


# ---- услышанное -> та же азбука ----------------------------------------------

def norm_heard(p):
    p = p.replace("ː", "").replace("ʰ", "")
    table = {"ɛ": "e", "ɪ": "i", "ɔ": "o", "ʊ": "u", "ɐ": "a", "ʌ": "a", "ɑ": "a", "æ": "a", "ə": "ə",
             "ʁ": "r", "ɾ": "r", "ɹ": "r", "χ": "x", "ç": "x", "ɡ": "g", "β": "v", "w": "v", "ɣ": "g",
             "eɪ": "ej", "aɪ": "aj", "oʊ": "o", "aʊ": "av", "ɑ̃": "a", "ɛ̃": "e", "ɔ̃": "o", "y": "i",
             "ʒ": "z", "dʒ": "dz", "tʃ": "ts", "θ": "t", "ð": "d", "ɕ": "ʃ", "ʎ": "l", "ɲ": "n", "ŋ": "n"}
    if p in table:
        return list(table[p]) if p in ("eɪ", "aɪ", "aʊ", "dʒ") else [table[p]]
    if p == "ts":
        return ["ts"]
    return [p] if len(p) == 1 or p == "ts" else list(p)


VOW = set("aeiou")


def sub_cost(a, b):
    if a == b:
        return 0.0
    if a in VOW and b in VOW:
        return 1.0  # главное, что ищем: не та гласная
    if (a, b) in {("v", "b"), ("b", "v"), ("x", "k"), ("k", "x"), ("f", "p"), ("p", "f"), ("ts", "s"), ("s", "ts"),
                  ("ʃ", "s"), ("s", "ʃ"), ("j", "i"), ("i", "j")}:
        return 0.6
    return 0.9


def align(exp, heard):
    """exp: [(фонема, номер слова)], heard: [фонема]. Возвращает ошибки по словам."""
    n, m = len(exp), len(heard)
    INF = 1e9
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    B = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best, bp = INF, None
            if i > 0:  # пропуск ожидаемого
                c = 0.15 if exp[i - 1][0] in OPTIONAL else 1.0
                if D[i - 1][j] + c < best:
                    best, bp = D[i - 1][j] + c, "del"
            if j > 0:  # лишний услышанный звук
                c = 0.3 if heard[j - 1] in ("ʔ", "h", "ə") else (0.8 if heard[j - 1] in VOW else 0.7)
                if D[i][j - 1] + c < best:
                    best, bp = D[i][j - 1] + c, "ins"
            if i > 0 and j > 0:
                e = exp[i - 1][0]
                c = 0.0 if (e == "ə" and heard[j - 1] in ("e", "ə", "i")) else sub_cost(e, heard[j - 1])
                if D[i - 1][j - 1] + c < best:
                    best, bp = D[i - 1][j - 1] + c, "sub"
            D[i][j], B[i][j] = best, bp
    # обратный проход: ошибки на слово
    errs = {}
    i, j = n, m
    last_word = exp[-1][1] if exp else 0
    while i > 0 or j > 0:
        bp = B[i][j]
        if bp == "sub":
            e, h, w = exp[i - 1][0], heard[j - 1], exp[i - 1][1]
            c = 0.0 if (e == "ə" and h in ("e", "ə", "i")) else sub_cost(e, h)
            if c:
                errs.setdefault(w, []).append(("sub", e, h, c))
            last_word = w
            i, j = i - 1, j - 1
        elif bp == "del":
            e, w = exp[i - 1][0], exp[i - 1][1]
            if e not in OPTIONAL:
                errs.setdefault(w, []).append(("del", e, "", 1.0))
            last_word = w
            i -= 1
        else:
            h = heard[j - 1]
            if h not in ("ʔ", "h", "ə"):
                # лишний звук приписываем слову слева (хвост вроде «-ха»)
                w = exp[i - 1][1] if i > 0 else 0
                errs.setdefault(w, []).append(("ins", "", h, 0.8 if h in VOW else 0.7))
            j -= 1
    return errs


def check_sentence(words, mp3_path, phon_mod):
    exp = []
    idx = []
    for k, w in enumerate(words):
        if not re.search(r"[\u05D0-\u05EA]", w.get("t", "")):
            continue
        p = g2p(w["t"])
        exp += [(x, k) for x in p]
        idx.append(k)
    heard_raw = phon_mod.phonemes(phon_mod.load_audio(mp3_path))
    heard = [q for p, _ in heard_raw for q in norm_heard(p)]
    errs = align(exp, heard)
    out = []
    for k, lst in errs.items():
        exp_w = "".join(g2p(words[k]["t"])).replace("ə", "")
        score = sum(c for *_, c in lst) / max(3, len(exp_w))
        vowel_err = any(t == "sub" and a in VOW and b in VOW for t, a, b, _ in lst) or \
            any(t == "ins" and b in VOW for t, _, b, _ in lst)
        out.append({"i": k, "t": words[k]["t"], "expected": exp_w, "errors": lst, "score": round(score, 3),
                    "vowel": vowel_err})
    heard_str = "".join(heard)
    return sorted(out, key=lambda x: -x["score"]), heard_str


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sids", nargs="*")
    ap.add_argument("--min-occ", type=int, default=3, help="форма учитывается, если встретилась хотя бы N раз")
    ap.add_argument("--min-rate", type=float, default=0.6, help="доля вхождений с ошибкой гласной")
    ap.add_argument("--out", default=r"E:\NEW BIG PROJ\reports\pronunciation")
    args = ap.parse_args()
    sys.path.insert(0, HERE)
    import phon_model as P  # noqa
    paths = args.paths or []
    if args.all:
        paths += sorted(glob.glob(os.path.join(ROOT, "books", "*", "book-data.json")))
    os.makedirs(args.out, exist_ok=True)
    # Ошибка TTS систематична: одна и та же форма слова звучит неверно при
    # КАЖДОМ появлении (כָּל → «каль» сотни раз), а шум распознавания случаен
    # (~20% слов). Поэтому решает статистика по форме на весь корпус.
    forms = {}
    for p in paths:
        slug = os.path.basename(os.path.dirname(os.path.abspath(p)))
        data = json.load(open(p, encoding="utf-8"))
        n = 0
        for s in data["sentences"]:
            if args.sids and s["id"] not in args.sids:
                continue
            mp3 = os.path.join(ROOT, s["audio"]) if s.get("audio") else None
            if not mp3 or not os.path.exists(mp3):
                continue
            try:
                res, heard = check_sentence(s["words"], mp3, P)
            except Exception as e:  # битый mp3 и т.п. — не валим весь прогон
                print("  !", slug, s["id"], e)
                continue
            bad = {r["i"]: r for r in res if vowel_errs(r) > 0}
            for k, w in enumerate(s["words"]):
                core = unicodedata.normalize("NFC", "".join(ch for ch in w.get("t", "") if "\u0591" <= ch <= "\u05EA"))
                if not re.search(r"[\u05D0-\u05EA]", core):
                    continue
                f = forms.setdefault(core, {"occ": 0, "bad": 0, "expected": "".join(g2p(core)).replace("ə", ""), "examples": []})
                f["occ"] += 1
                if k in bad:
                    f["bad"] += 1
                    if len(f["examples"]) < 5:
                        f["examples"].append({"book": slug, "sid": s["id"], "i": k,
                                              "errors": [list(e[:3]) for e in bad[k]["errors"]]})
            n += 1
        print(f"{slug:26s} проверено предложений: {n}", flush=True)
    ranked = sorted(([k, v] for k, v in forms.items() if v["occ"] >= args.min_occ and v["bad"] / v["occ"] >= args.min_rate),
                    key=lambda kv: (-kv[1]["bad"] / kv[1]["occ"], -kv[1]["bad"]))
    json.dump({"forms": forms}, open(os.path.join(args.out, "forms_all.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(ranked, open(os.path.join(args.out, "suspects.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nФорм: {len(forms)}; подозрительных (≥{args.min_occ} вхождений, ≥{args.min_rate:.0%} с ошибкой гласной): {len(ranked)}")
    for k, v in ranked[:40]:
        print(f"  {k:16s} {v['bad']}/{v['occ']}  ожидалось {v['expected']}")


# Модель распознавания путает близкие гласные е/и и а/е и слышит короткую
# вставную «е» на стыке слов — это её шум, а не ошибка TTS (проверено на
# заведомо хороших клипах: ~20% слов). Реальные ошибки озвучки, найденные на
# слух, — другого рода: а↔о (כָּל «каль»), у↔о, лишний слог «-а» (עָלַיִךְ
# «алайха»). Считаем только такие.
NOISY_PAIRS = {frozenset("ei"), frozenset("ae")}


def strong(t, a, b):
    if t == "sub":
        return a in VOW and b in VOW and frozenset((a, b)) not in NOISY_PAIRS
    if t == "ins":
        return b in ("a", "o", "u")
    return False


def vowel_errs(r):
    return sum(1 for t, a, b, _ in r["errors"] if strong(t, a, b))


if __name__ == "__main__":
    main()
