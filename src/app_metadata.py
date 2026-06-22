from pathlib import Path

APP_NAME = "ForensicPack"
APP_AUTHOR = "github.com/w617"
APP_VERSION = (Path(__file__).with_name("release_version.txt").read_text(encoding="utf-8").strip() or "2.0.8")
