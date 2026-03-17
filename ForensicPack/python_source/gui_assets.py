from pathlib import Path


ASSET_DIR = Path(__file__).resolve().parent / "assets"
TITLE_BANNER_CANDIDATES = (
    "forensicpack_banner.png",
    "forensicpack_logo.png",
    # If no banner is provided, reuse the main icon artwork in the header.
    "forensicpack_icon.png",
)


def resolve_gui_asset_path(filename: str) -> Path:
    return ASSET_DIR / filename


def resolve_first_existing_gui_asset(filenames: tuple[str, ...]) -> Path | None:
    for name in filenames:
        path = resolve_gui_asset_path(name)
        if path.is_file():
            return path
    return None
