# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import zipfile
from ctypes import wintypes
from pathlib import Path


APP_NAME = "GMGN Meme Monitor"
EXE_NAME = "GMGN_Meme_Monitor.exe"


def message(title: str, body: str, flags: int = 0x40) -> None:
    ctypes.windll.user32.MessageBoxW(None, body, title, flags)


def ask(title: str, body: str) -> bool:
    return ctypes.windll.user32.MessageBoxW(None, body, title, 0x24) == 6


def ask_install_choice() -> int:
    return ctypes.windll.user32.MessageBoxW(
        None,
        "请选择安装方式：\n\n是：选择安装位置\n否：使用默认位置\n取消：退出安装",
        APP_NAME,
        0x23,
    )


def payload_zip() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "payload.zip"  # type: ignore[attr-defined]
    return Path(__file__).with_name("payload.zip")


def default_install_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "Programs" / APP_NAME


class _BrowseInfo(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", ctypes.c_void_p),
        ("lParam", wintypes.LPARAM),
        ("iImage", ctypes.c_int),
    ]


def choose_install_dir() -> Path | None:
    choice = ask_install_choice()
    if choice == 2:
        return None
    if choice == 7:
        return default_install_dir()

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(_BrowseInfo)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    co_initialized = ole32.CoInitialize(None) >= 0
    display = ctypes.create_unicode_buffer(260)
    info = _BrowseInfo()
    info.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
    info.lpszTitle = "请选择安装目录，程序会安装到该目录下的 GMGN Meme Monitor 文件夹。"
    info.ulFlags = 0x0001 | 0x0010 | 0x0040
    try:
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return None
        path_buffer = ctypes.create_unicode_buffer(260)
        if not shell32.SHGetPathFromIDListW(pidl, path_buffer):
            return None
        base = Path(path_buffer.value)
        return base if base.name == APP_NAME else base / APP_NAME
    finally:
        if "pidl" in locals() and pidl:
            ole32.CoTaskMemFree(pidl)
        if co_initialized:
            ole32.CoUninitialize()


def desktop_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def start_menu_dir() -> Path:
    root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(root) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME


def create_shortcut(link_path: Path, target: Path, working_dir: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{ps_quote(link_path)}'); "
        f"$s.TargetPath = '{ps_quote(target)}'; "
        f"$s.WorkingDirectory = '{ps_quote(working_dir)}'; "
        f"$s.IconLocation = '{ps_quote(target)},0'; "
        "$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
        creationflags=0x08000000,
    )


def ps_quote(path: Path) -> str:
    return str(path).replace("'", "''")


def clear_install_dir(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in target.iterdir():
        if item.name.lower() == "data":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def main() -> int:
    if not ask(
        APP_NAME,
        "安装 GMGN Meme Monitor？\n\n"
        "下一步可以选择安装目录。\n"
        "重装/升级会保留安装目录里的 data 数据。\n"
        "安装包不包含任何内置 API Key。",
    ):
        return 0

    target = choose_install_dir()
    if target is None:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    clear_install_dir(target)

    archive = payload_zip()
    if not archive.exists():
        message(APP_NAME, "安装包缺少 payload.zip，无法安装。", 0x10)
        return 1

    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(target)

    data_dir = target / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    exe = target / EXE_NAME
    if not exe.exists():
        message(APP_NAME, "安装完成但没有找到主程序。", 0x10)
        return 1

    create_shortcut(desktop_dir() / f"{APP_NAME}.lnk", exe, target)
    create_shortcut(start_menu_dir() / f"{APP_NAME}.lnk", exe, target)

    if ask(APP_NAME, f"安装完成。\n\n安装位置：\n{target}\n\n配置、API Key、日志会保存在安装目录的 data 文件夹。\n\n是否现在启动？"):
        subprocess.Popen([str(exe)], cwd=str(target), creationflags=0x08000000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
