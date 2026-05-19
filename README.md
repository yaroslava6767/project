# English Cards

Простой веб-сервис на Flask + Postgres для регистрации пользователей и хранения личных наборов карточек для изучения английского языка.

Для подключения к Postgres используется `pg8000`, поэтому на Windows не нужен компилятор C для сборки `psycopg2`.

## Запуск

1. Убедитесь, что Postgres запущен.

Приложение попробует автоматически создать базу `english_cards`. Если у пользователя Postgres нет прав на создание базы, создайте её вручную:

```sql
CREATE DATABASE english_cards;
```

2. Установите зависимости:

```bash
pip install -r requirements.txt
```

3. Задайте переменные окружения при необходимости:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/english_cards
SECRET_KEY=replace-with-a-random-secret
DEEPSEEK_API_KEY=replace-with-your-deepseek-api-key
DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions
DEEPSEEK_MODEL=deepseek-v4-flash
```

4. Запустите приложение:

```bash
python app.py
```

База и таблицы создаются автоматически при старте приложения или при первом запросе, если у пользователя Postgres есть нужные права.

Запросы к DeepSeek и ответы модели сохраняются в таблицу `ai_log` вместе с пользователем и временем запроса.
