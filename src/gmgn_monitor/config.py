from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .gmgn_client import possible_wallet_chains

APP_DIR_NAME = "GMGN Meme Monitor"
DEFAULT_HOST = "https://openapi.gmgn.ai"
DEFAULT_CHAIN = "sol"
DEFAULT_ADDRESS = "So11111111111111111111111111111111111111112"
DEFAULT_WALLET_AVATAR = "\U0001f9e9"
AVAILABLE_SKINS = {"default", "okx", "binance", "gmgn", "claude"}


def app_data_dir() -> Path:
    frozen = getattr(sys, "frozen", False)
    if frozen:
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parents[2]
    path = root / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / "config.json"


def config_backup_path() -> Path:
    return app_data_dir() / "config.json.bak"


def log_path() -> Path:
    return app_data_dir() / "monitor.log"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_dotenv() -> None:
    candidates = [project_root() / ".env", Path.cwd() / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass(slots=True)
class WalletConfig:
    remark: str = ""
    address: str = ""
    chain: str = ""
    chains: list[str] | None = None
    avatar_kind: str = "emoji"
    avatar_value: str = DEFAULT_WALLET_AVATAR


@dataclass(slots=True)
class TokenConfig:
    chain: str = DEFAULT_CHAIN
    address: str = DEFAULT_ADDRESS
    symbol: str = ""
    name: str = ""
    remark: str = ""
    enabled: bool = True
    pinned: bool = True


@dataclass(slots=True)
class AppConfig:
    chain: str = DEFAULT_CHAIN
    address: str = DEFAULT_ADDRESS
    refresh_interval_ms: int = 1000
    locked: bool = False
    x: int | None = None
    y: int | None = None
    api_host: str = DEFAULT_HOST
    autostart: bool = False
    wallet_remark: str = ""
    wallet_address: str = ""
    wallet_chain: str = ""
    wallet_avatar_kind: str = "emoji"
    wallet_avatar_value: str = DEFAULT_WALLET_AVATAR
    wallets: list[dict[str, Any]] | None = None
    tokens: list[dict[str, Any]] | None = None
    skin: str = "default"
    portfolio_wallet_address: str = ""
    portfolio_evm_wallet_address: str = ""
    portfolio_sol_wallet_address: str = ""
    portfolio_holdings_cache: list[dict[str, Any]] | None = None

    @classmethod
    def load(cls) -> "AppConfig":
        path = config_path()
        backup_path = config_backup_path()
        data = read_config_data(path)
        backup_data = read_config_data(backup_path)
        should_restore_primary = False

        if data is None:
            if backup_data is None:
                return cls()
            data = backup_data
            should_restore_primary = True
        elif backup_data and is_empty_default_config(data) and has_user_config_state(backup_data):
            data = backup_data
            should_restore_primary = True

        try:
            cfg = cls.from_data(data)
        except Exception:
            if backup_data is None:
                return cls()
            cfg = cls.from_data(backup_data)
            should_restore_primary = True

        if should_restore_primary:
            write_config_data(path, asdict(cfg))
        return cfg

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "AppConfig":
        cfg = cls()
        for field in asdict(cfg):
            if field in data:
                setattr(cfg, field, data[field])
        cfg.refresh_interval_ms = max(100, int(cfg.refresh_interval_ms))
        cfg.chain = str(cfg.chain).lower().strip() or DEFAULT_CHAIN
        cfg.address = str(cfg.address).strip() or DEFAULT_ADDRESS
        cfg.api_host = str(cfg.api_host).rstrip("/") or DEFAULT_HOST
        cfg.skin = normalize_skin_name(cfg.skin)
        cfg.wallet_remark = str(cfg.wallet_remark or "").strip()
        if looks_mojibake(cfg.wallet_remark):
            cfg.wallet_remark = "Wallet"
        cfg.wallet_address = str(cfg.wallet_address or "").strip()
        cfg.wallet_chain = str(cfg.wallet_chain or "").lower().strip()
        cfg.wallet_avatar_kind = str(cfg.wallet_avatar_kind or "emoji").lower().strip()
        if cfg.wallet_avatar_kind not in {"emoji", "image"}:
            cfg.wallet_avatar_kind = "emoji"
        cfg.wallet_avatar_value = str(cfg.wallet_avatar_value or "").strip()
        if not cfg.wallet_avatar_value:
            cfg.wallet_avatar_kind = "emoji"
            cfg.wallet_avatar_value = DEFAULT_WALLET_AVATAR
        cfg.portfolio_wallet_address = normalize_plain_wallet_address(cfg.portfolio_wallet_address)
        cfg.portfolio_evm_wallet_address = normalize_plain_wallet_address(cfg.portfolio_evm_wallet_address)
        cfg.portfolio_sol_wallet_address = normalize_plain_wallet_address(cfg.portfolio_sol_wallet_address)
        if cfg.portfolio_wallet_address and not (cfg.portfolio_evm_wallet_address or cfg.portfolio_sol_wallet_address):
            if cfg.portfolio_wallet_address.startswith("0x"):
                cfg.portfolio_evm_wallet_address = cfg.portfolio_wallet_address
            else:
                cfg.portfolio_sol_wallet_address = cfg.portfolio_wallet_address
        cfg.portfolio_holdings_cache = normalize_portfolio_holdings_cache(data.get("portfolio_holdings_cache"))
        cfg.wallets = normalize_wallets(data)
        cfg.tokens = normalize_tokens(data)
        pinned = primary_token(cfg.tokens)
        cfg.chain = pinned["chain"]
        cfg.address = pinned["address"]
        return cfg

    def save(self) -> None:
        path = config_path()
        backup_path = config_backup_path()
        data = asdict(self)
        existing = read_config_data(path)
        backup_data = read_config_data(backup_path)

        if is_empty_default_config(data) and existing and has_user_config_state(existing):
            restored = dict(existing)
            for key in (
                "x",
                "y",
                "locked",
                "autostart",
                "refresh_interval_ms",
                "skin",
                "portfolio_wallet_address",
                "portfolio_evm_wallet_address",
                "portfolio_sol_wallet_address",
                "portfolio_holdings_cache",
            ):
                restored[key] = data.get(key)
            data = restored
            restored_cfg = self.from_data(data)
            for field, value in asdict(restored_cfg).items():
                setattr(self, field, value)
        elif is_empty_default_config(data) and backup_data and has_user_config_state(backup_data):
            restored = dict(backup_data)
            for key in (
                "x",
                "y",
                "locked",
                "autostart",
                "refresh_interval_ms",
                "skin",
                "portfolio_wallet_address",
                "portfolio_evm_wallet_address",
                "portfolio_sol_wallet_address",
                "portfolio_holdings_cache",
            ):
                restored[key] = data.get(key)
            data = restored
            restored_cfg = self.from_data(data)
            for field, value in asdict(restored_cfg).items():
                setattr(self, field, value)

        if existing is not None:
            preserve_backup = is_empty_default_config(existing) and backup_data and has_user_config_state(backup_data)
            if not preserve_backup:
                write_config_data(backup_path, existing)

        write_config_data(path, data)

    def api_key(self) -> str:
        from .credentials import load_api_key

        saved_key = load_api_key()
        if saved_key:
            return saved_key
        load_dotenv()
        return os.environ.get("GMGN_API_KEY", "").strip()


def clamp_refresh_interval(ms: int) -> int:
    return max(100, min(int(ms), 60_000))


def normalize_skin_name(value: object) -> str:
    raw = str(value or "").lower().strip()
    aliases = {"origin": "default", "original": "default", "base": "default", "原皮": "default"}
    raw = aliases.get(raw, raw)
    return raw if raw in AVAILABLE_SKINS else "default"


def read_config_data(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_config_data(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def has_user_config_state(data: dict[str, Any]) -> bool:
    address = str(data.get("address") or "").strip()
    if address and address != DEFAULT_ADDRESS:
        return True
    if normalize_tokens(data, include_default=False):
        return True
    if str(data.get("portfolio_wallet_address") or "").strip():
        return True
    if str(data.get("portfolio_evm_wallet_address") or "").strip():
        return True
    if str(data.get("portfolio_sol_wallet_address") or "").strip():
        return True
    return bool(normalize_wallets(data))


def is_empty_default_config(data: dict[str, Any]) -> bool:
    chain = str(data.get("chain") or DEFAULT_CHAIN).lower().strip()
    address = str(data.get("address") or DEFAULT_ADDRESS).strip()
    return chain == DEFAULT_CHAIN and address == DEFAULT_ADDRESS and not normalize_wallets(data) and not normalize_tokens(data, include_default=False)


def looks_mojibake(value: str) -> bool:
    markers = (
        "\u00c3",
        "\u00c2",
        "\u00d0",
        "\u00d1",
        "\u5a34",
        "\u9356",
        "\u95bd",
        "\u9429",
        "\u621e",
        "\u5e26",
        "\u762f",
    )
    return any(marker in value for marker in markers)


def normalize_wallets(data: dict[str, Any]) -> list[dict[str, Any]]:
    wallets: list[dict[str, Any]] = []
    raw_wallets = data.get("wallets")
    if isinstance(raw_wallets, list):
        for item in raw_wallets:
            if not isinstance(item, dict):
                continue
            normalized = normalize_wallet(item)
            if normalized["address"]:
                wallets.append(normalized)

    if not wallets:
        legacy = normalize_wallet(
            {
                "remark": data.get("wallet_remark", ""),
                "address": data.get("wallet_address", ""),
                "chain": data.get("wallet_chain", ""),
                "avatar_kind": data.get("wallet_avatar_kind", "emoji"),
                "avatar_value": data.get("wallet_avatar_value", DEFAULT_WALLET_AVATAR),
            }
        )
        if legacy["address"]:
            wallets.append(legacy)

    return wallets


def normalize_tokens(data: dict[str, Any], include_default: bool = True) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    raw_tokens = data.get("tokens")
    if isinstance(raw_tokens, list):
        for item in raw_tokens:
            if not isinstance(item, dict):
                continue
            normalized = normalize_token(item)
            if normalized["address"]:
                tokens.append(normalized)

    legacy = normalize_token(
        {
            "chain": data.get("chain", DEFAULT_CHAIN),
            "address": data.get("address", DEFAULT_ADDRESS),
            "enabled": True,
            "pinned": True,
        }
    )
    has_legacy = legacy["address"] and (include_default or legacy["address"] != DEFAULT_ADDRESS)
    if has_legacy and not any(token_key(token) == token_key(legacy) for token in tokens):
        tokens.insert(0, legacy)

    if not tokens and include_default:
        tokens.append(legacy)

    if not any(token.get("pinned") and token.get("enabled", True) for token in tokens):
        for token in tokens:
            if token.get("enabled", True):
                token["pinned"] = True
                break
    if not any(token.get("pinned") for token in tokens) and tokens:
        tokens[0]["pinned"] = True

    pinned_seen = False
    for token in tokens:
        if token.get("pinned"):
            if pinned_seen:
                token["pinned"] = False
            else:
                pinned_seen = True
    return tokens


def normalize_token(item: dict[str, Any]) -> dict[str, Any]:
    chain = str(item.get("chain") or "").lower().strip()
    address = str(item.get("address") or item.get("ca") or "").strip()
    if address.startswith(("0x", "0X")):
        address = address.lower()
    symbol = str(item.get("symbol") or "").strip()[:18]
    name = str(item.get("name") or "").strip()[:42]
    remark = str(item.get("remark") or "").strip()[:28]
    logo_url = str(item.get("logo_url") or "").strip()
    alert_threshold = normalize_alert_threshold(item.get("alert_threshold_percent"))
    token = {
        "chain": chain,
        "address": address,
        "symbol": symbol,
        "name": name,
        "remark": remark,
        "logo_url": logo_url,
        "alert_threshold_percent": alert_threshold,
        "enabled": bool(item.get("enabled", True)),
        "pinned": bool(item.get("pinned", False)),
    }
    for key in ("last_market_cap", "last_price", "last_change_percent", "last_volume_24h", "last_alert_at", "alert_count"):
        if key in item:
            token[key] = item.get(key)
    return token


def normalize_alert_threshold(value: Any) -> float | None:
    if value is None:
        return None
    raw = str(value).strip().rstrip("%")
    if not raw:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.1, min(number, 100.0))


def primary_token(tokens: list[dict[str, Any]] | None) -> dict[str, Any]:
    normalized = tokens or []
    for token in normalized:
        if token.get("pinned") and token.get("enabled", True):
            return token
    for token in normalized:
        if token.get("pinned"):
            return token
    for token in normalized:
        if token.get("enabled", True):
            return token
    return normalize_token({"chain": DEFAULT_CHAIN, "address": DEFAULT_ADDRESS, "pinned": True})


def token_key(token: dict[str, Any]) -> str:
    return f"{str(token.get('chain') or '').lower()}:{str(token.get('address') or '').lower()}"


def normalize_wallet(item: dict[str, Any]) -> dict[str, Any]:
    remark = str(item.get("remark") or item.get("wallet_remark") or "").strip()
    if looks_mojibake(remark):
        remark = "Wallet"
    address = str(item.get("address") or item.get("wallet_address") or "").strip()
    if address.startswith(("0x", "0X")):
        address = address.lower()
    chain = str(item.get("chain") or item.get("wallet_chain") or "").lower().strip()
    chains = normalize_chains(item.get("chains"), address, chain)
    if chains:
        chain = chains[0]
    avatar_kind = str(item.get("avatar_kind") or item.get("wallet_avatar_kind") or "emoji").lower().strip()
    if avatar_kind not in {"emoji", "image"}:
        avatar_kind = "emoji"
    avatar_value = str(item.get("avatar_value") or item.get("wallet_avatar_value") or "").strip()
    if not avatar_value:
        avatar_kind = "emoji"
        avatar_value = DEFAULT_WALLET_AVATAR
    group = str(item.get("group") or item.get("wallet_group") or "").strip()[:18]
    min_native_amount = normalize_wallet_float(item.get("min_native_amount"), 0.0, 999999.0, 0.0)
    repeat_seconds = normalize_wallet_int(item.get("repeat_seconds"), 0, 3600, 8)
    first_buy_only = bool(item.get("first_buy_only", False))
    return {
        "remark": remark or "Wallet",
        "address": address,
        "chain": chain,
        "chains": chains,
        "group": group or "默认",
        "min_native_amount": min_native_amount,
        "repeat_seconds": repeat_seconds,
        "first_buy_only": first_buy_only,
        "avatar_kind": avatar_kind,
        "avatar_value": avatar_value,
    }


def normalize_wallet_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


def normalize_wallet_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


def normalize_plain_wallet_address(address: object) -> str:
    value = str(address or "").strip()
    if value.startswith(("0x", "0X")):
        return value.lower()
    return value


def normalize_portfolio_holdings_cache(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cache: list[dict[str, Any]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        clean = dict(item)
        clean.pop("_logo_pixmap", None)
        clean.pop("raw", None)
        cache.append(clean)
    return cache


def normalize_chains(value: Any, address: str = "", preferred: str = "") -> list[str]:
    if isinstance(value, str):
        raw = [part.strip().lower() for part in value.split(",")]
    elif isinstance(value, list):
        raw = [str(part).lower().strip() for part in value]
    else:
        raw = []
    preferred = preferred.lower().strip()
    if preferred and preferred not in raw:
        raw.insert(0, preferred)
    valid_for_address = possible_wallet_chains(address, preferred) if address else []
    ordered: list[str] = []
    for chain in raw + [chain for chain in valid_for_address if chain not in raw]:
        if chain and (not valid_for_address or chain in valid_for_address) and chain not in ordered:
            ordered.append(chain)
    return ordered
