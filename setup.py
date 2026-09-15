from __future__ import annotations

from pathlib import Path

from setuptools import find_packages, setup


ROOT = Path(__file__).parent.resolve()
PACKAGE_ROOT = ROOT / "botboy"
IGNORED_PACKAGE_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
IGNORED_PACKAGE_FILENAMES = {".DS_Store"}
IGNORED_PACKAGE_SUFFIXES = {".pyc", ".pyo", ".pyd"}


def _collect_package_data(relative_root: str) -> list[str]:
    source_root = PACKAGE_ROOT / relative_root
    if not source_root.exists():
        return []
    package_files: list[str] = []
    for file_path in sorted(source_root.rglob("*")):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(PACKAGE_ROOT)
        parts = relative.parts
        if any(part in IGNORED_PACKAGE_PARTS for part in parts[:-1]):
            continue
        if file_path.name in IGNORED_PACKAGE_FILENAMES:
            continue
        if file_path.suffix in IGNORED_PACKAGE_SUFFIXES:
            continue
        package_files.append(str(relative).replace("\\", "/"))
    return package_files

setup(
    name="botboy",
    version="0.6.0-dev",
    packages=find_packages(include=["botboy", "botboy.*"]),
    py_modules=["botboy_mcp_server"],
    include_package_data=True,
    package_data={
        "botboy": (
            _collect_package_data("data")
            + _collect_package_data("web")
            + _collect_package_data("bundled")
        )
    },
    python_requires=">=3.10",
    install_requires=["pyyaml>=6.0"],
    extras_require={
        "standard": [
            "fastapi>=0.109.0,<0.140.0",
            "uvicorn[standard]>=0.27.0",
            "websockets>=12.0",
            "aiohttp>=3.9.0",
            "httpx>=0.26.0",
            "psutil>=5.9.0",
        ],
        "full": ["docker>=7.0.0", "sentence-transformers>=2.2.0", "vosk>=0.3.45", "pyaudio>=0.2.13"],
        "dev": ["pytest>=8.0.0", "pytest-asyncio>=0.23.0", "bandit>=1.7.0", "black>=24.0.0"],
    },
    entry_points={
        "console_scripts": [
            "botboy=botboy.__main__:main",
            "botboy-mcp=botboy_mcp_server:main",
        ]
    },
)
