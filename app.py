import json
import os
from contextlib import contextmanager
from functools import wraps
from urllib.parse import unquote, urlparse

import pg8000.dbapi
import requests
from dotenv import load_dotenv
from pg8000 import exceptions as pg_exceptions
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash


load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/english_cards",
)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = os.environ.get(
    "DEEPSEEK_API_URL",
    "https://api.deepseek.com/chat/completions",
)
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
ENGLISH_LEVELS = ("A1", "A2", "B1", "B2", "C1")
CARD_COUNT_OPTIONS = (5, 10, 15, 20, 25, 30)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")

_db_ready = False


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


class DictCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, *args, **kwargs):
        return self.cursor.execute(*args, **kwargs)

    def fetchone(self):
        row = self.cursor.fetchone()
        return self._row_to_dict(row)

    def fetchall(self):
        return [self._row_to_dict(row) for row in self.cursor.fetchall()]

    def _row_to_dict(self, row):
        if row is None:
            return None
        columns = [column[0] for column in self.cursor.description]
        return dict(zip(columns, row))


def database_config():
    parsed = urlparse(DATABASE_URL)
    return {
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
    }


def ensure_database_exists():
    config = database_config()
    try:
        conn = pg8000.dbapi.connect(**config)
        conn.close()
        return
    except pg_exceptions.DatabaseError as exc:
        error = getattr(exc, "args", [{}])[0]
        if not isinstance(error, dict) or error.get("C") != "3D000":
            raise

    database_name = config["database"]
    maintenance_config = {**config, "database": "postgres"}
    conn = pg8000.dbapi.connect(**maintenance_config)
    try:
        conn.autocommit = True
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,),
            )
            if cursor.fetchone() is None:
                cursor.execute(f"CREATE DATABASE {quote_identifier(database_name)}")
        finally:
            cursor.close()
    finally:
        conn.close()


@contextmanager
def db_cursor():
    conn = pg8000.dbapi.connect(**database_config())
    try:
        cursor = conn.cursor()
        try:
            yield DictCursor(cursor)
        finally:
            cursor.close()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    ensure_database_exists()
    with db_cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cursor.execute(
            """

            CREATE TABLE IF NOT EXISTS card_sets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                topic VARCHAR(120) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cursor.execute(
            """

            CREATE TABLE IF NOT EXISTS cards (
                id SERIAL PRIMARY KEY,
                set_id INTEGER NOT NULL REFERENCES card_sets(id) ON DELETE CASCADE,
                word VARCHAR(120) NOT NULL,
                translation VARCHAR(120) NOT NULL,
                example TEXT NOT NULL,
                learned BOOLEAN NOT NULL DEFAULT FALSE
            );
            """
        )
        cursor.execute("ALTER TABLE cards DROP COLUMN IF EXISTS created_at")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                topic VARCHAR(120) NOT NULL,
                english_level VARCHAR(2) NOT NULL DEFAULT 'A2',
                cards_count INTEGER NOT NULL DEFAULT 10,
                request_prompt TEXT NOT NULL,
                response_text TEXT,
                success BOOLEAN NOT NULL DEFAULT FALSE,
                error_message TEXT
            );
            """
        )
        cursor.execute(
            "ALTER TABLE ai_log ADD COLUMN IF NOT EXISTS english_level VARCHAR(2) NOT NULL DEFAULT 'A2'"
        )
        cursor.execute(
            "ALTER TABLE ai_log ADD COLUMN IF NOT EXISTS cards_count INTEGER NOT NULL DEFAULT 10"
        )


def ensure_db_ready():
    global _db_ready
    if not _db_ready:
        init_db()
        _db_ready = True


@app.before_request
def prepare_database():
    ensure_db_ready()


@app.cli.command("init-db")
def init_db_command():
    init_db()
    print("Database tables are ready.")


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            flash("Сначала войдите в аккаунт.", "warning")
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


