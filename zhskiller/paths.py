"""程序运行目录与文件路径的统一出口。

打包成 exe 后 ``sys.frozen`` 为真，此时数据目录取 exe 同级目录；
源码运行时取项目根目录。这样用户改配置、看日志都不用进到包里翻。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    """程序根目录（exe 同级目录 / 项目根目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    path = app_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def credentials_path() -> Path:
    return data_dir() / "accounts.dat"


def reset_login_flag() -> Path:
    """存在这个文件时，下次启动会清空浏览器里的登录凭证（等于"退出登录"）。"""
    return data_dir() / "reset_login.flag"


def profile_dir() -> Path:
    """「新建浏览器」模式使用的独立用户数据目录。

    用独立目录而不是用户日常的 Chrome 配置，原因有两个：
    1. 不会和用户正在使用的浏览器抢占 profile 锁；
    2. 登录状态天然持久化，下次运行免登录。
    """
    path = data_dir() / "browser_profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource(*parts: str) -> Path:
    return app_root().joinpath("resources", *parts)


def which_browser(driver: str) -> str | None:
    """按驱动名查找浏览器可执行文件的默认位置。"""
    driver = (driver or "").strip().lower()
    candidates: list[str] = []
    if driver == "edge":
        candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        ]
    elif driver == "chrome":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None
