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

# Windows 控制台默认 GBK，打印 ✅ 会抛 UnicodeEncodeError，这里统一改成 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import tkinter as tk  # noqa: E402

from ui.app import App  # noqa: E402
from ui.dialogs import AboutDialog, FirstRunDialog, NotifyDialog  # noqa: E402


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

        print("5. 「关于 / 检查更新」弹窗：不阻塞、能显示更新日志、能亮出更新按钮")
        from zhskiller import updater

        opened = {"repo": 0}
        about = AboutDialog(
            app, "1.0.2", updater.REPO_URL,
            on_open_repo=lambda: opened.__setitem__("repo", opened["repo"] + 1),
            on_check=lambda _d: None,
            on_update=lambda _d, _i: None,
        )
        about.show_async()          # 不能阻塞，否则后台线程的结果回不来
        app.update()
        failures += not check("窗口没被销毁", about.winfo_exists() == 1)
        failures += not check(
            "版本号写在界面上（1.0.2）",
            "1.0.2" in about.winfo_children()[0].winfo_children()[0].cget("text"),
        )
        info = updater.UpdateInfo(version="1.0.5", tag="v1.0.5",
                                  notes="修复了 XXX\n新增了 YYY")
        about.offer_update(info)
        app.update()
        failures += not check(
            "更新日志显示出来了",
            "修复了 XXX" in about.notes.get("1.0", "end"),
        )
        failures += not check(
            "更新按钮亮起且写明目标版本",
            str(about.update_button.cget("state")) == "normal"
            and "1.0.5" in str(about.update_button.cget("text")),
        )
        about._open_repo()
        failures += not check("点「打开项目主页」会回调", opened["repo"] == 1)
        about._on_close()
        app.update()

        print("6. 「关于」是菜单栏里独立的一栏（不在「工具」里）")

        def menu_labels(menu) -> list[str]:
            """菜单里各栏的文字（分隔符、撕下栏之类没有文字的就跳过）。"""
            texts = []
            for index in range(int(menu.index("end")) + 1):
                try:
                    texts.append(str(menu.entrycget(index, "label")))
                except tk.TclError:
                    continue
            return texts

        menubar = app.menubar
        labels = menu_labels(menubar)
        failures += not check(
            f"菜单栏有「工具」和「关于」两栏（实际 {labels}）",
            labels[:2] == ["工具", "关于"],
        )
        tool_menu = app.nametowidget(menubar.entrycget(labels.index("工具"), "menu"))
        tool_labels = menu_labels(tool_menu)
        failures += not check(
            "「工具」里已经没有任何「关于」相关项",
            not any("关于" in str(text) for text in tool_labels),
        )
        about_labels = menu_labels(app.about_menu)
        failures += not check(
            f"「关于」里有 关于本程序 / 检查更新 / 打开 GitHub 项目主页（实际 {about_labels}）",
            "关于本程序" in about_labels
            and "检查更新" in about_labels
            and any("GitHub" in str(text) for text in about_labels),
        )
    finally:
        app.destroy()
    print()
    print("全部通过 ✅" if failures == 0 else f"有 {failures} 项失败 ❌")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
