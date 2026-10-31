def register(app):
    from app.modules.orchards.routes import bp
    from app.modules.orchards.seed import import_orchard_command

    app.register_blueprint(bp)
    app.cli.add_command(import_orchard_command)
