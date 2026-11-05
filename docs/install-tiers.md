# Install tiers — what "any OS" means, and what each tier proves

A0 §2 defines three tiers. This file is the operator-facing half: the exact
prerequisites, the exact command, and — importantly — what CI does **not** claim.

`gfti tier` prints the tier of the host it runs on. `bootstrap/install.sh` asks the
CLI for that answer instead of keeping its own copy of the OS table, so the
installer, `gfti doctor` and `gfti tier` cannot disagree about which tier you are on.

```console
$ gfti tier
tier A — Linux 7.0.0-27-generic (x86_64)
```

## Prerequisites (all tiers)

| Tool | Minimum | Used for |
|---|---|---|
| Python | 3.11+ (stdlib only) | the `gfti` CLI and the installer's own logic |
| Docker Engine | 24+ **with the compose v2 plugin** | the six services (postgres, questdb, nats, rail, cell, e7) |
| Rust toolchain | 1.80+ | `rail`/`cell` release build |
| uv | 0.4+ | the python lanes (`uv sync`) |

`bootstrap/install.sh` reports **every** missing prerequisite in one message and
exits `2` without changing anything. It does not stop at the first one — that costs
the operator a second round trip on the host that is already having a bad day.

## Tier A — Linux (primary)

Everything runs here; this is where release artifacts are built and where the full
gate suite (`gfti verify --install`) is meaningful.

```sh
git clone <repo> && cd global-financial-trading-intelligence
cp compose/.env.example compose/.env      # fill POSTGRES_PASSWORD + LEDGER_SECRET_OWNER
sh bootstrap/install.sh                   # add --verify for the W1 + pilot suites
gfti status
```

The clean-install-twice rule (A0 §2) runs on tier A inside `G-INSTALL`:
`install.sh --verify` twice in a row, both ending in `install: OK`.

## Tier B — macOS (Apple Silicon)

Same services, via Docker Desktop; `cell`/`rail` build natively.

```sh
sh bootstrap/install.sh --tier=B
```

CI runs a `macos-latest` job that proves the Docker-free half honestly: the Rust
engines build and pass their unit tests, the python lanes pass, `gfti tier` reports
`B`, and the installer parses and runs under macOS `/bin/sh`. **It does not claim a
Docker-backed clean install** — GitHub's hosted macOS runners have no Docker daemon.
The full clean-install-twice on tier B is an owner action on a Mac with Docker
Desktop, and stays an open item until it is run there.

Latency gates (`FANOUT`'s 2 ms p99, `cell`/`rail` residency) are **tier A only**:
a shared CI VM is not a measurement device.

## Tier C — Windows

Native Windows is supported for the **CLI only**:

```powershell
pwsh -File bootstrap/gfti.ps1 tier
pwsh -File bootstrap/gfti.ps1 config --json
```

Services and the installer require WSL2, which is a tier-C Linux host:

```powershell
wsl -d Ubuntu
# inside the distro:
sh bootstrap/install.sh --tier=C
```

Native Windows is refused on purpose, with the route named:

```console
$ python python/gfti/cli.py install
gfti install: native Windows is not a supported host (A0 §2).
  run install inside WSL2:  wsl -d Ubuntu -- sh bootstrap/install.sh
  the CLI alone works natively:  pwsh -File bootstrap/gfti.ps1 --help
```

CI runs a `windows-latest` job limited to those non-Docker paths: the PowerShell
shim, the `tier`/`config`/`workflows` verbs, and the refusal above. A `docker` call
in that job would be a lie about the runner, so `tier_gates.CIOS` fails on one.

## The gate (`python/gates/tier_gates.py`)

| Case | Claim |
|---|---|
| `PROBE` | `gfti tier`, the sh shim and `gfti doctor` agree on the tier; a contradicting `--tier=` is refused with exit 2 |
| `POSIX` | `install.sh` is `#!/bin/sh`, has no bash-only token, parses under `dash -n`, and on a PATH containing *only* a shell and python it reports every missing prerequisite and exits 2 |
| `PS1` | `bootstrap/gfti.ps1` reports the same tier and renders the same configuration as the sh shim (skipped, with a reason, where pwsh is absent) |
| `CIOS` | `.github/workflows/ci.yml` declares ubuntu, macOS and Windows runners and wires this gate into them |

`G-CI-OS` then reads the newest run's jobs: green on **≥2 OS families and the exact
release commit** is the evidence, not the YAML.