def build_cards_prompt(topic, english_level):
    return f"""
Ты генерируешь карточки для изучения английского языка.

Тема: "{topic}"
Уровень английского языка пользователя: {english_level}

Сгенерируй 15 устойчивых выражений, которые должны состоять из глагола и существительного или существительного и прилагательного на английском языке, которые реально понадобятся в диалоге на заданную тему

Ответ верни строго в JSON-формате без Markdown, без пояснений и без дополнительного текста.

Формат ответа:
[
  {{
    "word": "english word or phrase",
    "translation": "перевод на русский",
    "example": "example sentence in English"
  }}
]

Требования:
- word должен быть на английском языке.
- translation должен быть на русском языке.
- example должен быть на английском языке.
- example должен показывать естественное использование слова или выражения.
- Не добавляй нумерацию.
- Не добавляй поля кроме word, translation, example.
- Не повторяй слова.
- Слова должны соответствовать теме.
- Уровень сложности слов и примеров должен соответствовать уровню {english_level}.
""".strip()


def log_ai_interaction(
    user_id,
    topic,
    english_level,
    cards_count,
    request_prompt,
    response_text,
    success,
    error_message=None,
):
    with db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ai_log (
                user_id,
                topic,
                english_level,
                cards_count,
                request_prompt,
                response_text,
                success,
                error_message
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                topic,
                english_level,
                cards_count,
                request_prompt,
                response_text,
                success,
                error_message,
            ),
        )


def extract_json_array(text):
    clean_text = text.strip()
    if clean_text.startswith("```"):
        clean_text = clean_text.strip("`").strip()
        if clean_text.lower().startswith("json"):
            clean_text = clean_text[4:].strip()

    start = clean_text.find("[")
    end = clean_text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("ИИ вернул ответ без JSON-массива.")

    return clean_text[start : end + 1]


def parse_ai_cards(response_text):
    data = json.loads(extract_json_array(response_text))
    if not isinstance(data, list) or not data:
        raise ValueError("ИИ вернул пустой список карточек.")

    cards = []
    seen_words = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Каждая карточка должна быть объектом.")

        word = str(item.get("word", "")).strip()
        translation = str(item.get("translation", "")).strip()
        example = str(item.get("example", "")).strip()

        if not word or not translation or not example:
            raise ValueError("В карточке должны быть word, translation и example.")

        word_key = word.lower()
        if word_key in seen_words:
            continue

        seen_words.add(word_key)
        cards.append(
            {
                "word": word[:120],
                "translation": translation[:120],
                "example": example,
            }
        )

    if not cards:
        raise ValueError("ИИ не вернул подходящих карточек.")

    return cards


