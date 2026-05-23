from flask import Flask, jsonify, render_template, request, current_app


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api"):
            return jsonify({"error": "Not found"}), 404
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        # Re-raise in debug mode so Werkzeug shows the traceback page
        if current_app.debug:
            original = getattr(error, "original_exception", None)
            raise original or error

        if request.path.startswith("/api"):
            return jsonify({"error": "Server error"}), 500
        return render_template("500.html"), 500