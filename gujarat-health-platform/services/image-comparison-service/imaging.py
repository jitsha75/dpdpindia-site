"""
imaging.py - AP-04 pairwise image comparison with numpy only (no ML model, no face or medical analytics).

  align()            translation alignment by phase correlation (FFT)
  ssim()             SSIM-style structural similarity (Wang et al. formula, box window)
  change_regions()   difference map -> threshold -> clean-up -> connected components -> boxes
  count_objects()    colour segmentation + connected components to count cartons / boxes against an expected count
  build_progress()   share of the planned structure outline that is built (works progress)

Synthetic scenes are drawn with PIL so the demo has realistic reference / current pairs.
"""
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 640, 420


# ------------------------------------------------------------------ basic ops
def to_gray(img):
    return np.asarray(img.convert("L"), dtype=np.float32)


def box_filter(a, k):
    p = k // 2
    pad = np.pad(a, p, mode="edge")
    cs = pad.cumsum(0).cumsum(1)
    cs = np.pad(cs, ((1, 0), (1, 0)))
    return (cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]) / (k * k)


def align(ref, cur):
    """Phase correlation: returns (dy, dx) such that cur shifted by (-dy, -dx) lines up with ref."""
    hy, hx = np.hanning(ref.shape[0]), np.hanning(ref.shape[1])
    win = np.outer(hy, hx)
    a = np.fft.fft2((ref - ref.mean()) * win)
    b = np.fft.fft2((cur - cur.mean()) * win)
    r = np.conj(a) * b
    r /= np.abs(r) + 1e-9
    corr = np.abs(np.fft.ifft2(r))
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    if dy > ref.shape[0] // 2:
        dy -= ref.shape[0]
    if dx > ref.shape[1] // 2:
        dx -= ref.shape[1]
    return int(dy), int(dx), float(corr.max())


def shift(a, dy, dx, fill=0.0):
    """Shift an array by (-dy, -dx) (undo the measured displacement); exposed border gets `fill`."""
    out = np.full_like(a, fill)
    h, w = a.shape[:2]
    ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
    xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
    out[yd, xd] = a[ys, xs]
    return out


def valid_mask(shape, dy, dx):
    m = np.zeros(shape, dtype=bool)
    h, w = shape
    m[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)] = True
    return m


def ssim(a, b, mask=None, k=7):
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = box_filter(a, k), box_filter(b, k)
    saa = box_filter(a * a, k) - mu_a ** 2
    sbb = box_filter(b * b, k) - mu_b ** 2
    sab = box_filter(a * b, k) - mu_a * mu_b
    smap = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (saa + sbb + c2))
    if mask is not None:
        return float(smap[mask].mean()), smap
    return float(smap.mean()), smap


def otsu(a):
    a = np.clip(a, 0, 255).astype(np.uint8)
    hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
    tot, s_all = a.size, np.dot(np.arange(256), hist)
    wb = sb = 0.0
    best, thr = -1, 0
    for t in range(256):
        wb += hist[t]
        if wb == 0 or wb == tot:
            continue
        sb += t * hist[t]
        mb, mf = sb / wb, (s_all - sb) / (tot - wb)
        v = wb * (tot - wb) * (mb - mf) ** 2
        if v > best:
            best, thr = v, t
    return thr


