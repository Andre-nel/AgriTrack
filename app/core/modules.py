from dataclasses import dataclass
from importlib import import_module

from flask import Flask


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    package: str
    description: str


MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec("auth", "app.modules.auth", "Private web session authentication"),
    ModuleSpec("legacy_web", "app.modules.legacy_web", "Compatibility web routes awaiting domain extraction"),
    ModuleSpec("tasks", "app.modules.tasks", "Task workspace web routes"),
    ModuleSpec("calendar", "app.modules.calendar", "Calendar web routes"),
    ModuleSpec("finance", "app.modules.finance", "Finance and cash-flow web routes"),
    ModuleSpec("ops", "app.modules.ops", "Private farm operations pages"),
    ModuleSpec("api_v1", "app.modules.api_v1", "Versioned API entrypoints"),
    ModuleSpec("mobile_api", "app.modules.mobile_api", "Android mobile API entrypoints"),
    ModuleSpec("farms", "app.modules.farms", "Farm API routes"),
    ModuleSpec("paddocks", "app.modules.paddocks", "Paddock API routes"),
    ModuleSpec("mobs", "app.modules.mobs", "Mob API routes"),
    ModuleSpec("water", "app.modules.water", "Water network API routes"),
)


def register_modules(app: Flask) -> None:
    for spec in MODULES:
        module = import_module(spec.package)
        module.register(app)
