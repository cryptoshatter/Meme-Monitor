from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = "GMGN_Meme_Monitor"
RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _target_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    root = Path(__file__).resolve().parents[2]
    return f'"{sys.executable}" "{root / "run.py"}"'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, RUN_KEY)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_KEY, 0, winreg.REG_SZ, _target_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_KEY)
            except FileNotFoundError:
                pass
