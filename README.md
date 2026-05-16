# GMGN Meme Floating Monitor

GMGN Meme coin realtime desktop floating monitor, built with Python, PySide6, QThread, requests, websocket-client, Pillow, and PyInstaller.

## What It Does

- No main window, only a system tray icon and floating desktop card.
- Always-on-top dark glass card docked near the right side of the desktop.
- Draggable, lockable, high-DPI aware, low-flicker UI.
- Fetches token info from GMGN OpenAPI only.
- Uses a worker QThread so network latency never blocks the UI.
- Saves CA, chain, position, refresh interval, and lock state to JSON.
- Handles network errors with backoff and handles API rate limits with cooldown.

## GMGN Docs Notes Applied

The Chinese GMGN docs at https://docs.gmgn.ai/cn say the old data scraping IP whitelist is discontinued and users should use the standardized OpenAPI docs at https://github.com/GMGNAI/gmgn-skills. The same docs also warn that the default API rate limit is 1 request/second.

Because of that, this app defaults to a 1 second refresh interval. Faster intervals are available from the menu for short bursts, but the worker automatically backs off on HTTP/API errors and respects 429 cooldowns.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Repository Layout

```text
src/                 Source code before packaging
tools/               Installer helper script
release/             Ready-to-share Windows installer
GMGN_Meme_Monitor.spec
GMGN_Meme_Monitor_Setup.spec
```

The installer in `release/GMGN_Meme_Monitor_Setup.exe` is separated from the source tree for GitHub distribution.

## Run

```powershell
python run.py
```

Or after editable install:

```powershell
pip install -e .
python -m gmgn_monitor
```

On first launch, the app asks the user to enter a GMGN API Key and provides a button to open the GMGN API Key page. On Windows the saved key is protected with DPAPI in the app data folder:

```text
data\credentials.json
```

For development, `GMGN_API_KEY` from the environment or local `.env` is still supported when no saved key exists.

## Change Token

Right click the card or tray icon, then choose `Modify CA`. Supported chains:

- `sol`
- `bsc`
- `base`
- `eth`

## Build EXE

```powershell
pyinstaller GMGN_Meme_Monitor.spec --clean --noconfirm
```

Output:

```text
dist\GMGN_Meme_Monitor\GMGN_Meme_Monitor.exe
```

The setup EXE asks whether to choose an install directory. Choose `Yes` to open a folder picker, or `No` to install to the default `%LOCALAPPDATA%\Programs\GMGN Meme Monitor` path. Reinstalling preserves the installed `data` directory.

## Runtime Files

Config:

```text
data\config.json
```

Log:

```text
data\monitor.log
```

In source mode, `data` is created under the project root. In packaged mode, `data` is created next to `GMGN_Meme_Monitor.exe` inside the selected install directory.
