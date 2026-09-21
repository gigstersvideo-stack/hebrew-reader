"""cover_filters.py — единый фильтр обложек читалки: яркая картинка (Flux) -> оформление библиотеки.

Исходники лежат в covers_src/<id>.jpg (яркие, как сгенерированы), готовые обложки — covers/<id>.webp.
Режим фильтра переключается одной командой, картинки заново генерировать не нужно:

    python build_covers.py --mode M4        # текущий: приглушённый фон со своим оттенком + живой герой
    python build_covers.py --mode M3        # то же, фон темнее
    python build_covers.py --mode A         # тёмно-зелёная плашка (палитра читалки), золото и крем
    python build_covers.py --mode C         # светлая «бумажная» плашка

M3/M4: фон приглушается и унифицируется (одинаковая светлота/насыщенность, оттенки подтянуты к
семейству красок библиотеки), герой остаётся ярче; общий бумажный налёт, чернильные линии,
зерно и одинаковая тонкая рамка связывают обложки в серию. A/C: карта градиента по палитре.
"""
import numpy as np
from PIL import Image, ImageFilter

import numpy as np
from PIL import Image, ImageFilter

W, H = 960, 450            # 2x от 480x225 (плашка на десктопе ~250x130)

def _hex(h): h = h.lstrip('#'); return np.array([int(h[i:i+2], 16) for i in (0, 2, 4)], dtype=np.float32)

# палитра читалки: --ink #211F1C, --accent #37564D, --gold #A8813F, --bg/--panel #EAE6DC/#F8F6F0
PALETTES = {
    # A: тёмно-зелёная плашка (как акцент читалки), золото и крем в светах
    "green": [(0.00, "#1B2622"), (0.30, "#3C5A50"), (0.62, "#B08D4A"), (1.00, "#F4EFE1")],
    # C: светлая «бумажная» плашка, тёмная линия, золотые акценты
    "paper": [(0.00, "#211F1C"), (0.30, "#6A5F49"), (0.62, "#D9CDB0"), (1.00, "#F3EEE0")],
}
BG_TARGET = {"green": 0.30, "paper": 0.62}

def _lum(a): return (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]) / 255.0

