from __future__ import annotations

import csv
import hmac
import io
import re
import secrets
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from .storage import (
    DIFFICULTIES,
    consensus_rows,
    dashboard_stats,
    get_song,
    next_song,
    rating_rows,
    save_rating,
)


rating_bp = Blueprint("rating", __name__)
RATER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,40}$")
INSTRUMENTS = (
    "Guitar",
    "Piano / Keyboard",
    "Bass",
    "Ukulele",
    "Vocals",
    "Drums / Percussion",
    "Other",
)


def _matches(provided: str, expected: str) -> bool:
    return bool(provided and expected and hmac.compare_digest(provided, expected))


@rating_bp.before_app_request
def protect_site():
    if request.endpoint in {"rating.health", "static"}:
        return None

    if request.path.startswith("/admin"):
        if session.get("rating_admin") is True:
            return None
        provided = request.args.get("admin_token", "")
        expected = current_app.config["ADMIN_TOKEN"]
        if request.method == "GET" and _matches(provided, expected):
            session["rating_admin"] = True
            return redirect(url_for("rating.admin"))
        abort(404)

    expected = current_app.config["INVITE_TOKEN"]
    if not expected or session.get("rating_access") is True:
        return None
    provided = request.args.get("access", "")
    if request.method == "GET" and _matches(provided, expected):
        session["rating_access"] = True
        return redirect(url_for("rating.index"))
    abort(404)


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _require_csrf() -> None:
    expected = session.get("csrf_token", "")
    provided = request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(provided, expected):
        abort(400, "The form expired. Reload the page and try again.")


@rating_bp.app_context_processor
def template_values():
    return {"csrf_token": _csrf_token()}


@rating_bp.get("/health")
def health():
    return {"status": "ok"}


@rating_bp.get("/")
def index():
    rater_key = session.get("rater_key")
    song = None
    rated = total = 0
    if rater_key:
        song, rated, total = next_song(current_app.config["DATABASE_PATH"], rater_key)
    return render_template(
        "rate.html",
        song=song,
        rated=rated,
        total=total,
        participant_ready=bool(rater_key),
        instruments=INSTRUMENTS,
        difficulties=DIFFICULTIES,
        participant={
            "rater_id": session.get("rater_id", ""),
            "instrument": session.get("instrument", ""),
            "experience_years": session.get("experience_years", ""),
        },
    )


@rating_bp.post("/participant")
def participant():
    _require_csrf()
    rater_id = request.form.get("rater_id", "").strip()
    instrument = request.form.get("instrument", "").strip()
    try:
        experience_years = float(request.form.get("experience_years", ""))
    except ValueError:
        experience_years = -1

    errors = []
    if not RATER_ID_PATTERN.fullmatch(rater_id):
        errors.append("Anonymous ID must be 2-40 letters, numbers, underscores, or hyphens.")
    if instrument not in INSTRUMENTS:
        errors.append("Select your main instrument.")
    if not 0 <= experience_years <= 80:
        errors.append("Experience must be between 0 and 80 years.")
    if request.form.get("consent") != "yes":
        errors.append("Consent is required to participate.")
    if errors:
        for error in errors:
            flash(error, "danger")
        return redirect(url_for("rating.index"))

    session.permanent = True
    session["rater_id"] = rater_id
    session["rater_key"] = rater_id.casefold()
    session["instrument"] = instrument
    session["experience_years"] = f"{experience_years:g}"
    return redirect(url_for("rating.index"))


@rating_bp.post("/rate")
def rate():
    _require_csrf()
    if not session.get("rater_key"):
        abort(400, "Participant details are required.")

    song_id = request.form.get("song_id", "").strip()
    difficulty = request.form.get("difficulty", "").strip()
    try:
        confidence = int(request.form.get("confidence", ""))
    except ValueError:
        confidence = 0
    song = get_song(current_app.config["DATABASE_PATH"], song_id)

    errors = []
    if song is None:
        errors.append("This song is no longer available.")
    if difficulty not in DIFFICULTIES:
        errors.append("Select a difficulty.")
    if confidence not in range(1, 6):
        errors.append("Confidence must be between 1 and 5.")
    if errors:
        for error in errors:
            flash(error, "danger")
        return redirect(url_for("rating.index"))

    save_rating(
        current_app.config["DATABASE_PATH"],
        {
            "song_id": song_id,
            "rater_id": session["rater_id"],
            "rater_key": session["rater_key"],
            "instrument": session["instrument"],
            "experience_years": float(session["experience_years"]),
            "difficulty": difficulty,
            "confidence": confidence,
            "comment": request.form.get("comment", "").strip()[:1000],
        },
    )
    flash("Rating saved. Thank you!", "success")
    return redirect(url_for("rating.index"))


@rating_bp.post("/participant/reset")
def reset_participant():
    _require_csrf()
    for key in ("rater_id", "rater_key", "instrument", "experience_years"):
        session.pop(key, None)
    return redirect(url_for("rating.index"))


@rating_bp.get("/audio/<song_id>")
def audio(song_id: str):
    if not session.get("rater_key"):
        abort(404)
    song = get_song(current_app.config["DATABASE_PATH"], song_id)
    if song is None:
        abort(404)
    audio_root = current_app.config["AUDIO_DIR"].resolve()
    path = (audio_root / song["filename"]).resolve()
    if audio_root not in path.parents or not path.is_file():
        abort(404)
    return send_file(path, conditional=True)


@rating_bp.get("/admin")
def admin():
    return render_template(
        "admin.html", stats=dashboard_stats(current_app.config["DATABASE_PATH"])
    )


def _csv_download(rows: list[dict], filename: str) -> Response:
    if rows:
        fieldnames = list(rows[0])
    else:
        fieldnames = []
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    if fieldnames:
        writer.writeheader()
        writer.writerows(rows)
    response = Response("\ufeff" + output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@rating_bp.get("/admin/export/ratings.csv")
def export_ratings():
    return _csv_download(
        rating_rows(current_app.config["DATABASE_PATH"]), "musician_ratings.csv"
    )


@rating_bp.get("/admin/export/consensus.csv")
def export_consensus():
    return _csv_download(
        consensus_rows(
            current_app.config["DATABASE_PATH"],
            current_app.config["MINIMUM_RATERS"],
            current_app.config["MINIMUM_AGREEMENT"],
        ),
        "difficulty_consensus.csv",
    )
