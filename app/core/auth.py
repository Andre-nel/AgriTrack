from urllib.parse import urlsplit

from flask import Flask, g, jsonify, redirect, request, session, url_for

from app.extensions import db
from app.models import User


PUBLIC_ENDPOINTS = {
    "static",
    "health",
    "api_v1.health",
    "auth.login",
    "auth.login_post",
    "auth.logout",
}


def register_session_auth(app: Flask) -> None:
    @app.before_request
    def require_web_login():
        g.web_user = _load_web_user()
        if not app.config.get("WEB_AUTH_REQUIRED", True):
            return None

        endpoint = request.endpoint or ""
        if endpoint in PUBLIC_ENDPOINTS or endpoint.startswith("mobile_api."):
            return None
        if g.web_user is not None:
            return None

        session.pop("web_user_id", None)
        if request.path.startswith("/api"):
            return (
                jsonify(
                    {
                        "error": {
                            "code": "authentication_required",
                            "message": "Web login is required",
                        }
                    }
                ),
                401,
            )

        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for("auth.login", next=next_url))


def login_web_user(user: User) -> None:
    session.clear()
    session.permanent = True
    session["web_user_id"] = str(user.id)


def logout_web_user() -> None:
    session.pop("web_user_id", None)


def safe_next_url(value: str | None) -> str:
    if not value:
        return url_for("web.dashboard")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/"):
        return url_for("web.dashboard")
    return value


def _load_web_user() -> User | None:
    user_id = session.get("web_user_id")
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    if user is None or not user.active:
        session.pop("web_user_id", None)
        return None
    return user
