import hashlib
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Farm, MobileAuthToken, User, UserFarmRole


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_mobile_cli_can_create_user_set_password_and_assign_farm(app):
    runner = app.test_cli_runner()
    with app.app_context():
        farm = Farm(name="CLI Mobile Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.commit()
        farm_id = str(farm.id)

    create_result = runner.invoke(
        args=[
            "mobile-create-user",
            "--email",
            "Field.User@Example.COM",
            "--name",
            "Field User",
            "--password",
            "first-password",
        ]
    )
    assert create_result.exit_code == 0
    assert "Created mobile user field.user@example.com" in create_result.output

    assign_result = runner.invoke(
        args=["mobile-assign-farm", "field.user@example.com", farm_id, "--role", "observer"]
    )
    assert assign_result.exit_code == 0
    assert "as observer" in assign_result.output

    password_result = runner.invoke(
        args=["mobile-set-password", "field.user@example.com", "--password", "second-password"]
    )
    assert password_result.exit_code == 0

    with app.app_context():
        user = User.query.filter_by(email="field.user@example.com").first()
        assert user is not None
        assert user.name == "Field User"
        assert user.check_password("second-password")
        role = UserFarmRole.query.filter_by(user_id=user.id, farm_id=farm_id).first()
        assert role is not None
        assert role.role == "observer"


def test_generic_user_cli_aliases_create_user_set_password_and_assign_farm(app):
    runner = app.test_cli_runner()
    with app.app_context():
        farm = Farm(name="Web Login Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.commit()

    create_result = runner.invoke(
        args=[
            "user-create",
            "--email",
            "owner@example.com",
            "--name",
            "Farm Owner",
            "--password",
            "first-password",
        ]
    )
    assert create_result.exit_code == 0
    assert "Created user owner@example.com" in create_result.output

    assign_result = runner.invoke(
        args=["user-assign-farm", "owner@example.com", "Web Login Farm", "--role", "manager"]
    )
    assert assign_result.exit_code == 0
    assert "as manager" in assign_result.output

    password_result = runner.invoke(
        args=["user-set-password", "owner@example.com", "--password", "second-password"]
    )
    assert password_result.exit_code == 0

    with app.app_context():
        user = User.query.filter_by(email="owner@example.com").first()
        assert user is not None
        assert user.check_password("second-password")


def test_mobile_cli_rejects_duplicate_users_and_missing_records(app):
    runner = app.test_cli_runner()
    create_args = [
        "mobile-create-user",
        "--email",
        "duplicate@example.com",
        "--name",
        "Duplicate",
        "--password",
        "password",
    ]

    assert runner.invoke(args=create_args).exit_code == 0
    duplicate_result = runner.invoke(args=create_args)
    assert duplicate_result.exit_code != 0
    assert "User already exists" in duplicate_result.output

    missing_user_result = runner.invoke(
        args=["mobile-assign-farm", "missing@example.com", "Missing Farm"]
    )
    assert missing_user_result.exit_code != 0
    assert "User not found" in missing_user_result.output


def test_mobile_cli_revoke_and_prune_tokens(app):
    runner = app.test_cli_runner()
    with app.app_context():
        farm = Farm(name="Token Admin Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()
        user = User(email="tokens@example.com", name="Token User", active=True)
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        db.session.add(UserFarmRole(user_id=user.id, farm_id=farm.id, role="manager"))
        active_token = MobileAuthToken(
            user_id=user.id,
            token_hash=_token_hash("active-token"),
            token_prefix="active-token"[:12],
            device_name="Pixel Field Phone",
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        expired_token = MobileAuthToken(
            user_id=user.id,
            token_hash=_token_hash("expired-token"),
            token_prefix="expired-token"[:12],
            device_name="Old Phone",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.session.add_all([active_token, expired_token])
        db.session.commit()

    revoke_result = runner.invoke(
        args=[
            "mobile-revoke-tokens",
            "tokens@example.com",
            "--device-name",
            "Pixel Field Phone",
        ]
    )
    assert revoke_result.exit_code == 0
    assert "Revoked 1 mobile token(s)" in revoke_result.output

    dry_run_result = runner.invoke(args=["mobile-prune-expired-tokens", "--dry-run"])
    assert dry_run_result.exit_code == 0
    assert "Would prune 2 mobile token(s)" in dry_run_result.output

    prune_result = runner.invoke(args=["mobile-prune-expired-tokens"])
    assert prune_result.exit_code == 0
    assert "Pruned 2 mobile token(s)" in prune_result.output

    with app.app_context():
        assert MobileAuthToken.query.count() == 0
