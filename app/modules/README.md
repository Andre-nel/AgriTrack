# AgriTrack Modules

Each package under `app/modules/<domain>` owns one feature area of the Flask
modular monolith.

Module packages should expose a `register(app)` function when they register
routes. Keep HTTP parsing in routes/forms, template payload assembly in
presenters, business rules in services, and JSON shape in serializers/schemas.

During the compatibility phase, web route modules expose `register_legacy_routes(bp)`
so they can keep the existing `web.*` endpoint names while the route bodies live
with their owning domain.

The `legacy_web` module now only assembles the compatibility `web` blueprint.
