"""WSGI config for forum_simulator project."""
from __future__ import annotations

import os
from pathlib import Path

from django.core.wsgi import get_wsgi_application


def _load_env() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        def load_dotenv(path: Path) -> None:
            for raw_line in path.read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))
    load_dotenv(env_path)


_load_env()

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'forum_simulator.settings')

application = get_wsgi_application()
