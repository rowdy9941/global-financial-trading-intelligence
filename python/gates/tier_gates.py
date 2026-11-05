#!/usr/bin/env python3
"""Cross-OS install gates (A2 §4 A-6, items 18–22).

"Any OS" is a testable claim only if the installer, the shims and the tier probe
are one definition each. The defects this gate exists to catch are the quiet kind:

  PROBE  the host tier comes from `cli.detect_tier()` and from nowhere else. The
         installer used to carry its own `uname` table — two definitions of the
         same fact, the same class of defect as two config files. Now
         `bootstrap/install.sh` asks `gfti tier`, and a `--tier=` assertion that
         disagrees with the detected tier is refused with exit 2.
  POSIX  `bootstrap/install.sh` runs on a host with no bash. Proven twice: a parse
         by `dash -n` where dash exists, and a real execution with a PATH that
         contains only the utilities the script is allowed to need — no bash, no
         docker, no cargo, no uv. That run must fail with exit 2 and name every
         missing prerequisite in one message, because dying on the first one costs
         the operator a second round trip.
  PS1    `bootstrap/gfti.ps1` is the Windows twin of the sh shim: same tier, same
         configuration. Parity is asserted against `gfti config --json` rather than
         against a copy of the values. Skipped with a reason where pwsh is absent —
         a silent pass would be the same lie as a silent skip.
  CIOS   `.github/workflows/ci.yml` declares the three OS families, and the macOS /
         Windows jobs reach the same gate file this is part of, so "green on ≥2 OS
         families" is a fact about the run rather than about a comment.
  ENCODING  every non-Docker verb survives cp1252 and ascii. The CLI's help text
         contains `→`/`·`/`—`; on windows-latest that aborted `gfti --help` with
         UnicodeEncodeError. Reproduced here, on tier A, with PYTHONIOENCODING.

Usage: python3 python/gates/tier_gates.py [PROBE|POSIX|PS1|CIOS|ENCODING|ALL]

Exit codes: 0 = every selected case passed (skips do not fail) · 1 = at least one failed.
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
CLI = REPO / "python" / "gfti" / "cli.py"
SHIM = REPO / "bootstrap" / "gfti"
PS1 = REPO / "bootstrap" / "gfti.ps1"
INSTALL = REPO / "bootstrap" / "install.sh"
CI = REPO / ".github" / "workflows" / "ci.yml"

# Tokens that only bash understands. If one appears on a non-comment line, the
# installer is not POSIX and the "no bash" claim is false on the host that matters
# most: a machine with nothing installed.
BASHISMS = (
    "[[",
    "]]",
    "BASH_SOURCE",
    "pipefail",
    "function ",
    "local ",
    "<<<",
    "${!",
    "$'",
    "declare ",
    "source ",
    "echo -e",
    "+=(",
    "pushd",
    "=~",
)

# The utilities install.sh is allowed to need besides the shell itself. Everything
# else (docker, cargo, uv, python) is a *reported* prerequisite, not a dependency
# of reaching the report.
ALLOWED_TOOLS = (
    "python3",
    "dirname",
    "readlink",
    "sed",
    "head",
    "mkdir",
    "chmod",
    "ln",
    "cat",
)


def run(
    argv: list[str], timeout: int = 120, env: dict | None = None, text: bool = True
):
    return subprocess.run(
        argv,
        cwd=str(REPO),
        capture_output=True,
        text=text,
        check=False,
        timeout=timeout,
        env=env,
    )


def out_of(p) -> str:
    out = p.stdout or ""
    err = p.stderr or ""
    if isinstance(out, bytes):
        out, err = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    return out + err


def parse_json(text: str):
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, str(CLI), *args])


def case_probe():
    doc = parse_json(cli("tier", "--json").stdout or "")
    if not isinstance(doc, dict):
        return False, "gfti tier --json unparseable"
    tier, detail = doc.get("tier"), str(doc.get("detail") or "")
    if tier not in ("A", "B", "C"):
        return False, f"tier={tier!r} on a supported host"
    if not detail:
        return False, "tier detail is empty — the refusal message would be nameless"

    # The same answer through the front door, and through doctor. Three surfaces,
    # one fact.
    shim = run(["sh", str(SHIM), "tier"]) if os.name == "posix" else None
    if shim is not None and (
        shim.returncode != 0 or f"tier {tier}" not in out_of(shim)
    ):
        return (
            False,
            f"shim disagrees: rc={shim.returncode} {out_of(shim).strip()[:80]!r}",
        )
    dr = parse_json(cli("doctor", "--json").stdout or "")
    checks = (dr or {}).get("checks") or []
    tier_row = next((c for c in checks if c.get("name") == "tier"), None)
    if tier_row is None:
        return False, "doctor has no tier row"
    if tier_row.get("status") != "ok" or tier not in str(tier_row.get("detail")):
        return False, f"doctor tier row {tier_row} disagrees with {tier}"

    # An assertion that contradicts the host is refused, not ignored. Pick a tier
    # this host is not, so the case is meaningful on every machine.
    other = "B" if tier != "B" else "A"
    if os.name != "posix":
        # Windows has no `sh`, so tier C is refused by the CLI with the WSL2 route
        # rather than by install.sh. Same claim — refuse loudly, exit 2.
        bad = cli("install")
        text = out_of(bad)
        if bad.returncode != 2 or "WSL2" not in text:
            return (
                False,
                f"native-Windows install not refused: rc={bad.returncode} {text.strip()[:120]!r}",
            )
        return (
            True,
            f"tier={tier} shim/doctor agree, native install refused rc=2 (WSL2)",
        )

    bad = run(["sh", str(INSTALL), f"--tier={other}"])
    text = out_of(bad)
    if bad.returncode != 2 or "refusing" not in text:
        return (
            False,
            f"--tier={other} not refused: rc={bad.returncode} {text.strip()[-120:]!r}",
        )

    return True, f"tier={tier} shim/doctor agree, --tier={other} refused rc=2"


def case_posix():
    raw = INSTALL.read_bytes().decode("utf-8", "replace")
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "#!/bin/sh":
        return False, f"shebang is {lines[0].strip()!r}, not '#!/bin/sh'"

    found = []
    for n, line in enumerate(lines[1:], start=2):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  # a comment may name bash without using it
        code = line.split(" #", 1)[0]
        # `[[:space:]]` is a POSIX bracket expression, not the bash `[[` keyword.
        # Strip the character classes before looking for bash-only tokens.
        code = re.sub(r"\[\[:[a-z]+:\]\]", "", code)
        for token in BASHISMS:
            if token in code:
                found.append(f"{n}:{token}")
    if found:
        return False, f"bash-only tokens at {found[:5]}"

    parsed = run(["sh", "-n", str(INSTALL)])
    if parsed.returncode != 0:
        return False, f"sh -n failed: {out_of(parsed).strip()[:120]}"
    dialects = ["sh"]
    if shutil.which("dash"):
        dash = run(["dash", "-n", str(INSTALL)])
        if dash.returncode != 0:
            return False, f"dash -n failed: {out_of(dash).strip()[:120]}"
        dialects.append("dash")

    helped = run(["sh", str(INSTALL), "--help"])
    if helped.returncode != 0 or "--tier=A|B|C" not in out_of(helped):
        return False, f"--help rc={helped.returncode} does not document --tier"

    # The real proof: run it where nothing but a shell and a python exists. It must
    # reach the prerequisite report, name ALL missing tools at once, and exit 2
    # without touching the system.
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        linked = []
        for tool in ALLOWED_TOOLS:
            real = shutil.which(tool)
            if real is None:
                continue
            (bin_dir / tool).symlink_to(real)
            linked.append(tool)
        if "python3" not in linked:
            return None, "skipped (no python3 to build a minimal PATH around)"
        env = {"PATH": str(bin_dir), "HOME": tmp, "GFTI_CONFIG": ""}
        shell = shutil.which("sh") or "/bin/sh"
        bare = run([shell, str(INSTALL)], env=env)
    text = out_of(bare)
    # docker/cargo/uv are the three hard prerequisites; the compose v2 plugin is
    # named on docker's own line because it cannot exist without docker.
    missing = [t for t in ("docker", "cargo", "uv") if t not in text]
    if bare.returncode != 2:
        return (
            False,
            f"bare-PATH run rc={bare.returncode} (want 2): {text.strip()[-140:]!r}",
        )
    if missing:
        return False, f"bare-PATH message omits {missing}: {text.strip()[-140:]!r}"
    if "compose v2 plugin" not in text:
        return (
            False,
            f"docker line does not name the compose plugin: {text.strip()[-140:]!r}",
        )
    if "not installable" not in text:
        return False, "bare-PATH run did not say the tier is not installable"
    if "install: OK" in text:
        return False, "bare-PATH run claimed success"

    return True, f"posix={dialects} bare-PATH names all 3 missing, rc=2"


def case_ps1():
    if not PS1.is_file():
        return False, "bootstrap/gfti.ps1 missing"
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        return None, "skipped (no pwsh on PATH; the shim is not exercised here)"

    # Windows has no `sh`, so the reference is the CLI itself there. The claim is
    # the same either way: the PowerShell shim must reach the identical answer.
    def reference(*args: str):
        if os.name == "posix":
            return parse_json(out_of(run(["sh", str(SHIM), *args])))
        return parse_json(cli(*args).stdout or "")

    tier_doc = parse_json(
        out_of(run([pwsh, "-NoProfile", "-File", str(PS1), "tier", "--json"]))
    )
    if not isinstance(tier_doc, dict) or tier_doc.get("tier") not in ("A", "B", "C"):
        return False, f"pwsh tier unparseable: {tier_doc!r}"
    ref_doc = reference("tier", "--json")
    if (ref_doc or {}).get("tier") != tier_doc.get("tier"):
        return (
            False,
            f"pwsh tier {tier_doc.get('tier')} != reference tier {(ref_doc or {}).get('tier')}",
        )

    # Parity is asserted against the CLI's own JSON, not against a copied list.
    ps_doc = parse_json(
        out_of(run([pwsh, "-NoProfile", "-File", str(PS1), "config", "--json"]))
    )
    ref_cfg = reference("config", "--json")
    if not isinstance(ps_doc, dict) or not isinstance(ref_cfg, dict):
        return False, "config --json unparseable from one of the entry points"
    for key in ("source", "config", "compose_env"):
        if ps_doc.get(key) != ref_cfg.get(key):
            return False, f"pwsh and the CLI disagree on {key!r}"
    parity = len(ref_cfg.get("compose_env") or {})
    return (
        True,
        f"pwsh {Path(pwsh).name}: tier {tier_doc['tier']}, config parity on {parity} keys",
    )


def case_cios():
    if not CI.is_file():
        return False, f"missing {CI.relative_to(REPO)}"
    text = CI.read_text(encoding="utf-8")
    problems = []
    for family in ("ubuntu-latest", "macos-latest", "windows-latest"):
        if family not in text:
            problems.append(f"no {family} runner")
    # The macOS and Windows jobs must run this gate: a matrix row that only builds
    # Rust would leave the installer untested on the OS that motivated it.
    if "tier_gates.py" not in text:
        problems.append("no job runs tier_gates.py")
    # The Windows job is tier C: CLI non-Docker paths only. A `docker ` invocation
    # there would be a lie about what GitHub's Windows runner can do.
    win = text.split("windows-tier-c", 1)
    if len(win) == 2 and re.search(r"^\s+(run|uses):.*docker", win[1], re.MULTILINE):
        problems.append("windows job touches docker (tier C is CLI-only)")
    ok = not problems
    detail = "families ubuntu+macos+windows, tier_gates wired" + (
        f" problems={problems}" if problems else ""
    )
    return ok, detail


def case_encoding():
    """Tier C is a cp1252 console as often as not. The CLI's own text contains
    `→`, `·` and `—`, so every non-Docker verb must survive an encoding that cannot
    represent them. This is the check that reproduces the windows-latest crash on
    a Linux box: `gfti --help` with PYTHONIOENCODING=cp1252 raised
    UnicodeEncodeError before `_safe_console()` existed."""
    problems = []
    for enc in ("cp1252", "ascii"):
        for args in (
            ("--help",),
            ("workflows",),
            ("tier", "--json"),
            ("config", "--json"),
        ):
            env = dict(os.environ, PYTHONIOENCODING=enc)
            # The child legitimately emits cp1252 bytes here, so read them as bytes
            # and only judge the exit code and that a usage line came out.
            p = run([sys.executable, str(CLI), *args], env=env, text=False)
            if p.returncode != 0:
                problems.append(f"{enc} {' '.join(args)} rc={p.returncode}")
            elif args == ("--help",) and b"usage" not in (p.stdout or b"").lower():
                problems.append(f"{enc} --help printed no usage")
    ok = not problems
    return ok, (
        "3 verbs x 2 encodings survive"
        + (f" problems={problems[:3]}" if problems else "")
    )


CASES = {
    "PROBE": case_probe,
    "POSIX": case_posix,
    "PS1": case_ps1,
    "CIOS": case_cios,
    "ENCODING": case_encoding,
}


def main() -> None:
    args = [a.upper() for a in sys.argv[1:]] or ["ALL"]
    selected = list(CASES) if "ALL" in args else args
    unknown = [c for c in selected if c not in CASES]
    if unknown:
        print(f"TIER-GATES unknown case(s) {unknown}; choose from {list(CASES)}")
        sys.exit(2)

    summary, bad, skipped = {}, [], []
    for name in selected:
        ok, detail = CASES[name]()
        if ok is None:
            summary[name] = "skip"
            skipped.append(name)
            print(f"TIER-{name} SKIP — {detail}", flush=True)
            continue
        summary[name] = ok
        print(f"TIER-{name} {'ok' if ok else 'FAIL'} — {detail}", flush=True)
        if not ok:
            bad.append(name)
    summary["ok"] = not bad
    if skipped:
        summary["skipped"] = skipped
    print("SUMMARY " + json.dumps(summary, sort_keys=True))
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
