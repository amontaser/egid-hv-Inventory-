"""Authentication routes — Flask-Login with username + password."""

from flask import Blueprint, request, redirect, url_for, render_template
from flask_login import login_user, logout_user, login_required

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        from app.models import User, db

        user = (
            db.session.query(User).filter_by(username=username, is_active=True).first()
        )
        if user:
            from werkzeug.security import check_password_hash

            if check_password_hash(user.password_hash, password):
                login_user(user, remember=True)
                next_page = request.args.get("next", "/")
                return redirect(next_page)

        return render_template("login.html", error="Invalid username or password")
    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
