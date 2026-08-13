from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

import requests


SCHEMA_VERSION = 3
DEFAULT_DB = "youtube_playlists_v2.db"
DEFAULT_PLAYLIST_FILE = "playlist.txt"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_PLAYER_URL = "https://www.youtube.com/youtubei/v1/player"
ANDROID_CLIENT_VERSION = "20.10.38"


class CollectionError(RuntimeError):
    """Raised when a complete snapshot cannot be collected safely."""


@dataclass(frozen=True)
class VideoItem:
    position: int
    source_position: int | None
    playlist_item_id: str | None
    video_id: str
    title: str
    channel_id: str | None
    channel_title: str
    availability: str
    webpage_url: str


@dataclass(frozen=True)
class PlaylistResult:
    playlist_id: str
    playlist_url: str
    title: str
    items: tuple[VideoItem, ...]
    reported_count: int | None
    hidden_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlayabilityResult:
    video_id: str
    status: str
    category: str
    reason: str | None
    current_title: str | None
    current_author: str | None


class Collector(Protocol):
    name: str
    version: str

    def collect(self, playlist_url: str) -> PlaylistResult: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_playlist_id(url: str) -> str:
    playlist_id = parse_qs(urlparse(url).query).get("list", [""])[0].strip()
    if not playlist_id:
        raise ValueError(f"플레이리스트 ID(list=...)가 없는 URL입니다: {url}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", playlist_id):
        raise ValueError(f"플레이리스트 ID 형식이 올바르지 않습니다: {playlist_id}")
    return playlist_id


def read_playlist_urls(path: Path) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        playlist_id = extract_playlist_id(line)
        if playlist_id in seen:
            raise ValueError(f"{path}:{line_number}: 중복 플레이리스트 ID: {playlist_id}")
        seen.add(playlist_id)
        urls.append(line)
    if not urls:
        raise ValueError(f"플레이리스트 URL이 없습니다: {path}")
    return urls


class _YtDlpLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.warnings.append(message)


