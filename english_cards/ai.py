import json
import re

import requests

from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
)
from .db import db_cursor


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


def extract_json_value(text):
    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()

    if clean_text.lower().startswith("json"):
        clean_text = clean_text[4:].strip()

    if clean_text:
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            pass

        for delimiter in ('{"', "[", "{\n"):
            start = clean_text.find(delimiter)
            if start != -1:
                try:
                    return json.loads(clean_text[start:])
                except json.JSONDecodeError:
                    pass

    brace_depth = 0
    in_string = False
    escape = False
    json_candidates = []
    candidate_start = -1
    for i, ch in enumerate(clean_text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "[{":
            if brace_depth == 0:
                candidate_start = i
            brace_depth += 1
        elif ch in "]}":
            brace_depth -= 1
            if brace_depth == 0 and candidate_start != -1:
                json_candidates.append(clean_text[candidate_start : i + 1])
                candidate_start = -1

    if brace_depth == 0 and not json_candidates:
        obj_match = re.search(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", clean_text, re.DOTALL)
        if obj_match:
            json_candidates.append(obj_match.group())
        arr_match = re.search(r"\[(?:[^\[\]]|(?:\[[^\[\]]*\]))*\]", clean_text, re.DOTALL)
        if arr_match:
            json_candidates.append(arr_match.group())

    for candidate in json_candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(clean_text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(clean_text[index:])
            return value
        except json.JSONDecodeError:
            continue

    snippet = text[:200] if text else "(пустой ответ)"
    raise ValueError(f"ИИ вернул ответ без JSON-данных. Начало ответа: {snippet!r}")


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


def response_text_for_log(raw_response_text, assistant_content):
    return assistant_content or raw_response_text


def generate_cards_with_deepseek(user_id, topic, english_level, cards_count):
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY.startswith("replace-with-"):
        raise RuntimeError("Не задан DEEPSEEK_API_KEY в .env.")

    prompt = build_cards_prompt(topic, english_level, cards_count)
    raw_response_text = None
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
                "max_tokens": 10000,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
            proxies={"http": None, "https": None},
        )
        raw_response_text = response.text
        response_text = raw_response_text
        response.raise_for_status()
        payload = response.json()
        assistant_content = payload["choices"][0]["message"].get("content") or ""
        response_text = response_text_for_log(raw_response_text, assistant_content)
        cards = parse_ai_cards(assistant_content)
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
