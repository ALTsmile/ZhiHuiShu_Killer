"""配置模型：默认值、读写、校验。

配置文件是 ``data/config.json``，字段全部带默认值，
所以老版本配置缺字段也能正常加载（缺失的用默认值补齐）。
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from . import paths

URL_RE = re.compile(r"https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]*[-A-Za-z0-9+&@#/%=~_|]")

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "courses": [],
    # watch = 自动观看未完成的视频（默认）
    # chapter_only = 不看视频，只找未完成的章节测验并完成
    "run_mode": "watch",
    "browser": {
        # new = 程序新开一个浏览器；attach = 接管已经打开的浏览器
        "mode": "new",
        "driver": "chrome",          # chrome | edge
        "exe_path": "",              # 留空则自动探测
        "cdp_url": "http://127.0.0.1:9222",
        # 是否拦截站点的 window.open（把"新标签页打开"改成当前页打开）。
        # 默认关闭：实测拦截会干扰站点的开考流程（考试接口调了但页面打不开）。
        # 早期以为"新标签页会丢登录态"其实是登录判断的 bug，已经修掉了。
        "same_tab_redirect": False,
    },
    "playback": {
        "speed": 1.5,
        # 音量：这里 1.0 表示 100%。绑视频播放器的 volume 属性（0~1）。
        # 用户要的是"网站显示 1%（即接近静音但不为 0）"，所以默认 0.01。
        # 0 可能被平台判定为静音而不计进度，所以不要把默认值改成 0。
        "volume": 0.01,
        # True = 用浏览器层面的静音（启动参数 --mute-audio），完全不改播放器音量。
        # 只有在它被关掉时，上面的 volume 才会生效。
        "mute_browser": True,
        # 单个视频最多学多久（分钟），0 = 不限制
        "max_minutes_per_lesson": 0,
        # 每门课程（界面上填的每个课程网址）本次最多学多久（分钟），0 = 不限制
        "max_minutes_per_course": 0,
        # 每门课程本次至少要学够多久（分钟），0 = 不用。
        # 用于刷平台的"规律学习"评分：学够之后才换下一门课，
        # 视频都看完了会重看已完成的课时来凑时长。
        "min_minutes_per_course": 0,
        "stall_seconds": 150,        # 视频与平台进度都不动多久算卡住
        "review_when_finished": False,  # 课程全部学完后是否再从头复习一遍
        # 模拟真人鼠标操作（含一次顶部栏点击），用于触发平台的学习时长上报
        "simulate_activity": True,
    },
    "quiz": {
        "auto_chapter_quiz": False,  # 章节测验是否自动作答
        # 章节测验作答完成后是否自动点提交；关闭时只作答，提交由用户自己点
        "auto_submit_chapter": False,
        # 复查：进入"完成率 100% 但未提交"或"做到一半"的测验，逐题重选
        # （仅在自动完成章节测验已勾选、且 AI 接口可用时生效）
        "review_chapter": False,
        "in_video_mode": "random",   # 随堂练习：random | ai | manual
        "chapter_mode": "random",    # 章节练习：random | ai | manual
        "ai": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
            "timeout": 30,
        },
    },
    "captcha": {
        "auto_slider": True,         # 自动尝试拖滑块
        "notify_user": True,         # 处理不了时弹窗通知用户手动完成
        # 额外微调(px)：程序会先"边拖边量"把拼图块对准缺口，
        # 这里是在对准的基础上再多拖几像素（个别账号/分辨率下有用），默认 0。
        "slider_offset": 0,
    },
    "diagnostics": {
        # 排障开关（界面上已移除）：需要排查"反复播放暂停 / 进度不涨"时，
        # 手动把这里改成 true 再运行，日志里就会有对应的记录。
        "record_events": False,
        "capture_network": False,
    },
    "login": {
        "auto_login": True,
        # account = 账号密码登录；student = 学号登录（需要机构 + 学号 + 密码）
        "method": "account",
        "school": "",
        # ask = 未配置账号时每次询问；muted = 不再提醒
        "prompt_state": "ask",
    },
}


class AttrDict(dict):
    """支持 ``cfg.playback.speed`` 这种点号访问的 dict。"""

    def __getattr__(self, item: str):
        try:
            value = self[item]
        except KeyError as exc:  # pragma: no cover - 只在字段写错时触发
            raise AttributeError(item) from exc
        return AttrDict(value) if isinstance(value, dict) else value

    def __setattr__(self, key: str, value) -> None:
        self[key] = value


def _merge_defaults(defaults: dict, override: dict) -> dict:
    result = copy.deepcopy(defaults)
    for key, value in (override or {}).items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_defaults(result[key], value)
        else:
            result[key] = value
    return result


def normalize_course_urls(raw) -> list[str]:
    """把文本域里粘贴的内容解析成去重后的 URL 列表。"""
    if isinstance(raw, (list, tuple)):
        text = "\n".join(str(item) for item in raw)
    else:
        text = str(raw or "")
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_RE.findall(text):
        url = match.rstrip("/")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.config_path()
        self.data: AttrDict = AttrDict(_merge_defaults(DEFAULTS, self._read()))
        self.normalize()

    # ---------- 读写 ----------
    def _read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def save(self) -> None:
        self.normalize()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        # run_mode 只在本次运行生效，不落盘：下次打开程序默认回到「刷课」。
        payload = dict(self.data)
        payload["run_mode"] = "watch"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def normalize(self) -> None:
        """把用户输入收拢到合理范围，界面上就不用做太多防御。"""
        self.data["courses"] = normalize_course_urls(self.data.get("courses"))
        if self.data.get("run_mode") not in ("watch", "chapter_only"):
            self.data["run_mode"] = "watch"
        browser = self.data["browser"]
        if browser.get("mode") not in ("new", "attach"):
            browser["mode"] = "new"
        if browser.get("driver") not in ("chrome", "edge"):
            browser["driver"] = "chrome"
        browser["exe_path"] = str(browser.get("exe_path") or "").strip()
        browser["cdp_url"] = str(browser.get("cdp_url") or "http://127.0.0.1:9222").strip()
        browser["same_tab_redirect"] = bool(browser.get("same_tab_redirect", False))

        playback = self.data["playback"]
        playback["speed"] = round(clamp(_to_float(playback.get("speed"), 1.5), 0.5, 2.0), 2)
        playback["volume"] = round(clamp(_to_float(playback.get("volume"), 1.0), 0.0, 1.0), 2)
        playback["mute_browser"] = bool(playback.get("mute_browser", True))
        for key in ("max_minutes_per_lesson", "max_minutes_per_course",
                    "min_minutes_per_course"):
            playback[key] = max(0.0, _to_float(playback.get(key), 0.0))
        playback["stall_seconds"] = int(clamp(_to_float(playback.get("stall_seconds"), 150), 30, 3600))
        playback["review_when_finished"] = bool(playback.get("review_when_finished", False))
        playback["simulate_activity"] = bool(playback.get("simulate_activity", True))

        quiz = self.data["quiz"]
        quiz["auto_chapter_quiz"] = bool(quiz.get("auto_chapter_quiz", False))
        quiz["auto_submit_chapter"] = bool(quiz.get("auto_submit_chapter", False))
        quiz["review_chapter"] = bool(quiz.get("review_chapter", False))
        if quiz.get("in_video_mode") not in ("random", "ai", "manual"):
            quiz["in_video_mode"] = "random"
        if quiz.get("chapter_mode") not in ("random", "ai", "manual"):
            quiz["chapter_mode"] = "random"
        ai = quiz["ai"]
        ai["base_url"] = str(ai.get("base_url") or "").strip()
        ai["api_key"] = str(ai.get("api_key") or "").strip()
        ai["model"] = str(ai.get("model") or "").strip()
        ai["timeout"] = int(clamp(_to_float(ai.get("timeout"), 30), 5, 300))

        captcha = self.data["captcha"]
        captcha["auto_slider"] = bool(captcha.get("auto_slider", True))
        captcha["notify_user"] = bool(captcha.get("notify_user", True))
        try:
            captcha["slider_offset"] = float(
                clamp(_to_float(captcha.get("slider_offset"), 0.0), -20.0, 120.0)
            )
        except (TypeError, ValueError):
            captcha["slider_offset"] = 0.0

        diagnostics = self.data.setdefault("diagnostics", {})
        diagnostics["record_events"] = bool(diagnostics.get("record_events", True))
        diagnostics["capture_network"] = bool(diagnostics.get("capture_network", True))

        login = self.data["login"]
        login["auto_login"] = bool(login.get("auto_login", True))
        if login.get("method") not in ("account", "student"):
            login["method"] = "account"
        login["school"] = str(login.get("school") or "").strip()
        if login.get("prompt_state") not in ("ask", "muted"):
            login["prompt_state"] = "ask"

    # ---------- 便捷访问 ----------
    def get(self, dotted: str, default=None):
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    @property
    def courses(self) -> list[str]:
        return normalize_course_urls(self.data["courses"])

    @property
    def ai_enabled(self) -> bool:
        return bool(self.data["quiz"]["ai"]["api_key"])

    def quiz_mode(self, kind: str) -> str:
        """kind: in_video | chapter。AI 未配置时自动退回随机选择。"""
        mode = self.get(f"quiz.{kind}_mode", "random")
        if mode == "ai" and not self.ai_enabled:
            return "random"
        return mode


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
