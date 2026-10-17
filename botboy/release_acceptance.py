from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
import venv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_TEMP_ROOT = Path(
    os.getenv(
        "BOTBOY_ACCEPTANCE_TEMP_ROOT",
        str(Path(tempfile.gettempdir()) / "botboy-release-acceptance"),
    )
)
IGNORED_ROOT_NAMES = {
    ".git",
    ".venv",
    ".env",
    ".pytest_cache",
    ".ruff_cache",
    ".botboy-mcp-runtime",
    ".botboy-runtime",
    ".codex_eval_runtime",
    ".deps-build",
    ".release-build-venv",
    ".venv-build",
    "botboy.egg-info",
    "build",
    "dist",
}
REQUIRED_SDIST_ENTRIES = (
    "README.md",
    "pyproject.toml",
    "setup.py",
    "MANIFEST.in",
    "botboy_mcp_server.py",
    "botboy/data/STATUS_SNAPSHOT.json",
    "botboy/bundled/skills/public_skills/botboy-cloud-topology-planner/SKILL.md",
    "botboy/bundled/examples/skills/calculator/SKILL.md",
    "skills/public_skills/botboy-cloud-topology-planner/SKILL.md",
    "examples/skills/calculator/SKILL.md",
    "tests/test_release_resources.py",
)
REQUIRED_WHEEL_ENTRIES = (
    "botboy/__main__.py",
    "botboy/release_smoke.py",
    "botboy/resources.py",
    "botboy/data/STATUS_SNAPSHOT.json",
    "botboy/data/evals/release_smoke_manifest.json",
    "botboy/data/evals/replays/release_smoke_seed.jsonl",
    "botboy/bundled/skills/public_skills/botboy-cloud-topology-planner/SKILL.md",
    "botboy/bundled/examples/skills/calculator/SKILL.md",
    "botboy/web/index.html",
    "botboy_mcp_server.py",
)


def _normalize_archive_names(names: list[str]) -> list[str]:
    return [name.replace("\\", "/") for name in names]


def _resolve_executable(spec: str) -> str:
    candidate = Path(spec)
    if candidate.exists() or any(token in spec for token in ("\\", "/", ":")):
        # Resolving a venv's Python symlink selects the base interpreter and
        # silently discards the very environment the caller asked us to test.
        return os.path.abspath(candidate)
    return spec


def _assert_no_absolute_archive_names(names: list[str], *, artifact: Path) -> None:
    for name in names:
        if not name:
            continue
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or (len(normalized) > 2 and normalized[1:3] == ":/"):
            raise RuntimeError(f"{artifact.name} contains absolute archive member: {normalized}")


