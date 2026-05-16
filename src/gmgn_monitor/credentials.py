from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes

from .config import app_data_dir


_ENTROPY = b"GMGN Meme Monitor API Key v1"


def credential_path():
    return app_data_dir() / "credentials.json"


def load_api_key() -> str:
    path = credential_path()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    mode = str(data.get("mode") or "").strip().lower()
    value = str(data.get("value") or "").strip()
    if not value:
        return ""
    try:
        raw = base64.b64decode(value.encode("ascii"))
        if mode == "dpapi":
            return _unprotect_dpapi(raw).decode("utf-8").strip()
        if mode == "base64":
            return raw.decode("utf-8").strip()
    except Exception:
        return ""
    return ""


def save_api_key(api_key: str) -> None:
    api_key = str(api_key or "").strip()
    path = credential_path()
    if not api_key:
        if path.exists():
            path.unlink()
        return
    raw = api_key.encode("utf-8")
    mode = "base64"
    protected = raw
    if os.name == "nt":
        try:
            protected = _protect_dpapi(raw)
            mode = "dpapi"
        except Exception:
            mode = "base64"
            protected = raw
    payload = {
        "mode": mode,
        "value": base64.b64encode(protected).decode("ascii"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob_from_bytes(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _protect_dpapi(data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    in_blob, in_buffer = _blob_from_bytes(data)
    entropy_blob, entropy_buffer = _blob_from_bytes(_ENTROPY)
    out_blob = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    _keep_alive = (in_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        _keep_alive


def _unprotect_dpapi(data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    in_blob, in_buffer = _blob_from_bytes(data)
    entropy_blob, entropy_buffer = _blob_from_bytes(_ENTROPY)
    out_blob = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    _keep_alive = (in_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        _keep_alive
