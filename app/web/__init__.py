"""Web layer: Flask blueprints and templates."""

from flask import Blueprint, jsonify, render_template

from app.extensions import db

main = Blueprint("main", __name__, template_folder="templates")


@main.get("/")
def index():
    return render_template("index.html")


@main.get("/health")
def health():
    db.session.execute(db.text("SELECT 1"))
    return jsonify(status="ok", database="ok")


def register_blueprints(app):
    app.register_blueprint(main)
