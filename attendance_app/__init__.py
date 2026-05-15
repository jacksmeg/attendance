from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from flask import Flask

from .config import load_config
from .db import close_db, init_db
from .services.maintenance import reset_system_data
from .services.seed import seed_demo_data
from .views import register_routes


def create_app(overrides: Mapping[str, Any] | None = None) -> Flask:
    project_root = Path(__file__).resolve().parent.parent
    settings = load_config(project_root, overrides=overrides)

    app = Flask(
        __name__,
        instance_path=str(settings.instance_dir),
        instance_relative_config=False,
    )
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["APP_SETTINGS"] = settings

    settings.instance_dir.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()

    register_routes(app)
    register_cli(app)
    return app


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-demo")
    def seed_demo_command() -> None:
        created = seed_demo_data()
        if created:
            print("Demo staff, fingerprints, and sample attendance records created.")
        else:
            print("Demo data skipped because staff records already exist.")

    @app.cli.command("reset-live-data")
    def reset_live_data_command() -> None:
        settings = app.config["APP_SETTINGS"]
        backup_path = reset_system_data(
            instance_dir=settings.instance_dir,
            database_path=settings.database_path,
        )
        if backup_path:
            print(f"Live data reset complete. Backup saved to: {backup_path}")
        else:
            print("Live data reset complete. No existing database backup was needed.")
