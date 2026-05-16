from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
import re
from typing import Any

import requests

SUPPORTED_CHAINS = ("sol", "eth", "base", "bsc")
EVM_CHAINS = ("eth", "base", "bsc")


class GmgnApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, reset_at: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reset_at = reset_at


@dataclass(slots=True)
class TokenSnapshot:
    chain: str
    address: str
    symbol: str
    name: str
    logo_url: str
    price: float | None
    market_cap: float | None
    change_percent: float | None
    volume_24h: float | None
    raw: dict[str, Any]
    received_at: float


@dataclass(slots=True)
class WalletActivitySnapshot:
    chain: str
    wallet_address: str
    remark: str
    side: str
    native_amount: float | None
    native_symbol: str
    token_symbol: str
    token_address: str
    token_logo_url: str
    cost_usd: float | None
    timestamp: int | None
    tx_hash: str
    raw: dict[str, Any]
    received_at: float
    avatar_kind: str = "emoji"
    avatar_value: str = ""


SOL_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
CHAIN_NATIVE_ASSETS: dict[str, tuple[str, str]] = {
    "sol": ("SOL", "So11111111111111111111111111111111111111112"),
    "eth": ("ETH", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
    "base": ("ETH", "0x4200000000000000000000000000000000000006"),
    "bsc": ("BNB", "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"),
}
CHAIN_NATIVE_ALIASES: dict[str, set[str]] = {
    "sol": {"sol", "wsol"},
    "eth": {"eth", "weth"},
    "base": {"eth", "weth"},
    "bsc": {"bnb", "wbnb"},
}


class GmgnOpenApiClient:
    def __init__(self, api_key: str, host: str = "https://openapi.gmgn.ai") -> None:
        if not api_key:
            raise ValueError("GMGN_API_KEY is empty")
        self.api_key = api_key
        self.host = host.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-APIKEY": api_key,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "User-Agent": "GMGN-Meme-Floating-Monitor/1.0",
            }
        )
        self._native_price_cache: dict[str, tuple[float, float]] = {}

    def get_token_info(self, chain: str, address: str) -> TokenSnapshot:
        data = self._normal_get("/v1/token/info", {"chain": chain, "address": address})
        return parse_token_snapshot(chain, address, data)

    def get_wallet_activity(
        self,
        chain: str,
        wallet_address: str,
        limit: int = 1,
        activity_types: list[str] | None = None,
    ) -> dict[str, Any]:
        wallet_address = normalize_wallet_address(wallet_address)
        params: dict[str, Any] = {
            "chain": chain,
            "wallet_address": wallet_address,
            "limit": max(1, min(int(limit), 20)),
        }
        if activity_types:
            params["type"] = activity_types
        return self._normal_get(
            "/v1/user/wallet_activity",
            params,
            timeout=(2.8, 4.5),
            retry_network_once=True,
        )

    def get_wallet_holdings(self, chain: str, wallet_address: str, limit: int = 1) -> dict[str, Any]:
        wallet_address = normalize_wallet_address(wallet_address)
        return self._normal_get(
            "/v1/user/wallet_holdings",
            {
                "chain": chain,
                "wallet_address": wallet_address,
                "limit": max(1, min(int(limit), 20)),
                "hide_airdrop": "true",
                "hide_closed": "true",
            },
        )

    def get_latest_wallet_activity(
        self,
        chain: str,
        wallet_address: str,
        remark: str,
        avatar_kind: str = "emoji",
        avatar_value: str = "",
    ) -> WalletActivitySnapshot:
        data = self.get_wallet_activity(chain, wallet_address, limit=20)
        snap = parse_wallet_activity_snapshot(
            chain=chain,
            wallet_address=wallet_address,
            remark=remark,
            avatar_kind=avatar_kind,
            avatar_value=avatar_value,
            data=data,
            native_price_usd=None,
        )
        if snap.side and snap.native_amount is None and snap.cost_usd is not None:
            return parse_wallet_activity_snapshot(
                chain=chain,
                wallet_address=wallet_address,
                remark=remark,
                avatar_kind=avatar_kind,
                avatar_value=avatar_value,
                data=data,
                native_price_usd=self.get_native_price_usd(chain),
            )
        return snap

    def get_native_price_usd(self, chain: str) -> float | None:
        chain = chain.lower().strip()
        now = time.monotonic()
        cached = self._native_price_cache.get(chain)
        if cached and cached[0] > now:
            return cached[1]
        asset = CHAIN_NATIVE_ASSETS.get(chain)
        if not asset:
            return None
        try:
            snap = self.get_token_info(chain, asset[1])
        except Exception:
            return None
        if snap.price is not None and snap.price > 0:
            self._native_price_cache[chain] = (now + 30.0, snap.price)
            return snap.price
        return None

    def detect_wallet_chain(self, wallet_address: str, preferred: str = "") -> str | None:
        chains = self.detect_wallet_chains(wallet_address, preferred)
        return chains[0] if chains else None

    def detect_wallet_chains(self, wallet_address: str, preferred: str = "") -> list[str]:
        chains = possible_wallet_chains(wallet_address, preferred)
        if not chains:
            return []

        scored: list[tuple[int, int, str]] = []
        success: set[str] = set()
        for chain in chains:
            try:
                data = self.get_wallet_activity(chain, wallet_address, limit=5)
            except GmgnApiError:
                continue
            success.add(chain)
            best_timestamp = 0
            for item in _extract_activities(data):
                timestamp = _normalize_timestamp(_first_float(item, ["timestamp", "time", "block_timestamp"]))
                if timestamp > best_timestamp:
                    best_timestamp = timestamp
            if best_timestamp:
                scored.append((2, best_timestamp, chain))

        for chain in chains:
            if chain in success:
                continue
            try:
                data = self.get_wallet_holdings(chain, wallet_address, limit=1)
            except GmgnApiError:
                continue
            if _extract_holdings(data):
                scored.append((1, 0, chain))
                success.add(chain)

        ordered: list[str] = []
        for _, _, chain in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True):
            if chain not in ordered:
                ordered.append(chain)
        for chain in chains:
            if chain not in ordered:
                ordered.append(chain)
        return ordered

    def _normal_get(
        self,
        path: str,
        params: dict[str, Any],
        timeout: tuple[float, float] = (1.6, 3.2),
        retry_network_once: bool = False,
    ) -> dict[str, Any]:
        query = dict(params)
        query["timestamp"] = int(time.time())
        query["client_id"] = str(uuid.uuid4())
        url = f"{self.host}{path}"
        attempts = 2 if retry_network_once else 1
        last_exc: requests.RequestException | None = None
        for attempt in range(attempts):
            try:
                response = self.session.get(url, params=query, timeout=timeout)
                break
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 >= attempts:
                    raise GmgnApiError(f"Network error: {exc}") from exc
                time.sleep(0.12)
        else:
            raise GmgnApiError(f"Network error: {last_exc}")

        reset_at = _parse_reset(response)
        if response.status_code == 429:
            raise GmgnApiError("GMGN rate limit hit", status=429, reset_at=reset_at)
        if response.status_code >= 400:
            raise GmgnApiError(f"HTTP {response.status_code}", status=response.status_code, reset_at=reset_at)

        try:
            payload = response.json()
        except ValueError as exc:
            snippet = response.text[:120].replace("\n", " ")
            raise GmgnApiError(f"GMGN returned non-JSON response: {snippet}", status=response.status_code) from exc

        code = payload.get("code")
        if code not in (0, "0", None):
            err = str(payload.get("error") or payload.get("message") or f"API code {code}")
            body_reset = payload.get("reset_at")
            try:
                reset_at = int(body_reset) if body_reset else reset_at
            except (TypeError, ValueError):
                pass
            status = 429 if "RATE_LIMIT" in err.upper() else response.status_code
            raise GmgnApiError(err, status=status, reset_at=reset_at)

        data = payload.get("data")
        if not isinstance(data, dict):
            raise GmgnApiError("GMGN response missing data object", status=response.status_code)
        return data


