"""Đọc file .env ở thư mục dự án (không ghi đè biến đã có trong shell)."""
import os
from pathlib import Path


def load_env_file(path=None):
    env_path = Path(path) if path else Path(__file__).resolve().parent / '.env'
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].strip()
        if '=' not in line:
            continue
        name, value = line.split('=', 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value
