"""账号密码的本地保存。

Windows 上使用系统自带的 DPAPI（CryptProtectData）加密：
密文只能被当前 Windows 用户解开，把 data/accounts.dat 拷到别的机器也没用。
非 Windows 或 DPAPI 不可用时退化为简单混淆存储，并给出提示。
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

from . import paths

_PREFIX_DPAPI = "dpapi:"
_PREFIX_OBFUSCATED = "obf:"
_OBFUSCATION_KEY = b"ZhiHuiShu_KILLER"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


class _Buffer:
    """同时持有 ctypes 缓冲区与 DATA_BLOB，保证指针不会指向已释放的内存。"""

    def __init__(self, data: bytes):
        self.buffer = ctypes.create_string_buffer(data, len(data))
        self.blob = _DataBlob(len(data), ctypes.cast(self.buffer, ctypes.POINTER(ctypes.c_char)))

    @property
    def byref(self):
        return ctypes.byref(self.blob)


def _bytes_from_blob(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _dpapi_available() -> bool:
    return sys.platform == "win32"


_ARG_TYPES_SET = False


def _configure_crypto() -> None:
    """显式声明参数类型；不声明的话 64 位下指针会被当成 int 截断。"""
    global _ARG_TYPES_SET
    if _ARG_TYPES_SET:
        return
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_ptr = ctypes.POINTER(_DataBlob)
    for name in ("CryptProtectData", "CryptUnprotectData"):
        function = getattr(crypt32, name)
        function.argtypes = [
            blob_ptr,
            wintypes.LPCWSTR,
            blob_ptr,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_ptr,
        ]
        function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    _ARG_TYPES_SET = True


def _dpapi_protect(plain: bytes) -> bytes:
    """先按当前用户加密；某些环境下用户配置未加载，退回本机范围。"""
    errors = []
    for flags in (0, _CRYPTPROTECT_LOCAL_MACHINE):
        try:
            return _dpapi_protect_with_flags(plain, flags)
        except OSError as exc:
            errors.append(str(exc))
    raise OSError("; ".join(errors))


_CRYPTPROTECT_LOCAL_MACHINE = 0x04


def _dpapi_protect_with_flags(plain: bytes, flags: int) -> bytes:
    _configure_crypto()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source = _Buffer(plain)
    blob_out = _DataBlob()
    ok = crypt32.CryptProtectData(
        source.byref,
        None,
        None,
        None,
        None,
        flags,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(f"CryptProtectData 调用失败（flags={flags}）")
    try:
        return _bytes_from_blob(blob_out)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(cipher: bytes) -> bytes:
    _configure_crypto()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source = _Buffer(cipher)
    blob_out = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        source.byref,
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError("CryptUnprotectData 调用失败")
    try:
        return _bytes_from_blob(blob_out)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _obfuscate(plain: bytes) -> bytes:
    key = _OBFUSCATION_KEY
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(plain))


def load_credentials(path: Path | None = None) -> dict | None:
    """读取已保存的账号密码；没有或解不开时返回 None。"""
    target = Path(path) if path else paths.credentials_path()
    if not target.is_file():
        return None
    try:
        raw = target.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        if raw.startswith(_PREFIX_DPAPI):
            payload = _dpapi_unprotect(base64.b64decode(raw[len(_PREFIX_DPAPI):]))
        elif raw.startswith(_PREFIX_OBFUSCATED):
            payload = _obfuscate(base64.b64decode(raw[len(_PREFIX_OBFUSCATED):]))
        else:
            return None
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not username or not password:
        return None
    return {
        "username": username,
        "password": password,
        "method": str(data.get("method") or "account"),
        "school": str(data.get("school") or "").strip(),
    }


def save_credentials(username: str, password: str, path: Path | None = None,
                     method: str = "account", school: str = "") -> str:
    """保存账号密码，返回实际使用的存储方式（用于界面提示）。"""
    target = Path(path) if path else paths.credentials_path()
    payload = json.dumps(
        {
            "username": username,
            "password": password,
            "method": method,
            "school": school,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    if _dpapi_available():
        try:
            blob = _dpapi_protect(payload)
            text = _PREFIX_DPAPI + base64.b64encode(blob).decode("ascii")
            method = "dpapi"
        except Exception:
            text = _PREFIX_OBFUSCATED + base64.b64encode(_obfuscate(payload)).decode("ascii")
            method = "obfuscated"
    else:
        text = _PREFIX_OBFUSCATED + base64.b64encode(_obfuscate(payload)).decode("ascii")
        method = "obfuscated"

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".dat.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return method


def clear_credentials(path: Path | None = None) -> None:
    target = Path(path) if path else paths.credentials_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