def parse_token_snapshot(chain: str, address: str, data: dict[str, Any]) -> TokenSnapshot:
    price_box = data.get("price")
    price = _extract_price(data)

    supply = _to_float(data.get("circulating_supply")) or _to_float(data.get("total_supply"))
    market_cap = _first_float(data, ["market_cap", "marketcap", "fdv", "mcap"])
    if market_cap is None and price is not None and supply is not None:
        market_cap = price * supply

    change = _extract_change(data, price, price_box)
    volume_24h = _extract_volume_24h(data, price_box)
    symbol = str(data.get("symbol") or data.get("ticker") or "TOKEN").strip() or "TOKEN"
    name = str(data.get("name") or symbol).strip() or symbol
    logo = str(data.get("logo") or data.get("logo_url") or data.get("image") or "").strip()
    return TokenSnapshot(
        chain=chain,
        address=address,
        symbol=symbol[:18],
        name=name[:42],
        logo_url=logo,
        price=price,
        market_cap=market_cap,
        change_percent=change,
        volume_24h=volume_24h,
        raw=data,
        received_at=time.time(),
    )


def parse_wallet_activity_snapshot(
    chain: str,
    wallet_address: str,
    remark: str,
    avatar_kind: str,
    avatar_value: str,
    data: dict[str, Any],
    native_price_usd: float | None = None,
) -> WalletActivitySnapshot:
    native_symbol = CHAIN_NATIVE_ASSETS.get(chain, (chain.upper(), ""))[0]
    activities = [
        item
        for item in _extract_activities(data)
        if _activity_side(item) in {"buy", "sell"}
    ]
    if not activities:
        return WalletActivitySnapshot(
            chain=chain,
            wallet_address=wallet_address,
            remark=remark,
            side="",
            native_amount=None,
            native_symbol=native_symbol,
            token_symbol="",
            token_address="",
            token_logo_url="",
            cost_usd=None,
            timestamp=None,
            tx_hash="",
            avatar_kind=avatar_kind,
            avatar_value=avatar_value,
            raw=data,
            received_at=time.time(),
        )

    item = max(activities, key=lambda entry: _activity_sort_key(chain, entry))
    side = _activity_side(item)
    cost_usd = _first_float(item, ["cost_usd", "amount_usd", "usd_value", "value_usd"])
    native_amount = _extract_native_amount(chain, item, cost_usd, native_price_usd)
    token = item.get("token") if isinstance(item.get("token"), dict) else {}
    base_token = item.get("base_token") if isinstance(item.get("base_token"), dict) else {}
    token_symbol = str(
        token.get("symbol")
        or base_token.get("symbol")
        or item.get("token_symbol")
        or item.get("symbol")
        or ""
    ).strip()
    token_address = _extract_base_address(item, wallet_address)
    token_logo_url = _extract_token_logo_url(item)
    tx_hash = str(item.get("transaction_hash") or item.get("tx_hash") or item.get("hash") or "").strip()
    timestamp = _activity_timestamp(item)
    return WalletActivitySnapshot(
        chain=chain,
        wallet_address=wallet_address,
        remark=remark,
        side=side,
        native_amount=native_amount,
        native_symbol=native_symbol,
        token_symbol=token_symbol[:18],
        token_address=token_address,
        token_logo_url=token_logo_url,
        cost_usd=cost_usd,
        timestamp=timestamp or None,
        tx_hash=tx_hash,
        avatar_kind=avatar_kind,
        avatar_value=avatar_value,
        raw=item,
        received_at=time.time(),
    )


