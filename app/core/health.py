from flask import Flask


def register_healthcheck(app: Flask) -> None:
    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

