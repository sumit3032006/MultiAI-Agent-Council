"""Configuration: .env handling, API key validation, limits and agent settings."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ENV_PATH = ROOT / ".env"
AGENTS_PATH = DATA_DIR / "agents.json"
DB_PATH = DATA_DIR / "maac.db"

PROVIDERS = ("openrouter", "anthropic")
KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
FREE_MODELS_PATH = DATA_DIR / "free_models.json"
_PREFIX = {"openrouter": "sk-or-", "anthropic": "sk-ant-"}
_PLACEHOLDER = re.compile(r"^(your[_\-]|paste|put[_\-]|<|changeme|replace)", re.I)
CLAUDE_MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]


def load_env() -> None:
    """Load the local .env file (if present). Keys already set in this session are not overwritten."""
    load_dotenv(ENV_PATH, override=False)


def provider_for_model(model: str) -> str:
    """Claude models (no '/' in the id) use the Anthropic API with the user's own key; everything else is OpenRouter."""
    m = (model or "").strip()
    return "anthropic" if m.startswith("claude-") and "/" not in m else "openrouter"


def key_env_names(provider: str, agent_id: str) -> tuple[str, str]:
    """(agent-specific variable, shared variable), e.g. OPENROUTER_API_KEY_CRITIC and OPENROUTER_API_KEY."""
    return f"{KEY_ENV[provider]}_{agent_id.upper()}", KEY_ENV[provider]


def validate_key(provider: str, value: Optional[str]) -> tuple[bool, str]:
    """Check that a key looks plausible. This is a format check only, not a live test."""
    v = (value or "").strip()
    if not v:
        return False, "empty"
    if _PLACEHOLDER.match(v) or "xxxx" in v.lower():
        return False, "still contains the placeholder text"
    if re.search(r"\s", v):
        return False, "contains spaces or line breaks"
    if len(v) < 20:
        return False, "too short to be a real key"
    prefix = _PREFIX.get(provider)
    if prefix and not v.startswith(prefix):
        return False, f"should start with '{prefix}'"
    return True, "ok"


def mask_key(value: Optional[str]) -> str:
    """Never show a full key: only the last 4 characters of long keys."""
    v = (value or "").strip()
    return "..." + v[-4:] if len(v) >= 12 else ("****" if v else "(not set)")


