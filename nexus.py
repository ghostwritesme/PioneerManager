"""Nexus Mods API client + nxm:// link handling + the Windows registry bit that
makes "Mod Manager Download" on the site actually open this app.

stdlib only (urllib, no requests) - didn't want to drag in a dependency just
for this, especially with PyInstaller in the picture.
"""

import difflib
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

API_BASE = "https://api.nexusmods.com/v1"
USER_AGENT = "WD2-Mod-Manager/1.0"

# HKCU (not HKLM/HKCR) so registration needs no admin elevation.
NXM_REGISTRY_PATH = r"Software\Classes\nxm"


class NexusApiError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class NexusClient:
    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()

    def _request(self, path: str, params: Optional[dict] = None) -> dict:
        if not self.api_key:
            raise NexusApiError("No Nexus API key configured.")
        url = f"{API_BASE}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        req = urllib.request.Request(
            url,
            headers={
                "apikey": self.api_key,
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body) if body else {}
                # download_link.json comes back as a bare array, so nowhere to stash rate limit info on it
                if isinstance(data, dict):
                    data["_rate_hourly_remaining"] = resp.headers.get("X-RL-Hourly-Remaining")
                    data["_rate_daily_remaining"] = resp.headers.get("X-RL-Daily-Remaining")
                return data
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            # codes below are per Nexus docs, never actually tested against a live call -
            # if the message's wrong trust the raw body over this
            if exc.code == 401:
                raise NexusApiError("API key rejected (401). Check the key in Settings.", 401)
            if exc.code == 403:
                raise NexusApiError(f"Nexus returned 403 Forbidden: {detail[:200] or '(no detail)'}", 403)
            if exc.code == 429:
                raise NexusApiError("Rate limited by Nexus (429). Try again shortly.", 429)
            raise NexusApiError(f"Nexus API error {exc.code}: {detail[:200]}", exc.code)
        except urllib.error.URLError as exc:
            raise NexusApiError(f"Could not reach Nexus Mods: {exc.reason}")
        except json.JSONDecodeError:
            raise NexusApiError("Nexus returned an unreadable response.")

    def validate(self) -> dict:
        # also tells us if they're Premium
        return self._request("/users/validate.json")

    def get_mod_info(self, game_domain: str, mod_id: int) -> dict:
        return self._request(f"/games/{game_domain}/mods/{mod_id}.json")

    def get_categories(self, game_domain: str) -> dict:
        # category_id -> name, cached on the instance. couldn't test this against
        # a real call, so if the response shape is off it just silently returns {}
        # instead of blowing up
        if not hasattr(self, "_category_cache"):
            self._category_cache = {}
        if game_domain in self._category_cache:
            return self._category_cache[game_domain]
        mapping = {}
        try:
            result = self._request(f"/games/{game_domain}.json")
            for cat in result.get("categories", []) or []:
                cid = cat.get("category_id")
                name = cat.get("name")
                if cid is not None and name:
                    mapping[cid] = name
        except NexusApiError:
            pass
        self._category_cache[game_domain] = mapping
        return mapping

    def get_mod_display_info(self, game_domain: str, mod_id: int) -> dict:
        # category is left out (not None-filled with junk) if that lookup fails
        info = self.get_mod_info(game_domain, mod_id)
        category_name = None
        cat_id = info.get("category_id")
        if cat_id is not None:
            category_name = self.get_categories(game_domain).get(cat_id)
        return {
            "mod_id": mod_id,
            "name": info.get("name") or f"mod-{mod_id}",
            "author": info.get("author") or info.get("uploaded_by") or "",
            "category": category_name,
            "version": info.get("version"),
        }

    # md5 of an empty file. MO2 had a bug (ModOrganizer2/modorganizer#770) where
    # some archive tooling hashes a blank placeholder instead of the real file
    # and gets a garbage match on Nexus. Just refuse to search on it.
    EMPTY_FILE_MD5 = "d41d8cd98f00b204e9800998ecf8427e"

    def search_by_md5(self, game_domain: str, md5_hash: str, expected_size: Optional[int] = None) -> Optional[dict]:
        # same idea as MO2's "Query Info". endpoint path came from a third-party
        # wrapper's docs, not confirmed live - a 404 here means check that first.
        # None = no match, not an error. not being on Nexus is normal, not a failure.
        md5_hash = (md5_hash or "").lower()
        if md5_hash == self.EMPTY_FILE_MD5:
            return None

        try:
            result = self._request(f"/games/{game_domain}/mods/md5_search/{md5_hash}.json")
        except NexusApiError as exc:
            if exc.status in (404, 400):
                return None
            raise

        candidates = result if isinstance(result, list) else result.get("data", [])
        if not candidates:
            return None

        # could get multiple hits (reuploads, collisions) - prefer the one matching our size
        if expected_size is not None:
            sized = [c for c in candidates
                    if c.get("file_details", {}).get("size_in_bytes") == expected_size]
            if sized:
                candidates = sized

        best = candidates[0]
        mod = best.get("mod", {})
        file_details = best.get("file_details", {})
        mod_id = mod.get("mod_id")
        if mod_id is None:
            return None

        category_name = None
        cat_id = mod.get("category_id")
        if cat_id is not None:
            category_name = self.get_categories(game_domain).get(cat_id)

        return {
            "mod_id": mod_id,
            "file_id": file_details.get("file_id"),
            "name": mod.get("name") or f"mod-{mod_id}",
            "author": mod.get("author") or mod.get("uploaded_by") or "",
            "category": category_name,
            # prefer the file's own version over the mod's "latest" if we have it
            "version": file_details.get("version") or mod.get("version"),
        }

    def get_download_url(self, game_domain: str, mod_id: int, file_id: int,
                         nxm_key: Optional[str] = None, nxm_expires: Optional[str] = None) -> str:
        # premium: works with no params. free: MUST pass key/expires from a real
        # nxm:// link (the "Mod Manager Download" button) or Nexus 403s it
        params = {}
        if nxm_key:
            params["key"] = nxm_key
        if nxm_expires:
            params["expires"] = nxm_expires
        result = self._request(
            f"/games/{game_domain}/mods/{mod_id}/files/{file_id}/download_link.json", params
        )
        links = result if isinstance(result, list) else result.get("data", result)
        if isinstance(links, list) and links:
            return links[0].get("URI", "")
        raise NexusApiError("Nexus returned no download mirrors for this file.")

    def download_file(self, url: str, dest_path: str,
                      progress_cb: Optional[Callable[[int, int], None]] = None) -> None:
        # urllib also happily does file:// and ftp://, don't trust the API response that much
        scheme = urllib.parse.urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise NexusApiError(f"Refusing to download from a non-web URL (scheme: {scheme or 'none'}).")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
            with open(dest_path, "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)


def guess_mod_id_from_folder_name(name: str) -> Optional[int]:
    # Nexus manual downloads name files "Name-modid-version-timestamp" or
    # "Name modid version ISO8601...". just a fallback for when there's no
    # archive to hash - it's a guess, caller still has to confirm it.
    #
    # has to anchor to the end and require a timestamp-shaped trailing number,
    # otherwise a number in the mod's actual title ("Jordan 1") gets mistaken
    # for the id. and the number of segments varies - "Better Blackouts-66-1-0-
    # 1666638299" needs to resolve to 66 (first number), not a fixed offset from the end.
    tail = re.search(r"((?:-\d+){2,})$", name)
    if tail:
        groups = tail.group(1).lstrip("-").split("-")
        if len(groups[-1]) >= 9:  # last group must look like a unix timestamp
            return int(groups[0])
    space_match = re.search(r"\s(\d+)\s\d+\s\d{4}-\d{2}-\d{2}T", name)
    if space_match:
        return int(space_match.group(1))
    return None


def _normalize_for_compare(text: str) -> str:
    text = re.sub(r"(?:-\d+){2,}$", "", text)                       # trailing hyphen-id-run
    text = re.sub(r"\s\d+\s\d+\s\d{4}-\d{2}-\d{2}T.*$", "", text)    # trailing space+ISO run
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def names_plausibly_match(folder_name: str, fetched_name: str, threshold: float = 0.5) -> bool:
    # fuzzy match, not exact word overlap - filenames get abbreviated a lot vs the
    # real listing. "No Deathscreen Slo Mo" vs "No Death Screen - Slow Motion Removed"
    # share zero words but are the same mod. 0.5 threshold, real matches score ~0.75+,
    # junk scores ~0.25 in testing.
    a = _normalize_for_compare(folder_name)
    b = _normalize_for_compare(fetched_name)
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def hash_file_md5(path: str, chunk_size: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def parse_mod_reference(text: str) -> Optional[int]:
    # for the "manually link this mod" box - takes a full mod page url or just the bare id
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.search(r"/mods/(\d+)", text)
    if match:
        return int(match.group(1))
    return None


def parse_nxm_url(url: str) -> Optional[dict]:
    # nxm://{game_domain}/mods/{mod_id}/files/{file_id}?key=...&expires=...&user_id=...
    # returns None instead of raising on garbage - this gets handed raw argv sometimes
    if not url or not url.lower().startswith("nxm://"):
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        game_domain = parsed.netloc.lower()
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) != 4 or parts[0] != "mods" or parts[2] != "files":
            return None
        mod_id = int(parts[1])
        file_id = int(parts[3])
        qs = urllib.parse.parse_qs(parsed.query)
        return {
            "game_domain": game_domain,
            "mod_id": mod_id,
            "file_id": file_id,
            "key": qs.get("key", [None])[0],
            "expires": qs.get("expires", [None])[0],
            "user_id": qs.get("user_id", [None])[0],
        }
    except (ValueError, IndexError):
        return None


# -----------------------------------------------------------------------------
# Windows protocol handler registration
# -----------------------------------------------------------------------------

def _handler_command() -> str:
    # what windows actually runs on an nxm:// click - same frozen check used
    # elsewhere for asset paths, works whether this is a script or a built exe
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    script = os.path.abspath(sys.argv[0])
    return f'"{sys.executable}" "{script}" "%1"'


def register_nxm_handler() -> str:
    if sys.platform != "win32":
        raise NexusApiError("NXM registration is Windows-only.")
    import winreg
    command = _handler_command()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, NXM_REGISTRY_PATH) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:NXM Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, NXM_REGISTRY_PATH + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    return command


