"""运行过程中需要用户参与的几个弹窗。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# 弹窗一律贴屏幕右下角，避免盖住浏览器中间区域（扫码二维码就在中间）
SCREEN_MARGIN = 32
BOTTOM_MARGIN = 96


def place_bottom_right(window) -> None:
    window.update_idletasks()
    width = max(window.winfo_width(), window.winfo_reqwidth())
    height = max(window.winfo_height(), window.winfo_reqheight())
    x = max(0, window.winfo_screenwidth() - width - SCREEN_MARGIN)
    y = max(0, window.winfo_screenheight() - height - BOTTOM_MARGIN)
    window.geometry(f"+{x}+{y}")


class _Modal(tk.Toplevel):
    def __init__(self, master, title: str):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _event: self._on_close())

    def _on_close(self) -> None:
        self.result = None
        self.destroy()

    def show(self):
        # 顺序很重要：必须先让窗口真正映射出来，否则 grab_set 会抛
        # "grab failed: window not viewable"，异常一旦冒泡到 tkinter 的
        # after 回调就会把消息泵整条链打断，表现为界面卡死。
        self.deiconify()
        self.update_idletasks()
        place_bottom_right(self)
        self.update_idletasks()
        try:
            self.wait_visibility()
        except tk.TclError:
            pass
        # 浏览器窗口刚打开时会抢焦点，弹窗必须置顶，否则用户以为程序卡死了
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass
        try:
            self.grab_set()
        except tk.TclError:
            pass
        try:
            self.bell()
        except tk.TclError:
            pass
        self.wait_window()
        return self.result


class Notice(tk.Toplevel):
    """右下角的提示浮窗：不抢焦点、不阻塞，程序自己也会继续往下走。

    用于「请在浏览器里登录」「请手动过验证码」这类只需要知会用户的场景。
    用户点按钮会立刻回填结果；程序自己检测到状态变化时也能把这个浮窗关掉。
    """

    def __init__(self, master, title: str, message: str, on_answer,
                 ok_text: str = "我已完成", cancel_text: str | None = "取消",
                 ok_value=True, cancel_value=False):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self._on_answer = on_answer
        self._ok_value = ok_value
        self._cancel_value = cancel_value
        self._done = False

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=title, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        ttk.Label(body, text=message, justify="left", wraplength=380).pack(
            anchor="w", pady=(6, 12)
        )
        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Button(row, text=ok_text, command=self._ok).pack(side="right")
        if cancel_text:
            ttk.Button(row, text=cancel_text, command=self._cancel).pack(side="right", padx=8)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self._on_close())

    def popup(self) -> None:
        self.update_idletasks()
        place_bottom_right(self)
        self.update_idletasks()
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            self.lift()
        except tk.TclError:
            pass
        try:
            self.bell()
        except tk.TclError:
            pass

    def _finish(self, value) -> None:
        if self._done:
            return
        self._done = True
        try:
            self._on_answer(value)
        finally:
            self.destroy()

    def _ok(self) -> None:
        self._finish(self._ok_value)

    def _cancel(self) -> None:
        self._finish(self._cancel_value)

    def _on_close(self) -> None:
        self._finish(self._cancel_value)


class FirstRunDialog(_Modal):
    """第一次发现没有账号密码时的三选一。"""

    def __init__(self, master, message: str):
        super().__init__(master, "是否在程序内保存智慧树账号密码")
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=message, justify="left", wraplength=460).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(16, 0))
        ttk.Button(row, text="同意（现在填写）", command=lambda: self._done("agree")).pack(
            side="left"
        )
        ttk.Button(row, text="拒绝", command=lambda: self._done("decline")).pack(
            side="left", padx=8
        )
        ttk.Button(row, text="不再提醒", command=lambda: self._done("mute")).pack(side="left")

    def _done(self, value: str) -> None:
        self.result = value
        self.destroy()


class CredentialsDialog(_Modal):
    """输入智慧树账号密码。"""

    def __init__(self, master, username: str = "", password: str = ""):
        super().__init__(master, "输入智慧树账号密码")
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="账号").grid(row=0, column=0, sticky="w", pady=4)
        self.username = ttk.Entry(body, width=34)
        self.username.grid(row=0, column=1, pady=4)
        self.username.insert(0, username)
        ttk.Label(body, text="密码").grid(row=1, column=0, sticky="w", pady=4)
        self.password = ttk.Entry(body, width=34, show="●")
        self.password.grid(row=1, column=1, pady=4)
        self.password.insert(0, password)
        self.show_password = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            body,
            text="显示密码",
            variable=self.show_password,
            command=self._toggle_password,
        ).grid(row=2, column=1, sticky="w", pady=(0, 6))
        ttk.Label(
            body,
            text="凭据会用 Windows 凭据保护（DPAPI）加密后保存在 data/accounts.dat。",
            foreground="#666666",
            wraplength=380,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 8))
        row = ttk.Frame(body)
        row.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(row, text="确定", command=self._submit).pack(side="left")
        ttk.Button(row, text="取消", command=self._on_close).pack(side="left", padx=8)
        self.bind("<Return>", lambda _event: self._submit())
        self.username.focus_set()

    def _toggle_password(self) -> None:
        self.password.configure(show="" if self.show_password.get() else "●")

    def _submit(self) -> None:
        username = self.username.get().strip()
        password = self.password.get()
        if not username or not password:
            self.result = None
            self.destroy()
            return
        self.result = {"username": username, "password": password}
        self.destroy()


class NotifyDialog(_Modal):
    """只提示，等用户点确认。"""

    def __init__(self, master, title: str, message: str, ok_text: str = "我已完成",
                 cancel_text: str | None = None, ok_value=True, cancel_value=None,
                 timeout: float | None = None, timeout_value=None):
        super().__init__(master, title)
        self._ok_value = ok_value
        self._cancel_value = cancel_value
        self._timeout_value = timeout_value
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=message, justify="left", wraplength=460).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(16, 0))
        ttk.Button(row, text=ok_text, command=self._ok).pack(side="right")
        if cancel_text:
            ttk.Button(row, text=cancel_text, command=self._cancel).pack(side="right", padx=8)
        if timeout:
            # 无人值守时不能一直挂着，超时按默认值走
            self._timeout_var = tk.StringVar(
                value=f"（{int(timeout)} 秒内未选择将自动按默认处理）"
            )
            ttk.Label(body, textvariable=self._timeout_var, foreground="#8a6d3b").pack(
                anchor="w", pady=(10, 0)
            )
            self._remaining = int(timeout)

            def tick():
                self._remaining -= 1
                if self._remaining <= 0:
                    self.result = self._timeout_value
                    self.destroy()
                    return
                self._timeout_var.set(
                    f"（{self._remaining} 秒内未选择将自动按默认处理）"
                )
                self.after(1000, tick)

            self.after(1000, tick)

    def _ok(self) -> None:
        self.result = self._ok_value
        self.destroy()

    def _cancel(self) -> None:
        self.result = self._cancel_value
        self.destroy()


def build_dialog(master, kind: str, payload: dict):
    """按 kind 造出对应的弹窗对象（尚未显示）。"""
    if kind == "first_run":
        return FirstRunDialog(master, payload.get("message", ""))
    if kind == "credentials":
        return CredentialsDialog(master)
    if kind == "manual_login":
        first = payload.get("first", True)
        if first:
            message = payload.get("message", "")
            title = "需要手动登录"
        else:
            message = "程序没有检测到登录成功，请在浏览器中完成登录后再次点击「确定」。"
            title = "仍未检测到登录状态"
        return NotifyDialog(
            master,
            title,
            message,
            ok_text="确定（已登录）",
            cancel_text="取消",
            ok_value=True,
            cancel_value=False,
        )
    if kind == "captcha":
        return NotifyDialog(
            master,
            "需要完成人机验证",
            payload.get("message", "请在浏览器中完成滑块验证。"),
            ok_text="我已完成",
        )
    if kind == "quiz_manual":
        message = payload.get("message") or (
            f"检测到{payload.get('label', '练习')}，按当前设置需要你手动作答。\n"
            f"题目：{payload.get('question', '')[:200]}"
        )
        return NotifyDialog(
            master,
            "请手动作答",
            message,
            ok_text="我已完成",
        )
    if kind == "chapter_quiz":
        return NotifyDialog(
            master,
            "检测到章节练习",
            payload.get("message", ""),
            ok_text="我已完成",
            cancel_text="跳过并继续",
            ok_value="done",
            cancel_value="skip",
        )
    if kind == "ai_unavailable":
        # 按钮文案由调用方给（随堂练习/章节测验的兜底方式不同）
        return NotifyDialog(
            master,
            "AI 接口用不了",
            payload.get("message", "AI 无法作答，请选择接下来的方式。"),
            ok_text=payload.get("ok_text", "继续用 AI"),
            cancel_text=payload.get("cancel_text", "改用随机选择"),
            ok_value=payload.get("ok_value", "retry_ai"),
            cancel_value=payload.get("cancel_value", "random"),
            timeout=120,
            timeout_value=payload.get("timeout_value", "timeout"),
        )
    return NotifyDialog(master, "提示", str(payload), ok_text="确定")


# 这些只是"知会用户"，程序自己会继续判断，所以用右下角浮窗而不是模态弹窗
NOTICE_KINDS = {
    "manual_login", "captcha", "quiz_manual", "busy_notice", "quiz_review",
}


def build_notice(master, kind: str, payload: dict, on_answer) -> Notice:
    """造一个右下角浮窗（不阻塞用户操作浏览器）。"""
    if kind == "manual_login":
        return Notice(
            master,
            "请在浏览器里登录",
            payload.get("message", "")
            or "请在浏览器里完成智慧树登录，登录成功后程序会自动继续。",
            on_answer,
            ok_text="确定（我已登录）",
            cancel_text="取消",
            ok_value=True,
            cancel_value=False,
        )
    if kind == "captcha":
        return Notice(
            master,
            "需要完成人机验证",
            payload.get("message", "请在浏览器里完成滑块验证，完成后程序会自动继续。"),
            on_answer,
            ok_text="我已完成",
            cancel_text=None,
            ok_value=True,
            cancel_value=False,
        )
    if kind == "quiz_manual":
        message = payload.get("message") or (
            f"检测到{payload.get('label', '练习')}，请手动作答。\n"
            f"题目：{payload.get('question', '')[:200]}"
        )
        return Notice(
            master,
            "请手动作答",
            message,
            on_answer,
            ok_text="我已完成",
            cancel_text=None,
            ok_value=True,
            cancel_value=False,
        )
    if kind == "busy_notice":
        return Notice(
            master,
            "视频被反复暂停",
            payload.get("message", "可能有未识别的弹窗，请到浏览器里看一眼。"),
            on_answer,
            ok_text="知道了",
            cancel_text=None,
            ok_value=None,
            cancel_value=None,
        )
    if kind == "quiz_review":
        return Notice(
            master,
            "章节测验已作答，等你提交",
            payload.get("message", "程序已完成作答，请到浏览器里核对后自行提交。"),
            on_answer,
            ok_text="知道了",
            cancel_text=None,
            ok_value=None,
            cancel_value=None,
        )
    return Notice(master, "提示", str(payload), on_answer, ok_text="确定", cancel_text=None)
