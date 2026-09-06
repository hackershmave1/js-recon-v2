"""Generate eye-mark logo icons at 16/48/128 px using Pillow.

The mark: a stylized eye (reconnaissance) — dark rounded-square background
with a lime (#CDEB45) eye teardrop outline, iris ring, and pupil.
Renders at 4× then downsamples for clean anti-aliasing.
"""
import math, pathlib
from PIL import Image, ImageDraw

LIME = (205, 235, 69)
DARK = (14, 16, 22)
base = pathlib.Path(__file__).parent


def eye_polygon(cx, cy, hw, hh, n=80):
    """Return (upper_pts, lower_pts) for the eye teardrop outline."""
    upper, lower = [], []
    for i in range(n + 1):
        t = i / n
        x = cx + (t * 2 - 1) * hw
        upper.append((x, cy - hh * math.sin(math.pi * t)))
        lower.append((cx + (1 - t * 2) * hw, cy + hh * math.sin(math.pi * t)))
    return upper, lower


def draw_eye_icon(target_size: int) -> Image.Image:
    scale = 4
    s = target_size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded-rect background
    r = max(8, int(s * 0.22))
    draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=(*DARK, 255))

    cx, cy = s / 2, s / 2

    if target_size <= 16:
        # 16 px: iris ring + pupil only (eye teardrop too thin to read)
        iris_r = s * 0.275
        lw = max(2, int(s * 0.07))
        draw.ellipse([cx - iris_r, cy - iris_r, cx + iris_r, cy + iris_r],
                     outline=LIME, width=lw)
        p = s * 0.115
        draw.ellipse([cx - p, cy - p, cx + p, cy + p], fill=LIME)
    else:
        hw = s * 0.385
        hh = s * 0.195
        upper, lower = eye_polygon(cx, cy, hw, hh)

        # Semi-transparent eye fill
        fill_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(fill_layer).polygon(upper + lower[::-1], fill=(*LIME, 22))
        img = Image.alpha_composite(img, fill_layer)
        draw = ImageDraw.Draw(img)

        # Eye outline (draw segments for smooth stroke)
        lw = max(2, int(s * 0.048))
        all_pts = upper + lower
        for i in range(len(all_pts) - 1):
            draw.line([all_pts[i], all_pts[i + 1]], fill=LIME, width=lw)
        draw.line([all_pts[-1], all_pts[0]], fill=LIME, width=lw)

        # Iris ring
        ir = s * 0.185
        lw2 = max(2, int(s * 0.046))
        draw.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], outline=LIME, width=lw2)

        # Pupil
        p = s * 0.082
        draw.ellipse([cx - p, cy - p, cx + p, cy + p], fill=LIME)

    return img.resize((target_size, target_size), Image.LANCZOS)


for size, name in [(16, "icon16.png"), (48, "icon48.png"), (128, "icon128.png")]:
    icon = draw_eye_icon(size)
    # Flatten onto opaque dark background (extension icons must be opaque PNGs)
    final = Image.new("RGB", (size, size), DARK)
    final.paste(icon, mask=icon.split()[3])
    out = base / name
    final.save(str(out))
    print(f"Generated {name} ({size}×{size})")
