"""给界面截图加"编号标注 + 底部说明条"，生成 README 用的配图。

用法（一次性脚本，不属于程序运行依赖）：
    python docs/images/make_figures.py <原图目录> <输出目录>

原图需要放在一起，文件名分别以 ui-1 / ui-2 / ui-3 开头。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

ACCENT = (26, 115, 232)
TITLE_COLOR = (26, 77, 143)
TEXT_COLOR = (58, 58, 58)
BG = (255, 255, 255)
BORDER = (214, 219, 226)

# 每张图：标题、编号说明、编号标记位置（按图片宽高的比例）
FIGURES = [
    {
        "key": "ui-1",
        "title": "图 1 · 课程与浏览器：填链接 → 选浏览器 → 点「开始学习」",
        "bullets": [
            "① 运行模式：默认「自动观看未完成的课程视频」；选「仅完成章节测验」就只做测验、不刷视频（仅本次运行有效）",
            "② 课程播放页链接：每行一个，填能看到视频的播放页地址",
            "③ 浏览器：默认新开一个（登录状态会记住），也可以接管你自己已经打开的浏览器窗口",
            "④ 控制区：开始学习 / 停止 / 暂停（暂停只是停下来，页面保持不动）；下面是实时运行日志",
        ],
        "badges": [("1", 0.945, 0.163), ("2", 0.945, 0.324),
                   ("3", 0.945, 0.415), ("4", 0.280, 0.648)],
    },
    {
        "key": "ui-2",
        "title": "图 2 · 播放与答题：倍速/静音、保活、两类答题、人机验证",
        "bullets": [
            "① 播放参数：默认 1.5 倍速 + 浏览器层面静音（不动播放器音量，避免被平台判定静音而不计进度）",
            "② 保活：「停滞判定」秒数内没有进度就自动排查验证码/弹题/错误弹窗；「模拟真人鼠标操作」用于触发学习时长上报",
            "③ 随堂练习：每个视频都会弹、不计分但必须答完；作答方式可选 AI 识别 / 随机选择 / 手动作答",
            "④ 章节测验：默认完全不碰（不勾选就不作答、也不打扰）；勾「自动完成」才作答，还可选自动提交、复查答题结果",
            "⑤ 人机验证：自动过滑块并实时对准缺口；处理不了时弹窗喊你手动完成",
        ],
        "badges": [("1", 0.305, 0.156), ("2", 0.945, 0.214),
                   ("3", 0.545, 0.296), ("4", 0.545, 0.352),
                   ("5", 0.935, 0.290)],
    },
    {
        "key": "ui-3",
        "title": "图 3 · 账号与 AI：自动登录 + AI 答题接口（都可留空）",
        "bullets": [
            "① 登录方式：账号密码，或学号登录（机构 + 学号 + 密码）",
            "② 使用自动登录：勾选后由程序自己登录（含滑块验证）；失败会自动转成手动登录",
            "③ 保存到本机：账号密码在本机加密保存，下次运行免输入；随时可以清除",
            "④ AI 答题接口：任何 OpenAI 兼容网关（OpenAI / DeepSeek / 通义 / 本地 Ollama 等）；留空则不使用 AI",
        ],
        "badges": [("1", 0.950, 0.168), ("2", 0.520, 0.301),
                   ("3", 0.300, 0.353), ("4", 0.235, 0.396)],
    },
]

# 图 3 里需要打码的敏感区域（账号、已保存的账号）
PRIVATE_BOXES = {
    "ui-3": [(0.042, 0.214, 0.975, 0.241), (0.183, 0.343, 0.280, 0.366)],
}


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.truetype(FONT_REG, size)


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        if draw.textlength(current + char, font=font) <= max_width or not current:
            current += char
            continue
        lines.append(current)
        current = char
    if current:
        lines.append(current)
    return lines


def draw_badge(draw: ImageDraw.ImageDraw, x: float, y: float, radius: float,
               text: str) -> None:
    draw.ellipse((x - radius - 4, y - radius - 4, x + radius + 4, y + radius + 4),
                 fill=(255, 255, 255))
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=ACCENT)
    font = load_font(FONT_BOLD, int(radius * 1.35))
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((x - (box[2] - box[0]) / 2 - box[0],
               y - (box[3] - box[1]) / 2 - box[1]),
              text, font=font, fill=(255, 255, 255))


def pixelate(image: Image.Image, box: tuple[float, float, float, float],
             blocks: int = 14) -> None:
    left, top, right, bottom = (int(round(value)) for value in box)
    region = image.crop((left, top, right, bottom))
    small = region.resize((max(2, blocks), max(2, blocks)), Image.BILINEAR)
    image.paste(small.resize(region.size, Image.NEAREST), (left, top))


def build(figure: dict, source: Path, target: Path, private: bool = False) -> None:
    image = Image.open(source).convert("RGB")
    if private:
        for box in PRIVATE_BOXES.get(figure["key"], []):
            pixelate(image, (box[0] * image.width, box[1] * image.height,
                             box[2] * image.width, box[3] * image.height))
    width, height = image.size
    title_font = load_font(FONT_BOLD, int(width * 0.0195))
    body_font = load_font(FONT_REG, int(width * 0.0160))
    badge_radius = width * 0.0155

    probe = ImageDraw.Draw(image)
    padding = int(width * 0.018)
    max_text_width = width - padding * 2 - int(width * 0.02)
    wrapped = [wrap(probe, line, body_font, max_text_width) for line in figure["bullets"]]
    line_height = int(width * 0.0215)
    band_height = padding * 2 + int(width * 0.030) + line_height * (
        sum(len(lines) for lines in wrapped) + 1
    )

    canvas = Image.new("RGB", (width, height + band_height), BG)
    canvas.paste(image, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.line((0, height, width, height), fill=BORDER, width=2)

    y = height + padding
    draw.text((padding, y), figure["title"], font=title_font, fill=TITLE_COLOR)
    y += int(width * 0.030)
    for lines in wrapped:
        for line in lines:
            draw.text((padding + int(width * 0.006), y), line,
                      font=body_font, fill=TEXT_COLOR)
            y += line_height

    for text, nx, ny in figure["badges"]:
        draw_badge(draw, nx * width, ny * height, badge_radius, text)

    canvas.save(target, optimize=True)
    print(f"  {target.name}  {canvas.width}x{canvas.height}")


def main() -> int:
    source_dir = Path(sys.argv[1])
    target_dir = Path(sys.argv[2])
    target_dir.mkdir(parents=True, exist_ok=True)
    for figure in FIGURES:
        matches = sorted(source_dir.glob(f"{figure['key']}*.png"))
        if not matches:
            print(f"找不到 {figure['key']} 的原图，跳过")
            continue
        build(figure, matches[0], target_dir / f"{figure['key']}.png")
        if figure["key"] in PRIVATE_BOXES:
            build(figure, matches[0], target_dir / f"{figure['key']}-private.png",
                  private=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
