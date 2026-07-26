from __future__ import annotations

import unittest
from collections import Counter

from rating_app import create_app


class FakeRepository:
    def __init__(self) -> None:
        self.song = {
            "song_id": "song-001",
            "filename": "Test Artist - Test Song.mp3",
            "storage_path": "song-001.mp3",
            "sha256": "a" * 64,
            "title": "Test Song",
            "artist": "Test Artist",
            "enabled": True,
        }
        self.saved_ratings: list[dict] = []

    def next_song(self, rater_key: str):
        completed = {
            row["song_id"]
            for row in self.saved_ratings
            if row["rater_key"] == rater_key
        }
        song = None if self.song["song_id"] in completed else self.song
        return song, len(completed), 1

    def get_song(self, song_id: str):
        return self.song if song_id == self.song["song_id"] else None

    def save_rating(self, rating: dict) -> None:
        self.saved_ratings = [
            row
            for row in self.saved_ratings
            if (row["song_id"], row["rater_key"])
            != (rating["song_id"], rating["rater_key"])
        ]
        self.saved_ratings.append({**rating, "rated_at": "2026-07-26T10:00:00Z"})

    def signed_audio_url(self, storage_path: str) -> str:
        assert storage_path == self.song["storage_path"]
        return "https://example.test/private-audio-link"

    def dashboard_stats(self) -> dict:
        return {
            "total_songs": 1,
            "total_ratings": len(self.saved_ratings),
            "total_raters": len({r["rater_key"] for r in self.saved_ratings}),
            "songs_with_three": 0,
            "songs": [{**self.song, "rating_count": len(self.saved_ratings)}],
        }

    def rating_rows(self) -> list[dict]:
        return [
            {
                "song_id": row["song_id"],
                "title": self.song["title"],
                "artist": self.song["artist"],
                "rater_id": row["rater_id"],
                "instrument": row["instrument"],
                "experience_years": row["experience_years"],
                "difficulty": row["difficulty"],
                "confidence": row["confidence"],
                "comment": row["comment"],
                "rated_at": row["rated_at"],
            }
            for row in self.saved_ratings
        ]

    def consensus_rows(self, minimum_raters: int, minimum_agreement: float):
        counts = Counter(row["difficulty"] for row in self.saved_ratings)
        total = len(self.saved_ratings)
        status = f"needs at least {minimum_raters} ratings"
        difficulty = ""
        agreement = 0.0
        if total >= minimum_raters:
            difficulty, top_count = counts.most_common(1)[0]
            agreement = top_count / total
            status = "accepted" if agreement >= minimum_agreement else "low agreement"
        return [
            {
                "song_id": self.song["song_id"],
                "audio_sha256": self.song["sha256"],
                "title": self.song["title"],
                "artist": self.song["artist"],
                "difficulty": difficulty,
                "num_ratings": total,
                "agreement": f"{agreement:.4f}",
                "beginner_ratings": counts.get("Beginner", 0),
                "intermediate_ratings": counts.get("Intermediate", 0),
                "advanced_ratings": counts.get("Advanced", 0),
                "status": status,
            }
        ]


class RatingSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "INVITE_TOKEN": "invite-secret",
                "ADMIN_TOKEN": "admin-secret",
                "SESSION_COOKIE_SECURE": False,
                "REPOSITORY": self.repository,
            }
        )

    def _enter_study(self, client) -> None:
        response = client.get("/?access=invite-secret", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as session:
            csrf = session["csrf_token"]
        response = client.post(
            "/participant",
            data={
                "csrf_token": csrf,
                "rater_id": "musician_01",
                "instrument": "Guitar",
                "experience_years": "5",
                "consent": "yes",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Test Song", response.data)

    def test_invitation_rating_audio_and_exports(self) -> None:
        with self.app.test_client() as client:
            self.assertEqual(client.get("/").status_code, 404)
            self._enter_study(client)
            with client.session_transaction() as session:
                csrf = session["csrf_token"]

            audio = client.get("/audio/song-001")
            self.assertEqual(audio.status_code, 302)
            self.assertEqual(
                audio.headers["Location"],
                "https://example.test/private-audio-link",
            )

            rated = client.post(
                "/rate",
                data={
                    "csrf_token": csrf,
                    "song_id": "song-001",
                    "difficulty": "Intermediate",
                    "confidence": "4",
                    "comment": "Several chord changes",
                },
                follow_redirects=True,
            )
            self.assertIn(b"All available songs are complete", rated.data)

        with self.app.test_client() as admin:
            dashboard = admin.get(
                "/admin?admin_token=admin-secret", follow_redirects=True
            )
            self.assertIn(b"Collection progress", dashboard.data)
            ratings = admin.get("/admin/export/ratings.csv")
            consensus = admin.get("/admin/export/consensus.csv")
            self.assertIn(b"musician_01", ratings.data)
            self.assertIn(b"needs at least 3 ratings", consensus.data)

    def test_invalid_csrf_is_rejected(self) -> None:
        with self.app.test_client() as client:
            client.get("/?access=invite-secret")
            response = client.post("/participant", data={"csrf_token": "wrong"})
            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
