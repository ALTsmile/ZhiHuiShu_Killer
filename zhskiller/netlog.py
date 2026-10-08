"""把站点关于「学习时长/进度上报」的网络请求抓下来。

用途：定位「刚开课时平台进度不涨」到底是
  * 请求根本没发出去（前端条件没满足），还是
  * 请求发了但服务端返回 0 / 被拒绝。

实现要点：
  * 事件回调里只做记录，绝不调用其它 Playwright API（会重入死锁）；
  * 响应体不在回调里读，而是先存下 response 对象，之后在主循环里惰性读取。
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from . import paths

KEYWORDS = (
    "progress", "study", "learn", "time", "save", "record", "heartbeat",
    "heart", "report", "lesson", "log",
)

BODY_KEYWORDS = (
    "progress", "studytime", "study-time", "save", "heartbeat", "time",
    # 章节测验相关的接口：响应里通常带考试 id / 入口地址，
    # 万一站点自己打不开试卷，我们可以拿这个地址直接导航过去
    "exam", "test",
)

SKIP_SUFFIXES = (".js", ".css", ".png", ".jpg", ".jpeg", ".mp4", ".svg", ".woff", ".ico")


class NetworkLog:
    def __init__(self, logger, enabled: bool = True):
        self.logger = logger
        self.enabled = enabled
        self.entries: list[dict] = []
        self._pending: list[object] = []
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path: Path = paths.logs_dir() / f"network-{stamp}.log"
        self._handle = None
        self._lines = 0

    def attach(self, page) -> None:
        if not self.enabled:
            return
        try:
            self._handle = open(self.path, "a", encoding="utf-8")
        except OSError:
            self.enabled = False
            return
        try:
            page.on("request", self._on_request)
            page.on("response", self._on_response)
        except Exception:
            self.enabled = False

    def detach(self, page) -> None:
        try:
            page.remove_listener("request", self._on_request)
            page.remove_listener("response", self._on_response)
        except Exception:
            pass
        if self._handle:
            try:
                self._handle.close()
            except Exception:
                pass
            self._handle = None

    def _interesting(self, url: str) -> bool:
        """诊断阶段尽量全抓：只排除静态资源，避免漏掉未知的上报接口名。"""
        lowered = (url or "").lower()
        if "zhihuishu.com" not in lowered:
            return False
        if any(lowered.split("?")[0].endswith(suffix) for suffix in SKIP_SUFFIXES):
            return False
        return True

    def _write(self, text: str) -> None:
        # 防止长时间挂机把文件写爆
        if self._lines > 20000:
            return
        try:
            if self._handle:
                self._handle.write(text + "\n")
                self._lines += 1
                self._handle.flush()
        except Exception:
            pass

    def _on_request(self, request) -> None:
        try:
            url = request.url
            if not self._interesting(url):
                return
            stamp = _dt.datetime.now().strftime("%H:%M:%S")
            try:
                body = request.post_data or ""
            except Exception:
                body = ""
            self.entries.append({"t": stamp, "method": request.method, "url": url[:300]})
            self._write(
                f"[{stamp}] >>> {request.method} {url[:300]}"
                + (f"\n           payload: {body[:800]}" if body else "")
            )
        except Exception:
            return

    def _on_response(self, response) -> None:
        try:
            url = response.url
            if not self._interesting(url):
                return
            stamp = _dt.datetime.now().strftime("%H:%M:%S")
            try:
                status = response.status
            except Exception:
                status = "?"
            self._write(f"[{stamp}] <<< {status} {url[:300]}")
            lowered = url.lower()
            if any(keyword in lowered for keyword in BODY_KEYWORDS):
                self._pending.append(response)
                if len(self._pending) > 40:
                    self._pending.pop(0)
        except Exception:
            return

    def flush_bodies(self, limit: int = 4) -> None:
        """在主循环里惰性读取响应体（回调里读会重入）。"""
        if not self.enabled:
            return
        pending, self._pending = self._pending[:limit], self._pending[limit:]
        for response in pending:
            try:
                text = response.text()
            except Exception:
                continue
            if not text:
                continue
            self._write(f"           response: {json.dumps(text[:1200], ensure_ascii=False)}")

    def summary(self) -> str:
        return f"网络记录 {len(self.entries)} 条 -> {self.path.name}"
