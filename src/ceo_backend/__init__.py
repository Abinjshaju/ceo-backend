import argparse
import logging
import os
import uuid
from pathlib import Path


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ceo-backend", description="CEO workspace backend")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    commands.add_parser("worker", help="Run the background job worker")
    commands.add_parser("migrate", help="Apply database migrations")

    token = commands.add_parser("dev-token", help="Issue a development token (AUTH_MODE=hs256)")
    token.add_argument("--email", required=True)
    token.add_argument("--subject", help="Stable user id; defaults to one derived from the email")
    token.add_argument("--name")
    token.add_argument("--hours", type=int, default=24)

    args = parser.parse_args()
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        import uvicorn

        uvicorn.run("ceo_backend.main:app", host=args.host, port=args.port, reload=args.reload)
    elif args.command == "worker":
        from ceo_backend.config import Settings
        from ceo_backend.services.container import build_services
        from ceo_backend.services.jobs import worker_loop

        worker_loop(build_services(Settings.from_env()))
    elif args.command == "migrate":
        from alembic import command
        from alembic.config import Config

        candidates = [os.getenv("ALEMBIC_CONFIG"), Path.cwd() / "alembic.ini",
                      Path(__file__).resolve().parents[2] / "alembic.ini"]
        path = next(str(c) for c in candidates if c and Path(c).is_file())
        config = Config(path)
        config.set_main_option("script_location", str(Path(path).parent / "migrations"))
        command.upgrade(config, "head")
    elif args.command == "dev-token":
        from ceo_backend.auth import mint_dev_token
        from ceo_backend.config import Settings

        subject = args.subject or str(uuid.uuid5(uuid.NAMESPACE_URL, f"mailto:{args.email.lower()}"))
        print(mint_dev_token(Settings.from_env(), subject, args.email, args.name, args.hours * 3600))
