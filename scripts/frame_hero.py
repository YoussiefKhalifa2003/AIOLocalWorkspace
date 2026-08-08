"""Build README hero frames from a real Windows Terminal capture.

Preferred source (if present):
  docs/aio-hero-windows-raw.png   # real WT screenshot from the user

Fallback:
  docs/aio-hero.png               # scripted Textual export

Outputs:
  docs/aio-hero-windows.png
  docs/aio-hero-macos.png
  docs/aio-hero-duo.png

    .venv/Scripts/python.exe scripts/frame_hero.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
RAW_WIN = ROOT / "docs" / "aio-hero-windows-raw.png"
SRC_FALLBACK = ROOT / "docs" / "aio-hero.png"
OUT_WIN = ROOT / "docs" / "aio-hero-windows.png"
OUT_MAC = ROOT / "docs" / "aio-hero-macos.png"
OUT_DUO = ROOT / "docs" / "aio-hero-duo.png"
OUT_HERO = ROOT / "docs" / "aio-hero.png"

BG = (13, 17, 23)
TITLE_MAC = (40, 42, 46)
BORDER = (48, 54, 61)
CAPTION = (230, 230, 230)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("segoeui.ttf", "SegoeUI.ttf", "arial.ttf", "Arial.ttf", "calibri.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _round_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _load_source() -> Image.Image:
    src = RAW_WIN if RAW_WIN.is_file() else SRC_FALLBACK
    if not src.is_file():
        raise SystemExit(f"missing source image ({RAW_WIN.name} or {SRC_FALLBACK.name})")
    print(f"source {src}")
    return Image.open(src).convert("RGB")


def _pad_windows(content: Image.Image) -> Image.Image:
    """Keep the real Windows Terminal shot; light outer matte only."""
    w, h = content.size
    framed = Image.new("RGB", (w + 24, h + 24), BG)
    mask = _round_mask((w, h), 6)
    tmp = Image.new("RGB", (w, h), BG)
    tmp.paste(content, (0, 0))
    framed.paste(tmp, (12, 12), mask)
    return framed


def _frame_macos(content: Image.Image) -> Image.Image:
    """Same TUI pixels, macOS traffic-light chrome (app is identical cross-platform)."""
    title_h = 36
    pad = 1
    w, h = content.size
    out_w, out_h = w + pad * 2, h + title_h + pad
    canvas = Image.new("RGB", (out_w, out_h), BORDER)
    panel = Image.new("RGB", (w, out_h - pad), TITLE_MAC)
    canvas.paste(panel, (pad, 0))
    canvas.paste(content, (pad, title_h))

    draw = ImageDraw.Draw(canvas)
    for i, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        x = 16 + i * 18
        draw.ellipse((x, 12, x + 12, 24), fill=color)
    font = _font(14)
    label = "aio - Terminal"
    tw = draw.textlength(label, font=font) if hasattr(draw, "textlength") else len(label) * 7
    draw.text(((out_w - tw) / 2, 10), label, fill=CAPTION, font=font)

    framed = Image.new("RGB", (out_w + 24, out_h + 24), BG)
    mask = _round_mask((out_w, out_h), 10)
    tmp = Image.new("RGB", (out_w, out_h), BG)
    tmp.paste(canvas, (0, 0))
    framed.paste(tmp, (12, 12), mask)
    return framed


def _side_by_side(win: Image.Image, mac: Image.Image, gap: int = 24) -> Image.Image:
    target_h = min(win.height, mac.height, 560)

    def scale(im: Image.Image) -> Image.Image:
        ratio = target_h / im.height
        return im.resize((max(1, int(im.width * ratio)), target_h), Image.Resampling.LANCZOS)

    a, b = scale(win), scale(mac)
    duo = Image.new("RGB", (a.width + b.width + gap + 40, target_h + 40), BG)
    duo.paste(a, (20, 20))
    duo.paste(b, (20 + a.width + gap, 20))
    return duo


def main() -> None:
    raw = _load_source()
    win = _pad_windows(raw)
    mac = _frame_macos(raw)
    duo = _side_by_side(win, mac)

    win.save(OUT_WIN, optimize=True)
    mac.save(OUT_MAC, optimize=True)
    duo.save(OUT_DUO, optimize=True)
    # Keep hero.png as the authentic Windows shot for single-image use.
    raw.save(OUT_HERO, optimize=True)
    print(f"wrote {OUT_WIN}")
    print(f"wrote {OUT_MAC}")
    print(f"wrote {OUT_DUO}")
    print(f"wrote {OUT_HERO}")


if __name__ == "__main__":
    main()