def extract_json_value(text):
    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(clean_text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(clean_text[index:])
            return value
        except json.JSONDecodeError:
            continue

    raise ValueError("ИИ вернул ответ без JSON-данных.")


def normalize_cards_payload(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("cards", "words", "items", "data", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    raise ValueError("ИИ вернул JSON, но без списка карточек.")


def parse_ai_cards(response_text):
    data = normalize_cards_payload(extract_json_value(response_text))
    if not data:
        raise ValueError("ИИ вернул пустой список карточек.")

    cards = []
    seen_words = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Каждая карточка должна быть объектом.")

        word = str(item.get("word", "")).strip()
        translation = str(item.get("translation", "")).strip()
        example = str(item.get("example", "")).strip()

        if not word or not translation or not example:
            raise ValueError("В карточке должны быть word, translation и example.")

        word_key = word.lower()
        if word_key in seen_words:
            continue

        seen_words.add(word_key)
        cards.append(
            {
                "word": word[:120],
                "translation": translation[:120],
                "example": example,
            }
        )

    if not cards:
        raise ValueError("ИИ не вернул подходящих карточек.")

    return cards


def build_cards_prompt(topic, english_level, cards_count):
    return f"""
Ты генерируешь карточки для изучения английского языка.

Тема: "{topic}"
Уровень английского языка пользователя: {english_level}

Сгенерируй ровно {cards_count} устойчивых выражений, которые должны состоять из глагола и существительного или существительного и прилагательного на английском языке, которые реально понадобятся в диалоге на заданную тему.

Ответ верни строго в JSON-формате без Markdown, без пояснений и без дополнительного текста.

Формат ответа:
[
  {{
    "word": "english word or phrase",
    "translation": "перевод на русский",
    "example": "example sentence in English"
  }}
]

Требования:
- word должен быть на английском языке.
- translation должен быть на русском языке.
- example должен быть на английском языке.
- example должен показывать естественное использование слова или выражения.
- Не добавляй нумерацию.
- Не добавляй поля кроме word, translation, example.
- Не повторяй слова.
- Количество объектов в JSON-массиве должно быть ровно {cards_count}.
- Слова должны соответствовать теме.
- Уровень сложности слов и примеров должен соответствовать уровню {english_level}.
""".strip()


def build_cards_prompt(topic, english_level, cards_count):
    return f"""
Ты генерируешь карточки для изучения английского языка.

Тема: "{topic}"
Уровень английского языка пользователя: {english_level}
Количество карточек: {cards_count}

Сгенерируй ровно {cards_count} устойчивых выражений, которые должны состоять из глагола и существительного или существительного и прилагательного на английском языке, которые реально понадобятся в диалоге на заданную тему.

Ответ верни строго как JSON-объект без Markdown, без пояснений и без дополнительного текста.

Формат ответа:
{{
  "cards": [
    {{
      "word": "english word or phrase",
      "translation": "перевод на русский",
      "example": "example sentence in English"
    }}
  ]
}}

Требования:
- В массиве cards должно быть ровно {cards_count} объектов.
- word должен быть на английском языке.
- translation должен быть на русском языке.
- example должен быть на английском языке.
- example должен показывать естественное использование слова или выражения.
- Не добавляй нумерацию.
- Не добавляй поля кроме cards, word, translation, example.
- Не повторяй слова.
- Слова должны соответствовать теме.
- Уровень сложности слов и примеров должен соответствовать уровню {english_level}.
""".strip()


def generate_cards_with_deepseek(user_id, topic, english_level, cards_count):
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY.startswith("replace-with-"):
        raise RuntimeError("Не задан DEEPSEEK_API_KEY в .env.")

    prompt = build_cards_prompt(topic, english_level, cards_count)
    response_text = None

    try:
        response = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты возвращаешь только валидный JSON без Markdown.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            },
            timeout=45,
        )
        response_text = response.text
        response.raise_for_status()
        payload = response.json()
        response_text = payload["choices"][0]["message"]["content"]
        cards = parse_ai_cards(response_text)
    except Exception as exc:
        log_ai_interaction(
            user_id=user_id,
            topic=topic,
            english_level=english_level,
            cards_count=cards_count,
            request_prompt=prompt,
            response_text=response_text,
            success=False,
            error_message=str(exc),
        )
        raise

    log_ai_interaction(
        user_id=user_id,
        topic=topic,
        english_level=english_level,
        cards_count=cards_count,
        request_prompt=prompt,
        response_text=response_text,
        success=True,
    )
    return cards


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

        if len(username) < 3:
            flash("Логин должен быть не короче 3 символов.", "danger")
        elif len(password) < 6:
            flash("Пароль должен быть не короче 6 символов.", "danger")
        else:
            try:
                with db_cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO users (username, password_hash)
                        VALUES (%s, %s)
                        RETURNING id
                        """,
                        (username, generate_password_hash(password)),
                    )
                    user = cursor.fetchone()
            except pg8000.dbapi.IntegrityError:
                flash("Пользователь с таким логином уже существует.", "danger")
            else:
                session.clear()
                session["user_id"] = user["id"]
                flash("Регистрация завершена.", "success")
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
            session.clear()
            session["user_id"] = user["id"]
            flash("Вы вошли в аккаунт.", "success")
            return redirect(url_for("dashboard"))

        flash("Неверный логин или пароль.", "danger")

    return render_template("login.html")


@app.route("/logout", methods=("POST",))
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for("login"))


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
    topic = request.form.get("topic", "").strip()
    english_level = request.form.get("english_level", "A2").strip().upper()
    try:
        cards_count = int(request.form.get("cards_count", "10"))
    except ValueError:
        cards_count = 10
    if not topic:
        flash("Введите тему для генерации карточек.", "danger")
        return redirect(url_for("dashboard"))

    if len(topic) > 120:
        flash("Тема должна быть не длиннее 120 символов.", "danger")
        return redirect(url_for("dashboard"))

    if english_level not in ENGLISH_LEVELS:
        flash("Выберите корректный уровень английского языка.", "danger")
        return redirect(url_for("dashboard"))

    if cards_count not in CARD_COUNT_OPTIONS:
        flash("Выберите корректное количество слов.", "danger")
        return redirect(url_for("dashboard"))

    try:
        cards = generate_cards_with_deepseek(
            session["user_id"],
            topic,
            english_level,
            cards_count,
        )
    except Exception as exc:
        flash(f"Не удалось сгенерировать карточки через DeepSeek: {exc}", "danger")
        return redirect(url_for("dashboard"))

    with db_cursor() as cursor:
        cursor.execute(
            "INSERT INTO card_sets (user_id, topic) VALUES (%s, %s) RETURNING id",
            (session["user_id"], topic),
        )
        card_set = cursor.fetchone()

        for card in cards:
            cursor.execute(
                """
                INSERT INTO cards (set_id, word, translation, example)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    card_set["id"],
                    card["word"],
                    card["translation"],
                    card["example"],
                ),
            )

    flash("Набор карточек создан.", "success")
    return redirect(url_for("view_set", set_id=card_set["id"]))