def components(mask, min_area=1):
    """4-connected component labelling (iterative flood fill). Returns list of dicts with area and box."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]
        seen[y0, x0] = True
        area, minx, miny, maxx, maxy = 0, x0, y0, x0, y0
        while stack:
            y, x = stack.pop()
            area += 1
            minx, maxx, miny, maxy = min(minx, x), max(maxx, x), min(miny, y), max(maxy, y)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if area >= min_area:
            out.append({"area": int(area), "box": [int(minx), int(miny), int(maxx - minx + 1), int(maxy - miny + 1)]})
    return out


# ------------------------------------------------------------------ analyses
def compare(ref_img, cur_img, min_region_px=120):
    """Full pairwise comparison. Returns (result dict, aligned current RGB array, change mask)."""
    ref_img = ref_img.convert("RGB").resize((W, H))
    cur_img = cur_img.convert("RGB").resize((W, H))
    rg, cg = to_gray(ref_img), to_gray(cur_img)
    dy, dx, peak = align(rg, cg)
    cg_al = shift(cg, dy, dx, fill=float(cg.mean()))
    vm = valid_mask(rg.shape, dy, dx)
    inner = np.zeros_like(vm)
    inner[6:-6, 6:-6] = True
    vm &= shift(inner, dy, dx).astype(bool) | inner
    score, smap = ssim(rg, cg_al, vm)
    cur_rgb = np.asarray(cur_img, dtype=np.float32)
    cur_al = np.stack([shift(cur_rgb[..., i], dy, dx, fill=float(cur_rgb[..., i].mean())) for i in range(3)], -1)
    diff = np.abs(np.asarray(ref_img, dtype=np.float32) - cur_al).max(axis=2)
    diff = box_filter(diff, 5)
    diff[~vm] = 0
    thr = max(40.0, float(otsu(diff[vm]))) if vm.any() else 40.0
    mask = diff > thr
    mask = box_filter(mask.astype(np.float32), 5) > 0.5   # opening-like clean-up
    # half-resolution labelling keeps it fast; boxes scaled back
    small = mask[::2, ::2]
    regs = components(small, max(1, min_region_px // 4))
    regions = []
    for r in sorted(regs, key=lambda r: -r["area"])[:12]:
        x, y, w, h = [v * 2 for v in r["box"]]
        regions.append({"box": [x, y, w, h], "area_px": r["area"] * 4,
                        "mean_diff": round(float(diff[y:y + h, x:x + w].mean()), 1)})
    changed = float(mask[vm].mean()) if vm.any() else 0.0
    res = {"alignment": {"dy": dy, "dx": dx, "peak": round(peak, 4)}, "ssim": round(score, 4),
           "changed_area_pct": round(100 * changed, 2), "diff_threshold": round(thr, 1), "regions": regions,
           "region_count": len(regions)}
    return res, cur_al.clip(0, 255).astype(np.uint8), mask


def carton_mask(rgb):
    a = rgb.astype(np.int32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (r > 110) & (r < 230) & (g > 70) & (g < 180) & (b < 120) & (r - b > 60) & (r > g)


def count_objects(img, expected=None):
    rgb = np.asarray(img.convert("RGB").resize((W, H)))
    m = carton_mask(rgb)
    m = box_filter(m.astype(np.float32), 3) > 0.6
    comps = [c for c in components(m[::2, ::2], 20)]
    if not comps:
        return {"count": 0, "expected": expected, "difference": (0 - expected) if expected is not None else None, "objects": []}
    med = float(np.median([c["area"] for c in comps]))
    objs, n = [], 0
    for c in comps:
        k = int(round(c["area"] / med)) if med else 1
        if k == 0:
            continue
        n += k
        objs.append({"box": [v * 2 for v in c["box"]], "units": k})
    out = {"count": n, "expected": expected, "difference": (n - expected) if expected is not None else None,
           "objects": objs, "method": "colour segmentation + connected components; merged blobs split by median unit area"}
    return out


def concrete_mask(rgb):
    a = rgb.astype(np.int32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (abs(r - g) < 14) & (abs(g - b) < 18) & (r > 120) & (r < 200)


def build_progress(img, plan_box):
    """Rows of the planned outline that are mostly built (concrete grey) / rows of the outline."""
    rgb = np.asarray(img.convert("RGB").resize((W, H)))
    x0, y0, x1, y1 = plan_box
    m = concrete_mask(rgb[y0:y1, x0:x1])
    rows = m.mean(axis=1) > 0.5
    return round(100.0 * rows.sum() / max(1, rows.size), 1)


# ------------------------------------------------------------------ synthetic scenes
def _noise(img, rng, sigma=4):
    a = np.asarray(img, dtype=np.float32)
    a = a + np.random.default_rng(rng.randint(0, 2 ** 31)).normal(0, sigma, a.shape)
    return Image.fromarray(a.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))


def _camera(img, rng, dx=0, dy=0, light=1.0):
    a = np.asarray(img, dtype=np.float32) * light
    img = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    canvas = Image.new("RGB", img.size, (205, 200, 190))
    canvas.paste(img, (dx, dy))
    return _noise(canvas, rng)


def scene_cartons(n_present, missing=(), seed=0, dx=0, dy=0, light=1.0, label="PARACETAMOL 500"):
    """Pallet of cartons in a 3 x 6 grid (or a different grid if n_present > 18). `missing` = slots left empty."""
    rng = random.Random(f"cartons|{seed}")
    img = Image.new("RGB", (W, H), (190, 188, 180))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 300, W, H), fill=(150, 146, 140))          # floor
    d.rectangle((60, 330, 580, 350), fill=(120, 90, 60))        # pallet
    cols, rows = 6, 3 if n_present <= 18 else 4
    cw, ch = 74, 70
    for i in range(cols * rows):
        if i in missing or i >= n_present + len(missing):
            continue
        cx, cy = 70 + (i % cols) * (cw + 10), 320 - (i // cols + 1) * (ch + 8)
        tone = rng.randint(-10, 10)
        d.rectangle((cx, cy, cx + cw, cy + ch), fill=(176 + tone, 128 + tone, 72 + tone), outline=(110, 75, 40), width=2)
        d.rectangle((cx + cw // 2 - 4, cy, cx + cw // 2 + 4, cy + ch), fill=(198, 150, 96))
        d.text((cx + 6, cy + ch // 2 - 5), label[:10], fill=(60, 40, 20))
    d.text((20, 20), "GRN inspection photo (synthetic)", fill=(40, 40, 40))
    return _camera(img, rng, dx, dy, light)


PLAN_BOX = (180, 90, 470, 330)


def scene_site(pct, seed=0, dx=0, dy=0, floors=4):
    """Works site: sky, ground, dashed planned outline, built part rising in concrete grey."""
    rng = random.Random(f"site|{seed}")
    img = Image.new("RGB", (W, H), (176, 204, 230))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 330, W, H), fill=(138, 120, 90))
    x0, y0, x1, y1 = PLAN_BOX
    for x in range(x0, x1, 12):   # dashed outline of the planned block
        d.line((x, y0, x + 6, y0), fill=(200, 60, 60), width=2)
    for y in range(y0, y1, 12):
        d.line((x0, y, x0, y + 6), fill=(200, 60, 60), width=2)
        d.line((x1, y, x1, y + 6), fill=(200, 60, 60), width=2)
    top = int(y1 - (y1 - y0) * pct / 100.0)
    d.rectangle((x0 + 2, top, x1 - 2, y1), fill=(162, 162, 158))
    fh = (y1 - y0) / floors
    for k in range(1, floors):
        yy = int(y1 - k * fh)
        if yy > top:
            d.line((x0 + 2, yy, x1 - 2, yy), fill=(120, 120, 118), width=3)
    for wx in range(x0 + 20, x1 - 30, 50):   # window openings in built part
        for k in range(floors):
            wy = int(y1 - (k + 0.7) * fh)
            if wy > top + 5:
                d.rectangle((wx, wy, wx + 22, wy + 18), fill=(70, 70, 80))
    d.line((520, 330, 520, 60), fill=(230, 180, 30), width=6)   # crane
    d.line((440, 64, 620, 64), fill=(230, 180, 30), width=5)
    d.rectangle((40, 290, 120, 330), fill=(120, 110, 100))      # material shed
    d.text((20, 20), "Works site photo (synthetic)", fill=(30, 30, 30))
    return _camera(img, rng, dx, dy)


def scene_panels(damaged=False, seed=0, dx=0, dy=0, kind="solar"):
    rng = random.Random(f"panel|{kind}|{seed}")
    if kind == "solar":
        img = Image.new("RGB", (W, H), (150, 170, 190))
        d = ImageDraw.Draw(img)
        for r in range(3):
            for c in range(5):
                x, y = 50 + c * 110, 70 + r * 100
                d.rectangle((x, y, x + 100, y + 90), fill=(30, 50, 110), outline=(200, 200, 210), width=3)
                for k in range(1, 4):
                    d.line((x + k * 25, y, x + k * 25, y + 90), fill=(60, 80, 140), width=1)
                d.line((x, y + 45, x + 100, y + 45), fill=(60, 80, 140), width=1)
        if damaged:   # cracked panel row 2 col 3 + scorch mark
            x, y = 50 + 3 * 110, 70 + 1 * 100
            pts = [(x + 10, y + 5), (x + 40, y + 40), (x + 30, y + 70), (x + 70, y + 88)]
            d.line(pts, fill=(235, 235, 240), width=3)
            d.line([(x + 40, y + 40), (x + 92, y + 30)], fill=(235, 235, 240), width=2)
            d.ellipse((50 + 110 + 30, 70 + 200 + 20, 50 + 110 + 75, 70 + 200 + 62), fill=(40, 30, 25))
        d.text((20, 20), "Asset condition photo (synthetic)", fill=(20, 20, 20))
    else:   # ceiling panels with water seepage
        img = Image.new("RGB", (W, H), (226, 224, 218))
        d = ImageDraw.Draw(img)
        for r in range(4):
            for c in range(6):
                x, y = 20 + c * 100, 30 + r * 95
                d.rectangle((x, y, x + 96, y + 91), fill=(236, 234, 228), outline=(170, 168, 160), width=3)
        d.rectangle((240, 160, 330, 180), fill=(250, 250, 240))   # light fitting
        if damaged:
            d.ellipse((350, 120, 470, 210), fill=(196, 176, 130))
            d.ellipse((380, 140, 440, 190), fill=(170, 145, 95))
            d.polygon([(130, 300), (200, 310), (180, 360), (120, 345)], fill=(205, 190, 150))
        d.text((20, 6), "Asset condition photo (synthetic)", fill=(20, 20, 20))
    return _camera(img, rng, dx, dy)


def overlay(cur_al, mask, regions, objects=None):
    """Current image (aligned) with a red tint on changed pixels, red boxes on change regions, green boxes on counted objects."""
    a = cur_al.astype(np.float32).copy()
    a[mask] = a[mask] * 0.45 + np.array([255, 0, 0], dtype=np.float32) * 0.55
    img = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    for o in objects or []:
        x, y, w, h = o["box"]
        d.rectangle((x, y, x + w, y + h), outline=(0, 200, 0), width=2)
    for i, r in enumerate(regions):
        x, y, w, h = r["box"]
        d.rectangle((x - 2, y - 2, x + w + 2, y + h + 2), outline=(255, 30, 30), width=3)
        d.text((x, max(0, y - 14)), f"#{i + 1}", fill=(255, 30, 30))
    return img