class YtDlpCollector:
    name = "yt-dlp"

    def __init__(self) -> None:
        try:
            import yt_dlp
        except ImportError as exc:
            raise CollectionError("yt-dlp가 없습니다. requirements.txt를 설치하세요.") from exc
        self._yt_dlp = yt_dlp
        self.version = getattr(yt_dlp.version, "__version__", "unknown")

    def collect(self, playlist_url: str) -> PlaylistResult:
        logger = _YtDlpLogger()
        options = {
            "quiet": True,
            "no_warnings": False,
            "logger": logger,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "ignoreerrors": False,
            "lazy_playlist": False,
        }
        try:
            with self._yt_dlp.YoutubeDL(options) as ydl:
                data = ydl.extract_info(playlist_url, download=False)
        except Exception as exc:
            raise CollectionError(f"yt-dlp 수집 실패: {playlist_url}: {exc}") from exc
        if not data or data.get("_type") not in {"playlist", "multi_video"}:
            raise CollectionError(f"플레이리스트 응답이 아닙니다: {playlist_url}")

        playlist_id = str(data.get("id") or extract_playlist_id(playlist_url))
        title = str(data.get("title") or playlist_id).strip()
        raw_entries = list(data.get("entries") or [])
        if not raw_entries:
            raise CollectionError(f"수집된 항목이 0개입니다: {title}")

        items: list[VideoItem] = []
        for position, entry in enumerate(raw_entries, 1):
            if not entry:
                raise CollectionError(f"빈 항목이 있습니다: {title}, 위치 {position}")
            video_id = str(entry.get("id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                raise CollectionError(f"영상 ID가 잘못되었습니다: {title}, 위치 {position}")
            item_title = str(entry.get("title") or "[Unavailable video]").strip()
            channel_title = str(entry.get("channel") or entry.get("uploader") or "Unknown").strip()
            availability = str(entry.get("availability") or "available")
            lowered = item_title.casefold()
            if "deleted video" in lowered:
                availability = "deleted"
            elif "private video" in lowered:
                availability = "private"
            source_position = entry.get("playlist_index")
            if not isinstance(source_position, int):
                source_position = None
            items.append(
                VideoItem(
                    position=position,
                    source_position=source_position,
                    playlist_item_id=None,
                    video_id=video_id,
                    title=item_title,
                    channel_id=str(entry.get("channel_id") or "").strip() or None,
                    channel_title=channel_title,
                    availability=availability,
                    webpage_url=f"https://www.youtube.com/watch?v={video_id}",
                )
            )

        hidden_count = 0
        for message in logger.warnings:
            match = re.search(r"(\d+) unavailable videos? (?:are|is) hidden", message, re.I)
            if match:
                hidden_count = max(hidden_count, int(match.group(1)))
        reported_count = data.get("playlist_count")
        if not isinstance(reported_count, int):
            reported_count = None
        return PlaylistResult(
            playlist_id=playlist_id,
            playlist_url=playlist_url,
            title=title,
            items=tuple(items),
            reported_count=reported_count,
            hidden_count=hidden_count,
            warnings=tuple(logger.warnings),
        )


class YouTubeApiCollector:
    name = "youtube-data-api-v3"
    version = "v3"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise CollectionError("YOUTUBE_API_KEY가 설정되지 않았습니다.")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "YoutubePlaylistSaver/2"})

    def _get(self, endpoint: str, **params: object) -> dict:
        params["key"] = self.api_key
        try:
            response = self.session.get(
                f"{YOUTUBE_API_BASE}/{endpoint}", params=params, timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise CollectionError(f"YouTube API 요청 실패 ({endpoint}): {exc}") from exc

    def collect(self, playlist_url: str) -> PlaylistResult:
        playlist_id = extract_playlist_id(playlist_url)
        playlist_data = self._get("playlists", part="snippet", id=playlist_id, maxResults=1)
        if not playlist_data.get("items"):
            raise CollectionError(f"플레이리스트에 접근할 수 없습니다: {playlist_id}")
        title = str(playlist_data["items"][0]["snippet"]["title"])

        raw_items: list[dict] = []
        page_token: str | None = None
        reported_count: int | None = None
        while True:
            params: dict[str, object] = {
                "part": "snippet,contentDetails,status",
                "playlistId": playlist_id,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token
            page = self._get("playlistItems", **params)
            raw_items.extend(page.get("items") or [])
            total = page.get("pageInfo", {}).get("totalResults")
            if isinstance(total, int):
                reported_count = total
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        if not raw_items:
            raise CollectionError(f"수집된 항목이 0개입니다: {title}")

        items: list[VideoItem] = []
        for position, raw in enumerate(raw_items, 1):
            snippet = raw.get("snippet", {})
            video_id = str(
                raw.get("contentDetails", {}).get("videoId")
                or snippet.get("resourceId", {}).get("videoId")
                or ""
            )
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                raise CollectionError(f"영상 ID가 잘못되었습니다: {title}, 위치 {position}")
            item_title = str(snippet.get("title") or "[Unavailable video]")
            channel_title = str(snippet.get("videoOwnerChannelTitle") or "Unknown")
            lowered = item_title.casefold()
            availability = "unavailable" if "private video" in lowered or "deleted video" in lowered else "available"
            source_position = snippet.get("position")
            if isinstance(source_position, int):
                source_position += 1
            else:
                source_position = None
            items.append(
                VideoItem(
                    position=position,
                    source_position=source_position,
                    playlist_item_id=str(raw.get("id") or "").strip() or None,
                    video_id=video_id,
                    title=item_title,
                    channel_id=str(snippet.get("videoOwnerChannelId") or "").strip() or None,
                    channel_title=channel_title,
                    availability=availability,
                    webpage_url=f"https://www.youtube.com/watch?v={video_id}",
                )
            )
        hidden_count = sum(item.availability != "available" for item in items)
        return PlaylistResult(
            playlist_id=playlist_id,
            playlist_url=playlist_url,
            title=title,
            items=tuple(items),
            reported_count=reported_count,
            hidden_count=hidden_count,
        )


def choose_collector(provider: str) -> Collector:
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if provider == "youtube-api" or (provider == "auto" and api_key):
        return YouTubeApiCollector(api_key)
    return YtDlpCollector()


def categorize_playability(status: str, reason: str | None) -> str:
    normalized_reason = (reason or "").casefold()
    if status == "OK":
        return "playable"
    if status in {"UNPLAYABLE", "ERROR"}:
        return "unavailable"
    if status == "LOGIN_REQUIRED":
        if "봇" in normalized_reason or "bot" in normalized_reason:
            return "check_error"
        if "private" in normalized_reason or "비공개" in normalized_reason:
            return "unavailable"
        return "restricted"
    if status in {"AGE_CHECK_REQUIRED", "CONTENT_CHECK_REQUIRED"}:
        return "restricted"
    if status == "CHECK_ERROR":
        return "check_error"
    return "unknown"


class PlayabilityChecker:
    """Checks whether each listed video can actually be played in Korea."""

    def __init__(self, workers: int = 6) -> None:
        self.workers = workers
        self._thread_local = threading.local()
        response = requests.get(
            "https://www.youtube.com/",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        response.raise_for_status()
        match = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', response.text)
        if not match:
            raise CollectionError("YouTube 재생 상태 확인용 키를 찾지 못했습니다.")
        self._api_key = match.group(1)
        self._context = {
            "client": {
                "clientName": "ANDROID",
                "clientVersion": ANDROID_CLIENT_VERSION,
                "hl": "ko",
                "gl": "KR",
            }
        }
        self._headers = {
            "User-Agent": (
                f"com.google.android.youtube/{ANDROID_CLIENT_VERSION} "
                "(Linux; U; Android 14) gzip"
            ),
            "Content-Type": "application/json",
        }

    def _session(self) -> requests.Session:
        if not hasattr(self._thread_local, "session"):
            self._thread_local.session = requests.Session()
        return self._thread_local.session

    def _check_one(self, video_id: str) -> PlayabilityResult:
        last_error: str | None = None
        for attempt in range(4):
            try:
                response = self._session().post(
                    YOUTUBE_PLAYER_URL,
                    params={"key": self._api_key},
                    headers=self._headers,
                    json={"context": self._context, "videoId": video_id},
                    timeout=20,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {response.status_code}")
                response.raise_for_status()
                data = response.json()
                playability = data.get("playabilityStatus", {})
                details = data.get("videoDetails", {})
                status = str(playability.get("status") or "UNKNOWN")
                reason = playability.get("reason")
                return PlayabilityResult(
                    video_id=video_id,
                    status=status,
                    category=categorize_playability(status, reason),
                    reason=str(reason) if reason else None,
                    current_title=str(details.get("title")) if details.get("title") else None,
                    current_author=str(details.get("author")) if details.get("author") else None,
                )
            except (requests.RequestException, ValueError) as exc:
                last_error = str(exc)
                time.sleep(1.5 * (attempt + 1))
        return PlayabilityResult(
            video_id=video_id,
            status="CHECK_ERROR",
            category="check_error",
            reason=last_error,
            current_title=None,
            current_author=None,
        )

    def check_all(self, results: list[PlaylistResult]) -> dict[str, PlayabilityResult]:
        video_ids = sorted({item.video_id for result in results for item in result.items})
        checked: dict[str, PlayabilityResult] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self._check_one, video_id): video_id for video_id in video_ids}
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                result = future.result()
                checked[result.video_id] = result
                if index % 250 == 0 or index == len(video_ids):
                    print(f"  재생 상태 확인 {index}/{len(video_ids)}", flush=True)
        return checked


def merge_playability_with_previous(
    db_path: Path,
    results: list[PlaylistResult],
    checked: dict[str, PlayabilityResult],
) -> tuple[dict[str, PlayabilityResult], int, int]:
    """Preserve a structural snapshot when YouTube blocks player checks.

    Successful checks always win. Failed checks reuse the latest non-error
    result for the same video. New videos remain explicitly unknown instead of
    being guessed playable.
    """
    previous: dict[str, sqlite3.Row] = {}
    previous_snapshot_id: int | None = None
    if db_path.exists():
        connection = connect_db(db_path, read_only=True)
        try:
            previous_snapshot_id = _latest_snapshot_id(connection)
            if previous_snapshot_id is not None:
                previous = {
                    row["video_id"]: row
                    for row in connection.execute(
                        "SELECT * FROM playability_checks WHERE snapshot_id=?",
                        (previous_snapshot_id,),
                    )
                    if row["category"] != "check_error"
                }
        finally:
            connection.close()

    items = {
        item.video_id: item for playlist in results for item in playlist.items
    }
    merged: dict[str, PlayabilityResult] = {}
    reused = 0
    unknown = 0
    for video_id, result in checked.items():
        if result.category != "check_error":
            merged[video_id] = result
            continue
        old = previous.get(video_id)
        if old is not None:
            reused += 1
            merged[video_id] = PlayabilityResult(
                video_id=video_id,
                status=f"STALE_{old['status']}",
                category=old["category"],
                reason=(
                    f"현재 확인 실패로 스냅샷 #{previous_snapshot_id} 결과 유지: "
                    f"{result.reason or '원인 불명'}"
                ),
                current_title=old["current_title"],
                current_author=old["current_author"],
            )
            continue

        item = items[video_id]
        availability = item.availability.casefold()
        if availability in {"deleted", "private", "unavailable"}:
            category = "unavailable"
            status = "PLAYLIST_UNAVAILABLE"
        else:
            category = "unknown"
            status = "CHECK_ERROR"
            unknown += 1
        merged[video_id] = PlayabilityResult(
            video_id=video_id,
            status=status,
            category=category,
            reason=result.reason or "현재 재생 상태를 확인하지 못했습니다.",
            current_title=None,
            current_author=None,
        )
    return merged, reused, unknown


def connect_db(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    connection = (
        sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        if read_only
        else sqlite3.connect(path)
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL, provider TEXT NOT NULL,
            provider_version TEXT NOT NULL, playlist_file_sha256 TEXT NOT NULL,
            playlist_count INTEGER NOT NULL, item_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS playlists (
            playlist_id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshot_playlists (
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
            playlist_id TEXT NOT NULL REFERENCES playlists(playlist_id), title TEXT NOT NULL,
            visible_count INTEGER NOT NULL, reported_count INTEGER, hidden_count INTEGER NOT NULL,
            warnings_json TEXT NOT NULL, PRIMARY KEY (snapshot_id, playlist_id)
        );
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            first_title TEXT NOT NULL, last_title TEXT NOT NULL, first_channel_id TEXT,
            last_channel_id TEXT, first_channel_title TEXT NOT NULL,
            last_channel_title TEXT NOT NULL, last_availability TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS playlist_items (
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
            playlist_id TEXT NOT NULL REFERENCES playlists(playlist_id), position INTEGER NOT NULL,
            source_position INTEGER, playlist_item_id TEXT,
            video_id TEXT NOT NULL REFERENCES videos(video_id), title TEXT NOT NULL,
            channel_id TEXT, channel_title TEXT NOT NULL, availability TEXT NOT NULL,
            webpage_url TEXT NOT NULL, PRIMARY KEY (snapshot_id, playlist_id, position)
        );
        CREATE INDEX IF NOT EXISTS idx_playlist_items_video
            ON playlist_items(playlist_id, video_id, snapshot_id);
        CREATE TABLE IF NOT EXISTS change_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
            previous_snapshot_id INTEGER REFERENCES snapshots(id), playlist_id TEXT NOT NULL,
            video_id TEXT NOT NULL,
            change_type TEXT NOT NULL CHECK(change_type IN ('added','missing','metadata_changed')),
            previous_title TEXT, previous_channel_title TEXT,
            current_title TEXT, current_channel_title TEXT, detected_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS playability_checks (
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
            video_id TEXT NOT NULL REFERENCES videos(video_id),
            status TEXT NOT NULL, category TEXT NOT NULL, reason TEXT,
            current_title TEXT, current_author TEXT, checked_at TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, video_id)
        );
        CREATE INDEX IF NOT EXISTS idx_playability_category
            ON playability_checks(snapshot_id, category);
        CREATE TABLE IF NOT EXISTS video_metadata_recoveries (
            video_id TEXT PRIMARY KEY REFERENCES videos(video_id),
            title TEXT NOT NULL, channel_title TEXT NOT NULL,
            playlist_title TEXT NOT NULL, legacy_position INTEGER NOT NULL,
            source_db TEXT NOT NULL, recovered_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO schema_info(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )


def _latest_snapshot_id(connection: sqlite3.Connection) -> int | None:
    row = connection.execute("SELECT MAX(id) FROM snapshots").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _snapshot_items(
    connection: sqlite3.Connection, snapshot_id: int
) -> dict[str, dict[str, sqlite3.Row]]:
    grouped: dict[str, dict[str, sqlite3.Row]] = {}
    for row in connection.execute(
        "SELECT * FROM playlist_items WHERE snapshot_id=?", (snapshot_id,)
    ):
        grouped.setdefault(row["playlist_id"], {})[row["video_id"]] = row
    return grouped


def validate_results(
    results: list[PlaylistResult], expected_count: int, db_path: Path, allow_large_drop: bool
) -> list[str]:
    if len(results) != expected_count:
        raise CollectionError(f"플레이리스트 수 불일치: 예상 {expected_count}, 실제 {len(results)}")
    ids = [result.playlist_id for result in results]
    if len(ids) != len(set(ids)):
        raise CollectionError("중복 플레이리스트 ID가 있습니다.")
    for result in results:
        if not result.items:
            raise CollectionError(f"빈 플레이리스트 결과: {result.title}")
        positions = [item.position for item in result.items]
        if positions != list(range(1, len(result.items) + 1)):
            raise CollectionError(f"위치 번호가 연속적이지 않습니다: {result.title}")
        if any(not item.video_id for item in result.items):
            raise CollectionError(f"영상 ID가 비었습니다: {result.title}")

    previous_counts: dict[str, int] = {}
    if db_path.exists():
        connection = connect_db(db_path, read_only=True)
        try:
            snapshot_id = _latest_snapshot_id(connection)
            if snapshot_id is not None:
                previous_counts = {
                    row["playlist_id"]: row["visible_count"]
                    for row in connection.execute(
                        "SELECT playlist_id,visible_count FROM snapshot_playlists WHERE snapshot_id=?",
                        (snapshot_id,),
                    )
                }
        finally:
            connection.close()

    warnings: list[str] = []
    for result in results:
        baseline = previous_counts.get(result.playlist_id)
        if baseline and len(result.items) < baseline:
            drop = baseline - len(result.items)
            message = (
                f"{result.title}: 직전 {baseline}개 → 현재 {len(result.items)}개 "
                f"({drop}개 감소, 숨김 보고 {result.hidden_count}개)"
            )
            warnings.append(message)
            if drop >= 10 and len(result.items) < baseline * 0.5 and not allow_large_drop:
                raise CollectionError(message + "; 50% 이상 급감하여 저장하지 않습니다.")
    return warnings


def write_snapshot(
    db_path: Path,
    results: list[PlaylistResult],
    playability: dict[str, PlayabilityResult],
    collector: Collector,
    started_at: str,
    playlist_file_sha256: str,
) -> int:
    now = utc_now()
    connection = connect_db(db_path)
    try:
        with connection:
            initialize_schema(connection)
            previous_id = _latest_snapshot_id(connection)
            cursor = connection.execute(
                "INSERT INTO snapshots(started_at,completed_at,provider,provider_version,"
                "playlist_file_sha256,playlist_count,item_count) VALUES(?,?,?,?,?,?,?)",
                (
                    started_at,
                    now,
                    collector.name,
                    collector.version,
                    playlist_file_sha256,
                    len(results),
                    sum(len(result.items) for result in results),
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            previous = _snapshot_items(connection, previous_id) if previous_id else {}

            for result in results:
                connection.execute(
                    "INSERT INTO playlists(playlist_id,url,title,first_seen_at,last_seen_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(playlist_id) DO UPDATE SET "
                    "url=excluded.url,title=excluded.title,last_seen_at=excluded.last_seen_at",
                    (result.playlist_id, result.playlist_url, result.title, now, now),
                )
                connection.execute(
                    "INSERT INTO snapshot_playlists(snapshot_id,playlist_id,title,visible_count,"
                    "reported_count,hidden_count,warnings_json) VALUES(?,?,?,?,?,?,?)",
                    (
                        snapshot_id,
                        result.playlist_id,
                        result.title,
                        len(result.items),
                        result.reported_count,
                        result.hidden_count,
                        json.dumps(result.warnings, ensure_ascii=False),
                    ),
                )
                for item in result.items:
                    playback = playability[item.video_id]
                    connection.execute(
                        "INSERT INTO videos(video_id,first_seen_at,last_seen_at,first_title,last_title,"
                        "first_channel_id,last_channel_id,first_channel_title,last_channel_title,last_availability) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET "
                        "last_seen_at=excluded.last_seen_at,last_title=excluded.last_title,"
                        "last_channel_id=excluded.last_channel_id,last_channel_title=excluded.last_channel_title,"
                        "last_availability=excluded.last_availability",
                        (
                            item.video_id,
                            now,
                            now,
                            item.title,
                            item.title,
                            item.channel_id,
                            item.channel_id,
                            item.channel_title,
                            item.channel_title,
                            playback.category,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO playlist_items(snapshot_id,playlist_id,position,source_position,"
                        "playlist_item_id,video_id,title,channel_id,channel_title,availability,webpage_url) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            snapshot_id,
                            result.playlist_id,
                            item.position,
                            item.source_position,
                            item.playlist_item_id,
                            item.video_id,
                            item.title,
                            item.channel_id,
                            item.channel_title,
                            playback.category,
                            item.webpage_url,
                        ),
                    )

                if previous_id:
                    old_items = previous.get(result.playlist_id, {})
                    new_items = {item.video_id: item for item in result.items}
                    for video_id in old_items.keys() - new_items.keys():
                        old = old_items[video_id]
                        connection.execute(
                            "INSERT INTO change_events(snapshot_id,previous_snapshot_id,playlist_id,video_id,"
                            "change_type,previous_title,previous_channel_title,detected_at) VALUES(?,?,?,?,?,?,?,?)",
                            (snapshot_id, previous_id, result.playlist_id, video_id, "missing", old["title"], old["channel_title"], now),
                        )
                    for video_id in new_items.keys() - old_items.keys():
                        item = new_items[video_id]
                        connection.execute(
                            "INSERT INTO change_events(snapshot_id,previous_snapshot_id,playlist_id,video_id,"
                            "change_type,current_title,current_channel_title,detected_at) VALUES(?,?,?,?,?,?,?,?)",
                            (snapshot_id, previous_id, result.playlist_id, video_id, "added", item.title, item.channel_title, now),
                        )
                    for video_id in old_items.keys() & new_items.keys():
                        old, item = old_items[video_id], new_items[video_id]
                        if old["title"] != item.title or old["channel_title"] != item.channel_title:
                            connection.execute(
                                "INSERT INTO change_events(snapshot_id,previous_snapshot_id,playlist_id,video_id,"
                                "change_type,previous_title,previous_channel_title,current_title,current_channel_title,detected_at) "
                                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (snapshot_id, previous_id, result.playlist_id, video_id, "metadata_changed", old["title"], old["channel_title"], item.title, item.channel_title, now),
                            )
            for playback in playability.values():
                connection.execute(
                    "INSERT INTO playability_checks(snapshot_id,video_id,status,category,reason,"
                    "current_title,current_author,checked_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        snapshot_id,
                        playback.video_id,
                        playback.status,
                        playback.category,
                        playback.reason,
                        playback.current_title,
                        playback.current_author,
                        now,
                    ),
                )
        return snapshot_id
    finally:
        connection.close()


def command_collect(args: argparse.Namespace) -> int:
    playlist_file = Path(args.playlist_file)
    db_path = Path(args.db)
    urls = read_playlist_urls(playlist_file)
    collector = choose_collector(args.provider)
    started_at = utc_now()
    results: list[PlaylistResult] = []
    for index, url in enumerate(urls, 1):
        print(f"[{index}/{len(urls)}] {url}", flush=True)
        result = collector.collect(url)
        results.append(result)
        print(
            f"  {result.title}: {len(result.items)}개, 숨김 보고 {result.hidden_count}개",
            flush=True,
        )
    for warning in validate_results(results, len(urls), db_path, args.allow_large_drop):
        print(f"경고: {warning}", file=sys.stderr)
    print("고유 영상의 실제 재생 상태를 확인합니다...", flush=True)
    playability = PlayabilityChecker(workers=args.check_workers).check_all(results)
    check_errors = sum(
        result.category == "check_error" for result in playability.values()
    )
    if check_errors > max(5, len(playability) // 100):
        playability, reused, unknown = merge_playability_with_previous(
            db_path, results, playability
        )
        print(
            f"경고: YouTube가 재생 확인 {check_errors}개를 차단했습니다. "
            f"이전 상태 {reused}개 유지, 신규 확인 보류 {unknown}개로 저장합니다.",
            file=sys.stderr,
        )
    unavailable_count = sum(
        result.category == "unavailable" for result in playability.values()
    )
    restricted_count = sum(
        result.category == "restricted" for result in playability.values()
    )
    snapshot_id = write_snapshot(
        db_path,
        results,
        playability,
        collector,
        started_at,
        hashlib.sha256(playlist_file.read_bytes()).hexdigest(),
    )
    print(
        f"스냅샷 #{snapshot_id} 저장 완료: {sum(len(r.items) for r in results)}개, "
        f"재생 불가 {unavailable_count}개, 제한 {restricted_count}개"
    )
    return 0


def command_history(args: argparse.Namespace) -> int:
    connection = connect_db(Path(args.db), read_only=True)
    try:
        for row in connection.execute("SELECT * FROM snapshots ORDER BY id DESC"):
            print(
                f"#{row['id']} {row['completed_at']} {row['provider']} "
                f"플레이리스트 {row['playlist_count']}개 / 영상 {row['item_count']}개"
            )
    finally:
        connection.close()
    return 0


def command_changes(args: argparse.Namespace) -> int:
    connection = connect_db(Path(args.db), read_only=True)
    try:
        snapshot_id = args.snapshot_id or _latest_snapshot_id(connection)
        if snapshot_id is None:
            print("저장된 스냅샷이 없습니다.")
            return 0
        rows = connection.execute(
            "SELECT c.*, COALESCE(p.title,c.playlist_id) AS playlist_title "
            "FROM change_events c LEFT JOIN playlists p ON p.playlist_id=c.playlist_id "
            "WHERE c.snapshot_id=? ORDER BY c.playlist_id,c.change_type,c.id",
            (snapshot_id,),
        ).fetchall()
        print(f"스냅샷 #{snapshot_id} 변경 {len(rows)}건")
        for row in rows:
            title = row["previous_title"] if row["change_type"] == "missing" else row["current_title"]
            channel = row["previous_channel_title"] if row["change_type"] == "missing" else row["current_channel_title"]
            print(f"[{row['change_type']}] {row['playlist_title']} | {title} | {channel} | {row['video_id']}")
    finally:
        connection.close()
    return 0


def command_unavailable(args: argparse.Namespace) -> int:
    connection = connect_db(Path(args.db), read_only=True)
    try:
        snapshot_id = args.snapshot_id or _latest_snapshot_id(connection)
        if snapshot_id is None:
            print("저장된 스냅샷이 없습니다.")
            return 0
        rows = connection.execute(
            "SELECT pc.category,pc.status,pc.reason,pi.video_id,"
            "CASE WHEN pi.title='[Unavailable video]' THEN COALESCE(vr.title,pi.title) ELSE pi.title END AS title,"
            "CASE WHEN pi.title='[Unavailable video]' THEN COALESCE(vr.channel_title,pi.channel_title) ELSE pi.channel_title END AS channel_title,"
            "vr.source_db AS recovery_source,"
            "GROUP_CONCAT(DISTINCT sp.title) AS playlist_titles "
            "FROM playability_checks pc "
            "JOIN playlist_items pi USING(snapshot_id,video_id) "
            "JOIN snapshot_playlists sp USING(snapshot_id,playlist_id) "
            "LEFT JOIN video_metadata_recoveries vr ON vr.video_id=pc.video_id "
            "WHERE pc.snapshot_id=? AND pc.category!='playable' "
            "GROUP BY pc.video_id ORDER BY pc.category,playlist_titles,pi.title",
            (snapshot_id,),
        ).fetchall()
        print(f"스냅샷 #{snapshot_id} 재생 문제 {len(rows)}개")
        for row in rows:
            print(
                f"[{row['category']}/{row['status']}] {row['playlist_titles']} | "
                f"{row['title']} | {row['channel_title']} | {row['video_id']} | "
                f"{row['reason'] or ''}"
                + (" | 과거 순번으로 제목 복원" if row["recovery_source"] else "")
            )
    finally:
        connection.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YouTube 플레이리스트 스냅샷 저장기")
    subparsers = parser.add_subparsers(dest="command")
    collect = subparsers.add_parser("collect", help="현재 플레이리스트를 새 스냅샷으로 저장")
    collect.add_argument("--playlist-file", default=DEFAULT_PLAYLIST_FILE)
    collect.add_argument("--db", default=DEFAULT_DB)
    collect.add_argument("--provider", choices=("auto", "yt-dlp", "youtube-api"), default="auto")
    collect.add_argument("--allow-large-drop", action="store_true")
    collect.add_argument("--check-workers", type=int, default=6)
    collect.set_defaults(handler=command_collect)
    history = subparsers.add_parser("history", help="저장된 스냅샷 목록")
    history.add_argument("--db", default=DEFAULT_DB)
    history.set_defaults(handler=command_history)
    changes = subparsers.add_parser("changes", help="스냅샷의 실제 영상 ID 변경 목록")
    changes.add_argument("--db", default=DEFAULT_DB)
    changes.add_argument("--snapshot-id", type=int)
    changes.set_defaults(handler=command_changes)
    unavailable = subparsers.add_parser("unavailable", help="실제 재생 불가 영상 목록")
    unavailable.add_argument("--db", default=DEFAULT_DB)
    unavailable.add_argument("--snapshot-id", type=int)
    unavailable.set_defaults(handler=command_unavailable)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["collect", *(argv or [])])
    try:
        return args.handler(args)
    except (CollectionError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
