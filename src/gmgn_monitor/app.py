from __future__ import annotations

import logging
import json
import sys
from dataclasses import asdict

from PySide6.QtCore import QLockFile, QPoint, Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME
from . import autostart
from .config import AppConfig, app_data_dir, normalize_token, normalize_tokens, normalize_wallet, primary_token, token_key
from .credentials import save_api_key
from .event_store import append_event
from .logging_setup import setup_logging
from .gmgn_client import GmgnOpenApiClient, is_valid_wallet_address, normalize_wallet_address, possible_wallet_chains
from .market_worker import MarketWorker
from .ui.api_key_dialog import ApiKeyDialog
from .ui.floating_card import FloatingCard
from .ui.images import tray_icon
from .ui.portfolio_dialog import PortfolioDialog
from .ui.timeline_dialog import TimelineDialog
from .ui.theme import SKIN_ORDER, app_stylesheet, get_theme, menu_stylesheet, normalize_skin, set_active_theme
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
        set_active_theme(self.config.skin)
        self.qt_app.setStyleSheet(app_stylesheet(get_theme(self.config.skin)))
        self.card = FloatingCard()
        self.card.set_theme(self.config.skin)
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
        self.portfolio_dialog: PortfolioDialog | None = None
        self.wallet_portfolio_dialog: PortfolioDialog | None = None
        self.timeline_dialog: TimelineDialog | None = None
        self._place_card()
        self.card.show()
        if self._ensure_api_key():
            self._start_worker()

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet(menu_stylesheet(get_theme(self.config.skin)))
        ca_action = QAction("CA", menu)
        ca_action.triggered.connect(self.edit_ca)
        wallet_action = QAction("钱包监控", menu)
        wallet_action.triggered.connect(self.edit_wallet)
        portfolio_action = QAction("个人持仓", menu)
        portfolio_action.triggered.connect(lambda _checked=False: self.edit_portfolio())
        timeline_action = QAction("事件时间线", menu)
        timeline_action.triggered.connect(self.show_timeline)
        api_key_action = QAction("API Key", menu)
        api_key_action.triggered.connect(self.edit_api_key)
        export_action = QAction("导出配置", menu)
        export_action.triggered.connect(self.export_config)
        import_action = QAction("导入配置", menu)
        import_action.triggered.connect(self.import_config)
        self.skin_menu = QMenu("皮肤", menu)
        self.skin_menu.setStyleSheet(menu.styleSheet())
        self.skin_group = QActionGroup(self.skin_menu)
        self.skin_group.setExclusive(True)
        self.skin_actions: dict[str, QAction] = {}
        for skin_key in SKIN_ORDER:
            theme = get_theme(skin_key)
            action = QAction(theme.label, self.skin_menu)
            action.setCheckable(True)
            action.setChecked(normalize_skin(self.config.skin) == skin_key)
            action.triggered.connect(lambda _checked=False, key=skin_key: self._apply_skin(key))
            self.skin_group.addAction(action)
            self.skin_menu.addAction(action)
            self.skin_actions[skin_key] = action
        self.autostart_action = QAction("开机启动", menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(self.config.autostart)
        self.autostart_action.triggered.connect(self.toggle_autostart)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(ca_action)
        menu.addAction(wallet_action)
        menu.addAction(portfolio_action)
        menu.addAction(timeline_action)
        menu.addAction(api_key_action)
        menu.addAction(export_action)
        menu.addAction(import_action)
        menu.addMenu(self.skin_menu)
        menu.addAction(self.autostart_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def _apply_skin(self, skin: str) -> None:
        skin = normalize_skin(skin)
        theme = set_active_theme(skin)
        self.config.skin = skin
        self.config.save()
        self.qt_app.setStyleSheet(app_stylesheet(theme))
        if self.menu:
            css = menu_stylesheet(theme)
            self.menu.setStyleSheet(css)
            self.skin_menu.setStyleSheet(css)
        for key, action in getattr(self, "skin_actions", {}).items():
            action.setChecked(key == skin)
        self.card.set_theme(skin)
        if self.token_dialog and self.token_dialog.isVisible() and hasattr(self.token_dialog, "set_theme"):
            self.token_dialog.set_theme(skin)
        if self.wallet_dialog and self.wallet_dialog.isVisible() and hasattr(self.wallet_dialog, "set_theme"):
            self.wallet_dialog.set_theme(skin)
        if self.portfolio_dialog and self.portfolio_dialog.isVisible() and hasattr(self.portfolio_dialog, "set_theme"):
            self.portfolio_dialog.set_theme(skin)
        if self.timeline_dialog and self.timeline_dialog.isVisible() and hasattr(self.timeline_dialog, "set_theme"):
            self.timeline_dialog.set_theme(skin)

    def show_context_menu(self, pos: QPoint) -> None:
        self.menu.popup(pos)

    def open_current_gmgn(self) -> None:
        self.card.open_current_token()

    def copy_current_ca(self) -> None:
        address = str(self.config.address or "").strip()
        if address:
            self.qt_app.clipboard().setText(address)

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

    def _preview_timeline_token(self, chain: str, address: str) -> None:
        chain = str(chain or "").lower().strip()
        address = str(address or "").strip()
        if address.startswith(("0x", "0X")):
            address = address.lower()
        if not chain or not address:
            return
        self.config.chain = chain
        self.config.address = address
        self.config.save()
        if self.worker:
            self.worker.update_tokens(self.config.tokens or [], chain, address)
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
        dialog = WalletDialog(self.config.wallets or [], self.config.api_key(), self.config.api_host, self.card)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.wallets_changed.connect(self._save_wallet_list)
        dialog.portfolio_requested.connect(self._open_wallet_portfolio)
        dialog.accepted.connect(lambda dialog=dialog: self._save_wallet_list(dialog.wallets))
        dialog.finished.connect(lambda _result: self._clear_wallet_dialog(dialog))
        self.wallet_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_wallet_dialog(self, dialog: WalletDialog) -> None:
        if self.wallet_dialog is dialog:
            self.wallet_dialog = None

    def edit_portfolio(self, evm_address: str | None = None, sol_address: str | None = None) -> None:
        has_requested_addresses = evm_address is not None or sol_address is not None
        evm_address = normalize_wallet_address(evm_address) if evm_address is not None else self.config.portfolio_evm_wallet_address
        sol_address = normalize_wallet_address(sol_address) if sol_address is not None else self.config.portfolio_sol_wallet_address
        if self.portfolio_dialog and self.portfolio_dialog.isVisible():
            if has_requested_addresses:
                self.portfolio_dialog.set_wallet_addresses(evm_address, sol_address)
            self.portfolio_dialog.raise_()
            self.portfolio_dialog.activateWindow()
            return
        dialog = PortfolioDialog(
            self.config.api_key(),
            self.config.api_host,
            evm_address,
            sol_address,
            self.config.portfolio_holdings_cache or [],
            self.card,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.wallet_addresses_changed.connect(self._save_portfolio_wallet_addresses)
        dialog.holdings_updated.connect(self._save_portfolio_holdings_cache)
        dialog.finished.connect(lambda _result: self._clear_portfolio_dialog(dialog))
        self.portfolio_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_wallet_portfolio(self, address: str) -> None:
        address = normalize_wallet_address(address)
        if not address:
            return
        chains = possible_wallet_chains(address)
        evm_address = address if address.startswith("0x") and chains else ""
        sol_address = address if chains == ["sol"] else ""
        if not evm_address and not sol_address:
            return
        if self.wallet_portfolio_dialog and self.wallet_portfolio_dialog.isVisible():
            self.wallet_portfolio_dialog.set_wallet_addresses(evm_address, sol_address)
            self.wallet_portfolio_dialog.raise_()
            self.wallet_portfolio_dialog.activateWindow()
            return
        dialog = PortfolioDialog(
            self.config.api_key(),
            self.config.api_host,
            evm_address,
            sol_address,
            [],
            self.card,
            title="钱包持仓",
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.finished.connect(lambda _result: self._clear_wallet_portfolio_dialog(dialog))
        self.wallet_portfolio_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_portfolio_dialog(self, dialog: PortfolioDialog) -> None:
        if self.portfolio_dialog is dialog:
            self.portfolio_dialog = None

    def _clear_wallet_portfolio_dialog(self, dialog: PortfolioDialog) -> None:
        if self.wallet_portfolio_dialog is dialog:
            self.wallet_portfolio_dialog = None

    def show_timeline(self) -> None:
        if self.timeline_dialog and self.timeline_dialog.isVisible():
            self.timeline_dialog.reload()
            self.timeline_dialog.raise_()
            self.timeline_dialog.activateWindow()
            return
        dialog = TimelineDialog(self.card)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.set_theme(self.config.skin)
        dialog.token_requested.connect(self._preview_timeline_token)
        dialog.finished.connect(lambda _result: self._clear_timeline_dialog(dialog))
        self.timeline_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_timeline_dialog(self, dialog: TimelineDialog) -> None:
        if self.timeline_dialog is dialog:
            self.timeline_dialog = None

    def export_config(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(self.card, "导出配置", str(app_data_dir() / "gmgn_monitor_config.json"), "JSON (*.json)")
        if not path:
            return
        data = asdict(self.config)
        data.pop("api_key", None)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            QMessageBox.warning(self.card, "导出配置", f"导出失败: {exc}")

    def import_config(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self.card, "导入配置", str(app_data_dir()), "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            QMessageBox.warning(self.card, "导入配置", f"读取失败: {exc}")
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self.card, "导入配置", "配置文件格式不正确。")
            return
        imported = AppConfig.from_data({**asdict(self.config), **data})
        imported.api_host = self.config.api_host
        self.config = imported
        self.config.save()
        self._apply_skin(self.config.skin)
        if self.worker:
            self.worker.update_tokens(self.config.tokens or [], self.config.chain, self.config.address)
            self.worker.update_wallets(self.config.wallets or [])
        self.card.set_status("Switching")

    def _save_portfolio_wallet_addresses(self, evm_address: str, sol_address: str) -> None:
        evm_address = str(evm_address or "").strip().lower()
        sol_address = str(sol_address or "").strip()
        legacy_address = evm_address or sol_address
        if (
            self.config.portfolio_evm_wallet_address == evm_address
            and self.config.portfolio_sol_wallet_address == sol_address
            and self.config.portfolio_wallet_address == legacy_address
        ):
            return
        self.config.portfolio_evm_wallet_address = evm_address
        self.config.portfolio_sol_wallet_address = sol_address
        self.config.portfolio_wallet_address = legacy_address
        self.config.save()

    def _save_portfolio_holdings_cache(self, holdings: object) -> None:
        if not isinstance(holdings, list):
            return
        clean: list[dict[str, object]] = []
        for item in holdings[:20]:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row.pop("_logo_pixmap", None)
            row.pop("raw", None)
            clean.append(row)
        self.config.portfolio_holdings_cache = clean
        self.config.save()

    def _apply_wallet_dialog(self, dialog: WalletDialog) -> None:
        self._save_wallet_list(dialog.wallets)

    def _save_wallet_list(self, raw_wallets: object) -> None:
        if not isinstance(raw_wallets, list):
            return
        wallets = [wallet for wallet in raw_wallets if isinstance(wallet, dict) and str(wallet.get("address", "")).strip()]
        if not wallets:
            self.config.wallets = []
            self.config.save()
            self.card.update_wallet_activity(None)
            if self.worker:
                self.worker.update_wallets([])
            return

        normalized_wallets: list[dict[str, object]] = []
        invalid: list[str] = []
        for wallet in wallets:
            wallet = normalize_wallet(wallet)
            if not is_valid_wallet_address(wallet["address"]):
                invalid.append(wallet["remark"])
                continue
            detected_chains = possible_wallet_chains(str(wallet["address"]), str(wallet.get("chain") or ""))
            wallet["chains"] = detected_chains
            wallet["chain"] = detected_chains[0] if detected_chains else str(wallet.get("chain") or "")
            normalized_wallets.append(wallet)

        if invalid:
            parts = []
            parts.append("地址格式不正确: " + ", ".join(invalid))
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
        self.worker.snapshot.connect(self._on_market_snapshot)
        self.worker.wallet_activity.connect(self._on_wallet_activity)
        self.worker.token_alert.connect(self._on_token_alert)
        self.worker.token_risk.connect(self.card.update_token_risk)
        self.worker.status.connect(self.card.set_status)
        self.worker.error.connect(self.card.set_error)
        self.worker.start()
        LOG.info("worker started")

    def _on_market_snapshot(self, snap: object) -> None:
        self.card.update_snapshot(snap)

    def _on_wallet_activity(self, snap: object) -> None:
        self.card.update_wallet_activity(snap)
        if not getattr(snap, "side", ""):
            return
        side = "买入" if getattr(snap, "side", "") == "buy" else "卖出"
        title = f"{getattr(snap, 'remark', '') or '钱包'} {side} {getattr(snap, 'token_symbol', '') or 'TOKEN'}"
        group = str(getattr(snap, "group", "") or "").strip()
        amount = f"{getattr(snap, 'native_amount', '') or '--'}{getattr(snap, 'native_symbol', '') or ''}"
        append_event(
            {
                "kind": "wallet",
                "title": title,
                "subtitle": f"{group + ' · ' if group and group != '默认' else ''}{short_token_address(str(getattr(snap, 'token_address', '') or ''))}",
                "chain": getattr(snap, "chain", ""),
                "address": getattr(snap, "token_address", ""),
                "side": getattr(snap, "side", ""),
                "timestamp": getattr(snap, "timestamp", None),
                "received_at": getattr(snap, "received_at", None),
                "logo_url": getattr(snap, "token_logo_url", ""),
                "value": amount,
            }
        )
        if self.timeline_dialog and self.timeline_dialog.isVisible():
            self.timeline_dialog.reload()

    def _on_token_alert(self, payload: object) -> None:
        self.card.update_token_alert(payload)
        if not isinstance(payload, dict):
            return
        self._update_token_metrics(payload, triggered=bool(payload.get("triggered")))
        if not payload.get("triggered"):
            return
        delta = payload.get("trigger_delta_percent") or payload.get("delta_percent")
        try:
            delta_value = float(delta)
        except (TypeError, ValueError):
            delta_value = 0.0
        side = "up" if delta_value >= 0 else "down"
        append_event(
            {
                "kind": "token_alert",
                "title": f"{payload.get('symbol') or 'TOKEN'} {'上涨' if side == 'up' else '下跌'}",
                "subtitle": str(payload.get("reason") or "市值突破阈值"),
                "chain": payload.get("chain", ""),
                "address": payload.get("address", ""),
                "side": side,
                "received_at": payload.get("received_at"),
                "logo_url": payload.get("logo_url", ""),
                "value": f"{delta_value:+.2f}%",
            }
        )
        if self.timeline_dialog and self.timeline_dialog.isVisible():
            self.timeline_dialog.reload()

    def _update_token_metrics(self, payload: dict[str, object], triggered: bool = False) -> None:
        key = f"{str(payload.get('chain') or '').lower()}:{str(payload.get('address') or '').lower()}"
        changed = False
        for token in self.config.tokens or []:
            if token_key(token) != key:
                continue
            token["last_market_cap"] = payload.get("market_cap")
            token["last_price"] = payload.get("price")
            token["last_change_percent"] = payload.get("delta_percent")
            token["last_volume_24h"] = payload.get("volume_24h")
            if triggered:
                token["last_alert_at"] = payload.get("received_at")
                token["alert_count"] = int(token.get("alert_count") or 0) + 1
            changed = True
            break
        if changed:
            self.config.save()

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
