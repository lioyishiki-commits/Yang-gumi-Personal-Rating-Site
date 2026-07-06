"""Install the tested Streamlit frontend used by Yang-gumi.

Streamlit 1.58.0's stock frontend uses JavaScript syntax and Web APIs that are
not available in the older Chromium-based Edge bundled with some Windows 10
images. The project ships a syntax-downleveled copy of that frontend bundle
and installs it into the project virtual environment before startup.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import shutil
from importlib import metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STREAMLIT_VERSION = "1.58.0"
BUNDLE_NAME = "index.dkY5s53S.js"
PATCH_DIR = ROOT / "compat" / f"streamlit-{STREAMLIT_VERSION}"
PATCH_BUNDLE = PATCH_DIR / BUNDLE_NAME
CACHE_QUERY = "yanggumi-old-edge-compat-v2"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def streamlit_static_root() -> Path:
    spec = importlib.util.find_spec("streamlit")
    if spec is None or spec.origin is None:
        raise RuntimeError("未安装 Streamlit，请先运行“安装并启动 Yang-gumi.bat”。")
    return Path(spec.origin).resolve().parent / "static"


def ensure_streamlit_frontend_compatibility() -> bool:
    """Install the pinned, old-Edge-compatible frontend when necessary."""
    installed = metadata.version("streamlit")
    if installed != STREAMLIT_VERSION:
        raise RuntimeError(
            f"当前 Streamlit 版本为 {installed}，Yang-gumi 需要 {STREAMLIT_VERSION}。"
            "请重新运行“安装并启动 Yang-gumi.bat”修复依赖。"
        )
    if not PATCH_BUNDLE.is_file():
        raise RuntimeError(f"缺少浏览器兼容文件：{PATCH_BUNDLE}")

    static_root = streamlit_static_root()
    target_bundle = static_root / "static" / "js" / BUNDLE_NAME
    index_path = static_root / "index.html"
    if not target_bundle.is_file() or not index_path.is_file():
        raise RuntimeError("Streamlit 前端文件不完整，请删除 .venv 后重新安装。")

    changed = False
    if _digest(target_bundle) != _digest(PATCH_BUNDLE):
        shutil.copyfile(PATCH_BUNDLE, target_bundle)
        changed = True

    index_source = index_path.read_text(encoding="utf-8")
    script_pattern = re.compile(
        rf'(src="\./static/js/{re.escape(BUNDLE_NAME)})(?:\?[^\"]*)?(\")'
    )
    updated_index = script_pattern.sub(
        rf'\1?v={CACHE_QUERY}\2', index_source, count=1
    )
    if updated_index != index_source:
        index_path.write_text(updated_index, encoding="utf-8")
        changed = True
    return changed
