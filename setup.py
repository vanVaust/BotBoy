from __future__ import annotations

from pathlib import Path

from setuptools import find_packages, setup


ROOT = Path(__file__).parent.resolve()
PACKAGE_ROOT = ROOT / "botboy"

# The FastAPI gateway path, plus what the gateway boundary tests need to drive
# it. Shared with the "dev" extra so the documented test setup is self
# sufficient and the version pins are not duplicated.
STANDARD_REQUIREMENTS = [
    # 0.139.x is the latest FastAPI line before the OpenAPI/ForwardRef
    # regression observed with BotBoy's postponed route annotations.
    "fastapi>=0.109.0,<0.139.0",
    # Lazily imported and invoked in botboy/gateway/server.py.
    "uvicorn[standard]>=0.27.0",
    # Not imported anywhere; asserted as part of the standard-runtime contract
    # by release_acceptance.check_standard_runtime and
    # tests/test_release_cli_gateway.py. Retires with the FastAPI path.
    "websockets>=12.0",
    # Lazily imported with a graceful fallback in botboy/core/state.py and
    # botboy/llm/integration.py. Independent of the FastAPI path: it does NOT
    # retire with it.
    "aiohttp>=3.9.0",
    # Required by fastapi.testclient.TestClient, which eleven gateway boundary
    # test modules import at module scope.
    "httpx>=0.26.0",
]
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
        # Every entry in an extra is either imported somewhere (including lazily,
        # inside a function) or asserted as part of the standard-runtime
        # contract. Anything that is neither does not belong here: a dependency
        # nobody imports is a promise about software that does not exist. Verify
        # with an AST walk over botboy/, not with a line-anchored grep -- a grep
        # pattern that requires a trailing character misses `import x` at the
        # end of a line.
        "standard": STANDARD_REQUIREMENTS,
        # Lazily imported with availability checks in botboy/voice/listener.py.
        "full": ["vosk>=0.3.45", "pyaudio>=0.2.13"],
        # Self sufficient on purpose: `pip install ".[dev]"` must yield an
        # environment in which the full suite collects and passes. Three groups
        # are needed for that, and leaving any of them out makes the documented
        # test command fail rather than pass:
        #   - the standard runtime, because eleven test modules import fastapi
        #     (and through it starlette) at module scope;
        #   - the test runners;
        #   - build, wheel and setuptools, because
        #     tests/test_release_build_install.py builds and installs the
        #     project. Without them that module does not skip, it fails:
        #     _select_build_python() finds an interpreter via sys.executable, so
        #     the build runs and then aborts -- "No module named build", or
        #     "Backend 'setuptools.build_meta' is not available" once build is
        #     present, since a Python 3.12+ venv no longer ships setuptools.
        #     These three mirror what the CI workflow installs separately.
        "dev": STANDARD_REQUIREMENTS
        + [
            "pytest>=8.0.0",
            "pytest-asyncio>=0.23.0",
            "bandit>=1.7.0",
            "black>=24.0.0",
            "build>=1.0.0",
            "wheel>=0.42.0",
            "setuptools>=68",
        ],
    },
    entry_points={
        "console_scripts": [
            "botboy=botboy.__main__:main",
            "botboy-mcp=botboy_mcp_server:main",
        ]
    },
)
