from __future__ import annotations

import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = ROOT / "tools" / "streamlit_modern_compat"
BUNDLED_FRONTEND_ROOT = ROOT / "compat" / "streamlit-1.58.0"
LEGACY_FRONTEND_MARKER = "/* yanggumi-old-edge-compat-v2 */"


def _installed_streamlit_root() -> Path:
    import streamlit

    return Path(streamlit.__file__).resolve().parent


def _patch_static_cache_headers(streamlit_root: Path) -> None:
    route_path = streamlit_root / "web" / "server" / "starlette" / "starlette_routes.py"
    source = route_path.read_text(encoding="utf-8")
    if '"public, max-age=31536000, immutable"' in source:
        return
    original = (
        '        response.headers["Access-Control-Allow-Origin"] = "*"\n'
        '        response.headers["X-Content-Type-Options"] = "nosniff"\n'
    )
    replacement = original + (
        '        cache_path = relative_path.replace("\\\\", "/")\n'
        '        if cache_path.startswith(("share_assets/", "daily_art/")):\n'
        '            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"\n'
        '        else:\n'
        '            response.headers["Cache-Control"] = "public, max-age=86400"\n'
    )
    if original not in source:
        raise RuntimeError("Streamlit 1.58 static route layout changed; compatibility patch was not applied")
    route_path.write_text(source.replace(original, replacement, 1), encoding="utf-8")


def _install_legacy_frontend(streamlit_root: Path) -> None:
    bundled = list(BUNDLED_FRONTEND_ROOT.glob("index.*.js"))
    targets = list((streamlit_root / "static" / "static" / "js").glob("index.*.js"))
    if len(bundled) != 1 or len(targets) != 1:
        raise RuntimeError("Expected exactly one bundled and one installed Streamlit frontend entry")
    source = bundled[0].read_text(encoding="utf-8")
    if not source.startswith(LEGACY_FRONTEND_MARKER):
        raise RuntimeError("Bundled legacy frontend marker is missing")
    targets[0].write_text(source, encoding="utf-8")

    index_path = streamlit_root / "static" / "index.html"
    index_source = index_path.read_text(encoding="utf-8")
    script_pattern = re.compile(r'(src="\./static/js/index\.[^"]+\.js)(?:\?[^\"]*)?(\")')
    index_path.write_text(script_pattern.sub(r"\1\2", index_source, count=1), encoding="utf-8")


def prepare_runtime() -> Path:
    source_root = _installed_streamlit_root()
    target_root = RUNTIME_ROOT / "streamlit"
    if not target_root.is_dir():
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_root, target_root)
    _patch_static_cache_headers(target_root)
    _install_legacy_frontend(target_root)
    return RUNTIME_ROOT


if __name__ == "__main__":
    prepare_runtime()
