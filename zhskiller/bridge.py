"""运行线程与界面线程之间的通信通道。

后台线程负责浏览器自动化，界面线程负责 tkinter。
两者通过一个 queue.Queue 单向传递消息（后台 -> 界面），
后台需要用户回答时，用 threading.Event 阻塞等待界面回填结果。
"""

from __future__ import annotations

import itertools
import queue
import threading
from typing import Any

UNANSWERED = object()


class Stopped(Exception):
    """用户点击「停止」时抛出，用于快速跳出当前流程。"""


class Bridge:
    def __init__(self, ui_queue: "queue.Queue[tuple]"):
        self.ui_queue = ui_queue
        self._requests: dict[int, dict] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self.stop_event = threading.Event()
        # set = 运行中；clear = 已暂停。默认运行。
        self.resume_event = threading.Event()
        self.resume_event.set()

    # ------------------------------------------------------------------
    # 后台线程 -> 界面
    # ------------------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        self.ui_queue.put(("log", level, message))

    def status(self, text: str) -> None:
        self.ui_queue.put(("status", text))

    def progress(self, **fields: Any) -> None:
        self.ui_queue.put(("progress", fields))

    def finished(self, ok: bool, message: str) -> None:
        self.ui_queue.put(("finished", ok, message))

    # ------------------------------------------------------------------
    # 后台 -> 界面 -> 后台（阻塞式提问）
    # ------------------------------------------------------------------
    def ask(self, kind: str, payload: dict | None = None, timeout: float | None = None):
        """向界面提一个问题并等待回答。timeout 到期返回 None。"""
        if self.stop_event.is_set():
            raise Stopped()
        request_id = next(self._ids)
        slot = {"event": threading.Event(), "value": None}
        with self._lock:
            self._requests[request_id] = slot
        try:
            self.ui_queue.put(("ask", request_id, kind, payload or {}))
            if not slot["event"].wait(timeout):
                return None
            return slot["value"]
        finally:
            with self._lock:
                self._requests.pop(request_id, None)

    def resolve(self, request_id: int, value) -> None:
        """界面线程调用：回答某个请求。"""
        with self._lock:
            slot = self._requests.get(request_id)
        if slot is None:
            return
        slot["value"] = value
        slot["event"].set()

    # ------------------------------------------------------------------
    # 非阻塞提问：用于"只是提醒一下，程序自己也会继续往下走"的场景
    # ------------------------------------------------------------------
    def post(self, kind: str, payload: dict | None = None) -> int:
        """投出一个提示，不等待回答，返回 request_id。"""
        request_id = next(self._ids)
        slot = {"event": threading.Event(), "value": None, "answered": False}
        with self._lock:
            self._requests[request_id] = slot
        self.ui_queue.put(("ask", request_id, kind, payload or {}))
        return request_id

    def poll(self, request_id: int, timeout: float = 0.0):
        """查看提示是否有回答；没回答返回 UNANSWERED。"""
        with self._lock:
            slot = self._requests.get(request_id)
        if slot is None:
            return UNANSWERED
        if slot["event"].wait(timeout):
            slot["answered"] = True
            return slot["value"]
        return UNANSWERED

    def release(self, request_id: int) -> None:
        """撤回提示（界面会把对应的浮窗关掉）。"""
        with self._lock:
            self._requests.pop(request_id, None)
        self.ui_queue.put(("dismiss", request_id))

    # ------------------------------------------------------------------
    # 停止控制
    # ------------------------------------------------------------------
    def stop(self) -> None:
        self.stop_event.set()
        self.resume_event.set()   # 叫醒可能正卡在"暂停"上的线程
        # 叫醒所有还在等回答的后台线程
        with self._lock:
            slots = list(self._requests.values())
        for slot in slots:
            slot["event"].set()

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise Stopped()
        if not self.resume_event.is_set():
            self._log_pause()

    @property
    def stopped(self) -> bool:
        return self.stop_event.is_set()

    # ------------------------------------------------------------------
    # 暂停 / 继续
    # ------------------------------------------------------------------
    def pause(self) -> None:
        """暂停自动化。已经打开的页面保持不动，只是不再往下做事。"""
        self.resume_event.clear()

    def resume(self) -> None:
        self.resume_event.set()

    @property
    def paused(self) -> bool:
        return not self.resume_event.is_set()

    def _log_pause(self) -> None:
        """暂停期间阻塞在这里（不推进逻辑，也不会把超时时间算进去）。"""
        first = True
        while not self.resume_event.wait(0.3):
            if self.stop_event.is_set():
                raise Stopped()
            if first:
                first = False
                self.log("已暂停，点「继续」后接着做。", "警告")

    def sleep(self, seconds: float, step: float = 0.25) -> None:
        """可被打断的 sleep（暂停期间不会推进）。"""
        remaining = float(seconds)
        while remaining > 0:
            self.check_stop()
            chunk = min(step, remaining)
            if self.stop_event.wait(chunk):
                raise Stopped()
            remaining -= chunk
