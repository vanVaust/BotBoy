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
DEFAULT_RUNTIME_TEMP_ROOT = (Path(tempfile.gettempdir()) / "botboy-release-acceptance").resolve()
RUNTIME_TEMP_ROOT = Path(
    os.getenv(
        "BOTBOY_ACCEPTANCE_TEMP_ROOT",
        str(DEFAULT_RUNTIME_TEMP_ROOT),
    )
).expanduser().resolve()
IGNORED_ROOT_NAMES = {
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
FORBIDDEN_WHEEL_PREFIXES = ("skills/", "examples/skills/", "tests/")
FORBIDDEN_SDIST_PREFIXES = (
    ".botboy-mcp-runtime/",
    ".botboy-runtime/",
    ".codex_eval_runtime/",
    ".deps-build/",
    ".release-build-venv/",
    ".venv-build/",
    "__pycache__/",
)
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
        return str(candidate.resolve())
    return spec


def _assert_no_absolute_archive_names(names: list[str], *, artifact: Path) -> None:
    for name in names:
        if not name:
            continue
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or (len(normalized) > 2 and normalized[1:3] == ":/"):
            raise RuntimeError(f"{artifact.name} contains absolute archive member: {normalized}")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _assert_disjoint_paths(paths: dict[str, Path]) -> None:
    items = [(label, value.expanduser().resolve()) for label, value in paths.items()]
    for index, (label_a, path_a) in enumerate(items):
        for label_b, path_b in items[index + 1:]:
            if path_a == path_b or _is_relative_to(path_a, path_b) or _is_relative_to(path_b, path_a):
                raise RuntimeError(
                    "Release write-set paths must be disjoint: "
                    f"{label_a}={path_a} overlaps with {label_b}={path_b}"
                )


def _assert_no_path_traversal_archive_names(names: list[str], *, artifact: Path) -> None:
    for name in names:
        normalized = name.strip("/")
        if not normalized:
            continue
        segments = [segment for segment in normalized.split("/") if segment]
        if any(segment == ".." for segment in segments):
            raise RuntimeError(f"{artifact.name} contains traversal archive member: {name}")


def _assert_no_cache_members(names: list[str], *, artifact: Path) -> None:
    for name in names:
        normalized = name.strip("/")
        if not normalized:
            continue
        if "/__pycache__/" in f"/{normalized}/" or normalized.endswith((".pyc", ".pyo", ".pyd")):
            raise RuntimeError(f"{artifact.name} contains cache-like member: {name}")


def _assert_no_forbidden_prefixes(names: list[str], *, artifact: Path, forbidden_prefixes: tuple[str, ...]) -> None:
    for name in names:
        normalized = name.strip("/")
        if not normalized:
            continue
        for forbidden in forbidden_prefixes:
            if normalized.startswith(forbidden):
                raise RuntimeError(f"{artifact.name} contains forbidden member: {normalized}")


def _assert_single_sdist_root(names: list[str], *, artifact: Path) -> str:
    roots: set[str] = set()
    for name in names:
        normalized = name.strip("/")
        if not normalized:
            continue
        roots.add(normalized.split("/", 1)[0])
    if len(roots) != 1:
        raise RuntimeError(f"{artifact.name} must contain exactly one top-level directory, got {sorted(roots)}")
    return next(iter(roots))


def _assert_no_tmp_root_members(names: list[str], *, artifact: Path) -> None:
    for name in names:
        normalized = name.strip("/")
        if not normalized:
            continue
        if normalized.split("/", 1)[0].startswith("tmp"):
            raise RuntimeError(f"{artifact.name} contains forbidden tmp* top-level member: {normalized}")


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
    for forbidden in IGNORED_ROOT_NAMES:
        if (target / forbidden).exists():
            raise RuntimeError(f"Clean source clone leaked ignored root directory: {forbidden}")
    if any(child.name.startswith("tmp") for child in target.iterdir()):
        raise RuntimeError("Clean source clone leaked tmp* root entries")
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
    _assert_no_path_traversal_archive_names(normalized, artifact=artifact)
    _assert_no_cache_members(normalized, artifact=artifact)
    root_prefix = _assert_single_sdist_root(normalized, artifact=artifact)
    members: list[str] = []
    for name in normalized:
        cleaned = name.strip("/")
        if not cleaned or cleaned == root_prefix:
            continue
        if not cleaned.startswith(f"{root_prefix}/"):
            raise RuntimeError(f"{artifact.name} contains inconsistent sdist member outside root: {cleaned}")
        members.append(cleaned[len(root_prefix) + 1 :])
    _assert_no_forbidden_prefixes(members, artifact=artifact, forbidden_prefixes=FORBIDDEN_SDIST_PREFIXES)
    _assert_no_tmp_root_members(members, artifact=artifact)
    for required in REQUIRED_SDIST_ENTRIES:
        if required not in members:
            raise RuntimeError(f"{artifact.name} missing expected sdist member: {required}")


def _inspect_wheel(artifact: Path) -> None:
    with zipfile.ZipFile(artifact) as archive:
        raw_names = archive.namelist()
    normalized = _normalize_archive_names(raw_names)
    _assert_no_absolute_archive_names(normalized, artifact=artifact)
    _assert_no_path_traversal_archive_names(normalized, artifact=artifact)
    _assert_no_cache_members(normalized, artifact=artifact)
    _assert_no_forbidden_prefixes(normalized, artifact=artifact, forbidden_prefixes=FORBIDDEN_WHEEL_PREFIXES)
    for required in REQUIRED_WHEEL_ENTRIES:
        if required not in normalized:
            raise RuntimeError(f"{artifact.name} missing expected wheel member: {required}")


def build_release_artifacts(*, python_executable: str | Path, outdir: Path) -> tuple[Path, Path]:
    outdir = outdir.expanduser().resolve()
    _assert_disjoint_paths({"acceptance temp root": RUNTIME_TEMP_ROOT, "release artifact outdir": outdir})
    outdir.mkdir(parents=True, exist_ok=True)
    temp_dir = _create_workspace_tempdir("build-")
    _assert_disjoint_paths({"release build temp dir": temp_dir, "release artifact outdir": outdir})
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


def _site_packages_dir(python_executable: str | Path, *, base_dir: Path) -> Path:
    result = _run(
        [
            str(python_executable),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        cwd=ROOT,
        env=_command_env(base_dir),
        label="resolve site-packages",
    )
    return Path(result.stdout.strip()).resolve()


def _copy_site_packages_seed(source_dir: Path, target_dir: Path) -> None:
    if not source_dir.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for item in source_dir.iterdir():
        if item.name == "__pycache__":
            continue
        destination = target_dir / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def _pip_install(
    bootstrap_python: str | Path,
    env_python: Path,
    spec: str,
    *,
    base_dir: Path,
    no_deps: bool = False,
) -> None:
    command = [str(bootstrap_python), "-m", "pip", "--python", str(env_python), "install"]
    if no_deps:
        command.append("--no-deps")
    command.append(spec)
    _run(
        command,
        cwd=ROOT,
        env=_command_env(base_dir),
        label=f"pip install {spec}",
    )


def _botboy_env(base_dir: Path, name: str) -> dict[str, str]:
    env = _command_env(base_dir)
    env["BOTBOY_HOME"] = str((base_dir / "botboy-home" / name).resolve())
    return env


def _run_botboy_command(env_python: Path, base_dir: Path, name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [str(env_python), "-m", "botboy", *args],
        cwd=base_dir,
        env=_botboy_env(base_dir, name),
        label=f"botboy {' '.join(args)}",
    )


def _select_standard_site_packages_seed(*, bootstrap_site_packages: Path) -> Path:
    candidates: list[Path] = []
    override = str(os.getenv("BOTBOY_ACCEPTANCE_STANDARD_SITE_PACKAGES", "")).strip()
    if override:
        candidates.append(Path(override).expanduser())
    release_build_root = ROOT / ".release-build-venv"
    candidates.append(release_build_root / "Lib" / "site-packages")
    candidates.extend(sorted((release_build_root / "lib").glob("python*/site-packages")))
    candidates.append(bootstrap_site_packages)

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return bootstrap_site_packages.expanduser().resolve()


def run_install_acceptance(*, bootstrap_python: str | Path, wheel_path: Path) -> None:
    temp_dir = _create_workspace_tempdir("install-")
    try:
        minimal_python = _create_venv(temp_dir / "release-minimal")
        standard_python = _create_venv(temp_dir / "release-standard")
        _assert_disjoint_paths(
            {
                "minimal botboy home": temp_dir / "botboy-home" / "minimal",
                "standard botboy home": temp_dir / "botboy-home" / "standard",
            }
        )
        bootstrap_site_packages = _site_packages_dir(bootstrap_python, base_dir=temp_dir)
        standard_source = _select_standard_site_packages_seed(bootstrap_site_packages=bootstrap_site_packages)

        _copy_site_packages_seed(bootstrap_site_packages, _site_packages_dir(minimal_python, base_dir=temp_dir))
        _pip_install(bootstrap_python, minimal_python, str(wheel_path), base_dir=temp_dir, no_deps=True)
        _run_botboy_command(minimal_python, temp_dir, "minimal", "version")
        _run_botboy_command(minimal_python, temp_dir, "minimal", "modules")
        _run_botboy_command(minimal_python, temp_dir, "minimal", "test")
        _run_botboy_command(minimal_python, temp_dir, "minimal", "evals", "--summary")
        _run_botboy_command(minimal_python, temp_dir, "minimal", "exec", "status")

        standard_spec = f"botboy[standard] @ {wheel_path.resolve().as_uri()}"
        source_for_standard = standard_source if standard_source.exists() else bootstrap_site_packages
        _copy_site_packages_seed(source_for_standard, _site_packages_dir(standard_python, base_dir=temp_dir))
        _pip_install(bootstrap_python, standard_python, standard_spec, base_dir=temp_dir, no_deps=True)
        _run_botboy_command(standard_python, temp_dir, "standard", "version")
        modules = _run_botboy_command(standard_python, temp_dir, "standard", "modules")
        lowered = modules.stdout.lower()
        for expected in ("fastapi", "uvicorn", "aiohttp", "websockets"):
            if expected not in lowered:
                raise RuntimeError(f"release-standard modules output missing {expected}: {modules.stdout}")
        _run(
            [
                str(standard_python),
                "-c",
                "import aiohttp, fastapi, uvicorn, websockets; print('standard-runtime-ok')",
            ],
            cwd=temp_dir,
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
        default=str((Path.cwd() / "dist").resolve()),
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
