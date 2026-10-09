"""抓一张程序界面截图，用于更新 README 里的配图。

用法（一次性开发脚本，不属于程序运行依赖）：
    python docs/images/capture_ui.py <页签序号 0/1/2> <输出 png>

会打开程序窗口（置顶）、切到指定页签、等它渲染完再截图，然后自动关闭。
抓的是干净的空配置，不会带上任何账号 / 接口信息。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import main as app_main  # noqa: E402
import zhskiller.paths as paths_mod  # noqa: E402
from ui.app import App  # noqa: E402

# 必须和正式启动一样先开 DPI 感知：否则 tkinter 拿到的是"被系统缩放过的"坐标，
# 而截图走的是物理像素，抓出来的图右边会被切掉一截。
app_main._enable_dpi_awareness()

# 用临时空目录当 data/：这样截图里不会出现你的课程链接、账号、接口地址
_TMP_DATA = Path(tempfile.mkdtemp(prefix="zhs_shot_"))
paths_mod.data_dir = lambda: _TMP_DATA

CAPTURE_PS = r"""
Add-Type -AssemblyName System.Drawing
$x = __X__; $y = __Y__; $w = __W__; $h = __H__
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($x, $y, 0, 0, $bmp.Size)
$bmp.Save('__OUT__', [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "$w x $h"
"""


def main() -> int:
    tab = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    out = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT / "docs/images/shot.png"
    app = App()
    screen_w, screen_h = app.winfo_screenwidth(), app.winfo_screenheight()
    width = min(1140, screen_w - 20)
    height = min(780, screen_h - 60)
    app.geometry(f"{width}x{height}+10+10")
    app.update_idletasks()
    app.notebook.select(tab)
    # AI 那几个框里是代码里的默认占位值，截图时清空，避免看起来像在推荐某家服务
    for attr in ("ai_base_entry", "ai_model_entry", "ai_key_entry"):
        entry = getattr(app, attr, None)
        if entry is not None:
            entry.delete(0, "end")
    app.update_idletasks()
    app.attributes("-topmost", True)
    app.lift()
    # 多喂几次事件循环，保证滚动区/表格都画完
    for _ in range(40):
        app.update()
        time.sleep(0.06)
    app.update_idletasks()
    box = (app.winfo_rootx(), app.winfo_rooty(),
           app.winfo_width(), app.winfo_height())
    out.parent.mkdir(parents=True, exist_ok=True)
    script = CAPTURE_PS
    for key, value in (
        ("__X__", box[0]), ("__Y__", box[1]), ("__W__", box[2]), ("__H__", box[3]),
    ):
        script = script.replace(key, str(int(value)))
    script = script.replace("__OUT__", str(out).replace("'", "''"))
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True,
    )
    print(result.stdout.strip() or result.stderr.strip())
    app.destroy()
    return 0 if out.exists() else 1


if __name__ == "__main__":
    sys.exit(main())
