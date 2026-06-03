import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
ASSETS_DIR = BACKEND_DIR / "assets"
DATA_ASSETS_DIR = ASSETS_DIR / "data"
EXAMPLES_DIR = ASSETS_DIR / "examples"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
RUNTIME_DIR = BACKEND_DIR / "runtime"
BOOKS_DIR = RUNTIME_DIR / "books"
UPLOADS_DIR = RUNTIME_DIR / "uploads"
GRAPHS_DIR = RUNTIME_DIR / "graphs"
LOGS_DIR = RUNTIME_DIR / "logs"
INDEXES_DIR = RUNTIME_DIR / "indexes"
ARCHIVE_DIR = RUNTIME_DIR / "archive"
PERSONA_KB_DIR = DATA_ASSETS_DIR / "processed" / "personas" / "persona_kb"
ENV_FILE = ROOT_DIR / ".env"


def _load_local_env() -> None:
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (key not in os.environ or not os.environ[key].strip()):
            os.environ[key] = value


_load_local_env()

for directory in (
    BACKEND_DIR,
    ASSETS_DIR,
    DATA_ASSETS_DIR,
    EXAMPLES_DIR,
    SCHEMAS_DIR,
    RUNTIME_DIR,
    BOOKS_DIR,
    UPLOADS_DIR,
    GRAPHS_DIR,
    LOGS_DIR,
    INDEXES_DIR,
    ARCHIVE_DIR,
    PERSONA_KB_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)
