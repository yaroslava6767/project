from functools import wraps

import pg8000.dbapi
from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import db_cursor


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, username, created_at FROM users WHERE id = %s",
            (user_id,),
        )
        return cursor.fetchone()


def sign_in(user):
    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]


def register_auth_routes(app):
    @app.route("/")
    def index():
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/register", methods=("GET", "POST"))
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            if len(username) >= 3 and len(password) >= 6:
                try:
                    with db_cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO users (username, password_hash)
                            VALUES (%s, %s)
                            RETURNING id, username
                            """,
                            (username, generate_password_hash(password)),
                        )
                        user = cursor.fetchone()
                except pg8000.dbapi.IntegrityError:
                    pass
                else:
                    sign_in(user)
                    return redirect(url_for("dashboard"))

        return render_template("register.html")

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            with db_cursor() as cursor:
                cursor.execute(
                    "SELECT id, username, password_hash FROM users WHERE username = %s",
                    (username,),
                )
                user = cursor.fetchone()

            if user and check_password_hash(user["password_hash"], password):
                sign_in(user)
                return redirect(url_for("dashboard"))

            flash("Неверный логин или пароль.", "danger")

        return render_template("login.html")

    @app.route("/logout", methods=("POST",))
    def logout():
        session.clear()
        return redirect(url_for("login"))
