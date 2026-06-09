"""BotBoy Configuration System — YAML + env var overlay."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from botboy.resources import bundled_skills_dir, default_skills_dir, runtime_home_dir

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


def _default_botboy_home() -> str:
    return os.getenv("BOTBOY_HOME", "~/.botboy")


def _default_state_path(name: str) -> str:
    return str(Path(_default_botboy_home()) / name)


def _default_config_path() -> str:
    return _default_state_path("config.yaml")


@dataclass
class SecurityConfig:
    level: str = "BEGINNER"          # BEGINNER|INTERMEDIATE|ADVANCED|EXPERT
    enable_auth: bool = False
    jwt_secret: str = ""
    jwt_secret_file: str = field(default_factory=lambda: _default_state_path("jwt.secret"))
    principal_db_path: str = field(default_factory=lambda: _default_state_path("principals.db"))
    auth_bootstrap_user: str = "admin"
    auth_bootstrap_secret: str = ""
    auth_bootstrap_role: str = "admin"
    auth_api_keys_enabled: bool = True
    auth_api_key_store_path: str = field(default_factory=lambda: _default_state_path("api_keys.db"))
    cors_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 3600
    sandbox_runtime: str = "runc"


@dataclass
class MemoryConfig:
    engine: str = "simple"           # simple | hybrid
    db_path: str = field(default_factory=lambda: _default_state_path("botboy.db"))
    max_memories: int = 10_000


@dataclass
class CacheConfig:
    enabled: bool = True
    max_size: int = 500


@dataclass
class SkillsConfig:
    directory: str = field(default_factory=lambda: str(default_skills_dir()))
    auto_discover: bool = True
    validate_security: bool = True


@dataclass
class LLMConfig:
    enabled: bool = False
    backend: str = "mock"            # mock | ollama | openai | anthropic
    model: str = "llama3.2"
    api_url: str = "http://localhost:11434"
    api_key: str = ""
    timeout: int = 30
    reflection_enabled: bool = False
    planning_enabled: bool = False
    parallel_think_enabled: bool = False
    max_context_tokens: int = 4096


@dataclass
class PerformanceConfig:
    enable_monitoring: bool = True
    metrics_retention: int = 1_000


@dataclass
class SchedulerConfig:
    db_path: str = field(default_factory=lambda: _default_state_path("scheduler.db"))
    enabled: bool = True


@dataclass
class HistoryConfig:
    db_path: str = field(default_factory=lambda: _default_state_path("history.db"))
    enabled: bool = True
    purge_after_days: int = 90


@dataclass
class TraceConfig:
    enabled: bool = True
    db_path: str = field(default_factory=lambda: _default_state_path("traces.db"))


@dataclass
class TasksConfig:
    enabled: bool = True
    db_path: str = field(default_factory=lambda: _default_state_path("tasks.db"))
    artifact_root: str = field(default_factory=lambda: _default_state_path("artifacts/tasks"))
    retain_days: int = 90


@dataclass
class BotBoyConfig:
    version: str = "0.6.0-dev"
    debug: bool = False
    log_level: str = "INFO"

    security: SecurityConfig = field(default_factory=SecurityConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    tasks: TasksConfig = field(default_factory=TasksConfig)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "BotBoyConfig":
        """Load config from YAML file, overlaid with environment variables."""
        cfg = cls()
        config_path = path or os.getenv("BOTBOY_CONFIG", _default_config_path())
        config_path = str(Path(config_path).expanduser())

        if YAML_AVAILABLE and os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    data = yaml.safe_load(f) or {}
                cfg._apply_dict(data)
            except OSError as exc:
                raise RuntimeError(f"Failed to read BotBoy config: {config_path}") from exc
            except yaml.YAMLError as exc:  # type: ignore[attr-defined]
                raise RuntimeError(f"Invalid BotBoy config YAML: {config_path}") from exc

        cfg._apply_env()
        return cfg

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.lower() in ("true", "1", "yes", "on")

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def _apply_dict(self, data: dict) -> None:
        bb = data.get("botboy", {})
        self.version = bb.get("version", self.version)
        self.debug = bb.get("debug", self.debug)
        self.log_level = bb.get("log_level", self.log_level)

        sec = data.get("security", {})
        self.security.level = sec.get("level", self.security.level)
        self.security.enable_auth = sec.get("enable_auth", self.security.enable_auth)
        self.security.jwt_secret = sec.get("jwt_secret", self.security.jwt_secret)
        self.security.jwt_secret_file = sec.get("jwt_secret_file", self.security.jwt_secret_file)
        self.security.principal_db_path = sec.get("principal_db_path", self.security.principal_db_path)
        self.security.auth_bootstrap_user = sec.get("auth_bootstrap_user", self.security.auth_bootstrap_user)
        self.security.auth_bootstrap_secret = sec.get("auth_bootstrap_secret", self.security.auth_bootstrap_secret)
        self.security.auth_bootstrap_role = sec.get("auth_bootstrap_role", self.security.auth_bootstrap_role)
        self.security.auth_api_keys_enabled = sec.get(
            "auth_api_keys_enabled", self.security.auth_api_keys_enabled
        )
        self.security.auth_api_key_store_path = sec.get(
            "auth_api_key_store_path", self.security.auth_api_key_store_path
        )
        self.security.cors_origin_regex = sec.get("cors_origin_regex", self.security.cors_origin_regex)
        self.security.rate_limit_enabled = sec.get("rate_limit_enabled", self.security.rate_limit_enabled)
        self.security.rate_limit_requests = sec.get("rate_limit_requests", self.security.rate_limit_requests)
        self.security.rate_limit_window_seconds = sec.get(
            "rate_limit_window_seconds", self.security.rate_limit_window_seconds
        )

        mem = data.get("memory", {})
        self.memory.engine = mem.get("engine", self.memory.engine)
        self.memory.db_path = mem.get("db_path", self.memory.db_path)
        self.memory.max_memories = mem.get("max_memories", self.memory.max_memories)

        cache = data.get("cache", {})
        self.cache.enabled = cache.get("enabled", self.cache.enabled)
        self.cache.max_size = cache.get("max_size", self.cache.max_size)

        skills = data.get("skills", {})
        self.skills.directory = skills.get("directory", self.skills.directory)
        self.skills.auto_discover = skills.get("auto_discover", self.skills.auto_discover)
        self.skills.validate_security = skills.get("validate_security", self.skills.validate_security)

        llm = data.get("llm", {})
        self.llm.enabled = llm.get("enabled", self.llm.enabled)
        self.llm.backend = llm.get("backend", self.llm.backend)
        self.llm.model = llm.get("model", self.llm.model)
        self.llm.api_url = llm.get("api_url", self.llm.api_url)
        self.llm.api_key = llm.get("api_key", self.llm.api_key)
        self.llm.timeout = llm.get("timeout", self.llm.timeout)
        self.llm.reflection_enabled = llm.get("reflection_enabled", self.llm.reflection_enabled)
        self.llm.planning_enabled = llm.get("planning_enabled", self.llm.planning_enabled)
        self.llm.parallel_think_enabled = llm.get("parallel_think_enabled", self.llm.parallel_think_enabled)

        perf = data.get("performance", {})
        self.performance.enable_monitoring = perf.get("enable_monitoring", self.performance.enable_monitoring)
        self.performance.metrics_retention = perf.get("metrics_retention", self.performance.metrics_retention)

        sched = data.get("scheduler", {})
        self.scheduler.db_path = sched.get("db_path", self.scheduler.db_path)
        self.scheduler.enabled = sched.get("enabled", self.scheduler.enabled)

        hist = data.get("history", {})
        self.history.db_path = hist.get("db_path", self.history.db_path)
        self.history.enabled = hist.get("enabled", self.history.enabled)
        self.history.purge_after_days = hist.get("purge_after_days", self.history.purge_after_days)

        trace = data.get("trace", {})
        self.trace.enabled = trace.get("enabled", self.trace.enabled)
        self.trace.db_path = trace.get("db_path", self.trace.db_path)

        tasks = data.get("tasks", {})
        self.tasks.enabled = tasks.get("enabled", self.tasks.enabled)
        self.tasks.db_path = tasks.get("db_path", self.tasks.db_path)
        self.tasks.artifact_root = tasks.get("artifact_root", self.tasks.artifact_root)
        self.tasks.retain_days = tasks.get("retain_days", self.tasks.retain_days)

    def _apply_env(self) -> None:
        """Override with environment variables."""
        if v := os.getenv("BOTBOY_SKILLS_DIR"):
            self.skills.directory = v
        self.llm.enabled = self._env_bool("BOTBOY_LLM_ENABLED", self.llm.enabled)
        if v := os.getenv("BOTBOY_LLM_BACKEND"):
            self.llm.backend = v
        if v := os.getenv("BOTBOY_LLM_MODEL"):
            self.llm.model = v
        if v := os.getenv("OLLAMA_URL"):
            self.llm.api_url = v
        if v := os.getenv("OPENAI_API_KEY"):
            if self.llm.backend == "openai" or not self.llm.api_key:
                self.llm.api_key = v
        if v := os.getenv("ANTHROPIC_API_KEY"):
            if self.llm.backend == "anthropic" or not self.llm.api_key:
                self.llm.api_key = v
        if v := os.getenv("BOTBOY_JWT_SECRET"):
            self.security.jwt_secret = v
        if v := os.getenv("BOTBOY_JWT_SECRET_FILE"):
            self.security.jwt_secret_file = v
        if v := os.getenv("BOTBOY_PRINCIPAL_DB_PATH"):
            self.security.principal_db_path = v
        if v := os.getenv("BOTBOY_AUTH_BOOTSTRAP_USER"):
            self.security.auth_bootstrap_user = v
        if v := os.getenv("BOTBOY_AUTH_BOOTSTRAP_SECRET"):
            self.security.auth_bootstrap_secret = v
        if v := os.getenv("BOTBOY_AUTH_BOOTSTRAP_ROLE"):
            self.security.auth_bootstrap_role = v
        self.security.auth_api_keys_enabled = self._env_bool(
            "BOTBOY_AUTH_API_KEYS_ENABLED", self.security.auth_api_keys_enabled
        )
        if v := os.getenv("BOTBOY_AUTH_API_KEY_STORE_PATH"):
            self.security.auth_api_key_store_path = v
        if v := os.getenv("BOTBOY_CORS_ORIGIN_REGEX"):
            self.security.cors_origin_regex = v
        self.security.enable_auth = self._env_bool("BOTBOY_ENABLE_AUTH", self.security.enable_auth)
        self.security.rate_limit_enabled = self._env_bool(
            "BOTBOY_RATE_LIMIT_ENABLED", self.security.rate_limit_enabled
        )
        self.security.rate_limit_requests = self._env_int(
            "BOTBOY_RATE_LIMIT_REQUESTS", self.security.rate_limit_requests
        )
        self.security.rate_limit_window_seconds = self._env_int(
            "BOTBOY_RATE_LIMIT_WINDOW_SECONDS", self.security.rate_limit_window_seconds
        )
        self.trace.enabled = self._env_bool("BOTBOY_TRACE_ENABLED", self.trace.enabled)
        if v := os.getenv("BOTBOY_TRACE_DB_PATH"):
            self.trace.db_path = v
        self.tasks.enabled = self._env_bool("BOTBOY_TASKS_ENABLED", self.tasks.enabled)
        if v := os.getenv("BOTBOY_TASKS_DB_PATH"):
            self.tasks.db_path = v
        if v := os.getenv("BOTBOY_TASK_ARTIFACT_ROOT"):
            self.tasks.artifact_root = v
        self.tasks.retain_days = self._env_int("BOTBOY_TASK_RETAIN_DAYS", self.tasks.retain_days)
        self.debug = self._env_bool("BOTBOY_DEBUG", self.debug)

    @staticmethod
    def _resolve_path(value: str) -> str:
        """Expand user and env markers without failing on missing home dirs."""
        try:
            return str(Path(value).expanduser())
        except (OSError, RuntimeError):
            return os.path.expandvars(value)

    @staticmethod
    def _resolve_runtime_path(value: str) -> str:
        raw = str(value or "").strip()
        normalized = raw.replace("\\", "/")
        if normalized.startswith("~/.botboy"):
            suffix = normalized[len("~/.botboy"):].lstrip("/\\")
            base = runtime_home_dir()
            return str((base / suffix) if suffix else base)
        return BotBoyConfig._resolve_path(raw)

    def resolve_jwt_secret(self) -> str:
        """Return the configured JWT secret from config, env, or file."""
        if self.security.jwt_secret:
            return self.security.jwt_secret.strip()

        if v := os.getenv("BOTBOY_JWT_SECRET"):
            return v.strip()

        secret_file = Path(
            self._resolve_path(
                os.getenv("BOTBOY_JWT_SECRET_FILE", self.security.jwt_secret_file)
            )
        )
        if secret_file.exists():
            try:
                secret = secret_file.read_text(encoding="utf-8").strip()
                if secret:
                    return secret
            except OSError:
                pass

        return ""

    def has_jwt_secret(self) -> bool:
        """Check whether a usable JWT secret is configured."""
        return bool(self.resolve_jwt_secret())

    def resolve_bootstrap_secret(self) -> str:
        """Return the bootstrap login secret, if configured."""
        if self.security.auth_bootstrap_secret:
            return self.security.auth_bootstrap_secret.strip()
        if v := os.getenv("BOTBOY_AUTH_BOOTSTRAP_SECRET"):
            return v.strip()
        return ""

    def resolve_api_key_store_path(self) -> str:
        """Return the path used for API key / principal credential storage."""
        if v := os.getenv("BOTBOY_AUTH_API_KEY_STORE_PATH"):
            return self._resolve_runtime_path(v)
        return self._resolve_runtime_path(self.security.auth_api_key_store_path)

    def resolve_principal_db_path(self) -> str:
        """Return the path used for persistent interactive principal credentials."""
        if v := os.getenv("BOTBOY_PRINCIPAL_DB_PATH"):
            return self._resolve_runtime_path(v)
        return self._resolve_runtime_path(self.security.principal_db_path)

    def resolve_memory_db_path(self) -> str:
        """Return the path used for memory persistence."""
        return self._resolve_runtime_path(self.memory.db_path)

    def resolve_scheduler_db_path(self) -> str:
        """Return the path used for scheduler persistence."""
        return self._resolve_runtime_path(self.scheduler.db_path)

    def resolve_history_db_path(self) -> str:
        """Return the path used for history persistence."""
        return self._resolve_runtime_path(self.history.db_path)

    def resolve_trace_db_path(self) -> str:
        """Return the path used for trace persistence."""
        if v := os.getenv("BOTBOY_TRACE_DB_PATH"):
            return self._resolve_runtime_path(v)
        return self._resolve_runtime_path(self.trace.db_path)

    def resolve_task_db_path(self) -> str:
        """Return the path used for task persistence."""
        if v := os.getenv("BOTBOY_TASKS_DB_PATH"):
            return self._resolve_runtime_path(v)
        return self._resolve_runtime_path(self.tasks.db_path)

    def resolve_task_artifact_root(self) -> str:
        """Return the root directory used for task artifacts."""
        if v := os.getenv("BOTBOY_TASK_ARTIFACT_ROOT"):
            return self._resolve_runtime_path(v)
        return self._resolve_runtime_path(self.tasks.artifact_root)

    def resolve_skills_dir(self) -> str:
        """Return the skills directory used for skill discovery."""
        if v := os.getenv("BOTBOY_SKILLS_DIR"):
            return self._resolve_path(v)
        configured = str(self.skills.directory or "").strip()
        if configured.lower() in {"", "auto", "bundled"}:
            return str(bundled_skills_dir())

        resolved = self._resolve_path(configured)
        legacy_default = self._resolve_path("~/.botboy/skills")
        if resolved == legacy_default and not Path(resolved).exists():
            return str(bundled_skills_dir())
        return resolved

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "debug": self.debug,
            "log_level": self.log_level,
            "security": {
                "level": self.security.level,
                "enable_auth": self.security.enable_auth,
                "jwt_secret_file": self.security.jwt_secret_file,
                "principal_db_path": self.security.principal_db_path,
                "resolved_principal_db_path": self.resolve_principal_db_path(),
                "jwt_secret_configured": self.has_jwt_secret(),
                "auth_bootstrap_user": self.security.auth_bootstrap_user,
                "auth_bootstrap_secret_configured": bool(self.resolve_bootstrap_secret()),
                "auth_bootstrap_role": self.security.auth_bootstrap_role,
                "auth_api_keys_enabled": self.security.auth_api_keys_enabled,
                "auth_api_key_store_path": self.resolve_api_key_store_path(),
                "cors_origin_regex": self.security.cors_origin_regex,
                "rate_limit_enabled": self.security.rate_limit_enabled,
                "rate_limit_requests": self.security.rate_limit_requests,
                "rate_limit_window_seconds": self.security.rate_limit_window_seconds,
            },
            "memory": {
                "engine": self.memory.engine,
                "db_path": self.memory.db_path,
                "resolved_db_path": self.resolve_memory_db_path(),
            },
            "cache": {"enabled": self.cache.enabled, "max_size": self.cache.max_size},
            "skills": {
                "directory": self.skills.directory,
                "resolved_directory": self.resolve_skills_dir(),
                "auto_discover": self.skills.auto_discover,
                "validate_security": self.skills.validate_security,
            },
            "llm": {
                "enabled": self.llm.enabled,
                "backend": self.llm.backend,
                "model": self.llm.model,
                "reflection_enabled": self.llm.reflection_enabled,
                "planning_enabled": self.llm.planning_enabled,
            },
            "scheduler": {"enabled": self.scheduler.enabled, "resolved_db_path": self.resolve_scheduler_db_path()},
            "history": {"enabled": self.history.enabled, "resolved_db_path": self.resolve_history_db_path()},
            "trace": {
                "enabled": self.trace.enabled,
                "db_path": self.trace.db_path,
                "resolved_db_path": self.resolve_trace_db_path(),
            },
            "tasks": {
                "enabled": self.tasks.enabled,
                "db_path": self.tasks.db_path,
                "resolved_db_path": self.resolve_task_db_path(),
                "artifact_root": self.tasks.artifact_root,
                "resolved_artifact_root": self.resolve_task_artifact_root(),
                "retain_days": self.tasks.retain_days,
            },
        }
