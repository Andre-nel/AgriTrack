from datetime import date, datetime, timezone

import click
from flask import Flask
from sqlalchemy import func, or_

from app.extensions import db
from app.models import Farm, Mob, MobileAuthToken, Paddock, User, UserFarmRole
from app.services.grazing_history_service import GrazingHistoryService


def _normalize_email(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _require_user(email: str) -> User:
    normalized_email = _normalize_email(email)
    user = User.query.filter_by(email=normalized_email).first()
    if user is None:
        raise click.ClickException(f"User not found: {normalized_email}")
    return user


def _require_farm(identifier: str) -> Farm:
    farm_key = " ".join((identifier or "").strip().split())
    farm = Farm.query.filter_by(id=farm_key).first()
    if farm is None:
        farm = Farm.query.filter(func.lower(Farm.name) == farm_key.lower()).first()
    if farm is None:
        raise click.ClickException(f"Farm not found: {farm_key}")
    return farm


def _create_user(*, email: str, name: str, password: str, inactive: bool) -> User:
    normalized_email = _normalize_email(email)
    if User.query.filter_by(email=normalized_email).first() is not None:
        raise click.ClickException(f"User already exists: {normalized_email}")

    user = User(email=normalized_email, name=" ".join(name.strip().split()), active=not inactive)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _assign_farm_role(*, email: str, farm: str, role: str) -> tuple[str, User, Farm, str]:
    user = _require_user(email)
    farm_record = _require_farm(farm)
    normalized_role = " ".join((role or "manager").strip().lower().split()) or "manager"
    role_record = UserFarmRole.query.filter_by(user_id=user.id, farm_id=farm_record.id).first()
    if role_record is None:
        role_record = UserFarmRole(
            user_id=user.id,
            farm_id=farm_record.id,
            role=normalized_role,
        )
        db.session.add(role_record)
        action = "Assigned"
    else:
        role_record.role = normalized_role
        action = "Updated"
    db.session.commit()
    return action, user, farm_record, normalized_role


def init_cli(app: Flask) -> None:
    @app.cli.command("seed-demo")
    def seed_demo() -> None:
        """Seed a small demo dataset."""
        if Farm.query.first():
            print("Seed skipped: farms already exist")
            return

        farm = Farm(name="Demo Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()

        north = Paddock(farm_id=farm.id, name="North 1", area_ha=12.5, grazeable_area_ha=10.0)
        south = Paddock(farm_id=farm.id, name="South 2", area_ha=9.0, grazeable_area_ha=8.0)
        mob = Mob(farm_id=farm.id, name="Main Mob", status="active")
        db.session.add_all([north, south, mob])
        db.session.commit()

        print(f"Seed complete for {date.today().isoformat()}")

    @app.cli.command("backfill-grazing-lsu-history")
    def backfill_grazing_lsu_history() -> None:
        """Rebuild persisted paddock LSU history from stock ledger and grazing allocations."""
        summary = GrazingHistoryService.backfill_all_from_ledger()
        db.session.commit()
        print(
            "Backfill complete: "
            f"{summary['mobs_backfilled']} mob(s), "
            f"{summary['rows_created']} history row(s), "
            f"{summary['breakdown_rows_created']} breakdown row(s)"
        )

    @app.cli.command("mobile-create-user")
    @click.option("--email", required=True, help="Email address used for mobile login.")
    @click.option("--name", required=True, help="Display name for the mobile user.")
    @click.option("--password", required=True, help="Initial mobile login password.")
    @click.option("--inactive", is_flag=True, help="Create the user as inactive.")
    def mobile_create_user(email: str, name: str, password: str, inactive: bool) -> None:
        """Create a passworded user for mobile API access."""
        user = _create_user(email=email, name=name, password=password, inactive=inactive)
        click.echo(f"Created mobile user {user.email} ({user.id})")

    @app.cli.command("user-create")
    @click.option("--email", required=True, help="Email address used for web and mobile login.")
    @click.option("--name", required=True, help="Display name for the user.")
    @click.option("--password", required=True, help="Initial login password.")
    @click.option("--inactive", is_flag=True, help="Create the user as inactive.")
    def user_create(email: str, name: str, password: str, inactive: bool) -> None:
        """Create a passworded AgriTrack user."""
        user = _create_user(email=email, name=name, password=password, inactive=inactive)
        click.echo(f"Created user {user.email} ({user.id})")

    @app.cli.command("mobile-set-password")
    @click.argument("email")
    @click.option("--password", required=True, help="New mobile login password.")
    def mobile_set_password(email: str, password: str) -> None:
        """Set or reset a mobile user's password."""
        user = _require_user(email)
        user.set_password(password)
        db.session.commit()
        click.echo(f"Password updated for {user.email}")

    @app.cli.command("user-set-password")
    @click.argument("email")
    @click.option("--password", required=True, help="New login password.")
    def user_set_password(email: str, password: str) -> None:
        """Set or reset an AgriTrack user's password."""
        user = _require_user(email)
        user.set_password(password)
        db.session.commit()
        click.echo(f"Password updated for {user.email}")

    @app.cli.command("mobile-assign-farm")
    @click.argument("email")
    @click.argument("farm")
    @click.option("--role", default="manager", show_default=True, help="Role to grant on the farm.")
    def mobile_assign_farm(email: str, farm: str, role: str) -> None:
        """Grant or update a mobile user's farm role."""
        action, user, farm_record, normalized_role = _assign_farm_role(
            email=email,
            farm=farm,
            role=role,
        )
        click.echo(f"{action} {user.email} to {farm_record.name} as {normalized_role}")

    @app.cli.command("user-assign-farm")
    @click.argument("email")
    @click.argument("farm")
    @click.option("--role", default="manager", show_default=True, help="Role to grant on the farm.")
    def user_assign_farm(email: str, farm: str, role: str) -> None:
        """Grant or update an AgriTrack user's farm role."""
        action, user, farm_record, normalized_role = _assign_farm_role(
            email=email,
            farm=farm,
            role=role,
        )
        click.echo(f"{action} {user.email} to {farm_record.name} as {normalized_role}")

    @app.cli.command("mobile-revoke-tokens")
    @click.argument("email")
    @click.option("--device-name", help="Only revoke tokens for a specific device name.")
    def mobile_revoke_tokens(email: str, device_name: str | None) -> None:
        """Revoke active mobile bearer tokens for a user."""
        user = _require_user(email)
        query = MobileAuthToken.query.filter_by(user_id=user.id, revoked_at=None)
        if device_name:
            query = query.filter(MobileAuthToken.device_name == device_name)
        tokens = query.all()
        now = datetime.now(timezone.utc)
        for token in tokens:
            token.revoked_at = now
        db.session.commit()
        click.echo(f"Revoked {len(tokens)} mobile token(s) for {user.email}")

    @app.cli.command("mobile-prune-expired-tokens")
    @click.option("--dry-run", is_flag=True, help="Report the number of tokens without deleting them.")
    def mobile_prune_expired_tokens(dry_run: bool) -> None:
        """Delete expired or revoked mobile bearer tokens."""
        now = datetime.now(timezone.utc)
        tokens = MobileAuthToken.query.filter(
            or_(
                MobileAuthToken.expires_at <= now,
                MobileAuthToken.revoked_at.is_not(None),
            )
        ).all()
        if not dry_run:
            for token in tokens:
                db.session.delete(token)
            db.session.commit()
        click.echo(f"{'Would prune' if dry_run else 'Pruned'} {len(tokens)} mobile token(s)")
