"""One config file (A2 §4 A-4).

Before this module every value lived twice: `compose/docker-compose.yml` carried a
literal (`CELL_SUBJECT: ${CELL_SUBJECT:-market.spot.NIFTY}`), the gates carried their
own copy, and `bootstrap/install.sh` carried a third. A default in two places is a
disagreement waiting to happen — the observed one was a gate that fed `NIFTY` while a
service subscribed to something else and nobody noticed for a day.

Now `configs/gfti.toml` is the only source for everything that is **not** a secret.
`compose/.env` holds secrets only, and its key set is declared in the TOML so a stray
non-secret can be rejected instead of silently becoming a fourth source.

Consumers:

  * `compose/docker-compose.yml` interpolates `${X:?}` — no `:-` fallbacks, because a
    fallback *is* a second source of truth.
  * `gfti` renders the same mapping into the environment of every `docker compose`
    call, so `gfti up` and `install.sh` cannot disagree about what they started.
  * the gates read it through `compose_env()` instead of hardcoding ports and URLs.

Python >= 3.11 because `tomllib` is stdlib there; a TOML parser we wrote ourselves
would be one more thing to get wrong.
"""

from __future__ import annotations

import os
import re
import shlex
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = REPO / "configs" / "gfti.toml"
ENV_FILE = REPO / "compose" / ".env"

TIERS = ("auto", "A", "B", "C")

# One definition of "this key looks like a credential". `cli.py` redacts with it and
# `config_gates.py` asserts nothing slipped past it, so it must not be duplicated.
SECRET_KEY_RE = re.compile(
    r"(SECRET|PASSWORD|PASSWD|TOKEN|_KEY|PRIVATE)", re.IGNORECASE
)

# section.key -> (type, description). Sparse on purpose: a key with no consumer is
# a value nobody can verify, so the file only carries values something reads.
SCHEMA = {
    "version": (int, "config schema version"),
    "host.tier": (str, "auto | A (Linux) | B (macOS) | C (Windows WSL2)"),
    "stores.postgres_port": (int, "host port for postgres"),
    "stores.postgres_db": (str, "database name"),
    "stores.postgres_user": (str, "application role"),
    "stores.questdb_http_port": (int, "QuestDB REST/ILP-HTTP host port"),
    "stores.questdb_ilp_port": (int, "QuestDB ILP TCP host port"),
    "stores.nats_port": (int, "NATS client host port"),
    "stores.nats_monitor_port": (int, "NATS monitoring host port"),
    "stores.nats_url": (str, "NATS URL as seen from inside the compose network"),
    "stores.nats_url_host": (str, "NATS URL as seen from the host"),
    "stores.questdb_ilp_url": (str, "QuestDB ILP-TCP address inside compose"),
    "subjects.market": (str, "market data subject prefix"),
    "subjects.intent": (str, "order intent prefix (cell publishes, e7 subscribes)"),
    "subjects.signal": (str, "E2 signal prefix (cell publishes)"),
    "subjects.risk": (str, "E5 verdict prefix (cell publishes)"),
    "subjects.ledger": (str, "signed fill prefix (e7 publishes)"),
    "subjects.seat": (str, "seat lifecycle prefix (cell publishes)"),
    "subjects.kill_refresh_ms": (int, "how often a cell re-reads the kill ladder"),
    "runtime.railtick_ms": (int, "rail idle poll interval"),
    "runtime.heartbeat_ms": (int, "heartbeat period for all three services"),
    "runtime.cell.ticks": (int, "ticks before exit; 0 = run forever"),
    "runtime.cell.symbol": (str, "symbol the cell trades"),
    "runtime.cell.decide_every": (int, "ticks between decisions"),
    "runtime.cell.max_qty": (int, "hard cap per order"),
    "runtime.e7.expect": (int, "fills before exit; 0 = run forever"),
    "runtime.e7.refuse_exit": (int, "exit code when the ledger refuses a fill"),
    "log.max_size": (str, "docker json-file max-size"),
    "log.max_file": (int, "docker json-file max-file"),
}