def get_registered_nxm_command() -> Optional[str]:
    # None = not registered
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            NXM_REGISTRY_PATH + r"\shell\open\command") as key:
            value, _ = winreg.QueryValueEx(key, "")
            return value
    except FileNotFoundError:
        return None
    except OSError:
        return None


def is_nxm_handler_current() -> bool:
    """Whether the CURRENT process would actually be the one launched."""
    current = get_registered_nxm_command()
    return current is not None and current == _handler_command()


# -----------------------------------------------------------------------------
# Single-instance guard
# -----------------------------------------------------------------------------
# local socket instead of a lock file, since a lock file can tell us an instance
# exists but can't actually hand it the nxm url we need to forward

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

SINGLE_INSTANCE_KEY = "WD2ModManager_SingleInstance_v1"


class SingleInstanceGuard(QObject):
    nxm_received = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = None

    def try_acquire(self, connect_timeout_ms: int = 250) -> bool:
        # True = we're the only instance, go ahead and show the UI
        probe = QLocalSocket()
        probe.connectToServer(SINGLE_INSTANCE_KEY)
        already_running = probe.waitForConnected(connect_timeout_ms)
        probe.close()
        if already_running:
            return False

        # a crash can leave a stale socket file around that'd make listen() fail
        # even though nothing's actually running - clear it first
        QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)
        self.server.listen(SINGLE_INSTANCE_KEY)
        return True

    def send_to_running_instance(self, message: str, timeout_ms: int = 1000) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_KEY)
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(message.encode("utf-8"))
        # don't call flush() before this - waitForBytesWritten already flushes, and if
        # flush() wins the race it can finish the write before waitForBytesWritten checks,
        # so it sees nothing pending and returns False even though we actually sent it.
        # bytesToWrite()==0 catches that case.
        sent = socket.waitForBytesWritten(timeout_ms) or socket.bytesToWrite() == 0
        socket.disconnectFromServer()
        return sent

    def _on_new_connection(self):
        conn = self.server.nextPendingConnection()
        if conn is None:
            return
        if conn.state() != QLocalSocket.LocalSocketState.ConnectedState:
            conn.waitForConnected(200)
        if conn.waitForReadyRead(1000):
            data = bytes(conn.readAll()).decode("utf-8", errors="ignore")
            if data:
                self.nxm_received.emit(data)
        conn.disconnectFromServer()
