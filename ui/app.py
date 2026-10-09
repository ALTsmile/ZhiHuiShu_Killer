"""主窗口。"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, scrolledtext, ttk

from zhskiller import paths
from zhskiller.bridge import Bridge
from zhskiller.browser import launch_debuggable_browser
from zhskiller.config import Config
from zhskiller.credentials import clear_credentials, load_credentials
from zhskiller.runner import Runner
from zhskiller.version import __version__

from .dialogs import NOTICE_KINDS, build_dialog, build_notice

QUIZ_MODES = [("随机选择", "random"), ("AI 识别", "ai"), ("手动作答", "manual")]
QUIZ_MODE_LABELS = [label for label, _ in QUIZ_MODES]
QUIZ_MODE_VALUES = {label: value for label, value in QUIZ_MODES}
QUIZ_MODE_BY_VALUE = {value: label for label, value in QUIZ_MODES}

LOG_COLORS = {
    "信息": "#111111",
    "完成": "#127a3c",
    "警告": "#b26a00",
    "错误": "#b3261e",
    "调试": "#777777",
}

# 界面统一用的字体（Windows 上微软雅黑最好看；其它系统退回默认）
UI_FONT_FAMILY = "Microsoft YaHei UI"
LOG_FONT_FAMILY = "Consolas"
BASE_FONT_SIZE = 10


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"智慧树杀手 ZhiHuiShu_Killer v{__version__}")
        self._dpi = self._detect_dpi()
        # 默认开大一点，但不超出屏幕（小屏笔记本上就不会把窗口顶出屏幕外）
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(int(1140 * self._dpi), max(900, screen_w - 60))
        height = min(int(780 * self._dpi), max(640, screen_h - 90))
        self.geometry(f"{width}x{height}")
        self.minsize(int(980 * self._dpi), int(640 * self._dpi))
        self._setup_style()

        self.config_model = Config()
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.bridge: Bridge | None = None
        self.worker: threading.Thread | None = None
        self._dialog_open = False
        self._notices: dict[int, object] = {}

        self._build_menu()
        self._build_layout()
        self._load_into_ui()
        self._refresh_credential_state()

        # 任何界面回调里的异常都要落盘，否则只会打到看不见的 stderr 上
        self.report_callback_exception = self._report_callback_exception
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._pump)

    # ------------------------------------------------------------------
    def _detect_dpi(self) -> float:
        """窗口所在屏幕相对 96 DPI 的缩放系数。"""
        try:
            return max(1.0, min(3.0, self.winfo_fpixels("1i") / 96.0))
        except Exception:
            return 1.0

    def _setup_style(self) -> None:
        """统一字体与配色，别让界面显得"简陋"。"""
        try:
            style = ttk.Style(self)
            for theme in ("vista", "winnative", "clam"):
                if theme in style.theme_names():
                    style.theme_use(theme)
                    break
        except Exception:
            style = ttk.Style(self)
        self.option_add("*Font", (UI_FONT_FAMILY, BASE_FONT_SIZE))
        for name, size in (
            ("TkDefaultFont", BASE_FONT_SIZE),
            ("TkTextFont", BASE_FONT_SIZE),
            ("TkMenuFont", BASE_FONT_SIZE),
            ("TkHeadingFont", BASE_FONT_SIZE),
            ("TkTooltipFont", BASE_FONT_SIZE - 1),
        ):
            try:
                tkfont.nametofont(name).configure(family=UI_FONT_FAMILY, size=size)
            except Exception:
                pass
        try:
            style.configure("TNotebook.Tab", padding=(14, 7))
            style.configure("TLabel", padding=(0, 1))
            style.configure("TLabelframe.Label", font=(UI_FONT_FAMILY, BASE_FONT_SIZE, "bold"))
            style.configure("Primary.TButton", font=(UI_FONT_FAMILY, BASE_FONT_SIZE + 1, "bold"))
            style.configure("Hint.TLabel", foreground="#6b7280")
            style.configure("SectionA.TLabel", foreground="#1a4d8f",
                            font=(UI_FONT_FAMILY, BASE_FONT_SIZE, "bold"))
            style.configure("SectionB.TLabel", foreground="#8f1a1a",
                            font=(UI_FONT_FAMILY, BASE_FONT_SIZE, "bold"))
            style.configure("Status.TLabel", foreground="#374151")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        data_menu = tk.Menu(menubar, tearoff=0)
        data_menu.add_command(label="打开数据目录", command=lambda: self._open_path(paths.data_dir()))
        data_menu.add_command(label="打开日志目录", command=lambda: self._open_path(paths.logs_dir()))
        data_menu.add_separator()
        data_menu.add_command(label="删除已保存的账号密码", command=self._forget_credentials)
        data_menu.add_command(label="重置登录状态（下次运行需重新登录）",
                              command=self._reset_login)
        data_menu.add_separator()
        data_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="工具", menu=data_menu)
        self.configure(menu=menubar)

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        # 上下可拖动的分栏：上面设置、下面日志。
        # 用 Panedwindow 而不是固定 grid，是为了让日志区不被设置区挤死，
        # 而且你可以自己拖动中间的分隔条调整比例。
        self.paned = ttk.Panedwindow(root, orient="vertical")
        self.paned.grid(row=0, column=0, sticky="nsew")
        self.paned.bind("<Configure>", lambda _e: self.after(50, self._place_sash))

        top = ttk.Frame(self.paned)
        self.notebook = ttk.Notebook(top)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._build_course_tab()
        self._build_playback_tab()
        self._build_account_tab()
        self.paned.add(top, weight=3)

        bottom = ttk.Frame(self.paned, padding=(0, 6, 0, 0))
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(2, weight=1)
        self.paned.add(bottom, weight=6)

        controls = ttk.Frame(bottom)
        controls.grid(row=0, column=0, sticky="ew")
        self.start_button = ttk.Button(
            controls, text="开始学习", command=self._start, style="Primary.TButton"
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            controls, text="停止", command=self._stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=8)
        self.pause_button = ttk.Button(
            controls, text="暂停", command=self._toggle_pause, state="disabled"
        )
        self.pause_button.pack(side="left")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(
            controls, textvariable=self.status_var, style="Status.TLabel"
        ).pack(side="left", padx=12)
        ttk.Button(controls, text="清空日志", command=self._clear_log).pack(side="right")
        self.progress = ttk.Progressbar(controls, mode="determinate", length=220, maximum=100)
        self.progress.pack(side="right", padx=(0, 8))

        log_head = ttk.Frame(bottom)
        log_head.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(log_head, text="运行日志", style="SectionA.TLabel").pack(side="left")
        self.log_hint_var = tk.StringVar(value="")
        ttk.Label(
            log_head, textvariable=self.log_hint_var, style="Hint.TLabel"
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            log_head, text="打开日志目录",
            command=lambda: self._open_path(paths.logs_dir()),
        ).pack(side="right")

        self.log_view = scrolledtext.ScrolledText(
            bottom, height=14, wrap="word", relief="flat", borderwidth=1,
            font=(LOG_FONT_FAMILY, BASE_FONT_SIZE - 1), padx=8, pady=6,
        )
        self.log_view.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        self.log_view.configure(state="disabled")
        for level, color in LOG_COLORS.items():
            self.log_view.tag_configure(level, foreground=color)

        # 默认给日志区分到大约 6:4 的窗口高度
        self.after(80, self._place_sash)

    def _place_sash(self) -> None:
        """分栏比例：日志区约占窗口高度的 1/4。

        规则：
          1. 先按 1/4 算日志区高度（140~320px 之间）；
          2. 设置区至少要放得下当前标签页的内容；
          3. 窗口很高时不让设置区空一大片，多出来的高度给日志。
        """
        try:
            index = self.notebook.index("current")
            tab = self.notebook.nametowidget(self.notebook.tabs()[index])
            content = getattr(tab, "content", None)
            need = (
                content.winfo_reqheight() if content is not None
                else tab.winfo_reqheight()
            ) + 40   # 40 给标签栏留余量
            height = self.paned.winfo_height()
            if height < 200:
                return
            # 日志框本身约占窗口的 1/4；下面还有一行工具条，所以多给 70px
            log_area = max(210, min(int(height * 0.25) + 70, 400))
            top = height - log_area
            if top > need + 160:            # 设置区空太多就把多出来的给日志
                top = need + 160
            top = max(top, min(need, int(height * 0.60)))   # 内容优先
            top = min(top, height - 210)                    # 日志区至少 210px
            top = max(120, top)
            try:
                if abs(self.paned.sashpos(0) - top) <= 4:
                    return      # 已经很接近了就别再动，避免和 Configure 事件互相触发
            except Exception:
                pass
            self.paned.sashpos(0, top)
        except Exception:
            pass

    def _on_tab_changed(self, _event=None) -> None:
        # 切页后把内容回到顶部（否则会保留上一个标签页的滚动位置，看起来像"跳过了开头"）
        try:
            index = self.notebook.index("current")
            holder = self.notebook.nametowidget(self.notebook.tabs()[index])
            canvas = getattr(holder, "canvas", None)
            if canvas is not None:
                canvas.yview_moveto(0)
        except Exception:
            pass
        # 不同标签页需要的高度不同，切页后重新分配，保证日志区尽量大
        self.after(60, self._place_sash)

    def _add_scrollable_tab(self, title: str) -> ttk.Frame:
        """加一个可纵向滚动的标签页，内容再高也不会被裁掉。"""
        holder = ttk.Frame(self.notebook)
        self.notebook.add(holder, text=title)
        canvas = tk.Canvas(holder, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        inner = ttk.Frame(canvas, padding=10)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window, width=event.width),
        )
        self._bind_wheel(canvas)
        holder.content = inner   # 供 _place_sash 量真实内容高度
        holder.canvas = canvas
        return inner

    def _bind_wheel(self, canvas: tk.Canvas) -> None:
        """让鼠标滚轮在设置区上也能滚动（Tk 默认不会向上冒泡）。

        两个细节：
          1. 内容没有超出可视高度时**完全不滚**，否则会出现
             "已经是最上面一行了，往上滚还能动"的怪现象；
          2. 触控板/高精度滚轮的 delta 可能小于 120，不能整除就丢掉。
        """
        def on_wheel(event):
            try:
                box = canvas.bbox("all")
            except Exception:
                box = None
            if not box or (box[3] - box[1]) <= canvas.winfo_height() + 2:
                try:
                    canvas.yview_moveto(0)
                except Exception:
                    pass
                return
            try:
                widget = canvas.winfo_containing(
                    *canvas.winfo_pointerxy()
                )
            except Exception:
                return
            node = widget
            while node is not None:
                if node is canvas:
                    step = -1 if event.delta > 0 else 1
                    canvas.yview_scroll(step, "units")
                    return
                node = getattr(node, "master", None)

        try:
            self.bind_all("<MouseWheel>", on_wheel, add="+")
        except Exception:
            pass

    def _clear_log(self) -> None:
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.configure(state="disabled")

    def _update_log_hint(self) -> None:
        """在日志区标题旁显示当前这次运行的日志文件名，方便出问题时找到它。"""
        try:
            files = sorted(
                paths.logs_dir().glob("run-*.log"), key=lambda p: p.stat().st_mtime
            )
            if files:
                self.log_hint_var.set(f"（本次：{files[-1].name}）")
        except Exception:
            pass

    def _build_course_tab(self) -> None:
        tab = self._add_scrollable_tab("课程与浏览器")
        tab.columnconfigure(0, weight=1)

        ttk.Label(
            tab,
            text="填课程播放页地址 → 选浏览器 → 点「开始学习」",
            style="SectionA.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        mode_box = ttk.LabelFrame(tab, text="本次运行要做什么", padding=10)
        mode_box.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.run_mode_var = tk.StringVar(value="watch")
        ttk.Radiobutton(
            mode_box,
            text="自动观看未完成的课程视频（默认）",
            variable=self.run_mode_var,
            value="watch",
        ).pack(anchor="w")
        ttk.Radiobutton(
            mode_box,
            text="仅完成章节测验：不刷视频，只完成未完成的章节测验",
            variable=self.run_mode_var,
            value="chapter_only",
        ).pack(anchor="w")
        ttk.Label(
            mode_box,
            text="只对本次运行有效；选第二项要同时勾选「自动完成」才会作答。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        ttk.Label(tab, text="课程播放页链接（每行一个，必须是能看到视频的播放页）").grid(
            row=2, column=0, sticky="w"
        )
        self.courses_text = tk.Text(tab, height=4, wrap="none")
        self.courses_text.grid(row=3, column=0, sticky="ew", pady=(4, 8))

        browser_box = ttk.LabelFrame(tab, text="浏览器", padding=10)
        browser_box.grid(row=4, column=0, sticky="ew")
        browser_box.columnconfigure(1, weight=1)

        self.browser_mode = tk.StringVar(value="new")
        ttk.Radiobutton(
            browser_box, text="新开一个浏览器（推荐，登录状态会记住）",
            variable=self.browser_mode, value="new",
            command=self._sync_widget_states,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(
            browser_box, text="接管我已经打开的浏览器窗口",
            variable=self.browser_mode, value="attach",
            command=self._sync_widget_states,
        ).grid(row=1, column=0, columnspan=3, sticky="w")

        # 两种模式各自需要的字段不同，只显示相关的那一组（更清爽，也更省高度）
        self.new_mode_box = ttk.Frame(browser_box)
        self.new_mode_box.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.new_mode_box.columnconfigure(1, weight=1)
        ttk.Label(self.new_mode_box, text="浏览器").grid(row=0, column=0, sticky="w", pady=2)
        self.driver_box = ttk.Combobox(
            self.new_mode_box, values=["chrome", "edge"], state="readonly", width=12
        )
        self.driver_box.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(self.new_mode_box, text="可执行文件（留空自动查找）").grid(
            row=1, column=0, sticky="w", pady=2
        )
        self.exe_entry = ttk.Entry(self.new_mode_box)
        self.exe_entry.grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Button(self.new_mode_box, text="浏览…", command=self._pick_exe).grid(
            row=1, column=2, padx=(6, 0)
        )

        self.attach_mode_box = ttk.Frame(browser_box)
        self.attach_mode_box.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.attach_mode_box.columnconfigure(1, weight=1)
        ttk.Label(self.attach_mode_box, text="远程调试地址").grid(
            row=0, column=0, sticky="w", pady=2
        )
        self.cdp_entry = ttk.Entry(self.attach_mode_box)
        self.cdp_entry.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Button(
            self.attach_mode_box, text="启动可调试浏览器",
            command=self._launch_debug_browser,
        ).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(
            self.attach_mode_box,
            text="先用上面的按钮开好浏览器并登录",
            style="Hint.TLabel",
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(4, 0))

    def _build_playback_tab(self) -> None:
        tab = self._add_scrollable_tab("播放与答题")
        for col in range(4):
            tab.columnconfigure(col, weight=1 if col == 3 else 0)

        ttk.Label(
            tab,
            text="下面这些设置会自动保存，下次打开还是这样。",
            style="SectionA.TLabel",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        # ---------------- 播放 ----------------
        play = ttk.LabelFrame(tab, text="播放", padding=10)
        play.grid(row=1, column=0, columnspan=4, sticky="ew")
        for col in (1, 3):
            play.columnconfigure(col, weight=1)

        ttk.Label(play, text="播放速度").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=2)
        self.speed_var = tk.StringVar(value="1.5")
        ttk.Spinbox(
            play, from_=0.5, to=2.0, increment=0.1, textvariable=self.speed_var, width=7
        ).grid(row=0, column=1, sticky="w", pady=2)

        ttk.Label(play, text="单个视频最长/分钟").grid(
            row=0, column=2, sticky="w", padx=(20, 6), pady=2
        )
        self.lesson_limit_var = tk.StringVar(value="0")
        ttk.Spinbox(
            play, from_=0, to=1440, increment=5, textvariable=self.lesson_limit_var, width=7
        ).grid(row=0, column=3, sticky="w", pady=2)

        ttk.Label(play, text="每门课最长/分钟").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=2
        )
        self.limit_var = tk.StringVar(value="0")
        ttk.Spinbox(
            play, from_=0, to=100000, increment=10, textvariable=self.limit_var, width=7
        ).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(play, text="每门课刷够/分钟").grid(
            row=1, column=2, sticky="w", padx=(20, 6), pady=2
        )
        self.course_target_var = tk.StringVar(value="0")
        ttk.Spinbox(
            play, from_=0, to=100000, increment=5, textvariable=self.course_target_var,
            width=7,
        ).grid(row=1, column=3, sticky="w", pady=2)

        # 音量与静音放在同一行紧挨着，避免"找不到静音在哪"
        ttk.Label(play, text="音量").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=2)
        volume_row = ttk.Frame(play)
        volume_row.grid(row=2, column=1, columnspan=3, sticky="w", pady=2)
        self.volume_var = tk.StringVar(value="0.01")
        self.volume_spin = ttk.Spinbox(
            volume_row, from_=0.01, to=1.0, increment=0.01,
            textvariable=self.volume_var, width=7,
        )
        self.volume_spin.pack(side="left")
        self.mute_browser_var = tk.BooleanVar(value=True)
        self.mute_check = ttk.Checkbutton(
            volume_row,
            text="浏览器静音（推荐，不动播放器音量）",
            variable=self.mute_browser_var,
            command=self._sync_widget_states,
        )
        self.mute_check.pack(side="left", padx=(10, 0))

        ttk.Label(play, text="停滞判定/秒").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=2
        )
        self.stall_var = tk.StringVar(value="150")
        ttk.Spinbox(
            play, from_=30, to=3600, increment=30, textvariable=self.stall_var, width=7
        ).grid(row=3, column=1, sticky="w", pady=2)
        self.review_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            play, text="课程学完后从头复习一遍", variable=self.review_var
        ).grid(row=3, column=2, sticky="w", padx=(20, 12), pady=2)
        self.activity_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            play, text="模拟真人鼠标操作（触发学习时长上报）",
            variable=self.activity_var,
        ).grid(row=3, column=3, sticky="w", pady=2)
        ttk.Label(
            play,
            text="「单个视频最长」= 一个视频看多久；"
                 "「每门课最长 / 刷够」= 每个课程网址本次最多 / 至少学多久"
                 "（刷够会重看已完成的视频）。",
            style="Hint.TLabel",
            wraplength=980,
            justify="left",
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # ---------------- 答题 ----------------
        lower = ttk.Frame(tab)
        lower.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)

        quiz_box = ttk.LabelFrame(lower, text="答题", padding=10)
        quiz_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        quiz_box.columnconfigure(1, weight=1)

        ttk.Label(
            quiz_box,
            text="① 随堂练习（不计分，不答完关不掉）",
            style="SectionA.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(quiz_box, text="作答方式").grid(
            row=1, column=0, sticky="w", padx=(18, 6), pady=(4, 2)
        )
        self.in_video_mode = ttk.Combobox(
            quiz_box, values=QUIZ_MODE_LABELS, state="readonly", width=12
        )
        self.in_video_mode.grid(row=1, column=1, sticky="w", pady=(4, 2))

        ttk.Separator(quiz_box, orient="horizontal").grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=5
        )

        ttk.Label(
            quiz_box,
            text="② 章节测验（计分；不勾选「自动完成」就完全不碰）",
            style="SectionB.TLabel",
        ).grid(row=3, column=0, columnspan=3, sticky="w")
        chapter_row = ttk.Frame(quiz_box)
        chapter_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.auto_chapter_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            chapter_row, text="自动完成", variable=self.auto_chapter_var,
            command=self._sync_widget_states,
        ).pack(side="left")
        ttk.Label(chapter_row, text="作答方式").pack(side="left", padx=(20, 6))
        self.chapter_mode = ttk.Combobox(
            chapter_row, values=QUIZ_MODE_LABELS, state="readonly", width=12
        )
        self.chapter_mode.pack(side="left")
        self.chapter_mode.bind(
            "<<ComboboxSelected>>", lambda _event: self._sync_widget_states()
        )
        self.auto_submit_chapter_var = tk.BooleanVar(value=False)
        self.auto_submit_chapter_check = ttk.Checkbutton(
            chapter_row, text="作答后自动提交试卷",
            variable=self.auto_submit_chapter_var,
        )
        self.auto_submit_chapter_check.pack(side="left", padx=(20, 0))
        self.review_chapter_var = tk.BooleanVar(value=False)
        self.review_chapter_check = ttk.Checkbutton(
            chapter_row, text="复查答题结果",
            variable=self.review_chapter_var,
        )
        self.review_chapter_check.pack(side="left", padx=(18, 0))
        ttk.Label(
            quiz_box,
            text="复查 = 连做完/做一半的测验也进去逐题重选（需已填 AI 接口）",
            style="Hint.TLabel",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))
        # ---------------- 人机验证 ----------------
        captcha_box = ttk.LabelFrame(lower, text="人机验证", padding=10)
        captcha_box.grid(row=0, column=1, sticky="nsew")
        self.auto_slider_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            captcha_box, text="自动尝试过滑块验证", variable=self.auto_slider_var
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.notify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            captcha_box, text="处理不了时弹窗提醒我", variable=self.notify_var
        ).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Label(
            captcha_box,
            text="拖动过程会实时对准缺口，一般不需要手动干预。",
            style="Hint.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _sync_widget_states(self) -> None:
        """按当前选择灰掉"没意义"的选项，避免指代不明。"""
        try:
            self.volume_spin.configure(
                state="disabled" if self.mute_browser_var.get() else "normal"
            )
        except Exception:
            pass
        chapter_on = self.auto_chapter_var.get()
        try:
            self.chapter_mode.configure(state="readonly" if chapter_on else "disabled")
        except Exception:
            pass
        # 手动作答时不存在"自动提交试卷"，直接灰掉
        mode = QUIZ_MODE_VALUES.get(self.chapter_mode.get(), "random")
        submit_ok = chapter_on and mode != "manual"
        try:
            self.auto_submit_chapter_check.configure(
                state="normal" if submit_ok else "disabled"
            )
            if not submit_ok:
                self.auto_submit_chapter_var.set(False)
        except Exception:
            pass
        # 「复查答题结果」要有 AI 接口才有意义（靠 AI 重新选答案）
        ai_ready = bool(self.ai_key_entry.get().strip())
        try:
            self.review_chapter_check.configure(
                state="normal" if chapter_on and ai_ready else "disabled"
            )
            if not (chapter_on and ai_ready):
                self.review_chapter_var.set(False)
        except Exception:
            pass
        # 学号登录才需要填机构；不勾自动登录时这三项都不用管
        student = self.login_method_var.get() == "student"
        try:
            state = "normal" if student else "disabled"
            self.school_entry.configure(state=state)
            self.school_label.configure(foreground="" if student else "#9aa0a6")
        except Exception:
            pass
        # 浏览器模式：只显示当前模式需要的字段
        try:
            if self.browser_mode.get() == "attach":
                self.new_mode_box.grid_remove()
                self.attach_mode_box.grid()
            else:
                self.attach_mode_box.grid_remove()
                self.new_mode_box.grid()
        except Exception:
            pass

    def _build_account_tab(self) -> None:
        tab = self._add_scrollable_tab("账号与 AI")
        tab.columnconfigure(1, weight=1)

        ttk.Label(
            tab,
            text="可选：保存账号密码让程序自动登录；AI 答题在这里填接口。",
            style="SectionA.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        account_box = ttk.LabelFrame(tab, text="智慧树账号", padding=10)
        account_box.grid(row=1, column=0, columnspan=3, sticky="ew")
        account_box.columnconfigure(1, weight=1)

        self.login_method_var = tk.StringVar(value="account")
        method_row = ttk.Frame(account_box)
        method_row.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(method_row, text="登录方式").pack(side="left")
        ttk.Radiobutton(
            method_row, text="账号密码", value="account",
            variable=self.login_method_var, command=self._sync_widget_states,
        ).pack(side="left", padx=(10, 0))
        ttk.Radiobutton(
            method_row, text="学号登录（机构 + 学号 + 密码）", value="student",
            variable=self.login_method_var, command=self._sync_widget_states,
        ).pack(side="left", padx=(10, 0))

        self.school_label = ttk.Label(account_box, text="机构/学校")
        self.school_label.grid(row=1, column=0, sticky="w", pady=3)
        self.school_entry = ttk.Entry(account_box)
        self.school_entry.grid(row=1, column=1, sticky="ew", pady=3)

        ttk.Label(account_box, text="账号").grid(row=2, column=0, sticky="w", pady=3)
        self.username_entry = ttk.Entry(account_box)
        self.username_entry.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(account_box, text="密码").grid(row=3, column=0, sticky="w", pady=3)
        self.password_entry = ttk.Entry(account_box, show="●")
        self.password_entry.grid(row=3, column=1, sticky="ew", pady=3)

        self.auto_login_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            account_box,
            text="使用自动登录（勾选=程序自己登录；不勾选=全部由你手动登录）",
            variable=self.auto_login_var,
            command=self._sync_widget_states,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(
            account_box,
            text="自动登录失败（账号错误、滑块没过）会自动转为你手动完成。",
            style="Hint.TLabel",
        ).grid(row=5, column=0, columnspan=2, sticky="w")

        row = ttk.Frame(account_box)
        row.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(row, text="保存到本机", command=self._save_credentials).pack(side="left")
        ttk.Button(row, text="清除已保存", command=self._forget_credentials).pack(
            side="left", padx=8
        )
        self.credential_state = tk.StringVar(value="未保存")
        ttk.Label(row, textvariable=self.credential_state, foreground="#666666").pack(
            side="left", padx=8
        )

        ai_box = ttk.LabelFrame(tab, text="AI 答题接口（OpenAI 兼容）", padding=10)
        ai_box.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ai_box.columnconfigure(1, weight=1)
        ttk.Label(ai_box, text="Base URL").grid(row=0, column=0, sticky="w", pady=3)
        self.ai_base_entry = ttk.Entry(ai_box)
        self.ai_base_entry.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(ai_box, text="模型").grid(row=1, column=0, sticky="w", pady=3)
        self.ai_model_entry = ttk.Entry(ai_box)
        self.ai_model_entry.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(ai_box, text="API Key").grid(row=2, column=0, sticky="w", pady=3)
        self.ai_key_entry = ttk.Entry(ai_box, show="●")
        self.ai_key_entry.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(
            ai_box,
            text="留空则不用 AI。API Key 存在 data/config.json，请勿分享该文件。",
            style="Hint.TLabel",
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(
            ai_box,
            text="没接口也能用（选「随机选择」或「手动作答」）；找接口可搜索"
                 "「OpenAI 兼容 API」「大模型 API 免费额度」。",
            style="Hint.TLabel",
            justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w")

    # ------------------------------------------------------------------
    # 配置 <-> 界面
    # ------------------------------------------------------------------
    def _load_into_ui(self) -> None:
        cfg = self.config_model
        self.courses_text.delete("1.0", "end")
        self.courses_text.insert("1.0", "\n".join(cfg.courses))
        # 运行模式只对本次运行有效：每次打开程序都回到「刷课」
        self.run_mode_var.set("watch")
        self.browser_mode.set(cfg.get("browser.mode", "new"))
        self.driver_box.set(cfg.get("browser.driver", "chrome"))
        self.exe_entry.delete(0, "end")
        self.exe_entry.insert(0, cfg.get("browser.exe_path", ""))
        self.cdp_entry.delete(0, "end")
        self.cdp_entry.insert(0, cfg.get("browser.cdp_url", "http://127.0.0.1:9222"))

        self.speed_var.set(str(cfg.get("playback.speed", 1.5)))
        self.volume_var.set(str(cfg.get("playback.volume", 1.0)))
        self.mute_browser_var.set(bool(cfg.get("playback.mute_browser", True)))
        self.limit_var.set(str(cfg.get("playback.max_minutes_per_course", 0)))
        self.lesson_limit_var.set(str(cfg.get("playback.max_minutes_per_lesson", 0)))
        self.course_target_var.set(str(cfg.get("playback.min_minutes_per_course", 0)))
        self.stall_var.set(str(cfg.get("playback.stall_seconds", 150)))
        self.review_var.set(bool(cfg.get("playback.review_when_finished", False)))
        self.activity_var.set(bool(cfg.get("playback.simulate_activity", True)))

        self.auto_chapter_var.set(bool(cfg.get("quiz.auto_chapter_quiz", False)))
        self.auto_submit_chapter_var.set(bool(cfg.get("quiz.auto_submit_chapter", False)))
        self.review_chapter_var.set(bool(cfg.get("quiz.review_chapter", False)))
        self.in_video_mode.set(
            QUIZ_MODE_BY_VALUE.get(cfg.get("quiz.in_video_mode", "random"), "随机选择")
        )
        self.chapter_mode.set(
            QUIZ_MODE_BY_VALUE.get(cfg.get("quiz.chapter_mode", "random"), "随机选择")
        )
        self.auto_slider_var.set(bool(cfg.get("captcha.auto_slider", True)))
        self.notify_var.set(bool(cfg.get("captcha.notify_user", True)))

        saved = load_credentials()
        if saved:
            self.username_entry.delete(0, "end")
            self.username_entry.insert(0, saved["username"])
            self.login_method_var.set(saved.get("method") or "account")
            self.school_entry.delete(0, "end")
            self.school_entry.insert(0, saved.get("school") or "")
        else:
            self.login_method_var.set(str(cfg.get("login.method", "account")))
            self.school_entry.delete(0, "end")
            self.school_entry.insert(0, str(cfg.get("login.school", "")))
        self.auto_login_var.set(bool(cfg.get("login.auto_login", True)))
        self.ai_base_entry.delete(0, "end")
        self.ai_base_entry.insert(0, cfg.get("quiz.ai.base_url", ""))
        self.ai_model_entry.delete(0, "end")
        self.ai_model_entry.insert(0, cfg.get("quiz.ai.model", ""))
        self.ai_key_entry.delete(0, "end")
        self.ai_key_entry.insert(0, cfg.get("quiz.ai.api_key", ""))
        self._sync_widget_states()

    def _collect_config(self) -> None:
        cfg = self.config_model
        cfg.set("courses", self.courses_text.get("1.0", "end"))
        cfg.set("run_mode", self.run_mode_var.get())
        cfg.set("browser.mode", self.browser_mode.get())
        cfg.set("browser.driver", self.driver_box.get() or "chrome")
        cfg.set("browser.exe_path", self.exe_entry.get().strip())
        cfg.set("browser.cdp_url", self.cdp_entry.get().strip() or "http://127.0.0.1:9222")

        cfg.set("playback.speed", self.speed_var.get())
        cfg.set("playback.volume", self.volume_var.get())
        cfg.set("playback.mute_browser", self.mute_browser_var.get())
        cfg.set("playback.max_minutes_per_course", self.limit_var.get())
        cfg.set("playback.max_minutes_per_lesson", self.lesson_limit_var.get())
        cfg.set("playback.min_minutes_per_course", self.course_target_var.get())
        cfg.set("playback.stall_seconds", self.stall_var.get())
        cfg.set("playback.review_when_finished", self.review_var.get())
        cfg.set("playback.simulate_activity", self.activity_var.get())

        cfg.set("quiz.auto_chapter_quiz", self.auto_chapter_var.get())
        cfg.set("quiz.auto_submit_chapter", self.auto_submit_chapter_var.get())
        cfg.set("quiz.review_chapter", self.review_chapter_var.get())
        cfg.set("quiz.in_video_mode", QUIZ_MODE_VALUES.get(self.in_video_mode.get(), "random"))
        cfg.set("quiz.chapter_mode", QUIZ_MODE_VALUES.get(self.chapter_mode.get(), "random"))
        cfg.set("captcha.auto_slider", self.auto_slider_var.get())
        cfg.set("captcha.notify_user", self.notify_var.get())
        cfg.set("login.auto_login", self.auto_login_var.get())
        cfg.set("login.method", self.login_method_var.get())
        cfg.set("login.school", self.school_entry.get().strip())

        cfg.set("quiz.ai.base_url", self.ai_base_entry.get().strip())
        cfg.set("quiz.ai.model", self.ai_model_entry.get().strip())
        cfg.set("quiz.ai.api_key", self.ai_key_entry.get().strip())
        cfg.normalize()

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _pick_exe(self) -> None:
        path = filedialog.askopenfilename(
            title="选择浏览器可执行文件",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")],
        )
        if path:
            self.exe_entry.delete(0, "end")
            self.exe_entry.insert(0, path)

    def _launch_debug_browser(self) -> None:
        driver = self.driver_box.get() or "chrome"
        port = 9222
        cdp = self.cdp_entry.get().strip()
        if ":" in cdp:
            try:
                port = int(cdp.rsplit(":", 1)[1].split("/")[0])
            except ValueError:
                port = 9222
        try:
            exe = launch_debuggable_browser(driver, port)
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc), parent=self)
            return
        self.browser_mode.set("attach")
        messagebox.showinfo(
            "已启动",
            f"已用 {os.path.basename(exe)} 打开一个可调试的浏览器（端口 {port}）。\n\n"
            "请在打开的窗口里登录智慧树，然后把模式设为「接管我已经打开的浏览器窗口」后开始学习。",
            parent=self,
        )

    def _open_path(self, path) -> None:
        try:
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _save_credentials(self) -> None:
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        if not username or not password:
            messagebox.showwarning("提示", "请先填写账号和密码。", parent=self)
            return
        from zhskiller.credentials import save_credentials

        method = save_credentials(
            username, password,
            method=self.login_method_var.get(),
            school=self.school_entry.get().strip(),
        )
        self._refresh_credential_state()
        note = "已用 Windows 凭据保护加密保存。" if method == "dpapi" else "已保存（简易混淆）。"
        messagebox.showinfo("已保存", note, parent=self)

    def _forget_credentials(self) -> None:
        clear_credentials()
        self.password_entry.delete(0, "end")
        self._refresh_credential_state()

    def _reset_login(self) -> None:
        """下次启动时清空浏览器里的登录凭证，让平台重新要求登录。"""
        try:
            paths.reset_login_flag().write_text("reset", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("重置失败", str(exc), parent=self)
            return
        messagebox.showinfo(
            "已安排重置登录",
            "已记录。下一次点「开始学习」时会先清空浏览器里的登录状态，\n"
            "平台会重新弹出登录页（适合换账号）。\n\n"
            "注意：本程序保存的账号密码不会被删除。",
            parent=self,
        )

    def _refresh_credential_state(self) -> None:
        saved = load_credentials()
        self.credential_state.set(f"已保存：{saved['username']}" if saved else "未保存")

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self._collect_config()
        if not self.config_model.courses:
            messagebox.showwarning("提示", "请先填写至少一个课程播放页链接。", parent=self)
            return
        if (
            self.config_model.get("browser.mode") == "attach"
            and not messagebox.askokcancel(
                "接管已打开的浏览器",
                "请确认已经用带调试端口的浏览器打开，并且完成了登录。\n是否继续？",
                parent=self,
            )
        ):
            return
        self.config_model.save()
        for path, value in (
            ("playback.speed", self.config_model.get("playback.speed")),
            ("playback.volume", self.config_model.get("playback.volume")),
        ):
            self._append_log("信息", f"配置：{path} = {value}")

        self.bridge = Bridge(self.queue)
        self.worker = threading.Thread(target=self._run_worker, daemon=True)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.pause_button.configure(state="normal", text="暂停")
        self.progress.configure(value=0)
        self.status_var.set("启动中…")
        self.after(1500, self._update_log_hint)
        try:
            self.deiconify()
            self.lift()
        except tk.TclError:
            pass
        self.worker.start()

    def _stop(self) -> None:
        if self.bridge:
            self.bridge.stop()
            self.status_var.set("正在停止…")

    def _toggle_pause(self) -> None:
        """暂停 / 继续：只让自动化停下来，浏览器页面保持不动。"""
        if not self.bridge:
            return
        if self.bridge.paused:
            self.bridge.resume()
            self.pause_button.configure(text="暂停")
            self.status_var.set("已继续")
            self._append_log("信息", "已继续，接着之前的工作往下做。")
        else:
            self.bridge.pause()
            self.pause_button.configure(text="继续")
            self.status_var.set("已暂停（页面保持不动）")
            self._append_log("警告", "已暂停：浏览器页面保持不变，自动化停止运行。")

    def _run_worker(self) -> None:
        runner = Runner(self.config_model, self.bridge)
        try:
            ok = runner.run()
        except Exception as exc:  # 兜底，避免线程静默死掉
            self.queue.put(("log", "错误", f"运行线程异常：{exc}"))
            ok = False
        self.queue.put(("finished", ok, "全部任务结束" if ok else "任务结束（有未完成项）"))

    # ------------------------------------------------------------------
    # 消息泵
    # ------------------------------------------------------------------
    def _pump(self) -> None:
        try:
            if not self._dialog_open:
                while True:
                    try:
                        message = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        self._handle_message(message)
                    except Exception as exc:  # 单条消息出错不能拖垮整个消息泵
                        self._log_ui_error(f"处理消息 {message[0]} 失败", exc)
        except Exception as exc:
            self._log_ui_error("界面刷新异常", exc)
        finally:
            # 无论发生什么都要继续排期，否则界面会「看起来还活着但再也弹不出东西」
            try:
                self.after(120, self._pump)
            except tk.TclError:
                pass

    def _report_callback_exception(self, exc_type, exc_value, exc_tb) -> None:
        self._log_ui_error("界面回调异常", exc_value)

    def _log_ui_error(self, title: str, exc: BaseException) -> None:
        detail = f"{title}：{type(exc).__name__}: {exc}"
        try:
            self._append_log("错误", detail)
        except Exception:
            pass
        try:
            from zhskiller import paths

            with open(paths.logs_dir() / "ui-errors.log", "a", encoding="utf-8") as handle:
                handle.write(detail + "\n")
                handle.write(traceback.format_exc() + "\n")
        except Exception:
            pass

    def _handle_message(self, message: tuple) -> None:
        kind = message[0]
        if kind == "log":
            self._append_log(message[1], message[2])
        elif kind == "status":
            self.status_var.set(message[1])
        elif kind == "progress":
            self._update_progress(message[1])
        elif kind == "finished":
            self._on_finished(message[1], message[2])
        elif kind == "ask":
            self._show_ask(message[1], message[2], message[3])
        elif kind == "dismiss":
            self._dismiss_notice(message[1])

    def _show_ask(self, request_id: int, kind: str, payload: dict) -> None:
        if kind in NOTICE_KINDS:
            self._show_notice(request_id, kind, payload)
            return
        self._dialog_open = True
        try:
            # 浏览器窗口常常抢在前台，先把主窗口拉回来
            try:
                self.deiconify()
                self.lift()
            except tk.TclError:
                pass
            dialog = build_dialog(self, kind, payload)
            answer = dialog.show()
            self.bridge.resolve(request_id, answer)
        finally:
            self._dialog_open = False

    def _show_notice(self, request_id: int, kind: str, payload: dict) -> None:
        """右下角浮窗：不阻塞、不抢焦点，程序自己也会继续判断状态。"""
        old = self._notices.pop(request_id, None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass

        def on_answer(value) -> None:
            self._notices.pop(request_id, None)
            if self.bridge:
                self.bridge.resolve(request_id, value)

        try:
            notice = build_notice(self, kind, payload, on_answer)
        except Exception as exc:
            self._log_ui_error("创建提示浮窗失败", exc)
            if self.bridge:
                self.bridge.resolve(request_id, None)
            return
        self._notices[request_id] = notice
        notice.popup()

    def _dismiss_notice(self, request_id: int) -> None:
        notice = self._notices.pop(request_id, None)
        if notice is None:
            return
        try:
            notice.destroy()
        except Exception:
            pass

    def _update_progress(self, fields: dict) -> None:
        total = fields.get("lesson_total") or 0
        index = fields.get("lesson_index") or 0
        if total:
            self.progress.configure(value=index / total * 100)
        if fields.get("lesson"):
            self.status_var.set(f"正在学习：{fields['lesson'][:40]}")

    def _append_log(self, level: str, message: str) -> None:
        self.log_view.configure(state="normal")
        self.log_view.insert("end", f"{message}\n", level)
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def _on_finished(self, ok: bool, message: str) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.pause_button.configure(state="disabled", text="暂停")
        self.status_var.set(message)
        self._append_log("完成" if ok else "警告", message)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askokcancel("退出", "任务还在运行，确定要退出吗？", parent=self):
                return
            if self.bridge:
                self.bridge.stop()
        for notice in list(self._notices.values()):
            try:
                notice.destroy()
            except Exception:
                pass
        self._notices.clear()
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()
