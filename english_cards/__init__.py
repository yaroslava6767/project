from flask import Flask

from .auth import register_auth_routes
from .card_sets import register_card_routes
from .config import SECRET_KEY
from .db import register_db_hooks


def create_app():
    app = Flask(
        __name__,
        template_folder="../templates",
    )
    app.config["SECRET_KEY"] = SECRET_KEY

    register_db_hooks(app)
    register_auth_routes(app)
    register_card_routes(app)

    return app
