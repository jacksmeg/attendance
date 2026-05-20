from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import click
from flask import Flask

from .config import load_config
from .db import close_db, init_db
from .services.maintenance import reset_system_data
from .services.seed import seed_demo_data
from .services.settings import save_admin_credentials_for_database
from .services.tenancy import (
    ensure_default_organization,
    get_organization_by_slug,
    init_platform_registry,
    list_organizations,
    provision_organization,
    set_current_organization,
)
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
        init_platform_registry(settings.platform_registry_path)
        ensure_default_organization(settings)
        init_db()

    register_routes(app)
    register_cli(app)
    return app


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-demo")
    @click.option("--slug", default="", help="Seed demo data into a specific organization slug.")
    def seed_demo_command(slug: str) -> None:
        if slug.strip():
            organization = get_organization_by_slug(app.config["APP_SETTINGS"], slug)
            if not organization:
                raise click.ClickException(f"Organization '{slug}' was not found.")
            set_current_organization(organization)
        created = seed_demo_data()
        if created:
            print("Demo staff, fingerprints, and sample attendance records created.")
        else:
            print("Demo data skipped because staff records already exist.")

    @app.cli.command("reset-live-data")
    @click.option("--slug", default="", help="Reset live data for a specific organization slug.")
    def reset_live_data_command(slug: str) -> None:
        settings = app.config["APP_SETTINGS"]
        if slug.strip():
            organization = get_organization_by_slug(settings, slug)
            if not organization:
                raise click.ClickException(f"Organization '{slug}' was not found.")
            set_current_organization(organization)
            instance_dir = organization.instance_dir
            database_path = organization.database_path
        else:
            organization = ensure_default_organization(settings)
            instance_dir = organization.instance_dir
            database_path = organization.database_path
        backup_path = reset_system_data(
            instance_dir=instance_dir,
            database_path=database_path,
        )
        if backup_path:
            print(f"Live data reset complete. Backup saved to: {backup_path}")
        else:
            print("Live data reset complete. No existing database backup was needed.")

    @app.cli.command("create-organization")
    @click.option("--slug", required=True, help="Unique organization slug, for example acme-hospital.")
    @click.option("--name", required=True, help="Display name for the organization.")
    @click.option(
        "--admin-username",
        default="",
        help="Institution admin username. Defaults to ATTENDANCE_ADMIN_USER if omitted.",
    )
    @click.option(
        "--admin-password",
        default="",
        help="Institution admin password. Defaults to ATTENDANCE_ADMIN_PASSWORD if omitted.",
    )
    @click.option(
        "--hostname",
        "hostnames",
        multiple=True,
        help="Optional custom hostname or subdomain. Repeat to add multiple hostnames.",
    )
    def create_organization_command(
        slug: str,
        name: str,
        admin_username: str,
        admin_password: str,
        hostnames: tuple[str, ...],
    ) -> None:
        settings = app.config["APP_SETTINGS"]
        organization = provision_organization(
            settings,
            slug=slug,
            display_name=name,
            hostnames=hostnames,
        )
        init_db(organization.database_path)
        save_admin_credentials_for_database(
            organization.database_path,
            username=admin_username.strip() or settings.admin_username,
            password=admin_password or settings.admin_password,
        )
        print(f"Organization created: {organization.display_name} ({organization.slug})")
        print(f"Database: {organization.database_path}")
        print(f"Files: {organization.instance_dir}")
        print(f"Admin username: {admin_username.strip() or settings.admin_username}")
        print("Admin password configured for institution login.")
        if organization.hostnames:
            print("Hostnames: " + ", ".join(organization.hostnames))

    @app.cli.command("list-organizations")
    def list_organizations_command() -> None:
        settings = app.config["APP_SETTINGS"]
        organizations = list_organizations(settings)
        if not organizations:
            print("No organizations have been created yet.")
            return
        for organization in organizations:
            hostnames = ", ".join(organization.hostnames) if organization.hostnames else "-"
            default_label = " (default)" if organization.is_default else ""
            print(
                f"{organization.slug}{default_label} | {organization.display_name} | "
                f"{organization.database_path} | {hostnames}"
            )
