import os

from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/english_cards",
)
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = os.environ.get(
    "DEEPSEEK_API_URL",
    "https://api.deepseek.com/chat/completions",
)
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

ENGLISH_LEVELS = ("A1", "A2", "B1", "B2", "C1")
CARD_COUNT_OPTIONS = (5, 10, 15, 20)
