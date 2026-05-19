from flask import jsonify, redirect, render_template, request, session, url_for

from .ai import generate_cards_with_deepseek
from .auth import current_user, login_required
from .config import CARD_COUNT_OPTIONS, ENGLISH_LEVELS
from .db import db_cursor


def dashboard_redirect():
    return redirect(url_for("dashboard"))


def requested_set_options():
    try:
        cards_count = int(request.form.get("cards_count", "10"))
    except ValueError:
        cards_count = 10

    return {
        "topic": request.form.get("topic", "").strip(),
        "english_level": request.form.get("english_level", "A2").strip().upper(),
        "cards_count": cards_count,
    }


def validate_set_options(options):
    topic = options["topic"]
    english_level = options["english_level"]
    cards_count = options["cards_count"]

    if not topic:
        return "Введите тему для генерации карточек."
    if len(topic) > 120:
        return "Тема должна быть не длиннее 120 символов."
    if english_level not in ENGLISH_LEVELS:
        return "Выберите корректный уровень английского языка."
    if cards_count not in CARD_COUNT_OPTIONS:
        return "Выберите корректное количество слов."
    return None


def load_user_set_with_cards(set_id, include_created_at=True):
    columns = "id, topic, created_at" if include_created_at else "id, topic"
    with db_cursor() as cursor:
        cursor.execute(
            f"SELECT {columns} FROM card_sets WHERE id = %s AND user_id = %s",
            (set_id, session["user_id"]),
        )
        card_set = cursor.fetchone()
        if not card_set:
            return None, []

        cursor.execute(
            "SELECT id, word, translation, example, learned FROM cards WHERE set_id = %s ORDER BY id",
            (set_id,),
        )
        return card_set, cursor.fetchall()


def save_card_set(user_id, topic, cards):
    with db_cursor() as cursor:
        cursor.execute(
            "INSERT INTO card_sets (user_id, topic) VALUES (%s, %s) RETURNING id",
            (user_id, topic),
        )
        card_set = cursor.fetchone()

        for card in cards:
            cursor.execute(
                "INSERT INTO cards (set_id, word, translation, example) VALUES (%s, %s, %s, %s)",
                (card_set["id"], card["word"], card["translation"], card["example"]),
            )

    return card_set


def build_card_payload(cards):
    return [
        {
            "id": card["id"],
            "word": card["word"],
            "translation": card["translation"],
            "example": card["example"],
            "learned": card["learned"],
            "statusUrl": url_for("set_card_status", card_id=card["id"]),
        }
        for card in cards
    ]


def find_user_card(cursor, card_id):
    cursor.execute(
        """
        SELECT c.id, c.learned, c.set_id
        FROM cards c
        JOIN card_sets cs ON cs.id = c.set_id
        WHERE c.id = %s AND cs.user_id = %s
        """,
        (card_id, session["user_id"]),
    )
    return cursor.fetchone()


def request_bool(name):
    value = request.form.get(name)
    if request.is_json:
        value = (request.get_json(silent=True) or {}).get(name, value)
    return str(value).lower() in {"1", "true", "yes", "on"}


def register_card_routes(app):
    @app.route("/dashboard")
    @login_required
    def dashboard():
        user = current_user()
        with db_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    cs.id,
                    cs.topic,
                    cs.created_at,
                    COUNT(c.id) AS total_cards,
                    COUNT(c.id) FILTER (WHERE c.learned) AS learned_cards
                FROM card_sets cs
                LEFT JOIN cards c ON c.set_id = cs.id
                WHERE cs.user_id = %s
                GROUP BY cs.id
                ORDER BY cs.created_at DESC
                """,
                (user["id"],),
            )
            card_sets = cursor.fetchall()

        return render_template("dashboard.html", user=user, card_sets=card_sets)

    @app.route("/sets/create", methods=("POST",))
    @login_required
    def create_set():
        options = requested_set_options()
        if validate_set_options(options):
            return dashboard_redirect()

        try:
            cards = generate_cards_with_deepseek(session["user_id"], **options)
        except Exception:
            return dashboard_redirect()

        card_set = save_card_set(session["user_id"], options["topic"], cards)
        return redirect(url_for("view_set", set_id=card_set["id"]))

    @app.route("/sets/<int:set_id>")
    @login_required
    def view_set(set_id):
        card_set, cards = load_user_set_with_cards(set_id)
        if not card_set:
            return dashboard_redirect()

        return render_template(
            "set.html",
            card_set=card_set,
            cards=cards,
            card_payload=build_card_payload(cards),
        )

    @app.route("/sets/<int:set_id>/delete", methods=("POST",))
    @login_required
    def delete_set(set_id):
        with db_cursor() as cursor:
            cursor.execute(
                "DELETE FROM card_sets WHERE id = %s AND user_id = %s RETURNING id",
                (set_id, session["user_id"]),
            )
            cursor.fetchone()
        return redirect(url_for("dashboard"))

    @app.route("/sets/<int:set_id>/words")
    @login_required
    def view_words(set_id):
        card_set, cards = load_user_set_with_cards(set_id, include_created_at=False)
        if not card_set:
            return dashboard_redirect()

        return render_template("words.html", card_set=card_set, cards=cards)

    @app.route("/cards/<int:card_id>/status", methods=("POST",))
    @login_required
    def set_card_status(card_id):
        learned = request_bool("learned")

        with db_cursor() as cursor:
            card = find_user_card(cursor, card_id)
            if not card:
                if request.is_json:
                    return jsonify({"ok": False, "error": "card_not_found"}), 404
                return dashboard_redirect()

            cursor.execute(
                "UPDATE cards SET learned = %s WHERE id = %s",
                (learned, card_id),
            )

        if request.is_json:
            return jsonify({"ok": True, "learned": learned})

        return redirect(url_for("view_set", set_id=card["set_id"]))

    @app.route("/cards/<int:card_id>/toggle", methods=("POST",))
    @login_required
    def toggle_card(card_id):
        with db_cursor() as cursor:
            card = find_user_card(cursor, card_id)
            if not card:
                return dashboard_redirect()

            cursor.execute(
                "UPDATE cards SET learned = %s WHERE id = %s",
                (not card["learned"], card_id),
            )

        return redirect(url_for("view_set", set_id=card["set_id"]))
