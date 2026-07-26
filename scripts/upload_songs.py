from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rating_app import _load_local_environment  # noqa: E402
from rating_app.storage import (  # noqa: E402
    AUDIO_EXTENSIONS,
    SupabaseError,
    SupabaseRepository,
    file_sha256,
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Upload approved study songs to private Supabase Storage."
    )
    parser.add_argument("source", type=Path, help="Folder containing MP3/WAV songs")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Upload songs again even if their SHA-256 hash already exists",
    )
    args = parser.parse_args()

    _load_local_environment(PROJECT_ROOT / ".env")
    source = args.source.expanduser().resolve()
    if not source.is_dir():
        parser.error(f"Song folder does not exist: {source}")

    repository = SupabaseRepository(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_SECRET_KEY", ""),
        os.environ.get("SUPABASE_BUCKET", "rating-audio"),
    )
    repository.ensure_private_bucket()

    paths = sorted(
        (
            path
            for path in source.rglob("*")
            if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
        ),
        key=lambda path: str(path).casefold(),
    )
    if not paths:
        print(f"No MP3 or WAV files found in {source}")
        return 1

    existing_hashes = {
        song["sha256"] for song in repository.songs(enabled_only=False)
    }
    uploaded = skipped = failed = 0
    for index, path in enumerate(paths, start=1):
        try:
            audio_hash = file_sha256(path)
            if audio_hash in existing_hashes and not args.force:
                skipped += 1
                print(f"[{index}/{len(paths)}] Already uploaded: {path.name}")
                continue
            repository.upload_song(path)
            existing_hashes.add(audio_hash)
            uploaded += 1
            print(f"[{index}/{len(paths)}] Uploaded: {path.name}")
        except (OSError, SupabaseError) as exc:
            failed += 1
            print(f"[{index}/{len(paths)}] FAILED: {path.name}: {exc}", file=sys.stderr)

    print(f"Finished: {uploaded} uploaded, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
