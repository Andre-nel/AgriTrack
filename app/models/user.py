from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

    farm_roles = db.relationship("UserFarmRole", back_populates="user", cascade="all, delete-orphan")


class UserFarmRole(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "user_farm_roles"

    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    role = db.Column(db.String(30), nullable=False, default="manager")

    user = db.relationship("User", back_populates="farm_roles")
    farm = db.relationship("Farm")

    __table_args__ = (db.UniqueConstraint("user_id", "farm_id", name="uq_user_farm_role"),)
