"""智慧树杀手（ZhiHuiShu_Killer）—— 程序入口。

双击 run.bat 或 ``python main.py`` 都可以启动。
"""

from __future__ import annotations

import sys


def _enable_dpi_awareness() -> None:
    """开启 Windows 的 DPI 感知。

    不做这一步的话，系统会把整个窗口当成位图去拉伸，
    高分屏上文字会明显发虚（"清晰度低"多半就是这个原因，不是字体问题）。
    开启后 tkinter 会按真实 DPI 渲染字体，界面既清晰、大小也正确。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            # PROCESS_PER_MONITOR_DPI_AWARE
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _check_dependencies() -> str | None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return (
            "缺少 playwright 依赖。\n\n"
            "请在命令行执行：\n"
            "    python -m pip install -r requirements.txt"
        )
    return None


def _set_console_title() -> None:
    """把控制台窗口标题改成程序名。

    不在 run.bat 里用 ``title`` 写中文：批处理里出现非 ASCII 字符会让
    cmd.exe 按代码页读错文件（实测把 ``chcp 65001`` 断成了 ``01``）。
    这里走 Win32 API 设标题，完全不受代码页影响。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW("智慧树杀手 ZhiHuiShu_Killer")
    except Exception:
        pass


def _ensure_std_streams() -> None:
    """pythonw.exe（无控制台）启动时 sys.stdout/stderr 是 None。

    有些库（warnings、playwright 等）会往 stderr 写东西，写到 None 上会直接抛错，
    所以这里兜底：stdout 丢进黑洞，stderr 追加到 data/logs/ui-errors.log，
    这样即使没有控制台，出问题也能在日志里看到。
    """
    import os

    if sys.stdout is None:
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        except Exception:
            pass
    if sys.stderr is None:
        stream = None
        try:
            from zhskiller import paths

            stream = open(paths.logs_dir() / "ui-errors.log", "a", encoding="utf-8")
        except Exception:
            stream = None
        if stream is None:
            try:
                stream = open(os.devnull, "w", encoding="utf-8")
            except Exception:
                stream = None
        if stream is not None:
            sys.stderr = stream


def _fatal(title: str, exc: BaseException) -> None:
    """启动/运行阶段的致命错误：写日志 + 弹窗，别让用户对着空气发呆。"""
    import traceback

    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        from zhskiller import paths

        with open(paths.logs_dir() / "ui-errors.log", "a", encoding="utf-8") as handle:
            handle.write(f"\n==== {title} ====\n{detail}\n")
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            title,
            f"{exc}\n\n详细信息已写入 data/logs/ui-errors.log。",
        )
        root.destroy()
    except Exception:
        pass
    if sys.stderr is not None:
        try:
            sys.stderr.write(detail)
            sys.stderr.flush()
        except Exception:
            pass


def main() -> int:
    _ensure_std_streams()
    _set_console_title()
    problem = _check_dependencies()
    if problem:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("依赖缺失", problem)
            root.destroy()
        except Exception:
            pass
        print(problem)
        return 2

    try:
        from ui.app import App
    except Exception as exc:            # 依赖装了一半 / 文件缺失
        _fatal("启动失败", exc)
        return 3

    _enable_dpi_awareness()
    try:
        App().mainloop()
    except Exception as exc:            # 后台线程之外的界面异常
        _fatal("运行出错", exc)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
