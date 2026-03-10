#!/usr/bin/env python3

import discord
from discord import app_commands
from discord.ext import commands
import requests
import time
import json
import random
import os
import re
import base64
import asyncio
import traceback
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
API_BASE = "https://discord.com/api/v9"
HEARTBEAT_INTERVAL = 20
AUTO_ACCEPT = True
MAX_CONCURRENT_USERS = 4   # Giới hạn số user xử lý đồng thời

# Load allowed channel IDs from env
_allowed_raw = os.getenv("ALLOWED_CHANNEL_IDS", "").strip()
ALLOWED_CHANNEL_IDS: set = set()
if _allowed_raw:
    for cid in _allowed_raw.split(","):
        cid = cid.strip()
        if cid.isdigit():
            ALLOWED_CHANNEL_IDS.add(int(cid))

# Proxy config
PROXY_API_KEY = os.getenv("PROXY_API_KEY", "").strip()
PROXY_API_URL = "https://proxyxoay.shop/api/get.php"
PROXY_ROTATE_INTERVAL = 60  # seconds

SUPPORTED_TASKS = [
    "WATCH_VIDEO",
    "PLAY_ON_DESKTOP",
    "STREAM_ON_DESKTOP",
    "PLAY_ACTIVITY",
    "WATCH_VIDEO_ON_MOBILE",
]

# ── Embed Colors ───────────────────────────────────────────────────────────────
COLOR_PRIMARY = 0x5865F2     # Discord Blurple
COLOR_SUCCESS = 0x57F287     # Green
COLOR_WARNING = 0xFEE75C     # Yellow
COLOR_ERROR   = 0xED4245     # Red
COLOR_INFO    = 0x5865F2     # Blurple
COLOR_LOADING = 0xEB459E     # Fuchsia
COLOR_QUEST   = 0x5865F2     # Blurple

# ── Emoji Config ───────────────────────────────────────────────────────────────
# Thay ID emoji cho phù hợp server của bạn, hoặc dùng unicode emoji
EMOJI_ACCEPTED  = "✅"
EMOJI_PENDING   = "🟡"
EMOJI_EXPIRED   = "❌"
EMOJI_SEARCH    = "🔍"
EMOJI_CLOCK     = "⏰"
EMOJI_LOCK      = "🔒"
EMOJI_GIFT      = "🎁"
EMOJI_REPORT    = "📊"
EMOJI_SHIELD    = "🛡️"
EMOJI_CHERRY    = "🌸"
EMOJI_WARNING   = "⚠️"


# ── Build number fetcher ───────────────────────────────────────────────────────
def fetch_latest_build_number() -> int:
    """Scrape Discord web app to get the latest client_build_number."""
    FALLBACK = 504649
    try:
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")
        r = requests.get("https://discord.com/app",
                         headers={"User-Agent": ua}, timeout=15)
        if r.status_code != 200:
            return FALLBACK

        scripts = re.findall(r'/assets/([a-f0-9]+)\.js', r.text)
        if not scripts:
            scripts_alt = re.findall(r'src="(/assets/[^"]+\.js)"', r.text)
            scripts = [s.split('/')[-1].replace('.js', '') for s in scripts_alt]

        if not scripts:
            return FALLBACK

        for asset_hash in scripts[-5:]:
            try:
                ar = requests.get(
                    f"https://discord.com/assets/{asset_hash}.js",
                    headers={"User-Agent": ua}, timeout=15
                )
                m = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', ar.text)
                if m:
                    return int(m.group(1))
            except Exception:
                continue

        return FALLBACK
    except Exception:
        return FALLBACK