def key_source(agent_id: str, provider: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Name of the environment variable that holds this agent's usable key, or None."""
    env = os.environ if env is None else env
    for name in key_env_names(provider, agent_id):
        if (env.get(name) or "").strip():
            return name
    return None


def agent_key(agent_id: str, provider: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The agent's own key if set, else the shared key. Returns only keys that pass the format check."""
    env = os.environ if env is None else env
    name = key_source(agent_id, provider, env)
    if not name:
        return None
    value = env[name].strip()
    return value if validate_key(provider, value)[0] else None


def agent_key_state(agent_id: str, provider: str, env: Optional[Mapping[str, str]] = None) -> tuple[str, str]:
    """(state, message); state is 'configured', 'missing' or 'invalid'."""
    env = os.environ if env is None else env
    name = key_source(agent_id, provider, env)
    if not name:
        own, shared = key_env_names(provider, agent_id)
        return "missing", f"no key: set {own} or {shared} in .env, or enter one in Settings"
    ok, why = validate_key(provider, env[name])
    if not ok:
        return "invalid", f"{name} {why}"
    return "configured", f"using {name} (format looks valid, not yet tested)"


_ENV_NAME = re.compile(r"^(OPENROUTER|ANTHROPIC)_API_KEY(_(RESEARCHER|TECHNICAL|CRITIC|INNOVATOR|JUDGE))?$")


def write_env_values(updates: Mapping[str, str], path: Path = ENV_PATH) -> None:
    """Set KEY=value lines in the local .env, keeping every other line. Only API key variables are allowed."""
    for name, value in updates.items():
        if not _ENV_NAME.match(name):
            raise ValueError(f"not an allowed setting: {name}")
        if re.search(r"\s", value or ""):
            raise ValueError(f"{name}: value contains spaces or line breaks")
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Limits(BaseModel):
    max_rounds: int = Field(4, ge=1, le=10)
    max_requests: int = Field(40, ge=4, le=500)
    max_output_tokens: int = Field(60000, ge=1000, le=1000000)
    max_seconds: int = Field(600, ge=30, le=7200)
    per_call_tokens: int = Field(900, ge=100, le=4000)
    min_interval: float = Field(2.0, ge=0, le=60)
    concurrency: int = Field(3, ge=1, le=5)
    enable_votes: bool = True


def limits_from_env(env: Optional[Mapping[str, str]] = None) -> Limits:
    env = os.environ if env is None else env
    mapping = {
        "max_rounds": ("MAAC_MAX_ROUNDS", int),
        "max_requests": ("MAAC_MAX_REQUESTS", int),
        "max_output_tokens": ("MAAC_MAX_OUTPUT_TOKENS", int),
        "max_seconds": ("MAAC_MAX_SECONDS", int),
        "per_call_tokens": ("MAAC_PER_CALL_TOKENS", int),
        "min_interval": ("MAAC_MIN_INTERVAL_SECONDS", float),
    }
    values = {}
    for field, (name, cast) in mapping.items():
        raw = env.get(name, "").strip()
        if raw:
            try:
                values[field] = cast(raw)
            except ValueError:
                raise ValueError(f"{name} must be a number, got '{raw}'") from None
    return Limits(**values)


class AgentConfig(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=40)
    role: str = Field(min_length=1, max_length=60)
    instructions: str = Field(min_length=10, max_length=1500)
    provider: str
    model: str = Field(min_length=1, max_length=120)
    enabled: bool = True


_MODEL_RE = re.compile(r"^[A-Za-z0-9@._:/\-]+$")
AGENT_IDS = ("researcher", "technical", "critic", "innovator", "judge")

# Five DIFFERENT free OpenRouter models by default. The free lineup changes often: use Settings ->
# "Refresh free models" to load the current list from OpenRouter.
DEFAULT_AGENTS = [
    dict(id="researcher", name="Researcher", role="Researcher", provider="openrouter",
         model="meta-llama/llama-3.3-70b-instruct:free",
         instructions="Analyse the task, research it using your knowledge, propose initial solutions and share findings. Say clearly what you are unsure about."),
    dict(id="technical", name="Technical Expert", role="Technical Expert", provider="openrouter",
         model="openai/gpt-oss-20b:free",
         instructions="Evaluate technical feasibility, suggest architecture and implementation, review other agents' technical proposals and point out technical limits."),
    dict(id="critic", name="Critic", role="Critic", provider="openrouter",
         model="google/gemma-3-27b-it:free",
         instructions="Challenge assumptions, find mistakes and weaknesses, question unsupported claims and ask other agents for clarification."),
    dict(id="innovator", name="Innovator", role="Innovator", provider="openrouter",
         model="mistralai/mistral-small-3.2-24b-instruct:free",
         instructions="Suggest alternative approaches, improve existing proposals and find optimisation opportunities. You may challenge any agent."),
    dict(id="judge", name="Judge", role="Judge", provider="openrouter",
         model="z-ai/glm-4.5-air:free",
         instructions="Review all proposals and discussion, weigh evidence and reasoning, identify unresolved disagreements, decide if more discussion is useful and write the final answer."),
]


def validate_agent(cfg: AgentConfig) -> AgentConfig:
    if cfg.id not in AGENT_IDS:
        raise ValueError(f"unknown agent id '{cfg.id}'")
    if cfg.provider not in PROVIDERS:
        raise ValueError(f"{cfg.name}: provider must be one of {', '.join(PROVIDERS)}")
    if not _MODEL_RE.match(cfg.model):
        raise ValueError(f"{cfg.name}: model name contains invalid characters")
    return cfg


def default_agents() -> list[AgentConfig]:
    return [AgentConfig(**d) for d in DEFAULT_AGENTS]


def load_agents(path: Path = AGENTS_PATH) -> list[AgentConfig]:
    agents = {a.id: a for a in default_agents()}
    try:
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
        for d in stored:
            cfg = validate_agent(AgentConfig(**d))
            agents[cfg.id] = cfg
    except (OSError, ValueError, TypeError, KeyError):
        pass  # missing or corrupt file: fall back to defaults for what could not be read
    return [agents[i] for i in AGENT_IDS]


def save_agents(agents: list[AgentConfig], path: Path = AGENTS_PATH) -> None:
    for a in agents:
        validate_agent(a)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([a.model_dump() for a in agents], indent=2), encoding="utf-8")
