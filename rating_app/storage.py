from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


AUDIO_EXTENSIONS = {".mp3", ".wav"}
DIFFICULTIES = ("Beginner", "Intermediate", "Advanced")


@contextmanager
def _connect(database_path: Path):
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS songs (
                song_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                artist TEXT NOT NULL DEFAULT '',
                file_size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_id TEXT NOT NULL REFERENCES songs(song_id),
                rater_id TEXT NOT NULL,
                rater_key TEXT NOT NULL,
                instrument TEXT NOT NULL,
                experience_years REAL NOT NULL,
                difficulty TEXT NOT NULL CHECK (
                    difficulty IN ('Beginner', 'Intermediate', 'Advanced')
                ),
                confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
                comment TEXT NOT NULL DEFAULT '',
                rated_at TEXT NOT NULL,
                UNIQUE(song_id, rater_key)
            );

            CREATE INDEX IF NOT EXISTS ratings_rater_key_idx
                ON ratings(rater_key);
            CREATE INDEX IF NOT EXISTS ratings_song_id_idx
                ON ratings(song_id);
            """
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def sync_audio_catalog(database_path: Path, audio_dir: Path) -> int:
    audio_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(
        path
        for path in audio_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
    )

    with _connect(database_path) as connection:
        existing = {
            row["filename"]: row
            for row in connection.execute("SELECT * FROM songs").fetchall()
        }
        connection.execute("UPDATE songs SET enabled = 0")

        for path in files:
            relative_name = path.relative_to(audio_dir).as_posix()
            stat = path.stat()
            previous = existing.get(relative_name)
            if (
                previous
                and previous["file_size"] == stat.st_size
                and previous["mtime_ns"] == stat.st_mtime_ns
            ):
                audio_hash = previous["sha256"]
            else:
                audio_hash = _sha256(path)

            song_id = audio_hash[:16]
            title, artist = _title_artist(path)
            connection.execute(
                "DELETE FROM songs WHERE filename = ? AND song_id <> ?",
                (relative_name, song_id),
            )
            connection.execute(
                """
                INSERT INTO songs (
                    song_id, filename, sha256, title, artist,
                    file_size, mtime_ns, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(song_id) DO UPDATE SET
                    filename = excluded.filename,
                    title = excluded.title,
                    artist = excluded.artist,
                    file_size = excluded.file_size,
                    mtime_ns = excluded.mtime_ns,
                    enabled = 1
                """,
                (
                    song_id,
                    relative_name,
                    audio_hash,
                    title,
                    artist,
                    stat.st_size,
                    stat.st_mtime_ns,
                ),
            )
    return len(files)


def next_song(database_path: Path, rater_key: str) -> tuple[dict | None, int, int]:
    with _connect(database_path) as connection:
        songs = [
            dict(row)
            for row in connection.execute(
                """
                SELECT s.*, COUNT(r.id) AS rating_count
                FROM songs s
                LEFT JOIN ratings r ON r.song_id = s.song_id
                WHERE s.enabled = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM ratings own
                      WHERE own.song_id = s.song_id AND own.rater_key = ?
                  )
                GROUP BY s.song_id
                """,
                (rater_key,),
            ).fetchall()
        ]
        total = connection.execute(
            "SELECT COUNT(*) FROM songs WHERE enabled = 1"
        ).fetchone()[0]
        rated = connection.execute(
            "SELECT COUNT(*) FROM ratings WHERE rater_key = ?",
            (rater_key,),
        ).fetchone()[0]

    if not songs:
        return None, rated, total
    minimum_count = min(song["rating_count"] for song in songs)
    candidates = [song for song in songs if song["rating_count"] == minimum_count]
    candidates.sort(
        key=lambda song: hashlib.sha256(
            f"{rater_key}:{song['song_id']}".encode("utf-8")
        ).hexdigest()
    )
    return candidates[0], rated, total


def get_song(database_path: Path, song_id: str) -> dict | None:
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM songs WHERE song_id = ? AND enabled = 1", (song_id,)
        ).fetchone()
    return dict(row) if row else None


def save_rating(database_path: Path, rating: dict) -> None:
    rated_at = datetime.now(timezone.utc).isoformat()
    with _connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO ratings (
                song_id, rater_id, rater_key, instrument, experience_years,
                difficulty, confidence, comment, rated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(song_id, rater_key) DO UPDATE SET
                rater_id = excluded.rater_id,
                instrument = excluded.instrument,
                experience_years = excluded.experience_years,
                difficulty = excluded.difficulty,
                confidence = excluded.confidence,
                comment = excluded.comment,
                rated_at = excluded.rated_at
            """,
            (
                rating["song_id"],
                rating["rater_id"],
                rating["rater_key"],
                rating["instrument"],
                rating["experience_years"],
                rating["difficulty"],
                rating["confidence"],
                rating.get("comment", ""),
                rated_at,
            ),
        )


def dashboard_stats(database_path: Path) -> dict:
    with _connect(database_path) as connection:
        total_songs = connection.execute(
            "SELECT COUNT(*) FROM songs WHERE enabled = 1"
        ).fetchone()[0]
        total_ratings = connection.execute("SELECT COUNT(*) FROM ratings").fetchone()[0]
        total_raters = connection.execute(
            "SELECT COUNT(DISTINCT rater_key) FROM ratings"
        ).fetchone()[0]
        song_rows = connection.execute(
            """
            SELECT s.song_id, s.title, s.artist, COUNT(r.id) AS rating_count
            FROM songs s LEFT JOIN ratings r ON r.song_id = s.song_id
            WHERE s.enabled = 1
            GROUP BY s.song_id
            ORDER BY rating_count, s.artist, s.title
            """
        ).fetchall()
    songs = [dict(row) for row in song_rows]
    return {
        "total_songs": total_songs,
        "total_ratings": total_ratings,
        "total_raters": total_raters,
        "songs_with_three": sum(song["rating_count"] >= 3 for song in songs),
        "songs": songs,
    }


def rating_rows(database_path: Path) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT r.song_id, s.title, s.artist, r.rater_id, r.instrument,
                   r.experience_years, r.difficulty, r.confidence,
                   r.comment, r.rated_at
            FROM ratings r JOIN songs s ON s.song_id = r.song_id
            ORDER BY s.artist, s.title, r.rater_key
            """
        ).fetchall()
    return [dict(row) for row in rows]


def consensus_rows(
    database_path: Path, minimum_raters: int, minimum_agreement: float
) -> list[dict]:
    with _connect(database_path) as connection:
        songs = connection.execute(
            "SELECT song_id, title, artist FROM songs WHERE enabled = 1 ORDER BY artist, title"
        ).fetchall()
        ratings = connection.execute(
            "SELECT song_id, difficulty FROM ratings"
        ).fetchall()

    by_song: dict[str, list[str]] = {}
    for row in ratings:
        by_song.setdefault(row["song_id"], []).append(row["difficulty"])

    output = []
    for song in songs:
        labels = by_song.get(song["song_id"], [])
        counts = Counter(labels)
        num_ratings = len(labels)
        agreement = 0.0
        consensus = ""
        status = f"needs at least {minimum_raters} ratings"
        if num_ratings >= minimum_raters and counts:
            top_count = max(counts.values())
            winners = [label for label, count in counts.items() if count == top_count]
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
