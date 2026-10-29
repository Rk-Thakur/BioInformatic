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

    # Monogenic diabetes gene panel (paper §4.2).
    PANEL_GENES = ("GCK", "HNF1A", "HNF4A", "HNF1B", "KCNJ11", "ABCC8", "INS")
    # "Current therapy" is evaluated as of this date (plan §2a, C13).
    # Synthea writes STOP dates in the future, so "today" is not a stable choice.
    THERAPY_REFERENCE_DATE = os.environ.get("THERAPY_REFERENCE_DATE", "2026-10-19")

    DATA_RAW_DIR = os.environ.get("DATA_RAW_DIR", "data/raw")
    DATA_CLEAN_DIR = os.environ.get("DATA_CLEAN_DIR", "data/clean")
    DATA_CURATED_DIR = os.environ.get("DATA_CURATED_DIR", "data/curated")


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


class ProductionConfig(Config):
    @classmethod
    def validate(cls):
        if cls.SECRET_KEY == _DEV_SECRET:
            raise RuntimeError("SECRET_KEY must be set in production")


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
