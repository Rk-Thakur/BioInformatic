"""Configuration classes. Select one with the GE_EHR_CONFIG environment variable
(development | testing | production); development is the default."""

import os

_DEV_SECRET = "dev-only-not-secret"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", _DEV_SECRET)
    # None -> create_app() places a SQLite file in the instance folder.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # VCF upload limit (20 MB)

    # Session security (plan §7). Production also sets SESSION_COOKIE_SECURE.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = 8 * 60 * 60  # one working day, in seconds

    # One-click login as a demo_* account from the login page (no password shown).
    # For local demos only: on in development, off unless DEMO_LOGIN=1 elsewhere.
    DEMO_LOGIN = os.environ.get("DEMO_LOGIN", "0") == "1"
    DEMO_PREFIX = "demo_"

    # Monogenic diabetes gene panel (paper §4.2).
    PANEL_GENES = ("GCK", "HNF1A", "HNF4A", "HNF1B", "KCNJ11", "ABCC8", "INS")
    # "Current therapy" is evaluated as of this date (plan §2a, C13).
    # Synthea writes STOP dates in the future, so "today" is not a stable choice.
    THERAPY_REFERENCE_DATE = os.environ.get("THERAPY_REFERENCE_DATE", "2026-10-19")

    DATA_RAW_DIR = os.environ.get("DATA_RAW_DIR", "data/raw")
    DATA_CLEAN_DIR = os.environ.get("DATA_CLEAN_DIR", "data/clean")
    DATA_CURATED_DIR = os.environ.get("DATA_CURATED_DIR", "data/curated")
    # Stage 7 (DD45): genetic risk levels for people without diabetes, in DATA_CURATED_DIR.
    RISK_LEVELS_FILE = os.environ.get("RISK_LEVELS_FILE", "risk_levels_v1.yaml")


class DevelopmentConfig(Config):
    DEBUG = True
    DEMO_LOGIN = os.environ.get("DEMO_LOGIN", "1") == "1"


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


class ProductionConfig(Config):
    # Cookies only over HTTPS. Set SESSION_COOKIE_SECURE=0 to run on plain http://localhost
    # (the Docker setup does this; never do it on a server reachable by others).
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") != "0"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    # When SECRET_KEY is not in the environment, read it from this file in the instance
    # folder. The Docker entrypoint generates it once, so `docker compose exec web flask
    # ...` commands work too, not only the web server it starts.
    SECRET_KEY_FILE = "secret_key"

    @classmethod
    def validate(cls, config):
        if config["SECRET_KEY"] == _DEV_SECRET:
            raise RuntimeError(
                "SECRET_KEY must be set in production (environment variable, or the file "
                "instance/secret_key)"
            )


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
