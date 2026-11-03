# G-PILOT-E2E — fixture end-to-end, three frozen pilots

Gate: **A0 §7 gate 7** (`docs/decisions/A0-DECISION-BASELINE.md`).
Runner: `python/gates/pilot_e2e.py` (`uv run --project python/train python3 python/gates/pilot_e2e.py ALL`).

## What it runs

Per pilot, on the **live release binaries** (`rail` → `cell` → `e7` → `ledger_recon`):

1. Pass A over a deterministic 220-tick fixture feed → the cell's own
   `LEDGER-CELL-SUMMARY` calibrates the exact intent count.
2. `e7` subscribed **first**, then Pass B over the identical feed → signed D4
   fills, then `ledger_recon` checks every signature.

Both passes must report identical `decisions` / `allows` / `intents_published`.

## Data honesty

The fixtures are **labelled synthetic** ramps: `data_kind=fixture-synthetic`,
`real_registered_data=false` in every line. No D3 row is written, no venue data
is claimed, and nothing is relabelled — P1 stays `BTC-USDC-PERP` (symbol
`BTCUSDC` in this gate), P2 is the NIFTY F&O chain identity, P3 Gold remains
feed-pending. Real-data E2E per pilot requires the registered dataset + a
`verified=true` D3 row and is a separate acceptance step.

## Evidence (2026-11-03, tier-A box, commit `5ad9aa2` binaries)

```
PILOT-E2E P1 {"symbol":"BTCUSDC","decisions":8,"allows":8,"intents":8,"d4_fills":8,
              "e7_fills":8,"bad_sigs":0,"e12_p99_ns":534,"ok":true,...}
PILOT-E2E P2 {"symbol":"NIFTY",  "decisions":8,"allows":8,"intents":8,"d4_fills":8,
              "e7_fills":8,"bad_sigs":0,"e12_p99_ns":542,"ok":true,...}
PILOT-E2E P3 {"symbol":"GOLD",   "decisions":8,"allows":8,"intents":8,"d4_fills":8,
              "e7_fills":8,"bad_sigs":0,"e12_p99_ns":610,"ok":true,...}
PILOT-E2E-SUITE {"failed":[],"ok":true,...}
```

Checks enforced per pilot: `decisions_gt_zero`, `e12_within_budget` (< 1,000,000 ns),
`intents_gt_zero`, `fills_equal_intents`, `e7_saw_all`, `bad_sigs_zero`,
`deterministic_passes`.

## What this gate does **not** prove

- venue connectivity, real fills, or queue position (needs W2 adapters + real data);
- profitability — DSR stays below the 0.95 promotion gate by design;
- anything about P3's identity, which is still unfrozen.