def _extract_token_logo_url(item: dict[str, Any]) -> str:
    for container_name in ("token", "base_token", "baseToken"):
        container = item.get(container_name)
        if not isinstance(container, dict):
            continue
        logo = str(
            container.get("logo")
            or container.get("logo_url")
            or container.get("image")
            or container.get("icon")
            or ""
        ).strip()
        if logo:
            return logo
    return str(item.get("token_logo") or item.get("logo") or item.get("logo_url") or item.get("image") or "").strip()


def possible_wallet_chains(address: str, preferred: str = "") -> list[str]:
    value = address.strip()
    if SOL_ADDRESS_RE.match(value):
        return ["sol"]
    if EVM_ADDRESS_RE.match(value):
        chains = list(EVM_CHAINS)
        preferred = preferred.lower().strip()
        if preferred in chains:
            chains = [preferred] + [chain for chain in chains if chain != preferred]
        return chains
    return []


def is_valid_wallet_address(address: str) -> bool:
    return bool(possible_wallet_chains(address))


def normalize_wallet_address(address: str) -> str:
    value = str(address or "").strip()
    if value.startswith(("0x", "0X")):
        return value.lower()
    return value


def _extract_price(data: dict[str, Any]) -> float | None:
    direct = _to_float(data.get("price"))
    if direct is not None:
        return direct

    price_box = data.get("price")
    if isinstance(price_box, dict):
        direct = _first_float(price_box, ["price", "price_usd", "usd", "value"])
        if direct is not None:
            return direct

    pool = data.get("pool")
    if isinstance(pool, dict):
        return _first_float(pool, ["price", "price_usd", "base_price", "token_price"])
    return None


def _extract_change(data: dict[str, Any], current_price: float | None, price_box: Any) -> float | None:
    direct_keys = [
        "price_change_percent",
        "price_change_percentage",
        "price_change_24h",
        "change_percent",
        "change_24h",
        "price_change",
    ]
    direct = _first_float(data, direct_keys)
    if direct is not None:
        return _normalize_percent(direct)

    if isinstance(price_box, dict):
        direct = _first_float(price_box, direct_keys)
        if direct is not None:
            return _normalize_percent(direct)
        for old_key in ("price_24h", "price_6h", "price_1h", "price_5m", "price_1m"):
            old_price = _to_float(price_box.get(old_key))
            if current_price is not None and old_price and old_price > 0:
                return ((current_price - old_price) / old_price) * 100.0

    for container_name in ("pool", "stat"):
        nested = data.get(container_name)
        if isinstance(nested, dict):
            direct = _first_float(nested, direct_keys)
            if direct is not None:
                return _normalize_percent(direct)
    return None


def _extract_volume_24h(data: dict[str, Any], price_box: Any) -> float | None:
    if isinstance(price_box, dict):
        direct = _first_float(price_box, ["volume_24h", "volume24h", "volume"])
        if direct is not None:
            return direct
    return _first_float(data, ["volume_24h", "volume24h", "volume"])


