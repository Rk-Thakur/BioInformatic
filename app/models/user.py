from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.base import ROLES, one_of, utcnow


class User(UserMixin, db.Model):
    __tablename__ = "user"
    __table_args__ = (one_of("role", ROLES),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)  # scrypt by default

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.active

    def can_view_recommendations(self):
        """S12: lab technicians upload VCFs but never see treatment recommendations."""
        return self.role in ("clinician", "admin")

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"