def _bg_lum(a):
    edge = np.concatenate([a[:6].reshape(-1, 3), a[-6:].reshape(-1, 3), a[:, :6].reshape(-1, 3), a[:, -6:].reshape(-1, 3)])
    q = (edge // 16).astype(int)
    key = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    vals, counts = np.unique(key, return_counts=True)
    top = vals[counts.argmax()]
    m = edge[key == top].mean(axis=0)
    return float(_lum(m[None, :])[0]), m

def _crop_band(img):
    """Кадр вокруг героя (целиком, без обрезки), масштаб по высоте плашки, боковые поля
    достраиваются продолжением фона (фон у обложек однотонный)."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    _, bg = _bg_lum(a)
    diff = np.abs(a - bg).sum(axis=2)
    rows = np.where((diff > 60).sum(axis=1) > 6)[0]
    if len(rows):
        top_c, bot_c = int(rows.min()), int(rows.max())
    else:
        top_c, bot_c = 0, a.shape[0] - 1
    cy = (top_c + bot_c) // 2
    ch = int(min(max((bot_c - top_c) * 1.14, 560), img.height))
    top = min(max(cy - ch // 2, 0), img.height - ch)
    crop = img.crop((0, top, img.width, top + ch))
    w = int(round(img.width * H / ch))
    crop = np.asarray(crop.resize((w, H), Image.LANCZOS).convert("RGB"), dtype=np.float32)
    if w >= W:
        x0 = (w - W) // 2
        return Image.fromarray(crop[:, x0:x0 + W].astype(np.uint8))
    left = (W - w) // 2
    right = W - w - left
    padded = np.pad(crop, ((0, 0), (left, right), (0, 0)), mode="edge")
    # шов: лёгкое размытие только на достроенных полях, чтобы не было полос
    blur = np.asarray(Image.fromarray(padded.astype(np.uint8)).filter(ImageFilter.GaussianBlur(10)), dtype=np.float32)
    mask = np.zeros(W, dtype=np.float32)
    mask[:left] = 1; mask[left + w:] = 1
    feather = 18
    for i in range(feather):
        f = 1 - (i + 1) / (feather + 1)
        if left + i < W: mask[left + i] = max(mask[left + i], f)
        if left - 1 - i >= 0: mask[left - 1 - i] = max(mask[left - 1 - i], f)
        if left + w - 1 - i >= 0: mask[left + w - 1 - i] = max(mask[left + w - 1 - i], f)
        if left + w + i < W: mask[left + w + i] = max(mask[left + w + i], f)
    out = padded * (1 - mask[None, :, None]) + blur * mask[None, :, None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

def _gradient_lut(stops):
    xs = np.array([s[0] for s in stops], dtype=np.float32)
    cols = np.stack([_hex(s[1]) for s in stops])
    t = np.linspace(0, 1, 256, dtype=np.float32)
    return np.stack([np.interp(t, xs, cols[:, c]) for c in range(3)], axis=1)

def apply_filter(img, palette="green", hue_keep=0.0, grain=5.0, seed=7):
    img = _crop_band(img)
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    L = _lum(a)
    bgL, _ = _bg_lum(a)
    bgL = min(max(bgL, 0.08), 0.92)
    tgt = BG_TARGET[palette]
    # фон -> один и тот же тон у всех обложек; тени и света растягиваются вокруг него
    L2 = np.where(L <= bgL, tgt * (L / bgL), tgt + (1 - tgt) * ((L - bgL) / (1 - bgL)))
    L2 = np.clip(L2, 0, 1)
    # герой отделяется от фона: маска «не фон» по расстоянию до цвета фона; в тёмной
    # палитре заливки героя светлеют, в светлой — темнеют (контуры не трогаем)
    _, bgc = _bg_lum(a)
    dist = np.sqrt(((a - bgc) ** 2).sum(axis=2)) / (255 * 1.732)
    m = np.clip((dist - 0.05) / 0.18, 0, 1)
    m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), dtype=np.float32) / 255
    sm = lambda x, e0, e1: np.clip((x - e0) / (e1 - e0), 0, 1) ** 2 * (3 - 2 * np.clip((x - e0) / (e1 - e0), 0, 1))
    if palette == "green":
        L2 = L2 + m * 0.36 * sm(L2, 0.06, 0.20)
    else:
        L2 = L2 - m * 0.34 * sm(L2, 0.45, 0.80)
    L2 = np.clip(L2, 0, 1)
    lut = _gradient_lut(PALETTES[palette])
    out = lut[np.clip((L2 * 255).astype(int), 0, 255)]
    if hue_keep > 0:   # чуть-чуть родного цвета, сильно приглушённого
        gray = np.repeat(L[..., None] * 255, 3, axis=2)
        muted = gray + (a - gray) * 0.55
        out = out * (1 - hue_keep) + muted * hue_keep
    # зерно + виньетка
    rng = np.random.default_rng(seed)
    out += rng.normal(0, grain, out.shape[:2])[..., None]
    yy, xx = np.mgrid[0:H, 0:W]
    v = 1 - 0.10 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    out *= v[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

def save_webp(img, path, quality=80):
    img.save(path, "WEBP", quality=quality, method=6)



INK = np.array([0x21, 0x1F, 0x1C], dtype=np.float32)
PAPER = np.array([0xEA, 0xE6, 0xDC], dtype=np.float32)
GOLD = np.array([0xC8, 0xA8, 0x62], dtype=np.float32)
# семейство красок библиотеки (градусы оттенка HSV)
ANCHORS = np.array([150, 178, 212, 28, 40, 305], dtype=np.float32)   # шалфей, море, синька, терракота, охра, слива


def rgb_to_hsv(a):
    r, g, b = a[..., 0] / 255, a[..., 1] / 255, a[..., 2] / 255
    mx = np.max(a / 255, axis=-1); mn = np.min(a / 255, axis=-1); d = mx - mn
    h = np.zeros_like(mx)
    m = d > 1e-6
    rc = (mx == r) & m; gc = (mx == g) & m & ~rc; bc = m & ~rc & ~gc
    h[rc] = ((g - b)[rc] / d[rc]) % 6
    h[gc] = (b - r)[gc] / d[gc] + 2
    h[bc] = (r - g)[bc] / d[bc] + 4
    h = h * 60
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0)
    return h, s, mx


def hsv_to_rgb(h, s, v):
    h6 = (h % 360) / 60
    c = v * s
    x = c * (1 - np.abs(h6 % 2 - 1))
    z = np.zeros_like(c)
    conds = [(h6 < 1), (h6 < 2), (h6 < 3), (h6 < 4), (h6 < 5), (h6 >= 5)]
    rs = np.select(conds, [c, x, z, z, x, c]); gs = np.select(conds, [x, c, c, x, z, z]); bs = np.select(conds, [z, z, x, c, c, x])
    m = v - c
    return np.stack([rs + m, gs + m, bs + m], axis=-1) * 255


def snap_hue(h, s, strength):
    """Подтянуть оттенок к ближайшему из семейства; чем ярче цвет, тем сильнее (серые не трогаем)."""
    diff = ((ANCHORS[None, :] - h.reshape(-1, 1) + 540) % 360) - 180        # знаковая разность до каждого якоря
    idx = np.abs(diff).argmin(axis=1)
    delta = diff[np.arange(len(idx)), idx].reshape(h.shape)
    return h + delta * strength * np.clip(s * 3, 0, 1)


def _frame(out, inset=14, width=2, alpha=0.55):
    col = PAPER
    for t in range(width):
        for line in (out[inset + t, inset:W - inset], out[H - 1 - inset - t, inset:W - inset]):
            line[:] = line * (1 - alpha) + col * alpha
        for line in (out[inset:H - inset, inset + t], out[inset:H - inset, W - 1 - inset - t]):
            line[:] = line * (1 - alpha) + col * alpha
    return out


def apply_filter2(img, bg_target=0.40, sat_bg=0.45, sat_hero=0.80, hero_keep=0.6, sat_window=(0.12, 0.40),
                  wash=0.18, wash_hero=0.08, snap=0.40, grain=4.0, seed=7, frame=True):
    """Фон приглушается и унифицируется сильно, герой — мягко (остаётся заметно ярче фона)."""
    img = _crop_band(img)
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    L = _lum(a)
    bgL, bgc = _bg_lum(a)
    bgL = min(max(bgL, 0.08), 0.92)
    # маска героя (не фон)
    dist = np.sqrt(((a - bgc) ** 2).sum(axis=2)) / (255 * 1.732)
    m = np.clip((dist - 0.05) / 0.18, 0, 1)
    m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), dtype=np.float32) / 255
    # светлота: фон -> bg_target; у героя часть исходной светлоты сохраняется
    L2 = np.where(L <= bgL, bg_target * (L / bgL), bg_target + (1 - bg_target) * ((L - bgL) / (1 - bgL)))
    L2 = np.clip(L2 * (1 - m * hero_keep) + L * m * hero_keep, 0, 1)
    gray = np.repeat((L * 255)[..., None], 3, axis=2)
    sat = (sat_bg * (1 - m) + sat_hero * m)[..., None]
    muted = gray + (a - gray) * sat
    Lm = np.maximum(_lum(muted), 1e-3)
    col = np.clip(muted * (L2 / Lm)[..., None], 0, 255)
    h, s, v = rgb_to_hsv(col)
    lo, hi = sat_window
    s_bg = np.where(s > 0.02, np.clip(lo + s * (hi - lo) * 1.6, 0, hi), s)
    s = s_bg * (1 - m) + np.minimum(s, 0.75) * m           # у героя насыщенность почти как есть (после sat_hero)
    h = snap_hue(h, s, snap)
    col = hsv_to_rgb(h, s, v)
    dark = np.clip((0.16 - L2) / 0.16, 0, 1)[..., None]
    col = col * (1 - dark * 0.85) + INK * dark * 0.85
    w = (wash * (1 - m) + wash_hero * m)[..., None]
    col = col * (1 - w) + PAPER * w
    col = np.maximum(col, INK * 0.9 + 6)
    rng = np.random.default_rng(seed)
    col += rng.normal(0, grain, col.shape[:2])[..., None]
    yy, xx = np.mgrid[0:H, 0:W]
    vg = 1 - 0.10 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    col *= vg[..., None]
    col = np.clip(col, 0, 255)
    if frame:
        col = _frame(col)
    return Image.fromarray(col.astype(np.uint8))


OUT_W, OUT_H = 640, 300     # итоговый размер файла (плашка в карточке ~130px высотой, 2x для ретины)

PRESETS = {
    "M4": lambda im: apply_filter2(im, bg_target=0.52, sat_bg=0.40, sat_hero=0.85, hero_keep=0.55, wash=0.26),
    "M3": lambda im: apply_filter2(im, bg_target=0.36, sat_bg=0.45, sat_hero=0.85, hero_keep=0.60, wash=0.20),
    "A": lambda im: apply_filter(im, "green", 0.0),
    "C": lambda im: apply_filter(im, "paper", 0.0),
}


def render_cover(img, mode="M4"):
    """PIL-картинка (квадрат от Flux) -> готовая обложка OUT_W x OUT_H."""
    return PRESETS[mode](img).resize((OUT_W, OUT_H), Image.LANCZOS)
