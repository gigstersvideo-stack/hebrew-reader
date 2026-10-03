"""Применяет решения со страницы «Сверка огласовки» к книгам.

    python tools/apply_review.py <review_dir>            # сухой прогон: что изменится
    python tools/apply_review.py <review_dir> --write    # записать book-data.json
    python tools/apply_review.py <review_dir> --write --revoice   # + переозвучить изменённые

<review_dir> — выгрузка базы страницы (ArtifactData list с out_dir):
    <review_dir>/books/<slug>.json      находки (items: id, kind, sid, i, ours, ...)
    <review_dir>/decisions/<slug>.json  решения ({"items": {id: {choice, value, ...}}})
Решение без записи в decisions НЕ применяется (вердикт Claude — только
предложение, пока владелец его не подтвердил).

Защиты:
  * правка применяется, только если слово в книге всё ещё совпадает с тем,
    что видел владелец (`ours`) — иначе «устарело», пропуск;
  * замена огласовки не может молча выбросить буквы ктив мале: если у нового
    варианта меньше ו/י, чем у нашего, а выбран не «свой вариант» — пропуск
    с пометкой (вариант Nakdan часто записан ктив хасер);
  * пунктуация вокруг слова (кавычки, точка, запятая) сохраняется.

Переозвучка берёт актуальный 3_generate_audio.py (подмены текста для
синтеза, fix_kol_qamats) из E:\\NEW BIG PROJ\\tools — копия в этом репо
отстала (см. аудит 2026-10-02) — и озвучивает ТОЛЬКО изменённые предложения
в тот же файл mp3 с новыми таймингами слов.
"""
import argparse
import asyncio
import importlib.util
import json
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import audit_book_hebrew as A  # noqa: E402

AUDIO_TOOL = r"E:\NEW BIG PROJ\tools\3_generate_audio.py"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def doc_body(d):
    # out_dir ArtifactData кладёт либо сам документ, либо {data: ...}
    return d.get("data", d) if isinstance(d, dict) else d


def replace_hebrew_span(t, new):
    """Заменяет ивритскую часть токена, сохраняя пунктуацию по краям."""
    idx = [k for k, ch in enumerate(t) if A.HEB_ANY.match(ch)]
    if not idx:
        return None
    return t[:idx[0]] + new + t[idx[-1] + 1:]


def matres_lost(old, new):
    count = lambda s: sum(A.bare(s).count(c) for c in "וי")
    return count(new) < count(old)


def value_of(item, dec):
    if dec.get("value"):
        return dec["value"]
    ch = dec.get("choice", "")
    if ch == "custom":
        return dec.get("custom", "")
    if ch == "ours":
        return item["ours"]
    if ch == "claude":
        return item.get("claude", "")
    if ch == "nakdan":
        return (item.get("fix") or [""])[0] if item["kind"] == "lemma_prefix" else item.get("nakdan", "")
    if ch.startswith("alt"):
        k = int(ch[3:])
        arr = item.get("fix") if item["kind"] == "lemma_prefix" else item.get("alts")
        return (arr or [])[k] if arr and k < len(arr) else ""
    if ch == "fix":
        return (item.get("fix") or [""])[0]
    return ""


