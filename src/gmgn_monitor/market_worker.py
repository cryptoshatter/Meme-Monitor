from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QMutex, QThread, Signal

from .gmgn_client import GmgnApiError, GmgnOpenApiClient, TokenSnapshot, possible_wallet_chains

LOG = logging.getLogger(__name__)
WALLET_ACTIVITY_STARTUP_GRACE_SECONDS = 180


@dataclass(slots=True)
class WorkerState:
    chain: str
    address: str
    interval_ms: int
    wallets: list[dict[str, Any]] | None = None
    tokens: list[dict[str, Any]] | None = None
    paused: bool = False


class MarketWorker(QThread):
    snapshot = Signal(object)
    wallet_activity = Signal(object)
    token_alert = Signal(object)
    token_risk = Signal(object)
    status = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        api_key: str,
        host: str,
        chain: str,
        address: str,
        interval_ms: int,
        wallet_remark: str = "",
        wallet_address: str = "",
        wallet_chain: str = "",
        wallets: list[dict[str, Any]] | None = None,
        tokens: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self._host = host
        self._state = WorkerState(
            chain=chain,
            address=address,
            interval_ms=max(100, interval_ms),
            wallets=self._normalize_wallets(
                wallets
                or [
                    {
                        "remark": wallet_remark,
                        "address": wallet_address,
                        "chain": wallet_chain,
                        "avatar_kind": "emoji",
                        "avatar_value": "\U0001f9e9",
                    }
                ]
            ),
            tokens=self._normalize_tokens(tokens, chain, address),
        )
        self._mutex = QMutex()
        self._running = True
        self._started_at = time.time()
        self._last_price: float | None = None
        self._last_wallet_event_keys: dict[str, str] = {}
        self._last_wallet_poll = 0.0
        self._next_wallet_index = 0
        self._next_wallet_chain_index: dict[str, int] = {}
        self._wallet_primary_chain: dict[str, str] = {}
        self._wallet_snapshots: dict[str, object] = {}
        self._last_wallet_fetch_log_key: dict[str, str] = {}
        self._last_wallet_poll_log_at = 0.0
        self._next_wallet_secondary_probe_at: dict[str, float] = {}
        self._last_token_poll = 0.0
        self._next_token_index = 0
        self._token_snapshots: dict[str, TokenSnapshot] = {}
        self._token_alert_baselines: dict[str, TokenSnapshot] = {}
        self._token_alert_origin_baselines: dict[str, TokenSnapshot] = {}
        self._token_alert_thresholds: dict[str, float | None] = {}
        self._last_token_alert_key: dict[str, str] = {}
        self._last_token_snapshot_by_key: dict[str, TokenSnapshot] = {}
        self._last_token_risk_poll = 0.0
        self._last_token_risk_key = ""

    def update_token(self, chain: str, address: str) -> None:
        self._mutex.lock()
        try:
            self._state.chain = chain
            self._state.address = address
            self._state.tokens = self._ensure_primary_token(self._state.tokens or [], chain, address)
            self._last_price = None
            self._last_wallet_poll = time.monotonic()
            self._last_token_poll = time.monotonic()
            self._sync_token_alert_baselines(self._state.tokens)
        finally:
            self._mutex.unlock()

    def update_tokens(self, tokens: list[dict[str, Any]], chain: str = "", address: str = "") -> None:
        self._mutex.lock()
        try:
            primary_chain = chain or self._state.chain
            primary_address = address or self._state.address
            self._state.tokens = self._normalize_tokens(tokens, primary_chain, primary_address)
            primary = primary_token(self._state.tokens)
            self._state.chain = primary["chain"]
            self._state.address = primary["address"]
            self._last_price = None
            self._last_wallet_poll = time.monotonic()
            self._last_token_poll = time.monotonic()
            self._next_token_index = 0
            self._token_snapshots = {}
            self._sync_token_alert_baselines(self._state.tokens)
            self._last_token_alert_key = {}
        finally:
            self._mutex.unlock()

    def update_wallet(self, remark: str, address: str, chain: str) -> None:
        self.update_wallets(
            [
                {
                    "remark": remark,
                    "address": address,
                    "chain": chain,
                    "avatar_kind": "emoji",
                    "avatar_value": "\U0001f9e9",
                }
            ]
        )

    def update_wallets(self, wallets: list[dict[str, Any]]) -> None:
        self._mutex.lock()
        try:
            self._state.wallets = self._normalize_wallets(wallets)
            self._last_wallet_event_keys = {}
            self._last_wallet_poll = 0.0
            self._next_wallet_index = 0
            self._next_wallet_chain_index = {}
            self._wallet_primary_chain = {}
            self._wallet_snapshots = {}
            self._last_wallet_fetch_log_key = {}
            self._last_wallet_poll_log_at = 0.0
            self._next_wallet_secondary_probe_at = {}
        finally:
            self._mutex.unlock()

    def set_interval(self, interval_ms: int) -> None:
        self._mutex.lock()
        try:
            self._state.interval_ms = max(100, int(interval_ms))
        finally:
            self._mutex.unlock()

    def set_paused(self, paused: bool) -> None:
        self._mutex.lock()
        try:
            self._state.paused = paused
        finally:
            self._mutex.unlock()

    def stop(self) -> None:
        self._mutex.lock()
        try:
            self._running = False
        finally:
            self._mutex.unlock()

    def run(self) -> None:
        try:
            client = GmgnOpenApiClient(self._api_key, self._host)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        LOG.info(
            "market worker loop started token=%s:%s wallets=%s",
            self._state.chain,
            self._state.address,
            [
                {
                    "remark": wallet.get("remark", ""),
                    "address": str(wallet.get("address", "")).lower(),
                    "chain": wallet.get("chain", ""),
                    "chains": wallet.get("chains", []),
                }
                for wallet in (self._state.wallets or [])
            ],
        )

        while True:
            running, state = self._copy_state()
            if not running:
                return
            if state.paused:
                self.msleep(180)
                continue

            try:
                snap = client.get_token_info(state.chain, state.address)
                if f"{snap.chain}:{snap.address}".lower() == f"{state.chain}:{state.address}".lower():
                    snap = self._with_local_change(snap)
                    self._last_token_snapshot_by_key[f"{snap.chain}:{snap.address}".lower()] = snap
                    self.snapshot.emit(snap)
                    self.status.emit("Live")
                    self._poll_risk_if_due(client, snap)
            except GmgnApiError as exc:
                LOG.warning("GMGN API error: %s", exc)
                self._sleep_interruptible(self._error_wait_ms(exc, state.interval_ms))
                continue
            except Exception as exc:
                LOG.exception("Unexpected worker error")
                self.error.emit(str(exc))
                self._sleep_interruptible(max(1500, state.interval_ms * 2))
                continue

            try:
                self._poll_wallet_if_due(client, state)
            except Exception as exc:
                LOG.exception("Unexpected wallet poll failure")
                self.error.emit(str(exc))

            try:
                self._poll_extra_token_if_due(client, state)
            except Exception as exc:
                LOG.exception("Unexpected token monitor failure")
                self.error.emit(str(exc))

            self._sleep_interruptible(state.interval_ms)

    def _poll_risk_if_due(self, client: GmgnOpenApiClient, snap: TokenSnapshot) -> None:
        key = f"{snap.chain}:{snap.address}".lower()
        now = time.monotonic()
        if key == self._last_token_risk_key and (now - self._last_token_risk_poll) < 120.0:
            return
        self._last_token_risk_key = key
        self._last_token_risk_poll = now
        try:
            tags = client.get_token_risk_tags(snap.chain, snap.address)
        except Exception:
            tags = ["风险未知"]
        self.token_risk.emit({"chain": snap.chain, "address": snap.address, "tags": tags or ["风险未知"]})

    def _copy_state(self) -> tuple[bool, WorkerState]:
        self._mutex.lock()
        try:
            return self._running, WorkerState(
                chain=self._state.chain,
                address=self._state.address,
                interval_ms=self._state.interval_ms,
                wallets=[dict(wallet) for wallet in (self._state.wallets or [])],
                tokens=[dict(token) for token in (self._state.tokens or [])],
                paused=self._state.paused,
            )
        finally:
            self._mutex.unlock()

    def _is_running(self) -> bool:
        self._mutex.lock()
        try:
            return self._running
        finally:
            self._mutex.unlock()

    def _sleep_interruptible(self, duration_ms: int) -> None:
        end = time.monotonic() + max(0, duration_ms) / 1000.0
        while time.monotonic() < end:
            if not self._is_running():
                return
            remaining = max(1, int((end - time.monotonic()) * 1000))
            self.msleep(min(100, remaining))

    def _with_local_change(self, snap: TokenSnapshot) -> TokenSnapshot:
        if snap.change_percent is None and snap.price is not None and self._last_price:
            if self._last_price > 0:
                snap.change_percent = ((snap.price - self._last_price) / self._last_price) * 100.0
        if snap.price is not None:
            self._last_price = snap.price
        return snap

    def _poll_extra_token_if_due(self, client: GmgnOpenApiClient, state: WorkerState) -> None:
        tokens = [
            token
            for token in (state.tokens or [])
            if token.get("enabled", True)
            and str(token.get("address") or "").strip()
            and token_key(token) != f"{state.chain}:{state.address}".lower()
        ]
        if not tokens:
            return
        now = time.monotonic()
        poll_interval_ms = max(900, state.interval_ms)
        if (now - self._last_token_poll) * 1000 < poll_interval_ms:
            return
        self._last_token_poll = now
        token = tokens[self._next_token_index % len(tokens)]
        self._next_token_index = (self._next_token_index + 1) % len(tokens)
        chain = str(token.get("chain") or "").lower().strip()
        address = str(token.get("address") or "").strip()
        if not chain or not address:
            return
        snap = client.get_token_info(chain, address)
        key = f"{snap.chain}:{snap.address}".lower()
        previous_snap = self._last_token_snapshot_by_key.get(key)
        self._last_token_snapshot_by_key[key] = snap
        self._token_snapshots[key] = snap
        threshold = token_alert_threshold(token)
        if threshold is None:
            self._token_alert_baselines.pop(key, None)
            self._token_alert_origin_baselines.pop(key, None)
            self._token_alert_thresholds[key] = None
            self._last_token_alert_key.pop(key, None)
            return
        if self._token_alert_thresholds.get(key) != threshold:
            self._token_alert_thresholds[key] = threshold
            self._token_alert_baselines[key] = snap
            self._token_alert_origin_baselines[key] = snap
            self._last_token_alert_key.pop(key, None)
            self._emit_token_alert(snap, 0.0, threshold, triggered=False)
            return
        baseline = self._token_alert_baselines.get(key)
        if baseline is None:
            self._token_alert_baselines[key] = snap
            self._token_alert_origin_baselines[key] = snap
            self._emit_token_alert(snap, 0.0, threshold, triggered=False)
            return
        origin = self._token_alert_origin_baselines.get(key)
        if origin is None:
            origin = baseline
            self._token_alert_origin_baselines[key] = origin
        display_delta = token_delta_percent(origin, snap)
        trigger_delta = token_delta_percent(baseline, snap)
        if display_delta is None or trigger_delta is None:
            return
        if abs(trigger_delta) < threshold:
            self._emit_token_alert(snap, display_delta, threshold, triggered=False)
            return
        alert_key = f"{format_alert_value(snap.market_cap or snap.price)}:{trigger_delta:.2f}"
        if self._last_token_alert_key.get(key) == alert_key:
            return
        self._last_token_alert_key[key] = alert_key
        self._token_alert_baselines[key] = snap
        reason = self._token_alert_reason(snap, trigger_delta, previous_snap)
        self._emit_token_alert(snap, display_delta, threshold, triggered=True, trigger_delta=trigger_delta, reason=reason)

    def _emit_token_alert(
        self,
        snap: TokenSnapshot,
        display_delta: float,
        threshold: float,
        *,
        triggered: bool,
        trigger_delta: float | None = None,
        reason: str = "",
    ) -> None:
        self.token_alert.emit(
            {
                "chain": snap.chain,
                "address": snap.address,
                "symbol": snap.symbol,
                "logo_url": snap.logo_url,
                "delta_percent": display_delta,
                "trigger_delta_percent": trigger_delta,
                "threshold_percent": threshold,
                "market_cap": snap.market_cap,
                "price": snap.price,
                "change_percent": snap.change_percent,
                "volume_24h": snap.volume_24h,
                "received_at": snap.received_at,
                "triggered": triggered,
                "reason": reason or "市值突破阈值",
            }
        )

    def _token_alert_reason(self, snap: TokenSnapshot, trigger_delta: float | None, previous_snap: TokenSnapshot | None) -> str:
        key = f"{snap.chain}:{snap.address}".lower()
        now = time.time()
        for wallet_snap in sorted(self._wallet_snapshots.values(), key=lambda item: getattr(item, "timestamp", None) or 0, reverse=True):
            token_key_value = f"{getattr(wallet_snap, 'chain', '')}:{getattr(wallet_snap, 'token_address', '')}".lower()
            timestamp = getattr(wallet_snap, "timestamp", None) or 0
            if token_key_value == key and timestamp and now - timestamp <= 300:
                side = "买入" if getattr(wallet_snap, "side", "") == "buy" else "卖出"
                remark = str(getattr(wallet_snap, "remark", "") or "钱包")
                return f"{remark}{side}"
        if previous_snap and previous_snap.volume_24h and snap.volume_24h:
            if snap.volume_24h >= previous_snap.volume_24h * 1.12:
                return "成交放大"
        if trigger_delta is not None:
            return "上涨突破" if trigger_delta >= 0 else "下跌突破"
        return "市值突破阈值"

    def _poll_wallet_if_due(self, client: GmgnOpenApiClient, state: WorkerState) -> None:
        wallets = state.wallets or []
        if not wallets:
            return
        now = time.monotonic()
        wallet_interval_ms = max(state.interval_ms, 650)
        if (now - self._last_wallet_poll) * 1000 < wallet_interval_ms:
            return
        self._last_wallet_poll = now
        wallet = wallets[self._next_wallet_index % len(wallets)]
        self._next_wallet_index = (self._next_wallet_index + 1) % len(wallets)
        address = str(wallet.get("address") or "").strip()
        if address.startswith(("0x", "0X")):
            address = address.lower()
        chains = wallet_chains(wallet)
        if not address or not chains:
            return
        wallet_id = f"{address.lower()}:{','.join(chains)}"
        poll_chains = self._wallet_poll_chains(wallet_id, chains, now)
        if now - self._last_wallet_poll_log_at > 15.0:
            self._last_wallet_poll_log_at = now
            LOG.info(
                "wallet poll started address=%s remark=%s chains=%s poll_chains=%s",
                address.lower(),
                str(wallet.get("remark") or "Wallet"),
                chains,
                poll_chains,
            )
        snapshots = []
        retry_delay_ms = 0
        for chain in poll_chains:
            try:
                snap = client.get_latest_wallet_activity(
                    chain,
                    address,
                    str(wallet.get("remark") or "Wallet"),
                    wallet.get("avatar_kind", "emoji"),
                    wallet.get("avatar_value", ""),
                    group=str(wallet.get("group") or "默认"),
                )
                if snap.side:
                    self._log_wallet_fetch(snap)
                    snapshots.append(snap)
            except GmgnApiError as exc:
                LOG.warning("GMGN wallet activity error: %s", exc)
                retry_delay_ms = max(retry_delay_ms, self._error_wait_ms(exc, state.interval_ms))
                continue
            except Exception as exc:
                LOG.exception("Unexpected wallet activity error")
                self.error.emit(str(exc))
                continue
        if retry_delay_ms:
            self._last_wallet_poll = now + retry_delay_ms / 1000.0
        if not snapshots:
            return

        fresh_snapshots = []
        for snap in snapshots:
            wallet_key = f"{snap.chain}:{snap.wallet_address.lower()}"
            self._wallet_snapshots[wallet_key] = snap
            if getattr(snap, "side", ""):
                self._wallet_primary_chain[wallet_id] = snap.chain
            event_key = self._wallet_event_key(snap)
            previous_key = self._last_wallet_event_keys.get(wallet_key)
            if previous_key == event_key:
                continue
            self._last_wallet_event_keys[wallet_key] = event_key
            timestamp = getattr(snap, "timestamp", None) or 0
            if previous_key is None and timestamp and timestamp < int(self._started_at) - WALLET_ACTIVITY_STARTUP_GRACE_SECONDS:
                LOG.info(
                    "wallet activity baseline skipped chain=%s address=%s remark=%s token=%s ts=%s tx=%s",
                    getattr(snap, "chain", ""),
                    str(getattr(snap, "wallet_address", "")).lower(),
                    getattr(snap, "remark", ""),
                    getattr(snap, "token_symbol", ""),
                    timestamp,
                    short_tx(getattr(snap, "tx_hash", "")),
                )
                continue
            token_address = str(getattr(snap, "token_address", "") or "").strip()
            if token_address and client.is_honeypot_token(str(getattr(snap, "chain", "") or ""), token_address):
                LOG.info(
                    "wallet activity honeypot skipped chain=%s address=%s remark=%s token=%s ca=%s ts=%s tx=%s",
                    getattr(snap, "chain", ""),
                    str(getattr(snap, "wallet_address", "")).lower(),
                    getattr(snap, "remark", ""),
                    getattr(snap, "token_symbol", ""),
                    token_address,
                    timestamp,
                    short_tx(getattr(snap, "tx_hash", "")),
                )
                continue
            if self._is_wallet_noise(snap, wallet):
                continue
            fresh_snapshots.append(snap)

        if not fresh_snapshots:
            return

        latest = max(fresh_snapshots, key=lambda item: getattr(item, "timestamp", None) or 0)
        LOG.info(
            "wallet activity emitted chain=%s address=%s remark=%s side=%s token=%s ca=%s amount=%s%s ts=%s tx=%s",
            getattr(latest, "chain", ""),
            str(getattr(latest, "wallet_address", "")).lower(),
            getattr(latest, "remark", ""),
            getattr(latest, "side", ""),
            getattr(latest, "token_symbol", ""),
            getattr(latest, "token_address", ""),
            getattr(latest, "native_amount", None),
            getattr(latest, "native_symbol", ""),
            getattr(latest, "timestamp", None),
            short_tx(getattr(latest, "tx_hash", "")),
        )
        self.wallet_activity.emit(latest)

    def _wallet_event_key(self, snap: object) -> str:
        return (
            f"{getattr(snap, 'tx_hash', '')}:"
            f"{getattr(snap, 'side', '')}:"
            f"{getattr(snap, 'timestamp', '')}:"
            f"{getattr(snap, 'native_amount', '')}:"
            f"{getattr(snap, 'token_address', '')}"
        )

    def _is_wallet_noise(self, snap: object, wallet: dict[str, Any]) -> bool:
        return False

    def _wallet_poll_chains(self, wallet_id: str, chains: list[str], now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        primary = self._wallet_primary_chain.get(wallet_id) or chains[0]
        if primary not in chains:
            primary = chains[0]
        poll = [primary]
        if len(chains) <= 1:
            return poll
        if wallet_id in self._wallet_primary_chain and now < self._next_wallet_secondary_probe_at.get(wallet_id, 0.0):
            return poll
        secondary = [chain for chain in chains if chain != primary]
        index = self._next_wallet_chain_index.get(wallet_id, 0) % len(secondary)
        self._next_wallet_chain_index[wallet_id] = (index + 1) % len(secondary)
        poll.append(secondary[index])
        self._next_wallet_secondary_probe_at[wallet_id] = now + 30.0
        return poll

    def _log_wallet_fetch(self, snap: object) -> None:
        wallet_key = f"{getattr(snap, 'chain', '')}:{str(getattr(snap, 'wallet_address', '')).lower()}"
        event_key = (
            f"{getattr(snap, 'tx_hash', '')}:"
            f"{getattr(snap, 'side', '')}:"
            f"{getattr(snap, 'timestamp', '')}:"
            f"{getattr(snap, 'native_amount', '')}"
        )
        if self._last_wallet_fetch_log_key.get(wallet_key) == event_key:
            return
        self._last_wallet_fetch_log_key[wallet_key] = event_key
        LOG.info(
            "wallet activity fetched chain=%s address=%s remark=%s side=%s token=%s ca=%s amount=%s%s ts=%s tx=%s",
            getattr(snap, "chain", ""),
            str(getattr(snap, "wallet_address", "")).lower(),
            getattr(snap, "remark", ""),
            getattr(snap, "side", ""),
            getattr(snap, "token_symbol", ""),
            getattr(snap, "token_address", ""),
            getattr(snap, "native_amount", None),
            getattr(snap, "native_symbol", ""),
            getattr(snap, "timestamp", None),
            short_tx(getattr(snap, "tx_hash", "")),
        )

    def _normalize_wallets(self, wallets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for wallet in wallets or []:
            address = str(wallet.get("address") or "").strip()
            if address.startswith(("0x", "0X")):
                address = address.lower()
            chain = str(wallet.get("chain") or "").lower().strip()
            chains = wallet_chains(wallet)
            detected_chains = possible_wallet_chains(address, chain)
            if detected_chains:
                chains = unique_chains([candidate for candidate in chains + detected_chains if candidate in detected_chains])
            elif not chains and chain:
                chains = [chain]
            if not address or not chains:
                continue
            chain = chains[0]
            avatar_kind = str(wallet.get("avatar_kind") or "emoji").lower().strip()
            if avatar_kind not in {"emoji", "image"}:
                avatar_kind = "emoji"
            avatar_value = str(wallet.get("avatar_value") or "").strip()
            if not avatar_value:
                avatar_kind = "emoji"
                avatar_value = "\U0001f9e9"
            normalized.append(
                {
                    "remark": str(wallet.get("remark") or "Wallet").strip() or "Wallet",
                    "address": address,
                    "chain": chain,
                    "chains": chains,
                    "group": str(wallet.get("group") or "默认").strip()[:18] or "默认",
                    "avatar_kind": avatar_kind,
                    "avatar_value": avatar_value,
                }
            )
        return normalized

    def _normalize_tokens(self, tokens: list[dict[str, Any]] | None, chain: str, address: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for token in tokens or []:
            item = {
                "chain": str(token.get("chain") or "").lower().strip(),
                "address": str(token.get("address") or "").strip(),
                "symbol": str(token.get("symbol") or "").strip()[:18],
                "name": str(token.get("name") or "").strip()[:42],
                "remark": str(token.get("remark") or "").strip()[:28],
                "logo_url": str(token.get("logo_url") or "").strip(),
                "alert_threshold_percent": token_alert_threshold(token),
                "enabled": bool(token.get("enabled", True)),
                "pinned": bool(token.get("pinned", False)),
            }
            for key in ("last_market_cap", "last_price", "last_change_percent", "last_volume_24h", "last_alert_at", "alert_count"):
                if key in token:
                    item[key] = token.get(key)
            if item["address"].startswith(("0x", "0X")):
                item["address"] = item["address"].lower()
            if item["chain"] and item["address"]:
                normalized.append(item)
        return self._ensure_primary_token(normalized, chain, address)

    def _ensure_primary_token(self, tokens: list[dict[str, Any]], chain: str, address: str) -> list[dict[str, Any]]:
        chain = str(chain or "").lower().strip()
        address = str(address or "").strip()
        if address.startswith(("0x", "0X")):
            address = address.lower()
        primary = {
            "chain": chain,
            "address": address,
            "symbol": "",
            "name": "",
            "remark": "",
            "logo_url": "",
            "alert_threshold_percent": None,
            "enabled": True,
            "pinned": True,
        }
        found = False
        for token in tokens:
            if token_key(token) == token_key(primary):
                token["enabled"] = True
                token["pinned"] = True
                found = True
            else:
                token["pinned"] = False
        if not found and chain and address:
            tokens.insert(0, primary)
        return tokens

    def _sync_token_alert_baselines(self, tokens: list[dict[str, Any]] | None) -> None:
        active_thresholds: dict[str, float | None] = {}
        for token in tokens or []:
            if token.get("pinned") or not token.get("enabled", True):
                continue
            key = token_key(token)
            if not key.strip(":"):
                continue
            active_thresholds[key] = token_alert_threshold(token)
        for key in list(self._token_alert_baselines):
            if key not in active_thresholds or active_thresholds[key] is None:
                self._token_alert_baselines.pop(key, None)
                self._token_alert_origin_baselines.pop(key, None)
        for key, threshold in active_thresholds.items():
            previous = self._token_alert_thresholds.get(key)
            if threshold is None:
                self._token_alert_baselines.pop(key, None)
                self._token_alert_origin_baselines.pop(key, None)
            elif previous != threshold:
                self._token_alert_baselines.pop(key, None)
                self._token_alert_origin_baselines.pop(key, None)
                self._last_token_alert_key.pop(key, None)
        self._token_alert_thresholds = active_thresholds

    def _error_wait_ms(self, exc: GmgnApiError, base_interval_ms: int) -> int:
        if exc.status == 429:
            if exc.reset_at:
                wait = max(1000, int((exc.reset_at - time.time()) * 1000) + 1000)
                return min(wait, 5 * 60 * 1000)
            return max(5000, base_interval_ms * 5)
        return min(max(1500, base_interval_ms * 2), 30_000)


def wallet_chains(wallet: dict[str, Any]) -> list[str]:
    raw = wallet.get("chains")
    if isinstance(raw, str):
        candidates = [part.strip().lower() for part in raw.split(",")]
    elif isinstance(raw, list):
        candidates = [str(part).lower().strip() for part in raw]
    else:
        candidates = []
    chain = str(wallet.get("chain") or "").lower().strip()
    if chain and chain not in candidates:
        candidates.insert(0, chain)

    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def token_alert_threshold(token: dict[str, Any]) -> float | None:
    value = token.get("alert_threshold_percent")
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


def unique_chains(chains: list[str]) -> list[str]:
    ordered: list[str] = []
    for chain in chains:
        chain = str(chain).lower().strip()
        if chain and chain not in ordered:
            ordered.append(chain)
    return ordered


def primary_token(tokens: list[dict[str, Any]] | None) -> dict[str, Any]:
    for token in tokens or []:
        if token.get("pinned") and token.get("enabled", True):
            return token
    for token in tokens or []:
        if token.get("enabled", True):
            return token
    return (tokens or [{}])[0] if tokens else {"chain": "", "address": ""}


def token_key(token: dict[str, Any]) -> str:
    return f"{str(token.get('chain') or '').lower()}:{str(token.get('address') or '').lower()}"


def token_delta_percent(previous: TokenSnapshot, current: TokenSnapshot) -> float | None:
    old = previous.market_cap or previous.price
    new = current.market_cap or current.price
    if old is None or new is None or old <= 0:
        return None
    return ((new - old) / old) * 100.0


def format_alert_value(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:.8g}"


def short_tx(value: str) -> str:
    value = str(value or "")
    if len(value) <= 18:
        return value
    return f"{value[:10]}...{value[-6:]}"
