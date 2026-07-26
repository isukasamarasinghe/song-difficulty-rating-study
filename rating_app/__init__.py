from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .routes import rating_bp
from .storage import SupabaseRepository


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

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "local-development-key"),
        INVITE_TOKEN=os.environ.get("INVITE_TOKEN", ""),
        ADMIN_TOKEN=os.environ.get("ADMIN_TOKEN", ""),
        SUPABASE_URL=os.environ.get("SUPABASE_URL", ""),
        SUPABASE_SECRET_KEY=os.environ.get("SUPABASE_SECRET_KEY", ""),
        SUPABASE_BUCKET=os.environ.get("SUPABASE_BUCKET", "rating-audio"),
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

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    repository = app.config.get("REPOSITORY")
    if repository is None:
        repository = SupabaseRepository(
            app.config["SUPABASE_URL"],
            app.config["SUPABASE_SECRET_KEY"],
            app.config["SUPABASE_BUCKET"],
        )
    app.extensions["rating_repository"] = repository
    app.register_blueprint(rating_bp)
    return app
