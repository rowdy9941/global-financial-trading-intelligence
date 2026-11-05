#!/usr/bin/env python3
"""One-config-file gates (A2 §4 A-4, items 13–15).

The claim being tested is narrow and checkable: **one file decides, and at least two
services obey it.** A config file that nothing reads passes every "does it parse"
test, so these cases assert behaviour, not shape.

  CONFIG   `gfti config --json` is machine-readable, names its source, and renders a
           complete compose environment from `configs/gfti.toml` alone.
  RENDER   `docker compose config -q` succeeds WITH the rendered environment and
           FAILS without it — the second half is the real proof that no `:-`
           fallback survived, because a fallback would have made it succeed too.
  PIVOT    change ONE value (`subjects.intent`) in a copy of the file via
           `GFTI_CONFIG`, and ≥2 services change: CELL_INTENT_PREFIX (publisher)
           and E7_SUBJECT (subscriber). This is A2 item 15 stated as an assertion.
  PARITY   `gfti config --sh` evaluated by a POSIX shell reproduces the same
           mapping `gfti config --json` reports, so `install.sh` and `gfti up` hand
           compose identical values.
  SECRETS  the secrets file is closed: an undeclared key is rejected, a missing
           required one is rejected, and declared keys pass. Tested against temp
           files, never the developer's .env.
  DOCTOR   `gfti doctor --json` reports the config source and redacts secrets, so
           the human-facing view cannot leak the credential the JSON keeps.

No Docker daemon is required for CONFIG/PIVOT/PARITY/SECRETS/DOCTOR; RENDER is
skipped (not failed) when the daemon is down.

Usage: python3 python/gates/config_gates.py [CONFIG|RENDER|PIVOT|PARITY|SECRETS|DOCTOR|ALL]

Exit codes: 0 = every selected case passed · 1 = at least one failed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SHIM = REPO / "bootstrap" / "gfti"
CLI = REPO / "python" / "gfti" / "cli.py"
CONFIG = REPO / "configs" / "gfti.toml"
COMPOSE = REPO / "compose" / "docker-compose.yml"
POSIX = os.name == "posix"

sys.path.insert(0, str(REPO / "python" / "gfti"))
import gfti_config as gcfg  # after the path insert, by necessity

# Every key compose may interpolate. A new `${X:?}` in the YAML that nobody added
# here is a key the gate would not notice going missing, so the RENDER case also
# reads the YAML and fails on an unknown name.
REQUIRED_KEYS = tuple(gcfg.compose_env(gcfg.load()))


def gfti(*args: str, env: dict | None = None) -> tuple[int, str]:
    argv = (["sh", str(SHIM)] if POSIX else [sys.executable, str(CLI)]) + list(args)
    p = subprocess.run(
        argv,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def parse_json(out: str):
    start = out.find("{")
    if start < 0:
        return None
    try:
        return json.loads(out[start:])
    except json.JSONDecodeError:
        return None


def case_config() -> tuple[bool, str]:
    rc, out = gfti("config", "--json")
    doc = parse_json(out)
    if rc != 0 or not isinstance(doc, dict):
        return False, f"rc={rc} unparseable: {out.strip()[:140]!r}"
    env = doc.get("compose_env") or {}
    missing = [k for k in REQUIRED_KEYS if k not in env]
    source_ok = Path(doc.get("source", "")).name == CONFIG.name
    odd = [k for k in ("POSTGRES_PASSWORD",) if k in env]
    ok = bool(env) and not missing and source_ok and not odd and doc.get("ok") is True
    detail = (
        f"rc={rc} keys={len(env)} source={source_ok} missing={missing[:4]} leaked={odd}"
    )
    return ok, detail


def _compose_config(env: dict) -> tuple[int, str]:
    p = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "config", "-q"],
        cwd=str(REPO / "compose"),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def case_render() -> tuple[bool, str]:
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if probe.returncode != 0:
        return True, "skipped (docker daemon unreachable; no fallbacks can be proven)"

    good = gcfg.process_env(gcfg.load())
    rc_ok, out_ok = _compose_config(good)

    # The load-bearing half: strip every config key and confirm compose REFUSES.
    # If this succeeds, some `${X:-default}` survived and the file is not the source.
    bare = {k: v for k, v in os.environ.items() if k not in REQUIRED_KEYS}
    rc_bare, out_bare = _compose_config(bare)

    # A `${VAR:?}` in the YAML that neither the loader renders nor [secrets]
    # declares would only show up at `up` time; catch it here instead. Comments are
    # stripped first so the prose that *documents* `${VAR:?}` is not read as a use.
    yaml_text = "\n".join(
        line.split("#", 1)[0]
        for line in COMPOSE.read_text(encoding="utf-8").splitlines()
    )
    referenced = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", yaml_text))
    required, optional = gcfg.declared_secrets(gcfg.load())
    declared = set(REQUIRED_KEYS) | set(required) | set(optional)
    undeclared = sorted(referenced - declared)

    # The load-bearing assertion. `rc_bare != 0` only proves that SOME variable is
    # required: with nine of them, re-adding one `:-` fallback still leaves the bare
    # render failing and the gate would stay green — measured, and exactly why the
    # shape is checked directly. A `:-` fallback or a bare `${NAME}` IS a second
    # source of truth for that value, so both are failures in this file.
    fallbacks = sorted(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-", yaml_text)))
    bare = sorted(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", yaml_text)))

    ok = rc_ok == 0 and rc_bare != 0 and not undeclared and not fallbacks and not bare
    detail = (
        f"with_config=rc{rc_ok} without_config=rc{rc_bare} "
        f"undeclared_in_yaml={undeclared[:5]} fallbacks={fallbacks[:5]} "
        f"bare_refs={bare[:5]}"
    )
    if not ok:
        detail += (
            f" · ok_out={out_ok.strip()[:80]!r} bare_out={out_bare.strip()[:80]!r}"
        )
    return ok, detail


def case_pivot() -> tuple[bool, str]:
    base = gcfg.compose_env(gcfg.load())
    src = CONFIG.read_text(encoding="utf-8")
    pivot = "order.pivotgate"
    if 'intent = "order.spot"' not in src:
        return False, 'configs/gfti.toml no longer carries intent = "order.spot"'
    patched = src.replace('intent = "order.spot"', f'intent = "{pivot}"', 1)

    tmpdir = Path(tempfile.mkdtemp(prefix="gfti-pivot-"))
    try:
        alt = tmpdir / "gfti.toml"
        alt.write_text(patched, encoding="utf-8")
        rc, out = gfti("config", "--json", env=dict(os.environ, GFTI_CONFIG=str(alt)))
        doc = parse_json(out)
        if rc != 0 or not isinstance(doc, dict):
            return False, f"rc={rc} unparseable: {out.strip()[:140]!r}"
        after = doc.get("compose_env") or {}

        changed = sorted(k for k in base if after.get(k) != base[k])
        # One value in the file, at least two services in the rendered env.
        services = {"CELL_INTENT_PREFIX", "E7_SUBJECT"} & set(changed)
        consistent = (
            after.get("CELL_INTENT_PREFIX") == pivot
            and after.get("E7_SUBJECT") == f"{pivot}.>"
        )
        unchanged = [
            k for k in base if k not in ("INTENT_PREFIX",) and k not in changed
        ]
        ok = (
            services == {"CELL_INTENT_PREFIX", "E7_SUBJECT"}
            and consistent
            and len(unchanged) == len(base) - len(changed)
            and doc.get("ok") is True
        )
        # The same rule for the publish namespace: `[subjects].market` must reach the
        # publisher (MARKET_PREFIX, read by rail) and the subscriber (CELL_SUBJECT,
        # read by cell). Two services, one value.
        marker = 'market = "market.spot"'
        market = "market.spot.pivotgate"
        if marker not in src:
            return False, f"configs/gfti.toml no longer carries {marker}"
        alt.write_text(src.replace(marker, f'market = "{market}"', 1), encoding="utf-8")
        rc2, out2 = gfti("config", "--json", env=dict(os.environ, GFTI_CONFIG=str(alt)))
        doc2 = parse_json(out2)
        if rc2 != 0 or not isinstance(doc2, dict):
            return False, f"market pivot rc={rc2} unparseable: {out2.strip()[:140]!r}"
        after2 = doc2.get("compose_env") or {}
        market_ok = (
            after2.get("MARKET_PREFIX") == market
            and after2.get("CELL_SUBJECT") == f"{market}.NIFTY"
        )

        detail = (
            f"changed={len(changed)} services={sorted(services)} "
            f"consistent={consistent} market_pivot={market_ok} "
            f"leaked={[k for k in changed if k not in ('INTENT_PREFIX', 'CELL_INTENT_PREFIX', 'E7_SUBJECT')]}"
        )
        return ok and market_ok, detail
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def case_parity() -> tuple[bool, str]:
    if not POSIX:
        return True, "skipped (POSIX sh not available; install.sh is tier A/B only)"
    rc, rendered = gfti("config", "--json")
    doc = parse_json(rendered)
    if rc != 0 or not isinstance(doc, dict):
        return False, f"rc={rc} could not read the JSON side"
    expected = doc.get("compose_env") or {}

    sh_rc, sh_out = gfti("config", "--sh")
    if sh_rc != 0:
        return False, f"--sh rc={sh_rc}: {sh_out.strip()[:140]!r}"

    # Evaluate the export lines the way install.sh does, then read the result back.
    probe = f"set -eu\n{sh_out.strip()}\nenv"
    p = subprocess.run(["sh", "-c", probe], capture_output=True, text=True, check=False)
    if p.returncode != 0:
        return False, f"eval failed rc={p.returncode}: {p.stderr.strip()[:140]!r}"
    got = {}
    for line in p.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            got[k] = v

    mismatch = {k: (v, got.get(k)) for k, v in expected.items() if got.get(k) != v}
    # install.sh must also see the secret, or the stack cannot start.
    secret_seen = bool(got.get("POSTGRES_PASSWORD"))
    ok = not mismatch and secret_seen
    detail = f"keys={len(expected)} mismatch={list(mismatch)[:4]} secret_exported={secret_seen}"
    return ok, detail


def case_secrets() -> tuple[bool, str]:
    cfg = gcfg.load()
    tmpdir = Path(tempfile.mkdtemp(prefix="gfti-secrets-"))
    try:
        good = tmpdir / "good.env"
        good.write_text("POSTGRES_PASSWORD=pw\nLEDGER_SECRET_OWNER=owner\n")

        undeclared = tmpdir / "undeclared.env"
        undeclared.write_text("POSTGRES_PASSWORD=pw\nCELL_SUBJECT=market.spot.NIFTY\n")

        missing = tmpdir / "missing.env"
        missing.write_text("LEDGER_SECRET_OWNER=owner\n")

        p_good = gcfg.secret_problems(cfg, good)
        p_undeclared = gcfg.secret_problems(cfg, undeclared)
        p_missing = gcfg.secret_problems(cfg, missing)

        ok = (
            not p_good
            and any("undeclared" in p for p in p_undeclared)
            and any("missing required" in p for p in p_missing)
        )
        detail = (
            f"good={len(p_good)} undeclared={len(p_undeclared)} "
            f"missing={len(p_missing)}"
        )
        return ok, detail
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def case_doctor() -> tuple[bool, str]:
    rc, out = gfti("doctor", "--json")
    doc = parse_json(out)
    if not isinstance(doc, dict) or "checks" not in doc:
        return False, f"rc={rc} unparseable: {out.strip()[:140]!r}"
    src = Path(doc.get("config_source") or "")
    config_ok = src.name == CONFIG.name and bool(doc.get("config"))
    secrets = doc.get("secrets") or {}
    password = gcfg.secrets().get("POSTGRES_PASSWORD")
    leaked = [
        k
        for k, v in secrets.items()
        if gcfg.SECRET_KEY_RE.search(k) and password and v == password
    ]
    # A config row must exist as its own check: doctor's job is to say the file is
    # the source, not to imply it by printing values.
    row = next((c for c in doc["checks"] if c.get("name") == "config_file"), None)
    row_ok = bool(row) and row.get("status") == "ok"
    ok = config_ok and not leaked and row_ok
    detail = (
        f"rc={rc} source={src.name} config_keys={len(doc.get('config') or {})} "
        f"config_row={row.get('status') if row else None} leaked={leaked}"
    )
    return ok, detail


CASES = {
    "CONFIG": case_config,
    "RENDER": case_render,
    "PIVOT": case_pivot,
    "PARITY": case_parity,
    "SECRETS": case_secrets,
    "DOCTOR": case_doctor,
}


def main() -> None:
    which = sys.argv[1].upper() if len(sys.argv) > 1 else "ALL"
    selected = list(CASES) if which == "ALL" else [which]
    unknown = [c for c in selected if c not in CASES]
    if unknown:
        print(f"CONFIG-GATES unknown case(s) {unknown}; choose from {list(CASES)}")
        sys.exit(2)

    summary, bad = {}, []
    for name in selected:
        ok, detail = CASES[name]()
        summary[name] = ok
        print(f"CONFIG-{name} {'ok' if ok else 'FAIL'} — {detail}", flush=True)
        if not ok:
            bad.append(name)
    summary["ok"] = not bad
    print("SUMMARY " + json.dumps(summary, sort_keys=True))
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
