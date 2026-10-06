"""应用图标重绘（plus.3）：HoldexarPlus 品牌图标 = 圆角方 + H + 加号 + PLUS 字样。

设计约定：
- 深色版（logo_dark / app.ico / favicon）：深空蓝渐变底 + 白 H + 亮蓝加号，
  深浅两版同一构图，仅底色与前景反色；
- 小尺寸（≤48px）不带 PLUS 字样（16px 下必然糊成一团），只留 H+ 单标；
- ico 多帧由大图 lanczos 缩出（Pillow append_images），不发虚的关键帧
  （16/32）单独过一遍高对比锐化。

用法：server/.venv/Scripts/python.exe scripts/make_icons.py
产物：web/public/logo.ico、desktop/app.ico、
     web/public/assets/logo_dark.ico、web/public/assets/logo_light.ico、
     web/public/assets/logo_dark.png、web/public/assets/logo_light.png
     （PNG 128px 供侧栏 <img> 品牌位显示——ICO 走 <img> 时浏览器选帧有
     运气成分，常挑 16/32 小帧导致模糊；ICO 保留给原生壳与 favicon。）
（另在 %TEMP% 落一张 preview.png 供人工目检，不入库。）
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SIZE = 512
RADIUS = 118  # ≈23% 圆角（squircle 观感）

FONT = Path(r"C:\Windows\Fonts\segoeuib.ttf")

# 深色版（默认 / 深色主题侧边栏 / exe 图标）
DARK = {
    "bg_top": (35, 50, 71),     # #233247
    "bg_bottom": (16, 24, 34),  # #101822
    "glyph": (242, 246, 250),   # #f2f6fa
    "plus": (78, 169, 224),     # #4ea9e0 亮蓝
    "wordmark": (143, 167, 189),
}
# 浅色版（浅色主题侧边栏）
LIGHT = {
    "bg_top": (245, 247, 250),  # #f5f7fa
    "bg_bottom": (221, 229, 238),
    "glyph": (22, 33, 46),      # #16212e
    "plus": (47, 127, 193),     # #2f7fc1
    "wordmark": (90, 107, 125),
}

ICO_SIZES = [256, 128, 64, 48, 32, 24, 16]
SMALL_SIZES = {48, 32, 24, 16}  # 这些帧不带 PLUS 字样


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def _gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    base = Image.new("RGB", (size, size), top)
    px = base.load()
    for y in range(size):
        t = y / (size - 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return base


def _master(palette: dict, with_wordmark: bool) -> Image.Image:
    img = _gradient(SIZE, palette["bg_top"], palette["bg_bottom"]).convert("RGBA")

    # 圆角蒙版
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], RADIUS, fill=255)
    img.putalpha(mask)

    draw = ImageDraw.Draw(img)

    # ── H 单标：视觉中心略偏左下，给右上的加号留位 ──
    h_font = _font(300)
    h_pos = (118, 96)
    draw.text(h_pos, "H", font=h_font, fill=palette["glyph"])

    # ── 加号：压在 H 右肩，同一支粗字体，亮蓝（plus 语义 = Plus 版本）──
    plus_font = _font(190)
    draw.text((318, 62), "+", font=plus_font, fill=palette["plus"])

    # ── PLUS 字样：仅中大尺寸帧携带（小帧上必然糊掉）──
    if with_wordmark:
        word = "P L U S"
        w_font = _font(52)
        bbox = draw.textbbox((0, 0), word, font=w_font)
        w = bbox[2] - bbox[0]
        draw.text(((SIZE - w) // 2, 388), word, font=w_font, fill=palette["wordmark"])

    return img


def _frame(master: Image.Image, size: int) -> Image.Image:
    img = master.resize((size, size), Image.LANCZOS)
    if size <= 32:
        # 最小两档：轻锐化保笔画（缩到 16px 时 H 的竖笔容易发糊）
        img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=60, threshold=2))
    return img


def _save_ico(master: Image.Image, path: Path) -> None:
    frames = []
    for size in ICO_SIZES:
        src = master if size not in SMALL_SIZES else master_small_for(master)
        frames.append(_frame(src, size))
    frames[0].save(path, format="ICO", append_images=frames[1:])
    print(f"  {path.name}: {path.stat().st_size} bytes")


def master_small_for(master: Image.Image) -> Image.Image:
    """小尺寸帧用无字样母版；母版按调色板缓存（同 palette 复用同一张）。"""
    return _small_cache[id(master)]


_small_cache: dict[int, Image.Image] = {}


def build(palette: dict, outputs: list[Path]) -> Image.Image:
    large = _master(palette, with_wordmark=True)
    small = _master(palette, with_wordmark=False)
    _small_cache[id(large)] = small
    for out in outputs:
        out.parent.mkdir(parents=True, exist_ok=True)
        _save_ico(large, out)
    return large


def main() -> None:
    font_exists = FONT.exists()
    if not font_exists:
        raise SystemExit(f"缺少字体：{FONT}（脚本假定 Windows 内置 Segoe UI Bold）")

    dark = build(DARK, [ROOT / "web" / "public" / "logo.ico", ROOT / "desktop" / "app.ico"])
    build(LIGHT, [ROOT / "web" / "public" / "assets" / "logo_light.ico"])

    # logo_dark：与 app.ico 同构图（深空蓝），单独产出
    dark_out = ROOT / "web" / "public" / "assets" / "logo_dark.ico"
    dark_out.parent.mkdir(parents=True, exist_ok=True)
    _save_ico(dark, dark_out)

    # 侧栏品牌位 PNG（128px，带 PLUS 字样档）：<img> 引用清晰可控
    for palette, name in ((DARK, "logo_dark.png"), (LIGHT, "logo_light.png")):
        png = _master(palette, with_wordmark=True).resize((128, 128), Image.LANCZOS)
        png_out = ROOT / "web" / "public" / "assets" / name
        png.save(png_out)
        print(f"  {png_out.name}: {png_out.stat().st_size} bytes")

    # 关闭弹窗标题栏专用（48px 无字样小标：20×20 显示位放全幅版必糊，
    # 只留 H+ 单标；GDI+ 对 PNG 直接解码取首帧，无 ICO 选帧问题）
    for palette, name in ((DARK, "logo_titlebar_dark.png"), (LIGHT, "logo_titlebar_light.png")):
        tb = _master(palette, with_wordmark=False).resize((48, 48), Image.LANCZOS)
        tb_out = ROOT / "web" / "public" / "assets" / name
        tb.save(tb_out)
        print(f"  {tb_out.name}: {tb_out.stat().st_size} bytes")

    # 预览图（不入库）：人工目检用
    preview = Path(tempfile.gettempdir()) / "holdexarplus_icon_preview.png"
    sheet = Image.new("RGBA", (SIZE * 2 + 24, SIZE), (128, 128, 128, 255))
    sheet.paste(dark, (0, 0))
    sheet.paste(_master(LIGHT, with_wordmark=True), (SIZE + 24, 0))
    sheet.save(preview)
    print(f"preview: {preview}")


if __name__ == "__main__":
    main()
