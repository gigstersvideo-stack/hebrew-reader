"""build_covers.py — covers_src/<id>.jpg -> covers/<id>.webp через фильтр + поле "cover" в books-manifest.json.

    python build_covers.py                  # режим M4, все книги, у которых есть исходник
    python build_covers.py --mode A         # переключить весь набор на другой фильтр
    python build_covers.py demo-scifi cat-bet   # только эти книги

Исходники (яркие картинки Flux) не меняются — меняется только фильтр. Старые coverSvg
остаются в манифесте как запасные (читалка показывает cover, а при ошибке загрузки — coverSvg).
"""
import argparse, json, os, sys
from PIL import Image
from cover_filters import PRESETS, render_cover

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "covers_src")
OUT = os.path.join(HERE, "covers")
MANIFEST = os.path.join(HERE, "books-manifest.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="M4", choices=sorted(PRESETS))
    ap.add_argument("--quality", type=int, default=78)
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raw = open(MANIFEST, encoding="utf-8", newline="").read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    manifest = json.loads(raw)
    by_id = {b["id"]: b for b in manifest["books"]}
    os.makedirs(OUT, exist_ok=True)
    ids = args.ids or [b["id"] for b in manifest["books"] if os.path.exists(os.path.join(SRC, b["id"] + ".jpg"))]
    total = 0
    for bid in ids:
        src = os.path.join(SRC, bid + ".jpg")
        if bid not in by_id or not os.path.exists(src):
            print("пропуск (нет книги или исходника):", bid)
            continue
        out = render_cover(Image.open(src).convert("RGB"), args.mode)
        dst = os.path.join(OUT, bid + ".webp")
        out.save(dst, "WEBP", quality=args.quality, method=6)
        by_id[bid]["cover"] = "covers/" + bid + ".webp"
        total += os.path.getsize(dst)
        print(f"{bid}: {os.path.getsize(dst) // 1024} КБ")
    text = json.dumps(manifest, ensure_ascii=False, indent=2).replace("\n", eol)
    open(MANIFEST, "w", encoding="utf-8", newline="").write(text)
    print(f"готово: {len(ids)} обложек, режим {args.mode}, всего {total // 1024} КБ")


if __name__ == "__main__":
    main()
