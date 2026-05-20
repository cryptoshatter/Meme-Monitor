from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
import re
import threading
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
    group: str = ""


@dataclass(slots=True)
class WalletHoldingSnapshot:
    chain: str
    wallet_address: str
    token_address: str
    symbol: str
    name: str
    logo_url: str
    balance: float | None
    usd_value: float | None
    unrealized_profit: float | None
    realized_profit: float | None
    total_profit: float | None
    market_cap: float | None
    avg_buy_market_cap: float | None
    avg_sell_market_cap: float | None
    holding_duration_seconds: int | None
    buy_count: int | None
    sell_count: int | None
    trade_count: int | None
    last_active_timestamp: int | None
    raw: dict[str, Any]
    received_at: float


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
DEFAULT_KOL_AVATAR = "\U0001f4a0"
HONEYPOT_CACHE_TTL_SECONDS = 600.0
HONEYPOT_FAILURE_CACHE_TTL_SECONDS = 60.0
HONEYPOT_FLAG_KEYS = {"is_honeypot", "honeypot"}
HONEYPOT_CONTAINER_KEYS = {"token", "base_token", "basetoken", "security", "token_security", "risk", "audit"}
_HONEYPOT_CACHE: dict[str, tuple[float, bool]] = {}
_HONEYPOT_CACHE_LOCK = threading.Lock()


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

    def get_token_security(self, chain: str, address: str) -> dict[str, Any]:
        return self._normal_get(
            "/v1/token/security",
            {"chain": chain, "address": address},
            timeout=(0.9, 1.8),
            retry_network_once=False,
        )

    def get_token_risk_tags(self, chain: str, address: str) -> list[str]:
        data: dict[str, Any] = {}
        try:
            data = self.get_token_security(chain, address)
        except Exception:
            try:
                data = self._normal_get(
                    "/v1/token/info",
                    {"chain": chain, "address": address},
                    timeout=(0.9, 1.8),
                    retry_network_once=False,
                )
            except Exception:
                return ["风险未知"]
        return risk_tags_from_token_data(data)

    def is_honeypot_token(self, chain: str, address: str) -> bool:
        chain = str(chain or "").lower().strip()
        address = _clean_chain_address(address)
        if chain == "sol" or not chain or not address:
            return False
        native = CHAIN_NATIVE_ASSETS.get(chain, ("", ""))[1].lower()
        if native and address.lower() == native:
            return False

        key = f"{chain}:{address.lower()}"
        now = time.monotonic()
        with _HONEYPOT_CACHE_LOCK:
            cached = _HONEYPOT_CACHE.get(key)
            if cached and cached[0] > now:
                return cached[1]

        result: bool | None = None
        for fetch in (
            lambda: self.get_token_security(chain, address),
            lambda: self._normal_get(
                "/v1/token/info",
                {"chain": chain, "address": address},
                timeout=(0.9, 1.8),
                retry_network_once=False,
            ),
        ):
            try:
                flag = honeypot_flag_from_token_data(fetch())
            except Exception:
                continue
            if flag is not None:
                result = flag
                break

        value = bool(result)
        ttl = HONEYPOT_CACHE_TTL_SECONDS if result is not None else HONEYPOT_FAILURE_CACHE_TTL_SECONDS
        with _HONEYPOT_CACHE_LOCK:
            _HONEYPOT_CACHE[key] = (now + ttl, value)
        return value

    def get_wallet_activity(
        self,
        chain: str,
        wallet_address: str,
        limit: int = 1,
        activity_types: list[str] | None = None,
        token_address: str = "",
    ) -> dict[str, Any]:
        wallet_address = normalize_wallet_address(wallet_address)
        params: dict[str, Any] = {
            "chain": chain,
            "wallet_address": wallet_address,
            "limit": max(1, min(int(limit), 20)),
        }
        if activity_types:
            params["type"] = activity_types
        token_address = str(token_address or "").strip()
        if token_address:
            params["token"] = token_address
        return self._normal_get(
            "/v1/user/wallet_activity",
            params,
            timeout=(2.8, 4.5),
            retry_network_once=True,
        )

    def get_wallet_holdings(
        self,
        chain: str,
        wallet_address: str,
        limit: int = 1,
        order_by: str = "usd_value",
        direction: str = "desc",
    ) -> dict[str, Any]:
        wallet_address = normalize_wallet_address(wallet_address)
        return self._normal_get(
            "/v1/user/wallet_holdings",
            {
                "chain": chain,
                "wallet_address": wallet_address,
                "limit": max(1, min(int(limit), 50)),
                "order_by": order_by,
                "direction": direction,
                "hide_airdrop": "true",
                "hide_closed": "true",
            },
            timeout=(2.8, 5.0),
            retry_network_once=True,
        )

    def get_kol_wallets(self, chain: str, limit: int = 100) -> list[dict[str, Any]]:
        data = self._normal_get(
            "/v1/user/kol",
            {
                "chain": chain,
                "limit": max(1, min(int(limit), 500)),
            },
            timeout=(2.4, 4.2),
            retry_network_once=True,
        )
        return parse_kol_wallets(chain, data)

    def get_smartmoney_wallets(self, chain: str, limit: int = 100) -> list[dict[str, Any]]:
        data = self._normal_get(
            "/v1/user/smartmoney",
            {
                "chain": chain,
                "limit": max(1, min(int(limit), 500)),
            },
            timeout=(2.4, 4.2),
            retry_network_once=True,
        )
        return parse_kol_wallets(chain, data)

    def search_kol_wallets(self, query: str, chain: str = "", limit: int = 100) -> list[dict[str, Any]]:
        query = str(query or "").lower().strip()
        chains = [chain] if chain else list(SUPPORTED_CHAINS)
        matches: list[dict[str, Any]] = []
        for item_chain in chains:
            for wallet in self.get_kol_wallets(item_chain, limit=limit):
                haystack = " ".join(
                    [
                        str(wallet.get("remark") or ""),
                        str(wallet.get("twitter_username") or ""),
                        str(wallet.get("twitter_name") or ""),
                        " ".join(str(tag) for tag in wallet.get("tags", []) if str(tag).strip()),
                        str(wallet.get("address") or ""),
                    ]
                ).lower()
                if not query or query in haystack:
                    matches.append(wallet)
        matches.sort(key=lambda item: _kol_match_score(query, item), reverse=True)
        return matches

    def get_latest_wallet_activity(
        self,
        chain: str,
        wallet_address: str,
        remark: str,
        avatar_kind: str = "emoji",
        avatar_value: str = "",
        group: str = "",
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
            group=group,
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
                group=group,
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


def honeypot_flag_from_token_data(data: dict[str, Any]) -> bool | None:
    if not isinstance(data, dict):
        return None
    stack: list[dict[str, Any]] = [data]
    scanned = 0
    while stack and scanned < 80:
        current = stack.pop()
        scanned += 1
        for key, value in current.items():
            normalized_key = str(key or "").lower().strip()
            if normalized_key in HONEYPOT_FLAG_KEYS:
                flag = _truthy_honeypot_flag(value)
                if flag is not None:
                    return flag
            if normalized_key in HONEYPOT_CONTAINER_KEYS and isinstance(value, dict):
                stack.append(value)
    return None


def is_honeypot_token_data(data: dict[str, Any]) -> bool:
    return honeypot_flag_from_token_data(data) is True


def risk_tags_from_token_data(data: dict[str, Any]) -> list[str]:
    if not isinstance(data, dict):
        return ["风险未知"]
    tags: list[str] = []
    flag = honeypot_flag_from_token_data(data)
    if flag is True:
        tags.append("貔貅")
    buy_tax = _recursive_first_float(data, ["buy_tax", "buytax", "tax_buy", "buy_fee", "buy_fee_rate"])
    sell_tax = _recursive_first_float(data, ["sell_tax", "selltax", "tax_sell", "sell_fee", "sell_fee_rate"])
    max_tax = max(value for value in [buy_tax, sell_tax] if value is not None) if any(value is not None for value in [buy_tax, sell_tax]) else None
    if max_tax is not None:
        tax = max_tax * 100 if 0 < max_tax <= 1 else max_tax
        if tax >= 10:
            tags.append("高税")
    liquidity = _recursive_first_float(data, ["liquidity", "liquidity_usd", "pool_liquidity", "reserve_usd"])
    if liquidity is not None and 0 < liquidity < 10_000:
        tags.append("池小")
    holder_rate = _recursive_first_float(data, ["top10_holder_rate", "top_10_holder_rate", "top_holders_rate", "holder_top10_rate"])
    if holder_rate is not None:
        rate = holder_rate * 100 if 0 < holder_rate <= 1 else holder_rate
        if rate >= 45:
            tags.append("集中")
    mintable = _recursive_truthy(data, ["mintable", "can_mint", "is_mintable"])
    if mintable is True:
        tags.append("可增发")
    blacklist = _recursive_truthy(data, ["blacklist", "is_blacklist", "can_blacklist"])
    if blacklist is True:
        tags.append("黑名单")
    if not tags:
        tags.append("低风险")
    return tags[:4]


def _recursive_first_float(data: dict[str, Any], keys: list[str]) -> float | None:
    stack: list[dict[str, Any]] = [data]
    wanted = {key.lower() for key in keys}
    scanned = 0
    while stack and scanned < 120:
        current = stack.pop()
        scanned += 1
        for key, value in current.items():
            if str(key or "").lower().strip() in wanted:
                number = _to_float(value)
                if number is not None:
                    return number
            if isinstance(value, dict):
                stack.append(value)
    return None


def _recursive_truthy(data: dict[str, Any], keys: list[str]) -> bool | None:
    stack: list[dict[str, Any]] = [data]
    wanted = {key.lower() for key in keys}
    scanned = 0
    while stack and scanned < 120:
        current = stack.pop()
        scanned += 1
        for key, value in current.items():
            if str(key or "").lower().strip() in wanted:
                return _truthy_honeypot_flag(value)
            if isinstance(value, dict):
                stack.append(value)
    return None


def _truthy_honeypot_flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"true", "1", "yes", "y", "honeypot"}:
        return True
    if text in {"false", "0", "no", "n", "none", "null", "safe"}:
        return False
    return None


