from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import app_data_dir

EVENT_LIMIT = 50


def event_path() -> Path:
    return app_data_dir() / "event_timeline.json"


def load_events() -> list[dict[str, Any]]:
    path = event_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    events = [normalize_event(item) for item in data if isinstance(item, dict)]
    events = [event for event in events if event.get("kind") and event.get("title") and event.get("kind") != "switch"]
    events.sort(key=lambda item: float(item.get("received_at") or 0.0), reverse=True)
    return events[:EVENT_LIMIT]


def append_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    item = normalize_event(event)
    if not item.get("kind") or not item.get("title"):
        return load_events()
    if item.get("kind") == "switch":
        return load_events()
    events = load_events()
    key = event_key(item)
    events = [existing for existing in events if event_key(existing) != key]
    events.insert(0, item)
    events = events[:EVENT_LIMIT]
    save_events(events)
    return events


def save_events(events: list[dict[str, Any]]) -> None:
    path = event_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = [normalize_event(item) for item in events[:EVENT_LIMIT] if isinstance(item, dict)]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    received_at = event.get("received_at") or time.time()
    try:
        received_at = float(received_at)
    except (TypeError, ValueError):
        received_at = time.time()
    timestamp = event.get("timestamp")
    try:
        timestamp = int(timestamp) if timestamp is not None else None
    except (TypeError, ValueError):
        timestamp = None
    kind = str(event.get("kind") or "").strip()[:24]
    title = str(event.get("title") or "").strip()[:80]
    subtitle = str(event.get("subtitle") or "").strip()[:140]
    chain = str(event.get("chain") or "").lower().strip()[:12]
    address = str(event.get("address") or "").strip()
    if address.startswith(("0x", "0X")):
        address = address.lower()
    return {
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "chain": chain,
        "address": address,
        "side": str(event.get("side") or "").lower().strip()[:12],
        "timestamp": timestamp,
        "received_at": received_at,
        "logo_url": str(event.get("logo_url") or "").strip(),
        "value": str(event.get("value") or "").strip()[:60],
    }


def event_key(event: dict[str, Any]) -> str:
    return "|".join(
        [
            str(event.get("kind") or ""),
            str(event.get("chain") or ""),
            str(event.get("address") or ""),
            str(event.get("side") or ""),
            str(event.get("timestamp") or ""),
            str(event.get("title") or ""),
        ]
    )
