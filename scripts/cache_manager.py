"""
Persistent local cache for Google Maps API responses.

Text Search responses and downloaded place photos are the two biggest
recurring API costs of this skill. This module stores both locally
(SQLite for search JSON, files for photos) with TTL-based expiry so
repeat runs over the same areas cost ~zero billable requests.

Thread-safe: all DB access is serialized with a lock.

Cache keys for search round latitude/longitude to 4 decimals (~11 m), so
near-identical probe points share entries.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

DEFAULT_TTL_DAYS = 7
DEFAULT_PHOTO_TTL_DAYS = 30
COORD_PRECISION = 4


def _default_cache_dir() -> str:
    return os.path.expanduser(os.path.join("~", ".cache", "place-scout"))


class PlaceCache:
    def __init__(
        self,
        cache_dir: Optional[str] = None,
        ttl_days: float = DEFAULT_TTL_DAYS,
        photo_ttl_days: float = DEFAULT_PHOTO_TTL_DAYS,
        enabled: bool = True,
    ):
        self.enabled = bool(enabled)
        self.ttl_seconds = max(0.0, float(ttl_days)) * 86400.0
        self.photo_ttl_seconds = max(0.0, float(photo_ttl_days)) * 86400.0
        self.cache_dir = os.path.expanduser(cache_dir or _default_cache_dir())
        self.photos_dir = os.path.join(self.cache_dir, "photos")
        self.db_path = os.path.join(self.cache_dir, "place_cache.sqlite3")
        self._lock = threading.Lock()
        self._db: Optional[sqlite3.Connection] = None
        if self.enabled:
            os.makedirs(self.photos_dir, exist_ok=True)
            self._db = sqlite3.connect(self.db_path, check_same_thread=False)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                " key TEXT PRIMARY KEY, kind TEXT NOT NULL,"
                " payload TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS photos ("
                " photo_name TEXT PRIMARY KEY,"
                " file_name TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            self._db.commit()

    # ------------------------------------------------------------------
    # search cache
    # ------------------------------------------------------------------
    @staticmethod
    def search_key(
        text_query: str,
        lat: float,
        lng: float,
        radius_meters: int,
        included_type: Optional[str],
        language: str,
        region_code: Optional[str],
        max_pages: int,
    ) -> str:
        raw = "|".join([
            text_query.strip().lower(),
            f"{round(lat, COORD_PRECISION):.{COORD_PRECISION}f}",
            f"{round(lng, COORD_PRECISION):.{COORD_PRECISION}f}",
            str(radius_meters),
            (included_type or "").strip().lower(),
            (language or "").strip().lower(),
            (region_code or "").strip().upper(),
            str(max_pages),
        ])
        return "search:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def get_search(self, key: str) -> Optional[List[Dict[str, Any]]]:
        if not self.enabled or self._db is None:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT payload, created_at FROM entries WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        payload, created_at = row
        if time.time() - created_at > self.ttl_seconds:
            with self._lock:
                self._db.execute("DELETE FROM entries WHERE key = ?", (key,))
                self._db.commit()
            return None
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None

    def put_search(self, key: str, places: List[Dict[str, Any]]) -> None:
        if not self.enabled or self._db is None:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO entries (key, kind, payload, created_at)"
                " VALUES (?, 'search', ?, ?)",
                (key, json.dumps(places), time.time()),
            )
            self._db.commit()

    # ------------------------------------------------------------------
    # photo cache
    # ------------------------------------------------------------------
    @staticmethod
    def photo_name_from_url(url: str) -> str:
        """Extracts the Places photo resource name from a media URL."""
        try:
            path = url.split("://", 1)[1].split("/", 1)[1]
            # expected: v1/{photo_name}/media?params
            if path.startswith("v1/") and "/media" in path:
                return path[3:].split("/media")[0]
        except (IndexError, AttributeError):
            pass
        return ""

    def get_photo_path(self, photo_name: str) -> Optional[str]:
        """Returns the local cached photo path if present and fresh."""
        if not self.enabled or not photo_name or self._db is None:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT file_name, created_at FROM photos WHERE photo_name = ?",
                (photo_name,),
            ).fetchone()
        if not row:
            return None
        file_name, created_at = row
        full_path = os.path.join(self.photos_dir, file_name)
        if time.time() - created_at > self.photo_ttl_seconds or not os.path.exists(full_path):
            with self._lock:
                self._db.execute("DELETE FROM photos WHERE photo_name = ?", (photo_name,))
                self._db.commit()
            return None
        return full_path

    def put_photo(self, photo_name: str, src_path: str) -> Optional[str]:
        """Copies a downloaded photo into the cache; returns cached path."""
        if not self.enabled or not photo_name or self._db is None:
            return None
        if not os.path.exists(src_path):
            return None
        file_name = hashlib.sha1(photo_name.encode("utf-8")).hexdigest() + ".jpg"
        dest = os.path.join(self.photos_dir, file_name)
        try:
            shutil.copyfile(src_path, dest)
        except OSError:
            return None
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO photos (photo_name, file_name, created_at)"
                " VALUES (?, ?, ?)",
                (photo_name, file_name, time.time()),
            )
            self._db.commit()
        return dest

    # ------------------------------------------------------------------
    # maintenance
    # ------------------------------------------------------------------
    def purge_expired(self) -> int:
        """Deletes expired rows/files; returns number of rows removed."""
        if not self.enabled or self._db is None:
            return 0
        now = time.time()
        removed = 0
        with self._lock:
            for table, ttl, ts_col in (("entries", self.ttl_seconds, "created_at"),
                                       ("photos", self.photo_ttl_seconds, "created_at")):
                cur = self._db.execute(
                    f"DELETE FROM {table} WHERE ? - {ts_col} > ?", (now, ttl)
                )
                removed += cur.rowcount
            self._db.commit()
        # orphan photo files whose DB rows are gone
        try:
            with self._lock:
                live = {r[0] for r in self._db.execute("SELECT file_name FROM photos")}
            for fname in os.listdir(self.photos_dir):
                if fname not in live:
                    try:
                        os.remove(os.path.join(self.photos_dir, fname))
                    except OSError:
                        pass
        except OSError:
            pass
        return removed

    def close(self) -> None:
        if self._db is not None:
            with self._lock:
                self._db.close()
            self._db = None