def parse_wallet_activity_snapshot(
    chain: str,
    wallet_address: str,
    remark: str,
    avatar_kind: str,
    avatar_value: str,
    data: dict[str, Any],
    native_price_usd: float | None = None,
    group: str = "",
) -> WalletActivitySnapshot:
    native_symbol = CHAIN_NATIVE_ASSETS.get(chain, (chain.upper(), ""))[0]
    activities = [
        item
        for item in _extract_activities(data)
        if _activity_side(item) in {"buy", "sell"}
        and not is_honeypot_token_data(item)
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
            group=group,
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
        group=group,
        raw=item,
        received_at=time.time(),
    )


def parse_kol_wallets(chain: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    wallets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _extract_dict_list(data, ["list", "items", "records", "kol"]):
        maker_info = item.get("maker_info") if isinstance(item.get("maker_info"), dict) else {}
        common = item.get("common") if isinstance(item.get("common"), dict) else {}
        base = maker_info or common or item
        address = _clean_chain_address(
            base.get("address")
            or base.get("wallet_address")
            or item.get("maker")
            or item.get("wallet")
            or item.get("address")
        )
        if not address:
            continue
        key = f"{chain}:{address.lower()}"
        if key in seen:
            continue
        seen.add(key)
        name = _kol_display_name(base)
        if not name:
            name = short_address_for_identity(address)
        avatar = str(base.get("avatar") or base.get("avatar_url") or base.get("image") or "").strip()
        wallets.append(
            {
                "remark": name,
                "address": address,
                "chain": str(item.get("chain") or chain).lower().strip() or chain,
                "chains": [str(item.get("chain") or chain).lower().strip() or chain],
                "avatar_kind": "image" if avatar else "emoji",
                "avatar_value": avatar or DEFAULT_KOL_AVATAR,
                "twitter_username": str(base.get("twitter_username") or "").strip(),
                "twitter_name": str(base.get("twitter_name") or "").strip(),
                "tags": _kol_tags(base),
            }
        )
    return wallets


def parse_wallet_activity_items(chain: str, wallet_address: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _extract_activities(data):
        side = _activity_side(item)
        if side not in {"buy", "sell"}:
            continue
        if is_honeypot_token_data(item):
            continue
        token = item.get("token") if isinstance(item.get("token"), dict) else {}
        base_token = item.get("base_token") if isinstance(item.get("base_token"), dict) else {}
        symbol = str(
            token.get("symbol")
            or base_token.get("symbol")
            or item.get("token_symbol")
            or item.get("symbol")
            or "TOKEN"
        ).strip() or "TOKEN"
        name = str(token.get("name") or base_token.get("name") or item.get("token_name") or item.get("name") or symbol).strip() or symbol
        cost_usd = _first_float(item, ["cost_usd", "amount_usd", "usd_value", "value_usd"])
        token_amount = _first_float(item, ["token_amount", "base_amount", "amount", "amount_token", "base_token_amount"])
        price_usd = _activity_price_usd(item, cost_usd, token_amount)
        buy_market_cap = _activity_market_cap(item, "buy", price_usd)
        sell_market_cap = _activity_market_cap(item, "sell", price_usd)
        market_cap = buy_market_cap if side == "buy" else sell_market_cap if side == "sell" else buy_market_cap or sell_market_cap
        tx_hash = str(item.get("transaction_hash") or item.get("tx_hash") or item.get("hash") or "").strip()
        items.append(
            {
                "row_type": "activity",
                "chain": chain,
                "wallet_address": normalize_wallet_address(wallet_address),
                "token_address": _extract_base_address(item, wallet_address),
                "symbol": symbol[:18],
                "name": name[:42],
                "logo_url": _extract_token_logo_url(item),
                "side": side,
                "timestamp": _activity_timestamp(item) or None,
                "cost_usd": cost_usd,
                "token_amount": token_amount,
                "price_usd": price_usd,
                "market_cap": market_cap,
                "trade_market_cap": market_cap,
                "buy_market_cap": buy_market_cap,
                "sell_market_cap": sell_market_cap,
                "tx_hash": tx_hash,
                "raw": item,
                "received_at": time.time(),
            }
        )
    items.sort(key=lambda entry: int(entry.get("timestamp") or 0), reverse=True)
    return items


def _activity_price_usd(item: dict[str, Any], cost_usd: float | None, token_amount: float | None) -> float | None:
    keys = ["price_usd", "token_price_usd", "price", "token_price", "base_price", "base_price_usd"]
    direct = _first_float(item, keys)
    if direct is not None:
        return direct
    for container_name in ("token", "base_token", "baseToken", "pool", "price", "stat"):
        container = item.get(container_name)
        if isinstance(container, dict):
            direct = _first_float(container, keys)
            if direct is not None:
                return direct
    if cost_usd is not None and token_amount is not None and token_amount > 0:
        return cost_usd / token_amount
    return None


def _activity_market_cap(item: dict[str, Any], side: str = "", price_usd: float | None = None) -> float | None:
    side = side.lower().strip()
    side_keys = []
    if side == "buy":
        side_keys = ["buy_market_cap", "buy_mcap", "buy_fdv", "market_cap_at_buy", "mcap_at_buy", "fdv_at_buy"]
    elif side == "sell":
        side_keys = ["sell_market_cap", "sell_mcap", "sell_fdv", "market_cap_at_sell", "mcap_at_sell", "fdv_at_sell"]
    keys = side_keys + ["trade_market_cap", "token_market_cap", "market_cap", "marketcap", "mcap", "fdv", "fully_diluted_valuation"]
    direct = _first_float(item, keys)
    if direct is not None:
        return direct
    for container_name in ("token", "base_token", "baseToken", "pool", "stat"):
        container = item.get(container_name)
        if isinstance(container, dict):
            direct = _first_float(container, keys)
            if direct is not None:
                return direct
    supply = None
    supply_keys = ["circulating_supply", "total_supply", "supply", "token_supply"]
    for container_name in ("token", "base_token", "baseToken"):
        container = item.get(container_name)
        if isinstance(container, dict):
            supply = _first_float(container, supply_keys)
            if supply is not None:
                break
    if supply is None:
        supply = _first_float(item, supply_keys)
    if price_usd is not None and supply is not None and 0 < supply < 1_000_000_000_000_000:
        return price_usd * supply
    return None


def parse_wallet_holdings(chain: str, wallet_address: str, data: dict[str, Any]) -> list[WalletHoldingSnapshot]:
    holdings: list[WalletHoldingSnapshot] = []
    now = time.time()
    for item in _extract_holdings(data):
        token = item.get("token") if isinstance(item.get("token"), dict) else {}
        if is_honeypot_token_data(item) or is_honeypot_token_data(token):
            continue
        token_address = _clean_chain_address(
            token.get("address")
            or token.get("token_address")
            or item.get("token_address")
            or item.get("address")
            or item.get("contract_address")
        )
        symbol = str(
            token.get("symbol")
            or token.get("ticker")
            or item.get("token_symbol")
            or item.get("symbol")
            or "TOKEN"
        ).strip() or "TOKEN"
        name = str(token.get("name") or item.get("token_name") or item.get("name") or symbol).strip() or symbol
        unrealized = _first_float(item, ["unrealized_profit", "unrealized_pnl", "unrealized_profit_usd"])
        realized = _first_float(item, ["realized_profit", "realized_pnl", "realized_profit_usd"])
        total = _first_float(item, ["total_profit", "total_pnl", "profit", "profit_usd"])
        if total is None and (unrealized is not None or realized is not None):
            total = (unrealized or 0.0) + (realized or 0.0)
        buy_count, sell_count = _extract_holding_buy_sell_counts(item)
        market_cap = _holding_market_cap(item)
        avg_buy_market_cap = _extract_holding_avg_market_cap(item, "buy")
        avg_sell_market_cap = _extract_holding_avg_market_cap(item, "sell")
        holdings.append(
            WalletHoldingSnapshot(
                chain=chain,
                wallet_address=wallet_address,
                token_address=token_address,
                symbol=symbol[:18],
                name=name[:42],
                logo_url=_extract_token_logo_url(item),
                balance=_first_float(item, ["balance", "amount", "token_balance", "ui_amount"]),
                usd_value=_first_float(item, ["usd_value", "value_usd", "amount_usd", "market_value"]),
                unrealized_profit=unrealized,
                realized_profit=realized,
                total_profit=total,
                market_cap=market_cap,
                avg_buy_market_cap=avg_buy_market_cap,
                avg_sell_market_cap=avg_sell_market_cap,
                holding_duration_seconds=_extract_holding_duration_seconds(item, now),
                buy_count=buy_count,
                sell_count=sell_count,
                trade_count=_sum_counts(buy_count, sell_count, _first_float(item, ["trade_count", "tx_count", "transaction_count", "swap_count"])),
                last_active_timestamp=_normalize_timestamp(_first_float(item, ["last_active_timestamp", "last_active_time", "updated_at"])),
                raw=item,
                received_at=now,
            )
        )
    holdings.sort(key=lambda item: item.usd_value or 0.0, reverse=True)
    return holdings


def _kol_tags(common: dict[str, Any]) -> list[str]:
    raw_tags = common.get("tags")
    if isinstance(raw_tags, list):
        tags = [str(tag).lower().strip() for tag in raw_tags if str(tag).strip()]
    elif isinstance(raw_tags, str):
        tags = [part.lower().strip() for part in raw_tags.split(",") if part.strip()]
    else:
        tags = []
    primary = str(common.get("tag") or "").lower().strip()
    if primary and primary not in tags:
        tags.insert(0, primary)
    return tags


def _kol_display_name(common: dict[str, Any]) -> str:
    for key in ("name", "twitter_name", "ens"):
        value = str(common.get(key) or "").strip()
        if value:
            return value[:28]
    username = str(common.get("twitter_username") or "").strip().lstrip("@")
    if username:
        return f"@{username}"[:28]
    return ""


def _kol_match_score(query: str, item: dict[str, Any]) -> int:
    if not query:
        return 0
    name = str(item.get("remark") or "").lower()
    twitter = str(item.get("twitter_username") or "").lower().lstrip("@")
    address = str(item.get("address") or "").lower()
    if query == name or query == twitter:
        return 100
    if name.startswith(query) or twitter.startswith(query):
        return 80
    if query in name or query in twitter:
        return 60
    if query in address:
        return 30
    return 0


def short_address_for_identity(address: str) -> str:
    if len(address) <= 12:
        return address
    return f"{address[:6]}...{address[-4:]}"


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


def _extract_holding_trade_count(item: dict[str, Any]) -> int | None:
    direct = _first_float(item, ["trade_count", "tx_count", "transaction_count", "swap_count"])
    if direct is not None:
        return max(0, int(direct))
    buy_count = _first_float(item, ["history_total_buys", "buy_tx_count", "buy_count", "buy_trade_count"])
    sell_count = _first_float(item, ["history_total_sells", "sell_tx_count", "sell_count", "sell_trade_count"])
    if buy_count is None and sell_count is None:
        return None
    return max(0, int(buy_count or 0) + int(sell_count or 0))


def _extract_holding_buy_sell_counts(item: dict[str, Any]) -> tuple[int | None, int | None]:
    buy_count = _first_float(item, ["history_total_buys", "buy_tx_count", "buy_count", "buy_trade_count"])
    sell_count = _first_float(item, ["history_total_sells", "sell_tx_count", "sell_count", "sell_trade_count"])
    return _count_or_none(buy_count), _count_or_none(sell_count)


def _holding_market_cap(item: dict[str, Any]) -> float | None:
    keys = ["market_cap", "marketcap", "mcap", "fdv", "fully_diluted_valuation", "token_market_cap"]
    direct = _first_float(item, keys)
    if direct is not None:
        return direct
    for container_name in ("token", "base_token", "baseToken", "pool", "stat"):
        container = item.get(container_name)
        if isinstance(container, dict):
            direct = _first_float(container, keys)
            if direct is not None:
                return direct
    price = _activity_price_usd(item, None, None) or _extract_price(item)
    supply = _first_float(item, ["circulating_supply", "total_supply", "supply", "token_supply", "max_supply"])
    token = item.get("token") if isinstance(item.get("token"), dict) else {}
    if supply is None and token:
        supply = _first_float(token, ["circulating_supply", "total_supply", "supply", "token_supply", "max_supply"])
    if price is not None and supply is not None and 0 < supply < 1_000_000_000_000_000:
        return price * supply
    return None


def _extract_holding_avg_market_cap(item: dict[str, Any], side: str) -> float | None:
    if side == "buy":
        keys = [
            "avg_buy_market_cap",
            "average_buy_market_cap",
            "avg_buy_mcap",
            "average_buy_mcap",
            "buy_avg_market_cap",
            "buy_avg_mcap",
            "avg_buy_fdv",
            "buy_avg_fdv",
        ]
        price_keys = [
            "avg_buy_price",
            "average_buy_price",
            "buy_avg_price",
            "avg_buy_price_usd",
            "average_cost",
            "avg_cost",
            "avg_entry_price",
            "avg_price",
        ]
        cost_key = "history_bought_cost"
        amount_key = "history_bought_amount"
    else:
        keys = [
            "avg_sell_market_cap",
            "average_sell_market_cap",
            "avg_sell_mcap",
            "average_sell_mcap",
            "sell_avg_market_cap",
            "sell_avg_mcap",
            "avg_sell_fdv",
            "sell_avg_fdv",
        ]
        price_keys = [
            "avg_sell_price",
            "average_sell_price",
            "sell_avg_price",
            "avg_sell_price_usd",
            "average_sold_price",
            "avg_sold_price",
        ]
        cost_key = "history_sold_income"
        amount_key = "history_sold_amount"

    direct = _first_float(item, keys)
    if direct is not None:
        return direct
    for container_name in ("token", "base_token", "baseToken", "stat"):
        container = item.get(container_name)
        if isinstance(container, dict):
            direct = _first_float(container, keys)
            if direct is not None:
                return direct

    avg_price = _first_float(item, price_keys)
    if avg_price is None:
        total_cost = _first_float(item, [cost_key])
        total_amount = _first_float(item, [amount_key])
        if total_cost is not None and total_amount is not None and total_amount > 0:
            avg_price = total_cost / total_amount

    supply = _first_float(item, ["circulating_supply", "total_supply", "supply", "token_supply", "max_supply"])
    token = item.get("token") if isinstance(item.get("token"), dict) else {}
    if supply is None and token:
        supply = _first_float(token, ["circulating_supply", "total_supply", "supply", "token_supply", "max_supply"])
    if avg_price is not None and supply is not None and 0 < supply < 1_000_000_000_000_000:
        return avg_price * supply
    return None


def _count_or_none(value: float | None) -> int | None:
    if value is None:
        return None
    return max(0, int(value))


def _sum_counts(buy_count: int | None, sell_count: int | None, fallback: float | None = None) -> int | None:
    if buy_count is not None or sell_count is not None:
        return max(0, int(buy_count or 0) + int(sell_count or 0))
    return _count_or_none(fallback)


def _extract_holding_duration_seconds(item: dict[str, Any], now: float) -> int | None:
    direct = _first_float(
        item,
        [
            "holding_duration",
            "holding_duration_seconds",
            "holding_seconds",
            "hold_duration",
            "hold_time",
            "holding_time",
            "duration",
        ],
    )
    if direct is not None and direct > 0:
        if direct > 1_000_000_000:
            timestamp = _normalize_timestamp(direct)
            return max(0, int(now - timestamp)) if timestamp else None
        return max(0, int(direct))

    opened_at = _normalize_timestamp(
        _first_float(
            item,
            [
                "first_buy_timestamp",
                "first_buy_time",
                "start_holding_at",
                "open_timestamp",
                "opened_at",
                "position_open_timestamp",
                "position_created_at",
                "created_timestamp",
                "create_timestamp",
                "start_timestamp",
                "buy_timestamp",
                "buy_time",
                "last_active_timestamp",
            ],
        )
    )
    if not opened_at:
        return None
    return max(0, int(now - opened_at))


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