def apply_book(slug, items, decisions, report, extra_ops=None):
    path = os.path.join(ROOT, "books", slug, "book-data.json")
    data = load(path)
    sents = {s["id"]: s for s in data["sentences"]}
    changed_sids, removals = set(), []
    # леммы — раньше огласовки: на одном слове бывают обе находки, и правка
    # огласовки иначе успела бы переписать лемму, равную словоформе
    order = {"lemma_prefix": 0, "script": 1, "niqqud": 2, "double": 3}
    for it in sorted(items, key=lambda x: order.get(x["kind"], 9)):
        dec = decisions.get(it["id"])
        if not dec or not dec.get("choice") or dec["choice"] == "ours":
            continue
        val = A.nfc(value_of(it, dec).strip())
        s = sents.get(it["sid"])
        if not s or it["i"] >= len(s["words"]):
            report["stale"].append((slug, it["sid"], it["ours"], "нет такого слова"))
            continue
        w = s["words"][it["i"]]
        kind = it["kind"]
        if kind == "niqqud":
            if A.heb_word(w.get("t")) != A.nfc(it["ours"]):
                report["stale"].append((slug, it["sid"], it["ours"], A.heb_word(w.get("t"))))
                continue
            if not val or not A.bare(val):
                report["skipped"].append((slug, it["sid"], it["ours"], "пустой вариант"))
                continue
            if dec["choice"] != "custom" and matres_lost(it["ours"], val):
                report["skipped"].append((slug, it["sid"], it["ours"], f"{val}: теряются ו/י ктив мале — нужен «свой вариант»"))
                continue
            new_t = replace_hebrew_span(w["t"], val)
            old_word = A.heb_word(w["t"])
            w["t"] = new_t
            if A.nfc(w.get("lemma", "")) == old_word:
                w["lemma"] = val  # лемма = та же форма (имена, неизменяемые слова)
            changed_sids.add(it["sid"])
            report["niqqud"] += 1
        elif kind == "lemma_prefix":
            if A.nfc(w.get("lemma", "")) != A.nfc(it["ours"]):
                report["stale"].append((slug, it["sid"], it["ours"], w.get("lemma")))
                continue
            if not val or not A.bare(val):
                continue
            w["lemma"] = val
            report["lemma"] += 1
        elif kind == "double":
            if A.bare(w.get("t")) != A.bare(it["ours"]) or it["i"] == 0 or \
                    A.bare(s["words"][it["i"] - 1].get("t")) != A.bare(it["ours"]):
                report["stale"].append((slug, it["sid"], it["ours"], "дубль уже не на месте"))
                continue
            removals.append((it["sid"], it["i"]))
            report["double"] += 1
        elif kind == "script":
            fld, _, fixed = val.partition(": ")
            if fld and fixed and fld in w:
                w[fld] = fixed
                report["script"] += 1
    # ручные правки из нескольких слов (extras): replace / delete / merge
    for op in sorted(extra_ops or [], key=lambda o: -o["i"]):
        s = sents.get(op["sid"])
        if not s or op["i"] >= len(s["words"]) or A.heb_word(s["words"][op["i"]]["t"]) != A.nfc(op["ours"]):
            report["stale"].append((slug, op["sid"], op["ours"], "ручная правка: слово не на месте"))
            continue
        w = s["words"][op["i"]]
        if op["op"] == "replace":
            w["t"] = replace_hebrew_span(w["t"], A.nfc(op["value"]))
            if op.get("lemma"):
                w["lemma"] = A.nfc(op["lemma"])
        elif op["op"] == "delete":
            del s["words"][op["i"]]
        elif op["op"] == "merge":  # слово i + слово i+1 → одно слово value (свойства — у следующего)
            nxt = s["words"][op["i"] + 1]
            nxt["t"] = replace_hebrew_span(nxt["t"], A.nfc(op["value"]))
            if op.get("lemma"):
                nxt["lemma"] = A.nfc(op["lemma"])
            del s["words"][op["i"]]
        changed_sids.add(op["sid"])
        report["extra"] = report.get("extra", 0) + 1
    # удаления дублей — с конца, чтобы индексы не съехали
    for sid, i in sorted(removals, key=lambda x: (x[0], -x[1])):
        prev, cur = sents[sid]["words"][i - 1], sents[sid]["words"][i]
        # пунктуация, висевшая на удаляемом слове, переезжает на предыдущее
        tail = cur["t"][len(cur["t"].rstrip(".,!?:;…\"'»”")):]
        if tail and not prev["t"].endswith(tail):
            prev["t"] = prev["t"] + tail
        del sents[sid]["words"][i]
        changed_sids.add(sid)
    return path, data, changed_sids


