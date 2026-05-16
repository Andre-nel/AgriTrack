import re
import shutil
import uuid
from pathlib import Path

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db


@pytest.fixture
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def tmp_path(request):
    base_path = Path(__file__).resolve().parent.parent / "pytest-cache-files-workspace-tmp"
    safe_name = re.sub(r"[\W]", "_", request.node.name)[:30]
    path = base_path / f"{safe_name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    yield path
    shutil.rmtree(path, ignore_errors=True)
