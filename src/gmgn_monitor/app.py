from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QLockFile, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME
from . import autostart
from .config import AppConfig, app_data_dir, normalize_token, normalize_tokens, normalize_wallet, primary_token, token_key
from .credentials import save_api_key
from .logging_setup import setup_logging
from .gmgn_client import GmgnOpenApiClient, is_valid_wallet_address, possible_wallet_chains
from .market_worker import MarketWorker
from .ui.api_key_dialog import ApiKeyDialog
from .ui.floating_card import FloatingCard
from .ui.images import tray_icon
from .ui.token_dialog import TokenDialog
from .ui.wallet_dialog import WalletDialog

LOG = logging.getLogger(__name__)


class MonitorApp:
    def __init__(self, qt_app: QApplication) -> None:
        setup_logging()
        self.qt_app = qt_app
        self._shutting_down = False
        self.config = AppConfig.load()
        LOG.info(
            "app started token=%s:%s refresh=%sms wallets=%s",
            self.config.chain,
            self.config.address,
            self.config.refresh_interval_ms,
            [
                {
                    "remark": wallet.get("remark", ""),
                    "address": str(wallet.get("address", "")).lower(),
                    "chain": wallet.get("chain", ""),
                    "chains": wallet.get("chains", []),
                }
                for wallet in (self.config.wallets or [])
            ],
        )
        self.config.autostart = autostart.is_enabled()
        self.card = FloatingCard()
        self.card.set_locked(self.config.locked)
        self.card.position_changed.connect(self._save_position)
        self.card.menu_requested.connect(self.show_context_menu)
        self.card.monitor_token_requested.connect(self._switch_main_token)

        self.menu = self._build_menu()
        self.tray = QSystemTrayIcon(tray_icon(), self.qt_app)
        self.tray.setToolTip(APP_NAME)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

        self.worker: MarketWorker | None = None
        self.token_dialog: TokenDialog | None = None
        self.wallet_dialog: WalletDialog | None = None
        self._place_card()
        self.card.show()
        if self._ensure_api_key():
            self._start_worker()

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet(
            """
            QMenu { background: #11161a; color: #edf7f3; border: 1px solid #2a3439; padding: 6px; }
            QMenu::item { padding: 8px 26px 8px 22px; border-radius: 6px; }
            QMenu::item:selected { background: #1d7f55; }
            QMenu::indicator:checked { image: none; background: #25d184; border-radius: 5px; width: 10px; height: 10px; }
            """
        )
        ca_action = QAction("CA", menu)
        ca_action.triggered.connect(self.edit_ca)
        wallet_action = QAction("钱包监控", menu)
        wallet_action.triggered.connect(self.edit_wallet)
        api_key_action = QAction("API Key", menu)
        api_key_action.triggered.connect(self.edit_api_key)
        self.autostart_action = QAction("开机启动", menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(self.config.autostart)
        self.autostart_action.triggered.connect(self.toggle_autostart)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(ca_action)
        menu.addAction(wallet_action)
        menu.addAction(api_key_action)
        menu.addAction(self.autostart_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def show_context_menu(self, pos: QPoint) -> None:
        self.menu.popup(pos)

    def edit_ca(self) -> None:
        if self.token_dialog and self.token_dialog.isVisible():
            self.token_dialog.raise_()
            self.token_dialog.activateWindow()
            return
        dialog = TokenDialog(self.config.tokens or [], self.config.api_key(), self.config.api_host, self.card)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.main_token_requested.connect(lambda chain, address: self._switch_main_token(chain, address, dialog.tokens))
        dialog.tokens_changed.connect(self._save_token_list)
        dialog.accepted.connect(lambda dialog=dialog: self._save_token_list(dialog.tokens))
        dialog.finished.connect(lambda _result: self._clear_token_dialog(dialog))
        self.token_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_token_dialog(self, dialog: TokenDialog) -> None:
        if self.token_dialog is dialog:
            self.token_dialog = None

    def _save_token_list(self, raw_tokens: object) -> None:
        if not isinstance(raw_tokens, list):
            return
        tokens = normalize_tokens({"tokens": raw_tokens, "chain": self.config.chain, "address": self.config.address}, include_default=False)
        tokens = [token for token in tokens if token.get("chain") and token.get("address")]
        if not tokens:
            return
        self.config.tokens = tokens
        pinned = primary_token(tokens)
        if pinned.get("chain") and pinned.get("address"):
            self.config.chain = str(pinned["chain"])
            self.config.address = str(pinned["address"])
        self.config.save()
        if self.worker:
            self.worker.update_tokens(tokens, self.config.chain, self.config.address)

    def _resolve_and_apply_tokens(self, raw_tokens: list[dict[str, object]]) -> None:
        self.card.set_status("Detecting")
        tokens, errors = self._resolve_tokens(raw_tokens)
        if errors:
            QMessageBox.warning(self.card, "CA", "\n".join(errors))
            self.card.set_status("Live")
            return
        if not tokens:
            QMessageBox.warning(self.card, "CA", "至少保留一个 CA。")
            self.card.set_status("Live")
            return

        self.config.tokens = tokens
        pinned = primary_token(tokens)
        self.config.chain = pinned["chain"]
        self.config.address = pinned["address"]
        self.config.save()
        if self.worker:
            self.worker.update_tokens(tokens, self.config.chain, self.config.address)
        self.card.set_status("Switching")

    def _resolve_and_switch_tokens(self, raw_tokens: list[dict[str, object]], chain: str, address: str) -> None:
        tokens, errors = self._resolve_tokens(raw_tokens)
        if not errors:
            pinned = primary_token(tokens)
            self._switch_main_token(str(pinned["chain"]), str(pinned["address"]), tokens)

    def _switch_main_token(self, chain: str, address: str, tokens: list[dict[str, object]] | None = None) -> None:
        tokens = [dict(token) for token in (tokens or self.config.tokens or [])]
        key = f"{chain}:{address}".lower()
        found = False
        for token in tokens:
            is_target = token_key(token) == key
            was_pinned = bool(token.get("pinned"))
            token["pinned"] = is_target
            if is_target:
                token["enabled"] = True
                found = True
            elif was_pinned:
                token["alert_threshold_percent"] = None
        if not found:
            tokens.insert(0, normalize_token({"chain": chain, "address": address, "enabled": True, "pinned": True}))
        self.config.tokens = tokens
        self.config.chain = chain
        self.config.address = address
        self.config.save()
        if self.worker:
            self.worker.update_tokens(tokens, chain, address)
        self.card.set_status("Switching")

    def _resolve_tokens(self, raw_tokens: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[str]]:
        client = self._gmgn_client()
        normalized = normalize_tokens({"tokens": raw_tokens, "chain": self.config.chain, "address": self.config.address}, include_default=False)
        resolved: list[dict[str, object]] = []
        errors: list[str] = []
        seen: set[str] = set()
        for token in normalized:
            if not token.get("address"):
                continue
            if not token.get("chain"):
                detected = self._detect_chain(str(token.get("address") or ""), client=client)
                if not detected:
                    errors.append(f"未识别 CA: {short_token_address(str(token.get('address') or ''))}")
                    continue
                token["chain"] = detected
            key = token_key(token)
            if key in seen:
                continue
            seen.add(key)
            if client and (not token.get("symbol") or not token.get("name") or not token.get("logo_url")):
                try:
                    snap = client.get_token_info(str(token["chain"]), str(token["address"]))
                    token["symbol"] = snap.symbol
                    token["name"] = snap.name
                    token["logo_url"] = snap.logo_url
                except Exception:
                    pass
            resolved.append(token)

        if resolved and not any(token.get("pinned") and token.get("enabled", True) for token in resolved):
            resolved[0]["pinned"] = True
            resolved[0]["enabled"] = True
        pinned_seen = False
        for token in resolved:
            if token.get("pinned"):
                if pinned_seen:
                    token["pinned"] = False
                else:
                    token["enabled"] = True
                    pinned_seen = True
        return resolved, errors

    def edit_wallet(self) -> None:
        if self.wallet_dialog and self.wallet_dialog.isVisible():
            self.wallet_dialog.raise_()
            self.wallet_dialog.activateWindow()
            return
        dialog = WalletDialog(self.config.wallets or [], self.card)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.accepted.connect(lambda dialog=dialog: self._apply_wallet_dialog(dialog))
        dialog.finished.connect(lambda _result: self._clear_wallet_dialog(dialog))
        self.wallet_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_wallet_dialog(self, dialog: WalletDialog) -> None:
        if self.wallet_dialog is dialog:
            self.wallet_dialog = None

    def _apply_wallet_dialog(self, dialog: WalletDialog) -> None:
        wallets = [wallet for wallet in dialog.wallets if wallet.get("address", "").strip()]
        if not wallets:
            self.config.wallets = []
            self.config.save()
            self.card.update_wallet_activity(None)
            if self.worker:
                self.worker.update_wallets([])
            return

        self.card.set_status("Detecting")
        normalized_wallets: list[dict[str, object]] = []
        invalid: list[str] = []
        undetected: list[str] = []
        for wallet in wallets:
            wallet = normalize_wallet(wallet)
            if not is_valid_wallet_address(wallet["address"]):
                invalid.append(wallet["remark"])
                continue
            detected_chains = self._detect_wallet_chains(wallet["address"], str(wallet.get("chain") or ""))
            if not detected_chains:
                undetected.append(wallet["remark"])
                continue
            wallet["chains"] = detected_chains
            wallet["chain"] = detected_chains[0]
            normalized_wallets.append(wallet)

        if invalid or undetected:
            parts = []
            if invalid:
                parts.append("地址格式不正确: " + ", ".join(invalid))
            if undetected:
                parts.append("未识别链: " + ", ".join(undetected))
            QMessageBox.warning(self.card, "钱包监控", "\n".join(parts))
            self.card.set_status("Live")
            return

        self.config.wallets = normalized_wallets
        first = normalized_wallets[0] if normalized_wallets else {}
        self.config.wallet_remark = first.get("remark", "")
        self.config.wallet_address = first.get("address", "")
        self.config.wallet_chain = first.get("chain", "")
        self.config.wallet_avatar_kind = first.get("avatar_kind", "emoji")
        self.config.wallet_avatar_value = first.get("avatar_value", "")
        self.config.save()
        if self.worker:
            self.worker.update_wallets(normalized_wallets)
        self.card.set_status("Switching")

    def edit_api_key(self) -> None:
        dialog = ApiKeyDialog(self.config.api_key(), self.card)
        if dialog.exec() != ApiKeyDialog.DialogCode.Accepted:
            return
        self._apply_api_key(dialog.api_key)

    def _ensure_api_key(self) -> bool:
        if self.config.api_key():
            return True
        dialog = ApiKeyDialog("", self.card)
        if dialog.exec() != ApiKeyDialog.DialogCode.Accepted:
            self.card.set_error("Missing GMGN API Key")
            return False
        self._apply_api_key(dialog.api_key, restart=False)
        return bool(self.config.api_key())

    def _apply_api_key(self, api_key: str, restart: bool = True) -> None:
        api_key = str(api_key or "").strip()
        if not api_key:
            return
        save_api_key(api_key)
        self.card.set_status("Switching")
        if restart:
            self._restart_worker()

    def _restart_worker(self) -> None:
        if self.worker:
            self.worker.stop()
            self.worker.wait(2500)
            self.worker = None
        self._start_worker()

    def toggle_autostart(self, checked: bool) -> None:
        try:
            autostart.set_enabled(checked)
            self.config.autostart = autostart.is_enabled()
            self.autostart_action.setChecked(self.config.autostart)
            self.config.save()
        except Exception as exc:
            LOG.exception("failed to toggle autostart")
            self.autostart_action.setChecked(False)
            QMessageBox.warning(self.card, "开机启动", f"设置失败: {exc}")

    def cleanup(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.config.x = self.card.x()
        self.config.y = self.card.y()
        self.config.save()
        if self.worker:
            self.worker.stop()
            self.worker.wait(2500)
        self.tray.hide()

    def quit(self) -> None:
        self.cleanup()
        self.qt_app.quit()

    def _start_worker(self) -> None:
        api_key = self.config.api_key()
        if not api_key:
            self.card.set_error("Missing GMGN_API_KEY")
            QMessageBox.warning(self.card, APP_NAME, "缺少 GMGN API Key，请在右键菜单中填写。")
            return
        self.worker = MarketWorker(
            api_key=api_key,
            host=self.config.api_host,
            chain=self.config.chain,
            address=self.config.address,
            interval_ms=self.config.refresh_interval_ms,
            wallet_remark=self.config.wallet_remark,
            wallet_address=self.config.wallet_address,
            wallet_chain=self.config.wallet_chain,
            wallets=self.config.wallets or [],
            tokens=self.config.tokens or [],
        )
        self.worker.snapshot.connect(self.card.update_snapshot)
        self.worker.wallet_activity.connect(self.card.update_wallet_activity)
        self.worker.token_alert.connect(self.card.update_token_alert)
        self.worker.status.connect(self.card.set_status)
        self.worker.error.connect(self.card.set_error)
        self.worker.start()
        LOG.info("worker started")

    def _gmgn_client(self) -> GmgnOpenApiClient | None:
        api_key = self.config.api_key()
        if not api_key:
            return None
        return GmgnOpenApiClient(api_key, self.config.api_host)

    def _detect_chain(self, address: str, client: GmgnOpenApiClient | None = None) -> str | None:
        client = client or self._gmgn_client()
        if client is None:
            return None
        address = str(address or "").strip()
        if address.startswith(("0x", "0X")):
            chains = ["eth", "base", "bsc"]
        else:
            chains = ["sol", "bsc", "base", "eth"]
        for chain in chains:
            try:
                snap = client.get_token_info(chain, address)
                if snap.symbol and snap.symbol != "TOKEN":
                    return chain
            except Exception:
                continue
        return None

    def _detect_wallet_chain(self, wallet_address: str) -> str | None:
        chains = self._detect_wallet_chains(wallet_address)
        return chains[0] if chains else None

    def _detect_wallet_chains(self, wallet_address: str, preferred: str = "") -> list[str]:
        api_key = self.config.api_key()
        if not api_key:
            return possible_wallet_chains(wallet_address, preferred or self.config.wallet_chain or self.config.chain)
        client = GmgnOpenApiClient(api_key, self.config.api_host)
        return client.detect_wallet_chains(wallet_address, preferred or self.config.wallet_chain or self.config.chain)

    def _place_card(self) -> None:
        screen = self.qt_app.primaryScreen()
        geometry = screen.availableGeometry() if screen else None
        if self.config.x is not None and self.config.y is not None:
            self.card.move(int(self.config.x), int(self.config.y))
            return
        if geometry:
            x = geometry.right() - self.card.width() - 28
            y = geometry.top() + 160
            self.card.move(x, y)

    def _save_position(self, x: int, y: int) -> None:
        self.config.x = x
        self.config.y = y
        self.config.save()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.card.show()
            self.card.raise_()
            self.card.activateWindow()


def run() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("GMGN")
    lock = QLockFile(str(app_data_dir() / "app.lock"))
    lock.setStaleLockTime(2000)
    if not lock.tryLock(100):
        if not lock.removeStaleLockFile() or not lock.tryLock(100):
            return 0
    app._gmgn_single_instance_lock = lock  # type: ignore[attr-defined]
    controller = MonitorApp(app)
    app.aboutToQuit.connect(controller.cleanup)
    return app.exec()


def short_token_address(address: str) -> str:
    if len(address) <= 14:
        return address
    return f"{address[:8]}...{address[-6:]}"
