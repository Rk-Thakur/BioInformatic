"""GE-EHR application factory."""

import os

from flask import Flask

from app.config import CONFIGS
from app.extensions import csrf, db, login_manager, migrate


def create_app(config_name=None, overrides=None):
    """Build the Flask app.

    config_name: development | testing | production (default: $GE_EHR_CONFIG or development)
    overrides:   dict of config values applied last (used by tests)
    """
    config_name = config_name or os.environ.get("GE_EHR_CONFIG", "development")
    config_cls = CONFIGS[config_name]
    if hasattr(config_cls, "validate"):
        config_cls.validate()

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_cls)
    if overrides:
        app.config.update(overrides)

    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config["SQLALCHEMY_DATABASE_URI"]:
        db_path = os.path.join(app.instance_path, "ge_ehr.db")
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)  # batch mode: SQLite ALTER TABLE
    login_manager.init_app(app)
    csrf.init_app(app)

    from app import models  # noqa: F401  (register tables with SQLAlchemy)
    from app.cli import register_cli
    from app.web import register_blueprints

    register_blueprints(app)
    register_cli(app)
    return app
