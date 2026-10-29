"""`flask ...` commands. Import, re-evaluation and benchmark commands are added
in later stages (plan §4); this module registers them all in one place."""

import click
from flask import current_app

from app.extensions import db


@click.command("init-db")
@click.option("--drop", is_flag=True, help="Drop all tables first (destroys data).")
def init_db(drop):
    """Create tables from the models (dev shortcut; normally use `flask db upgrade`)."""
    if drop:
        click.confirm("Drop ALL tables and their data?", abort=True)
        db.drop_all()
    db.create_all()
    click.echo(f"Database ready: {current_app.config['SQLALCHEMY_DATABASE_URI']}")


@click.command("show-config")
def show_config():
    """Print the non-secret settings in use."""
    for key in (
        "SQLALCHEMY_DATABASE_URI",
        "PANEL_GENES",
        "THERAPY_REFERENCE_DATE",
        "DATA_RAW_DIR",
        "DATA_CLEAN_DIR",
        "DATA_CURATED_DIR",
    ):
        click.echo(f"{key} = {current_app.config[key]}")


def register_cli(app):
    app.cli.add_command(init_db)
    app.cli.add_command(show_config)
