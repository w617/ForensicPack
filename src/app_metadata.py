from pathlib import Path

APP_NAME = "ForensicPack"
APP_AUTHOR = "github.com/w617"
_DEFAULT_VERSION = "2.1.0"

try:
    APP_VERSION = Path(__file__).with_name("release_version.txt").read_text(encoding="utf-8").strip()
except OSError:
    APP_VERSION = _DEFAULT_VERSION

if not APP_VERSION:
    APP_VERSION = _DEFAULT_VERSION
