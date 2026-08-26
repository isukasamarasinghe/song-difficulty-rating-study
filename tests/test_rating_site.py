from __future__ import annotations

import unittest
from collections import Counter

from rating_app import create_app
from rating_app.storage import SupabaseRepository


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

    def next_song(
        self,
        rater_key: str,
        minimum_raters: int = 3,
        minimum_agreement: float = 0.60,
    ):
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


class InMemorySupabaseRepository(SupabaseRepository):
    def __init__(self, songs: list[dict], ratings: list[dict]) -> None:
        self._songs = songs
        self._ratings = ratings

    def songs(self, enabled_only: bool = True) -> list[dict]:
        return self._songs

    def ratings(self) -> list[dict]:
        return self._ratings


class ConsensusAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.songs = [
            {"song_id": song_id, "title": song_id, "artist": ""}
            for song_id in ("accepted", "tied", "low", "needs")
        ]
        self.ratings = []

        def add(song_id: str, difficulty: str, rater_key: str) -> None:
            self.ratings.append(
                {
                    "song_id": song_id,
                    "difficulty": difficulty,
                    "rater_key": rater_key,
                }
            )

        add("accepted", "Beginner", "accepted-1")
        add("accepted", "Beginner", "accepted-2")
        add("accepted", "Beginner", "accepted-3")
        add("tied", "Beginner", "all")
        add("tied", "Beginner", "tied-2")
        add("tied", "Intermediate", "tied-3")
        add("tied", "Intermediate", "tied-4")
        add("low", "Beginner", "low-1")
        add("low", "Intermediate", "all")
        add("low", "Intermediate", "low-3")
        add("low", "Advanced", "low-4")
        add("needs", "Advanced", "all")

        self.repository = InMemorySupabaseRepository(self.songs, self.ratings)

    def test_only_unresolved_songs_are_assigned(self) -> None:
        song, rated, total = self.repository.next_song("new-musician")

        self.assertEqual(song["song_id"], "needs")
        self.assertEqual(rated, 0)
        self.assertEqual(total, 3)

    def test_participant_never_receives_an_accepted_or_repeated_song(self) -> None:
        song, rated, total = self.repository.next_song("all")

        self.assertIsNone(song)
        self.assertEqual(rated, 3)
        self.assertEqual(total, 3)


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
            self.assertIn(b"No unresolved songs are available to you", rated.data)

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
