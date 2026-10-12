import logging

from dotenv import load_dotenv

load_dotenv()

from ceo_backend.app import create_app  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()
