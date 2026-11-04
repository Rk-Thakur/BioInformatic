"""The Alembic migrations must build exactly the schema the models describe."""

from flask_migrate import downgrade, upgrade
from sqlalchemy import create_engine, inspect

from app import create_app
from app.extensions import db


def test_upgrade_matches_models_and_downgrades(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    app = create_app("testing", overrides={"SQLALCHEMY_DATABASE_URI": url})
    with app.app_context():
        upgrade()
        insp = inspect(db.engine)
        migrated = set(insp.get_table_names()) - {"alembic_version"}
        assert migrated == set(db.metadata.tables)
        partial = {i["name"] for i in insp.get_indexes("alert")}
        assert "uq_alert_open" in partial
        db.engine.dispose()

        downgrade(revision="base")
        engine = create_engine(url)
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
        engine.dispose()
        db.engine.dispose()