def _normalize_percent(value: float) -> float:
    if -1.0 < value < 1.0:
        return value * 100.0
    return value


def _first_float(data: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        val = _to_float(data.get(key))
        if val is not None:
            return val
    return None


def _extract_activities(data: dict[str, Any]) -> list[dict[str, Any]]:
    return _extract_dict_list(data, ["activities", "list", "items", "records"])


def _extract_holdings(data: dict[str, Any]) -> list[dict[str, Any]]:
    return _extract_dict_list(data, ["holdings", "list", "items", "records"])


def _extract_dict_list(data: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    nested = data.get("data")
    if isinstance(nested, dict):
        return _extract_dict_list(nested, keys)
    return []


def _extract_native_amount(
    chain: str,
    item: dict[str, Any],
    cost_usd: float | None,
    native_price_usd: float | None,
) -> float | None:
    direct = _first_float(
        item,
        [
            "native_amount",
            "main_amount",
            "quote_amount_ui",
            "quote_token_amount",
            "amount_native",
            "amount_in_native",
            "receive_native_amount",
            "pay_native_amount",
        ],
    )
    if direct is not None and 0 <= direct < 1_000_000:
        return direct
    quote_amount = _to_float(item.get("quote_amount"))
    if quote_amount is not None and 0 <= quote_amount < 1_000_000 and _is_native_quote(chain, item):
        return quote_amount
    if cost_usd is not None and native_price_usd is not None and native_price_usd > 0:
        return cost_usd / native_price_usd
    return None


def _extract_base_address(item: dict[str, Any], wallet_address: str = "") -> str:
    wallet_address = normalize_wallet_address(wallet_address).lower()
    direct_keys = [
        "base_address",
        "base_token_address",
        "token_address",
        "contract_address",
        "ca",
        "address",
    ]
    for key in direct_keys:
        address = _clean_chain_address(item.get(key))
        if address and address.lower() != wallet_address:
            return address

    for container_name in ("base_token", "token"):
        container = item.get(container_name)
        if not isinstance(container, dict):
            continue
        for key in ("address", "token_address", "contract_address", "base_address"):
            address = _clean_chain_address(container.get(key))
            if address and address.lower() != wallet_address:
                return address
    return ""


def _clean_chain_address(value: Any) -> str:
    address = str(value or "").strip()
    if not address:
        return ""
    if EVM_ADDRESS_RE.match(address):
        return address.lower()
    if SOL_ADDRESS_RE.match(address):
        return address
    return ""


def _is_native_quote(chain: str, item: dict[str, Any]) -> bool:
    quote_token = item.get("quote_token") if isinstance(item.get("quote_token"), dict) else {}
    symbol = str(quote_token.get("symbol") or item.get("quote_symbol") or "").lower().strip()
    address = str(
        quote_token.get("token_address")
        or quote_token.get("address")
        or item.get("quote_address")
        or ""
    ).lower().strip()
    chain = chain.lower().strip()
    native_symbol, native_address = CHAIN_NATIVE_ASSETS.get(chain, ("", ""))
    aliases = CHAIN_NATIVE_ALIASES.get(chain, {native_symbol.lower()})
    return bool((symbol and symbol in aliases) or (address and address == native_address.lower()))


def _activity_side(item: dict[str, Any]) -> str:
    raw = str(item.get("type") or item.get("event_type") or item.get("side") or item.get("trade_type") or "").lower().strip()
    if raw in {"bought", "buy_token"}:
        return "buy"
    if raw in {"sold", "sell_token"}:
        return "sell"
    return raw


def _activity_timestamp(item: dict[str, Any]) -> int:
    return _normalize_timestamp(_first_float(item, ["timestamp", "time", "block_timestamp"]))


def _activity_sort_key(chain: str, item: dict[str, Any]) -> tuple[int, int, int, float]:
    # GMGN can return several legs in the same second. Prefer a sell leg on ties
    # because wallet monitors are commonly used to catch exits quickly.
    side_priority = 1 if _activity_side(item) == "sell" else 0
    value = _first_float(item, ["cost_usd", "amount_usd", "usd_value", "value_usd"]) or 0.0
    native_priority = 1 if _is_native_quote(chain, item) else 0
    return (_activity_timestamp(item), side_priority, native_priority, value)


def _normalize_timestamp(value: float | None) -> int:
    if value is None or value <= 0:
        return 0
    while value > 10_000_000_000:
        value /= 1000.0
    return int(value)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_reset(response: requests.Response) -> int | None:
    raw = response.headers.get("x-ratelimit-reset") or response.headers.get("X-RateLimit-Reset")
    if not raw:
        return None
    try:
        val = int(raw)
    except ValueError:
        return None
    return val if val > 0 else None
