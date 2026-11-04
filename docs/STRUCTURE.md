# Repository structure — reference notes

The full annotated file tree lives in the README under **Repository structure**, so there
is exactly one copy of it. This page keeps the material that would bloat the README: the
crate dependency graph, how the filesystem encodes the three layers, what `.gitignore`
hides and why, and the things that look missing but are deliberate.

For per-gate verdicts see [`gates/STATUS.md`](gates/STATUS.md). For the frozen decisions
this layout implements see
[`decisions/A0-DECISION-BASELINE.md`](decisions/A0-DECISION-BASELINE.md).

## Crate dependency graph

Read from the eight `Cargo.toml` files. It is a DAG, and the direction is deliberate:
dependencies point *downward* toward pure computation, so the top of the graph is the
only place that knows about the outside world.

```
        engine          ← rail · featurize · exec · govern · ledger
       ╱   │   ╲
   exec    │    d0      ← rail · parquet · arrow
  ╱  │ ╲   │
govern │ evaluate      ← featurize · hmac · hex
       │  ╱
   featurize           ← (no internal deps)
   govern              ← (no internal deps)
   rail                ← (no internal deps)
   ledger              ← postgres · hmac · hex
```

Consequences worth stating:

- **`featurize`, `govern`, `rail` are leaves.** They cannot import a store, a socket, or a
  ledger — so they are trivially testable and cannot accidentally acquire I/O.
- **`engine` is the only crate that sees everything**, which is why the decision cell
  lives there and nowhere else.
- **`exec` depends on `govern`**, not the reverse. E5 authority cannot be bypassed by E6
  because E6 is downstream of it in the type system, not just in the call order.

## The three layers, as the filesystem expresses them

| Layer | Paths | Position |
|---|---|---|
| **Rust kernel** | `rust/{rail,d0,ledger,engine,featurize,evaluate,govern,exec}` | hot path — no framework, no store query per tick |
| **Python edge** | `python/{collect,gates,train,seats}` | offline / nightly / test harness — never in a decision |
| **LLM seats** | `python/seats` + the 12 seat ports | approvals and findings through the L0–L3 bridge only |

Crossings between layers are the five versioned contracts: storage · research · artifact
· execution · audit. There is no shared runtime.

## Ignored, and why

`.gitignore` is short on purpose, and each line protects an invariant rather than hiding
clutter:

| Pattern | Reason |
|---|---|
| `target/` | cargo build output |
| `python/collect/.venv/`, `__pycache__/`, `*.pyc` | local env |
| `/d0/` | root-level D0 captures — the 133 MB NSE sweep must never enter git |
| `data/d0/` | scratch data tree (1.8 GB, including an incomplete Kraken download) |
| `/d5/` | D5 artifact outputs; the `d5.artifacts` table is the audit index instead |
| `.env` | secrets are never committed; `compose/.env.example` documents the shape |

The evidence model this encodes: **`d0/` holds the bytes, D3 holds the claim.** Every
statement about captured data is pinned by a D3 row carrying a repo-relative path and a
sha256, so the archive can be regenerated or discarded without invalidating the record.

## Things that look like they're missing but aren't

- `configs/`, `python/research/`, `python/tools/` — reserved and **empty**. Listed so the
  gap is visible rather than quietly forgotten.
- No `rust/*/build.rs` anywhere: nothing generates code at build time.
- No `setup.py`, no `requirements.txt`, no `package.json` — Python is `uv` per package
  (`pyproject.toml` + committed `uv.lock`), and there is no JavaScript in this repo at all.
- `data/d0/kraken/archive/Kraken_Trades_Full.zip.part00.partial` is a **partial** download
  of the ~28 GB archive. Its presence is why G-DATA reads `blocked` rather than absent.
