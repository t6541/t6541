from __future__ import annotations

import ctypes
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import threading
import time
import tomllib
import pandas as pd

from .config import RETIRED_OKX_CONFIG_KEYS, load_config
from .credential_store import delete_credentials, load_credentials, save_credentials
from .dns_recovery import (is_dns_resolution_failure,
                           network_failure_summary,
                           is_sustained_pre_request_network_failure,
                           probe_configured_api_route)
from .live_audit import LIVE_INSTRUMENT, LiveAuditCredentials, OkxLiveReadOnlyClient
from .live_credential_store import delete_live_credentials, load_live_credentials, save_live_credentials
from .live_execution import OkxLiveManualClient, execute_approved_minimum_order
from .live_aggressive_adapter import OkxLiveAggressiveAdapter
from .live_account_settings import LiveAccountSettings, apply_account_strategy
from .account05_signals import evaluate_account05_signals
from .account05_execution import ACCOUNT05_INSTRUMENT, execute_account05_tick, import_manual_orders, manual_exit_owners
from .account05_live import Account05LiveClient
from .account05_state import Account05StateStore, PositionSide
from .account05_entry_review import review_summary
from .manual_order_review import load_manual_order_history, manual_order_rows
from .account05_review_view import (
    sort_recovery_pool_rows,
    sort_recovery_pool_by_structure_profit,
)
from .data import okx_current_unconfirmed_market, okx_history_market, okx_recent_market
from .live_ui_state import (aggressive_button_text, aggressive_click_transition,
                            aggressive_live_audit_error,
                            aggressive_network_backoff_seconds,
                            is_confirmed_aggressive_post_rejection,
                            is_retryable_aggressive_get_error)
from .live_state import LiveStateStore, approval_phrase
from .live_strategy import LIVE_PROFILES, scan_conservative_live_candidate
from .execution_guards import matched_closing_fills
from .okx import OkxCredentials, OkxDemoClient, OkxError
from .pipeline import run_pipeline
from .runner import observe_once, retry_delay_seconds
from .range_observer import observe_range_pivot_once, write_observation_report
from .range_pivot import STRATEGY_VERSION as RANGE_STRATEGY_VERSION
from .range_execution import execute_range_pivot_tick
from .ma20_retest import observe_ma20_retest_once
from .ma20_execution import execute_ma20_retest_tick
from .updater import download_verified_update, is_newer, load_manifest
from .validation_execution import (VALIDATION_VERSION, execute_validation_tick,
                                   execute_five_second_top_short_tick)
from .external_signal_receiver import start_external_signal_receiver
from .pullback_execution import monitor_pullbacks
from .version import (APP_VERSION, CURRENT_STRATEGY_RULE_SUMMARY, SIMPLE_EXECUTION_RULES, STRATEGY_RULES,
                      changelog_pages, changelog_text)
from .risk_profiles import PROFILE_LABELS, RISK_PROFILES, normalize_profile, profile_display, profile_spec
from .state import StateStore
from .live_condition_log import (read_condition_events_for_beijing_day,
                                 read_recent_condition_event_records)
from .live_fault_journal import write_live_fault
from .reversal_miss_view import (prune_reversal_miss_history, read_reversal_misses,
                                 reversal_feed_freshness, reversal_miss_table_row)
from .hourly_missed_entry import read_hourly_missed_entries, schedule_completed_hour_scan
from .ma_endpoint_view import ma_endpoint_table_row, read_ma_endpoint_lineage
from .trade_cycle_rules import LONG_CYCLE_ROWS, SHORT_CYCLE_ROWS, CYCLE_RULE_SECTIONS, build_entry_rule_audit
from .sniper_restore import restore_demo_structure_snipers
from .ui_localization import localize_main_status