# Secrets are declared in the TOML, never stored there: the file names them and
# `compose/.env` carries them. A key in `.env` that is not declared here is an
# error, so a non-secret cannot quietly become a fourth source of truth.
SECRETS_FALLBACK = {
    "required": ("POSTGRES_PASSWORD",),
    "optional": ("LEDGER_SECRET_OWNER",),
}

# The synthetic/pilot symbol used by the resident cell. Gates must never reuse it:
# a gate that shares a symbol with a live service gets its traffic consumed twice.
DEFAULT_SYMBOL = "NIFTY"


class ConfigError(RuntimeError):
    """A config problem the user can fix; never a traceback."""


def path_from_env() -> Path:
    """`GFTI_CONFIG` exists so a gate can pivot one value without touching the repo."""
    return Path(os.environ.get("GFTI_CONFIG") or DEFAULT_PATH)


def _lookup(cfg: dict, dotted: str):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ConfigError(f"configs/gfti.toml: missing [{part}] under {dotted!r}")
        cur = cur[part]
    return cur


def load(path: Path | None = None) -> dict:
    p = Path(path or path_from_env())
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise ConfigError(f"config not readable: {p} ({e.strerror})") from None
    try:
        cfg = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p}: {e}") from None
    validate(cfg, p)
    return cfg


def validate(cfg: dict, path: Path = DEFAULT_PATH) -> None:
    where = path.name
    if cfg.get("version") != 1:
        raise ConfigError(f"{where}: version must be 1 (found {cfg.get('version')!r})")
    for key, (typ, _why) in SCHEMA.items():
        if key == "version":
            continue
        try:
            val = _lookup(cfg, key)
        except ConfigError:
            raise ConfigError(f"{where}: missing required key {key!r}") from None
        if not isinstance(val, typ) or isinstance(val, bool):
            raise ConfigError(
                f"{where}: {key} must be {typ.__name__}, got {type(val).__name__}"
            )
    tier = _lookup(cfg, "host.tier")
    if tier not in TIERS:
        raise ConfigError(f"{where}: host.tier must be one of {TIERS}, got {tier!r}")
    for dotted in ("stores.nats_url", "stores.nats_url_host"):
        if not _lookup(cfg, dotted).startswith("nats://"):
            raise ConfigError(f"{where}: {dotted} must start with nats://")
    if not 0 <= _lookup(cfg, "runtime.heartbeat_ms") <= 60_000:
        raise ConfigError(f"{where}: runtime.heartbeat_ms must be 0..60000")
    required, optional = declared_secrets(cfg)
    if not required:
        raise ConfigError(f"{where}: [secrets].required must name at least one secret")
    overlap = set(required) & set(optional)
    if overlap:
        raise ConfigError(
            f"{where}: {sorted(overlap)} cannot be both required and optional"
        )


def secret_problems(cfg: dict, env_path: Path | None = None) -> list:
    """What `gfti doctor` reports about compose/.env. Structure load never fails on
    a missing .env: doctor's job is to say so, and `gfti --help` must work anyway."""
    required, optional = declared_secrets(cfg)
    present = secrets(env_path)
    problems = []
    for key in required:
        if not present.get(key):
            problems.append(f"compose/.env is missing required secret {key}")
    for key in present:
        if key not in required and key not in optional:
            problems.append(
                f"compose/.env has undeclared key {key} (declare it in [secrets])"
            )
    return problems


def declared_secrets(cfg: dict) -> tuple:
    """(required, optional) from [secrets]. An explicit empty list stays empty.

    `table.get(k) or fallback` would turn `optional = []` back into the fallback
    list, which is the opposite of what a migration to "no optional secrets" means.
    """
    table = cfg.get("secrets") or SECRETS_FALLBACK
    required = tuple(table.get("required", SECRETS_FALLBACK["required"]))
    optional = tuple(table.get("optional", SECRETS_FALLBACK["optional"]))
    return required, optional


def value(cfg: dict, key: str):
    return _lookup(cfg, key)


