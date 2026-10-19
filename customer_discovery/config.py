"""Read the project's simple .env file without another dependency.

Process environment variables win. The file is resolved beside pyproject.toml,
so callers can run the module from another working directory.
"""
import ast
import os
from pathlib import Path


ENV_FILE = Path(__file__).resolve().parent.parent / '.env'
KEYS = {'JUSTONEAPI_TOKEN', 'LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL', 'DISCOVERY_DB'}


def settings() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding='utf-8-sig').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, sep, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if not sep or key not in KEYS:
                continue
            if value.startswith(("'", '"')):
                try:
                    value = ast.literal_eval(value)
                except (SyntaxError, ValueError):
                    raise ValueError(f'Invalid .env value for {key}') from None
                if not isinstance(value, str):
                    raise ValueError(f'Invalid .env value for {key}')
            values[key] = value
    return {key: os.environ.get(key, values.get(key, '')) for key in KEYS}
