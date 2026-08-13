import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import main


def item(video_id, title, channel, position=1):
    return main.VideoItem(
        position=position,
        source_position=position,
        playlist_item_id=None,
        video_id=video_id,
        title=title,
        channel_id=None,
        channel_title=channel,
        availability="available",
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
    )


def result(items, title="aa", playlist_id="PL_TEST"):
    normalized = tuple(replace(value, position=index, source_position=index) for index, value in enumerate(items, 1))
    return main.PlaylistResult(
        playlist_id=playlist_id,
        playlist_url=f"https://www.youtube.com/playlist?list={playlist_id}",
        title=title,
        items=normalized,
        reported_count=len(normalized),
        hidden_count=0,
    )


class FakeCollector:
    name = "fake"
    version = "1"


class MainTests(unittest.TestCase):
    def test_extract_playlist_id(self):
        self.assertEqual(
            main.extract_playlist_id("https://www.youtube.com/playlist?list=PL_abc-123"),
            "PL_abc-123",
        )
        with self.assertRaises(ValueError):
            main.extract_playlist_id("https://www.youtube.com/watch?v=abc")

    def test_snapshot_is_immutable_and_tracks_id_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "v2.db"
            first = result([item("aaaaaaaaaaa", "Old title", "Artist")])
            second = result([item("bbbbbbbbbbb", "New title", "Artist")])
            first_playability = {
                "aaaaaaaaaaa": main.PlayabilityResult(
                    "aaaaaaaaaaa", "OK", "playable", None, "Old title", "Artist"
                )
            }
            second_playability = {
                "bbbbbbbbbbb": main.PlayabilityResult(
                    "bbbbbbbbbbb", "UNPLAYABLE", "unavailable", "Video unavailable", "New title", "Artist"
                )
            }
            first_id = main.write_snapshot(db, [first], first_playability, FakeCollector(), main.utc_now(), "hash")
            second_id = main.write_snapshot(db, [second], second_playability, FakeCollector(), main.utc_now(), "hash")
            connection = sqlite3.connect(db)
            self.assertEqual((first_id, second_id), (1, 2))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM playlist_items").fetchone()[0], 2)
            changes = dict(connection.execute("SELECT change_type,COUNT(*) FROM change_events GROUP BY change_type"))
            self.assertEqual(changes, {"added": 1, "missing": 1})
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(
                connection.execute(
                    "SELECT category FROM playability_checks WHERE snapshot_id=2"
                ).fetchone()[0],
                "unavailable",
            )
            connection.close()

    def test_playability_categories(self):
        self.assertEqual(main.categorize_playability("OK", None), "playable")
        self.assertEqual(
            main.categorize_playability("UNPLAYABLE", "Video unavailable"),
            "unavailable",
        )
        self.assertEqual(
            main.categorize_playability("LOGIN_REQUIRED", "비공개 동영상입니다."),
            "unavailable",
        )
        self.assertEqual(
            main.categorize_playability("LOGIN_REQUIRED", "age restriction"),
            "restricted",
        )
        self.assertEqual(
            main.categorize_playability("LOGIN_REQUIRED", "로그인하여 봇이 아님을 확인하세요."),
            "check_error",
        )

    def test_playability_fallback_reuses_previous_and_marks_new_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "v2.db"
            previous = result([item("aaaaaaaaaaa", "Known", "Artist")])
            main.write_snapshot(
                db,
                [previous],
                {
                    "aaaaaaaaaaa": main.PlayabilityResult(
                        "aaaaaaaaaaa", "OK", "playable", None, "Known", "Artist"
                    )
                },
                FakeCollector(),
                main.utc_now(),
                "hash",
            )
            current = result(
                [
                    item("aaaaaaaaaaa", "Known", "Artist"),
                    item("bbbbbbbbbbb", "New", "Artist", position=2),
                ]
            )
            failures = {
                video_id: main.PlayabilityResult(
                    video_id, "CHECK_ERROR", "check_error", "bot check", None, None
                )
                for video_id in ("aaaaaaaaaaa", "bbbbbbbbbbb")
            }

            merged, reused, unknown = main.merge_playability_with_previous(
                db, [current], failures
            )

            self.assertEqual((reused, unknown), (1, 1))
            self.assertEqual(merged["aaaaaaaaaaa"].category, "playable")
            self.assertTrue(merged["aaaaaaaaaaa"].status.startswith("STALE_"))
            self.assertEqual(merged["bbbbbbbbbbb"].category, "unknown")

    def test_validation_rejects_duplicate_playlists(self):
        one = result([item("aaaaaaaaaaa", "One", "Artist")])
        with self.assertRaises(main.CollectionError):
            main.validate_results([one, one], 2, Path("missing.db"), False)

    def test_validation_rejects_non_contiguous_positions(self):
        broken = main.PlaylistResult(
            playlist_id="PL_TEST",
            playlist_url="https://www.youtube.com/playlist?list=PL_TEST",
            title="aa",
            items=(item("aaaaaaaaaaa", "One", "Artist", position=2),),
            reported_count=1,
            hidden_count=0,
        )
        with self.assertRaises(main.CollectionError):
            main.validate_results([broken], 1, Path("missing.db"), False)


if __name__ == "__main__":
    unittest.main()