def compose_env(cfg: dict) -> dict:
    """The mapping compose interpolates. One key, one consumer, no defaults.

    `subjects.intent` deliberately feeds **two** services (`CELL_INTENT_PREFIX` and
    `E7_SUBJECT`): that is A2 item 15's "changing one value changes behaviour of
    ≥2 services", and it is also the invariant that keeps a publisher and its
    subscriber on the same namespace.
    """
    intent = value(cfg, "subjects.intent")
    market = value(cfg, "subjects.market")
    signal = value(cfg, "subjects.signal")
    risk = value(cfg, "subjects.risk")
    ledger = value(cfg, "subjects.ledger")
    seat = value(cfg, "subjects.seat")
    symbol = value(cfg, "runtime.cell.symbol")
    return {
        "POSTGRES_PORT": str(value(cfg, "stores.postgres_port")),
        "POSTGRES_DB": value(cfg, "stores.postgres_db"),
        "POSTGRES_USER": value(cfg, "stores.postgres_user"),
        "QUESTDB_HTTP_PORT": str(value(cfg, "stores.questdb_http_port")),
        "QUESTDB_ILP_PORT": str(value(cfg, "stores.questdb_ilp_port")),
        "NATS_PORT": str(value(cfg, "stores.nats_port")),
        "NATS_MONITOR_PORT": str(value(cfg, "stores.nats_monitor_port")),
        "NATS_URL": value(cfg, "stores.nats_url"),
        "NATS_URL_HOST": value(cfg, "stores.nats_url_host"),
        "RAIL_D1_URL": value(cfg, "stores.questdb_ilp_url"),
        "MARKET_PREFIX": market,
        "INTENT_PREFIX": intent,
        "CELL_SUBJECT": f"{market}.{symbol}",
        "CELL_INTENT_PREFIX": intent,
        "E7_SUBJECT": f"{intent}.>",
        "CELL_SIGNAL_PREFIX": signal,
        "CELL_RISK_PREFIX": risk,
        "CELL_SEAT_PREFIX": seat,
        "E7_LEDGER_PREFIX": ledger,
        "HEARTBEAT_MS": str(value(cfg, "runtime.heartbeat_ms")),
        "RAIL_TICK_MS": str(value(cfg, "runtime.railtick_ms")),
        "CELL_TICKS": str(value(cfg, "runtime.cell.ticks")),
        "CELL_DECIDE_EVERY": str(value(cfg, "runtime.cell.decide_every")),
        "CELL_MAX_QTY": str(value(cfg, "runtime.cell.max_qty")),
        "KILL_REFRESH_MS": str(value(cfg, "subjects.kill_refresh_ms")),
        "E7_EXPECT": str(value(cfg, "runtime.e7.expect")),
        "E7_REFUSE_EXIT": str(value(cfg, "runtime.e7.refuse_exit")),
        "LOG_MAX_SIZE": value(cfg, "log.max_size"),
        "LOG_MAX_FILE": str(value(cfg, "log.max_file")),
    }


def secrets(path: Path | None = None) -> dict:
    """Read compose/.env — the only file that may carry a credential.

    `path` is for tests and gates: they must be able to prove the *rule* (an
    undeclared key is rejected) without editing the developer's real .env.
    """
    out = {}
    try:
        for line in Path(path or ENV_FILE).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        return {}
    return out


def process_env(cfg: dict, env_path: Path | None = None) -> dict:
    """The full environment a `docker compose` call needs: secrets + config."""
    return {**os.environ, **compose_env(cfg), **secrets(env_path)}


def psql_argv(cfg: dict | None = None) -> list:
    """The `docker compose exec ... psql` argv prefix, from the config file.

    Five places needed this and each carried its own `-U gfti_app -d gfti`. That is
    the same duplication the config file exists to delete, so the coordinates live
    here once and every caller asks.
    """
    cfg = cfg or load()
    return [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        value(cfg, "stores.postgres_user"),
        "-d",
        value(cfg, "stores.postgres_db"),
    ]


def sh_export(env: dict) -> str:
    """POSIX `export` lines, for the bash installer to `eval`.

    Values are single-quoted so a symbol or URL with a shell metacharacter cannot
    change the meaning of the line.
    """
    return "".join(f"export {k}={shlex.quote(v)}\n" for k, v in sorted(env.items()))


def load_or_raise() -> dict:
    try:
        return load()
    except ConfigError as e:
        raise SystemExit(f"gfti: {e}") from None
