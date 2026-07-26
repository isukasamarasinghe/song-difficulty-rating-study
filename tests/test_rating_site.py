from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rating_app import create_app


class RatingSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.audio_dir = root / "audio"
        self.audio_dir.mkdir()
        (self.audio_dir / "Test Artist - Test Song.mp3").write_bytes(
            b"ID3" + b"test-audio" * 300
        )
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "INVITE_TOKEN": "invite-secret",
                "ADMIN_TOKEN": "admin-secret",
                "AUDIO_DIR": self.audio_dir,
                "DATABASE_PATH": root / "ratings.sqlite3",
                "SESSION_COOKIE_SECURE": False,
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

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

            page = client.get("/")
            song_id = page.get_data(as_text=True).split('name="song_id" value="')[1].split('"')[0]
            audio = client.get(f"/audio/{song_id}", headers={"Range": "bytes=0-99"})
            self.assertEqual(audio.status_code, 206)
            audio.close()

            rated = client.post(
                "/rate",
                data={
                    "csrf_token": csrf,
                    "song_id": song_id,
                    "difficulty": "Intermediate",
                    "confidence": "4",
                    "comment": "Several chord changes",
                },
                follow_redirects=True,
            )
            self.assertIn(b"All available songs are complete", rated.data)
            rated.close()

        with self.app.test_client() as admin:
            dashboard = admin.get(
                "/admin?admin_token=admin-secret", follow_redirects=True
            )
            self.assertIn(b"Collection progress", dashboard.data)
            ratings = admin.get("/admin/export/ratings.csv")
            consensus = admin.get("/admin/export/consensus.csv")
            self.assertIn(b"musician_01", ratings.data)
            self.assertIn(b"needs at least 3 ratings", consensus.data)
            dashboard.close()
            ratings.close()
            consensus.close()

    def test_invalid_csrf_is_rejected(self) -> None:
        with self.app.test_client() as client:
            client.get("/?access=invite-secret")
            response = client.post(
                "/participant",
                data={"csrf_token": "wrong"},
            )
            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