def _copy_ignore(_src: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in IGNORED_ROOT_NAMES or name == "__pycache__":
            ignored.add(name)
        elif name.startswith("tmp"):
            ignored.add(name)
        elif name.endswith((".pyc", ".pyo", ".pyd")):
            ignored.add(name)
    return ignored


def _clean_source_tree(temp_dir: Path) -> Path:
    target = temp_dir / "source"
    shutil.copytree(ROOT, target, ignore=_copy_ignore)
    return target


def _create_workspace_tempdir(prefix: str) -> Path:
    RUNTIME_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = RUNTIME_TEMP_ROOT / f"{prefix}{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    return temp_dir


def _venv_python_path(env_dir: Path) -> Path:
    windows = env_dir / "Scripts" / "python.exe"
    if windows.exists():
        return windows
    return env_dir / "bin" / "python"


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    label: str,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = [f"{label} failed with exit code {result.returncode}."]
        if result.stdout.strip():
            message.append(result.stdout.strip())
        if result.stderr.strip():
            message.append(result.stderr.strip())
        raise RuntimeError("\n".join(message))
    return result


def _command_env(base_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    temp_root = (base_dir / "_tmp").resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(temp_root)
    env["TEMP"] = str(temp_root)
    if extra:
        env.update(extra)
    return env


def _inspect_sdist(artifact: Path) -> None:
    with tarfile.open(artifact, "r:gz") as archive:
        raw_names = archive.getnames()
    normalized = _normalize_archive_names(raw_names)
    _assert_no_absolute_archive_names(normalized, artifact=artifact)
    members = ["/".join(name.split("/")[1:]) for name in normalized if "/" in name]
    for required in REQUIRED_SDIST_ENTRIES:
        if required not in members:
            raise RuntimeError(f"{artifact.name} missing expected sdist member: {required}")


def _inspect_wheel(artifact: Path) -> None:
    with zipfile.ZipFile(artifact) as archive:
        raw_names = archive.namelist()
    normalized = _normalize_archive_names(raw_names)
    _assert_no_absolute_archive_names(normalized, artifact=artifact)
    for required in REQUIRED_WHEEL_ENTRIES:
        if required not in normalized:
            raise RuntimeError(f"{artifact.name} missing expected wheel member: {required}")


def build_release_artifacts(*, python_executable: str | Path, outdir: Path) -> tuple[Path, Path]:
    output_root = outdir.resolve()
    temp_root = RUNTIME_TEMP_ROOT.resolve()
    if output_root.is_relative_to(temp_root) or temp_root.is_relative_to(output_root):
        raise ValueError("Artifact output and acceptance temporary root must be disjoint")
    outdir.mkdir(parents=True, exist_ok=True)
    temp_dir = _create_workspace_tempdir("build-")
    try:
        source_root = _clean_source_tree(temp_dir)
        build_env = _command_env(temp_dir)
        try:
            _run(
                [str(python_executable), "-m", "build", "--sdist", "--wheel", "--outdir", str(outdir), "--no-isolation"],
                cwd=source_root,
                env=build_env,
                label="release build",
            )
        except RuntimeError as exc:
            error_text = str(exc)
            if "PermissionError" not in error_text and "pyproject_hooks" not in error_text:
                raise
            _run(
                [str(python_executable), "setup.py", "sdist", f"--dist-dir={outdir}"],
                cwd=source_root,
                env=build_env,
                label="release build fallback sdist",
            )
            _run(
                [str(python_executable), "setup.py", "bdist_wheel", f"--dist-dir={outdir}"],
                cwd=source_root,
                env=build_env,
                label="release build fallback wheel",
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    sdist = max(outdir.glob("botboy-*.tar.gz"), key=lambda path: path.stat().st_mtime)
    wheel = max(outdir.glob("botboy-*.whl"), key=lambda path: path.stat().st_mtime)
    _inspect_sdist(sdist)
    _inspect_wheel(wheel)
    return sdist, wheel


def _create_venv(env_dir: Path) -> Path:
    builder = venv.EnvBuilder(with_pip=False, clear=True)
    builder.create(env_dir)
    python_executable = _venv_python_path(env_dir)
    if not python_executable.exists():
        raise RuntimeError(f"Failed to create virtual environment at {env_dir}")
    return python_executable


def _pip_install(
    bootstrap_python: str | Path,
    env_python: Path,
    spec: str,
    *,
    base_dir: Path,
) -> None:
    command = [str(bootstrap_python), "-m", "pip", "--python", str(env_python), "install"]
    wheelhouse = os.getenv("BOTBOY_ACCEPTANCE_WHEELHOUSE", "")
    if wheelhouse:
        wheelhouse_path = Path(wheelhouse).expanduser().resolve()
        if not wheelhouse_path.is_dir():
            raise ValueError("BOTBOY_ACCEPTANCE_WHEELHOUSE must be an existing directory")
        command.extend(["--no-index", "--find-links", str(wheelhouse_path)])
    command.append(spec)
    _run(
        command,
        cwd=base_dir,
        env=_command_env(base_dir),
        label=f"pip install {spec}",
    )


def _botboy_env(base_dir: Path, name: str) -> dict[str, str]:
    env = _command_env(base_dir)
    env["BOTBOY_HOME"] = str((base_dir / "botboy-home" / name).resolve())
    return env


def _run_botboy_command(env_python: Path, base_dir: Path, name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [str(env_python), "-I", "-m", "botboy", *args],
        cwd=base_dir,
        env=_botboy_env(base_dir, name),
        label=f"botboy {' '.join(args)}",
    )


def run_install_acceptance(*, bootstrap_python: str | Path, wheel_path: Path) -> None:
    temp_dir = _create_workspace_tempdir("install-")
    try:
        minimal_python = _create_venv(temp_dir / "release-minimal")
        standard_python = _create_venv(temp_dir / "release-standard")
        _pip_install(bootstrap_python, minimal_python, str(wheel_path), base_dir=temp_dir)
        _run(
            [str(minimal_python), "-I", "-c", "import importlib.util; assert importlib.util.find_spec('fastapi') is None, 'Base install unexpectedly includes FastAPI'"],
            cwd=temp_dir,
            env=_command_env(temp_dir),
            label="minimal dependency isolation",
        )
        _run_botboy_command(minimal_python, temp_dir, "minimal", "version")
        _run_botboy_command(minimal_python, temp_dir, "minimal", "modules")
        _run_botboy_command(minimal_python, temp_dir, "minimal", "test")
        _run_botboy_command(minimal_python, temp_dir, "minimal", "evals", "--summary")
        _run_botboy_command(minimal_python, temp_dir, "minimal", "exec", "status")

        standard_spec = f"botboy[standard] @ {wheel_path.resolve().as_uri()}"
        _pip_install(bootstrap_python, standard_python, standard_spec, base_dir=temp_dir)
        _run_botboy_command(standard_python, temp_dir, "standard", "version")
        modules = _run_botboy_command(standard_python, temp_dir, "standard", "modules")
        lowered = modules.stdout.lower()
        for expected in ("fastapi", "uvicorn", "aiohttp", "websockets"):
            if expected not in lowered:
                raise RuntimeError(f"release-standard modules output missing {expected}: {modules.stdout}")
        _run(
            [
                str(standard_python),
                "-I",
                "-c",
                "import aiohttp, fastapi, uvicorn, websockets; print('standard-runtime-ok')",
            ],
            cwd=temp_dir,
            env=_command_env(temp_dir),
            label="standard runtime imports",
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and verify BotBoy release artifacts.")
    parser.add_argument(
        "mode",
        choices=("build", "install", "full"),
        help="build artifacts only, run install checks from an existing wheel, or do both",
    )
    parser.add_argument(
        "--python",
        dest="build_python_executable",
        default=sys.executable,
        help="Python executable with build tooling available.",
    )
    parser.add_argument(
        "--bootstrap-python",
        dest="bootstrap_python_executable",
        default=sys.executable,
        help="Python executable with pip available for clean-install checks.",
    )
    parser.add_argument(
        "--outdir",
        default=str(ROOT / "dist"),
        help="Output directory for build artifacts.",
    )
    parser.add_argument(
        "--wheel",
        default="",
        help="Existing wheel path for install mode.",
    )
    args = parser.parse_args(argv)

    build_python_executable = _resolve_executable(args.build_python_executable)
    bootstrap_python_executable = _resolve_executable(args.bootstrap_python_executable)
    outdir = Path(args.outdir).resolve()

    if args.mode == "build":
        build_release_artifacts(python_executable=build_python_executable, outdir=outdir)
        print(f"Built release artifacts in {outdir}")
        return 0

    if args.mode == "install":
        wheel_path = Path(args.wheel).resolve()
        if not wheel_path.exists():
            raise SystemExit("--wheel must point to an existing wheel for install mode")
        run_install_acceptance(bootstrap_python=bootstrap_python_executable, wheel_path=wheel_path)
        print(f"Verified install acceptance for {wheel_path.name}")
        return 0

    _, wheel = build_release_artifacts(python_executable=build_python_executable, outdir=outdir)
    run_install_acceptance(bootstrap_python=bootstrap_python_executable, wheel_path=wheel)
    print(f"Built and verified release artifacts in {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
