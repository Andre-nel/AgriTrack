from flask import Flask, jsonify, render_template, request


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api"):
            return jsonify({"error": "Not found"}), 404
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(_error):
        if request.path.startswith("/api"):
            return jsonify({"error": "Server error"}), 500
        return render_template("500.html"), 500