def load_audio_tool():
    spec = importlib.util.spec_from_file_location("gen_audio", AUDIO_TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


async def revoice(data, sids, slug, m, voice="he-IL-AvriNeural"):
    bad = m.load_known_bad_lemmas()
    subs = m.load_text_substitutions()
    mism = 0
    for s in data["sentences"]:
        if s["id"] not in sids or not s.get("audio"):
            continue
        out = os.path.join(ROOT, s["audio"])
        text = m.build_tts_text(s["words"], subs)
        for w in s["words"]:
            w.pop("start", None); w.pop("end", None)
        if s.get("ttsEngine") == "gtts" or any(w.get("lemma") in bad for w in s["words"]):
            s["ttsEngine"] = "gtts"
            bounds = m.synthesize_sentence_gtts(text, out)
        elif voice == "he-IL-AvriNeural" and getattr(m, "_azure_ipa", None) is not None and m._azure_ipa.needs_ipa(text):
            # формы на -ךְ, которые edge-tts не умеет — та же ветка, что в main()
            bounds = m._azure_ipa.synthesize_ipa(text, out)
        else:
            bounds, fell = await m.synthesize_sentence_with_retry(text, voice, out)
            if fell:
                s["ttsEngine"] = "gtts"
        if not m.assign_word_timings(s["words"], bounds):
            mism += 1
            print(f"  [!] {slug}/{s['id']}: тайминги не сошлись ({len(bounds)} границ / {len(s['words'])} слов)")
    return mism


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("review_dir")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--revoice", action="store_true")
    ap.add_argument("--only", nargs="*", help="только эти книги (slug)")
    args = ap.parse_args()

    books_dir = os.path.join(args.review_dir, "books")
    dec_dir = os.path.join(args.review_dir, "decisions")
    m = load_audio_tool() if args.revoice else None
    extras_path = os.path.join(args.review_dir, "extras.json")
    extras = load(extras_path) if os.path.exists(extras_path) else []
    total = {"niqqud": 0, "lemma": 0, "double": 0, "script": 0, "stale": [], "skipped": []}
    revoiced = 0
    for fn in sorted(os.listdir(books_dir)):
        slug = fn[:-5]
        if args.only and slug not in args.only:
            continue
        dpath = os.path.join(dec_dir, fn)
        if not os.path.exists(dpath) and not any(o["book"] == slug for o in extras):
            continue
        items = doc_body(load(os.path.join(books_dir, fn)))["items"]
        decisions = doc_body(load(dpath)).get("items", {}) if os.path.exists(dpath) else {}
        rep = {"niqqud": 0, "lemma": 0, "double": 0, "script": 0, "stale": [], "skipped": []}
        extra_ops = [o for o in extras if o["book"] == slug]
        path, data, sids = apply_book(slug, items, decisions, rep, extra_ops)
        print(f"{slug:26s} огласовка {rep['niqqud']:4d}  лемм {rep['lemma']:4d}  дублей {rep['double']:3d}  "
              f"символов {rep['script']:3d}  к переозвучке {len(sids):3d}  устарело {len(rep['stale'])}  пропущено {len(rep['skipped'])}")
        for k in ("niqqud", "lemma", "double", "script"):
            total[k] += rep[k]
        total["stale"] += rep["stale"]; total["skipped"] += rep["skipped"]
        if args.write:
            if args.revoice and sids:
                asyncio.run(revoice(data, sids, slug, m))
                revoiced += len(sids)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
    print(f"\nИтого: огласовка {total['niqqud']}, лемм {total['lemma']}, дублей {total['double']}, "
          f"символов {total['script']}; переозвучено {revoiced}.")
    for x in total["skipped"]:
        print("  ПРОПУСК", *x)
    for x in total["stale"][:30]:
        print("  УСТАРЕЛО", *x)
    if not args.write:
        print("Сухой прогон — ничего не записано. Для записи: --write [--revoice]")


if __name__ == "__main__":
    main()
