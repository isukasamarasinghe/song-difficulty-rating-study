from __future__ import annotations

import unittest
from unittest.mock import Mock

from rating_app.storage import SupabaseRepository


class SupabaseRepositoryTests(unittest.TestCase):
    def test_missing_bucket_response_creates_private_bucket(self) -> None:
        repository = SupabaseRepository(
            "https://project.supabase.co", "sb_secret_test", "rating-audio"
        )
        missing_response = Mock(status_code=400, ok=False)
        repository.session.get = Mock(return_value=missing_response)
        repository._request = Mock()

        repository.ensure_private_bucket()

        repository._request.assert_called_once_with(
            "POST",
            "/storage/v1/bucket",
            json={
                "id": "rating-audio",
                "name": "rating-audio",
                "public": False,
                "file_size_limit": 50 * 1024 * 1024,
                "allowed_mime_types": [
                    "audio/mpeg",
                    "audio/wav",
                    "audio/x-wav",
                ],
            },
        )

    def test_secret_is_sent_as_api_key_and_signed_url_is_expanded(self) -> None:
        repository = SupabaseRepository(
            "https://project.supabase.co/", "sb_secret_test", "rating-audio"
        )
        self.assertEqual(repository.session.headers["apikey"], "sb_secret_test")
        self.assertNotIn("Authorization", repository.session.headers)

        response = Mock()
        response.json.return_value = {
            "signedURL": "/object/sign/rating-audio/song.mp3?token=test"
        }
        repository._request = Mock(return_value=response)

        result = repository.signed_audio_url("song.mp3", expires_in=600)

        self.assertEqual(
            result,
            "https://project.supabase.co/storage/v1/object/sign/"
            "rating-audio/song.mp3?token=test",
        )
        repository._request.assert_called_once_with(
            "POST",
            "/storage/v1/object/sign/rating-audio/song.mp3",
            json={"expiresIn": 600},
        )


if __name__ == "__main__":
    unittest.main()