def make_super_properties(build_number: int) -> str:
    """Create base64-encoded X-Super-Properties header."""
    obj = {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": "1.0.9175",
        "os_version": "10.0.26100",
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": "en-US",
        "browser_user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        ),
        "browser_version": "32.2.7",
        "client_build_number": build_number,
        "native_build_number": 59498,
        "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()


# ── Proxy Manager ─────────────────────────────────────────────────────────────
class ProxyManager:
    """Rotating proxy manager using proxyxoay.shop API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.current_proxy: Optional[dict] = None
        self.proxy_url: Optional[str] = None
        self.last_fetch: float = 0
        self.proxy_ttl: int = 0
        self._lock = asyncio.Lock()
        self._rotate_task: Optional[asyncio.Task] = None
        self._active_quests: int = 0  # pause rotation when > 0

    @property
    def is_enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def proxies_dict(self) -> Optional[dict]:
        """Return proxies dict for requests.Session."""
        if not self.proxy_url:
            return None
        return {
            "http": self.proxy_url,
            "https": self.proxy_url,
        }

    async def fetch_proxy(self) -> bool:
        """Fetch a new proxy from the API. Returns True on success."""
        if not self.api_key:
            return False

        async with self._lock:
            try:
                params = {
                    "key": self.api_key,
                    "nhamang": "random",
                    "tinhthanh": "0",
                }
                r = await asyncio.to_thread(
                    requests.get, PROXY_API_URL, params=params, timeout=15
                )
                data = r.json()

                if data.get("status") == 100:
                    # Success - parse proxy
                    proxy_http = data.get("proxyhttp", "")
                    parts = proxy_http.split(":")
                    if len(parts) >= 2:
                        ip, port = parts[0], parts[1]
                        self.proxy_url = f"http://{ip}:{port}"
                        self.current_proxy = {
                            "http": proxy_http,
                            "socks5": data.get("proxysocks5", ""),
                            "ip": data.get("ip", ""),
                            "nhamang": data.get("Nha Mang", ""),
                            "vitri": data.get("Vi Tri", ""),
                        }
                        # Parse TTL from message like "proxy nay se die sau 1530s"
                        msg = data.get("message", "")
                        ttl_match = re.search(r'(\d+)s', msg)
                        self.proxy_ttl = int(ttl_match.group(1)) if ttl_match else 60
                        self.last_fetch = time.time()
                        print(f"[PROXY] New proxy: {ip}:{port} | {self.current_proxy['nhamang']} | {self.current_proxy['vitri']} | TTL: {self.proxy_ttl}s")
                        return True

                elif data.get("status") == 101:
                    # Cooldown - parse wait time
                    msg = data.get("message", "")
                    wait_match = re.search(r'(\d+)s', msg)
                    wait_secs = int(wait_match.group(1)) if wait_match else 10
                    print(f"[PROXY] Cooldown: waiting {wait_secs}s...")
                    await asyncio.sleep(wait_secs + 1)
                    return await self._fetch_unlocked()
                else:
                    print(f"[PROXY] API error: {data}")
                    return False

            except Exception as e:
                print(f"[PROXY] Fetch error: {e}")
                return False

    async def _fetch_unlocked(self) -> bool:
        """Fetch proxy without lock (called from within locked context after cooldown)."""
        try:
            params = {
                "key": self.api_key,
                "nhamang": "random",
                "tinhthanh": "0",
            }
            r = await asyncio.to_thread(
                requests.get, PROXY_API_URL, params=params, timeout=15
            )
            data = r.json()
            if data.get("status") == 100:
                proxy_http = data.get("proxyhttp", "")
                parts = proxy_http.split(":")
                if len(parts) >= 2:
                    ip, port = parts[0], parts[1]
                    self.proxy_url = f"http://{ip}:{port}"
                    self.current_proxy = {
                        "http": proxy_http,
                        "socks5": data.get("proxysocks5", ""),
                        "ip": data.get("ip", ""),
                        "nhamang": data.get("Nha Mang", ""),
                        "vitri": data.get("Vi Tri", ""),
                    }
                    msg = data.get("message", "")
                    ttl_match = re.search(r'(\d+)s', msg)
                    self.proxy_ttl = int(ttl_match.group(1)) if ttl_match else 60
                    self.last_fetch = time.time()
                    print(f"[PROXY] New proxy: {ip}:{port} | {self.current_proxy['nhamang']} | {self.current_proxy['vitri']} | TTL: {self.proxy_ttl}s")
                    return True
            return False
        except Exception:
            return False

    async def start_rotation(self):
        """Start background proxy rotation task."""
        if not self.is_enabled:
            print("[PROXY] No API key configured, running without proxy")
            return

        # Fetch initial proxy
        success = await self.fetch_proxy()
        if success:
            print(f"[PROXY] Rotation started (every {PROXY_ROTATE_INTERVAL}s)")
        else:
            print("[PROXY] WARNING: Failed to fetch initial proxy, will retry...")

        # Start background rotation
        self._rotate_task = asyncio.create_task(self._rotation_loop())

    async def _rotation_loop(self):
        """Background loop to rotate proxy periodically."""
        while True:
            await asyncio.sleep(PROXY_ROTATE_INTERVAL)
            if self._active_quests > 0:
                print(f"[PROXY] Rotation skipped ({self._active_quests} quest(s) active)")
                continue
            try:
                await self.fetch_proxy()
            except Exception as e:
                print(f"[PROXY] Rotation error: {e}")

    def pause_rotation(self):
        """Pause proxy rotation (call when quest starts)."""
        self._active_quests += 1
        print(f"[PROXY] Rotation paused (active: {self._active_quests})")

    def resume_rotation(self):
        """Resume proxy rotation (call when quest finishes)."""
        self._active_quests = max(0, self._active_quests - 1)
        print(f"[PROXY] Rotation resumed (active: {self._active_quests})")

    def apply_to_session(self, session: requests.Session):
        """Apply current proxy to a requests session."""
        if self.proxies_dict:
            session.proxies.update(self.proxies_dict)


# Global proxy manager
proxy_manager = ProxyManager(PROXY_API_KEY)


# ── Session Manager (JSON persistence) ───────────────────────────────────
SESSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.json")


class SessionManager:
    """Save/load active quest sessions to JSON for crash recovery."""

    def __init__(self):
        self._sessions: dict = {}  # {user_id_str: session_data}
        self._load()

    def _load(self):
        """Load sessions from disk."""
        try:
            if os.path.exists(SESSIONS_FILE):
                with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                    self._sessions = json.load(f)
                print(f"[SESSION] Loaded {len(self._sessions)} pending session(s)")
            else:
                self._sessions = {}
        except Exception as e:
            print(f"[SESSION] Load error: {e}")
            self._sessions = {}

    def _save(self):
        """Save sessions to disk."""
        try:
            if self._sessions:
                with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                    json.dump(self._sessions, f, indent=2, ensure_ascii=False)
            else:
                # No sessions - delete file
                if os.path.exists(SESSIONS_FILE):
                    os.remove(SESSIONS_FILE)
                    print("[SESSION] File cleaned up (no active sessions)")
        except Exception as e:
            print(f"[SESSION] Save error: {e}")

    def add(self, user_id: int, token: str, channel_id: int, guild_id: int):
        """Save an active session."""
        self._sessions[str(user_id)] = {
            "token": token,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        print(f"[SESSION] Saved session for user {user_id}")

    def remove(self, user_id: int):
        """Remove a completed session."""
        uid = str(user_id)
        if uid in self._sessions:
            del self._sessions[uid]
            self._save()
            print(f"[SESSION] Removed session for user {user_id}")

    def get_pending(self) -> list:
        """Get all pending sessions for resume."""
        return [
            {"user_id": int(uid), **data}
            for uid, data in self._sessions.items()
        ]

    def has_session(self, user_id: int) -> bool:
        return str(user_id) in self._sessions


session_manager = SessionManager()


# ── HTTP API helper ────────────────────────────────────────────────────────────
class QuestAPI:
    """HTTP client for Discord Quest API calls."""

    def __init__(self, token: str, build_number: int):
        self.token = token
        self.session = requests.Session()
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        )
        sp = make_super_properties(build_number)
        self.session.headers.update({
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": ua,
            "X-Super-Properties": sp,
            "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": "Asia/Ho_Chi_Minh",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
        })
        # Apply proxy if available
        proxy_manager.apply_to_session(self.session)

    async def get(self, path: str, **kwargs) -> requests.Response:
        # Re-apply proxy in case it rotated
        proxy_manager.apply_to_session(self.session)
        return await asyncio.to_thread(self.session.get, f"{API_BASE}{path}", **kwargs)

    async def post(self, path: str, payload: Optional[dict] = None, **kwargs) -> requests.Response:
        proxy_manager.apply_to_session(self.session)
        return await asyncio.to_thread(self.session.post, f"{API_BASE}{path}", json=payload, **kwargs)

    async def validate_token(self) -> Optional[dict]:
        """Validate token and return user info or None."""
        try:
            r = await self.get("/users/@me")
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    def close(self):
        self.session.close()
        self.token = None


# ── Quest helpers ──────────────────────────────────────────────────────────────
def _get(d: Optional[dict], *keys):
    if d is None:
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None


def get_task_config(quest: dict) -> Optional[dict]:
    cfg = quest.get("config", {})
    return _get(cfg, "taskConfig", "task_config", "taskConfigV2", "task_config_v2")


def get_quest_name(quest: dict) -> str:
    cfg = quest.get("config", {})
    msgs = cfg.get("messages", {})
    name = _get(msgs, "questName", "quest_name")
    if name:
        return name.strip()
    game = _get(msgs, "gameTitle", "game_title")
    if game:
        return game.strip()
    app_name = cfg.get("application", {}).get("name")
    if app_name:
        return app_name
    return f"Quest#{quest.get('id', '?')}"


def get_expires_at(quest: dict) -> Optional[str]:
    cfg = quest.get("config", {})
    return _get(cfg, "expiresAt", "expires_at")


def get_user_status(quest: dict) -> dict:
    us = _get(quest, "userStatus", "user_status")
    return us if isinstance(us, dict) else {}


def is_completable(quest: dict) -> bool:
    expires = get_expires_at(quest)
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if exp_dt <= datetime.now(timezone.utc):
                return False
        except Exception:
            pass
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc:
        return False
    tasks = tc["tasks"]
    return any(tasks.get(t) is not None for t in SUPPORTED_TASKS)


def is_enrolled(quest: dict) -> bool:
    us = get_user_status(quest)
    return bool(_get(us, "enrolledAt", "enrolled_at"))


def is_completed(quest: dict) -> bool:
    us = get_user_status(quest)
    return bool(_get(us, "completedAt", "completed_at"))


def get_task_type(quest: dict) -> Optional[str]:
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc:
        return None
    for t in SUPPORTED_TASKS:
        if tc["tasks"].get(t) is not None:
            return t
    return None


def get_seconds_needed(quest: dict) -> int:
    tc = get_task_config(quest)
    task_type = get_task_type(quest)
    if not tc or not task_type:
        return 0
    return tc["tasks"][task_type].get("target", 0)


def get_seconds_done(quest: dict) -> float:
    task_type = get_task_type(quest)
    if not task_type:
        return 0
    us = get_user_status(quest)
    progress = us.get("progress", {})
    if not progress:
        progress = {}
    return progress.get(task_type, {}).get("value", 0)


def get_enrolled_at(quest: dict) -> Optional[str]:
    us = get_user_status(quest)
    return _get(us, "enrolledAt", "enrolled_at")


def format_duration(seconds: int) -> str:
    """Format seconds to human-readable string like '15m' or '1m25s'."""
    if seconds <= 0:
        return "0s"
    m, s = divmod(int(seconds), 60)
    if m > 0 and s > 0:
        return f"{m}m{s}s"
    elif m > 0:
        return f"{m}m"
    else:
        return f"{s}s"


def make_progress_bar(current: float, total: float, length: int = 20) -> str:
    """Create a progress bar string like ██████████░░░░░░░░░░"""
    if total <= 0:
        return "█" * length
    ratio = min(current / total, 1.0)
    filled = int(length * ratio)
    empty = length - filled
    return "█" * filled + "░" * empty


# ── Quest Processor ────────────────────────────────────────────────────────────
class QuestProcessor:
    """Process quests: enroll, complete tasks, report progress."""

    def __init__(self, api: QuestAPI, channel):
        self.api = api
        self.channel = channel
        self.results = []  # list of (quest_name, task_type, duration, status)

    async def fetch_quests(self) -> list:
        """Fetch all quests, handle rate limits."""
        try:
            r = await self.api.get("/quests/@me")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return data.get("quests", [])
                elif isinstance(data, list):
                    return data
                return []
            elif r.status_code == 429:
                retry_after = r.json().get("retry_after", 10)
                await asyncio.sleep(retry_after)
                return await self.fetch_quests()
            else:
                return []
        except Exception:
            return []

    async def enroll_quest(self, quest: dict) -> bool:
        """Auto-enroll in a quest."""
        qid = quest["id"]
        for attempt in range(3):
            try:
                r = await self.api.post(f"/quests/{qid}/enroll", {
                    "location": 11,
                    "is_targeted": False,
                    "metadata_raw": None,
                    "metadata_sealed": None,
                    "traffic_metadata_raw": quest.get("traffic_metadata_raw"),
                    "traffic_metadata_sealed": quest.get("traffic_metadata_sealed"),
                })
                if r.status_code == 429:
                    retry_after = r.json().get("retry_after", 5)
                    await asyncio.sleep(retry_after + 1)
                    continue
                return r.status_code in (200, 201, 204)
            except Exception:
                return False
        return False

    async def auto_accept_all(self, quests: list) -> list:
        """Auto-accept all unenrolled completable quests."""
        if not AUTO_ACCEPT:
            return quests
        unaccepted = [
            q for q in quests
            if not is_enrolled(q) and not is_completed(q) and is_completable(q)
        ]
        if not unaccepted:
            return quests
        for q in unaccepted:
            await self.enroll_quest(q)
            await asyncio.sleep(3)
        await asyncio.sleep(2)
        return await self.fetch_quests()

    async def complete_video(self, quest: dict, progress_msg: discord.WebhookMessage) -> bool:
        """Complete a WATCH_VIDEO quest with progress updates."""
        name = get_quest_name(quest)
        qid = quest["id"]
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)
        enrolled_at_str = get_enrolled_at(quest)

        if enrolled_at_str:
            enrolled_ts = datetime.fromisoformat(
                enrolled_at_str.replace("Z", "+00:00")
            ).timestamp()
        else:
            enrolled_ts = time.time()

        max_future = 10
        speed = 7
        last_update = 0

        while seconds_done < seconds_needed:
            max_allowed = (time.time() - enrolled_ts) + max_future
            diff = max_allowed - seconds_done
            timestamp = seconds_done + speed

            if diff >= speed:
                try:
                    r = await self.api.post(f"/quests/{qid}/video-progress", {
                        "timestamp": min(seconds_needed, timestamp + random.random())
                    })
                    if r.status_code == 200:
                        body = r.json()
                        if body.get("completed_at"):
                            return True
                        seconds_done = min(seconds_needed, timestamp)
                    elif r.status_code == 429:
                        retry_after = r.json().get("retry_after", 5)
                        await asyncio.sleep(retry_after + 1)
                        continue
                except Exception:
                    pass

            # Update progress embed every ~5 seconds
            now = time.time()
            if now - last_update >= 5:
                last_update = now
                pct = min(seconds_done / seconds_needed * 100, 100) if seconds_needed > 0 else 100
                remaining_secs = max(0, seconds_needed - seconds_done)
                remaining_min = remaining_secs / 60

                bar = make_progress_bar(seconds_done, seconds_needed)
                embed = discord.Embed(
                    description=(
                        f"{EMOJI_CLOCK} **{name}**\n"
                        f"`{bar}` **{pct:.1f}%**\n\n"
                        f"Tiến trình: **{int(seconds_done)}/{int(seconds_needed)}s**\n"
                        f"Còn lại: **~{remaining_min:.1f} phút**"
                    ),
                    color=COLOR_LOADING,
                )
                embed.set_footer(text="Quest Auto-Completer")
                try:
                    await progress_msg.edit(embed=embed)
                except Exception:
                    pass

            if timestamp >= seconds_needed:
                break
            await asyncio.sleep(1)

        try:
            await self.api.post(f"/quests/{qid}/video-progress",
                          {"timestamp": seconds_needed})
        except Exception:
            pass
        return True

    async def complete_heartbeat(self, quest: dict, progress_msg: discord.WebhookMessage) -> bool:
        """Complete PLAY_ON_DESKTOP / STREAM_ON_DESKTOP quest."""
        name = get_quest_name(quest)
        qid = quest["id"]
        task_type = get_task_type(quest)
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)
        pid = random.randint(1000, 30000)
        last_update = 0

        while seconds_done < seconds_needed:
            try:
                r = await self.api.post(f"/quests/{qid}/heartbeat", {
                    "stream_key": f"call:0:{pid}",
                    "terminal": False,
                })
                if r.status_code == 200:
                    body = r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and task_type in progress_data:
                        seconds_done = progress_data[task_type].get("value", seconds_done)
                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        return True
                elif r.status_code == 429:
                    retry_after = r.json().get("retry_after", 10)
                    await asyncio.sleep(retry_after + 1)
                    continue
            except Exception:
                pass

            # Update progress
            now = time.time()
            if now - last_update >= 10:
                last_update = now
                pct = min(seconds_done / seconds_needed * 100, 100) if seconds_needed > 0 else 100
                remaining_secs = max(0, seconds_needed - seconds_done)
                remaining_min = remaining_secs / 60

                bar = make_progress_bar(seconds_done, seconds_needed)
                embed = discord.Embed(
                    description=(
                        f"{EMOJI_CLOCK} **{name}**\n"
                        f"`{bar}` **{pct:.1f}%**\n\n"
                        f"Tiến trình: **{int(seconds_done)}/{int(seconds_needed)}s**\n"
                        f"Còn lại: **~{remaining_min:.1f} phút**"
                    ),
                    color=COLOR_LOADING,
                )
                embed.set_footer(text="Quest Auto-Completer")
                try:
                    await progress_msg.edit(embed=embed)
                except Exception:
                    pass

            await asyncio.sleep(HEARTBEAT_INTERVAL)

        try:
            await self.api.post(f"/quests/{qid}/heartbeat", {
                "stream_key": f"call:0:{pid}",
                "terminal": True,
            })
        except Exception:
            pass
        return True

    async def complete_activity(self, quest: dict, progress_msg: discord.WebhookMessage) -> bool:
        """Complete PLAY_ACTIVITY quest."""
        name = get_quest_name(quest)
        qid = quest["id"]
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)
        stream_key = "call:0:1"
        last_update = 0

        while seconds_done < seconds_needed:
            try:
                r = await self.api.post(f"/quests/{qid}/heartbeat", {
                    "stream_key": stream_key,
                    "terminal": False,
                })
                if r.status_code == 200:
                    body = r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and "PLAY_ACTIVITY" in progress_data:
                        seconds_done = progress_data["PLAY_ACTIVITY"].get("value", seconds_done)
                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        break
                elif r.status_code == 429:
                    retry_after = r.json().get("retry_after", 10)
                    await asyncio.sleep(retry_after + 1)
                    continue
            except Exception:
                pass

            now = time.time()
            if now - last_update >= 10:
                last_update = now
                pct = min(seconds_done / seconds_needed * 100, 100) if seconds_needed > 0 else 100
                remaining_secs = max(0, seconds_needed - seconds_done)
                remaining_min = remaining_secs / 60

                bar = make_progress_bar(seconds_done, seconds_needed)
                embed = discord.Embed(
                    description=(
                        f"{EMOJI_CLOCK} **{name}**\n"
                        f"`{bar}` **{pct:.1f}%**\n\n"
                        f"Tiến trình: **{int(seconds_done)}/{int(seconds_needed)}s**\n"
                        f"Còn lại: **~{remaining_min:.1f} phút**"
                    ),
                    color=COLOR_LOADING,
                )
                embed.set_footer(text="Quest Auto-Completer")
                try:
                    await progress_msg.edit(embed=embed)
                except Exception:
                    pass

            await asyncio.sleep(HEARTBEAT_INTERVAL)

        try:
            await self.api.post(f"/quests/{qid}/heartbeat", {
                "stream_key": stream_key,
                "terminal": True,
            })
        except Exception:
            pass
        return True

    async def process_quest(self, quest: dict, progress_msg: discord.WebhookMessage) -> str:
        """Process a single quest. Returns status: COMPLETED, FAILED, SKIPPED."""
        task_type = get_task_type(quest)
        if not task_type:
            return "SKIPPED"

        try:
            if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
                success = await self.complete_video(quest, progress_msg)
            elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
                success = await self.complete_heartbeat(quest, progress_msg)
            elif task_type == "PLAY_ACTIVITY":
                success = await self.complete_activity(quest, progress_msg)
            else:
                return "SKIPPED"

            return "COMPLETED" if success else "FAILED"
        except Exception as e:
            traceback.print_exc()
            return "FAILED"


# ── Discord Bot ────────────────────────────────────────────────────────────────
class QuestBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.build_number = None

    async def setup_hook(self):
        # Fetch build number on startup
        self.build_number = await asyncio.to_thread(fetch_latest_build_number)
        print(f"[OK] Build number: {self.build_number}")
        # Start proxy rotation
        await proxy_manager.start_rotation()
        await self.tree.sync()
        print(f"[OK] Slash commands synced")

    async def on_ready(self):
        print(f"[OK] Bot online: {self.user} (ID: {self.user.id})")
        print(f"[OK] Servers: {len(self.guilds)}")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Quest Auto-Completer | /quest"
            )
        )
        # Auto-resume pending sessions
        await self._resume_sessions()

    async def _resume_sessions(self):
        """Resume any sessions that were interrupted by bot restart."""
        pending = session_manager.get_pending()
        if not pending:
            return

        print(f"[SESSION] Resuming {len(pending)} pending session(s)...")
        for sess in pending:
            try:
                guild = self.get_guild(sess["guild_id"])
                if not guild:
                    print(f"[SESSION] Guild {sess['guild_id']} not found, removing session")
                    session_manager.remove(sess["user_id"])
                    continue

                channel = guild.get_channel(sess["channel_id"])
                if not channel:
                    print(f"[SESSION] Channel {sess['channel_id']} not found, removing session")
                    session_manager.remove(sess["user_id"])
                    continue

                user = guild.get_member(sess["user_id"]) or await guild.fetch_member(sess["user_id"])
                if not user:
                    print(f"[SESSION] User {sess['user_id']} not found, removing session")
                    session_manager.remove(sess["user_id"])
                    continue

                print(f"[SESSION] Resuming quest for {user.display_name} (ID: {sess['user_id']})")
                # Resume in background task
                asyncio.create_task(process_quests(channel, user, sess["token"]))

            except Exception as e:
                print(f"[SESSION] Resume error for user {sess['user_id']}: {e}")
                session_manager.remove(sess["user_id"])


bot = QuestBot()


# ── Terms View (Buttons) ──────────────────────────────────────────────────────
class TermsView(discord.ui.View):
    """View with Accept/Decline buttons for terms."""

    def __init__(self, token: str, user_id: int):
        super().__init__(timeout=120)
        self.token = token
        self.user_id = user_id
        self.accepted = False

    @discord.ui.button(
        label="Đồng ý điều khoản",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="accept_terms",
    )
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Bạn không phải người sử dụng lệnh này.", ephemeral=True
            )
            return
        self.accepted = True
        self.stop()

        # Disable buttons
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        # Start processing - send results to the channel (public)
        channel = interaction.channel
        user = interaction.user
        await process_quests(channel, user, self.token)

    @discord.ui.button(
        label="Từ chối",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id="decline_terms",
    )
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Bạn không phải người sử dụng lệnh này.", ephemeral=True
            )
            return
        self.stop()
        for item in self.children:
            item.disabled = True

        embed = discord.Embed(
            title=f"{EMOJI_EXPIRED} Đã từ chối",
            description="Bạn đã từ chối điều khoản. Token không được xử lý.",
            color=COLOR_ERROR,
        )
        embed.set_footer(text="Quest Auto-Completer")
        await interaction.response.edit_message(embed=embed, view=self)


# ── Build Terms Embed ──────────────────────────────────────────────────────────
def build_terms_embed() -> discord.Embed:
    embed = discord.Embed(
        title=f"{EMOJI_WARNING} ĐIỀU KHOẢN SỬ DỤNG & CẢNH BÁO RỦI RO",
        description="Vui lòng đọc kỹ trước khi sử dụng dịch vụ auto-quest:",
        color=COLOR_WARNING,
    )

    embed.add_field(
        name=f"🔑 Về Token của bạn",
        value=(
            "• Token Discord là **chìa khóa** tài khoản của bạn\n"
            "• Bot **chỉ sử dụng** token để xử lý nhiệm vụ (quest)\n"
            "• Token **sẽ bị xóa ngay** sau khi hoàn thành\n"
            "• Bot **không lưu trữ** token dưới bất kỳ hình thức nào\n"
            "• Lệnh gửi token là **ephemeral** (chỉ bạn thấy)"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"{EMOJI_SHIELD} Cam kết bảo mật",
        value=(
            "• Token **chỉ dùng** cho mục đích hoàn thành quest\n"
            "• **Không** sử dụng token cho bất kỳ mục đích nào khác\n"
            "• **Không** gửi tin nhắn, thay đổi thông tin tài khoản\n"
            "• **Không** truy cập server, bạn bè hoặc DM của bạn\n"
            "• **Không được** gửi token hộ người khác\n"
            "• Nếu token không khớp User ID → **từ chối xử lý**"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"{EMOJI_WARNING} Rủi ro & Miễn trừ trách nhiệm",
        value=(
            "• Việc sử dụng user token có thể **vi phạm ToS** Discord\n"
            "• Tài khoản có thể bị **cảnh cáo hoặc khóa** bởi Discord\n"
            "• **Mọi rủi ro** bạn tự chịu trách nhiệm\n"
            "• Chúng tôi **không liên quan** đến bất kỳ hậu quả nào\n"
            "• Bằng việc nhấn \"Đồng ý\", bạn **chấp nhận toàn bộ** điều khoản trên"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"✅ Xác thực bảo mật",
        value=(
            "• Bot sẽ **kiểm tra token** có đúng tài khoản của bạn không\n"
            "• **Không được** gửi token hộ người khác\n"
            "• Nếu token không khớp User ID → **từ chối xử lý**"
        ),
        inline=False,
    )

    embed.set_footer(
        text="Quest Auto-Completer • Bấm nút bên dưới để tiếp tục"
    )
    embed.timestamp = datetime.now(timezone.utc)
    return embed


# ── Concurrency limiter ────────────────────────────────────────────────────────
_quest_semaphore = asyncio.Semaphore(MAX_CONCURRENT_USERS)
_active_users: set = set()  # track which users are currently processing


# ── Process Quests (main flow after accept) ────────────────────────────────────
async def process_quests(channel: discord.TextChannel, user: discord.User, token: str):
    """Main quest processing flow after user accepts terms.
    - Scanning, progress bars, quest details, report → DM (private)
    - Only compact summary → channel (public)
    - Token deleted after use
    - Max 4 concurrent users via semaphore
    """

    # Check if user already has an active session
    if user.id in _active_users:
        await channel.send(embed=discord.Embed(
            title=f"{EMOJI_WARNING} Đang xử lý",
            description="Bạn đã có một phiên quest đang chạy.\nVui lòng chờ hoàn thành trước khi sử dụng lại.",
            color=COLOR_WARNING,
        ))
        return

    # Try to acquire semaphore (non-blocking check)
    if _quest_semaphore._value <= 0:
        await channel.send(embed=discord.Embed(
            title=f"{EMOJI_CLOCK} Hàng chờ đầy",
            description=(
                f"Hiện đang có **{MAX_CONCURRENT_USERS}** user xử lý cùng lúc.\n"
                f"Vui lòng thử lại sau ít phút."
            ),
            color=COLOR_WARNING,
        ))
        return

    async with _quest_semaphore:
        _active_users.add(user.id)
        proxy_manager.pause_rotation()
        # Save session for crash recovery
        session_manager.add(user.id, token, channel.id, channel.guild.id if hasattr(channel, 'guild') and channel.guild else 0)
        try:
            await _process_quests_inner(channel, user, token)
        finally:
            _active_users.discard(user.id)
            proxy_manager.resume_rotation()
            # Remove session after completion
            session_manager.remove(user.id)


async def _process_quests_inner(channel: discord.TextChannel, user: discord.User, token: str):
    """Inner quest processing (called within semaphore)."""
    username = user.display_name
    avatar_url = user.display_avatar.url if user.display_avatar else None

    # Open DM channel with user
    try:
        dm = await user.create_dm()
    except Exception:
        dm = None

    if not dm:
        error_embed = discord.Embed(
            title=f"{EMOJI_EXPIRED} Không thể gửi DM",
            description="Bot không thể gửi tin nhắn riêng cho bạn.\nHãy bật **Allow Direct Messages** trong server settings.",
            color=COLOR_ERROR,
        )
        error_embed.set_footer(text="Quest Auto-Completer")
        await channel.send(embed=error_embed)
        return

    # Step 1: Send "scanning" embed via DM
    scanning_embed = discord.Embed(
        description=(
            f"{EMOJI_SEARCH} **Đang quét nhiệm vụ...**\n"
            f"Xin chào **{username}**!\n"
            f"Đang kết nối và quét danh sách quest của bạn...\n\n"
            f"{EMOJI_CLOCK} Vui lòng chờ, quá trình này có thể mất vài giây."
        ),
        color=COLOR_LOADING,
    )
    scanning_embed.set_footer(text="Quest Auto-Completer • Đang xử lý...")
    scanning_embed.timestamp = datetime.now(timezone.utc)
    if avatar_url:
        scanning_embed.set_thumbnail(url=avatar_url)

    try:
        scanning_msg = await dm.send(embed=scanning_embed)
    except discord.Forbidden:
        error_embed = discord.Embed(
            title=f"{EMOJI_EXPIRED} Không thể gửi DM",
            description="Bot không thể gửi tin nhắn riêng cho bạn.\nHãy bật **Allow Direct Messages** trong server settings.",
            color=COLOR_ERROR,
        )
        error_embed.set_footer(text="Quest Auto-Completer")
        await channel.send(embed=error_embed)
        return

    # Step 2: Validate token
    api = QuestAPI(token, bot.build_number)
    user_info = await api.validate_token()

    if not user_info:
        error_embed = discord.Embed(
            title=f"{EMOJI_EXPIRED} Lỗi xác thực",
            description="Token không hợp lệ hoặc đã hết hạn.\nVui lòng kiểm tra lại token của bạn.",
            color=COLOR_ERROR,
        )
        error_embed.set_footer(text="Quest Auto-Completer")
        await scanning_msg.edit(embed=error_embed)
        api.close()
        return

    # Verify token matches the command user
    token_user_id = user_info.get("id")
    if str(user.id) != str(token_user_id):
        error_embed = discord.Embed(
            title=f"{EMOJI_EXPIRED} Token không khớp",
            description=(
                "Token này **không thuộc về bạn**.\n"
                "Bạn chỉ có thể sử dụng token của chính mình."
            ),
            color=COLOR_ERROR,
        )
        error_embed.add_field(
            name=f"{EMOJI_LOCK} Bảo mật",
            value="Token của bạn đã được xóa hoàn toàn khỏi hệ thống.",
            inline=False,
        )
        error_embed.set_footer(text="Quest Auto-Completer")
        await scanning_msg.edit(embed=error_embed)
        api.close()
        return

    # Step 3: Fetch quests
    processor = QuestProcessor(api, dm)
    quests = await processor.fetch_quests()

    if not quests:
        empty_embed = discord.Embed(
            title=f"{EMOJI_SEARCH} Không tìm thấy quest",
            description="Không có quest nào được tìm thấy trên tài khoản của bạn.",
            color=COLOR_INFO,
        )
        empty_embed.add_field(
            name=f"{EMOJI_LOCK} Bảo mật",
            value="Token của bạn đã được xóa hoàn toàn khỏi hệ thống.",
            inline=False,
        )
        empty_embed.set_footer(text="Quest Auto-Completer")
        await scanning_msg.edit(embed=empty_embed)
        api.close()
        return

    # Step 4: Classify quests
    total = len(quests)
    completed_quests = [q for q in quests if is_completed(q)]
    expired_quests = [q for q in quests if not is_completable(q) and not is_completed(q)]
    pending_quests = [
        q for q in quests
        if is_completable(q) and not is_completed(q)
    ]

    completed_count = len(completed_quests)
    expired_count = len(expired_quests)
    pending_count = len(pending_quests)

    # Step 5: Build quest list embed → DM
    quest_lines = []
    for q in quests:
        name = get_quest_name(q)
        task = get_task_type(q) or "?"
        duration = format_duration(get_seconds_needed(q))

        if is_completed(q):
            emoji = EMOJI_ACCEPTED
            status = "Hoàn thành"
        elif is_completable(q):
            emoji = EMOJI_PENDING
            status = "Cần làm"
        else:
            emoji = EMOJI_EXPIRED
            status = "Hết hạn/Không hỗ trợ"

        quest_lines.append(f"{emoji} **{name}**\n╰ {task} • {duration} • {status}")

    # Split into chunks if too long (Discord 4096 char limit)
    quest_text = "\n\n".join(quest_lines)
    if len(quest_text) > 3800:
        shown_lines = []
        char_count = 0
        for line in quest_lines:
            if char_count + len(line) + 2 > 3600:
                shown_lines.append(f"\n*...và {len(quest_lines) - len(shown_lines)} quest khác*")
                break
            shown_lines.append(line)
            char_count += len(line) + 2
        quest_text = "\n\n".join(shown_lines)

    list_embed = discord.Embed(
        title=f"📋 Danh sách Quest",
        description=(
            f"Tìm thấy **{total}** quest:\n"
            f"{EMOJI_ACCEPTED} Hoàn thành: **{completed_count}** • "
            f"{EMOJI_PENDING} Cần làm: **{pending_count}** • "
            f"{EMOJI_EXPIRED} Hết hạn: **{expired_count}**\n\n"
            f"{quest_text}"
        ),
        color=COLOR_QUEST,
    )
    list_embed.set_footer(
        text=f"{EMOJI_ACCEPTED} {completed_count}/{total} hoàn thành | "
             f"{pending_count} cần làm | {expired_count} hết hạn"
    )
    list_embed.timestamp = datetime.now(timezone.utc)
    await scanning_msg.edit(embed=list_embed)

    # Step 6: Auto-accept and process
    quests = await processor.auto_accept_all(quests)

    # Re-classify after auto-accept
    actionable = [
        q for q in quests
        if is_enrolled(q) and not is_completed(q) and is_completable(q)
    ]

    processed_count = 0
    completed_results = 0
    failed_results = 0
    skipped_results = 0
    quest_details = []

    if actionable:
        for q in actionable:
            name = get_quest_name(q)
            task_type = get_task_type(q) or "?"
            duration = format_duration(get_seconds_needed(q))

            # Send progress message via DM
            progress_embed = discord.Embed(
                description=(
                    f"{EMOJI_CLOCK} **{name}**\n"
                    f"`{'░' * 20}` **0.0%**\n\n"
                    f"Tiến trình: **0/{get_seconds_needed(q)}s**\n"
                    f"Còn lại: **~{get_seconds_needed(q) / 60:.1f} phút**"
                ),
                color=COLOR_LOADING,
            )
            progress_embed.set_footer(text="Quest Auto-Completer")
            progress_embed.timestamp = datetime.now(timezone.utc)
            progress_msg = await dm.send(embed=progress_embed)

            # Process quest
            status = await processor.process_quest(q, progress_msg)
            processed_count += 1

            if status == "COMPLETED":
                completed_results += 1
                done_embed = discord.Embed(
                    title=f"{EMOJI_CHERRY} Quest hoàn thành!",
                    description=(
                        f"**{name}**\n"
                        f"Loại: {task_type}\n\n"
                        f"{EMOJI_GIFT} Nhiệm vụ đã được hoàn thành thành công!"
                    ),
                    color=COLOR_SUCCESS,
                )
                done_embed.set_footer(text="Quest Auto-Completer")
                done_embed.timestamp = datetime.now(timezone.utc)
                await progress_msg.edit(embed=done_embed)
                quest_details.append(
                    f"{EMOJI_ACCEPTED} **{processed_count}. {name}**\n"
                    f"╰ {task_type} • {duration} • COMPLETED"
                )
            elif status == "FAILED":
                failed_results += 1
                fail_embed = discord.Embed(
                    title=f"{EMOJI_EXPIRED} Quest thất bại",
                    description=f"**{name}**\nKhông thể hoàn thành quest này.",
                    color=COLOR_ERROR,
                )
                fail_embed.set_footer(text="Quest Auto-Completer")
                await progress_msg.edit(embed=fail_embed)
                quest_details.append(
                    f"{EMOJI_EXPIRED} **{processed_count}. {name}**\n"
                    f"╰ {task_type} • {duration} • FAILED"
                )
            else:
                skipped_results += 1
                quest_details.append(
                    f"{EMOJI_PENDING} **{processed_count}. {name}**\n"
                    f"╰ {task_type} • {duration} • SKIPPED"
                )

    # Step 7: Final detailed report → DM
    final_quests = await processor.fetch_quests()
    final_total = len(final_quests) if final_quests else total
    final_completed = sum(1 for q in (final_quests or quests) if is_completed(q))
    final_expired = sum(
        1 for q in (final_quests or quests)
        if not is_completable(q) and not is_completed(q)
    )

    summary_desc = f"Xin chào **{username}**"
    if not actionable:
        summary_desc += ", tất cả quest đã được hoàn thành từ trước!"
    else:
        summary_desc += f", đây là báo cáo chi tiết:"

    report_embed = discord.Embed(
        title=f"{EMOJI_REPORT} BÁO CÁO TỔNG KẾT CHI TIẾT",
        description=summary_desc,
        color=COLOR_SUCCESS if failed_results == 0 else COLOR_WARNING,
    )
    if avatar_url:
        report_embed.set_thumbnail(url=avatar_url)

    report_embed.add_field(
        name=f"{EMOJI_SEARCH} Kết quả quét",
        value=(
            f"```\n"
            f"Tổng quest:        {final_total}\n"
            f"Đã hoàn thành:     {final_completed}\n"
            f"Hết hạn:           {final_expired}\n"
            f"Cần làm lần này:   {len(actionable)}\n"
            f"```"
        ),
        inline=False,
    )

    if actionable:
        report_embed.add_field(
            name="🏆 Kết quả xử lý",
            value=(
                f"```\n"
                f"Xử lý:        {processed_count}\n"
                f"Hoàn thành:    {completed_results}\n"
                f"Thất bại:      {failed_results}\n"
                f"Bỏ qua:        {skipped_results}\n"
                f"```"
            ),
            inline=False,
        )
        if quest_details:
            details_text = "\n\n".join(quest_details)
            if len(details_text) > 1000:
                details_text = details_text[:990] + "\n..."
            report_embed.add_field(
                name="📋 Chi tiết từng Quest",
                value=details_text,
                inline=False,
            )
    else:
        report_embed.add_field(
            name="ℹ️ Trạng thái",
            value=(
                f"{EMOJI_ACCEPTED} **{final_completed}/{final_total}** quest đã xong\n"
                f"{EMOJI_EXPIRED} **{final_expired}** quest hết hạn/không hỗ trợ\n\n"
                f"Không có quest nào cần xử lý thêm."
            ),
            inline=False,
        )

    report_embed.add_field(
        name=f"{EMOJI_LOCK} Bảo mật",
        value="Token của bạn đã được xóa hoàn toàn khỏi hệ thống.",
        inline=False,
    )
    report_embed.set_footer(text="Quest Auto-Completer • Cảm ơn bạn đã sử dụng!")
    report_embed.timestamp = datetime.now(timezone.utc)

    # Send detailed report to DM
    await dm.send(embed=report_embed)

    # Step 8: Send COMPACT summary to the CHANNEL (public)
    if not actionable:
        channel_status = f"{EMOJI_ACCEPTED} Tất cả quest đã hoàn thành từ trước!"
    elif failed_results == 0:
        channel_status = f"{EMOJI_ACCEPTED} Đã hoàn thành {completed_results} quest thành công!"
    else:
        channel_status = (
            f"{EMOJI_ACCEPTED} Hoàn thành: {completed_results} | "
            f"{EMOJI_EXPIRED} Thất bại: {failed_results}"
        )

    channel_embed = discord.Embed(
        description=(
            f"{channel_status}\n\n"
            f"📋 **Tình trạng**\n"
            f"{EMOJI_ACCEPTED} **{final_completed}/{final_total}** đã hoàn thành\n"
            f"{EMOJI_EXPIRED} **{final_expired}** hết hạn\n\n"
            f"{'Không có quest nào cần xử lý.' if not actionable else f'Đã xử lý {processed_count} quest.'}"
        ),
        color=COLOR_SUCCESS if failed_results == 0 else COLOR_WARNING,
    )
    channel_embed.set_author(
        name=username,
        icon_url=avatar_url or discord.Embed.Empty,
    )
    channel_embed.set_footer(
        text=f"User ID: {user.id} • Quest Auto-Completer"
    )
    channel_embed.timestamp = datetime.now(timezone.utc)

    await channel.send(embed=channel_embed)

    # Cleanup - xóa token
    api.close()
    del token


# ── Slash Command ──────────────────────────────────────────────────────────────
@bot.tree.command(name="quest", description="Tự động quét và hoàn thành Discord Quests")
@app_commands.describe(token="Discord User Token của bạn (chỉ bạn thấy tin nhắn này)")
async def quest_command(interaction: discord.Interaction, token: str):
    """Main /quest slash command handler."""

    # Check if channel is allowed
    if ALLOWED_CHANNEL_IDS and interaction.channel_id not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{EMOJI_EXPIRED} Kênh không được phép",
                description=(
                    "Lệnh `/quest` chỉ được sử dụng trong các kênh đã được chỉ định.\n"
                    "Vui lòng sử dụng lệnh trong kênh phù hợp."
                ),
                color=COLOR_ERROR,
            ),
            ephemeral=True,
        )
        return

    # Respond ephemeral first (token is hidden)
    terms_embed = build_terms_embed()
    view = TermsView(token=token, user_id=interaction.user.id)

    await interaction.response.send_message(
        embed=terms_embed,
        view=view,
        ephemeral=True,
    )

    # Wait for button response
    timed_out = await view.wait()
    if timed_out and not view.accepted:
        # Timeout - disable buttons
        for item in view.children:
            item.disabled = True
        try:
            await interaction.edit_original_response(view=view)
        except Exception:
            pass


# ── Entry Point ────────────────────────────────────────────────────────────────
def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("[ERR] DISCORD_BOT_TOKEN not found in environment!")
        print("[ERR] Create a .env file with: DISCORD_BOT_TOKEN=your_bot_token_here")
        return

    print("╔══════════════════════════════════════════════╗")
    print("║     Discord Quest Auto-Completer Bot        ║")
    print("║  Auto quét · Auto nhận · Auto hoàn thành    ║")
    print("╚══════════════════════════════════════════════╝")
    print()

    bot.run(token)


if __name__ == "__main__":
    main()
