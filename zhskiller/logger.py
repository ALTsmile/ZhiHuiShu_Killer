"""日志：同时写到界面日志区和 data/logs/ 下的文件。"""

from __future__ import annotations

import datetime as _dt
import time
import traceback

from . import paths
from .bridge import Bridge

# 日志/诊断导出的保留时间与数量上限（超期或超量的自动删掉，避免越积越多）
LOG_RETENTION_DAYS = 7
LOG_KEEP_MAX = 40

# 只清理这些命名规则的文件：run-*.log / network-*.log / ui-errors.log / xxx-*.html
_LOG_PATTERNS = ("run-*.log", "network-*.log", "ui-errors.log", "*.html")


def cleanup_old_logs(days: int = LOG_RETENTION_DAYS, keep_max: int = LOG_KEEP_MAX) -> int:
    """清理 data/logs 里过期的日志与页面导出，返回删掉的文件数。

    规则：超过 days 天的直接删；剩下的超过 keep_max 个就从最旧的开始删。
    只动 data/logs 目录里符合已知命名规则的文件，不碰子目录、不碰其它文件。
    """
    try:
        directory = paths.logs_dir()
    except Exception:
        return 0
    cutoff = time.time() - max(1, days) * 86400
    removed = 0
    survivors: list[tuple[float, object]] = []
    for pattern in _LOG_PATTERNS:
        for item in directory.glob(pattern):
            try:
                if not item.is_file():
                    continue
                mtime = item.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    pass
            else:
                survivors.append((mtime, item))
    if keep_max > 0 and len(survivors) > keep_max:
        survivors.sort(key=lambda entry: entry[0])
        for _mtime, item in survivors[: len(survivors) - keep_max]:
            try:
                item.unlink()
                removed += 1
            except OSError:
                pass
    return removed


class Logger:
    def __init__(self, bridge: Bridge, echo: bool = True):
        self.bridge = bridge
        self.echo = echo
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = paths.logs_dir() / f"run-{stamp}.log"

    # ---------- 基础 ----------
    def _write(self, level: str, message: str) -> None:
        line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] [{level}] {message}"
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass
        if self.echo:
            self.bridge.log(message, level)

    def info(self, message: str) -> None:
        self._write("信息", message)

    def success(self, message: str) -> None:
        self._write("完成", message)

    def warn(self, message: str) -> None:
        self._write("警告", message)

    def error(self, message: str) -> None:
        self._write("错误", message)

    def debug(self, message: str) -> None:
        self._write("调试", message)

    def section(self, title: str) -> None:
        self._write("信息", f"==== {title} ====")

    def exception(self, message: str, exc: BaseException | None = None) -> None:
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip() if exc else ""
        self._write("错误", f"{message} {detail}".strip())
        if exc is not None:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(traceback.format_exc() + "\n")
            except OSError:
                pass
