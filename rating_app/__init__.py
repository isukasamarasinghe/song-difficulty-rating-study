from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .routes import rating_bp
from .storage import initialize_database, sync_audio_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_local_environment(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def create_app(test_config: dict | None = None) -> Flask:
    _load_local_environment(PROJECT_ROOT / ".env")

    data_root = Path(os.environ.get("DATA_ROOT", PROJECT_ROOT / "data"))
    audio_dir = Path(os.environ.get("AUDIO_DIR", data_root / "audio"))
    database_path = Path(
        os.environ.get("DATABASE_PATH", data_root / "ratings.sqlite3")
    )

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "local-development-key"),
        INVITE_TOKEN=os.environ.get("INVITE_TOKEN", ""),
        ADMIN_TOKEN=os.environ.get("ADMIN_TOKEN", ""),
        AUDIO_DIR=audio_dir,
        DATABASE_PATH=database_path,
        MINIMUM_RATERS=3,
        MINIMUM_AGREEMENT=0.60,
        MAX_CONTENT_LENGTH=16 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("RENDER") == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    )
    if test_config:
        app.config.update(test_config)

    app.config["AUDIO_DIR"] = Path(app.config["AUDIO_DIR"])
    app.config["DATABASE_PATH"] = Path(app.config["DATABASE_PATH"])
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    initialize_database(app.config["DATABASE_PATH"])
    sync_audio_catalog(app.config["DATABASE_PATH"], app.config["AUDIO_DIR"])
    app.register_blueprint(rating_bp)
    return app
