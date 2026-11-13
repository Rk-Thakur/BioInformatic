import pytest
from sqlalchemy import text

from app import create_app
from app.config import ProductionConfig


def test_testing_config(app):
    assert app.config["TESTING"]
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    assert len(app.config["PANEL_GENES"]) == 7


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "database": "ok"}


def test_public_home_page_explains_project_and_links_to_login(client):
    resp = client.get("/")
    assert resp.status_code == 200
    page = resp.data
    assert b"When the science changes" in page and b"How it works" in page
    assert b'href="/login"' in page and b">Log in<" in page  # Log in button in the top bar
    assert b"advisory" in page
    login = client.get("/login")
    assert login.status_code == 200 and b"Back to home" in login.data


def test_cli_commands_registered(runner):
    result = runner.invoke(args=["show-config"])
    assert result.exit_code == 0
    assert "PANEL_GENES" in result.output


def test_production_requires_secret_key(monkeypatch):
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "dev-only-not-secret")
    with pytest.raises(RuntimeError):
        create_app("production")


def test_sqlite_foreign_keys_enforced(session):
    assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
