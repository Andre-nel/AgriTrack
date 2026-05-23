from app import create_app
from app.config import get_config


app = create_app(get_config())

print("DEBUG:", app.debug)
print("PROPAGATE_EXCEPTIONS:", app.config.get("PROPAGATE_EXCEPTIONS"))