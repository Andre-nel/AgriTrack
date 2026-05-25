from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.auth import login_web_user, logout_web_user, safe_next_url
from app.models import User

bp = Blueprint("auth", __name__)


@bp.get("/login")
def login():
    next_url = safe_next_url(request.args.get("next"))
    if getattr(g, "web_user", None) is not None:
        return redirect(next_url)
    return render_template("auth/login.html", email="", next_url=next_url)


@bp.post("/login")
def login_post():
    email = " ".join((request.form.get("email") or "").strip().lower().split())
    password = request.form.get("password") or ""
    next_url = safe_next_url(request.form.get("next"))
    user = User.query.filter_by(email=email).first()

    if user is None or not user.active or not user.check_password(password):
        flash("Invalid email or password.", "error")
        return render_template("auth/login.html", email=email, next_url=next_url), 401

    login_web_user(user)
    return redirect(next_url)


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    logout_web_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
