import os
from datetime import datetime, timezone

from flask import Flask

from . import fmt
from .board import load_board
from .store import Store


def create_app(db_path: str | None = None, host_ip: str | None = None) -> Flask:
    app = Flask(__name__)
    # Only used to sign flash messages; there is no auth on a LAN-only banker app.
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.urandom(16).hex()
    app.config["HOST_IP"] = host_ip if host_ip is not None else os.environ.get("HOST_IP", "")
    app.config["PUBLIC_PORT"] = os.environ.get("PUBLIC_PORT", "8080")
    board = load_board(os.environ.get("EDITION", "uk"))
    fmt.CURRENCY = board.currency
    app.extensions["store"] = Store(db_path or os.environ.get("DB_PATH", "monopoly.db"), board)

    app.add_template_filter(fmt.money, "money")
    app.add_template_filter(fmt.price, "price")
    app.add_template_filter(fmt.pct, "pct")
    app.add_template_filter(buildings, "buildings")
    app.add_template_filter(ago, "ago")

    from . import views
    app.register_blueprint(views.bp)
    return app


def buildings(n: int) -> str:
    return {0: "—", 5: "Hotel"}.get(n, f"{n} house{'s' if n != 1 else ''}")


def ago(iso: str | None) -> str:
    if not iso:
        return "never"
    then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    minutes = int((datetime.now(timezone.utc) - then).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    return f"{minutes // 60} h {minutes % 60} min ago"