if sys.platform != "win32":
    raise RuntimeError("The desktop client requires Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
comctl32.InitCommonControls()
ORDER_NOTICE_SESSION_START_MS = int(time.time() * 1000)
LIVE_AGGRESSIVE_POLL_SECONDS = 10
LIVE_DNS_SELF_HEAL_FAILURE_THRESHOLD = 3
LIVE_DNS_SELF_HEAL_COOLDOWN_SECONDS = 1800
LIVE_DNS_SELF_HEAL_FAILED_COOLDOWN_SECONDS = 60
LIVE_DNS_SELF_HEAL_LAST_ATTEMPT = 0.0
LIVE_DNS_SELF_HEAL_LAST_VERIFIED = False
ORDER_NOTICE_WIDTH = 560
ORDER_NOTICE_HEIGHT = 500
# v0.7.43 was started after the selected API domain and split-routing network became stable.
# Observation timestamps are stored as naive UTC; UI and OKX fill timestamps are Beijing time.
NETWORK_STABLE_CUTOFF_UTC = "2026-08-27T10:45:13"
NETWORK_STABLE_CUTOFF_LOCAL = "2026-08-27 18:45:13"
NETWORK_STABLE_CUTOFF_MS = 1787827513000
def _is_stable_stage(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) >= datetime(2026, 8, 27, 10, 45, 13, tzinfo=timezone.utc)
    except ValueError:
        return False

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


user32.CreateWindowExW.restype = wintypes.HWND
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = LRESULT
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.GetMessageW.restype = wintypes.BOOL
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
shell32.ShellExecuteW.restype = wintypes.HINSTANCE
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_TABSTOP = 0x00010000
WS_BORDER = 0x00800000
BS_PUSHBUTTON = 0
ES_PASSWORD = 0x0020
ES_AUTOHSCROLL = 0x0080
ES_MULTILINE = 0x0004
ES_AUTOVSCROLL = 0x0040
ES_READONLY = 0x0800
EM_SETLIMITTEXT = 0x00C5
WS_VSCROLL = 0x00200000
WS_HSCROLL = 0x00100000
SS_LEFT = 0
LVS_REPORT = 0x0001
LVS_SHOWSELALWAYS = 0x0008
LVCF_FMT, LVCF_WIDTH, LVCF_TEXT = 0x0001, 0x0002, 0x0004
LVIF_TEXT = 0x0001
LVM_FIRST = 0x1000
LVM_DELETEALLITEMS = LVM_FIRST + 9
LVM_INSERTITEMW = LVM_FIRST + 77
LVM_SETITEMTEXTW = LVM_FIRST + 116
LVM_INSERTCOLUMNW = LVM_FIRST + 97
LVM_SETCOLUMNW = LVM_FIRST + 96
LVM_SETEXTENDEDLISTVIEWSTYLE = LVM_FIRST + 54
LVM_GETNEXTITEM = LVM_FIRST + 12
LVM_GETITEMTEXTW = LVM_FIRST + 115
LVNI_SELECTED = 0x0002
LVS_EX_GRIDLINES = 0x00000001
LVS_EX_FULLROWSELECT = 0x00000020
LVS_EX_DOUBLEBUFFER = 0x00010000
CW_USEDEFAULT = -2147483648
SW_SHOW = 5
SW_MAXIMIZE = 3
WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_COMMAND = 0x0111
WM_NOTIFY = 0x004E
WM_SIZE = 0x0005
WM_SETFONT = 0x0030
COLOR_WINDOW = 5
IDC_ARROW = 32512
NM_CLICK = -2
LVN_COLUMNCLICK = -108

ID_RUN, ID_REPORT, ID_FOLDER = 101, 102, 103
ID_PUBLIC, ID_ACCOUNT, ID_LEVERAGE = 104, 105, 106
ID_CREDENTIALS = 107
ID_CHANGELOG = 108
ID_BRANDING = 109
ID_SAVE_CREDENTIALS = 110
ID_DELETE_CREDENTIALS = 111
ID_INSTRUMENTS = 112
ID_UPDATE = 113
ID_OBSERVE_START = 114
ID_OBSERVE_PAUSE = 115
ID_DEMO_UNLOCK = 116
ID_CHANGELOG_CLOSE = 117
ID_CHANGELOG_PREVIOUS = 171
ID_CHANGELOG_NEXT = 172
ID_SECOND_API_OPEN = 118
ID_SECOND_API_SAVE = 119
ID_SECOND_API_VERIFY = 120
ID_SECOND_API_DELETE = 121
ID_SECOND_TIMEFRAME = 122
ID_SECOND_OBSERVE = 123
ID_SECOND_DEMO_UNLOCK = 124
ID_THIRD_API_OPEN, ID_THIRD_API_SAVE, ID_THIRD_API_VERIFY, ID_THIRD_API_DELETE, ID_THIRD_OBSERVE = 125, 126, 127, 128, 129
ID_THIRD_DEMO_UNLOCK = 130
ID_TRADE_DETAILS, ID_TRADE_REFRESH, ID_TRADE_CHART = 131, 132, 133
ID_NOTICE_CHART, ID_NOTICE_CLOSE = 134, 135
ID_STRATEGY_RULES, ID_RULES_CLOSE = 136, 137
ID_PROFILE_AGGRESSIVE, ID_PROFILE_CONSERVATIVE, ID_PROFILE_PRUDENT = 138, 139, 140
ID_FIRST_API_OPEN = 141
ID_LOSS_REVIEWS, ID_LOSS_REFRESH = 142, 143
ID_MA_EXPERIMENT, ID_MA_EXPERIMENT_REFRESH, ID_MA_EXPERIMENT_CLOSE = 144, 145, 146
ID_LIVE_OPEN, ID_LIVE_SAVE, ID_LIVE_AUDIT, ID_LIVE_DELETE, ID_LIVE_API_WEB = 147, 148, 149, 150, 151
ID_LIVE_SCAN = 152
ID_LIVE_AGGRESSIVE_OPEN, ID_LIVE_CONSERVATIVE_OPEN, ID_LIVE_PRUDENT_OPEN = 153, 154, 155
ID_LIVE_EXECUTE = 156
ID_LIVE_AGGRESSIVE_TOGGLE = 157
ID_LIVE_MAIN_CONSERVATIVE_SCAN, ID_LIVE_MAIN_CONSERVATIVE_EXECUTE = 158, 159
ID_LIVE_MAIN_PRUDENT_SCAN, ID_LIVE_MAIN_PRUDENT_EXECUTE = 160, 161
ID_LIVE_MAIN_REFRESH = 162
ID_SNAPSHOT_DETAILS, ID_SNAPSHOT_REFRESH, ID_SNAPSHOT_CLOSE = 163, 164, 165
ID_PINETS_COMPARISON, ID_PINETS_REFRESH, ID_PINETS_CLOSE = 166, 167, 168
ID_REVERSAL_MISSES, ID_REVERSAL_MISSES_REFRESH, ID_REVERSAL_MISSES_CLOSE = 169, 170, 171
ID_MA_ENDPOINTS, ID_MA_ENDPOINTS_REFRESH, ID_MA_ENDPOINTS_CLOSE = 173, 174, 175
ID_SIMPLE_RULES, ID_SIMPLE_RULES_CLOSE = 176, 177
ID_MAIN_LIFECYCLE_REFRESH = 178
ID_TRADING_CYCLES, ID_TRADING_CYCLES_CLOSE = 179, 180
ID_LIFECYCLE_DASHBOARD, ID_LIFECYCLE_DASHBOARD_REFRESH, ID_LIFECYCLE_DASHBOARD_CLOSE = 181, 182, 183
ID_CONDITION_LOG, ID_CONDITION_LOG_PREVIOUS, ID_CONDITION_LOG_NEXT = 184, 185, 186
ID_CONDITION_LOG_TODAY, ID_CONDITION_LOG_REFRESH, ID_CONDITION_LOG_CLOSE = 187, 188, 189
ID_LIVE_SAVE_SIZE = 190
ID_LIVE_ACCOUNT02_TOGGLE, ID_LIVE_ACCOUNT03_TOGGLE = 191, 192
ID_LIVE_ACCOUNT04_OPEN, ID_LIVE_ACCOUNT05_OPEN = 193, 194
ID_ACCOUNT05_SAVE_SETTINGS, ID_ACCOUNT05_REFRESH_SIGNALS, ID_ACCOUNT05_BASE_TOGGLE = 195, 196, 205
ID_ACCOUNT04_TOGGLE = 206
ID_MANUAL_ORDERS, ID_MANUAL_ORDERS_REFRESH = 207, 208
ID_LIVE_ACCOUNT05_TOGGLE = 197
ID_RECOVERY_POOL, ID_RECOVERY_POOL_REFRESH, ID_RECOVERY_POOL_CLOSE, ID_RECOVERY_POOL_RESET = 198, 199, 200, 204
ID_RECOVERY_POOL_DELETE = 209
ID_FIXED_ADDON_ORDERS, ID_FIXED_ADDON_REFRESH, ID_FIXED_ADDON_CLOSE = 201, 202, 203
WM_ORDER_NOTICE = 0x8001

HANDLES: dict[str, int] = {}
LAST_OUTPUT: Path | None = None
RUNNING = False
SESSION_CREDENTIALS: OkxCredentials | None = None
SECOND_SESSION_CREDENTIALS: OkxCredentials | None = None
THIRD_SESSION_CREDENTIALS: OkxCredentials | None = None
LIVE_SESSION_CREDENTIALS: dict[str, LiveAuditCredentials | None] = {
    "aggressive": None, "conservative": None, "prudent": None,
    "external_observer": None, "clone_research": None,
}
LIVE_PENDING_CANDIDATES: dict[str, str | None] = {
    "aggressive": None, "conservative": None, "prudent": None,
    "external_observer": None, "clone_research": None,
}
LIVE_ACCOUNT_STOPS = {slot: threading.Event() for slot in LIVE_SESSION_CREDENTIALS}
for _stop in LIVE_ACCOUNT_STOPS.values():
    _stop.set()
LIVE_ACCOUNT_STATES = {slot: "stopped" for slot in LIVE_SESSION_CREDENTIALS}
LIVE_AGGRESSIVE_NETWORK_TIMEOUT = 20
LIVE_ACCOUNT_THREADS: dict[str, threading.Thread | None] = {slot: None for slot in LIVE_SESSION_CREDENTIALS}
LIVE_FAST_ENTRY_ALLOWED = {slot: False for slot in LIVE_SESSION_CREDENTIALS}
LIVE_MAIN_LAST_TOUCH_TOTAL = 0
SECOND_DEMO_UNLOCKED = False
THIRD_DEMO_UNLOCKED = False
UNLOCK_API_LOCK = threading.Lock()
OBSERVING = False
DEMO_UNLOCKED = False
OBSERVE_STOP = threading.Event()
ORDER_NOTICE_QUEUE: list[dict[str, str]] = []
LIVE_PROTECTION_NOTICE_KEYS: set[str] = set()
LAST_TRIGGER_CONTEXT: dict[str, str] = {}
# Only open the verified OKX website.  Do not choose a login domain from public
# IP geolocation: VPNs, mobile networks and account registration regions can
# disagree, and a look-alike domain could capture account credentials.
OKX_ETH_SWAP_URL = "https://www.tpouxyihas.com/zh-hans/trade-swap/eth-usdt-swap"
OKX_API_MANAGEMENT_URL = "https://www.tpouxyihas.com/zh-hans/account/my-api"
MAIN_DESIGN_WIDTH = 1280
MAIN_DESIGN_HEIGHT = 1040
MAIN_LAYOUT: dict[int, tuple[int, int, int, int]] = {}
MAIN_FONTS: dict[int, int] = {}


class LVCOLUMNW(ctypes.Structure):
    _fields_ = [("mask", wintypes.UINT), ("fmt", ctypes.c_int), ("cx", ctypes.c_int),
                ("pszText", wintypes.LPWSTR), ("cchTextMax", ctypes.c_int),
                ("iSubItem", ctypes.c_int), ("iImage", ctypes.c_int),
                ("iOrder", ctypes.c_int), ("cxMin", ctypes.c_int),
                ("cxDefault", ctypes.c_int), ("cxIdeal", ctypes.c_int)]


class LVITEMW(ctypes.Structure):
    _fields_ = [("mask", wintypes.UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
                ("state", wintypes.UINT), ("stateMask", wintypes.UINT),
                ("pszText", wintypes.LPWSTR), ("cchTextMax", ctypes.c_int),
                ("iImage", ctypes.c_int), ("lParam", wintypes.LPARAM),
                ("iIndent", ctypes.c_int), ("iGroupId", ctypes.c_int),
                ("cColumns", wintypes.UINT), ("puColumns", ctypes.POINTER(wintypes.UINT)),
                ("piColFmt", ctypes.POINTER(ctypes.c_int)), ("iGroup", ctypes.c_int)]


class NMHDRW(ctypes.Structure):
    _fields_ = [("hwndFrom", wintypes.HWND), ("idFrom", ctypes.c_size_t),
                ("code", ctypes.c_int)]


class POINTW(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class NMITEMACTIVATEW(ctypes.Structure):
    _fields_ = [("hdr", NMHDRW), ("iItem", ctypes.c_int),
                ("iSubItem", ctypes.c_int), ("uNewState", wintypes.UINT),
                ("uOldState", wintypes.UINT), ("uChanged", wintypes.UINT),
                ("ptAction", POINTW), ("lParam", wintypes.LPARAM),
                ("uKeyFlags", wintypes.UINT)]


def _resource(relative: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / relative


WORKSPACE = Path(os.environ.get("QUANTBOT_WORKSPACE", Path.home() / "Documents" / "QuantBotWorkspace"))
CONFIG = WORKSPACE / "config.toml"
BRANDING = WORKSPACE / "branding.toml"
CREDENTIAL_FILE = WORKSPACE / "okx-demo-credentials.bin"
SECOND_CREDENTIAL_FILE = WORKSPACE / "okx-demo-credentials-strategy-02.bin"
THIRD_CREDENTIAL_FILE = WORKSPACE / "okx-demo-credentials-strategy-03.bin"
LIVE_WORKSPACE = Path(os.environ.get(
    "QUANTBOT_LIVE_WORKSPACE", Path.home() / "Documents" / "QuantBotWorkspace-Live"))
LIVE_CREDENTIAL_FILES = {
    "aggressive": LIVE_WORKSPACE / "credentials" / "okx-live-aggressive-credentials.bin",
    "conservative": LIVE_WORKSPACE / "credentials" / "okx-live-conservative-credentials.bin",
    # Keep the already-bound API in the prudent slot without moving or exposing it.
    "prudent": LIVE_WORKSPACE / "credentials" / "okx-live-readonly-credentials.bin",
    "external_observer": LIVE_WORKSPACE / "credentials" / "okx-live-account04-observer-credentials.bin",
    "clone_research": LIVE_WORKSPACE / "credentials" / "okx-live-account05-clone-credentials.bin",
}
LIVE_SHARED_TRADING_SLOTS = ("aggressive", "conservative", "prudent")
LIVE_TRADING_SLOTS = tuple(LIVE_SESSION_CREDENTIALS)
LIVE_OBSERVATION_SLOTS = ()
LIVE_SLOT_ORDER = LIVE_SHARED_TRADING_SLOTS + ("external_observer", "clone_research")
RANGE_CONFIG = WORKSPACE / "range-pivot.toml"
THIRD_CONFIG = WORKSPACE / "strategy-03.toml"
UPDATE_SOURCE = WORKSPACE / "update-source.txt"


def load_branding(path: Path) -> tuple[str, str]:
    """Load user-editable white-label text with safe, compact fallbacks."""
    fallback = ("谭明泽", "tmz8873")
    if not path.exists():
        return fallback
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8")).get("branding", {})
        name = str(values.get("name", fallback[0])).strip().replace("\r", " ").replace("\n", " ")[:32]
        wechat = str(values.get("wechat", fallback[1])).strip().replace("\r", " ").replace("\n", " ")[:48]
        return name or fallback[0], wechat or fallback[1]
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return fallback


def migrate_legacy_config(path: Path) -> bool:
    """Migrate app-generated legacy settings without losing user edits."""
    if not path.exists():
        return False
    original = path.read_text(encoding="utf-8")
    updated = original.replace('margin_mode = "isolated"', 'margin_mode = "cross"')
    updated = re.sub(r'(?m)^instrument\s*=\s*"([A-Z0-9-]+)"\s*$', r'instruments = ["\1"]', updated)
    for key in RETIRED_OKX_CONFIG_KEYS:
        updated = re.sub(rf'(?m)^{re.escape(key)}\s*=.*(?:\n|$)', '', updated)
    if updated == original:
        return False
    backup = path.with_suffix(".toml.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(updated, encoding="utf-8")
    return True


def load_instruments(path: Path) -> tuple[str, ...]:
    with path.open("rb") as handle:
        okx = tomllib.load(handle).get("okx", {})
    raw = okx.get("instruments") or ([okx["instrument"]] if "instrument" in okx else ["ETH-USDT-SWAP"])
    return tuple(str(item).upper() for item in raw)


def load_leverage(path: Path) -> int:
    with path.open("rb") as handle:
        return int(tomllib.load(handle).get("okx", {}).get("leverage", 10))


def save_instruments(path: Path, instruments: tuple[str, ...]) -> None:
    original = path.read_text(encoding="utf-8")
    value = json.dumps(list(instruments), ensure_ascii=False)
    if re.search(r"(?m)^instruments\s*=.*$", original):
        updated = re.sub(r"(?m)^instruments\s*=.*$", f"instruments = {value}", original)
    else:
        updated = re.sub(r'(?m)^instrument\s*=.*$', f"instruments = {value}", original)
    if updated == original and "instruments" not in original:
        raise ValueError("Cannot find [okx] instrument configuration")
    path.write_text(updated, encoding="utf-8")


def save_risk_profile(paths: tuple[Path, ...], profile: str) -> None:
    """Persist the profile in both strategy configs; credentials are untouched."""
    profile = normalize_profile(profile)
    for path in paths:
        if not path.exists():
            continue
        original = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^risk_profile\s*=.*$", original):
            updated = re.sub(r"(?m)^risk_profile\s*=.*$", f'risk_profile = "{profile}"', original)
        else:
            updated = re.sub(r"(?m)^(\[strategy\]\s*)$", rf'\1\nrisk_profile = "{profile}"', original, count=1)
        path.write_text(updated, encoding="utf-8")


def current_risk_profile() -> str:
    try:
        return load_config(CONFIG).strategy.risk_profile
    except (OSError, ValueError):
        return "aggressive"


def _refresh_risk_profile_buttons(profile: str | None = None) -> None:
    selected = normalize_profile(profile or current_risk_profile())
    for name in RISK_PROFILES:
        handle = HANDLES.get(f"profile_{name}", 0)
        if handle:
            user32.EnableWindow(handle, name != selected)


def _select_risk_profile(profile: str) -> None:
    profile = normalize_profile(profile)
    save_risk_profile((CONFIG, RANGE_CONFIG, THIRD_CONFIG), profile)
    if HANDLES.get("profile_status"):
        user32.SetWindowTextW(HANDLES["profile_status"], f"当前交易风格：{profile_display(profile)}｜{profile_spec(profile).description}")
    _refresh_risk_profile_buttons(profile)
    _message(f"已切换为{profile_display(profile)}。仅影响后续新信号，已有仓位和服务器保护单不变。")


def migrate_range_frequency_test_config(path: Path) -> bool:
    """Enable the explicit 1m high-frequency Demo profile without touching credentials."""
    if not path.exists():
        return False
    original = path.read_text(encoding="utf-8")
    updated = original
    if "frequency_test_mode" not in updated:
        updated = re.sub(
            r"(?m)^(max_adx_for_reversal\s*=.*)$", r"\1\nfrequency_test_mode = true", updated,
        )
    else:
        updated = re.sub(r"(?m)^frequency_test_mode\s*=.*$", "frequency_test_mode = true", updated)
    updated = re.sub(r"(?m)^max_trades_per_day\s*=.*$", "max_trades_per_day = 200", updated)
    updated = re.sub(r"(?m)^cooldown_seconds\s*=.*$", "cooldown_seconds = 60", updated)
    if updated == original:
        return False
    backup = path.with_suffix(".toml.frequency-test.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(updated, encoding="utf-8")
    return True


def _prepare() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        shutil.copy2(_resource("configs/default.toml"), CONFIG)
    if not BRANDING.exists():
        shutil.copy2(_resource("configs/branding.toml"), BRANDING)
    if not RANGE_CONFIG.exists():
        shutil.copy2(_resource("configs/range-pivot.toml"), RANGE_CONFIG)
    migrate_legacy_config(CONFIG)
    migrate_range_frequency_test_config(RANGE_CONFIG)


def _text(handle: int, value: str) -> None:
    user32.SetWindowTextW(handle, value)


def _message(value: str, title: str = "Codex QuantBot", error: bool = False) -> None:
    user32.MessageBoxW(HANDLES.get("main", 0), value, title, 0x10 if error else 0x40)


def _serialized_unlock(worker) -> None:
    """Avoid three unlock flows bursting the same OKX service simultaneously."""
    with UNLOCK_API_LOCK:
        worker()


def _assert_clear_except_owned_snipers(client: OkxDemoClient, prefix: str) -> None:
    snapshot = client.safety_snapshot()
    unrelated = [item for item in snapshot.get("orders", [])
                 if not str(item.get("clOrdId", "")).startswith(prefix + "SNP")]
    if snapshot.get("positions") or snapshot.get("algo_orders") or unrelated:
        raise OkxError("Demo账户存在持仓、保护委托或非本策略预埋单；请先处理后再解锁")


def _restore_after_unlock(strategy: str, credentials: OkxCredentials, prefix: str,
                          strategy_version: str, instrument: str) -> str:
    try:
        result = restore_demo_structure_snipers(
            ((strategy, credentials, prefix, strategy_version),), instrument,
            WORKSPACE / "live" / "state.sqlite3",
            range_config=load_config(RANGE_CONFIG) if strategy == "strategy_02" else None,
        )[0]
    except Exception as exc:
        return f"结构预埋恢复异常｜{exc}；普通策略解锁状态不受影响"
    actions = {"placed": "已补挂", "reanchored": "已重锚", "armed": "有效单已存在",
               "invalidated": "已撤失效单", "sibling_cancelled": "已撤反向兄弟单",
               "blocked": "安全条件阻止", "observe": "条件未成立", "skipped": "已跳过",
               "suspended": "合约暂停等待恢复", "error": "异常"}
    return f"结构预埋：{actions.get(result.action, result.action)}｜{result.reason}"


def _saved_demo_accounts() -> tuple[tuple[str, OkxCredentials | None, str, str], ...]:
    if current_risk_profile() != "aggressive":
        return ()
    return (
        ("strategy_01", SESSION_CREDENTIALS, "QBVAL", STRATEGY_RULES[0]["version"]),
        ("strategy_02", SECOND_SESSION_CREDENTIALS, "QBR", STRATEGY_RULES[1]["version"]),
        ("strategy_03", THIRD_SESSION_CREDENTIALS, "QBM", STRATEGY_RULES[2]["version"]),
    )


def _restore_saved_demo_state(cfg, database: Path):
    """Rescan queues and reconcile all eligible saved Demo accounts."""
    return restore_demo_structure_snipers(
        _saved_demo_accounts(), cfg.okx.instruments[0], database,
        range_config=load_config(RANGE_CONFIG),
    )


def _attempt_restore_saved_demo_state(cfg, database: Path):
    """Keep a transient recovery-scan timeout from aborting the strategy cycle."""
    try:
        return _restore_saved_demo_state(cfg, database), ""
    except Exception as exc:
        return (), str(exc)


def _startup_restore_worker() -> None:
    """Run the first recovery scan immediately after the desktop is visible."""
    try:
        cfg = load_config(CONFIG)
        results = _restore_saved_demo_state(cfg, WORKSPACE / "live" / "state.sqlite3")
        restored = sum(item.action in {"placed", "reanchored"} for item in results)
        armed = sum(item.action in {"armed", "suspended"} for item in results)
        errors = sum(item.action == "error" for item in results)
        _text(HANDLES["status"], localize_main_status(
            f"启动恢复扫描完成｜补挂/重锚 {restored}｜有效等待 {armed}｜异常 {errors}｜触发排队已扫描"))
    except Exception as exc:
        _text(HANDLES["status"], localize_main_status(f"启动恢复扫描异常｜{exc}"))


def _queue_order_notice(strategy: str, order_id: str, *, reason: str = "", mode: str = "Demo模拟",
                        result_text: str = "已提交") -> None:
    ORDER_NOTICE_QUEUE.append({
        "strategy": strategy,
        "order_id": order_id,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reason": reason,
        "mode": mode,
        "result_text": result_text,
    })
    user32.PostMessageW(HANDLES.get("main", 0), WM_ORDER_NOTICE, 0, 0)


def _queue_order_notices(strategy: str, order_ids: str | tuple[str, ...]) -> None:
    values = order_ids if isinstance(order_ids, tuple) else tuple(
        item.strip() for item in str(order_ids).split(",") if item.strip())
    for order_id in values:
        _queue_order_notice(strategy, order_id)


def _queue_confirmed_fill_notices(credentials: OkxCredentials, database: Path,
                                  instrument: str = "ETH-USDT-SWAP") -> int:
    """Queue one popup only after a known entry order appears in OKX Demo fills."""
    fills = OkxDemoClient(credentials).recent_fills(instrument, 100)
    store = StateStore(database)
    queued = 0
    try:
        for fill in fills:
            order_id = str(fill.get("ordId", "")).strip()
            filled_ms = int(fill.get("ts") or 0)
            if not order_id or filled_ms < ORDER_NOTICE_SESSION_START_MS:
                continue
            label = store.entry_order_label(order_id)
            if not label:
                continue
            filled_at = datetime.fromtimestamp(filled_ms / 1000, timezone.utc).isoformat()
            if store.claim_order_fill_notice(order_id, label, filled_at):
                _queue_order_notice(label, order_id)
                queued += 1
    finally:
        store.close()
    return queued


def _show_order_notice() -> None:
    notice = ORDER_NOTICE_QUEUE.pop(0) if ORDER_NOTICE_QUEUE else {}
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", "交易订单提示", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        CW_USEDEFAULT, CW_USEDEFAULT, ORDER_NOTICE_WIDTH, ORDER_NOTICE_HEIGHT,
        HANDLES.get("main", 0), None,
        kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["order_notice_window"] = hwnd
    detail = _live_message_zh(notice.get('reason', '-')).replace("｜", "\r\n")
    _create(hwnd, "EDIT",
            f"订单结果：{notice.get('result_text', '已提交')}\n方向与策略：{_live_message_zh(notice.get('strategy', '-'))}\n"
            f"提交时间：{notice.get('time', '-')}\nOKX订单号：{notice.get('order_id', '-')}\n"
            f"运行模式：{notice.get('mode', '-')}\n\n"
            f"触发、位置、止损与止盈说明：\n{detail}",
            WS_BORDER | WS_VSCROLL | ES_MULTILINE | ES_AUTOVSCROLL | ES_READONLY,
            24, 24, 500, 322)
    _create(hwnd, "BUTTON", "打开官方1分钟K线", WS_TABSTOP | BS_PUSHBUTTON,
            24, 370, 190, 38, ID_NOTICE_CHART)
    _create(hwnd, "BUTTON", "确定", WS_TABSTOP | BS_PUSHBUTTON,
            390, 370, 120, 38, ID_NOTICE_CLOSE)


def _main_trade_panel_worker() -> None:
    """Refresh the embedded order/lifecycle panel; order events never open a window."""
    lifecycle_rows = []
    for slot in LIVE_SLOT_ORDER:
        store = StateStore(LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3")
        try:
            rows = store.connection.execute(
                "SELECT * FROM trade_lifecycle ORDER BY signal_time DESC").fetchall()
            for row in rows:
                lifecycle_rows.append((str(row["signal_time"]), LIVE_PROFILES[slot]["label"], row))
        finally:
            store.close()
    lifecycle_rows.sort(key=lambda item: item[0], reverse=True)
    table_rows = []
    for _, profile, row in lifecycle_rows:
        try:
            context = json.loads(str(row["signal_context_json"] or "{}"))
        except json.JSONDecodeError:
            context = {}
        direction = "做多" if int(row["direction"]) > 0 else "做空"
        identity = str(context.get("entry_classification_label") or row["branch"] or "未分类")
        stage = str(context.get("winning_trigger_template") or context.get("external_signal_snapshot", {}).get("structure") or row["branch"])
        exit_tf = str(context.get("ma5_exit_timeframe") or "1m")
        take_profit_rule = str(context.get("take_profit_rule") or
                               (f"{exit_tf} MA5拐弯止盈" if row["trailing_activation"] is not None
                                else "固定止盈/服务器保护"))
        if row["trailing_activation"] is not None and "启动=" not in take_profit_rule:
            take_profit_rule += f"；启动={float(row['trailing_activation']):.2f}"
        result = (f"{row['status']}｜{row['exit_reason'] or '持仓及保护管理中'}"
                  + (f"｜净={float(row['net_pnl']):+.4f}U" if row["net_pnl"] is not None else ""))
        table_rows.append((str(row["signal_time"])[:19].replace("T", " "), profile, direction,
                           identity, stage, f"{float(row['entry_reference'] or 0):.2f}",
                           f"{float(row['stop_price'] or 0):.2f}", take_profit_rule,
                           str(row["order_id"] or "等待回执"), result))
    _listview_rows(HANDLES.get("main_lifecycle_table", 0), table_rows)
    dashboard_rows = []
    for item, table_row in zip(lifecycle_rows, table_rows):
        try:
            context = json.loads(str(item[2]["signal_context_json"] or "{}"))
        except json.JSONDecodeError:
            context = {}
        rule_audit = context.get("entry_rule_audit") or {}
        dashboard_rows.append((*table_row[:5], str(rule_audit.get("summary") or "旧版未冻结核对"), *table_row[5:]))
    _listview_rows(HANDLES.get("lifecycle_dashboard_orders", 0), dashboard_rows)

    latest_context = {}
    latest_direction = 1
    if lifecycle_rows:
        try:
            latest_context = json.loads(str(lifecycle_rows[0][2]["signal_context_json"] or "{}"))
            latest_direction = int(lifecycle_rows[0][2]["direction"])
        except (json.JSONDecodeError, ValueError):
            pass
    audit = latest_context.get("reversal_three_stage_audit") or {}
    conditions = audit.get("conditions") or []
    if not conditions:
        names = ("1m价格进入MA5内侧", "1m MA5走平并向反转方向拐弯",
                 "最近3根实体形成12根局部底/顶", "1m位于三均线发散末端",
                 "1m反转前MA5/MA10/MA20排列", "5m反向实体位于MA5与MA20外侧",
                 "5m相邻反向实体覆盖至少45%")
        conditions = ({"name": name, "matched": None, "evidence": "等待下一笔订单冻结盘面证据"}
                      for name in names)
    condition_rows = []
    for item in conditions:
        matched = item.get("matched")
        state = "符合" if matched is True else "不符合" if matched is False else "待形成"
        condition_rows.append(("底部反转做多" if latest_direction > 0 else "顶部反转做空",
                               str(item.get("name", "-")), state, str(item.get("evidence", "-"))))
    _listview_rows(HANDLES.get("main_condition_table", 0), condition_rows)
    for direction, key, template in ((1, "lifecycle_dashboard_long", LONG_CYCLE_ROWS),
                                     (-1, "lifecycle_dashboard_short", SHORT_CYCLE_ROWS)):
        matching = next((item for item in lifecycle_rows if int(item[2]["direction"]) == direction), None)
        frozen = {}
        if matching:
            try:
                frozen = json.loads(str(matching[2]["signal_context_json"] or "{}"))
            except json.JSONDecodeError:
                pass
        rule = frozen.get("entry_rule_audit") or {}
        checks = list(rule.get("three_stage_conditions") or [])
        cycle_rows = []
        for period, requirement, protection in template:
            related = [check for check in checks if str(check.get("name", "")).startswith(period.replace("分钟", "m").replace("小时", "h"))]
            matched = all(check.get("matched") is True for check in related) if related else None
            state = "符合" if matched is True else "不符合" if matched is False else "待冻结证据"
            cycle_rows.append((period, requirement, state, protection,
                               "；".join(str(check.get("evidence") or "-") for check in related)
                               or "本笔无该周期逐项证据"))
        _listview_rows(HANDLES.get(key, 0), cycle_rows)
    _text(HANDLES.get("lifecycle_dashboard_status", 0),
          f"做多/做空条件与订单核对｜生命周期 {len(lifecycle_rows)} 笔｜最新在前｜旧版无冻结证据明确标注")
    _text(HANDLES.get("main_trade_panel_status", 0),
          f"最近订单核对：{audit.get('summary', '等待订单快照')}｜生命周期 {len(lifecycle_rows)} 笔；详情见顶部菜单")


def _listview(parent: int, columns: tuple[tuple[str, int], ...], x: int, y: int, width: int, height: int) -> int:
    handle = _create(parent, "SysListView32", "", WS_BORDER | WS_TABSTOP | WS_VSCROLL | WS_HSCROLL |
                     LVS_REPORT | LVS_SHOWSELALWAYS, x, y, width, height)
    user32.SendMessageW(handle, LVM_SETEXTENDEDLISTVIEWSTYLE, 0,
                        LVS_EX_GRIDLINES | LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER)
    for index, (title, column_width) in enumerate(columns):
        buffer = ctypes.create_unicode_buffer(title)
        column = LVCOLUMNW(mask=LVCF_FMT | LVCF_WIDTH | LVCF_TEXT, fmt=0, cx=column_width,
                           pszText=ctypes.cast(buffer, wintypes.LPWSTR), iSubItem=index)
        user32.SendMessageW(handle, LVM_INSERTCOLUMNW, index, ctypes.addressof(column))
    return handle


def _listview_rows(handle: int, rows: list[tuple[str, ...]]) -> None:
    if not handle or not user32.IsWindow(handle):
        return
    user32.SendMessageW(handle, LVM_DELETEALLITEMS, 0, 0)
    for row_index, row in enumerate(rows):
        first_buffer = ctypes.create_unicode_buffer(str(row[0]))
        item = LVITEMW(mask=LVIF_TEXT, iItem=row_index, iSubItem=0,
                       pszText=ctypes.cast(first_buffer, wintypes.LPWSTR))
        user32.SendMessageW(handle, LVM_INSERTITEMW, 0, ctypes.addressof(item))
        for column_index, value in enumerate(row[1:], start=1):
            buffer = ctypes.create_unicode_buffer(str(value))
            subitem = LVITEMW(iItem=row_index, iSubItem=column_index,
                              pszText=ctypes.cast(buffer, wintypes.LPWSTR))
            user32.SendMessageW(handle, LVM_SETITEMTEXTW, row_index, ctypes.addressof(subitem))


def _listview_text(handle: int, item: int, column: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    item_data = LVITEMW(iSubItem=column, pszText=ctypes.cast(buffer, wintypes.LPWSTR), cchTextMax=511)
    user32.SendMessageW(handle, LVM_GETITEMTEXTW, item, ctypes.addressof(item_data))
    return buffer.value


def _apply_recovery_pool_time_sort() -> None:
    """Sort visible rows by time or structure's realized local net PnL."""
    table = HANDLES.get("recovery_pool_table", 0)
    if not table or not user32.IsWindow(table):
        return
    raw_rows = list(HANDLES.get("recovery_pool_raw_rows", []))
    timestamps = list(HANDLES.get("recovery_pool_timestamps", []))
    column = int(HANDLES.get("recovery_pool_sort_column", 0))
    if column == 3:
        rows = sort_recovery_pool_by_structure_profit(
            raw_rows, timestamps,
            highest_first=bool(HANDLES.get("recovery_pool_structure_highest_first", True)))
    else:
        rows = sort_recovery_pool_rows(raw_rows, timestamps, column=column)
    _listview_rows(table, rows)


def _condition_history_rows(rows: list[tuple[str, str, str, str, str]],
                            reason_width: int = 74) -> list[tuple[str, ...]]:
    """Wrap long evidence into continuation rows without truncating its meaning."""
    rendered = []
    for when, structure, result, reason, order_id in rows:
        chunks = [reason[index:index + reason_width]
                  for index in range(0, max(len(reason), 1), reason_width)][:3] or ["-"]
        rendered.append((when, structure, result, chunks[0], order_id))
        rendered.extend(("", "", "", chunk, "") for chunk in chunks[1:])
    return rendered


def _latest_condition_text(row: tuple[str, str, str, str, str] | None) -> str:
    if row is None:
        return "最新一条（每5秒刷新）：等待事件"
    when, structure, result, reason, order_id = row
    chunks = [reason[index:index + 86] for index in range(0, max(len(reason), 1), 86)][:3]
    return (f"最新一条（每5秒刷新）｜{when}｜{structure}｜{result}｜订单号：{order_id}\r\n" +
            "\r\n".join(chunks))


def _pnl_text(value: object, *, floating: bool = False) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    label = "浮盈" if floating else "盈利"
    if amount < 0:
        label = "浮亏" if floating else "亏损"
    if amount == 0:
        return "— 0.000000 USDT"
    return f"{'▲' if amount > 0 else '▼'} {label} {amount:+.6f} USDT"


def _show_strategy_rules() -> None:
    existing = HANDLES.get("strategy_rules_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"旧版规则总表（保留）｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 720,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["strategy_rules_window"] = hwnd
    HANDLES["strategy_rules_note"] = _create(
        hwnd, "STATIC", "旧版表仅为过渡期查询，不再作为新规则解释入口；稳定后再删除。",
        SS_LEFT, 18, 14, 1200, 28,
    )
    columns = (("策略", 205), ("策略版本", 220), ("规则分类", 125), ("详细规则（原有与新增均完整保留）", 1120))
    HANDLES["strategy_rules_table"] = _listview(hwnd, columns, 18, 48, 1220, 560)
    labels = (("trigger", "触发下单"), ("take_profit", "止盈"),
              ("stop_loss", "止损"), ("limits", "限制/安全"))
    labels = (labels[0], ("continuation", "趋势追单"), *labels[1:])
    rows = [
        ("LIVE实盘｜激进型", "live-aggressive-auto-v2", "触发下单",
         "五分钟是唯一开仓主触发：已收盘结构确认，或前序已收盘K线已建立顶底证据后，当前五分钟盘中穿越动态MA5/MA20快触发；一分钟只做同步确认和精确择时。下降趋势反抽MA20转弱做空，上涨趋势回踩MA5/MA10守住后重新转强做多；底部抬高低点上穿MA5且一分钟持续站稳MA20也可早多。"),
        ("LIVE实盘｜三账户", "live-five-minute-primary-v2", "数据复盘",
         f"以{NETWORK_STABLE_CUTOFF_LOCAL}为网络稳定分界。分界前成交、亏损与共享实验只作低可信历史归档，不直接推动参数升级；分界后完整生命周期和新实验样本独立累计。"),
        ("LIVE实盘｜激进型", "live-aggressive-auto-v1", "限制/安全",
         "独立子账户；自动总闸单独启停；每单0.05张；单笔计划亏损≤0.20U；每日净亏损≤5U；每日成功交易≤100次；候选不计数；无时间冷却；停止或熔断撤销本策略预埋开仓单。"),
        ("LIVE实盘｜保守型", "live-manual-minimum-v1", "触发下单",
         "以5分钟为唯一主触发：既支持收盘确认，也支持顶部/底部证据已建立后的盘中MA5穿越快触发；1分钟只同步验证。15分钟/1小时按方案C只作背景，4小时只作参考，30分钟已移除。"),
        ("LIVE实盘｜保守型", "live-manual-minimum-v1", "限制/安全",
         "独立子账户；每单0.05张；单笔计划亏损≤0.15U；每日净亏损≤5U；每日成功交易≤100次；候选不计数；冷却5分钟；已有持仓或挂单拒绝新单。"),
        ("LIVE实盘｜稳妥型", "live-manual-minimum-v1", "触发下单",
         "以5分钟为唯一主触发：既支持收盘确认，也支持顶部/底部证据已建立后的盘中MA5穿越快触发；1分钟只同步验证。15分钟/1小时按方案C只作背景，4小时只作参考，30分钟已移除。"),
        ("LIVE实盘｜稳妥型", "live-manual-minimum-v1", "限制/安全",
         "独立子账户；每单0.05张；单笔计划亏损≤0.10U；每日净亏损≤5U；每日成功交易≤100次；候选不计数；冷却5分钟；已有持仓或挂单拒绝新单。"),
    ]
    rows.extend(
        ("策略01｜当前核心规则", STRATEGY_RULES[0]["version"], category, detail)
        for category, detail in CURRENT_STRATEGY_RULE_SUMMARY
    )
    for rule in STRATEGY_RULES:
        for key, label in labels:
            for detail in str(rule[key]).splitlines():
                clean = detail.strip()
                # Version-prefixed lines are release history.  They used to be
                # appended to the current-rule table and made it look corrupted.
                if clean and not (clean.startswith("[v0.") or clean.startswith("【v0.")):
                    rows.append((rule["strategy"], rule["version"], label, clean))
    _listview_rows(HANDLES["strategy_rules_table"], rows)
    HANDLES["strategy_rules_close"] = _create(hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON,
                                               1118, 620, 120, 36, ID_RULES_CLOSE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def _show_simple_rules() -> None:
    existing = HANDLES.get("simple_rules_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE); user32.SetForegroundWindow(existing); return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"新版简明执行规则｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 720,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None)
    HANDLES["simple_rules_window"] = hwnd
    HANDLES["simple_rules_note"] = _create(
        hwnd, "STATIC", "本表是新的唯一执行解释表；旧表暂时保留但不与本表混排。",
        SS_LEFT, 18, 14, 1200, 28)
    HANDLES["simple_rules_table"] = _listview(
        hwnd, (("规则", 220), ("作用周期/条件", 240), ("唯一执行说明", 900)),
        18, 48, 1220, 560)
    _listview_rows(HANDLES["simple_rules_table"], SIMPLE_EXECUTION_RULES)
    HANDLES["simple_rules_close"] = _create(
        hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON, 1118, 620, 120, 36,
        ID_SIMPLE_RULES_CLOSE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def _show_trading_cycles() -> None:
    existing = HANDLES.get("trading_cycles_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"交易周期多空条件｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 720,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None)
    HANDLES["trading_cycles_window"] = hwnd
    columns = (("阶段/周期", 105), ("识别条件", 470), ("执行/保护", 330))
    titles, tables = [], []
    for index, (name, rows) in enumerate(CYCLE_RULE_SECTIONS):
        x = 18 if index % 2 == 0 else 630
        y = 12 + (index // 2) * 204
        titles.append(_create(hwnd, "STATIC", name, SS_LEFT, x, y, 580, 28))
        table = _listview(hwnd, columns, x, y + 30, 590, 170)
        _listview_rows(table, rows)
        tables.append(table)
    HANDLES["trading_cycles_titles"] = titles
    HANDLES["trading_cycles_tables"] = tables
    HANDLES["trading_cycles_long_table"] = tables[0]
    HANDLES["trading_cycles_short_table"] = tables[1]
    HANDLES["trading_cycles_close"] = _create(
        hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON, 1100, 630, 120, 36,
        ID_TRADING_CYCLES_CLOSE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def _show_lifecycle_dashboard() -> None:
    existing = HANDLES.get("lifecycle_dashboard_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        threading.Thread(target=_main_trade_panel_worker, daemon=True).start()
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"订单结构与生命周期对照｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 800,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None)
    HANDLES["lifecycle_dashboard_window"] = hwnd
    HANDLES["lifecycle_dashboard_status"] = _create(hwnd, "STATIC",
        "做多/做空条件与每笔订单核对正在载入……", SS_LEFT, 18, 10, 1190, 28)
    HANDLES["lifecycle_dashboard_long_title"] = _create(hwnd, "STATIC",
        "做多：底部反转 / 回踩追多", SS_LEFT, 18, 42, 580, 28)
    HANDLES["lifecycle_dashboard_short_title"] = _create(hwnd, "STATIC",
        "做空：顶部反转 / 反抽追空", SS_LEFT, 630, 42, 580, 28)
    columns = (("周期", 70), ("条件", 360), ("结果", 80), ("保护/身份", 220), ("冻结证据", 330))
    HANDLES["lifecycle_dashboard_long"] = _listview(hwnd, columns, 18, 72, 590, 260)
    HANDLES["lifecycle_dashboard_short"] = _listview(hwnd, columns, 630, 72, 590, 260)
    HANDLES["lifecycle_dashboard_orders_title"] = _create(hwnd, "STATIC",
        "每笔订单：触发结构 → 价格与保护 → 成交和平仓生命周期", SS_LEFT, 18, 345, 1120, 28)
    HANDLES["lifecycle_dashboard_orders"] = _listview(hwnd, (
        ("信号时间", 145), ("账户", 75), ("方向", 60), ("下单结构", 185),
        ("触发模板", 215), ("条件核对", 250), ("下单价", 85), ("止损价", 85),
        ("止盈规则", 210), ("OKX订单号", 170), ("生命周期/平仓", 260)),
        18, 376, 1202, 320)
    HANDLES["lifecycle_dashboard_refresh"] = _create(hwnd, "BUTTON", "刷新对照",
        WS_TABSTOP | BS_PUSHBUTTON, 18, 710, 140, 36, ID_LIFECYCLE_DASHBOARD_REFRESH)
    HANDLES["lifecycle_dashboard_close"] = _create(hwnd, "BUTTON", "关闭",
        WS_TABSTOP | BS_PUSHBUTTON, 1100, 710, 120, 36, ID_LIFECYCLE_DASHBOARD_CLOSE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    threading.Thread(target=_main_trade_panel_worker, daemon=True).start()


def _recovery_pool_structure_label(signal_id: str) -> str:
    if str(signal_id).startswith("manual:"):
        return "手工下单"
    """Render internal recovery-pool signal identifiers in Chinese."""
    raw = str(signal_id or "未知结构")
    kind, separator, detail = raw.partition(":")
    if kind == "extreme-rotation":
        extreme_kind = detail.split(":", 1)[0]
        return {
            "top-short": "顶部极值反手空单",
            "bottom-long": "底部极值反手多单",
        }.get(extreme_kind, "极值反手单")

    # Add-on prefixes describe the execution board, while the next field is
    # the human-facing strategy name. Keep unknown Chinese names intact.
    if kind in {"recovery-addon", "addon"}:
        strategy = detail.split(":", 1)[0]
        if strategy and not any("一" <= ch <= "龥" for ch in strategy):
            return {
                "trend-pullback-long": "上涨趋势回踩追多",
                "trend-throwback-short": "下跌趋势反抽追空",
                "local-reversal-long": "局部反转做多",
                "local-reversal-short": "局部反转做空",
            }.get(strategy, "解套利润池策略单" if kind == "recovery-addon" else "独立小单策略")
        return strategy or ("解套利润池策略单" if kind == "recovery-addon" else "独立小单策略")

    if kind == "base-rebuild-stage1":
        return "基础仓重建第一阶段"
    if kind == "base-pullback-stage2":
        return "基础仓回踩补齐第二阶段"
    if kind == "exit":
        return "平仓记录"
    label = detail if separator else kind
    return label if any("一" <= ch <= "龥" for ch in label) else "策略单"


RECOVERY_POOL_REFRESH_LOCK = threading.Lock()


def _request_recovery_pool_refresh() -> None:
    # Sorting and the refresh button share one reconciliation path. Avoid
    # concurrent SQLite/API refreshes when the user clicks repeatedly.
    if not RECOVERY_POOL_REFRESH_LOCK.acquire(blocking=False):
        return

    def run():
        try:
            _recovery_pool_worker()
        finally:
            RECOVERY_POOL_REFRESH_LOCK.release()

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        RECOVERY_POOL_REFRESH_LOCK.release()
        raise


def _recovery_pool_worker() -> None:
    ledger = None
    try:
        database = LIVE_WORKSPACE / "profiles" / "clone_research" / "strategy.sqlite3"
        ledger = Account05StateStore(database)
        stats_since = ledger.recovery_stats_since_utc()
        rows = [row for row in ledger.recovery_lot_rows(500, stats_since)
                if not str(row["signal_id"] or "").startswith("manual:")]

        # Backfill older locally managed exits from read-only OKX order
        # details.  This does not submit, amend, cancel, or reduce anything.
        credentials = LIVE_SESSION_CREDENTIALS.get("clone_research")
        client = Account05LiveClient(credentials, timeout=20) if credentials else None
        if client is not None:
            # Import manual fills on every pool refresh as well as from the
            # automatic worker. This covers fills made while automation is
            # stopped and newly filled orders not yet in the archive feed.
            try:
                import_manual_orders(
                    client, ledger,
                    profit_points=Decimal(
                        LiveAccountSettings(
                            LIVE_WORKSPACE / "profiles" / "clone_research" / "state.sqlite3"
                        ).addon_take_profit_points()))
                rows = [row for row in ledger.recovery_lot_rows(500, stats_since)
                        if not str(row["signal_id"] or "").startswith("manual:")]
            except Exception:
                pass
            for row in rows:
                if str(row["kind"] or "") == "base":
                    continue
                if str(row["status"]) != "closed" or str(row["exit_price"] or ""):
                    continue
                order_id = str(row["exit_exchange_order_id"] or "")
                if not order_id:
                    continue
                try:
                    order = client.order(ACCOUNT05_INSTRUMENT, order_id=order_id)
                    exit_price = Decimal(str(order.get("avgPx") or order.get("fillPx") or "0"))
                    if exit_price <= 0:
                        continue
                    entry_price = Decimal(str(row["entry_price"]))
                    size = Decimal(str(row["original_size"]))
                    direction = Decimal("1") if str(row["side"]) == "long" else Decimal("-1")
                    gross = direction * (exit_price - entry_price) * size * Decimal("0.1")
                    fee_estimate = ((entry_price + exit_price) * size * Decimal("0.1")
                                    * Decimal("0.0005"))
                    def optional_decimal(*names):
                        for name in names:
                            value = str(order.get(name) or "").strip()
                            if value:
                                try:
                                    return Decimal(value)
                                except Exception:
                                    pass
                        return None
                    ledger.record_addon_exit_accounting(
                        str(row["lot_id"]), exit_order_id=order_id,
                        exit_price=exit_price, local_gross_pnl=gross,
                        local_fee_estimate=fee_estimate,
                        local_net_pnl=gross - fee_estimate,
                        exchange_realized_pnl=optional_decimal("pnl", "fillPnl", "realizedPnl"),
                        exchange_fee=optional_decimal("fee", "fillFee"))
                except Exception:
                    continue
            rows = [row for row in ledger.recovery_lot_rows(500, stats_since)
                    if not str(row["signal_id"] or "").startswith("manual:")]

        table_rows = []
        time_keys = []
        local_profit = Decimal("0")
        local_loss = Decimal("0")
        exchange_total = Decimal("0")
        for row in rows:
            # Base rows are shown for auditability, but remain outside the
            # addon recovery-pool accounting and 36-slot occupancy totals.
            is_base = str(row["kind"] or "") == "base"
            local_text = str(row["local_net_pnl"] or "")
            exchange_pnl_text = str(row["exchange_realized_pnl"] or "")
            exchange_fee_text = str(row["exchange_fee"] or "")
            if local_text and not is_base:
                local_value = Decimal(local_text)
                if local_value >= 0:
                    local_profit += local_value
                else:
                    local_loss += abs(local_value)
            if exchange_pnl_text and not is_base:
                exchange_total += Decimal(exchange_pnl_text)
            if exchange_fee_text and not is_base:
                exchange_total += Decimal(exchange_fee_text)
            created = str(row["created_at_utc"] or "")
            closed = str(row["closed_at_utc"] or "")
            def beijing(value: str) -> str:
                if not value:
                    return "—"
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
            side = "多" if str(row["side"]) == "long" else "空"
            status = "持仓中" if str(row["status"]) != "closed" else "已释放"
            local_display = f"{Decimal(local_text):+.6f}" if local_text else "待平仓"
            exchange_display = "—"
            if exchange_pnl_text or exchange_fee_text:
                exchange_display = f"{Decimal(exchange_pnl_text or '0') + Decimal(exchange_fee_text or '0'):+.6f}"
            comparison = "—"
            if local_text and exchange_display != "—":
                comparison = f"{Decimal(local_text) - (Decimal(exchange_pnl_text or '0') + Decimal(exchange_fee_text or '0')):+.6f}"
            raw_signal = str(row["signal_id"] or "未知结构")
            structure = _recovery_pool_structure_label(raw_signal)
            table_rows.append((
                beijing(created), beijing(closed), side, structure,
                f"{Decimal(str(row['original_size'])):.2f}",
                f"{Decimal(str(row['entry_price'])):.2f}",
                f"{Decimal(str(row['exit_price'])):.2f}" if str(row["exit_price"] or "") else "—",
                local_display, exchange_display, comparison, status,
                str(row["entry_order_id"]),
                str(row["exit_order_id"] or row["exit_exchange_order_id"] or "—"),
                str(row["exit_reason"] or "—")))
            time_keys.append((created, closed))
        pool = ledger.recovery_pool()
        settings = LiveAccountSettings(LIVE_WORKSPACE / "profiles" / "clone_research" / "state.sqlite3")
        long_capacity, short_capacity = settings.long_slot_capacity(), settings.short_slot_capacity()
        open_long = sum(str(row["kind"] or "") != "base" and str(row["side"]) == "long" and str(row["status"]) != "closed" for row in rows)
        open_short = sum(str(row["kind"] or "") != "base" and str(row["side"]) == "short" and str(row["status"]) != "closed" for row in rows)
        local_net = local_profit - local_loss
        HANDLES["recovery_pool_raw_rows"] = table_rows
        HANDLES["recovery_pool_timestamps"] = time_keys
        _apply_recovery_pool_time_sort()
        since_text = "历史全量" if not stats_since else beijing(stats_since) + " 起"
        _text(HANDLES.get("recovery_pool_status", 0),
              f"独立小单账本（普通槽位每笔0.02张；极值反手按独立规则）｜统计起点：{since_text}｜占用：多{open_long}/{long_capacity}、空{open_short}/{short_capacity}｜"
              f"本地盈利 {local_profit:.6f}U－本地亏损 {local_loss:.6f}U＝净额 {local_net:+.6f}U｜"
              f"欧易部分平仓净记录 {exchange_total:+.6f}U｜差异 {local_net-exchange_total:+.6f}U｜"
              f"解套池 {pool['pool_balance']:+.6f}U（允许负数）＋20%储备 "
              f"{pool['reserve_balance']:.6f}U")
    except Exception as exc:
        _text(HANDLES.get("recovery_pool_status", 0), f"解套利润池读取失败：{exc}")
    finally:
        if ledger is not None:
            ledger.close()


def _show_recovery_pool() -> None:
    existing = HANDLES.get("recovery_pool_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        _request_recovery_pool_refresh()
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"解套利润池｜72个循环槽位｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1500, 800,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None)
    HANDLES["recovery_pool_window"] = hwnd
    HANDLES["recovery_pool_status"] = _create(
        hwnd, "STATIC", "正在读取本地虚拟小单与欧易部分平仓对照……",
        SS_LEFT, 18, 12, 1440, 50)
    HANDLES["recovery_pool_table"] = _listview(hwnd, (
        ("开仓时间（点击排序）", 150), ("平仓时间（点击排序）", 150),
        ("方向", 45), ("结构（点击按累计净收益排序）", 270), ("张数", 55),
        ("开仓价", 78), ("平仓价", 78), ("本地净盈亏U", 100),
        ("欧易平仓净额U", 110), ("口径差异U", 95), ("槽位", 65),
        ("开仓订单", 150), ("平仓订单", 150), ("退出原因", 420)),
        18, 68, 1440, 590)
    HANDLES["recovery_pool_sort_column"] = 0
    HANDLES["recovery_pool_structure_highest_first"] = False
    HANDLES["recovery_pool_raw_rows"] = []
    HANDLES["recovery_pool_timestamps"] = []
    HANDLES["recovery_pool_refresh"] = _create(
        hwnd, "BUTTON", "刷新对照", WS_TABSTOP | BS_PUSHBUTTON,
        18, 675, 140, 36, ID_RECOVERY_POOL_REFRESH)
    HANDLES["recovery_pool_delete"] = _create(
        hwnd, "BUTTON", "删除选中记录", WS_TABSTOP | BS_PUSHBUTTON,
        370, 675, 150, 36, ID_RECOVERY_POOL_DELETE)
    HANDLES["manual_orders_button"] = _create(
        hwnd, "BUTTON", "手工订单列表", WS_TABSTOP | BS_PUSHBUTTON,
        530, 675, 150, 36, ID_MANUAL_ORDERS)
    HANDLES["recovery_pool_reset"] = _create(
        hwnd, "BUTTON", "盈亏与池余额归零", WS_TABSTOP | BS_PUSHBUTTON,
        180, 675, 190, 36, ID_RECOVERY_POOL_RESET)
    HANDLES["recovery_pool_close"] = _create(
        hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON,
        1338, 675, 120, 36, ID_RECOVERY_POOL_CLOSE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    _request_recovery_pool_refresh()


def _manual_order_close_links() -> dict[str, str]:
    database = LIVE_WORKSPACE / "profiles" / "clone_research" / "strategy.sqlite3"
    if not database.exists():
        return {}
    ledger = Account05StateStore(database)
    try:
        return manual_exit_owners(ledger)
    finally:
        ledger.close()


def _manual_orders_worker() -> None:
    try:
        credentials = LIVE_SESSION_CREDENTIALS.get("clone_research")
        if credentials is None:
            raise OkxError("账户05尚未绑定API")
        api_client = Account05LiveClient(credentials, timeout=20)
        orders, fills, warning = load_manual_order_history(api_client, ACCOUNT05_INSTRUMENT)
        table = manual_order_rows(orders, fills, limit=20, exit_owners=_manual_order_close_links())
        HANDLES["manual_orders_raw_rows"] = table
        HANDLES["manual_orders_sort_column"] = int(HANDLES.get("manual_orders_sort_column", 0))
        _apply_manual_orders_sort()
        status = f"最近手工成交{len(table)}条（最多20条）｜平仓优先账本关联，其余按方向/数量/时间FIFO匹配｜不参与自动下单"
        if warning:
            status += f"｜历史不完整：{warning}"
        _text(HANDLES.get("manual_orders_status", 0), status)
    except Exception as exc:
        _text(HANDLES.get("manual_orders_status", 0), f"手工订单读取失败（保留上次结果）：{exc}")


def _show_manual_orders() -> None:
    existing = HANDLES.get("manual_orders_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing); threading.Thread(target=_manual_orders_worker, daemon=True).start(); return
    hwnd = user32.CreateWindowExW(0, "CodexQuantBotWindow", f"账户05手工订单列表｜v{APP_VERSION}",
                                  WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1100, 650,
                                  HANDLES.get("recovery_pool_window", HANDLES.get("main", 0)), None,
                                  kernel32.GetModuleHandleW(None), None)
    HANDLES["manual_orders_window"] = hwnd
    HANDLES["manual_orders_status"] = _create(hwnd, "STATIC", "正在读取手工订单……", SS_LEFT, 18, 12, 1040, 32)
    HANDLES["manual_orders_table"] = _listview(hwnd, (("开仓时间", 170), ("平仓时间", 170), ("类型", 100), ("方向", 60), ("订单类型", 100), ("成交数量", 95), ("开仓价", 100), ("平仓价", 100), ("开仓订单", 210), ("平仓订单", 320), ("状态", 240)), 18, 55, 1040, 500)
    HANDLES["manual_orders_refresh"] = _create(hwnd, "BUTTON", "刷新", WS_TABSTOP | BS_PUSHBUTTON, 18, 570, 120, 34, ID_MANUAL_ORDERS_REFRESH)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    threading.Thread(target=_manual_orders_worker, daemon=True).start()


def _layout_manual_orders(client_width: int, client_height: int) -> None:
    content_width = max(600, client_width - 36)
    button_y = max(18, client_height - 48)
    user32.MoveWindow(HANDLES.get("manual_orders_status", 0), 18, 12,
                      content_width, 32, True)
    user32.MoveWindow(HANDLES.get("manual_orders_table", 0), 18, 55,
                      content_width, max(180, client_height - 120), True)
    user32.MoveWindow(HANDLES.get("manual_orders_refresh", 0), 18,
                      button_y, 120, 34, True)


def _apply_manual_orders_sort() -> None:
    table = HANDLES.get("manual_orders_table", 0)
    rows = list(HANDLES.get("manual_orders_raw_rows", []))
    if not rows:
        _listview_rows(table, [])
        return
    column = int(HANDLES.get("manual_orders_sort_column", 0))
    # ISO-formatted display timestamps sort lexicographically by time.
    rows.sort(key=lambda row: str(row[column] or ""), reverse=bool(HANDLES.get("manual_orders_sort_desc", True)))
    _listview_rows(table, rows)


def _layout_recovery_pool(client_width: int, client_height: int) -> None:
    content_width = max(600, client_width - 36)
    button_y = max(18, client_height - 48)
    user32.MoveWindow(HANDLES.get("recovery_pool_status", 0), 18, 12,
                      content_width, 50, True)
    user32.MoveWindow(HANDLES.get("recovery_pool_table", 0), 18, 68,
                      content_width, max(180, client_height - 128), True)
    user32.MoveWindow(HANDLES.get("recovery_pool_refresh", 0), 18,
                      button_y, 140, 34, True)
    user32.MoveWindow(HANDLES.get("recovery_pool_reset", 0), 170,
                      button_y, 190, 34, True)
    user32.MoveWindow(HANDLES.get("recovery_pool_delete", 0), 370,
                      button_y, 150, 34, True)
    user32.MoveWindow(HANDLES.get("manual_orders_button", 0), 530,
                      button_y, 150, 34, True)
    user32.MoveWindow(HANDLES.get("recovery_pool_close", 0),
                      max(18, client_width - 138), button_y, 120, 34, True)


def _fixed_addon_orders_worker() -> None:
    ledger = None
    try:
        database = LIVE_WORKSPACE / "profiles" / "clone_research" / "strategy.sqlite3"
        ledger = Account05StateStore(database)
        rows = ledger.fixed_addon_lot_rows()
        table_rows = []
        open_count = 0
        closed_count = 0
        total_fee = Decimal("0")
        total_net = Decimal("0")

        def beijing(value: str) -> str:
            if not value:
                return "—"
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")

        for row in rows:
            signal_key = str(row["signal_id"])
            signal_key = signal_key[:-3] if signal_key.endswith("-T1") else signal_key
            review_source, review_detail = review_summary(ledger.entry_review(signal_key))
            side_value = str(row["side"])
            side = "多" if side_value == "long" else "空"
            signal_id = str(row["signal_id"] or "")
            identity = signal_id
            if signal_id.startswith("addon:"):
                identity = signal_id[len("addon:"):].split(f":{side_value}:", 1)[0]
            is_closed = str(row["status"]) == "closed"
            open_count += int(not is_closed)
            closed_count += int(is_closed)
            fee_text = str(row["local_fee_estimate"] or "")
            net_text = str(row["local_net_pnl"] or "")
            if fee_text:
                total_fee += Decimal(fee_text)
            if net_text:
                total_net += Decimal(net_text)
            table_rows.append((
                beijing(str(row["created_at_utc"] or "")), side, identity,
                f"{Decimal(str(row['original_size'])):.2f}",
                f"{Decimal(str(row['entry_price'])):.2f}",
                f"{Decimal(str(row['exit_price'])):.2f}" if str(row["exit_price"] or "") else "—",
                f"{Decimal(fee_text):.6f}" if fee_text else "—",
                f"{Decimal(net_text):+.6f}" if net_text else "—",
                "已释放" if is_closed else "持仓中",
                str(row["entry_order_id"] or row["entry_exchange_order_id"] or "—"),
                str(row["exit_order_id"] or row["exit_exchange_order_id"] or "—"),
                beijing(str(row["closed_at_utc"] or "")),
                str(row["exit_reason"] or "—"), review_source, review_detail))
        _listview_rows(HANDLES.get("fixed_addon_orders_table", 0), table_rows)
        _text(HANDLES.get("fixed_addon_orders_status", 0),
              f"账户05独立0.01张小单｜共{len(rows)}笔｜持仓中{open_count}笔｜"
              f"已释放{closed_count}笔｜本地估算手续费 {total_fee:.6f}U｜"
              f"本地净盈亏 {total_net:+.6f}U")
    except Exception as exc:
        _text(HANDLES.get("fixed_addon_orders_status", 0), f"0.01张小单记录读取失败：{exc}")
    finally:
        if ledger is not None:
            ledger.close()


def _show_fixed_addon_orders() -> None:
    existing = HANDLES.get("fixed_addon_orders_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        threading.Thread(target=_fixed_addon_orders_worker, daemon=True).start()
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"账户05独立0.01张小单列表｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1500, 800,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None)
    HANDLES["fixed_addon_orders_window"] = hwnd
    HANDLES["fixed_addon_orders_status"] = _create(
        hwnd, "STATIC", "正在读取账户05固定0.01张小单记录……",
        SS_LEFT, 18, 12, 1440, 50)
    HANDLES["fixed_addon_orders_table"] = _listview(hwnd, (
        ("开仓时间", 115), ("方向", 45), ("六类触发身份", 190), ("张数", 55),
        ("开仓价", 78), ("平仓价", 78), ("估算手续费U", 100),
        ("本地净盈亏U", 105), ("状态", 65), ("开仓订单", 165),
        ("平仓订单", 165), ("平仓时间", 115), ("退出原因", 390),
        ("入场审计", 230), ("六类规则与下单依据", 1200)),
        18, 68, 1440, 590)
    HANDLES["fixed_addon_orders_refresh"] = _create(
        hwnd, "BUTTON", "刷新记录", WS_TABSTOP | BS_PUSHBUTTON,
        18, 675, 140, 36, ID_FIXED_ADDON_REFRESH)
    HANDLES["fixed_addon_orders_close"] = _create(
        hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON,
        1338, 675, 120, 36, ID_FIXED_ADDON_CLOSE)
    user32.SetTimer(hwnd, 505, 5000, None)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    threading.Thread(target=_fixed_addon_orders_worker, daemon=True).start()


def _layout_fixed_addon_orders(client_width: int, client_height: int) -> None:
    content_width = max(600, client_width - 36)
    button_y = max(18, client_height - 48)
    user32.MoveWindow(HANDLES.get("fixed_addon_orders_status", 0), 18, 12,
                      content_width, 50, True)
    user32.MoveWindow(HANDLES.get("fixed_addon_orders_table", 0), 18, 68,
                      content_width, max(180, client_height - 128), True)
    user32.MoveWindow(HANDLES.get("fixed_addon_orders_refresh", 0), 18,
                      button_y, 140, 34, True)
    user32.MoveWindow(HANDLES.get("fixed_addon_orders_close", 0),
                      max(18, client_width - 138), button_y, 120, 34, True)


def _ma_endpoints_worker() -> None:
    try:
        database = LIVE_WORKSPACE / "profiles" / "aggressive" / "strategy.sqlite3"
        rows = read_ma_endpoint_lineage(database, 300)
        _listview_rows(HANDLES.get("ma_endpoints_table", 0),
                       [ma_endpoint_table_row(item) for item in rows])
        paired = sum(str(item.get("pair_status")) == "paired" for item in rows)
        _text(HANDLES.get("ma_endpoints_status", 0),
              f"四级三均线末端 {len(rows)} 条｜已配对 {paired}｜未配对 {len(rows)-paired}｜未配对保留为局部末端/上级趋势触发点")
    except Exception as exc:
        _text(HANDLES.get("ma_endpoints_status", 0), f"末端谱系读取失败：{exc}")


def _show_ma_endpoints() -> None:
    existing = HANDLES.get("ma_endpoints_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE); user32.SetForegroundWindow(existing); _ma_endpoints_worker(); return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"四级三均线末端配对表｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1500, 760,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None)
    HANDLES["ma_endpoints_window"] = hwnd
    HANDLES["ma_endpoints_status"] = _create(hwnd, "STATIC", "正在读取末端谱系……", SS_LEFT, 18, 14, 1400, 28)
    HANDLES["ma_endpoints_table"] = _listview(hwnd, (
        ("区域起点", 120), ("周期", 60), ("最新极值时间", 120), ("方向", 90),
        ("末端价", 85), ("极值", 85), ("MA5", 75), ("MA10", 75), ("MA20", 75),
        ("半覆盖", 70), ("穿MA20", 75), ("穿MA5", 70), ("配对状态", 130), ("上级", 60),
        ("上级时间", 120), ("结构身份/趋势解释", 430), ("已升级订单", 200)),
        18, 48, 1440, 560)
    HANDLES["ma_endpoints_refresh"] = _create(hwnd, "BUTTON", "刷新", WS_TABSTOP | BS_PUSHBUTTON, 18, 620, 120, 36, ID_MA_ENDPOINTS_REFRESH)
    HANDLES["ma_endpoints_close"] = _create(hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON, 1320, 620, 120, 36, ID_MA_ENDPOINTS_CLOSE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE); _ma_endpoints_worker()


def _refresh_ma_experiment() -> None:
    store = StateStore(WORKSPACE / "live" / "state.sqlite3")
    try:
        historical_stats = store.ma_deviation_statistics(before=NETWORK_STABLE_CUTOFF_UTC)
        stats = store.ma_deviation_statistics(since=NETWORK_STABLE_CUTOFF_UTC)
        recent = store.recent_ma_deviation_touches(500, since=NETWORK_STABLE_CUTOFF_UTC)
        demo_stats = store.ma_experiment_demo_statistics()
    finally:
        store.close()
    total = sum(int(row["touches"] or 0) for row in stats)
    reverted = sum(int(row["reverted"] or 0) for row in stats)
    pending = sum(int(row["pending"] or 0) for row in stats)
    timed_out = sum(int(row["timed_out"] or 0) for row in stats)
    rate = (100.0 * reverted / max(1, reverted + timed_out))
    old_total = sum(int(row["touches"] or 0) for row in historical_stats)
    _text(HANDLES.get("ma_experiment_status", 0),
          f"稳定网络新阶段（{NETWORK_STABLE_CUTOFF_LOCAL}起）｜样本 {total}｜回归 {reverted}｜观察中 {pending}｜超时 {timed_out}｜回归率 {rate:.1f}%｜"
          f"旧阶段 {old_total} 条仅归档，不参与升级｜"
          f"独立Demo验证成交 {int(demo_stats['filled'] or 0)}/100｜已平 {int(demo_stats['closed'] or 0)}｜"
          f"净盈亏 {float(demo_stats['net_pnl'] or 0):.4f} USDT")
    summary_rows = []
    for row in stats:
        completed = int(row["reverted"] or 0) + int(row["timed_out"] or 0)
        row_rate = 100.0 * int(row["reverted"] or 0) / max(1, completed)
        summary_rows.append(("稳定网络新阶段", str(row["timeframe"]), str(row["distance_mode"]), str(row["touches"]),
                             str(row["reverted"]), str(row["pending"]), str(row["timed_out"]),
                             f"{row_rate:.1f}%", f"{float(row['avg_bars'] or 0):.1f}",
                             f"{float(row['worst_adverse'] or 0):.3f} U"))
    for row in historical_stats:
        completed = int(row["reverted"] or 0) + int(row["timed_out"] or 0)
        row_rate = 100.0 * int(row["reverted"] or 0) / max(1, completed)
        summary_rows.append(("历史掉线阶段｜仅参考", str(row["timeframe"]), str(row["distance_mode"]),
                             str(row["touches"]), str(row["reverted"]), str(row["pending"]),
                             str(row["timed_out"]), f"{row_rate:.1f}%",
                             f"{float(row['avg_bars'] or 0):.1f}", f"{float(row['worst_adverse'] or 0):.3f} U"))
    _listview_rows(HANDLES.get("ma_experiment_summary", 0), summary_rows)
    status_labels = {"pending": "观察中", "reverted": "已回归", "timeout": "超时未回归"}
    detail_rows = []
    for row in recent:
        when = str(row["touch_time"]).replace("T", " ")[:19]
        detail_rows.append((when, str(row["timeframe"]), str(row["distance_mode"]),
                            "预埋多" if int(row["direction"]) > 0 else "预埋空",
                            f"{float(row['entry_price']):.3f}", f"{float(row['target_price']):.3f}",
                            f"{float(row['adx']):.1f}",
                            status_labels.get(str(row["status"]), str(row["status"])),
                            str(row["bars_to_resolve"]), f"{float(row['max_adverse']):.3f} U"))
    _listview_rows(HANDLES.get("ma_experiment_details", 0), detail_rows)


def _show_ma_experiment() -> None:
    existing = HANDLES.get("ma_experiment_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        _refresh_ma_experiment()
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"共享实验｜MA偏离插针回归｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 760,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["ma_experiment_window"] = hwnd
    HANDLES["ma_experiment_status"] = _create(hwnd, "STATIC", "正在读取累计数据…", SS_LEFT, 18, 12, 1200, 26)
    HANDLES["ma_experiment_summary"] = _listview(hwnd, (
        ("数据阶段", 170), ("周期", 70), ("距离方案", 130), ("触碰", 70), ("回归", 70), ("观察中", 80),
        ("超时", 70), ("回归率", 90), ("普通K线", 90), ("最大不利", 110)), 18, 44, 1220, 180)
    HANDLES["ma_experiment_details"] = _listview(hwnd, (
        ("触碰时间", 165), ("周期", 65), ("距离方案", 120), ("方向", 80),
        ("假想成交价", 110), ("冻结目标", 110), ("ADX", 70), ("结果", 100),
        ("耗时K线", 85), ("最大不利", 100)), 18, 250, 1220, 400)
    HANDLES["ma_experiment_refresh"] = _create(hwnd, "BUTTON", "刷新统计", WS_TABSTOP | BS_PUSHBUTTON,
                                                18, 670, 130, 34, ID_MA_EXPERIMENT_REFRESH)
    HANDLES["ma_experiment_close"] = _create(hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON,
                                              1118, 670, 120, 34, ID_MA_EXPERIMENT_CLOSE)
    _refresh_ma_experiment()
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def _snapshot_database_paths() -> list[Path]:
    """Known research stores only; opening is delegated to a read-only URI."""
    return [WORKSPACE / "live" / "state.sqlite3"] + [
        LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3"
        for slot in LIVE_SLOT_ORDER
    ]


def _snapshot_details_worker() -> None:
    try:
        details = five_minute_snapshot_details(_snapshot_database_paths(), limit=1000)
        _listview_rows(
            HANDLES.get("snapshot_details_table", 0),
            [snapshot_table_row(item) for item in details],
        )
        edge_count = sum(bool(item.get("edge")) for item in details)
        webhook_count = sum(bool(item.get("webhook")) for item in details)
        okx_count = sum(bool(item.get("okx")) for item in details)
        labelled = sum(bool(item.get("labels")) for item in details)
        missing = len(details) - edge_count
        webhook_state = (
            "Webhook端口冲突" if EDGE_RECEIVER_STATUS == "port_conflict" else
            "Webhook已配置" if os.environ.get("QUANTBOT_RESEARCH_WEBHOOK_SECRET") else
            "Webhook未配置"
        )
        _text(
            HANDLES.get("snapshot_details_status", 0),
            f"五分钟节点 {len(details)}｜OKX自动样本 {okx_count}｜"
            f"Edge有效 {edge_count}｜Edge缺失 {missing}｜Webhook有效 {webhook_count}｜"
            f"30分钟标签已成熟 {labelled}｜只读审计",
        )
        _text(
            HANDLES.get("snapshot_details_status", 0),
            f"五分钟节点 {len(details)}｜OKX自动样本 {okx_count}｜"
            f"Edge有效 {edge_count}｜Edge缺失 {missing}｜Webhook有效 {webhook_count}｜"
            f"{webhook_state}｜30分钟标签已成熟 {labelled}｜只读审计",
        )
    except Exception as exc:
        _text(HANDLES.get("snapshot_details_status", 0), f"快照明细读取失败（未修改数据库）：{exc}")


def _show_snapshot_details() -> None:
    existing = HANDLES.get("snapshot_details_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        threading.Thread(target=_snapshot_details_worker, daemon=True).start()
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"每5分钟 Edge/OKX 快照明细｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 760,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["snapshot_details_window"] = hwnd
    HANDLES["snapshot_details_status"] = _create(
        hwnd, "STATIC", "正在只读汇总五分钟快照……", SS_LEFT, 18, 12, 1220, 26,
    )
    HANDLES["snapshot_details_table"] = _listview(hwnd, (
        ("五分钟节点", 150), ("OKX样本", 75), ("Edge快照", 75),
        ("Webhook", 75), ("Webhook结果", 230), ("Edge价格", 95),
        ("周期", 60), ("Edge技术指标", 520),
        ("假设多", 80), ("假设空", 80), ("30分钟前瞻", 220),
        ("节点/缺失原因", 260),
    ), 18, 48, 1220, 600)
    HANDLES["snapshot_details_refresh"] = _create(
        hwnd, "BUTTON", "刷新明细", WS_TABSTOP | BS_PUSHBUTTON,
        18, 670, 130, 34, ID_SNAPSHOT_REFRESH,
    )
    HANDLES["snapshot_details_close"] = _create(
        hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON,
        1118, 670, 120, 34, ID_SNAPSHOT_CLOSE,
    )
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    threading.Thread(target=_snapshot_details_worker, daemon=True).start()


def _pinets_comparison_worker() -> None:
    try:
        payload = read_live_decisions(
            LIVE_WORKSPACE / "profiles" / "aggressive" / "strategy.sqlite3")
        rows = payload.get("rows") or []
        _listview_rows(
            HANDLES.get("pinets_comparison_table", 0),
            [live_decision_table_row(item) for item in rows],
        )
        submitted = sum(item.get("action") == "已实盘开仓" for item in rows)
        vetoed = sum(item.get("action") == "PineTS反向拦截" for item in rows)
        _text(
            HANDLES.get("pinets_comparison_status", 0),
            f"PineTS/LuxAlgo/Webhook实盘综合决策｜节点 {len(rows)}｜已开仓 {submitted}｜"
            f"反向拦截 {vetoed}｜5/15分钟识别翻转背景；1分钟穿MA5提前开仓，MA5/MA10交叉补漏；交易Webhook可直接开仓/平仓",
        )
    except Exception as exc:
        _text(HANDLES.get("pinets_comparison_status", 0), f"PineTS对照读取失败（未修改数据库）：{exc}")


def _reversal_misses_worker() -> None:
    try:
        database = LIVE_WORKSPACE / "profiles" / "aggressive" / "strategy.sqlite3"
        removed = prune_reversal_miss_history(database, keep=30)
        rows = read_reversal_misses(database, limit=30)
        freshness = reversal_feed_freshness(database)
        _listview_rows(HANDLES.get("reversal_misses_table", 0),
                       [reversal_miss_table_row(item) for item in rows])
        hourly_rows = read_hourly_missed_entries(database)
        _listview_rows(HANDLES.get("hourly_misses_table", 0), hourly_rows)
        missed = sum(item["result"] == "确认漏单" for item in rows)
        pending = sum(item["result"] == "观察中/未成交" for item in rows)
        _text(HANDLES.get("reversal_misses_status", 0),
              f"仅保留最新 {len(rows)}/30 条｜确认漏单 {missed}｜观察中/未成交 {pending}｜"
              f"最新扫描 {freshness['scan']}｜最新阶段 {freshness['stage']}｜"
              f"最新反转区 {freshness['zone']}｜整点60根回放 {len(hourly_rows)} 条｜本次自动清理 {removed} 条旧记录")
    except Exception as exc:
        _text(HANDLES.get("reversal_misses_status", 0), f"反转区复查刷新/保留清理失败：{exc}")


def _show_reversal_misses() -> None:
    existing = HANDLES.get("reversal_misses_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        _reversal_misses_worker()
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"反转区漏单复查｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 760,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["reversal_misses_window"] = hwnd
    HANDLES["reversal_misses_status"] = _create(
        hwnd, "STATIC", "正在汇总反转区证据并仅保留最新30条……", SS_LEFT, 18, 12, 1220, 26)
    HANDLES["reversal_misses_table"] = _listview(hwnd, (
        ("反转区时间", 145), ("周期", 55), ("方向", 85), ("形态分类", 180), ("第一阶段", 150),
        ("第二阶段", 155), ("第三阶段", 165), ("最终结果", 105),
        ("漏单分类", 125), ("完整原因", 460), ("当时价格", 90),
        ("局部止损", 90), ("策略版本", 170), ("订单方向", 85),
        ("关联订单号", 190), ("提交时间", 145),
    ), 18, 48, 1220, 310)
    HANDLES["hourly_misses_label"] = _create(
        hwnd, "STATIC", "整点回放：上一小时60根已收盘1分钟K线｜候选机会与发单故障分列；回放不触发下单",
        SS_LEFT, 18, 365, 1220, 24)
    HANDLES["hourly_misses_table"] = _listview(hwnd, (
        ("候选时间", 135), ("方向", 65), ("触发类型", 260), ("复盘结果", 155),
        ("随后15分钟有利点数", 145), ("未成交原因", 490), ("客户端订单号", 180),
    ), 18, 390, 1220, 260)
    HANDLES["reversal_misses_refresh"] = _create(
        hwnd, "BUTTON", "刷新复查", WS_TABSTOP | BS_PUSHBUTTON,
        18, 670, 130, 34, ID_REVERSAL_MISSES_REFRESH)
    HANDLES["reversal_misses_close"] = _create(
        hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON,
        1118, 670, 120, 34, ID_REVERSAL_MISSES_CLOSE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    _layout_reversal_misses(rect.right - rect.left, rect.bottom - rect.top)
    _reversal_misses_worker()


def _show_pinets_comparison() -> None:
    existing = HANDLES.get("pinets_comparison_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        _pinets_comparison_worker()
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"PineTS/LuxAlgo/Webhook实盘综合决策｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 760,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["pinets_comparison_window"] = hwnd
    HANDLES["pinets_comparison_status"] = _create(
        hwnd, "STATIC", "正在加载实盘综合决策记录……", SS_LEFT, 18, 12, 1220, 26,
    )
    HANDLES["pinets_comparison_table"] = _listview(hwnd, (
        ("五分钟节点", 145), ("Pine源码逐项投票", 330), ("票数", 105),
        ("综合方向", 80), ("原策略", 75), ("Webhook", 150), ("信号所有者", 90),
        ("最终动作", 120), ("服务器止损", 100), ("服务器止盈", 100), ("原因/拦截", 420),
    ), 18, 48, 1220, 600)
    HANDLES["pinets_comparison_refresh"] = _create(
        hwnd, "BUTTON", "刷新决策", WS_TABSTOP | BS_PUSHBUTTON, 18, 670, 130, 34, ID_PINETS_REFRESH,
    )
    HANDLES["pinets_comparison_close"] = _create(
        hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON, 1118, 670, 120, 34, ID_PINETS_CLOSE,
    )
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    _pinets_comparison_worker()


def _trade_details_worker() -> None:
    open_rows: list[tuple[int, tuple[str, ...]]] = []
    rows: list[tuple[int, tuple[str, ...]]] = []
    errors: list[str] = []
    accounts = tuple((LIVE_PROFILES[slot]["label"], slot, LIVE_SESSION_CREDENTIALS.get(slot))
                     for slot in LIVE_SLOT_ORDER)
    for strategy, slot, credentials in accounts:
        if credentials is None:
            errors.append(f"{strategy}实盘API未载入")
            continue
        try:
            client = OkxLiveAggressiveAdapter(credentials, timeout=20)
            positions = client._request("GET", "/api/v5/account/positions", {
                "instType": "SWAP", "instId": "ETH-USDT-SWAP"}, private=True).get("data", [])
            lifecycle_store = StateStore(LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3")
            try:
                lifecycle_rows = lifecycle_store.connection.execute(
                    "SELECT * FROM trade_lifecycle"
                ).fetchall()
                lifecycle = {str(row["order_id"]): row for row in lifecycle_rows}
            finally:
                lifecycle_store.close()
            for item in positions:
                if abs(float(item.get("pos") or 0)) <= 0:
                    continue
                pos_side = "多仓" if item.get("posSide") == "long" else "空仓"
                opened = int(item.get("cTime") or item.get("uTime") or 0)
                when = datetime.fromtimestamp(opened / 1000).strftime("%Y-%m-%d %H:%M:%S") if opened else "-"
                row = (when, strategy, "ETH-USDT 永续", pos_side, str(item.get("pos", "-")),
                       str(item.get("avgPx", "-")), str(item.get("last", "-")),
                       _pnl_text(item.get("upl"), floating=True), "未平仓")
                open_rows.append((opened, row))
            fills = client._request("GET", "/api/v5/trade/fills-history", {
                "instType": "SWAP", "instId": "ETH-USDT-SWAP", "limit": "100"}, private=True).get("data", [])
            reconcile = StateStore(LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3")
            try:
                open_lifecycles = reconcile.connection.execute(
                    "SELECT * FROM trade_lifecycle WHERE status='open' ORDER BY signal_time"
                ).fetchall()
                for lifecycle_row in open_lifecycles:
                    entry, exits = matched_closing_fills(lifecycle_row, fills)
                    if not entry or not exits:
                        continue
                    gross = sum(float(item.get("fillPnl") or 0) for item in exits)
                    fees = sum(float(item.get("fee") or 0) for item in entry + exits)
                    size = sum(float(item.get("fillSz") or 0) for item in exits)
                    close_price = (sum(float(item.get("fillPx") or 0) * float(item.get("fillSz") or 0)
                                       for item in exits) / size) if size else None
                    exit_reason = "服务器止损触发" if gross <= 0 else "服务器止盈/盈利保护触发"
                    reconcile.close_trade_lifecycle(
                        str(lifecycle_row["trade_uid"]), close_price=close_price,
                        gross_pnl=gross, total_fees=fees, exit_reason=exit_reason)
            finally:
                reconcile.close()
            lifecycle_store = StateStore(LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3")
            try:
                lifecycle_rows = lifecycle_store.connection.execute(
                    "SELECT * FROM trade_lifecycle"
                ).fetchall()
                lifecycle = {str(row["order_id"]): row for row in lifecycle_rows}
                for lifecycle_row in lifecycle_rows:
                    _, closing = matched_closing_fills(lifecycle_row, fills)
                    for closing_fill in closing:
                        lifecycle[str(closing_fill.get("ordId", ""))] = lifecycle_row
            finally:
                lifecycle_store.close()
            for item in fills:
                ts = int(item.get("fillTime") or item.get("ts") or 0)
                when = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
                side = "买入" if item.get("side") == "buy" else "卖出"
                pos = "多仓" if item.get("posSide") == "long" else "空仓"
                order_id = str(item.get("ordId", "-"))
                life = lifecycle.get(order_id)
                stage = "稳定网络新阶段" if ts >= NETWORK_STABLE_CUTOFF_MS else "历史掉线阶段"
                quality = "完整生命周期" if life else ("待后续匹配" if ts >= NETWORK_STABLE_CUTOFF_MS else "低可信归档")
                trigger = str(life["signal_reason"]) if life else "本地生命周期缺失"
                exit_reason = str(life["exit_reason"] or "持仓/保护单管理中") if life else "不据此修改策略"
                row = (when, stage, quality, strategy, f"{side}{pos}", str(item.get("fillSz", "-")),
                       str(item.get("fillPx", "-")), _pnl_text(item.get("fillPnl")),
                       f"{item.get('fee', '-')} USDT", trigger, exit_reason, order_id)
                rows.append((ts, row))
        except Exception as exc:
            errors.append(f"{strategy}读取失败：{exc}")
    open_rows.sort(key=lambda value: value[0], reverse=True)
    rows.sort(key=lambda value: value[0], reverse=True)
    _listview_rows(HANDLES.get("trade_open_table", 0), [row for _, row in open_rows])
    _listview_rows(HANDLES.get("trade_history_table", 0), [row for _, row in rows[:300]])
    if HANDLES.get("trade_status"):
        new_count = sum(1 for ts, _ in rows if ts >= NETWORK_STABLE_CUTOFF_MS)
        status = (f"三实盘账户｜当前持仓 {len(open_rows)} 条｜成交 {len(rows)} 条｜稳定网络新阶段 {new_count} 条｜"
                  f"分界 {NETWORK_STABLE_CUTOFF_LOCAL}；旧记录仅归档｜北京时间，最新在前")
        if errors:
            status += "｜" + "；".join(errors)
        _text(HANDLES["trade_status"], status)


def _show_trade_details() -> None:
    existing = HANDLES.get("trade_details_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        threading.Thread(target=_trade_details_worker, daemon=True).start()
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", "激进型/保守型/稳妥型｜LIVE实盘交易明细", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        CW_USEDEFAULT, CW_USEDEFAULT, 1120, 700, HANDLES.get("main", 0), None,
        kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["trade_details_window"] = hwnd
    HANDLES["trade_status"] = _create(hwnd, "STATIC", "正在读取三个实盘子账户的成交与生命周期……", SS_LEFT, 18, 12, 1060, 26)
    HANDLES["trade_open_label"] = _create(hwnd, "STATIC", "当前未平仓（固定显示在上方）", SS_LEFT, 18, 42, 500, 24)
    open_columns = (("开仓时间", 155), ("策略", 80), ("交易品种", 125), ("仓位", 75),
                    ("数量", 70), ("开仓均价", 100), ("最新价", 100), ("浮动盈亏", 190), ("状态", 80))
    HANDLES["trade_open_table"] = _listview(hwnd, open_columns, 18, 68, 1060, 160)
    HANDLES["trade_history_label"] = _create(hwnd, "STATIC", "已成交历史", SS_LEFT, 18, 240, 500, 24)
    history_columns = (("成交时间", 155), ("数据阶段", 145), ("记录质量", 110), ("策略", 80), ("交易方向", 90),
                       ("成交数量", 85), ("成交均价", 105), ("已实现盈亏", 200),
                       ("手续费", 130), ("触发下单原因", 420), ("止盈止损/平仓原因", 300), ("OKX订单号", 190))
    HANDLES["trade_history_table"] = _listview(hwnd, history_columns, 18, 266, 1060, 304)
    HANDLES["trade_refresh"] = _create(hwnd, "BUTTON", "刷新明细", WS_TABSTOP | BS_PUSHBUTTON, 18, 602, 130, 38, ID_TRADE_REFRESH)
    HANDLES["trade_chart"] = _create(hwnd, "BUTTON", "打开官方1分钟K线", WS_TABSTOP | BS_PUSHBUTTON, 166, 602, 200, 38, ID_TRADE_CHART)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    threading.Thread(target=_trade_details_worker, daemon=True).start()


def _loss_review_worker() -> None:
    errors: list[str] = []
    rows = []
    for slot in LIVE_SLOT_ORDER:
        label = LIVE_PROFILES[slot]["label"]
        credentials = LIVE_SESSION_CREDENTIALS.get(slot)
        store = StateStore(LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3")
        try:
            if credentials is not None:
                try:
                    client = OkxLiveAggressiveAdapter(credentials, timeout=20)
                    fills = client._request("GET", "/api/v5/trade/fills-history", {
                        "instType": "SWAP", "instId": "ETH-USDT-SWAP", "limit": "100"}, private=True).get("data", [])
                    for item in fills:
                        gross = float(item.get("fillPnl") or 0)
                        fee = float(item.get("fee") or 0)
                        if gross + fee >= 0 or gross == 0:
                            continue
                        ts = int(item.get("fillTime") or item.get("ts") or 0)
                        close_time = (datetime.fromtimestamp(ts / 1000).astimezone().isoformat()
                                      if ts else datetime.now().astimezone().isoformat())
                        direction = 1 if item.get("posSide") == "long" else -1
                        uid = f"LIVE-{slot}-{item.get('tradeId') or item.get('ordId') or ts}"
                        store.record_external_loss(
                            trade_uid=uid, strategy_id=f"live_{slot}", close_time=close_time,
                            direction=direction, gross_pnl=gross, fee=fee,
                            evidence=(f"实盘账户={label}；OKX订单={item.get('ordId','-')}；"
                                      f"成交价={item.get('fillPx','-')}；数量={item.get('fillSz','-')}；"
                                      f"毛盈亏={gross:.6f}；手续费={fee:.6f}"),
                        )
                except Exception as exc:
                    errors.append(f"{label}补录失败：{exc}")
            for item in store.loss_reviews():
                try:
                    when = datetime.fromisoformat(str(item["close_time"])).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    when = str(item["close_time"])
                direction = "做多" if int(item["direction"]) > 0 else "做空" if int(item["direction"]) < 0 else "未知"
                stable = _is_stable_stage(str(item["close_time"]))
                stage = "稳定网络新阶段" if stable else "历史掉线阶段"
                value = "可纳入候选" if stable and str(item["confidence"]) in ("中", "高") else "仅归档，不驱动升级"
                rows.append((when, stage, value, label, str(item["strategy_version"]), direction,
                             f"{float(item['net_pnl']):+.6f} USDT", str(item["category"]),
                             str(item["trigger_snapshot"]), str(item["cause"]), str(item["evidence"]),
                             str(item["recommendation"]), str(item["confidence"]), str(item["review_status"])))
        finally:
            store.close()
    rows.sort(key=lambda row: row[0], reverse=True)
    _listview_rows(HANDLES.get("loss_review_table", 0), rows)
    useful = sum(1 for row in rows if row[2] == "可纳入候选")
    status = (f"三个实盘账户已记录 {len(rows)} 笔净亏损｜当前可纳入优化候选 {useful} 笔｜"
              f"分界 {NETWORK_STABLE_CUTOFF_LOCAL}；旧掉线记录仅归档，不会自动修改策略。")
    if errors:
        status += "｜" + "；".join(errors)
    _text(HANDLES.get("loss_review_status", 0), status)


def _show_loss_reviews() -> None:
    existing = HANDLES.get("loss_review_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        threading.Thread(target=_loss_review_worker, daemon=True).start()
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", "LIVE实盘亏损订单复盘｜激进型/保守型/稳妥型",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 720,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["loss_review_window"] = hwnd
    HANDLES["loss_review_status"] = _create(
        hwnd, "STATIC", "正在汇总三个实盘子账户的生命周期、亏损成交与优化证据……",
        SS_LEFT, 18, 14, 1220, 28,
    )
    columns = (("平仓时间", 155), ("数据阶段", 145), ("优化价值", 145), ("策略", 90), ("策略版本", 190), ("方向", 65),
               ("净亏损", 135), ("原因分类", 130), ("开仓触发快照", 480), ("初步原因", 300), ("证据", 420),
               ("优化建议", 460), ("可信度", 70), ("状态", 100))
    HANDLES["loss_review_table"] = _listview(hwnd, columns, 18, 48, 1220, 560)
    HANDLES["loss_review_refresh"] = _create(
        hwnd, "BUTTON", "刷新并补录", WS_TABSTOP | BS_PUSHBUTTON, 18, 620, 140, 36, ID_LOSS_REFRESH,
    )
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    threading.Thread(target=_loss_review_worker, daemon=True).start()


def _open(path: Path) -> None:
    shell32.ShellExecuteW(None, "open", str(path.resolve()), None, None, SW_SHOW)


def _show_changelog() -> None:
    existing = HANDLES.get("changelog_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"Codex QuantBot v{APP_VERSION} 更新日志",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 760, 620,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    if hwnd:
        HANDLES["changelog_window"] = hwnd
        pages = changelog_pages(50)
        HANDLES["changelog_pages"] = pages
        HANDLES["changelog_page_index"] = 0
        text = pages[0].replace("\n", "\r\n")
        edit = _create(
            hwnd, "EDIT", "",
            WS_BORDER | WS_VSCROLL | ES_MULTILINE | ES_AUTOVSCROLL | ES_READONLY,
            18, 18, 706, 500,
        )
        HANDLES["changelog_text"] = edit
        user32.SendMessageW(edit, EM_SETLIMITTEXT, max(1_000_000, len(text) + 1), 0)
        _text(edit, text)
        user32.SendMessageW(edit, 0x00B1, 0, 0)
        HANDLES["changelog_previous"] = _create(hwnd, "BUTTON", "上一页", WS_TABSTOP | BS_PUSHBUTTON, 18, 530, 100, 34, ID_CHANGELOG_PREVIOUS)
        HANDLES["changelog_page_label"] = _create(hwnd, "STATIC", f"第1/{len(pages)}页（每页50行）", 0, 130, 536, 160, 26)
        HANDLES["changelog_next"] = _create(hwnd, "BUTTON", "下一页", WS_TABSTOP | BS_PUSHBUTTON, 300, 530, 100, 34, ID_CHANGELOG_NEXT)
        HANDLES["changelog_close"] = _create(hwnd, "BUTTON", "确定", WS_TABSTOP | BS_PUSHBUTTON, 624, 530, 100, 34, ID_CHANGELOG_CLOSE)
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        return
    _message(changelog_text(), f"Codex QuantBot v{APP_VERSION} 更新日志")


def _change_changelog_page(delta: int) -> None:
    pages = HANDLES.get("changelog_pages") or changelog_pages(50)
    current = int(HANDLES.get("changelog_page_index", 0))
    target = max(0, min(len(pages) - 1, current + delta))
    HANDLES["changelog_page_index"] = target
    edit = HANDLES.get("changelog_text", 0)
    if edit:
        text = pages[target].replace("\n", "\r\n")
        user32.SendMessageW(edit, EM_SETLIMITTEXT, max(1_000_000, len(text) + 1), 0)
        _text(edit, text)
        user32.SendMessageW(edit, 0x00B1, 0, 0)
    label = HANDLES.get("changelog_page_label", 0)
    if label:
        _text(label, f"第{target + 1}/{len(pages)}页（每页50行）")


def _update_manifest_location() -> str | Path:
    configured = os.environ.get("QUANTBOT_UPDATE_MANIFEST", "").strip()
    if configured:
        return configured
    if UPDATE_SOURCE.exists():
        value = UPDATE_SOURCE.read_text(encoding="utf-8").strip()
        if value:
            return value
    return _resource("configs/update-manifest.json")


def _update_button_text() -> str:
    try:
        manifest = load_manifest(_update_manifest_location())
        return f"🔴 新版本 v{manifest.version}" if is_newer(manifest.version, APP_VERSION) else f"🟢 已是最新版 v{APP_VERSION}"
    except Exception:
        return "🟡 更新状态不可用"


def _download_update_worker(manifest) -> None:
    try:
        if SESSION_CREDENTIALS is None:
            raise OkxError("无法确认 Demo 账户为空，请先验证已保存的 Demo API")
        OkxDemoClient(SESSION_CREDENTIALS).assert_account_clear()
        destination = WORKSPACE / "updates" / f"CodexQuantBot-v{manifest.version}.exe"
        download_verified_update(manifest, destination)
        _text(HANDLES["update"], f"🟢 v{manifest.version} 已下载")
        _message(
            f"更新已下载并通过 SHA-256 校验：\n{destination}\n\n"
            "为避免交易中断，本版本不会在程序运行中强制覆盖 EXE。请保持账户空仓，关闭客户端后安装新版本。\n"
            "QuantBotWorkspace 中的加密 API、配置、状态和报告不会被覆盖。",
            "更新下载完成",
        )
    except Exception as exc:
        _text(HANDLES["update"], "🔴 更新失败")
        _message(str(exc), "更新失败", True)


def _open_update() -> None:
    try:
        manifest = load_manifest(_update_manifest_location())
        if not is_newer(manifest.version, APP_VERSION):
            _show_changelog()
            return
        details = "\n".join(f"• {item}" for item in manifest.changelog)
        answer = user32.MessageBoxW(
            HANDLES.get("main", 0),
            f"发现新版本 v{manifest.version}\n\n{details}\n\n阅读完更新日志后，点击“是”开始下载。",
            "新版本更新日志",
            0x24,
        )
        if answer == 6:
            _text(HANDLES["update"], f"🟡 正在下载 v{manifest.version}")
            threading.Thread(target=_download_update_worker, args=(manifest,), daemon=True).start()
    except Exception as exc:
        _message(str(exc), "检查更新失败", True)


def _edit_branding() -> None:
    shell32.ShellExecuteW(None, "open", "notepad.exe", f'"{BRANDING.resolve()}"', None, SW_SHOW)
    _message("修改 name 和 wechat 后保存文件，再重新启动软件即可生效。", "修改品牌信息")


def _input_value(name: str) -> str:
    handle = HANDLES[name]
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value.strip()


def _apply_credentials() -> None:
    global SESSION_CREDENTIALS
    api_key = _input_value("api_key")
    secret_key = _input_value("secret_key")
    passphrase = _input_value("passphrase")
    if not all((api_key, secret_key, passphrase)):
        _message("请完整输入 Demo API Key、Secret Key 和 Passphrase。", "凭据不完整", True)
        return
    SESSION_CREDENTIALS = OkxCredentials(api_key, secret_key, passphrase)
    for name in ("api_key", "secret_key", "passphrase"):
        _text(HANDLES[name], "")
    _text(HANDLES["okx"], "Demo API 凭据已载入当前进程内存；关闭软件后自动清除。请点击“验证 Demo API”。")


def _field_credentials() -> OkxCredentials:
    values = tuple(_input_value(name) for name in ("api_key", "secret_key", "passphrase"))
    if not all(values):
        raise OkxError("请完整输入 Demo API Key、Secret Key 和 Passphrase。")
    return OkxCredentials(*values)


def _clear_credential_fields() -> None:
    for name in ("api_key", "secret_key", "passphrase"):
        _text(HANDLES[name], "")


def _live_field_name(slot: str, field: str) -> str:
    return f"live_{slot}_{field}"


def _live_api_fields(slot: str) -> LiveAuditCredentials:
    values = tuple(_input_value(_live_field_name(slot, field)).strip() for field in (
        "api_key", "secret_key", "passphrase"))
    if not all(values):
        raise OkxError(f"请完整输入{LIVE_PROFILES[slot]['label']}实盘 API 三项凭据。")
    return LiveAuditCredentials(*values)


def _clear_live_api_fields(slot: str) -> None:
    for field in ("api_key", "secret_key", "passphrase"):
        name = _live_field_name(slot, field)
        if name in HANDLES:
            _text(HANDLES[name], "")


def _format_live_audit(report: dict) -> str:
    account = report["account"]
    balance = report["usdt_balance"]
    contract = report["instrument"]
    market = report["market"]
    minimum_order = report["minimum_order"]
    side_names = {"long": "多仓", "short": "空仓", "net": "净持仓", "": "净持仓"}
    mode_names = {"long_short_mode": "双向持仓", "net_mode": "单向持仓"}
    margin_names = {"cross": "全仓", "isolated": "逐仓"}
    state_names = {"live": "可交易", "suspend": "暂停交易", "preopen": "待开盘", "test": "测试"}
    level_names = {"1": "简单交易", "2": "单币种保证金", "3": "跨币种保证金", "4": "组合保证金"}
    leverage = "，".join(
        f"{side_names.get(str(item.get('posSide') or ''), str(item.get('posSide') or '净持仓'))}="
        f"{item.get('lever') or '未知'}倍"
        for item in report["leverage"]
    ) or "未返回"
    positions = report["open_positions"]
    position_text = "空仓" if not positions else ", ".join(
        f"{side_names.get(str(item.get('posSide') or ''), item.get('posSide') or '未知方向')} "
        f"{item.get('pos')}张 {margin_names.get(str(item.get('mgnMode') or ''), item.get('mgnMode') or '未知模式')}"
        for item in positions
    )
    account_level = str(account.get("acctLv") or "")
    position_mode = str(account.get("posMode") or "")
    contract_state = str(contract.get("state") or "")
    return (
        "只读审计成功｜审计本身不会下单；实盘单仅接受独立候选确认语\n"
        f"账户模式={level_names.get(account_level, '未知')}｜"
        f"持仓模式={mode_names.get(position_mode, '未知')}｜"
        f"全仓杠杆={leverage}\n"
        f"USDT权益={balance.get('eq') or '?'}｜可用={balance.get('availBal') or '?'}｜"
        f"当前持仓={position_text}\n"
        f"{contract.get('instId') or LIVE_INSTRUMENT}｜合约状态={state_names.get(contract_state, '未知')}｜"
        f"最小={contract.get('minSz') or '?'}张｜步长={contract.get('lotSz') or '?'}｜"
        f"面值={contract.get('ctVal') or '?'} {contract.get('ctValCcy') or ''}\n"
        f"最新价={market.get('last') or '?'}｜标记价={market.get('markPx') or '?'}\n"
        f"最小实盘单={minimum_order['api_size_contracts']}张≈"
        f"{minimum_order['estimated_notional_usdt']} USDT｜API sz必须传张数，不得传USDT金额\n"
        "仍需在OKX网页人工确认：独立子账户、固定IP、无Withdraw权限。"
    )


def _live_message_zh(value: object) -> str:
    """Translate engine/status vocabulary before it reaches the main console."""
    text = str(value)
    exact = {
        "observe": "继续观察", "blocked": "已被安全规则拦截", "manage": "管理现有仓位",
        "submitted": "下单已提交", "exit_submitted": "主动止盈已提交",
        "placed": "预埋单已挂出", "reanchored": "预埋单已重新定位",
        "armed": "预埋条件已启用", "invalidated": "候选已失效", "sibling_cancelled": "同组多余委托已撤销",
        "duplicate": "重复信号已忽略", "protection_alert": "保护单异常警报",
    }
    if text in exact:
        return exact[text]
    replacements = {
        "OKX Live HTTP 401:": "OKX实盘接口身份校验失败：",
        "Timestamp request expired": "请求时间戳已过期",
        '"msg":': '"说明":',
        '"code":': '"错误代码":',
        "one-contract Demo order submitted with server-side ma5_turn take-profit":
            "下单已提交；服务器保留止损，程序按5分钟MA5拐头主动止盈",
        "Unknown OkxConfig keys:": "配置文件中存在无法识别的OKX参数：",
        "validation_channel_bar": "已废弃的一分钟通道周期参数",
        "validation_channel_entry_fraction": "已废弃的通道入场比例参数",
        "validation_channel_window": "已废弃的通道计算窗口参数",
        "validation_extreme_entry_fraction": "已废弃的极值入场比例参数",
        "can't subtract offset-naive and offset-aware datetimes": "历史时间记录缺少时区，无法与当前UTC时间比较",
        "Live candidate cooldown is active": "候选信号冷却时间尚未结束",
        "Live daily candidate limit reached": "今日候选信号数量已达到上限",
        "validation cooldown is active": "交易冷却时间尚未结束",
        "daily validation trade limit reached": "今日交易次数已达到上限",
        "1m market data is stale": "一分钟行情数据已过期",
        "mark price is stale": "标记价格数据已过期",
        "latest price is unavailable": "无法取得最新成交价",
        "same-side position/order already exists": "同方向仓位或委托已经存在",
        "durable lifecycle already has same-side exposure": "交易生命周期中已经存在同方向风险敞口",
        "rejected at falling MA20": "反抽下降中的MA20后受阻回落",
        "rebounded at rising MA20": "回踩上升中的MA20后企稳回升",
        "unprotected position blocks every new entry": "存在未受保护的仓位，禁止任何新开仓",
        "same-side position/order already exists": "同方向仓位或委托已经存在",
        "durable lifecycle already has same-side exposure": "交易生命周期中已经存在同方向风险敞口",
        "entry abandoned": "放弃入场",
        "structure stop is": "结构止损距离为",
        "ATR away": "倍ATR，距离过远",
        "not enough closed candles": "已收盘K线数量不足",
        "entry abandoned": "放弃入场",
        "waiting": "等待", "confirmed": "已确认", "downtrend": "下跌趋势",
        "uptrend": "上涨趋势", "pullback": "回抽", "rejection": "受阻回落",
        "structure stop": "结构止损", "ATR away": "倍ATR距离",
        "OKX Live GET failed": "OKX实盘只读查询失败",
        "The handshake operation timed out": "SSL安全连接握手超时",
        "after 3 attempts": "连续重试3次后仍失败",
        "15m": "15分钟", "5m": "5分钟", "1m": "1分钟",
        "30m": "30分钟", "1H": "1小时", "4H": "4小时",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _set_live_control_state(slot: str, state: str) -> None:
    LIVE_ACCOUNT_STATES[slot] = state
    button_text = aggressive_button_text(state)
    for name in (f"live_main_{slot}_toggle", f"live_child_{slot}_toggle"):
        handle = HANDLES.get(name, 0)
        if handle and user32.IsWindow(handle):
            _text(handle, button_text)
    bound_count = sum(item is not None for item in LIVE_SESSION_CREDENTIALS.values())
    state_text = {
        "stopped": "已停止", "starting": "正在启动", "running": "运行中",
        "stopping": "正在停止", "faulted": "故障停止",
    }[state]
    suffix = ""
    try:
        enabled = LiveAccountSettings(
            LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3"
        ).base_rebuild_enabled()
        suffix = f"｜基础仓建立={'开启' if enabled else '关闭'}"
    except Exception:
        suffix = "｜基础仓建立=状态读取失败"
    _text(HANDLES.get("live_main_status", 0),
          f"实盘控制台：{bound_count}/5 个账户已绑定｜{LIVE_PROFILES[slot]['label']}状态={state_text}{suffix}")


def _live_status_text(slot: str, value: str) -> None:
    _text(HANDLES.get(f"live_{slot}_status", 0), value)
    _text(HANDLES.get(f"live_main_{slot}_detail", 0), value)
    bound = LIVE_SESSION_CREDENTIALS.get(slot) is not None
    mode = "独立自动开关"
    summary = f"{LIVE_PROFILES[slot]['label']}：{'已绑定' if bound else '尚未绑定'}｜{mode}"
    _text(HANDLES.get(f"live_console_{slot}_status", 0), summary)
    bound_count = sum(item is not None for item in LIVE_SESSION_CREDENTIALS.values())
    state_text = {
        "stopped": "已停止", "starting": "正在启动", "running": "运行中",
        "stopping": "正在停止", "faulted": "故障停止",
    }[LIVE_ACCOUNT_STATES[slot]]
    suffix = ""
    try:
        enabled = LiveAccountSettings(
            LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3"
        ).base_rebuild_enabled()
        suffix = f"｜基础仓建立={'开启' if enabled else '关闭'}"
    except Exception:
        suffix = "｜基础仓建立=状态读取失败"
    _text(HANDLES.get("live_main_status", 0),
          f"实盘控制台：{bound_count}/5 个账户已绑定｜{LIVE_PROFILES[slot]['label']}状态={state_text}{suffix}")


def _live_api_worker(action: str, slot: str) -> None:
    try:
        credential_file = LIVE_CREDENTIAL_FILES[slot]
        if action == "delete":
            if load_live_credentials(credential_file) is None:
                raise OkxError(f"本机尚未保存{LIVE_PROFILES[slot]['label']}实盘 API。")
            delete_live_credentials(credential_file)
            LIVE_SESSION_CREDENTIALS[slot] = None
            _clear_live_api_fields(slot)
            value = "本机加密 API 已删除；OKX 网页上的 API Key 尚未撤销。"
        else:
            # Save means replace: always validate what is currently typed.
            # Audit means reuse the active/saved credential.  The old ordering
            # silently ignored replacement fields whenever a stale key existed.
            candidate = (_live_api_fields(slot) if action == "save" else
                         (LIVE_SESSION_CREDENTIALS.get(slot)
                          or load_live_credentials(credential_file)
                          or _live_api_fields(slot)))
            for other_slot in LIVE_SLOT_ORDER:
                if other_slot == slot:
                    continue
                other = LIVE_SESSION_CREDENTIALS.get(other_slot) or load_live_credentials(
                    LIVE_CREDENTIAL_FILES[other_slot])
                if other is not None and other.api_key == candidate.api_key:
                    raise OkxError(
                        f"该API Key已绑定到{LIVE_PROFILES[other_slot]['label']}槽位；五个账户禁止复用API。"
                    )
            report = OkxLiveReadOnlyClient(candidate, timeout=20).audit()
            LIVE_SESSION_CREDENTIALS[slot] = candidate
            if action == "save":
                save_live_credentials(credential_file, candidate)
                _clear_live_api_fields(slot)
            value = f"{LIVE_PROFILES[slot]['label']}子账户｜" + _format_live_audit(report)
        _live_status_text(slot, value)
    except Exception as exc:
        _live_status_text(slot, f"实盘只读操作失败：{exc}")


def _live_scan_worker(slot: str) -> None:
    try:
        LIVE_PENDING_CANDIDATES[slot] = None
        _text(HANDLES.get(f"live_{slot}_approval", 0), "")
        _text(HANDLES.get(f"live_main_{slot}_approval", 0), "")
        credentials = LIVE_SESSION_CREDENTIALS.get(slot) or load_live_credentials(
            LIVE_CREDENTIAL_FILES[slot])
        if credentials is None:
            raise OkxError(f"请先绑定并审计{LIVE_PROFILES[slot]['label']}子账户。")
        lifecycle = StateStore(LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3")
        try:
            successful_trades = lifecycle.successful_lifecycle_order_count(
                datetime.now(timezone.utc).date(), f"live_{slot}")
        finally:
            lifecycle.close()
        if LIVE_PROFILES[slot]["max_successful_trades"] > 0 and successful_trades >= int(LIVE_PROFILES[slot]["max_successful_trades"]):
            raise OkxError("今日成功交易已达到100次上限；候选信号从未计入额度。")
        report = OkxLiveReadOnlyClient(credentials, timeout=20).audit()
        candidate = scan_conservative_live_candidate(report, slot)
        label = LIVE_PROFILES[slot]["label"]
        if not candidate.eligible:
            value = f"{label}实盘扫描：保持空仓｜{candidate.reason}"
        else:
            state = LiveStateStore(LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3", slot)
            candidate_id = state.register_candidate(candidate)
            LIVE_PENDING_CANDIDATES[slot] = candidate_id
            side = "做多" if candidate.direction > 0 else "做空"
            value = (
                f"发现{label}实盘候选（仍未下单）｜{side} {candidate.contracts}张｜"
                f"名义价值≈{candidate.estimated_notional_usdt:.2f} USDT\n"
                f"参考入场={candidate.entry_reference:.2f}｜标记价止损={candidate.stop_loss:.2f}｜"
                f"目标={candidate.take_profit:.2f}｜R={candidate.reward_risk:.2f}\n"
                f"计划最大亏损≈{candidate.planned_loss_usdt:.4f} USDT｜"
                f"确认K线={candidate.confirmed_bar_time}\n"
                f"触发规则={candidate.reason}\n"
                f"候选编号={candidate_id}\n"
                f"后续人工确认语={approval_phrase(candidate_id)}\n"
                "候选已写入该子账户独立状态库；不会自动成交，必须逐字输入一次性确认语。"
            )
        _live_status_text(slot, value)
    except Exception as exc:
        _live_status_text(slot, f"实盘扫描失败：{exc}")


def _live_execute_worker(slot: str) -> None:
    candidate_id = LIVE_PENDING_CANDIDATES.get(slot)
    try:
        if not candidate_id:
            raise OkxError("请先扫描并生成当前合格候选。")
        approval_name = (f"live_main_{slot}_approval"
                         if HANDLES.get(f"live_main_{slot}_approval") else f"live_{slot}_approval")
        phrase = _input_value(approval_name)
        if phrase != approval_phrase(candidate_id):
            raise OkxError("人工确认语不完全一致，拒绝实盘下单。")
        credentials = LIVE_SESSION_CREDENTIALS.get(slot) or load_live_credentials(
            LIVE_CREDENTIAL_FILES[slot])
        if credentials is None:
            raise OkxError(f"请先绑定并审计{LIVE_PROFILES[slot]['label']}子账户。")
        # Fresh preflight immediately before consuming the one-time approval.
        report = OkxLiveReadOnlyClient(credentials, timeout=20).audit()
        store = LiveStateStore(LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3", slot)
        result = execute_approved_minimum_order(
            store, OkxLiveManualClient(credentials, timeout=20), candidate_id, phrase, report,
        )
        candidate = result["candidate"]
        manual_rule_audit = build_entry_rule_audit(
            direction=int(candidate["direction"]),
            classification={"label": "人工确认实盘候选", "category": "人工确认",
                            "rule": str(candidate.get("reason", "-"))},
            entry_kind=str(candidate.get("branch", "manual_live_candidate")),
            three_stage_audit={}, cover_ok=None, position_ok=None,
            profit_space_ok=float(candidate.get("reward_risk", 0)) > 1.8,
            indicator_ok=True,
            stop_side_ok=int(candidate["direction"]) * (
                float(candidate["entry_reference"]) - float(candidate["stop_loss"])) > 0)
        lifecycle = StateStore(LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3")
        try:
            lifecycle.open_trade_lifecycle(
                trade_uid=str(result["clOrdId"]), strategy_id=f"live_{slot}",
                strategy_version=str(candidate.get("policy_version", "live-manual-minimum-v1")),
                instrument=LIVE_INSTRUMENT, direction=int(candidate["direction"]),
                signal_time=str(candidate.get("confirmed_bar_time") or datetime.now(timezone.utc).isoformat()),
                signal_reason=str(candidate.get("reason", "实盘候选人工确认")),
                signal_context={**candidate, "entry_rule_audit": manual_rule_audit},
                order_id=str(result["ordId"]), algo_id="attached-tp-sl",
                entry_reference=float(candidate["entry_reference"]), stop_price=float(candidate["stop_loss"]),
                trailing_activation=float(candidate["take_profit"]), trailing_callback=0.0,
                branch=str(candidate.get("branch", "manual_live_candidate")),
            )
        finally:
            lifecycle.close()
        _queue_order_notice(
            f"LIVE {LIVE_PROFILES[slot]['label']}", str(result["ordId"]),
            reason=str(candidate.get("reason", "候选人工确认")), mode="LIVE实盘",
        )
        _live_status_text(
            slot,
            f"实盘订单已被OKX接受｜ordId={result['ordId']}｜clOrdId={result['clOrdId']}\n"
            "数量=0.05张｜主单已附带标记价止损与止盈。请立即在OKX核对成交和保护单状态。",
        )
    except Exception as exc:
        _live_status_text(slot, f"实盘人工下单未完成：{exc}")
    finally:
        # Never allow a second click with the same in-memory candidate.
        LIVE_PENDING_CANDIDATES[slot] = None
        _text(HANDLES.get(f"live_{slot}_approval", 0), "")
        _text(HANDLES.get(f"live_main_{slot}_approval", 0), "")


def _cancel_aggressive_opening_orders(client: OkxLiveAggressiveAdapter) -> None:
    snapshot = client.safety_snapshot()
    owned = [item for item in snapshot.get("orders", [])
             if str(item.get("clOrdId", "")).startswith("QBVAL")]
    if owned:
        client.cancel_orders(owned)


def _maybe_self_heal_live_dns(message: str, failures: int, database: Path) -> str:
    """Optimize DNS after explicit resolver errors or a sustained pre-request outage."""
    global LIVE_DNS_SELF_HEAL_LAST_ATTEMPT, LIVE_DNS_SELF_HEAL_LAST_VERIFIED
    explicit_dns_failure = is_dns_resolution_failure(message)
    sustained_pre_request_failure = (
        failures >= 6 and is_sustained_pre_request_network_failure(message))
    if (failures < LIVE_DNS_SELF_HEAL_FAILURE_THRESHOLD
            or not (explicit_dns_failure or sustained_pre_request_failure)):
        return ""
    now = time.monotonic()
    cooldown = (LIVE_DNS_SELF_HEAL_COOLDOWN_SECONDS if LIVE_DNS_SELF_HEAL_LAST_VERIFIED
                else LIVE_DNS_SELF_HEAL_FAILED_COOLDOWN_SECONDS)
    if now - LIVE_DNS_SELF_HEAL_LAST_ATTEMPT < cooldown:
        return "DNS self-heal is cooling down"
    LIVE_DNS_SELF_HEAL_LAST_ATTEMPT = now
    result = probe_configured_api_route()
    LIVE_DNS_SELF_HEAL_LAST_VERIFIED = result.verified
    audit_store = StateStore(database)
    try:
        audit_store.record_event("live_dns_self_heal", {
            "strategy_version": VALIDATION_VERSION,
            "failures": failures,
            "changed": result.changed,
            "verified": result.verified,
            "detail": result.detail,
            "selection_mode": "current_proxy_https_probe_no_system_dns_write",
            "new_orders_paused": True,
            "full_live_audit_required_before_resume": True,
        })
    finally:
        audit_store.close()
    return result.detail


def _five_second_top_short_worker(slot: str, cfg, credentials, database: Path,
                                  order_contracts) -> None:
    """Poll the isolated top-short trigger once per second."""
    private_client = OkxLiveAggressiveAdapter(
        credentials, timeout=2.0, contracts_per_unit=order_contracts)
    public_client = OkxDemoClient(timeout=2.0)
    stop_event = LIVE_ACCOUNT_STOPS[slot]
    while not stop_event.is_set():
        cycle_started = time.monotonic()
        if LIVE_FAST_ENTRY_ALLOWED.get(slot, False):
            try:
                result = execute_five_second_top_short_tick(
                    cfg, database, client=private_client,
                    public_client=public_client)
                if result.action == "submitted" and result.order_id:
                    _queue_order_notice(
                        f"LIVE {LIVE_PROFILES[slot]['label']} 5秒快线",
                        result.order_id, reason=result.reason, mode="LIVE实盘")
            except Exception as exc:
                # A fast-path outage is isolated from lifecycle protection and
                # the normal strategy. Record it and try a fresh scan; POST
                # outcome-unknown errors remain fatal in the adapter itself.
                audit_store = StateStore(database)
                try:
                    audit_store.record_event("five_second_fast_short_error", {
                        "strategy_version": VALIDATION_VERSION,
                        "reason": str(exc),
                        "elapsed_ms": round((time.monotonic() - cycle_started) * 1000, 2),
                    })
                finally:
                    audit_store.close()
                if "POST outcome unknown" in str(exc):
                    LIVE_FAST_ENTRY_ALLOWED[slot] = False
                    stop_event.set()
                    _set_live_control_state(slot, "faulted")
                    _live_status_text(
                        slot, "5秒快线订单POST结果未知，自动执行已停止；"
                        "请先在OKX核对clOrdId和持仓，禁止自动重发。")
                    return
        remaining = max(0.0, 1.0 - (time.monotonic() - cycle_started))
        stop_event.wait(remaining)


def _automatic_live_worker(slot: str) -> None:
    if slot not in LIVE_TRADING_SLOTS:
        LIVE_ACCOUNT_STOPS[slot].set()
        _set_live_control_state(slot, "stopped")
        _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}为观察/研究账户，自动执行已硬锁定。")
        return
    if slot in LIVE_TRADING_SLOTS:
        _account05_automatic_worker(slot)
        return
    try:
        database = LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3"
        settings = LiveAccountSettings(LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3")
        order_contracts = settings.order_contracts()
        credentials = LIVE_SESSION_CREDENTIALS.get(slot) or load_live_credentials(
            LIVE_CREDENTIAL_FILES[slot])
        if credentials is None:
            raise OkxError(f"请先绑定并审计{LIVE_PROFILES[slot]['label']} API。")
        startup_failures = 0
        while not LIVE_ACCOUNT_STOPS[slot].is_set():
            try:
                audit = OkxLiveReadOnlyClient(
                    credentials, timeout=LIVE_AGGRESSIVE_NETWORK_TIMEOUT).audit()
                break
            except OkxError as exc:
                if not is_retryable_aggressive_get_error(str(exc)):
                    raise
                startup_failures += 1
                delay = aggressive_network_backoff_seconds(startup_failures)
                _maybe_self_heal_live_dns(str(exc), startup_failures, database)
                _live_status_text(
                    slot,
                    f"启动审计暂时无法连接签名API，已失败{startup_failures}轮｜{delay}秒后自动重试｜"
                    "软件保持启动中，不需要人工反复点击；连接恢复后先完成只读账户与风险核验",
                )
                LIVE_ACCOUNT_STOPS[slot].wait(delay)
        else:
            return
        audit_error = aggressive_live_audit_error(audit)
        if audit_error:
            raise OkxError(audit_error)
        account = audit.get("account", {})
        leverage = {str(x.get("posSide")): str(x.get("lever")) for x in audit.get("leverage", [])}
        if account.get("acctLv") != "2" or account.get("posMode") != "long_short_mode":
            raise OkxError("激进型账户模式不符合单币种保证金和双向持仓要求。")
        if leverage.get("long") != "100" or leverage.get("short") != "100":
            raise OkxError("激进型多空全仓杠杆必须均为100倍。")
        if str(audit.get("minimum_order", {}).get("api_size_contracts")) != "0.01":
            raise OkxError("交易所最小张数不是0.01，自动执行拒绝启动。")
        base = load_config(CONFIG)
        base, strategy_source = apply_account_strategy(base, settings)
        client = OkxLiveAggressiveAdapter(
            credentials, timeout=LIVE_AGGRESSIVE_NETWORK_TIMEOUT,
            contracts_per_unit=order_contracts)
        cfg = replace(
            base,
            strategy=replace(base.strategy, risk_profile="aggressive"),
            risk=replace(base.risk, daily_loss_limit=.05, max_trades_per_day=100,
                         cooldown_seconds=0),
            okx=replace(base.okx, environment="live", leverage=100,
                        enable_demo_orders=True, validation_max_leverage=100,
                        validation_max_trades_per_day=100, validation_contracts=1),
        )
        cycle_store = LiveStateStore(
            LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3", slot)
        start_external_signal_receiver(database)
        if LIVE_ACCOUNT_STOPS[slot].is_set():
            return
        _set_live_control_state(slot, "running")
        initial_net = client.daily_net_pnl_usdt()
        if LIVE_PROFILES[slot]["daily_loss"] > 0 and initial_net <= -float(LIVE_PROFILES[slot]["daily_loss"]):
            _cancel_aggressive_opening_orders(client)
            raise OkxError(
                f"每日净亏损已达{initial_net:.2f} USDT，预埋开仓单已撤销并触发熔断。")
        # The top-short scanner is a separate one-second worker.  The normal
        # strategy may spend tens of seconds on protection reconciliation and
        # research; that work can no longer prevent a new local top from being
        # recognised and submitted promptly.
        LIVE_FAST_ENTRY_ALLOWED[slot] = True
        fast_thread = threading.Thread(
            target=_five_second_top_short_worker,
            args=(slot, cfg, credentials, database, order_contracts), daemon=True)
        fast_thread.start()
        network_failures = 0
        while not LIVE_ACCOUNT_STOPS[slot].is_set():
            try:
                if network_failures:
                    # Unattended equivalent of "audit saved API": a transient
                    # signed-GET outage must be followed by a fresh read-only
                    # account audit before strategy evaluation resumes.
                    recovery_audit = OkxLiveReadOnlyClient(
                        credentials, timeout=LIVE_AGGRESSIVE_NETWORK_TIMEOUT).audit()
                    audit_error = aggressive_live_audit_error(recovery_audit)
                    if audit_error:
                        raise OkxError(audit_error)
                net = client.daily_net_pnl_usdt()
                if LIVE_PROFILES[slot]["daily_loss"] > 0 and net <= -float(LIVE_PROFILES[slot]["daily_loss"]):
                    LIVE_FAST_ENTRY_ALLOWED[slot] = False
                    _cancel_aggressive_opening_orders(client)
                    raise OkxError(f"每日净亏损已达{net:.2f} USDT，预埋开仓单已撤销并触发熔断。")
                cycle_store.claim_automatic_cycle()
                near_result = monitor_pullbacks(
                    client, database, stop_event=LIVE_ACCOUNT_STOPS[slot],
                    can_enter=lambda: client.daily_net_pnl_usdt() >
                    -float(LIVE_PROFILES[slot]["daily_loss"]))
                if LIVE_ACCOUNT_STOPS[slot].is_set():
                    break
                result = execute_validation_tick(
                    cfg, credentials, database, client_override=client)
                schedule_completed_hour_scan(Path(database), cfg.okx.instruments[0])
                if result.action == "pullback_limit_submitted":
                    _live_status_text(slot, "回踩限价单已挂出，进入最长5分钟快速盯价；等待接近挂价")
                    near_result = monitor_pullbacks(
                        client, database, stop_event=LIVE_ACCOUNT_STOPS[slot],
                        can_enter=lambda: client.daily_net_pnl_usdt() >
                        -float(LIVE_PROFILES[slot]["daily_loss"]))
                if near_result.action == "submitted":
                    _queue_order_notice(f"LIVE {LIVE_PROFILES[slot]['label']}", near_result.order_id,
                                        reason=near_result.reason, mode="LIVE实盘")
                count_store = StateStore(database)
                try:
                    successful_trades = count_store.submitted_intent_count(
                        datetime.now(timezone.utc).date(), VALIDATION_VERSION)
                finally:
                    count_store.close()
                network_failures = 0
                LIVE_FAST_ENTRY_ALLOWED[slot] = True
            except ValueError as exc:
                if "cooldown" not in str(exc):
                    raise
                LIVE_ACCOUNT_STOPS[slot].wait(LIVE_AGGRESSIVE_POLL_SECONDS)
                continue
            except OkxError as exc:
                message = str(exc)
                if is_confirmed_aggressive_post_rejection(message):
                    rejection_store = StateStore(database)
                    try:
                        rejection_store.record_event("aggressive_live_post_rejected", {
                            "strategy_version": VALIDATION_VERSION,
                            "reason": message,
                            "loop_continues": True,
                            "retry_same_request": False,
                        })
                    finally:
                        rejection_store.close()
                    _live_status_text(
                        slot,
                        f"OKX明确拒绝本轮POST，未创建订单｜{message}｜"
                        f"不重试同一请求，{LIVE_AGGRESSIVE_POLL_SECONDS}秒后继续扫描新候选；自动总闸仍在运行",
                    )
                    LIVE_ACCOUNT_STOPS[slot].wait(LIVE_AGGRESSIVE_POLL_SECONDS)
                    continue
                if not is_retryable_aggressive_get_error(message):
                    raise
                network_failures += 1
                delay = aggressive_network_backoff_seconds(network_failures)
                _maybe_self_heal_live_dns(message, network_failures, database)
                _live_status_text(
                    slot,
                    f"OKX签名API暂时不可用，已连续失败{network_failures}轮｜{network_failure_summary(message)}｜{delay}秒后快速探测恢复｜"
                    "软件与激进型自动总闸仍在运行；网页可打开不代表签名API正常。网络恢复前不提交新订单，"
                    "恢复后立即重新核验账户、持仓、保护单与风险状态",
                )
                LIVE_ACCOUNT_STOPS[slot].wait(delay)
                continue
            except Exception as exc:
                # Public candle reads and executor wrappers can preserve the
                # safe GET-only error text while changing the exception type.
                # Recovery policy is based on whether an order POST could have
                # happened, not on the Python wrapper class.
                message = str(exc)
                if not is_retryable_aggressive_get_error(message):
                    raise
                network_failures += 1
                delay = aggressive_network_backoff_seconds(network_failures)
                _maybe_self_heal_live_dns(message, network_failures, database)
                _live_status_text(
                    slot,
                    f"OKX只读GET暂时不可用（包装异常已安全识别），已连续失败{network_failures}轮｜{network_failure_summary(message)}｜"
                    f"{delay}秒后快速探测恢复｜软件与激进型自动总闸仍在运行；"
                    "网络恢复前不提交新订单，恢复后先重新核验账户、持仓、保护单与风险状态",
                )
                LIVE_ACCOUNT_STOPS[slot].wait(delay)
                continue
            if result.action in {"submitted", "placed", "reanchored"} and result.order_id:
                order_ids = tuple(item.strip() for item in str(result.order_id).split(",") if item.strip())
                for order_id in order_ids:
                    _queue_order_notice(f"LIVE {LIVE_PROFILES[slot]['label']}", order_id, reason=result.reason, mode="LIVE实盘")
            if result.action in {"protection_recovery_warning", "protection_state_recovered",
                                 "stop_tightened", "post_fill_exit", "layer_consolidated",
                                 "unprotected_emergency_exit"}:
                # Market-direction text in `reason` changes every cycle.  It
                # must not turn one protection incident into dozens of unique
                # popups.  One action for one local protection/order id is
                # shown once per application session; the live status panel
                # continues to refresh without opening another window.
                notice_key = f"{result.action}|{result.order_id or 'local-protection-state'}"
                if notice_key not in LIVE_PROTECTION_NOTICE_KEYS:
                    LIVE_PROTECTION_NOTICE_KEYS.add(notice_key)
                    result_text = {
                        "protection_recovery_warning": "保护异常，需要核对",
                        "protection_state_recovered": "保护状态已自动恢复",
                        "stop_tightened": "灾难止损已缩至正常止损",
                        "post_fill_exit": "成交观察结束，已请求退出",
                        "layer_consolidated": "双层成交观察结束，已减为一层",
                        "unprotected_emergency_exit": "未保护持仓已紧急只减仓退出",
                    }[result.action]
                    _queue_order_notice(
                        f"LIVE {LIVE_PROFILES[slot]['label']}预埋单保护",
                        result.order_id or "本机保护状态",
                        reason=result.reason,
                        mode="LIVE实盘保护提示",
                        result_text=result_text,
                    )
            _live_status_text(
                slot,
                f"{LIVE_PROFILES[slot]['label']}自动执行运行中｜今日净盈亏={net:.4f} USDT｜成功交易={successful_trades}/100｜候选不计数\n"
                f"策略来源={strategy_source}｜下单数量={order_contracts}张｜本轮动作={_live_message_zh(result.action)}｜{_live_message_zh(result.reason)}",
            )
            LIVE_ACCOUNT_STOPS[slot].wait(LIVE_AGGRESSIVE_POLL_SECONDS)
    except Exception as exc:
        LIVE_ACCOUNT_STOPS[slot].set()
        _set_live_control_state(slot, "faulted")
        try:
            fault_path = write_live_fault(LIVE_WORKSPACE, slot, exc)
            detail = f"；堆栈记录：{fault_path}"
        except OSError:
            detail = "；本地堆栈记录写入失败"
        _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}自动执行已停止：{_live_message_zh(exc)}{detail}")
    finally:
        LIVE_FAST_ENTRY_ALLOWED[slot] = False
        if LIVE_ACCOUNT_STATES[slot] not in {"stopping", "faulted"}:
            _set_live_control_state(slot, "stopped")


def _toggle_live(slot: str) -> None:
    if slot not in LIVE_TRADING_SLOTS:
        _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}本版仅允许API绑定、只读审计和策略观察；自动下单硬锁定。")
        return
    state = LIVE_ACCOUNT_STATES[slot]
    if state == "stopping":
        return
    next_state = aggressive_click_transition(state)
    stop_event = LIVE_ACCOUNT_STOPS[slot]
    if next_state == "stopping":
        stop_event.set()
        _set_live_control_state(slot, "stopping")
        credentials = LIVE_SESSION_CREDENTIALS.get(slot) or load_live_credentials(LIVE_CREDENTIAL_FILES[slot])
        def stop_cleanup():
            if slot in LIVE_TRADING_SLOTS:
                _live_status_text(
                    slot, "账户05自动扫描已停止；已挂出的独立减仓止盈单保留在交易所，不撤销、不平仓。")
                _set_live_control_state(slot, "stopped")
                return
            if credentials is not None:
                try:
                    settings = LiveAccountSettings(LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3")
                    client = OkxLiveAggressiveAdapter(credentials, timeout=20, contracts_per_unit=settings.order_contracts())
                    _cancel_aggressive_opening_orders(client)
                    _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}自动执行已停止；预埋开仓单撤销请求已提交。")
                except Exception as exc:
                    _live_status_text(slot, f"自动循环已停止，但撤单需人工核对：{exc}")
            else:
                _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}自动执行已停止；本机未载入API。")
            _set_live_control_state(slot, "stopped")
        threading.Thread(target=stop_cleanup, daemon=True).start()
        return
    stop_event.clear()
    _set_live_control_state(slot, "starting")
    LIVE_ACCOUNT_THREADS[slot] = threading.Thread(target=_automatic_live_worker, args=(slot,), daemon=True)
    LIVE_ACCOUNT_THREADS[slot].start()


def _account05_automatic_worker(slot: str = "clone_research") -> None:
    stop_event = LIVE_ACCOUNT_STOPS[slot]
    ledger = None
    try:
        settings_path = LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3"
        strategy_path = LIVE_WORKSPACE / "profiles" / slot / "strategy.sqlite3"
        settings = LiveAccountSettings(settings_path)
        credentials = LIVE_SESSION_CREDENTIALS.get(slot) or load_live_credentials(
            LIVE_CREDENTIAL_FILES[slot])
        if credentials is None:
            raise OkxError(f"请先绑定并审计{LIVE_PROFILES[slot]['label']} API。")
        client = Account05LiveClient(credentials, timeout=LIVE_AGGRESSIVE_NETWORK_TIMEOUT)
        audit = _account05_audit_with_recovery(credentials, stop_event, phase="启动", slot=slot)
        if audit is None:
            return
        account = audit.get("account", {})
        if account.get("acctLv") != "2" or account.get("posMode") != "long_short_mode":
            raise OkxError(f"{LIVE_PROFILES[slot]['label']}必须使用单币种保证金模式和双向持仓模式")
        leverage = {str(x.get("posSide")): str(x.get("lever")) for x in audit.get("leverage", [])}
        if leverage.get("long") != "100" or leverage.get("short") != "100":
            _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}正在把ETH-USDT-SWAP多空全仓杠杆设置为100倍……")
            client.set_hedge_leverage_100()
            audit = _account05_audit_with_recovery(credentials, stop_event, phase="杠杆复核", slot=slot)
            if audit is None:
                return
            leverage = {str(x.get("posSide")): str(x.get("lever")) for x in audit.get("leverage", [])}
            if leverage.get("long") != "100" or leverage.get("short") != "100":
                raise OkxError(f"{LIVE_PROFILES[slot]['label']}多空杠杆未能确认到100倍，拒绝启动")
        ledger = Account05StateStore(strategy_path)
        snapshot = _account05_get_with_recovery(
            client.raw_snapshot, stop_event, phase="启动持仓快照")
        if snapshot is None:
            return
        existing = [row for row in snapshot["positions"]
                    if abs(float(row.get("pos") or 0)) > 0]
        if existing and not ledger.open_lots():
            raise OkxError("账户05已有交易所持仓但本机没有对应批次账本；拒绝自动接管，请先人工核对")
        _set_live_control_state(slot, "running")
        while not stop_event.is_set():
            audit = _account05_audit_with_recovery(credentials, stop_event, phase="运行中", slot=slot)
            if audit is None:
                return
            try:
                imported_manual = import_manual_orders(
                    client, ledger,
                    profit_points=Decimal(settings.addon_take_profit_points()))
                if imported_manual:
                    _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}已导入{imported_manual}笔手工成交到解套利润池审计")
            except Exception as exc:
                _live_status_text(slot, f"手工订单只读同步暂不可用：{exc}")
            result = _account05_get_with_recovery(
                lambda: _account05_evaluate_cycle(
                    client=client, ledger=ledger, settings=settings, audit=audit),
                stop_event, phase="行情/持仓/止盈读取", slot=slot)
            if result is None:
                return
            if result.order_ids:
                for order_id in result.order_ids:
                    _queue_order_notice(
                        f"LIVE {LIVE_PROFILES[slot]['label']}", order_id, reason=result.reason, mode="账户独立实盘")
            long_hist, long_open = ledger.base_audit_state(PositionSide.LONG)
            short_hist, short_open = ledger.base_audit_state(PositionSide.SHORT)
            base_audit_text = (
                f"基础仓审计库={strategy_path}｜"
                f"多单历史={'有' if long_hist else '无'}当前={'有' if long_open else '缺失'}｜"
                f"空单历史={'有' if short_hist else '无'}当前={'有' if short_open else '缺失'}")
            _live_status_text(
                slot,
                f"{LIVE_PROFILES[slot]['label']}自动执行运行中｜运行资金={settings.operating_capital_usdt()} USDT｜"
                f"小单止盈=浮盈严格>{settings.addon_take_profit_points()}点且MA5拐弯/快速末端｜"
                f"本轮={result.action}\n{base_audit_text}\n{result.reason}")
            stop_event.wait(5)
    except Exception as exc:
        stop_event.set()
        _set_live_control_state(slot, "faulted")
        try:
            fault_path = write_live_fault(LIVE_WORKSPACE, slot, exc)
            detail = f"；堆栈记录：{fault_path}"
        except OSError:
            detail = "；本地堆栈记录写入失败"
        _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}自动执行已停止：{exc}{detail}")
    finally:
        if ledger is not None:
            ledger.close()
        if LIVE_ACCOUNT_STATES[slot] not in {"stopping", "faulted"}:
            _set_live_control_state(slot, "stopped")


def _account05_audit_with_recovery(credentials, stop_event, *, phase: str, slot: str = "clone_research"):
    """Retry only pre-order, read-only audit failures until connectivity returns."""
    failures = 0
    while not stop_event.is_set():
        try:
            return OkxLiveReadOnlyClient(
                credentials, timeout=LIVE_AGGRESSIVE_NETWORK_TIMEOUT).audit()
        except Exception as exc:
            message = str(exc)
            if not is_retryable_aggressive_get_error(message):
                raise
            failures += 1
            delay = aggressive_network_backoff_seconds(failures)
            _live_status_text(
                slot,
                f"{LIVE_PROFILES[slot]['label']}{phase}只读审计暂时无法连接OKX，已失败{failures}轮｜"
                f"{delay}秒后自动重试｜不进入策略评估、不下单｜详情：{_live_message_zh(exc)}",
            )
            stop_event.wait(delay)
    return None


def _account05_get_with_recovery(operation, stop_event, *, phase: str, slot: str = "clone_research"):
    """Keep account 05 alive across failures from operations made only of safe GETs.

    The classifier deliberately rejects POST/unknown-outcome errors, so an
    uncertain order write still escapes immediately for manual reconciliation.
    """
    failures = 0
    while not stop_event.is_set():
        try:
            return operation()
        except Exception as exc:
            message = str(exc)
            if not is_retryable_aggressive_get_error(message):
                raise
            failures += 1
            delay = aggressive_network_backoff_seconds(failures)
            _live_status_text(
                slot,
                f"{LIVE_PROFILES[slot]['label']}{phase}暂时无法连接OKX，已失败{failures}轮｜"
                f"{delay}秒后自动恢复｜本轮不评估信号、不下单｜详情：{_live_message_zh(exc)}",
            )
            stop_event.wait(delay)
    return None


def _account05_evaluate_cycle(*, client, ledger, settings, audit):
    """Load a coherent multi-timeframe snapshot and evaluate one account-05 tick."""
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="account05-bars") as pool:
        futures = {
            bar: pool.submit(okx_recent_market, LIVE_INSTRUMENT, bar, 100)
            for bar in ("1m", "5m", "15m", "1H")
        }
        one, five, fifteen, hour = (
            futures[bar].result() for bar in ("1m", "5m", "15m", "1H"))
    return execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=audit,
        one=one, five=five, fifteen=fifteen, one_hour=hour)


def _save_live_size(slot: str) -> None:
    try:
        if LIVE_ACCOUNT_STATES[slot] != "stopped":
            raise ValueError("请先停止该账户自动执行，再修改下单数量")
        value = _input_value(f"live_{slot}_order_contracts")
        settings = LiveAccountSettings(LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3")
        saved = settings.set_order_contracts(value)
        _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}下单数量已保存为{saved}张；下次启动该账户时生效。")
    except Exception as exc:
        _live_status_text(slot, f"下单数量未保存：{exc}")


def _save_account05_settings(slot: str = "clone_research") -> None:
    try:
        settings = LiveAccountSettings(LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3")
        capital = settings.set_operating_capital_usdt(
            _input_value(f"live_{slot}_operating_capital"))
        points = settings.set_addon_take_profit_points(
            _input_value(f"live_{slot}_addon_tp_points"))
        long_slots, short_slots = settings.set_slot_capacities(
            _input_value(f"live_{slot}_long_slots"),
            _input_value(f"live_{slot}_short_slots"))
        extreme_contracts, slot_contracts = settings.set_account05_contracts(
            _input_value(f"live_{slot}_extreme_contracts"),
            _input_value(f"live_{slot}_slot_contracts"))
        _live_status_text(
            slot, f"{LIVE_PROFILES[slot]['label']}参数已保存｜运行资金={capital} USDT｜"
            f"多单槽位={long_slots}｜空单槽位={short_slots}｜"
            f"槽位单笔={slot_contracts}张｜极值反手={extreme_contracts}张（计入槽位）｜"
            f"小单浮盈严格超过{points}点后按MA5拐弯/快速末端止盈。")
    except Exception as exc:
        _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}参数未保存：{exc}")


def _toggle_account05_base_rebuild(slot: str = "clone_research") -> None:
    try:
        settings = LiveAccountSettings(LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3")
        enabled = settings.set_base_rebuild_enabled(not settings.base_rebuild_enabled())
        _text(HANDLES.get(f"live_{slot}_base_toggle", 0),
              ("基础仓：已开启（点击关闭）" if enabled
               else "基础仓：已关闭（点击开启）"))
        run_state = LIVE_ACCOUNT_STATES.get(slot, "stopped")
        effect = ("下一轮自动执行立即生效" if run_state == "running"
                  else "当前自动执行未运行；启动账户05后生效")
        _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}基础仓建立已{'开启' if enabled else '关闭'}；{effect}。")
    except Exception as exc:
        _live_status_text(slot, f"基础仓开关修改失败：{exc}")


def _account05_signal_worker() -> None:
    slot = "clone_research"
    try:
        one = okx_history_market((LIVE_INSTRUMENT,), "1m", 120)
        five = okx_history_market((LIVE_INSTRUMENT,), "5m", 120)
        fifteen = okx_history_market((LIVE_INSTRUMENT,), "15m", 120)
        hour = okx_history_market((LIVE_INSTRUMENT,), "1H", 120)
        signals = evaluate_account05_signals(one, five, fifteen, hour)
        trend = {"up": "上涨", "down": "下跌", "unclear": "横盘/不明确"}[signals.trend_5m.value]
        trigger_text = ("；".join(f"{item.identity}｜{item.reason}" for item in signals.triggers)
                        if signals.triggers else "六类触发均未成立")
        value = (
            f"账户05公开行情信号（只观察，不下单）｜5分钟={trend}\n{signals.trend_reason}\n"
            f"当前六类信号：{trigger_text}"
        )
        _live_status_text(slot, value)
    except Exception as exc:
        _live_status_text(slot, f"账户05信号刷新失败：{exc}")

def _refresh_live_main_dashboard() -> None:
    for slot in LIVE_SLOT_ORDER:
        if not LIVE_ACCOUNT_STOPS[slot].is_set():
            continue
        credentials = LIVE_SESSION_CREDENTIALS.get(slot) or load_live_credentials(
            LIVE_CREDENTIAL_FILES[slot])
        if credentials is None:
            _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}：API尚未绑定。")
            continue
        try:
            report = OkxLiveReadOnlyClient(credentials, timeout=20).audit()
            _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}｜" + _format_live_audit(report))
        except Exception as exc:
            if is_retryable_aggressive_get_error(str(exc)):
                _live_status_text(
                    slot,
                    f"{LIVE_PROFILES[slot]['label']}：OKX签名API临时无法连接，本次只读审计未完成｜"
                    "这不是账户或策略故障，也不会下单｜自动总闸启动后会持续退避重试｜"
                    f"详情：{_live_message_zh(exc)}",
                )
            else:
                _live_status_text(slot, f"{LIVE_PROFILES[slot]['label']}审计失败：{exc}")


def _condition_log_worker() -> None:
    database = LIVE_WORKSPACE / "profiles" / "aggressive" / "strategy.sqlite3"
    while HANDLES.get("main", 0) and user32.IsWindow(HANDLES["main"]):
        try:
            records = read_recent_condition_event_records(database, 80)
            _text(HANDLES.get("condition_log_latest", 0),
                  _latest_condition_text(records[0][1] if records else None))
        except (OSError, ValueError, sqlite3.Error) as exc:
            _text(HANDLES.get("condition_log_status", 0), f"实时日志读取暂不可用：{exc}")
        time.sleep(5)


def _condition_log_selected_day() -> date:
    value = HANDLES.get("condition_log_day")
    return value if isinstance(value, date) else datetime.now(timezone(timedelta(hours=8))).date()


def _refresh_condition_log_page() -> None:
    database = LIVE_WORKSPACE / "profiles" / "aggressive" / "strategy.sqlite3"
    selected = _condition_log_selected_day()
    try:
        records = read_condition_events_for_beijing_day(database, selected)
        _listview_rows(HANDLES.get("condition_log_table", 0),
                       _condition_history_rows([row for _event_id, row in records]))
        _text(HANDLES.get("condition_log_status", 0),
              f"北京时间 {selected.isoformat()}｜全天{len(records)}条事件｜一页一天｜"
              "只有上方最新一条每5秒刷新，本页不自动重载")
    except (OSError, ValueError, sqlite3.Error) as exc:
        _text(HANDLES.get("condition_log_status", 0), f"当日运行日志读取暂不可用：{exc}")


def _change_condition_log_day(days: int) -> None:
    HANDLES["condition_log_day"] = _condition_log_selected_day() + timedelta(days=days)
    _refresh_condition_log_page()


def _show_condition_log_window() -> None:
    existing = HANDLES.get("condition_log_window", 0)
    if existing and user32.IsWindow(existing):
        user32.ShowWindow(existing, SW_MAXIMIZE)
        user32.SetForegroundWindow(existing)
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"运行日志｜一页一天｜v{APP_VERSION}",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1280, 760,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None)
    HANDLES["condition_log_window"] = hwnd
    HANDLES["condition_log_day"] = datetime.now(timezone(timedelta(hours=8))).date()
    HANDLES["condition_log_status"] = _create(
        hwnd, "STATIC", "正在读取当日全部事件……", SS_LEFT, 18, 12, 900, 28)
    HANDLES["condition_log_latest"] = _create(
        hwnd, "EDIT", "最新一条（每5秒刷新）：等待事件",
        WS_BORDER | ES_MULTILINE | ES_AUTOVSCROLL | ES_READONLY, 18, 44, 1220, 82)
    HANDLES["condition_log_table"] = _listview(hwnd, (
        ("北京时间", 140), ("结构/阶段", 235), ("结果", 95),
        ("全天触发证据或挡单原因", 900), ("订单号", 105)), 18, 136, 1220, 520)
    HANDLES["condition_log_previous"] = _create(
        hwnd, "BUTTON", "上一天", WS_TABSTOP | BS_PUSHBUTTON, 18, 672, 110, 34,
        ID_CONDITION_LOG_PREVIOUS)
    HANDLES["condition_log_today"] = _create(
        hwnd, "BUTTON", "今天", WS_TABSTOP | BS_PUSHBUTTON, 138, 672, 90, 34,
        ID_CONDITION_LOG_TODAY)
    HANDLES["condition_log_next"] = _create(
        hwnd, "BUTTON", "下一天", WS_TABSTOP | BS_PUSHBUTTON, 238, 672, 110, 34,
        ID_CONDITION_LOG_NEXT)
    HANDLES["condition_log_refresh"] = _create(
        hwnd, "BUTTON", "刷新本页", WS_TABSTOP | BS_PUSHBUTTON, 358, 672, 120, 34,
        ID_CONDITION_LOG_REFRESH)
    HANDLES["condition_log_close"] = _create(
        hwnd, "BUTTON", "关闭", WS_TABSTOP | BS_PUSHBUTTON, 1118, 672, 120, 34,
        ID_CONDITION_LOG_CLOSE)
    _refresh_condition_log_page()
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def _show_live_console() -> None:
    existing = HANDLES.get("live_window", 0)
    if existing and user32.IsWindow(existing):
        user32.SetForegroundWindow(existing)
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", "LIVE 实盘控制台｜五个独立账户",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 980, 820,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["live_window"] = hwnd
    _create(hwnd, "STATIC", "LIVE 实盘｜5个账户｜5套独立API与数据库｜ETH-USDT-SWAP",
            SS_LEFT, 22, 18, 900, 30)
    _create(hwnd, "STATIC",
            "账户01–03运行我们的策略；账户04用于第三方策略观察，账户05用于后续独立复刻优化。",
            SS_LEFT, 22, 52, 900, 28)
    open_ids = {
        "aggressive": ID_LIVE_AGGRESSIVE_OPEN,
        "conservative": ID_LIVE_CONSERVATIVE_OPEN,
        "prudent": ID_LIVE_PRUDENT_OPEN,
        "external_observer": ID_LIVE_ACCOUNT04_OPEN,
        "clone_research": ID_LIVE_ACCOUNT05_OPEN,
    }
    for index, slot in enumerate(LIVE_SLOT_ORDER):
        spec = LIVE_PROFILES[slot]
        cooldown_text = ("无时间冷却" if int(spec["cooldown_minutes"]) == 0
                         else f"冷却{spec['cooldown_minutes']}分钟")
        limit_text = "单笔/每日亏损不限制" if spec["max_loss"] <= 0 or spec["daily_loss"] <= 0 else f"单笔≤{spec['max_loss']:.2f}U｜每日≤{spec['daily_loss']:.2f}U"
        y = 92 + index * 112
        header = (f"实盘{spec['label']}｜{spec.get('purpose')}｜本软件自动下单已锁定"
                  if not spec.get("execution_enabled", True) else
                  f"实盘{spec['label']}｜{limit_text}｜"
                  f"每日最多{spec['max_successful_trades']}次成功交易｜候选不计数｜{cooldown_text}")
        _create(hwnd, "STATIC", header,
                SS_LEFT, 22, y, 690, 28)
        bound = LIVE_SESSION_CREDENTIALS.get(slot) is not None
        HANDLES[f"live_console_{slot}_status"] = _create(
            hwnd, "STATIC", f"{spec['label']}：{'已绑定' if bound else '尚未绑定'}｜独立自动开关",
            SS_LEFT, 22, y + 34, 690, 28)
        _create(hwnd, "BUTTON", f"打开{spec['label']}API", WS_TABSTOP | BS_PUSHBUTTON,
                730, y + 4, 190, 38, open_ids[slot])
    _create(hwnd, "STATIC",
            "现有凭据仍保留在原内部槽位，无需迁移；新增账户04、05各自绑定独立API。\n"
            "凭据均由当前Windows用户DPAPI分别加密；禁止五个槽位绑定同一个API Key。\n"
            "账户04严格只读观察；账户05运行独立双向对冲与六类小单策略。",
            SS_LEFT, 22, 656, 900, 60,
    )
    _create(hwnd, "STATIC",
            "安全边界：五个API Key禁止复用；账户05使用专用执行器，不运行账户01–03策略。",
            SS_LEFT, 22, 724, 900, 30,
    )


def _show_live_slot_window(slot: str) -> None:
    key = f"live_{slot}_window"
    existing = HANDLES.get(key, 0)
    if existing and user32.IsWindow(existing):
        user32.SetForegroundWindow(existing)
        return
    spec = LIVE_PROFILES[slot]
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"LIVE 实盘账户｜{spec['label']}｜独立自动执行",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 980, 760,
        HANDLES.get("live_window", HANDLES.get("main", 0)), None,
        kernel32.GetModuleHandleW(None), None,
    )
    HANDLES[key] = hwnd
    execution_enabled = spec.get("execution_enabled", True)
    purpose = spec.get("purpose", "共享策略基线＋可选单账户覆盖")
    _create(hwnd, "STATIC", f"{spec['label']}｜独立API｜{purpose}",
            SS_LEFT, 22, 18, 900, 30)
    for label, field, x, width in (
        ("API Key", "api_key", 22, 190), ("Secret Key", "secret_key", 316, 190),
        ("Passphrase", "passphrase", 610, 230),
    ):
        _create(hwnd, "STATIC", label, SS_LEFT, x, 70, 90, 24)
        HANDLES[_live_field_name(slot, field)] = _create(
            hwnd, "EDIT", "", WS_TABSTOP | WS_BORDER | ES_PASSWORD | ES_AUTOHSCROLL,
            x + 92, 66, width, 28)
    _create(hwnd, "BUTTON", "验证并加密保存", WS_TABSTOP | BS_PUSHBUTTON,
            22, 118, 190, 38, ID_LIVE_SAVE)
    _create(hwnd, "BUTTON", "审计已保存API", WS_TABSTOP | BS_PUSHBUTTON,
            226, 118, 170, 38, ID_LIVE_AUDIT)
    _create(hwnd, "BUTTON", "删除本机API", WS_TABSTOP | BS_PUSHBUTTON,
            410, 118, 160, 38, ID_LIVE_DELETE)
    if execution_enabled and slot != "clone_research":
        _create(hwnd, "STATIC", "下单数量（张）", SS_LEFT, 584, 92, 125, 24)
        settings = LiveAccountSettings(LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3")
        HANDLES[f"live_{slot}_order_contracts"] = _create(
            hwnd, "EDIT", settings.order_contracts(), WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL,
            584, 118, 95, 38)
        _create(hwnd, "BUTTON", "保存数量", WS_TABSTOP | BS_PUSHBUTTON,
                688, 118, 110, 38, ID_LIVE_SAVE_SIZE)
    _create(hwnd, "BUTTON", "OKX API管理", WS_TABSTOP | BS_PUSHBUTTON,
            768, 118, 150, 38, ID_LIVE_API_WEB)
    HANDLES[f"live_{slot}_status"] = _create(
        hwnd, "STATIC", "尚未执行审计；绑定API不会触发交易。",
        SS_LEFT, 22, 180, 900, 250)
    if execution_enabled:
        settings = LiveAccountSettings(LIVE_WORKSPACE / "profiles" / slot / "state.sqlite3")
        _create(hwnd, "STATIC", "运行资金(USDT)", SS_LEFT, 22, 496, 130, 24)
        HANDLES[f"live_{slot}_operating_capital"] = _create(
            hwnd, "EDIT", settings.operating_capital_usdt(),
            WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL, 154, 492, 120, 30)
        _create(hwnd, "STATIC", "小单止盈门槛(至少10点)", SS_LEFT, 300, 496, 190, 24)
        HANDLES[f"live_{slot}_addon_tp_points"] = _create(
            hwnd, "EDIT", settings.addon_take_profit_points(),
            WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL, 452, 492, 70, 30)
        _create(hwnd, "STATIC", "多单槽位容量", SS_LEFT, 22, 536, 105, 24)
        HANDLES[f"live_{slot}_long_slots"] = _create(
            hwnd, "EDIT", settings.long_slot_capacity(),
            WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL, 126, 532, 64, 30)
        _create(hwnd, "STATIC", "空单槽位容量", SS_LEFT, 210, 536, 105, 24)
        HANDLES[f"live_{slot}_short_slots"] = _create(
            hwnd, "EDIT", settings.short_slot_capacity(),
            WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL, 314, 532, 64, 30)
        _create(hwnd, "STATIC", "极值反手(张)", SS_LEFT, 390, 536, 110, 24)
        HANDLES[f"live_{slot}_extreme_contracts"] = _create(
            hwnd, "EDIT", settings.extreme_rotation_contracts(),
            WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL, 500, 532, 64, 30)
        _create(hwnd, "STATIC", "槽位单笔(张)", SS_LEFT, 575, 536, 100, 24)
        HANDLES[f"live_{slot}_slot_contracts"] = _create(
            hwnd, "EDIT", settings.slot_contracts(),
            WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL, 675, 532, 64, 30)
        _create(hwnd, "BUTTON", f"保存{spec['label']}参数", WS_TABSTOP | BS_PUSHBUTTON,
                544, 490, 170, 34, ID_ACCOUNT05_SAVE_SETTINGS)
        _create(hwnd, "STATIC", "基础仓控制", SS_LEFT, 720, 466, 120, 24)
        HANDLES[f"live_{slot}_base_toggle"] = _create(
            hwnd, "BUTTON",
            ("基础仓：已开启（点击关闭）" if settings.base_rebuild_enabled()
                   else "基础仓：已关闭（点击开启）"),
            WS_TABSTOP | BS_PUSHBUTTON, 720, 490, 250, 38, ID_ACCOUNT05_BASE_TOGGLE)
        _create(hwnd, "BUTTON", "刷新趋势/信号", WS_TABSTOP | BS_PUSHBUTTON,
                730, 450, 188, 34, ID_ACCOUNT05_REFRESH_SIGNALS)
    if execution_enabled:
        _create(hwnd, "STATIC",
                ("账户05按5秒快速扫描；基础仓服务器止盈保留，小单MA5止盈需软件运行。" if slot == "clone_research" else
                 "点击启动后按10秒正常轮询；当前策略来源和下单数量会写入运行状态。"),
                SS_LEFT, 22, 440, 570, 34)
        HANDLES[f"live_child_{slot}_toggle"] = _create(
            hwnd, "BUTTON", aggressive_button_text(LIVE_ACCOUNT_STATES[slot]), WS_TABSTOP | BS_PUSHBUTTON,
            614, 438, 304, 38, ID_LIVE_AGGRESSIVE_TOGGLE)
    operation_text = "自动执行独立启停；停止时撤销本账户本策略预埋开仓单。"
    cooldown_text = ("无时间冷却" if int(spec["cooldown_minutes"]) == 0
                     else f"冷却{spec['cooldown_minutes']}分钟")
    limit_text = ("单笔/每日亏损不限制" if spec["max_loss"] <= 0 or spec["daily_loss"] <= 0
                  else f"单笔计划亏损≤{spec['max_loss']:.2f} USDT｜每日亏损≤{spec['daily_loss']:.2f} USDT")
    limits_text = ((
        f"{spec['label']}限制：核心周期=1分钟确认＋5分钟结构＋15分钟方向｜高周期只作背景提示｜"
        f"{limit_text}｜"
        f"每日最多{spec['max_successful_trades']}次成功交易（候选不计数）｜{cooldown_text}。\n"
        f"{operation_text}"
    ) if execution_enabled else
        f"{spec['label']}本版仅提供独立API绑定、只读审计和独立数据库。\n"
        "本软件不会在该账户自动开仓、加仓、撤单或平仓。")
    _create(hwnd, "STATIC", limits_text, SS_LEFT, 22,
            548 if slot == "clone_research" else 500, 900, 92)


def _live_slot_for_window(hwnd: int) -> str:
    for slot in LIVE_SLOT_ORDER:
        if hwnd == HANDLES.get(f"live_{slot}_window"):
            return slot
    raise OkxError("实盘API操作必须在对应子账户窗口执行。")


def _show_first_api_window() -> None:
    existing = HANDLES.get("first_api_window", 0)
    if existing and user32.IsWindow(existing):
        user32.SetForegroundWindow(existing)
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", "策略01｜独立验证交易｜OKX Demo API",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 900, 560,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["first_api_window"] = hwnd
    _create(hwnd, "STATIC", "策略01：独立验证交易凭据与操作；绑定API不会自动下单，必须单独解锁。", SS_LEFT, 22, 20, 820, 28)
    for label, name, x in (("API Key", "api_key", 22), ("Secret Key", "secret_key", 310), ("Passphrase", "passphrase", 610)):
        _create(hwnd, "STATIC", label, SS_LEFT, x, 68, 90, 24)
        HANDLES[name] = _create(hwnd, "EDIT", "", WS_TABSTOP | WS_BORDER | ES_PASSWORD | ES_AUTOHSCROLL, x + 92, 64, 180 if x < 610 else 220, 28)
    _create(hwnd, "BUTTON", "临时应用（不保存）", WS_TABSTOP | BS_PUSHBUTTON, 22, 120, 180, 36, ID_CREDENTIALS)
    _create(hwnd, "BUTTON", "检查公开合约", WS_TABSTOP | BS_PUSHBUTTON, 216, 120, 150, 36, ID_PUBLIC)
    _create(hwnd, "BUTTON", "验证 Demo API", WS_TABSTOP | BS_PUSHBUTTON, 380, 120, 150, 36, ID_ACCOUNT)
    HANDLES["leverage"] = _create(hwnd, "BUTTON", "设置 Demo 10×", WS_TABSTOP | BS_PUSHBUTTON, 544, 120, 150, 36, ID_LEVERAGE)
    _create(hwnd, "BUTTON", "加密保存/更换 API", WS_TABSTOP | BS_PUSHBUTTON, 22, 166, 200, 34, ID_SAVE_CREDENTIALS)
    _create(hwnd, "BUTTON", "删除本机 API", WS_TABSTOP | BS_PUSHBUTTON, 236, 166, 160, 34, ID_DELETE_CREDENTIALS)
    HANDLES["okx"] = _create(hwnd, "STATIC", "请在此窗口完成策略01 Demo API操作。", SS_LEFT, 22, 220, 820, 44)
    _create(hwnd, "STATIC", "策略01验证交易：每次1张、最高10×、只允许OKX Demo；重启后自动恢复锁定。", SS_LEFT, 22, 286, 820, 28)
    HANDLES["demo_unlock"] = _create(hwnd, "BUTTON", "验证交易已解锁" if DEMO_UNLOCKED else "解锁策略01验证交易", WS_TABSTOP | BS_PUSHBUTTON, 22, 330, 230, 38, ID_DEMO_UNLOCK)
    if DEMO_UNLOCKED:
        user32.EnableWindow(HANDLES["demo_unlock"], False)
    _create(hwnd, "STATIC", "解锁后回到主界面点击“开始观察”，统一观察策略01/02/03。", SS_LEFT, 270, 334, 560, 32)


def _second_api_fields() -> OkxCredentials:
    values = tuple(_input_value(name) for name in ("api2_key", "api2_secret", "api2_passphrase"))
    if not all(values):
        raise OkxError("请完整输入策略02的 Demo API Key、Secret Key 和 Passphrase。")
    return OkxCredentials(*values)


def _clear_second_api_fields() -> None:
    for name in ("api2_key", "api2_secret", "api2_passphrase"):
        _text(HANDLES[name], "")


def _second_api_worker(action: str) -> None:
    global SECOND_SESSION_CREDENTIALS
    try:
        if SECOND_DEMO_UNLOCKED and action in {"save", "delete"}:
            raise OkxError("策略02自动测试已解锁，运行期间禁止更换或删除API；请重启并保持空仓后操作")
        if action == "save":
            candidate = _second_api_fields()
            existing = load_credentials(SECOND_CREDENTIAL_FILE)
            if existing is not None:
                OkxDemoClient(existing).assert_account_clear()
            OkxDemoClient(candidate).check_demo_account()
            save_credentials(SECOND_CREDENTIAL_FILE, candidate)
            SECOND_SESSION_CREDENTIALS = candidate
            _clear_second_api_fields()
            value = "策略02独立 Demo API 已验证并由 Windows 加密保存。"
        elif action == "verify":
            candidate = SECOND_SESSION_CREDENTIALS or load_credentials(SECOND_CREDENTIAL_FILE) or _second_api_fields()
            OkxDemoClient(candidate).check_demo_account()
            SECOND_SESSION_CREDENTIALS = candidate
            value = "策略02独立 Demo API 验证成功（仅模拟盘）。"
        else:
            existing = load_credentials(SECOND_CREDENTIAL_FILE)
            if existing is None:
                raise OkxError("策略02尚未保存独立 Demo API")
            OkxDemoClient(existing).assert_account_clear()
            delete_credentials(SECOND_CREDENTIAL_FILE)
            SECOND_SESSION_CREDENTIALS = None
            value = "策略02本机加密 API 已删除；欧易网页API未被撤销。"
        _text(HANDLES["api2_status"], value)
        if "strategy2_api_status" in HANDLES:
            _text(HANDLES["strategy2_api_status"], value)
    except Exception as exc:
        _text(HANDLES["api2_status"], f"策略02 API操作失败：{exc}")


def _switch_second_timeframe_worker() -> None:
    try:
        if OBSERVING:
            raise OkxError("请先暂停观察，再切换策略02周期")
        value = _input_value("api2_timeframe")
        if value not in {"1m", "5m", "15m", "30m", "1H"}:
            raise ValueError("周期只能是1m、5m、15m、30m或1H")
        credentials = SECOND_SESSION_CREDENTIALS or load_credentials(SECOND_CREDENTIAL_FILE)
        if credentials is None:
            raise OkxError("请先绑定并验证策略02独立Demo API")
        OkxDemoClient(credentials).assert_account_clear()
        original = RANGE_CONFIG.read_text(encoding="utf-8")
        updated = re.sub(r'(?m)^pivot_timeframe\s*=.*$', f'pivot_timeframe = "{value}"', original)
        RANGE_CONFIG.write_text(updated, encoding="utf-8")
        _text(HANDLES["api2_status"], f"策略02区间周期已切换为 {value}；账户持仓与委托已确认全部为空。")
    except Exception as exc:
        _text(HANDLES["api2_status"], f"策略02周期切换失败：{exc}")


def _unlock_second_demo_worker() -> None:
    global SECOND_DEMO_UNLOCKED, SECOND_SESSION_CREDENTIALS
    try:
        credentials = SECOND_SESSION_CREDENTIALS or load_credentials(SECOND_CREDENTIAL_FILE)
        if credentials is None:
            raise OkxError("请先绑定并验证策略02独立Demo API")
        SECOND_SESSION_CREDENTIALS = credentials
        client = OkxDemoClient(credentials)
        client.require_swap_trading_mode()
        _assert_clear_except_owned_snipers(client, "QBR")
        cfg = load_config(RANGE_CONFIG)
        for instrument in cfg.okx.instruments:
            client.set_leverage(10, instrument, "long")
            client.set_leverage(10, instrument, "short")
        SECOND_DEMO_UNLOCKED = True
        restore_text = _restore_after_unlock("strategy_02", credentials, "QBR",
                                             STRATEGY_RULES[1]["version"], cfg.okx.instruments[0])
        _text(HANDLES["second_demo_unlock"], "策略02自动测试已解锁")
        _text(HANDLES["api2_status"], f"策略02自动测试已解锁｜{restore_text}")
        _text(HANDLES["strategy2_api_status"], "策略02独立模拟盘接口：已绑定｜自动测试已解锁")
    except Exception as exc:
        _message(str(exc), "策略02解锁失败", True)


def _unlock_second_demo() -> None:
    answer = user32.MessageBoxW(
        HANDLES.get("second_api_window", HANDLES.get("main", 0)),
        "策略02自动测试只操作独立OKX Demo账户。\n\n"
        "解锁前会确认双向持仓模式，并要求全部持仓、普通委托和策略委托为空。\n"
        "首轮每次仅1张、最高10倍、5分钟冷却；使用ATR标记价止损和服务器端移动止盈。\n"
        "只有1张合约，首轮不能执行50%分批止盈。重启客户端后自动恢复锁定。\n\n是否继续？",
        "解锁策略02自动下单测试", 0x24,
    )
    if answer == 6:
        threading.Thread(target=_serialized_unlock, args=(_unlock_second_demo_worker,), daemon=True).start()


def _show_second_api_window() -> None:
    existing = HANDLES.get("second_api_window", 0)
    if existing and user32.IsWindow(existing):
        user32.SetForegroundWindow(existing)
        return
    hwnd = user32.CreateWindowExW(
        0, "CodexQuantBotWindow", f"策略02｜{RANGE_STRATEGY_VERSION}｜独立OKX Demo API",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 720, 540,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None,
    )
    HANDLES["second_api_window"] = hwnd
    _create(hwnd, "STATIC", "本接口仅用于策略02模拟盘；凭据与策略01分开加密保存。", SS_LEFT, 22, 20, 650, 26)
    _create(hwnd, "STATIC", "API Key", SS_LEFT, 22, 62, 90, 24)
    HANDLES["api2_key"] = _create(hwnd, "EDIT", "", WS_TABSTOP | WS_BORDER | ES_PASSWORD | ES_AUTOHSCROLL, 120, 58, 540, 28)
    _create(hwnd, "STATIC", "Secret Key", SS_LEFT, 22, 104, 90, 24)
    HANDLES["api2_secret"] = _create(hwnd, "EDIT", "", WS_TABSTOP | WS_BORDER | ES_PASSWORD | ES_AUTOHSCROLL, 120, 100, 540, 28)
    _create(hwnd, "STATIC", "Passphrase", SS_LEFT, 22, 146, 90, 24)
    HANDLES["api2_passphrase"] = _create(hwnd, "EDIT", "", WS_TABSTOP | WS_BORDER | ES_PASSWORD | ES_AUTOHSCROLL, 120, 142, 540, 28)
    _create(hwnd, "BUTTON", "验证策略02 Demo API", WS_TABSTOP | BS_PUSHBUTTON, 22, 192, 190, 36, ID_SECOND_API_VERIFY)
    _create(hwnd, "BUTTON", "加密保存/更换", WS_TABSTOP | BS_PUSHBUTTON, 226, 192, 160, 36, ID_SECOND_API_SAVE)
    _create(hwnd, "BUTTON", "删除策略02本机API", WS_TABSTOP | BS_PUSHBUTTON, 400, 192, 190, 36, ID_SECOND_API_DELETE)
    saved = "已绑定并自动载入策略02独立Demo API。" if SECOND_SESSION_CREDENTIALS else "尚未绑定策略02独立Demo API。"
    _create(hwnd, "STATIC", "策略02区间周期", SS_LEFT, 22, 250, 110, 24)
    HANDLES["api2_timeframe"] = _create(hwnd, "EDIT", load_config(RANGE_CONFIG).strategy.pivot_timeframe, WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL, 142, 246, 100, 28)
    _create(hwnd, "BUTTON", "空仓校验并切换周期", WS_TABSTOP | BS_PUSHBUTTON, 258, 242, 200, 36, ID_SECOND_TIMEFRAME)
    HANDLES["api2_status"] = _create(hwnd, "STATIC", saved, SS_LEFT, 22, 298, 638, 54)
    _create(hwnd, "STATIC", "绑定API不会自动下单；必须单独解锁。切换账户或周期前请保持全部持仓和委托为零。", SS_LEFT, 22, 362, 638, 46)
    unlock_text = "策略02自动测试已解锁" if SECOND_DEMO_UNLOCKED else "解锁策略02自动下单测试"
    HANDLES["second_demo_unlock"] = _create(hwnd, "BUTTON", unlock_text, WS_TABSTOP | BS_PUSHBUTTON, 22, 420, 250, 38, ID_SECOND_DEMO_UNLOCK)
    _create(hwnd, "STATIC", "解锁后回到主界面点击“开始观察”；关闭或重启客户端会自动恢复锁定。", SS_LEFT, 292, 424, 368, 40)


def _third_fields() -> OkxCredentials:
    values = tuple(_input_value(name) for name in ("api3_key", "api3_secret", "api3_passphrase"))
    if not all(values):
        raise OkxError("请完整输入策略03的 Demo API Key、Secret Key 和 Passphrase。")
    return OkxCredentials(*values)


def _third_api_worker(action: str) -> None:
    global THIRD_SESSION_CREDENTIALS
    try:
        if THIRD_DEMO_UNLOCKED and action in {"save", "delete"}:
            raise OkxError("策略03自动测试已解锁，运行期间禁止更换或删除API；请重启并保持空仓后操作")
        if action == "save":
            candidate = _third_fields()
            existing = load_credentials(THIRD_CREDENTIAL_FILE)
            if existing is not None:
                OkxDemoClient(existing).assert_account_clear()
            OkxDemoClient(candidate).check_demo_account()
            save_credentials(THIRD_CREDENTIAL_FILE, candidate)
            THIRD_SESSION_CREDENTIALS = candidate
            value = "策略03独立Demo API已验证并由Windows加密保存；自动下单默认锁定。"
        elif action == "verify":
            candidate = THIRD_SESSION_CREDENTIALS or load_credentials(THIRD_CREDENTIAL_FILE) or _third_fields()
            OkxDemoClient(candidate).check_demo_account()
            THIRD_SESSION_CREDENTIALS = candidate
            value = "策略03 Demo API验证成功（仅模拟盘，自动下单默认锁定）。"
        else:
            existing = load_credentials(THIRD_CREDENTIAL_FILE)
            if existing is None:
                raise OkxError("策略03尚未保存独立Demo API。")
            OkxDemoClient(existing).assert_account_clear()
            delete_credentials(THIRD_CREDENTIAL_FILE)
            THIRD_SESSION_CREDENTIALS = None
            value = "策略03本机加密API已删除。"
        _text(HANDLES["api3_status"], value)
        _text(HANDLES["strategy3_api_status"], value)
    except Exception as exc:
        _text(HANDLES["api3_status"], f"策略03 API操作失败：{exc}")


def _unlock_third_demo_worker() -> None:
    global THIRD_DEMO_UNLOCKED, THIRD_SESSION_CREDENTIALS
    try:
        credentials = THIRD_SESSION_CREDENTIALS or load_credentials(THIRD_CREDENTIAL_FILE)
        if credentials is None:
            raise OkxError("请先绑定并验证策略03独立Demo API")
        client = OkxDemoClient(credentials)
        client.require_swap_trading_mode()
        _assert_clear_except_owned_snipers(client, "QBM")
        client.set_leverage(10, "ETH-USDT-SWAP", "long")
        client.set_leverage(10, "ETH-USDT-SWAP", "short")
        THIRD_SESSION_CREDENTIALS = credentials
        THIRD_DEMO_UNLOCKED = True
        restore_text = _restore_after_unlock("strategy_03", credentials, "QBM",
                                             STRATEGY_RULES[2]["version"], "ETH-USDT-SWAP")
        _text(HANDLES["third_demo_unlock"], "策略03自动测试已解锁")
        value = f"策略03独立模拟盘接口：已绑定｜自动测试已解锁｜{restore_text}"
        _text(HANDLES["api3_status"], value)
        _text(HANDLES["strategy3_api_status"], value)
    except Exception as exc:
        _message(str(exc), "策略03解锁失败", True)


def _unlock_third_demo() -> None:
    answer = user32.MessageBoxW(
        HANDLES.get("third_api_window", HANDLES.get("main", 0)),
        "策略03只操作独立OKX Demo账户。解锁前将确认双向持仓模式、账户空仓且无委托，"
        "并把ETH-USDT永续多空杠杆设为最高10倍。\n"
        "5分钟趋势翻转并确认回踩后，用1分钟K线在回踩区上沿转弱做空/下沿转强做多；结构止损放在MA20回踩区外；"
        "订单附带标记价0.15%止损及盈利0.20%启动、回撤0.05%的移动止盈。\n\n是否继续？",
        "解锁策略03自动下单测试", 0x24,
    )
    if answer == 6:
        threading.Thread(target=_serialized_unlock, args=(_unlock_third_demo_worker,), daemon=True).start()


def _third_observe_worker() -> None:
    try:
        signal = observe_ma20_retest_once()
        _text(HANDLES["status"],
              f"策略03：{signal.action}｜5m状态 {signal.five_minute_state}｜5m MA20 {signal.ma20_5m:.2f}｜"
              f"5m回踩区 {signal.retest_low:.2f}-{signal.retest_high:.2f}｜{signal.reason}")
    except Exception as exc:
        _text(HANDLES["status"], f"策略03观察失败（未下单）：{exc}")


def _show_third_api_window() -> None:
    existing = HANDLES.get("third_api_window", 0)
    if existing and user32.IsWindow(existing):
        user32.SetForegroundWindow(existing)
        return
    hwnd = user32.CreateWindowExW(0, "CodexQuantBotWindow", "策略03｜ma20_trend_retest_v3｜独立OKX Demo API",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 720, 470,
        HANDLES.get("main", 0), None, kernel32.GetModuleHandleW(None), None)
    HANDLES["third_api_window"] = hwnd
    _create(hwnd, "STATIC", "策略03：5分钟确认回踩，1分钟在上沿转弱做空/下沿转强做多，采用MA20结构止损。", SS_LEFT, 22, 20, 650, 26)
    for label, name, y in (("API Key", "api3_key", 62), ("Secret Key", "api3_secret", 104), ("Passphrase", "api3_passphrase", 146)):
        _create(hwnd, "STATIC", label, SS_LEFT, 22, y + 4, 90, 24)
        HANDLES[name] = _create(hwnd, "EDIT", "", WS_TABSTOP | WS_BORDER | ES_PASSWORD | ES_AUTOHSCROLL, 120, y, 540, 28)
    _create(hwnd, "BUTTON", "验证策略03 Demo API", WS_TABSTOP | BS_PUSHBUTTON, 22, 192, 190, 36, ID_THIRD_API_VERIFY)
    _create(hwnd, "BUTTON", "加密保存/更换", WS_TABSTOP | BS_PUSHBUTTON, 226, 192, 160, 36, ID_THIRD_API_SAVE)
    _create(hwnd, "BUTTON", "删除策略03本机API", WS_TABSTOP | BS_PUSHBUTTON, 400, 192, 190, 36, ID_THIRD_API_DELETE)
    saved = "已绑定并自动载入策略03独立Demo API。" if THIRD_SESSION_CREDENTIALS else "尚未绑定策略03独立Demo API。"
    HANDLES["api3_status"] = _create(hwnd, "STATIC", saved, SS_LEFT, 22, 248, 638, 54)
    unlock_text = "策略03自动测试已解锁" if THIRD_DEMO_UNLOCKED else "解锁策略03自动下单测试"
    HANDLES["third_demo_unlock"] = _create(hwnd, "BUTTON", unlock_text, WS_TABSTOP | BS_PUSHBUTTON, 22, 318, 230, 38, ID_THIRD_DEMO_UNLOCK)
    _create(hwnd, "STATIC", "绑定API不会自动下单；必须单独解锁。关闭或重启客户端后自动恢复锁定。解锁后回到主界面点击“开始观察”。", SS_LEFT, 22, 370, 638, 40)


def _save_credentials_worker() -> None:
    global SESSION_CREDENTIALS
    try:
        candidate = _field_credentials()
        existing = load_credentials(CREDENTIAL_FILE)
        if existing is not None:
            OkxDemoClient(existing).assert_account_clear()
        OkxDemoClient(candidate).check_demo_account()
        save_credentials(CREDENTIAL_FILE, candidate)
        SESSION_CREDENTIALS = candidate
        _clear_credential_fields()
        _text(HANDLES["okx"], "Demo API 已由 Windows 加密保存；重启后自动载入。")
    except Exception as exc:
        _text(HANDLES["okx"], f"保存/更换失败：{exc}")


def _delete_credentials_worker() -> None:
    global SESSION_CREDENTIALS
    try:
        existing = load_credentials(CREDENTIAL_FILE)
        if existing is None:
            raise OkxError("本机没有已保存的 Demo API")
        OkxDemoClient(existing).assert_account_clear()
        delete_credentials(CREDENTIAL_FILE)
        SESSION_CREDENTIALS = None
        _text(HANDLES["okx"], "本机加密 API 已删除；欧易网页上的 API Key 未被撤销。")
    except Exception as exc:
        _text(HANDLES["okx"], f"删除失败：{exc}")


def _parse_instruments() -> tuple[str, ...]:
    raw = _input_value("instruments").replace("，", ",")
    values = tuple(item.strip().upper() for item in raw.split(",") if item.strip())
    if not 1 <= len(values) <= 10:
        raise ValueError("交易品种必须为 1 到 10 个")
    if len(set(values)) != len(values):
        raise ValueError("交易品种不能重复")
    if any(not re.fullmatch(r"[A-Z0-9]+-USDT-SWAP", item) for item in values):
        raise ValueError("请使用 BTC-USDT-SWAP 这样的完整永续合约代码")
    return values


def _switch_instruments_worker() -> None:
    try:
        values = _parse_instruments()
        client = OkxDemoClient(SESSION_CREDENTIALS)
        client.assert_account_clear()
        for instrument in values:
            data = client.public_instrument(instrument).get("data", [])
            if not data or data[0].get("state") != "live":
                raise OkxError(f"合约不可用：{instrument}")
        save_instruments(CONFIG, values)
        _text(HANDLES["okx"], f"交易品种已更新：{', '.join(values)}")
    except Exception as exc:
        _text(HANDLES["okx"], f"切换品种失败：{exc}")


def _research_worker() -> None:
    global LAST_OUTPUT, RUNNING
    try:
        os.chdir(WORKSPACE)
        output = run_pipeline(CONFIG)
        metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
        LAST_OUTPUT = output
        summary = "总收益 {total_return:.2%}   年化 {annual_return:.2%}   夏普 {sharpe:.2f}   最大回撤 {max_drawdown:.2%}".format(**metrics)
        _text(HANDLES["metrics"], summary)
        _text(HANDLES["status"], f"运行完成：{output.resolve()}")
    except Exception as exc:
        _text(HANDLES["status"], f"运行失败：{exc}")
        _message(str(exc), "运行失败", True)
    finally:
        RUNNING = False
        user32.EnableWindow(HANDLES["run"], True)


def _range_observation_worker() -> None:
    try:
        cfg = load_config(RANGE_CONFIG)
        signal = observe_range_pivot_once(cfg, WORKSPACE / "live" / "state.sqlite3")
        report = write_observation_report(
            signal, WORKSPACE / "artifacts" / "range-pivot", timeframe=cfg.strategy.pivot_timeframe,
            instrument=cfg.okx.instruments[0],
        )
        _text(HANDLES["status"],
              f"策略02｜{RANGE_STRATEGY_VERSION}｜{cfg.strategy.pivot_timeframe}｜"
              f"区间 {signal.pivot_low:.2f}–{signal.pivot_high:.2f}｜ATR {signal.atr:.2f}｜"
              f"{signal.market_state}｜动作 {signal.action}｜{signal.reason}")
        _message(f"策略02公开K线观察完成，没有提交订单。\n\n报告：{report}", "策略02观察报告")
    except Exception as exc:
        _text(HANDLES["status"], f"策略02观察失败（未下单）：{exc}")
    finally:
        pass


def _run_range_observation() -> None:
    _text(HANDLES["status"], "策略02正在读取OKX公开已收盘K线；自动下单保持关闭…")
    threading.Thread(target=_range_observation_worker, daemon=True).start()


def _run_research() -> None:
    global RUNNING
    if RUNNING:
        return
    RUNNING = True
    user32.EnableWindow(HANDLES["run"], False)
    _text(HANDLES["status"], "正在运行模拟研究…")
    threading.Thread(target=_research_worker, daemon=True).start()


def _observation_worker() -> None:
    global OBSERVING
    try:
        cfg = load_config(CONFIG)
        database = WORKSPACE / "live" / "state.sqlite3"
        try:
            from .state import StateStore
            store = StateStore(database)
            try:
                for row in store.connection.execute(
                    "SELECT strategy_id, signal_reason FROM trade_lifecycle WHERE status='open' ORDER BY signal_time"
                ).fetchall():
                    strategy_name = {"strategy_01": "策略01", "strategy_02": "策略02", "strategy_03": "策略03"}.get(str(row[0]))
                    if strategy_name and row[1]:
                        LAST_TRIGGER_CONTEXT[strategy_name] = str(row[1])
            finally:
                store.close()
        except Exception:
            pass
        failures = 0
        next_sniper_restore = 0.0
        first_sniper_scan = True
        reconnect_restore_pending = False
        while not OBSERVE_STOP.is_set():
            try:
                lines: list[str] = []
                cycle_connection_failed = False
                if time.monotonic() >= next_sniper_restore:
                    aggressive = current_risk_profile() == "aggressive"
                    restore_results, restore_error = _attempt_restore_saved_demo_state(cfg, database)
                    if restore_error:
                        cycle_connection_failed = True
                        lines.append(f"共享预埋扫描暂时失败：{restore_error}｜本轮三个策略继续独立检查")
                    restored = sum(item.action in {"placed", "reanchored"} for item in restore_results)
                    armed = sum(item.action in {"armed", "suspended"} for item in restore_results)
                    errors = sum(item.action == "error" for item in restore_results)
                    cycle_connection_failed = cycle_connection_failed or errors > 0
                    notice_labels = {"strategy_01": "策略01｜双均线趋势", "strategy_02": "策略02｜区间高低点反转",
                                     "strategy_03": "策略03｜MA20趋势翻转回踩"}
                    if first_sniper_scan:
                        if not aggressive:
                            lines.append("共享触发首次扫描：已扫描盘面并记录候选；当前非激进型，不补挂模拟盘订单")
                        labels = {"strategy_01": "策略01", "strategy_02": "策略02", "strategy_03": "策略03"}
                        actions = {"placed": "已补挂", "reanchored": "已重锚", "armed": "有效单已存在",
                                   "invalidated": "已撤失效单", "sibling_cancelled": "已撤反向兄弟单",
                                   "blocked": "安全条件阻止", "observe": "条件未成立", "skipped": "已跳过",
                                   "suspended": "合约暂停等待恢复", "error": "异常"}
                        lines.extend(
                            f"{labels.get(item.strategy, item.strategy)}预埋扫描：{actions.get(item.action, item.action)}｜{item.reason}"
                            for item in restore_results
                        )
                        first_sniper_scan = False
                    else:
                        lines.append(f"共享预埋：每60秒对账｜补挂/重锚 {restored}｜有效等待 {armed}｜异常 {errors}")
                    next_sniper_restore = time.monotonic() + 60
                try:
                    if DEMO_UNLOCKED and SESSION_CREDENTIALS is not None:
                        execution = execute_validation_tick(cfg, SESSION_CREDENTIALS, database)
                        schedule_completed_hour_scan(Path(database), cfg.okx.instruments[0])
                        if execution.action == "submitted":
                            LAST_TRIGGER_CONTEXT["策略01"] = execution.reason
                        context = (f"｜触发条件：{LAST_TRIGGER_CONTEXT['策略01']}"
                                   if execution.action == "manage" and LAST_TRIGGER_CONTEXT.get("策略01") else "")
                        lines.append(f"策略01：{execution.action}｜{execution.reason}{context}")
                        _queue_confirmed_fill_notices(
                            SESSION_CREDENTIALS, database, cfg.okx.instruments[0])
                    else:
                        result = observe_once(cfg, database)
                        direction = "多头" if result.direction > 0 else "空头" if result.direction < 0 else "观望"
                        lines.append(f"策略01：observe｜{direction}｜{result.reason}")
                except Exception as exc:
                    cycle_connection_failed = True
                    lines.append(f"策略01：error｜{exc}")
                try:
                    range_cfg = load_config(RANGE_CONFIG)
                    if SECOND_DEMO_UNLOCKED and SECOND_SESSION_CREDENTIALS is not None:
                        range_result = execute_range_pivot_tick(range_cfg, SECOND_SESSION_CREDENTIALS, database)
                        if range_result.action == "submitted":
                            LAST_TRIGGER_CONTEXT["策略02"] = range_result.reason
                        context = (f"｜触发条件：{LAST_TRIGGER_CONTEXT['策略02']}"
                                   if range_result.action == "manage" and LAST_TRIGGER_CONTEXT.get("策略02") else "")
                        lines.append(f"策略02：{range_result.action}｜{range_result.reason}{context}")
                        _queue_confirmed_fill_notices(
                            SECOND_SESSION_CREDENTIALS, database, cfg.okx.instruments[0])
                    else:
                        signal = observe_range_pivot_once(range_cfg, database)
                        lines.append(f"策略02：observe｜区间 {signal.pivot_low:.2f}-{signal.pivot_high:.2f}｜{signal.reason}")
                except Exception as exc:
                    cycle_connection_failed = True
                    lines.append(f"策略02：error｜{exc}")
                try:
                    if THIRD_DEMO_UNLOCKED and THIRD_SESSION_CREDENTIALS is not None:
                        third_result = execute_ma20_retest_tick(THIRD_SESSION_CREDENTIALS, database, risk_profile=current_risk_profile())
                        if third_result.action == "submitted":
                            LAST_TRIGGER_CONTEXT["策略03"] = third_result.reason
                        context = (f"｜触发条件：{LAST_TRIGGER_CONTEXT['策略03']}"
                                   if third_result.action == "manage" and LAST_TRIGGER_CONTEXT.get("策略03") else "")
                        lines.append(f"策略03：{third_result.action}｜{third_result.reason}{context}")
                        _queue_confirmed_fill_notices(
                            THIRD_SESSION_CREDENTIALS, database, cfg.okx.instruments[0])
                    else:
                        signal3 = observe_ma20_retest_once(cfg.okx.instruments[0])
                        lines.append(f"策略03：{signal3.action}｜5m {signal3.five_minute_state}｜{signal3.reason}")
                except Exception as exc:
                    cycle_connection_failed = True
                    lines.append(f"策略03：error｜{exc}")
                if cycle_connection_failed:
                    reconnect_restore_pending = True
                elif reconnect_restore_pending:
                    recovery, recovery_error = _attempt_restore_saved_demo_state(cfg, database)
                    if recovery_error:
                        lines.append(f"网络恢复复扫暂时失败：{recovery_error}｜下轮继续复扫")
                    recovery_errors = sum(item.action == "error" for item in recovery)
                    recovered = sum(item.action in {"placed", "reanchored"} for item in recovery)
                    armed_after_reconnect = sum(item.action in {"armed", "suspended"} for item in recovery)
                    reconnect_restore_pending = not recovery or recovery_errors > 0
                    notice_labels = {"strategy_01": "策略01｜双均线趋势", "strategy_02": "策略02｜区间高低点反转",
                                     "strategy_03": "策略03｜MA20趋势翻转回踩"}
                    lines.append(
                        f"网络恢复后立即扫描｜补挂/重锚 {recovered}｜有效等待 {armed_after_reconnect}"
                        f"｜异常 {recovery_errors}｜触发排队已重新扫描")
                failures = 0
                # Keep the full per-strategy explanation visible so a submitted
                # order can be traced back to its measured trigger conditions.
                _text(HANDLES["status"], "\n".join(localize_main_status(line[:900]) for line in lines))
                refresh_seconds = 5 if (DEMO_UNLOCKED or SECOND_DEMO_UNLOCKED or THIRD_DEMO_UNLOCKED) else 30
                if OBSERVE_STOP.wait(refresh_seconds):
                    break
            except Exception as exc:
                failures += 1
                reconnect_restore_pending = True
                delay = retry_delay_seconds(failures)
                _text(HANDLES["status"], f"网络/行情异常（第{failures}次）：{exc}｜{delay}秒后自动重连")
                if OBSERVE_STOP.wait(delay):
                    break
    except Exception as exc:
        _text(HANDLES["status"], f"观察无法启动：{exc}")
    finally:
        OBSERVING = False
        user32.EnableWindow(HANDLES["observe_start"], True)


def _start_observation() -> None:
    global OBSERVING
    if OBSERVING:
        return
    OBSERVING = True
    OBSERVE_STOP.clear()
    user32.EnableWindow(HANDLES["observe_start"], False)
    _text(HANDLES["status"], "正在扫描三个模拟盘账户的结构预埋单；完成后进入持续观察…")
    threading.Thread(target=_observation_worker, daemon=True).start()


def _pause_observation() -> None:
    OBSERVE_STOP.set()
    _text(HANDLES["status"], "已请求暂停；不会产生新的信号或订单意图。")


def _unlock_demo_worker() -> None:
    global DEMO_UNLOCKED
    try:
        if SESSION_CREDENTIALS is None:
            raise OkxError("请先载入并验证 Demo API")
        client = OkxDemoClient(SESSION_CREDENTIALS)
        client.require_swap_trading_mode()
        _assert_clear_except_owned_snipers(client, "QBVAL")
        cfg = load_config(CONFIG)
        validation_leverage = min(cfg.okx.validation_max_leverage, 10)
        for instrument in cfg.okx.instruments:
            client.set_leverage(validation_leverage, instrument, "long")
            client.set_leverage(validation_leverage, instrument, "short")
        DEMO_UNLOCKED = True
        restore_text = _restore_after_unlock("strategy_01", SESSION_CREDENTIALS, "QBVAL",
                                             STRATEGY_RULES[0]["version"], cfg.okx.instruments[0])
        user32.EnableWindow(HANDLES["leverage"], False)
        _text(HANDLES["demo_unlock"], "验证交易已解锁")
        if HANDLES.get("strategy1_api_status"):
            _text(HANDLES["strategy1_api_status"], "策略01｜独立验证交易：已解锁")
        user32.EnableWindow(HANDLES["demo_unlock"], False)
        _text(HANDLES["status"], f"模拟盘验证模式已解锁：1张、{validation_leverage}×｜{restore_text}")
    except Exception as exc:
        _message(str(exc), "解锁失败", True)


def _unlock_demo() -> None:
    answer = user32.MessageBoxW(
        HANDLES.get("main", 0),
        "解锁只在本次客户端运行期间有效，重启后自动恢复锁定。\n"
        "解锁前将验证 Demo 账户模式并要求持仓、普通委托和策略委托全部为空。\n"
        "v0.5.3 将在1分钟和5分钟方向一致、账户空仓且冷却结束时，自动提交1张Demo验证订单，附带0.20%止盈和0.15%止损。\n"
        "Demo验证每天最多200次（正式策略仍为24次）。是否继续？",
        "解锁Demo频繁交易验证模式",
        0x24,
    )
    if answer == 6:
        threading.Thread(target=_serialized_unlock, args=(_unlock_demo_worker,), daemon=True).start()


def _okx_worker(action: str) -> None:
    try:
        client = OkxDemoClient(SESSION_CREDENTIALS)
        if action == "public":
            data = client.public_instrument()["data"][0]
            value = f"公开接口正常：{data['instId']}，状态 {data['state']}，平台杠杆上限 {data['lever']}×"
        elif action == "account":
            result = client.check_demo_account()
            account = (result.get("data") or [{}])[0]
            level = account.get("acctLv", "未知")
            mode = account.get("posMode", "未知")
            if str(level) == "2" and mode == "long_short_mode":
                value = "Demo API 与交易模式验证成功：单币种保证金、双向持仓。"
            else:
                value = f"Demo API 密钥有效，但交易模式未就绪：acctLv={level}，posMode={mode}。请在模拟交易中切换账户模式。"
        else:
            if DEMO_UNLOCKED:
                raise OkxError("验证交易已解锁，运行期间禁止修改杠杆；请重启并保持空仓后再操作")
            client.require_swap_trading_mode()
            instruments = load_instruments(CONFIG)
            leverage = load_leverage(CONFIG)
            for instrument in instruments:
                client.set_leverage(leverage, instrument)
            value = f"Demo 账户 {len(instruments)} 个合约全仓 {leverage}× 设置成功"
        _text(HANDLES["okx"], value)
    except Exception as exc:
        _text(HANDLES["okx"], f"操作失败：{exc}")


def _create(parent: int, cls: str, text: str, style: int, x: int, y: int, width: int, height: int, control_id: int = 0) -> int:
    handle = user32.CreateWindowExW(0, cls, text, style | WS_CHILD | WS_VISIBLE, x, y, width, height, parent, control_id, kernel32.GetModuleHandleW(None), None)
    font = user32.SendMessageW(parent, 0x0031, 0, 0)
    if font:
        user32.SendMessageW(handle, WM_SETFONT, font, True)
    if parent == HANDLES.get("main"):
        MAIN_LAYOUT[handle] = (x, y, width, height)
    return handle


def _layout_main(client_width: int, client_height: int) -> None:
    """Fluidly scale every main-window control from the authored desktop grid."""
    if not MAIN_LAYOUT or client_width <= 0 or client_height <= 0:
        return
    scale_x = max(0.72, client_width / MAIN_DESIGN_WIDTH)
    scale_y = max(0.78, client_height / MAIN_DESIGN_HEIGHT)
    font_scale = min(1.65, max(0.90, min(scale_x, scale_y)))
    font_height = round(16 * font_scale)
    font = MAIN_FONTS.get(font_height, 0)
    if not font:
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi32.CreateFontW.restype = wintypes.HANDLE
        font = gdi32.CreateFontW(
            -font_height, 0, 0, 0, 500, 0, 0, 0, 134, 0, 0, 5, 0,
            "Microsoft YaHei UI",
        )
        if font:
            MAIN_FONTS[font_height] = font
    for handle, (x, y, width, height) in tuple(MAIN_LAYOUT.items()):
        user32.MoveWindow(
            handle, round(x * scale_x), round(y * scale_y),
            max(1, round(width * scale_x)), max(1, round(height * scale_y)), True,
        )
        if font:
            user32.SendMessageW(handle, WM_SETFONT, font, True)


def _layout_changelog(client_width: int, client_height: int) -> None:
    edit = HANDLES.get("changelog_text", 0)
    close = HANDLES.get("changelog_close", 0)
    previous = HANDLES.get("changelog_previous", 0)
    next_page = HANDLES.get("changelog_next", 0)
    label = HANDLES.get("changelog_page_label", 0)
    if edit:
        user32.MoveWindow(edit, 18, 18, max(200, client_width - 36), max(120, client_height - 76), True)
    if close:
        user32.MoveWindow(close, max(18, client_width - 138), max(18, client_height - 48), 120, 34, True)
    if previous:
        user32.MoveWindow(previous, 18, max(18, client_height - 48), 100, 34, True)
    if label:
        user32.MoveWindow(label, 130, max(18, client_height - 42), 160, 26, True)
    if next_page:
        user32.MoveWindow(next_page, 300, max(18, client_height - 48), 100, 34, True)


def _layout_trade_details(client_width: int, client_height: int) -> None:
    status = HANDLES.get("trade_status", 0)
    open_label = HANDLES.get("trade_open_label", 0)
    open_table = HANDLES.get("trade_open_table", 0)
    history_label = HANDLES.get("trade_history_label", 0)
    history_table = HANDLES.get("trade_history_table", 0)
    refresh = HANDLES.get("trade_refresh", 0)
    chart = HANDLES.get("trade_chart", 0)
    content_width = max(400, client_width - 36)
    open_height = max(125, min(210, client_height // 4))
    history_y = 82 + open_height
    history_height = max(150, client_height - history_y - 68)
    if status:
        user32.MoveWindow(status, 18, 12, content_width, 26, True)
    if open_label:
        user32.MoveWindow(open_label, 18, 42, content_width, 24, True)
    if open_table:
        user32.MoveWindow(open_table, 18, 68, content_width, open_height, True)
    if history_label:
        user32.MoveWindow(history_label, 18, history_y - 4, content_width, 24, True)
    if history_table:
        user32.MoveWindow(history_table, 18, history_y + 22, content_width, history_height, True)
    button_y = max(18, client_height - 48)
    if refresh:
        user32.MoveWindow(refresh, 18, button_y, 130, 34, True)
    if chart:
        user32.MoveWindow(chart, 166, button_y, 200, 34, True)


def _layout_strategy_rules(client_width: int, client_height: int) -> None:
    note = HANDLES.get("strategy_rules_note", 0)
    table = HANDLES.get("strategy_rules_table", 0)
    close = HANDLES.get("strategy_rules_close", 0)
    if note:
        user32.MoveWindow(note, 18, 14, max(400, client_width - 36), 28, True)
    if table:
        user32.MoveWindow(table, 18, 48, max(400, client_width - 36), max(180, client_height - 108), True)
    if close:
        user32.MoveWindow(close, max(18, client_width - 138), max(18, client_height - 48), 120, 34, True)


def _layout_simple_table(prefix: str, client_width: int, client_height: int) -> None:
    note = HANDLES.get(f"{prefix}_note", 0) or HANDLES.get(f"{prefix}_status", 0)
    table = HANDLES.get(f"{prefix}_table", 0)
    refresh = HANDLES.get(f"{prefix}_refresh", 0)
    close = HANDLES.get(f"{prefix}_close", 0)
    if note:
        user32.MoveWindow(note, 18, 14, max(400, client_width - 36), 28, True)
    if table:
        user32.MoveWindow(table, 18, 48, max(400, client_width - 36), max(180, client_height - 108), True)
    if refresh:
        user32.MoveWindow(refresh, 18, max(18, client_height - 48), 120, 34, True)
    if close:
        user32.MoveWindow(close, max(18, client_width - 138), max(18, client_height - 48), 120, 34, True)


def _layout_ma_experiment(client_width: int, client_height: int) -> None:
    status = HANDLES.get("ma_experiment_status", 0)
    summary = HANDLES.get("ma_experiment_summary", 0)
    details = HANDLES.get("ma_experiment_details", 0)
    refresh = HANDLES.get("ma_experiment_refresh", 0)
    close = HANDLES.get("ma_experiment_close", 0)
    width = max(500, client_width - 36)
    summary_height = max(150, min(220, client_height // 3))
    detail_y = 62 + summary_height
    detail_height = max(180, client_height - detail_y - 68)
    if status:
        user32.MoveWindow(status, 18, 12, width, 26, True)
    if summary:
        user32.MoveWindow(summary, 18, 44, width, summary_height, True)
    if details:
        user32.MoveWindow(details, 18, detail_y, width, detail_height, True)
    button_y = max(18, client_height - 48)
    if refresh:
        user32.MoveWindow(refresh, 18, button_y, 130, 34, True)
    if close:
        user32.MoveWindow(close, max(18, client_width - 138), button_y, 120, 34, True)


def _layout_loss_reviews(client_width: int, client_height: int) -> None:
    status = HANDLES.get("loss_review_status", 0)
    table = HANDLES.get("loss_review_table", 0)
    refresh = HANDLES.get("loss_review_refresh", 0)
    if status:
        user32.MoveWindow(status, 18, 14, max(400, client_width - 36), 28, True)
    if table:
        user32.MoveWindow(table, 18, 48, max(400, client_width - 36), max(180, client_height - 108), True)
    if refresh:
        user32.MoveWindow(refresh, 18, max(18, client_height - 48), 140, 34, True)


def _layout_snapshot_details(client_width: int, client_height: int) -> None:
    status = HANDLES.get("snapshot_details_status", 0)
    table = HANDLES.get("snapshot_details_table", 0)
    refresh = HANDLES.get("snapshot_details_refresh", 0)
    close = HANDLES.get("snapshot_details_close", 0)
    content_width = max(500, client_width - 36)
    button_y = max(18, client_height - 48)
    if status:
        user32.MoveWindow(status, 18, 12, content_width, 26, True)
    if table:
        user32.MoveWindow(table, 18, 48, content_width, max(180, client_height - 108), True)
    if refresh:
        user32.MoveWindow(refresh, 18, button_y, 130, 34, True)
    if close:
        user32.MoveWindow(close, max(18, client_width - 138), button_y, 120, 34, True)


def _layout_pinets_comparison(client_width: int, client_height: int) -> None:
    status = HANDLES.get("pinets_comparison_status", 0)
    table = HANDLES.get("pinets_comparison_table", 0)
    refresh = HANDLES.get("pinets_comparison_refresh", 0)
    close = HANDLES.get("pinets_comparison_close", 0)
    content_width = max(500, client_width - 36)
    button_y = max(18, client_height - 48)
    if status:
        user32.MoveWindow(status, 18, 12, content_width, 26, True)
    if table:
        user32.MoveWindow(table, 18, 48, content_width, max(180, client_height - 108), True)
    if refresh:
        user32.MoveWindow(refresh, 18, button_y, 130, 34, True)
    if close:
        user32.MoveWindow(close, max(18, client_width - 138), button_y, 120, 34, True)


def _layout_reversal_misses(client_width: int, client_height: int) -> None:
    status = HANDLES.get("reversal_misses_status", 0)
    table = HANDLES.get("reversal_misses_table", 0)
    hourly_label = HANDLES.get("hourly_misses_label", 0)
    hourly_table = HANDLES.get("hourly_misses_table", 0)
    refresh = HANDLES.get("reversal_misses_refresh", 0)
    close = HANDLES.get("reversal_misses_close", 0)
    content_width = max(500, client_width - 36)
    button_y = max(18, client_height - 48)
    if status:
        user32.MoveWindow(status, 18, 12, content_width, 26, True)
    if table:
        user32.MoveWindow(table, 18, 48, content_width,
                          max(150, (client_height - 138) // 2), True)
    split_y = 48 + max(150, (client_height - 138) // 2)
    if hourly_label:
        user32.MoveWindow(hourly_label, 18, split_y + 6, content_width, 24, True)
    if hourly_table:
        user32.MoveWindow(hourly_table, 18, split_y + 32, content_width,
                          max(120, client_height - split_y - 88), True)
    if refresh:
        user32.MoveWindow(refresh, 18, button_y, 130, 34, True)
    if close:
        user32.MoveWindow(close, max(18, client_width - 138), button_y, 120, 34, True)


@WNDPROC
def window_proc(hwnd, message, wparam, lparam):
    if message == WM_TIMER and hwnd == HANDLES.get("fixed_addon_orders_window"):
        _fixed_addon_orders_worker()
        return 0
    if message == WM_ORDER_NOTICE:
        ORDER_NOTICE_QUEUE.clear()
        threading.Thread(target=_main_trade_panel_worker, daemon=True).start()
        return 0
    if message == WM_NOTIFY and hwnd == HANDLES.get("recovery_pool_window") and lparam:
        try:
            notification = ctypes.cast(
                lparam, ctypes.POINTER(NMITEMACTIVATEW)).contents
            if (notification.hdr.hwndFrom == HANDLES.get("recovery_pool_table")
                    and notification.hdr.code == LVN_COLUMNCLICK
                    and notification.iSubItem in (0, 1, 3)):
                if notification.iSubItem == 3:
                    HANDLES["recovery_pool_structure_highest_first"] = not bool(
                        HANDLES.get("recovery_pool_structure_highest_first", False))
                HANDLES["recovery_pool_sort_column"] = notification.iSubItem
                _apply_recovery_pool_time_sort()
                if notification.iSubItem in (0, 1):
                    _request_recovery_pool_refresh()
                return 0
        except (ValueError, OSError):
            pass
    if message == WM_NOTIFY and hwnd == HANDLES.get("manual_orders_window") and lparam:
        try:
            notification = ctypes.cast(lparam, ctypes.POINTER(NMITEMACTIVATEW)).contents
            if (notification.hdr.hwndFrom == HANDLES.get("manual_orders_table")
                    and notification.hdr.code == LVN_COLUMNCLICK
                    and notification.iSubItem in (0, 1)):
                if HANDLES.get("manual_orders_sort_column") == notification.iSubItem:
                    HANDLES["manual_orders_sort_desc"] = not bool(HANDLES.get("manual_orders_sort_desc", True))
                else:
                    HANDLES["manual_orders_sort_column"] = notification.iSubItem
                    HANDLES["manual_orders_sort_desc"] = True
                _apply_manual_orders_sort()
                return 0
        except (ValueError, OSError):
            pass
    if message == WM_COMMAND:
        control_id = int(wparam) & 0xFFFF
        if control_id == ID_RUN:
            _run_research()
        elif control_id == ID_REPORT:
            _open(LAST_OUTPUT / "report.html") if LAST_OUTPUT else _message("请先运行一次模拟研究。")
        elif control_id == ID_FOLDER:
            _open(WORKSPACE)
        elif control_id == ID_PUBLIC:
            threading.Thread(target=_okx_worker, args=("public",), daemon=True).start()
        elif control_id == ID_ACCOUNT:
            threading.Thread(target=_okx_worker, args=("account",), daemon=True).start()
        elif control_id == ID_LEVERAGE:
            threading.Thread(target=_okx_worker, args=("leverage",), daemon=True).start()
        elif control_id == ID_CREDENTIALS:
            _apply_credentials()
        elif control_id == ID_CHANGELOG:
            _show_changelog()
        elif control_id == ID_CONDITION_LOG:
            _show_condition_log_window()
        elif control_id == ID_CONDITION_LOG_PREVIOUS:
            _change_condition_log_day(-1)
        elif control_id == ID_CONDITION_LOG_NEXT:
            _change_condition_log_day(1)
        elif control_id == ID_CONDITION_LOG_TODAY:
            HANDLES["condition_log_day"] = datetime.now(timezone(timedelta(hours=8))).date()
            _refresh_condition_log_page()
        elif control_id == ID_CONDITION_LOG_REFRESH:
            _refresh_condition_log_page()
        elif control_id == ID_CONDITION_LOG_CLOSE:
            user32.DestroyWindow(HANDLES.get("condition_log_window", hwnd))
        elif control_id == ID_MAIN_LIFECYCLE_REFRESH:
            threading.Thread(target=_main_trade_panel_worker, daemon=True).start()
        elif control_id == ID_CHANGELOG_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_CHANGELOG_PREVIOUS:
            _change_changelog_page(-1)
        elif control_id == ID_CHANGELOG_NEXT:
            _change_changelog_page(1)
        elif control_id == ID_UPDATE:
            _open_update()
        elif control_id == ID_OBSERVE_START:
            _start_observation()
        elif control_id == ID_OBSERVE_PAUSE:
            _pause_observation()
        elif control_id == ID_DEMO_UNLOCK:
            _unlock_demo()
        elif control_id == ID_BRANDING:
            _edit_branding()
        elif control_id == ID_SAVE_CREDENTIALS:
            threading.Thread(target=_save_credentials_worker, daemon=True).start()
        elif control_id == ID_DELETE_CREDENTIALS:
            threading.Thread(target=_delete_credentials_worker, daemon=True).start()
        elif control_id == ID_INSTRUMENTS:
            threading.Thread(target=_switch_instruments_worker, daemon=True).start()
        elif control_id == ID_SECOND_API_OPEN:
            _show_second_api_window()
        elif control_id == ID_SECOND_API_SAVE:
            threading.Thread(target=_second_api_worker, args=("save",), daemon=True).start()
        elif control_id == ID_SECOND_API_VERIFY:
            threading.Thread(target=_second_api_worker, args=("verify",), daemon=True).start()
        elif control_id == ID_SECOND_API_DELETE:
            threading.Thread(target=_second_api_worker, args=("delete",), daemon=True).start()
        elif control_id == ID_SECOND_TIMEFRAME:
            threading.Thread(target=_switch_second_timeframe_worker, daemon=True).start()
        elif control_id == ID_SECOND_OBSERVE:
            _run_range_observation()
        elif control_id == ID_SECOND_DEMO_UNLOCK:
            _unlock_second_demo()
        elif control_id == ID_THIRD_API_OPEN:
            _show_third_api_window()
        elif control_id == ID_THIRD_API_SAVE:
            threading.Thread(target=_third_api_worker, args=("save",), daemon=True).start()
        elif control_id == ID_THIRD_API_VERIFY:
            threading.Thread(target=_third_api_worker, args=("verify",), daemon=True).start()
        elif control_id == ID_THIRD_API_DELETE:
            threading.Thread(target=_third_api_worker, args=("delete",), daemon=True).start()
        elif control_id == ID_THIRD_DEMO_UNLOCK:
            _unlock_third_demo()
        elif control_id == ID_FIRST_API_OPEN:
            _show_first_api_window()
        elif control_id == ID_LIVE_OPEN:
            _show_live_console()
        elif control_id == ID_LIVE_AGGRESSIVE_OPEN:
            _show_live_slot_window("aggressive")
        elif control_id == ID_LIVE_CONSERVATIVE_OPEN:
            _show_live_slot_window("conservative")
        elif control_id == ID_LIVE_PRUDENT_OPEN:
            _show_live_slot_window("prudent")
        elif control_id == ID_LIVE_ACCOUNT04_OPEN:
            _show_live_slot_window("external_observer")
        elif control_id == ID_LIVE_ACCOUNT05_OPEN:
            _show_live_slot_window("clone_research")
        elif control_id == ID_LIVE_SAVE:
            threading.Thread(target=_live_api_worker,
                             args=("save", _live_slot_for_window(hwnd)), daemon=True).start()
        elif control_id == ID_LIVE_AUDIT:
            threading.Thread(target=_live_api_worker,
                             args=("audit", _live_slot_for_window(hwnd)), daemon=True).start()
        elif control_id == ID_LIVE_DELETE:
            threading.Thread(target=_live_api_worker,
                             args=("delete", _live_slot_for_window(hwnd)), daemon=True).start()
        elif control_id == ID_LIVE_SAVE_SIZE:
            _save_live_size(_live_slot_for_window(hwnd))
        elif control_id == ID_ACCOUNT05_SAVE_SETTINGS:
            _save_account05_settings(_live_slot_for_window(hwnd))
        elif control_id == ID_ACCOUNT05_BASE_TOGGLE:
            _toggle_account05_base_rebuild(_live_slot_for_window(hwnd))
        elif control_id == ID_ACCOUNT05_REFRESH_SIGNALS:
            threading.Thread(target=_account05_signal_worker, daemon=True).start()
        elif control_id == ID_LIVE_ACCOUNT05_TOGGLE:
            _toggle_live("clone_research")
        elif control_id == ID_LIVE_API_WEB:
            shell32.ShellExecuteW(None, "open", OKX_API_MANAGEMENT_URL, None, None, SW_SHOW)
        elif control_id == ID_LIVE_SCAN:
            threading.Thread(target=_live_scan_worker,
                             args=(_live_slot_for_window(hwnd),), daemon=True).start()
        elif control_id == ID_LIVE_EXECUTE:
            threading.Thread(target=_live_execute_worker,
                             args=(_live_slot_for_window(hwnd),), daemon=True).start()
        elif control_id == ID_LIVE_AGGRESSIVE_TOGGLE:
            slot = "aggressive" if hwnd == HANDLES.get("main") else _live_slot_for_window(hwnd)
            _toggle_live(slot)
        elif control_id == ID_LIVE_ACCOUNT02_TOGGLE:
            _toggle_live("conservative")
        elif control_id == ID_LIVE_ACCOUNT03_TOGGLE:
            _toggle_live("prudent")
        elif control_id == ID_ACCOUNT04_TOGGLE:
            _toggle_live("external_observer")
        elif control_id == ID_LIVE_MAIN_CONSERVATIVE_SCAN:
            threading.Thread(target=_live_scan_worker, args=("conservative",), daemon=True).start()
        elif control_id == ID_LIVE_MAIN_CONSERVATIVE_EXECUTE:
            threading.Thread(target=_live_execute_worker, args=("conservative",), daemon=True).start()
        elif control_id == ID_LIVE_MAIN_PRUDENT_SCAN:
            threading.Thread(target=_live_scan_worker, args=("prudent",), daemon=True).start()
        elif control_id == ID_LIVE_MAIN_PRUDENT_EXECUTE:
            threading.Thread(target=_live_execute_worker, args=("prudent",), daemon=True).start()
        elif control_id == ID_LIVE_MAIN_REFRESH:
            threading.Thread(target=_refresh_live_main_dashboard, daemon=True).start()
        elif control_id == ID_SNAPSHOT_DETAILS:
            _show_snapshot_details()
        elif control_id == ID_SNAPSHOT_REFRESH:
            threading.Thread(target=_snapshot_details_worker, daemon=True).start()
        elif control_id == ID_SNAPSHOT_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_PINETS_COMPARISON:
            _show_pinets_comparison()
        elif control_id == ID_PINETS_REFRESH:
            _pinets_comparison_worker()
        elif control_id == ID_PINETS_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_REVERSAL_MISSES:
            _show_reversal_misses()
        elif control_id == ID_REVERSAL_MISSES_REFRESH:
            _reversal_misses_worker()
        elif control_id == ID_REVERSAL_MISSES_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_TRADE_DETAILS:
            _show_trade_details()
        elif control_id == ID_LOSS_REVIEWS:
            _show_loss_reviews()
        elif control_id == ID_MA_EXPERIMENT:
            _show_ma_experiment()
        elif control_id == ID_MA_EXPERIMENT_REFRESH:
            _refresh_ma_experiment()
        elif control_id == ID_MA_EXPERIMENT_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_STRATEGY_RULES:
            _show_strategy_rules()
        elif control_id == ID_SIMPLE_RULES:
            _show_simple_rules()
        elif control_id == ID_TRADING_CYCLES:
            _show_trading_cycles()
        elif control_id == ID_TRADING_CYCLES_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_LIFECYCLE_DASHBOARD:
            _show_lifecycle_dashboard()
        elif control_id == ID_LIFECYCLE_DASHBOARD_REFRESH:
            threading.Thread(target=_main_trade_panel_worker, daemon=True).start()
        elif control_id == ID_LIFECYCLE_DASHBOARD_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_RECOVERY_POOL:
            _show_recovery_pool()
        elif control_id == ID_MANUAL_ORDERS:
            _show_manual_orders()
        elif control_id == ID_MANUAL_ORDERS_REFRESH:
            threading.Thread(target=_manual_orders_worker, daemon=True).start()
        elif control_id == ID_RECOVERY_POOL_REFRESH:
            _request_recovery_pool_refresh()
        elif control_id == ID_RECOVERY_POOL_DELETE:
            table = HANDLES.get("recovery_pool_table", 0)
            selected = user32.SendMessageW(table, LVM_GETNEXTITEM, -1, LVNI_SELECTED)
            if selected < 0:
                _message("请先在利润池列表中选中要删除的记录。", error=True)
            else:
                entry_order_id = _listview_text(table, selected, 11)
                answer = user32.MessageBoxW(
                    hwnd,
                    f"仅删除本地利润池记录（不会撤销交易所订单或持仓）？\n开仓订单：{entry_order_id}",
                    "删除选中利润池记录", 0x04 | 0x30)
                if answer == 6:
                    ledger = None
                    try:
                        database = LIVE_WORKSPACE / "profiles" / "clone_research" / "strategy.sqlite3"
                        ledger = Account05StateStore(database)
                        row = ledger.connection.execute(
                            "SELECT lot_id FROM account05_lots WHERE entry_order_id=?",
                            (entry_order_id,)).fetchone()
                        if row is None:
                            raise KeyError(entry_order_id)
                        ledger.delete_lot(str(row[0]))
                        _request_recovery_pool_refresh()
                    except Exception as exc:
                        _message(f"删除利润池记录失败：{exc}", error=True)
                    finally:
                        if ledger is not None:
                            ledger.close()
        elif control_id == ID_RECOVERY_POOL_RESET:
            answer = user32.MessageBoxW(
                hwnd,
                "确认将解套池累计盈亏、亏损覆盖余额和20%储备全部归零并从现在重算？\n\n"
                "逐笔历史记录和未平仓槽位会保留；重置前开仓、重置后平仓的批次会按平仓时间计入新统计。\n"
                "归零会使当前可用于覆盖亏损的池余额变为0。",
                "重置解套利润池统计与余额", 0x04 | 0x30)
            if answer == 6:
                ledger = None
                try:
                    database = LIVE_WORKSPACE / "profiles" / "clone_research" / "strategy.sqlite3"
                    ledger = Account05StateStore(database)
                    pool = ledger.reset_recovery_accounting(clear_balances=True)
                    user32.MessageBoxW(
                        hwnd,
                        f"已开始新统计周期。\n利润池：{pool['pool_balance']:.6f}U\n"
                        f"20%储备：{pool['reserve_balance']:.6f}U\n历史逐笔记录已保留。",
                        "解套利润池已重置", 0x40)
                    _request_recovery_pool_refresh()
                except Exception as exc:
                    _message(f"解套利润池重置失败：{exc}", error=True)
                finally:
                    if ledger is not None:
                        ledger.close()
        elif control_id == ID_RECOVERY_POOL_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_FIXED_ADDON_ORDERS:
            _show_fixed_addon_orders()
        elif control_id == ID_FIXED_ADDON_REFRESH:
            threading.Thread(target=_fixed_addon_orders_worker, daemon=True).start()
        elif control_id == ID_FIXED_ADDON_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_SIMPLE_RULES_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_MA_ENDPOINTS:
            _show_ma_endpoints()
        elif control_id == ID_MA_ENDPOINTS_REFRESH:
            _ma_endpoints_worker()
        elif control_id == ID_MA_ENDPOINTS_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_PROFILE_AGGRESSIVE:
            _select_risk_profile("aggressive")
        elif control_id == ID_PROFILE_CONSERVATIVE:
            _select_risk_profile("conservative")
        elif control_id == ID_PROFILE_PRUDENT:
            _select_risk_profile("prudent")
        elif control_id == ID_TRADE_REFRESH:
            threading.Thread(target=_trade_details_worker, daemon=True).start()
        elif control_id == ID_LOSS_REFRESH:
            threading.Thread(target=_loss_review_worker, daemon=True).start()
        elif control_id in {ID_TRADE_CHART, ID_NOTICE_CHART}:
            shell32.ShellExecuteW(None, "open", OKX_ETH_SWAP_URL, None, None, SW_SHOW)
        elif control_id == ID_NOTICE_CLOSE:
            user32.DestroyWindow(hwnd)
        elif control_id == ID_RULES_CLOSE:
            user32.DestroyWindow(hwnd)
        return 0
    if message == WM_SIZE:
        client_width, client_height = int(lparam) & 0xFFFF, (int(lparam) >> 16) & 0xFFFF
        if hwnd == HANDLES.get("main"):
            _layout_main(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("changelog_window"):
            _layout_changelog(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("trade_details_window"):
            _layout_trade_details(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("strategy_rules_window"):
            _layout_strategy_rules(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("simple_rules_window"):
            _layout_simple_table("simple_rules", client_width, client_height)
            return 0
        if hwnd == HANDLES.get("trading_cycles_window"):
            half = max(300, (client_width - 54) // 2)
            card_height = max(155, (client_height - 76) // 3)
            for index, (title, table) in enumerate(zip(
                    HANDLES.get("trading_cycles_titles", []), HANDLES.get("trading_cycles_tables", []))):
                x = 18 if index % 2 == 0 else 36 + half
                y = 12 + (index // 2) * card_height
                user32.MoveWindow(title, x, y, half, 28, True)
                user32.MoveWindow(table, x, y + 30, half, card_height - 36, True)
            user32.MoveWindow(HANDLES.get("trading_cycles_close", 0), max(18, client_width - 138), max(18, client_height - 48), 120, 34, True)
            return 0
        if hwnd == HANDLES.get("lifecycle_dashboard_window"):
            half = max(300, (client_width - 54) // 2)
            top_height = max(180, client_height // 3)
            for key, x in (("lifecycle_dashboard_long_title", 18),
                           ("lifecycle_dashboard_short_title", 36 + half)):
                user32.MoveWindow(HANDLES.get(key, 0), x, 42, half, 28, True)
            for key, x in (("lifecycle_dashboard_long", 18),
                           ("lifecycle_dashboard_short", 36 + half)):
                user32.MoveWindow(HANDLES.get(key, 0), x, 72, half, top_height, True)
            orders_y = 92 + top_height
            user32.MoveWindow(HANDLES.get("lifecycle_dashboard_status", 0), 18, 10,
                              max(400, client_width - 36), 28, True)
            user32.MoveWindow(HANDLES.get("lifecycle_dashboard_orders_title", 0), 18,
                              orders_y, max(400, client_width - 36), 28, True)
            user32.MoveWindow(HANDLES.get("lifecycle_dashboard_orders", 0), 18,
                              orders_y + 32, max(400, client_width - 36),
                              max(160, client_height - orders_y - 96), True)
            user32.MoveWindow(HANDLES.get("lifecycle_dashboard_refresh", 0), 18,
                              max(18, client_height - 48), 140, 34, True)
            user32.MoveWindow(HANDLES.get("lifecycle_dashboard_close", 0),
                              max(18, client_width - 138), max(18, client_height - 48), 120, 34, True)
            return 0
        if hwnd == HANDLES.get("recovery_pool_window"):
            _layout_recovery_pool(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("manual_orders_window"):
            _layout_manual_orders(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("fixed_addon_orders_window"):
            _layout_fixed_addon_orders(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("condition_log_window"):
            content_width = max(600, client_width - 36)
            button_y = max(18, client_height - 48)
            user32.MoveWindow(HANDLES.get("condition_log_status", 0), 18, 12,
                              content_width, 28, True)
            user32.MoveWindow(HANDLES.get("condition_log_latest", 0), 18, 44,
                              content_width, 82, True)
            user32.MoveWindow(HANDLES.get("condition_log_table", 0), 18, 136,
                              content_width, max(200, client_height - 196), True)
            for key, x, width in (("condition_log_previous", 18, 110),
                                  ("condition_log_today", 138, 90),
                                  ("condition_log_next", 238, 110),
                                  ("condition_log_refresh", 358, 120)):
                user32.MoveWindow(HANDLES.get(key, 0), x, button_y, width, 34, True)
            user32.MoveWindow(HANDLES.get("condition_log_close", 0),
                              max(18, client_width - 138), button_y, 120, 34, True)
            return 0
        if hwnd == HANDLES.get("ma_endpoints_window"):
            _layout_simple_table("ma_endpoints", client_width, client_height)
            return 0
        if hwnd == HANDLES.get("loss_review_window"):
            _layout_loss_reviews(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("ma_experiment_window"):
            _layout_ma_experiment(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("snapshot_details_window"):
            _layout_snapshot_details(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("pinets_comparison_window"):
            _layout_pinets_comparison(client_width, client_height)
            return 0
        if hwnd == HANDLES.get("reversal_misses_window"):
            _layout_reversal_misses(client_width, client_height)
            return 0
    if message == WM_DESTROY:
        if hwnd == HANDLES.get("changelog_window"):
            for key in ("changelog_window", "changelog_text", "changelog_close",
                        "changelog_previous", "changelog_next", "changelog_page_label",
                        "changelog_pages", "changelog_page_index"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("second_api_window"):
            HANDLES.pop("second_api_window", None)
        elif hwnd == HANDLES.get("first_api_window"):
            HANDLES.pop("first_api_window", None)
        elif hwnd == HANDLES.get("third_api_window"):
            HANDLES.pop("third_api_window", None)
        elif hwnd == HANDLES.get("live_window"):
            for key in ("live_window", "live_console_aggressive_status",
                        "live_console_conservative_status", "live_console_prudent_status",
                        "live_console_external_observer_status", "live_console_clone_research_status"):
                HANDLES.pop(key, None)
        elif any(hwnd == HANDLES.get(f"live_{slot}_window") for slot in LIVE_SLOT_ORDER):
            slot = next(slot for slot in LIVE_SLOT_ORDER
                        if hwnd == HANDLES.get(f"live_{slot}_window"))
            for field in ("window", "api_key", "secret_key", "passphrase", "status",
                          "order_contracts"):
                HANDLES.pop(f"live_{slot}_{field}", None)
            HANDLES.pop(f"live_child_{slot}_toggle", None)
        elif hwnd == HANDLES.get("trade_details_window"):
            HANDLES.pop("trade_details_window", None)
            for key in ("trade_status", "trade_open_label", "trade_open_table", "trade_history_label",
                        "trade_history_table", "trade_refresh", "trade_chart"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("strategy_rules_window"):
            for key in ("strategy_rules_window", "strategy_rules_note", "strategy_rules_table", "strategy_rules_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("simple_rules_window"):
            for key in ("simple_rules_window", "simple_rules_note", "simple_rules_table", "simple_rules_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("trading_cycles_window"):
            for key in ("trading_cycles_window", "trading_cycles_titles", "trading_cycles_tables",
                        "trading_cycles_long_table", "trading_cycles_short_table", "trading_cycles_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("lifecycle_dashboard_window"):
            for key in ("lifecycle_dashboard_window", "lifecycle_dashboard_status",
                        "lifecycle_dashboard_long_title", "lifecycle_dashboard_short_title",
                        "lifecycle_dashboard_long", "lifecycle_dashboard_short",
                        "lifecycle_dashboard_orders_title", "lifecycle_dashboard_orders",
                        "lifecycle_dashboard_refresh", "lifecycle_dashboard_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("recovery_pool_window"):
            for key in ("recovery_pool_window", "recovery_pool_status",
                        "recovery_pool_table", "recovery_pool_refresh",
                        "recovery_pool_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("fixed_addon_orders_window"):
            for key in ("fixed_addon_orders_window", "fixed_addon_orders_status",
                        "fixed_addon_orders_table", "fixed_addon_orders_refresh",
                        "fixed_addon_orders_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("condition_log_window"):
            for key in ("condition_log_window", "condition_log_day", "condition_log_status",
                        "condition_log_latest", "condition_log_table", "condition_log_previous",
                        "condition_log_today", "condition_log_next", "condition_log_refresh",
                        "condition_log_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("ma_endpoints_window"):
            for key in ("ma_endpoints_window", "ma_endpoints_status", "ma_endpoints_table", "ma_endpoints_refresh", "ma_endpoints_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("ma_experiment_window"):
            for key in ("ma_experiment_window", "ma_experiment_status", "ma_experiment_summary",
                        "ma_experiment_details", "ma_experiment_refresh", "ma_experiment_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("snapshot_details_window"):
            for key in ("snapshot_details_window", "snapshot_details_status", "snapshot_details_table",
                        "snapshot_details_refresh", "snapshot_details_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("pinets_comparison_window"):
            for key in ("pinets_comparison_window", "pinets_comparison_status", "pinets_comparison_table",
                        "pinets_comparison_refresh", "pinets_comparison_close"):
                HANDLES.pop(key, None)
        elif hwnd == HANDLES.get("order_notice_window"):
            HANDLES.pop("order_notice_window", None)
        elif hwnd == HANDLES.get("main"):
            OBSERVE_STOP.set()
            user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, message, wparam, lparam)


def main() -> None:
    global SESSION_CREDENTIALS, SECOND_SESSION_CREDENTIALS, THIRD_SESSION_CREDENTIALS
    global LIVE_SESSION_CREDENTIALS
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass
    _prepare()
    brand_name, brand_wechat = load_branding(BRANDING)
    credential_status = "尚未保存 API；可临时应用或使用 Windows 加密保存。"
    try:
        SESSION_CREDENTIALS = load_credentials(CREDENTIAL_FILE)
        if SESSION_CREDENTIALS is not None:
            credential_status = "已自动载入本 Windows 用户加密保存的 Demo API。"
    except OkxError as exc:
        credential_status = f"已保存 API 无法载入：{exc}"
    second_api_status = "策略02独立模拟盘接口：尚未绑定"
    try:
        SECOND_SESSION_CREDENTIALS = load_credentials(SECOND_CREDENTIAL_FILE)
        if SECOND_SESSION_CREDENTIALS is not None:
            second_api_status = "策略02独立模拟盘接口：已加密绑定"
    except OkxError as exc:
        second_api_status = f"策略02独立模拟盘接口无法载入：{exc}"
    third_api_status = "策略03独立模拟盘接口：尚未绑定"
    try:
        THIRD_SESSION_CREDENTIALS = load_credentials(THIRD_CREDENTIAL_FILE)
        if THIRD_SESSION_CREDENTIALS is not None:
            third_api_status = "策略03独立模拟盘接口：已加密绑定｜自动下单已锁定"
    except OkxError as exc:
        third_api_status = f"策略03独立模拟盘接口无法载入：{exc}"
    live_errors = []
    for slot in LIVE_SLOT_ORDER:
        # Materialize two fully separate databases even before an API is bound.
        # Account04/05 must never share lifecycle or settings state with 01-03.
        profile_dir = LIVE_WORKSPACE / "profiles" / slot
        lifecycle_store = StateStore(profile_dir / "strategy.sqlite3")
        lifecycle_store.close()
        LiveAccountSettings(profile_dir / "state.sqlite3")
        try:
            LIVE_SESSION_CREDENTIALS[slot] = load_live_credentials(LIVE_CREDENTIAL_FILES[slot])
        except OkxError as exc:
            live_errors.append(f"{LIVE_PROFILES[slot]['label']}无法载入：{exc}")
    bound_count = sum(item is not None for item in LIVE_SESSION_CREDENTIALS.values())
    live_api_status = (
        "；".join(live_errors) if live_errors else
        f"实盘控制台：{bound_count}/5 个账户已绑定｜账户04观察锁定｜账户05独立策略可启动"
    )
    instruments = load_instruments(CONFIG)
    leverage = load_leverage(CONFIG)
    if os.environ.get("QUANTBOT_SMOKE_TEST") == "1":
        return
    instance = kernel32.GetModuleHandleW(None)
    class_name = "CodexQuantBotWindow"
    wc = WNDCLASSW(0, window_proc, 0, 0, instance, None, user32.LoadCursorW(None, IDC_ARROW), COLOR_WINDOW + 1, None, class_name)
    if not user32.RegisterClassW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:
        raise ctypes.WinError(ctypes.get_last_error())
    hwnd = user32.CreateWindowExW(0, class_name, f"{brand_name} · v{APP_VERSION} · LIVE实盘交易主控制台", WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 1320, 1080, None, None, instance, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    HANDLES["main"] = hwnd
    _create(hwnd, "STATIC", f"{brand_name}｜微信：{brand_wechat}｜v{APP_VERSION}｜ETH-USDT-SWAP｜全仓｜双向｜100×",
            SS_LEFT, 20, 16, 650, 32)
    HANDLES["update"] = _create(hwnd, "BUTTON", _update_button_text(), WS_TABSTOP | BS_PUSHBUTTON, 690, 14, 210, 34, ID_UPDATE)
    _create(hwnd, "BUTTON", "新版规则", WS_TABSTOP | BS_PUSHBUTTON, 910, 14, 90, 34, ID_SIMPLE_RULES)
    _create(hwnd, "BUTTON", "旧版规则", WS_TABSTOP | BS_PUSHBUTTON, 1008, 14, 90, 34, ID_STRATEGY_RULES)
    _create(hwnd, "BUTTON", "交易周期", WS_TABSTOP | BS_PUSHBUTTON, 1106, 14, 96, 34, ID_TRADING_CYCLES)
    _create(hwnd, "BUTTON", "生命周期", WS_TABSTOP | BS_PUSHBUTTON, 1210, 14, 96, 34, ID_LIFECYCLE_DASHBOARD)
    HANDLES["live_main_status"] = _create(hwnd, "STATIC", live_api_status, SS_LEFT, 20, 58, 860, 28)
    _create(hwnd, "BUTTON", "反转区漏单", WS_TABSTOP | BS_PUSHBUTTON, 568, 54, 128, 34, ID_REVERSAL_MISSES)
    _create(hwnd, "BUTTON", "四级末端配对", WS_TABSTOP | BS_PUSHBUTTON, 702, 54, 128, 34, ID_MA_ENDPOINTS)
    _create(hwnd, "BUTTON", "运行日志", WS_TABSTOP | BS_PUSHBUTTON, 836, 54, 96, 34, ID_CONDITION_LOG)
    _create(hwnd, "BUTTON", "解套利润池", WS_TABSTOP | BS_PUSHBUTTON, 938, 54, 108, 34, ID_RECOVERY_POOL)
    _create(hwnd, "BUTTON", "0.01张小单", WS_TABSTOP | BS_PUSHBUTTON, 1052, 54, 108, 34, ID_FIXED_ADDON_ORDERS)
    _create(hwnd, "BUTTON", "更新日志", WS_TABSTOP | BS_PUSHBUTTON, 1166, 54, 108, 34, ID_CHANGELOG)

    _create(hwnd, "STATIC", "账户01｜激进型共享基线/可单独覆盖｜自动执行｜单笔/每日亏损不限制｜无时间冷却",
            SS_LEFT, 20, 100, 900, 28)
    _create(hwnd, "BUTTON", "API绑定/审计", WS_TABSTOP | BS_PUSHBUTTON, 1010, 96, 125, 34, ID_LIVE_AGGRESSIVE_OPEN)
    HANDLES["live_main_aggressive_toggle"] = _create(
        hwnd, "BUTTON", aggressive_button_text(LIVE_ACCOUNT_STATES["aggressive"]),
        WS_TABSTOP | BS_PUSHBUTTON, 1143, 96, 131, 34, ID_LIVE_AGGRESSIVE_TOGGLE)
    HANDLES["live_main_aggressive_detail"] = _create(
        hwnd, "STATIC", "正在读取激进型实盘状态……", SS_LEFT, 20, 134, 1254, 60)
    _create(hwnd, "STATIC", "账户02｜激进型共享基线/可单独覆盖｜自动执行｜单笔/每日亏损不限制｜无时间冷却",
            SS_LEFT, 20, 216, 900, 28)
    _create(hwnd, "BUTTON", "API绑定/审计", WS_TABSTOP | BS_PUSHBUTTON, 1010, 212, 125, 34, ID_LIVE_CONSERVATIVE_OPEN)
    HANDLES["live_main_conservative_toggle"] = _create(
        hwnd, "BUTTON", aggressive_button_text(LIVE_ACCOUNT_STATES["conservative"]),
        WS_TABSTOP | BS_PUSHBUTTON, 1143, 212, 131, 34, ID_LIVE_ACCOUNT02_TOGGLE)
    HANDLES["live_main_conservative_detail"] = _create(
        hwnd, "STATIC", "正在读取账户02实盘状态……", SS_LEFT, 20, 250, 1254, 70)
    _create(hwnd, "STATIC", "账户02的API、数据库、下单数量、策略覆盖和运行开关均独立。",
            SS_LEFT, 20, 326, 900, 28)

    _create(hwnd, "STATIC", "账户03｜激进型共享基线/可单独覆盖｜自动执行｜单笔/每日亏损不限制｜无时间冷却",
            SS_LEFT, 20, 374, 900, 28)
    _create(hwnd, "BUTTON", "API绑定/审计", WS_TABSTOP | BS_PUSHBUTTON, 1010, 370, 125, 34, ID_LIVE_PRUDENT_OPEN)
    HANDLES["live_main_prudent_toggle"] = _create(
        hwnd, "BUTTON", aggressive_button_text(LIVE_ACCOUNT_STATES["prudent"]),
        WS_TABSTOP | BS_PUSHBUTTON, 1143, 370, 131, 34, ID_LIVE_ACCOUNT03_TOGGLE)
    HANDLES["live_main_prudent_detail"] = _create(
        hwnd, "STATIC", "正在读取账户03实盘状态……", SS_LEFT, 20, 408, 1254, 70)
    _create(hwnd, "STATIC", "账户03的API、数据库、下单数量、策略覆盖和运行开关均独立。",
            SS_LEFT, 20, 484, 900, 28)

    _create(hwnd, "STATIC", "账户04｜第三方量化策略观察账户｜独立API｜独立数据库｜本软件自动下单锁定",
            SS_LEFT, 20, 532, 960, 28)
    _create(hwnd, "BUTTON", "API绑定/审计", WS_TABSTOP | BS_PUSHBUTTON,
            1010, 528, 125, 34, ID_LIVE_ACCOUNT04_OPEN)
    HANDLES["live_main_external_observer_toggle"] = _create(
        hwnd, "BUTTON", aggressive_button_text(LIVE_ACCOUNT_STATES["external_observer"]),
        WS_TABSTOP | BS_PUSHBUTTON, 1143, 528, 131, 34, ID_ACCOUNT04_TOGGLE)
    HANDLES["live_main_external_observer_detail"] = _create(
        hwnd, "STATIC", "正在读取账户04只读审计状态……", SS_LEFT, 20, 566, 1254, 62)
    _create(hwnd, "STATIC", "账户04仅用于运行并观察第三方软件；不会调用QuantBot开仓链路。",
            SS_LEFT, 20, 632, 1050, 28)

    _create(hwnd, "STATIC", "账户05｜独立双向对冲＋六类小单策略｜独立API｜独立数据库｜自动执行",
            SS_LEFT, 20, 680, 960, 28)
    _create(hwnd, "BUTTON", "API绑定/审计", WS_TABSTOP | BS_PUSHBUTTON,
            1010, 676, 125, 34, ID_LIVE_ACCOUNT05_OPEN)
    HANDLES["live_main_clone_research_toggle"] = _create(
        hwnd, "BUTTON", aggressive_button_text(LIVE_ACCOUNT_STATES["clone_research"]),
        WS_TABSTOP | BS_PUSHBUTTON, 1143, 676, 131, 34, ID_LIVE_ACCOUNT05_TOGGLE)
    HANDLES["live_main_clone_research_detail"] = _create(
        hwnd, "STATIC", "正在读取账户05只读审计状态……", SS_LEFT, 20, 714, 1254, 62)
    _create(hwnd, "STATIC", "账户05基础仓｜独立0.01张小单｜解套利润池多空槽位容量可在账户05参数中调整，普通槽位每笔0.02张；极值反手按独立规则。",
            SS_LEFT, 20, 780, 1050, 28)

    HANDLES["main_trade_panel_status"] = _create(
        hwnd, "STATIC", "订单结构与生命周期请打开顶部『生命周期』菜单查看。",
        SS_LEFT, 20, 836, 1080, 26)
    _create(hwnd, "BUTTON", "打开生命周期", WS_TABSTOP | BS_PUSHBUTTON,
            1110, 830, 164, 34, ID_LIFECYCLE_DASHBOARD)
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    _layout_main(rect.right - rect.left, rect.bottom - rect.top)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)
    os.environ.pop("QUANTBOT_OPEN_LIVE_SLOT", None)
    threading.Thread(target=_refresh_live_main_dashboard, daemon=True).start()
    threading.Thread(target=_main_trade_panel_worker, daemon=True).start()
    threading.Thread(target=_condition_log_worker, daemon=True).start()
    msg = MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
