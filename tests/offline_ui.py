"""离线自测：界面消息泵的健壮性与弹窗可见性。

对应两个已修复的真实问题：
  * 弹窗 grab_set 在窗口还没映射时抛 TclError -> 消息泵整条链断掉 -> 界面卡死；
  * after 回调里的异常没有兜底，任何一条消息出错都会让界面再也刷不动。

运行： .venv\\Scripts\\python.exe tests\\offline_ui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from ui.app import App  # noqa: E402
from ui.dialogs import FirstRunDialog, NotifyDialog  # noqa: E402


def check(name: str, condition: bool) -> bool:
    print(("  [通过] " if condition else "  [失败] ") + name)
    return condition


def log_text(app: App) -> str:
    return app.log_view.get("1.0", "end")


def main() -> int:
    failures = 0
    app = App()
    try:
        print("1. 弹窗能正常弹出并返回结果（grab_set 不再抛异常）")
        dialog = FirstRunDialog(app, "测试内容")
        app.after(150, lambda: dialog._done("agree"))
        try:
            result = dialog.show()
            raised = None
        except Exception as exc:
            result, raised = None, exc
        failures += not check(f"没有抛异常（{raised!r}）", raised is None)
        failures += not check("返回了选择结果", result == "agree")

        print("2. 通知型弹窗同样正常")
        notify = NotifyDialog(app, "标题", "内容", ok_text="确定", cancel_text="取消",
                              ok_value="done", cancel_value="skip")
        app.after(150, notify._cancel)
        try:
            result = notify.show()
            raised = None
        except Exception as exc:
            result, raised = None, exc
        failures += not check(f"没有抛异常（{raised!r}）", raised is None)
        failures += not check("返回了取消值", result == "skip")

        print("3. 单条消息出错后消息泵仍然存活")
        app.queue.put(("progress", {"lesson_total": 2, "lesson_index": "不是数字"}))
        app.queue.put(("log", "信息", "PUMP_STILL_ALIVE"))
        try:
            app._pump()
            raised = None
        except Exception as exc:
            raised = exc
        failures += not check(f"_pump 没有向外抛异常（{raised!r}）", raised is None)
        failures += not check("后续消息仍被处理", "PUMP_STILL_ALIVE" in log_text(app))
        failures += not check("出错消息被记进日志", "失败" in log_text(app))

        print("4. 界面异常会写入 ui-errors.log")
        from zhskiller import paths

        app._log_ui_error("自测异常", tk.TclError("模拟错误"))
        ui_log = paths.logs_dir() / "ui-errors.log"
        failures += not check("日志文件已生成", ui_log.is_file())
        if ui_log.is_file():
            failures += not check(
                "日志内容包含标识", "自测异常" in ui_log.read_text(encoding="utf-8")
            )
    finally:
        app.destroy()
    print()
    print("全部通过 ✅" if failures == 0 else f"有 {failures} 项失败 ❌")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
