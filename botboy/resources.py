"""Runtime resource locations bundled with BotBoy."""

from __future__ import annotations

import os
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parent


def install_root() -> Path:
    return package_root().parent


def runtime_home_dir() -> Path:
    configured = str(os.getenv("BOTBOY_HOME", "~/.botboy")).strip() or "~/.botboy"
    primary = Path(configured).expanduser()
    fallback = install_root() / ".botboy-runtime"
    for candidate in (primary, fallback):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    return fallback


def bundled_assets_dir() -> Path:
    for candidate in (package_root() / "bundled", package_root() / "bundled_assets"):
        if candidate.exists():
            return candidate
    return package_root() / "bundled"


def data_dir() -> Path:
    return package_root() / "data"


def status_snapshot_path() -> Path:
    return data_dir() / "STATUS_SNAPSHOT.json"


def skill_registry_path() -> Path:
    return data_dir() / "botboy_skill_registry.json"


def web_dir() -> Path:
    return package_root() / "web"


def skills_dir() -> Path:
    return install_root() / "skills"


def example_skills_dir() -> Path:
    return install_root() / "examples" / "skills"


def bundled_mcp_server_path() -> Path:
    return install_root() / "botboy_mcp_server.py"


def bundled_skills_dir() -> Path:
    for candidate in (bundled_assets_dir() / "skills", skills_dir()):
        if candidate.exists():
            return candidate
    return bundled_assets_dir() / "skills"


def bundled_example_skills_dir() -> Path:
    for candidate in (bundled_assets_dir() / "examples" / "skills", example_skills_dir()):
        if candidate.exists():
            return candidate
    return bundled_assets_dir() / "examples" / "skills"


def default_skills_dir() -> Path:
    for candidate in (bundled_skills_dir(), skills_dir(), bundled_example_skills_dir(), example_skills_dir()):
        if candidate.exists():
            return candidate
    return bundled_skills_dir()


def bundled_evals_dir() -> Path:
    return data_dir() / "evals"


def bundled_eval_replays_dir() -> Path:
    return bundled_evals_dir() / "replays"


def bundled_eval_manifest_path(name: str = "release_smoke") -> Path:
    token = str(name or "release_smoke").strip() or "release_smoke"
    if token.endswith(".json"):
        return bundled_evals_dir() / token
    return bundled_evals_dir() / f"{token}_manifest.json"


def bundled_eval_seed_path(name: str = "release_smoke") -> Path:
    token = str(name or "release_smoke").strip() or "release_smoke"
    if token.endswith(".jsonl"):
        return bundled_eval_replays_dir() / token
    return bundled_eval_replays_dir() / f"{token}_seed.jsonl"


def resolve_registry_skill_path(path: str) -> Path:
    raw = Path(str(path or "").strip())
    if raw.is_absolute():
        return raw

    normalized = raw.as_posix()
    if normalized.startswith("skills/"):
        relative = Path(*raw.parts[1:])
        return bundled_skills_dir() / relative
    if normalized.startswith("examples/skills/"):
        relative = Path(*raw.parts[2:])
        return bundled_example_skills_dir() / relative
    return install_root() / raw
