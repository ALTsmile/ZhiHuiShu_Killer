"""检查 GitHub 上的新版本，并把发布包更新到本机。

只用标准库：读 releases/latest 拿版本号、更新日志和发布包地址（zip），
下载后解压覆盖程序目录 —— **data/ 里的账号、Cookie、配置、日志一律不动**。
"""

from __future__ import annotations

import json
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .version import __version__

REPO = "ALTsmile/ZhiHuiShu_Killer"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"{REPO_URL}/releases"
USER_AGENT = "ZhiHuiShu-Killer-Updater"
TIMEOUT = 10.0


@dataclass
class UpdateInfo:
    version: str                  # 例如 "1.0.3"
    tag: str                      # "v1.0.3"
    notes: str = ""               # 更新日志（Release 正文）
    asset_url: str = ""           # 发布包 zip 的下载地址
    asset_name: str = ""
    page_url: str = RELEASES_PAGE


def parse_version(text: str) -> tuple[int, ...]:
    """把 "v1.2.3" / "1.2.3-beta" 这类字符串变成可比较的数字元组。"""
    cleaned = str(text or "").strip().lstrip("vV")
    numbers: list[int] = []
    for chunk in cleaned.replace("-", ".").replace("_", ".").split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if digits:
            numbers.append(int(digits))
        elif numbers:
            break
    return tuple(numbers)


def is_newer(latest: str, current: str = __version__) -> bool:
    """latest 是否比 current 新（按数字段比较，缺失的段当 0）。"""
    left, right = parse_version(latest), parse_version(current)
    if not left:
        return False
    length = max(len(left), len(right))
    left = left + (0,) * (length - len(left))
    right = right + (0,) * (length - len(right))
    return left > right


def _system_proxy() -> str | None:
    """读 Windows 系统代理。

    urllib 默认**不认**系统代理（只认环境变量），而国内机器上代理
    往往只配在系统设置里；带代理的机器直连 GitHub 会失败，所以这里兜一手。
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except Exception:
        return None
    server = str(server or "").strip()
    if not server:
        return None
    if "=" in server:                 # 形如 "http=1.2.3.4:8080;https=1.2.3.4:8080"
        pairs = dict(part.split("=", 1) for part in server.split(";") if "=" in part)
        server = (pairs.get("https") or pairs.get("http") or "").strip()
    if not server:
        return None
    return server if server.startswith("http") else f"http://{server}"


def _open(request: urllib.request.Request, timeout: float, proxy: str | None = None):
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        return urllib.request.build_opener(handler).open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def _read_bytes(request: urllib.request.Request, timeout: float = TIMEOUT) -> bytes:
    """先直连，失败且系统配了代理时再走代理重试一次。"""
    try:
        with _open(request, timeout) as response:
            return response.read()
    except Exception:
        auto = _system_proxy()
        if not auto:
            raise
        with _open(request, timeout, auto) as response:
            return response.read()


def check(current: str = __version__, timeout: float = TIMEOUT) -> UpdateInfo | None:
    """检查有没有新版本；没有（或获取失败）返回 None / 抛异常。"""
    request = urllib.request.Request(
        RELEASES_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    payload = json.loads(_read_bytes(request, timeout).decode("utf-8"))
    tag = str(payload.get("tag_name") or "").strip()
    latest = tag.lstrip("vV")
    if not latest or not is_newer(latest, current):
        return None
    asset_name, asset_url = "", ""
    for item in payload.get("assets") or []:
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or "")
        if name.lower().endswith(".zip") and url:
            asset_name, asset_url = name, url
            break
    return UpdateInfo(
        version=latest,
        tag=tag or f"v{latest}",
        notes=str(payload.get("body") or "").strip(),
        asset_url=asset_url,
        asset_name=asset_name,
        page_url=str(payload.get("html_url") or RELEASES_PAGE),
    )


def download(url: str, target: Path, progress=None, timeout: float = 60.0) -> Path:
    """下载发布包到 target，边下边回调 progress(已下载字节, 总字节)。"""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    target.parent.mkdir(parents=True, exist_ok=True)

    def _stream(response) -> None:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with open(target, "wb") as handle:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if progress:
                    try:
                        progress(done, total)
                    except Exception:
                        pass

    try:
        with _open(request, timeout) as response:
            _stream(response)
    except Exception:
        auto = _system_proxy()
        if not auto:
            raise
        with _open(request, timeout, auto) as response:
            _stream(response)
    return target


def _strip_common_root(names: list[str]) -> int:
    """发布包里所有文件都在同一个顶层目录下时，返回 1（要剥掉这一层）。

    我们的发布包就是「智慧树杀手_ZhiHuiShu_Killer/ 里面放全部文件」这种。
    只有当**每个**成员都在同一层目录里（没有顶层散文件）时才剥壳，
    否则保持原样，免得把文件挪到莫名其妙的位置。
    """
    parts_list = [PurePosixPath(name).parts for name in names]
    parts_list = [parts for parts in parts_list if parts]
    if not parts_list:
        return 0
    if any(len(parts) < 2 for parts in parts_list):
        return 0
    if len({parts[0] for parts in parts_list}) != 1:
        return 0
    return 1


def apply_zip(zip_path: Path, root: Path, keep: tuple[str, ...] = ("data",)) -> int:
    """把发布包解压覆盖到程序目录，返回写入的文件数。

    keep 里的顶层目录不覆盖（默认只保护 data/：账号、Cookie、配置、日志都在这儿）。
    """
    root = Path(root).resolve()
    keep_names = {name.lower() for name in keep}
    with zipfile.ZipFile(zip_path) as archive:
        names = [item.filename for item in archive.infolist() if not item.is_dir()]
        strip = _strip_common_root(names)
        written = 0
        for member in archive.infolist():
            if member.is_dir():
                continue
            parts = PurePosixPath(member.filename).parts[strip:]
            if not parts or parts[0].lower() in keep_names:
                continue
            if any(part in ("..", "") for part in parts):
                continue                    # 防目录穿越
            target = root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle)
            written += 1
    return written
