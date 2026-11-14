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


def test_stage7_migration_keeps_existing_patients(tmp_path):
    """SQLite rebuilds the patient table; it must work while other rows reference
    patients (foreign keys are enforced), keep their data and default them to diabetic."""
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'p.db'}"
    app = create_app("testing", overrides={"SQLALCHEMY_DATABASE_URI": url})
    with app.app_context():
        upgrade(revision="9f0783150ae2")
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO patient (id, external_id, name, is_active, created_at) "
                    "VALUES (1, 'p1', 'Patient 1', 1, '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO medication (patient_id, description, drug_class, start) "
                    "VALUES (1, 'insulin', 'insulin', '2026-01-01')"
                )
            )
        upgrade()
        with db.engine.connect() as conn:
            row = conn.execute(text("SELECT name, has_diabetes, relation FROM patient")).one()
            assert tuple(row) == ("Patient 1", 1, None)
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
            assert conn.execute(text("SELECT count(*) FROM medication")).scalar() == 1
        assert "_alembic_tmp_patient" not in inspect(db.engine).get_table_names()
        downgrade(revision="9f0783150ae2")
        db.engine.dispose()
