"""登录 Cookie 的落盘与回注。

智慧树的登录凭证里有会话级 Cookie，浏览器一关就可能丢，
所以每次登录成功后把 Cookie 存到 data/cookies.json，
下次开浏览器时先注回去，能显著减少重复扫码。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import paths, site


def cookie_path() -> Path:
    return paths.data_dir() / "cookies.json"


def load(path: Path | None = None) -> list[dict]:
    target = Path(path) if path else cookie_path()
    if not target.is_file():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        domain = str(item.get("domain") or "")
        if not name or "zhihuishu.com" not in domain:
            continue
        cookie = {
            "name": name,
            "value": str(item.get("value") or ""),
            "domain": domain,
            "path": item.get("path") or "/",
        }
        if isinstance(item.get("expires"), (int, float)) and item["expires"] > 0:
            cookie["expires"] = float(item["expires"])
        if item.get("httpOnly"):
            cookie["httpOnly"] = True
        if item.get("secure"):
            cookie["secure"] = True
        if item.get("sameSite") in ("Strict", "Lax", "None"):
            cookie["sameSite"] = item["sameSite"]
        cleaned.append(cookie)
    return cleaned


def save(context, path: Path | None = None) -> int:
    target = Path(path) if path else cookie_path()
    try:
        cookies = context.cookies(site.COOKIE_URLS)
    except Exception:
        return 0
    if not cookies:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return len(cookies)


def restore(context, path: Path | None = None) -> int:
    cookies = load(path)
    if not cookies:
        return 0
    try:
        context.add_cookies(cookies)
    except Exception:
        return 0
    return len(cookies)


def clear(path: Path | None = None) -> None:
    target = Path(path) if path else cookie_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
