from english_cards import create_app
from english_cards.db import init_db


app = create_app()


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
