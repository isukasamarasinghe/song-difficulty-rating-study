from __future__ import annotations

import hashlib
import mimetypes
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests


DIFFICULTIES = ("Beginner", "Intermediate", "Advanced")
AUDIO_EXTENSIONS = {".mp3", ".wav"}


class SupabaseError(RuntimeError):
    pass


def _title_artist(path: Path) -> tuple[str, str]:
    stem = path.stem.replace("_", " ").strip()
    if stem.endswith("]") and " [" in stem:
        possible_id = stem.rsplit(" [", 1)[1][:-1]
        if 6 <= len(possible_id) <= 20:
            stem = stem.rsplit(" [", 1)[0].strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip()
    return stem, ""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SupabaseRepository:
    def __init__(self, url: str, secret_key: str, bucket: str = "rating-audio"):
        self.url = url.rstrip("/")
        self.secret_key = secret_key.strip()
        self.bucket = bucket.strip()
        if not self.url or not self.secret_key or not self.bucket:
            raise ValueError(
                "SUPABASE_URL, SUPABASE_SECRET_KEY, and SUPABASE_BUCKET are required"
            )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "apikey": self.secret_key,
                "Accept": "application/json",
                "User-Agent": "song-difficulty-rating-study/1.0",
            }
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | list | None = None,
        headers: dict | None = None,
        timeout: int = 30,
    ) -> requests.Response:
        try:
            response = self.session.request(
                method,
                f"{self.url}{path}",
                params=params,
                json=json,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise SupabaseError("Could not connect to the study database") from exc
        if not response.ok:
            detail = response.text[:500]
            raise SupabaseError(
                f"Supabase request failed ({response.status_code}): {detail}"
            )
        return response

    def _get_rows(self, table: str, params: dict | None = None) -> list[dict]:
        response = self._request("GET", f"/rest/v1/{table}", params=params)
        return list(response.json())

    def songs(self, enabled_only: bool = True) -> list[dict]:
        params = {"select": "*", "order": "artist.asc,title.asc"}
        if enabled_only:
            params["enabled"] = "eq.true"
        return self._get_rows("songs", params)

    def ratings(self) -> list[dict]:
        return self._get_rows("ratings", {"select": "*", "order": "rated_at.asc"})

    def next_song(self, rater_key: str) -> tuple[dict | None, int, int]:
        songs = self.songs()
        ratings = self.ratings()
        own_song_ids = {
            row["song_id"] for row in ratings if row["rater_key"] == rater_key
        }
        rating_counts = Counter(row["song_id"] for row in ratings)
        candidates = [song for song in songs if song["song_id"] not in own_song_ids]
        if not candidates:
            return None, len(own_song_ids), len(songs)

        minimum_count = min(rating_counts[song["song_id"]] for song in candidates)
        candidates = [
            song
            for song in candidates
            if rating_counts[song["song_id"]] == minimum_count
        ]
        candidates.sort(
            key=lambda song: hashlib.sha256(
                f"{rater_key}:{song['song_id']}".encode("utf-8")
            ).hexdigest()
        )
        return candidates[0], len(own_song_ids), len(songs)

    def get_song(self, song_id: str) -> dict | None:
        rows = self._get_rows(
            "songs",
            {
                "select": "*",
                "song_id": f"eq.{song_id}",
                "enabled": "eq.true",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def save_rating(self, rating: dict) -> None:
        payload = {
            **rating,
            "rated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._request(
            "POST",
            "/rest/v1/ratings",
            params={"on_conflict": "song_id,rater_key"},
            json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def dashboard_stats(self) -> dict:
        songs = self.songs()
        ratings = self.ratings()
        counts = Counter(row["song_id"] for row in ratings)
        song_rows = [
            {**song, "rating_count": counts[song["song_id"]]} for song in songs
        ]
        song_rows.sort(key=lambda row: (row["rating_count"], row["artist"], row["title"]))
        return {
            "total_songs": len(songs),
            "total_ratings": len(ratings),
            "total_raters": len({row["rater_key"] for row in ratings}),
            "songs_with_three": sum(row["rating_count"] >= 3 for row in song_rows),
            "songs": song_rows,
        }

    def rating_rows(self) -> list[dict]:
        songs = {row["song_id"]: row for row in self.songs(enabled_only=False)}
        output = []
        for rating in self.ratings():
            song = songs.get(rating["song_id"], {})
            output.append(
                {
                    "song_id": rating["song_id"],
                    "title": song.get("title", ""),
                    "artist": song.get("artist", ""),
                    "rater_id": rating["rater_id"],
                    "instrument": rating["instrument"],
                    "experience_years": rating["experience_years"],
                    "difficulty": rating["difficulty"],
                    "confidence": rating["confidence"],
                    "comment": rating.get("comment", ""),
                    "rated_at": rating["rated_at"],
                }
            )
        return output

    def consensus_rows(
        self, minimum_raters: int, minimum_agreement: float
    ) -> list[dict]:
        ratings_by_song: dict[str, list[str]] = {}
        for rating in self.ratings():
            ratings_by_song.setdefault(rating["song_id"], []).append(
                rating["difficulty"]
            )

        output = []
        for song in self.songs():
            labels = ratings_by_song.get(song["song_id"], [])
            counts = Counter(labels)
            num_ratings = len(labels)
            agreement = 0.0
            consensus = ""
            status = f"needs at least {minimum_raters} ratings"
            if num_ratings >= minimum_raters and counts:
                top_count = max(counts.values())
                winners = [
                    label for label, count in counts.items() if count == top_count
                ]
                agreement = top_count / num_ratings
                if len(winners) > 1:
                    status = "tied ratings"
                elif agreement < minimum_agreement:
                    status = f"agreement below {minimum_agreement:.0%}"
                else:
                    consensus = winners[0]
                    status = "accepted"

            output.append(
                {
                    "song_id": song["song_id"],
                    "audio_sha256": song["sha256"],
                    "title": song["title"],
                    "artist": song["artist"],
                    "difficulty": consensus,
                    "num_ratings": num_ratings,
                    "agreement": f"{agreement:.4f}",
                    "beginner_ratings": counts.get("Beginner", 0),
                    "intermediate_ratings": counts.get("Intermediate", 0),
                    "advanced_ratings": counts.get("Advanced", 0),
                    "status": status,
                }
            )
        return output

    def signed_audio_url(self, storage_path: str, expires_in: int = 900) -> str:
        encoded = quote(f"{self.bucket}/{storage_path}", safe="/")
        response = self._request(
            "POST",
            f"/storage/v1/object/sign/{encoded}",
            json={"expiresIn": expires_in},
        )
        signed_url = response.json().get("signedURL", "")
        if not signed_url:
            raise SupabaseError("Supabase did not return a signed audio URL")
        if signed_url.startswith("http://") or signed_url.startswith("https://"):
            return signed_url
        if signed_url.startswith("/storage/v1/"):
            return f"{self.url}{signed_url}"
        return f"{self.url}/storage/v1{signed_url}"

    def ensure_private_bucket(self) -> None:
        response = self.session.get(
            f"{self.url}/storage/v1/bucket/{quote(self.bucket, safe='')}", timeout=30
        )
        if response.status_code == 404:
            self._request(
                "POST",
                "/storage/v1/bucket",
                json={
                    "id": self.bucket,
                    "name": self.bucket,
                    "public": False,
                    "file_size_limit": 50 * 1024 * 1024,
                    "allowed_mime_types": ["audio/mpeg", "audio/wav", "audio/x-wav"],
                },
            )
        elif not response.ok:
            raise SupabaseError(
                f"Could not inspect the storage bucket ({response.status_code})"
            )

    def upload_song(self, path: Path) -> dict:
        audio_hash = file_sha256(path)
        song_id = audio_hash[:16]
        storage_path = f"{song_id}{path.suffix.casefold()}"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = quote(f"{self.bucket}/{storage_path}", safe="/")
        try:
            with path.open("rb") as handle:
                response = self.session.post(
                    f"{self.url}/storage/v1/object/{encoded}",
                    data=handle,
                    headers={"Content-Type": content_type, "x-upsert": "true"},
                    timeout=300,
                )
        except requests.RequestException as exc:
            raise SupabaseError(f"Could not upload {path.name}") from exc
        if not response.ok:
            raise SupabaseError(
                f"Upload failed for {path.name} ({response.status_code}): "
                f"{response.text[:300]}"
            )

        title, artist = _title_artist(path)
        song = {
            "song_id": song_id,
            "filename": path.name,
            "storage_path": storage_path,
            "sha256": audio_hash,
            "title": title,
            "artist": artist,
            "enabled": True,
        }
        self._request(
            "POST",
            "/rest/v1/songs",
            params={"on_conflict": "song_id"},
            json=song,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        return song