@app.route("/sets/<int:set_id>")
@login_required
def view_set(set_id):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, topic, created_at
            FROM card_sets
            WHERE id = %s AND user_id = %s
            """,
            (set_id, session["user_id"]),
        )
        card_set = cursor.fetchone()

        if not card_set:
            flash("Набор карточек не найден.", "danger")
            return redirect(url_for("dashboard"))

        cursor.execute(
            """
            SELECT id, word, translation, example, learned
            FROM cards
            WHERE set_id = %s
            ORDER BY id
            """,
            (set_id,),
        )
        cards = cursor.fetchall()

    card_payload = [
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

    return render_template(
        "set.html",
        card_set=card_set,
        cards=cards,
        card_payload=card_payload,
    )


@app.route("/cards/<int:card_id>/status", methods=("POST",))
@login_required
def set_card_status(card_id):
    raw_learned = request.form.get("learned")
    if request.is_json:
        json_data = request.get_json(silent=True) or {}
        raw_learned = json_data.get("learned", raw_learned)

    learned = str(raw_learned).lower() in {"1", "true", "yes", "on"}

    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT c.id, c.set_id
            FROM cards c
            JOIN card_sets cs ON cs.id = c.set_id
            WHERE c.id = %s AND cs.user_id = %s
            """,
            (card_id, session["user_id"]),
        )
        card = cursor.fetchone()

        if not card:
            if request.is_json:
                return jsonify({"ok": False, "error": "card_not_found"}), 404
            flash("Карточка не найдена.", "danger")
            return redirect(url_for("dashboard"))

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
        cursor.execute(
            """
            SELECT c.id, c.learned, c.set_id
            FROM cards c
            JOIN card_sets cs ON cs.id = c.set_id
            WHERE c.id = %s AND cs.user_id = %s
            """,
            (card_id, session["user_id"]),
        )
        card = cursor.fetchone()

        if not card:
            flash("Карточка не найдена.", "danger")
            return redirect(url_for("dashboard"))

        cursor.execute(
            "UPDATE cards SET learned = %s WHERE id = %s",
            (not card["learned"], card_id),
        )

    return redirect(url_for("view_set", set_id=card["set_id"]))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
