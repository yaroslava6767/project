## Что нужно

- Python 3.10+
- PostgreSQL
- API-ключ DeepSeek

## Настройка

1. Создайте и активируйте виртуальное окружение:

```bash
python -m venv .venv
.venv\Scripts\activate
```

2. Установите зависимости:

```bash
pip install -r requirements.txt
```

3. Скопируйте пример настроек:

```bash
copy .env.example .env
```

4. Заполните `.env`:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/english_cards
SECRET_KEY=replace-with-a-random-secret
DEEPSEEK_API_KEY=replace-with-your-deepseek-api-key
DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions
DEEPSEEK_MODEL=deepseek-v4-flash
```

## База данных

Убедитесь, что PostgreSQL запущен. Приложение попробует создать базу `english_cards` автоматически.

Если у пользователя PostgreSQL нет прав на создание базы, создайте ее вручную:

```sql
CREATE DATABASE english_cards;
```

Таблицы создаются автоматически при запуске приложения или при первом запросе.

## Запуск

```bash
python app.py
```

Откройте в браузере:

```text
http://127.0.0.1:5000
```
