#!/usr/bin/env python3
"""E4-WF walk-forward trainer — the W1 non-LLM training lane.

Honest scope (FULL-SYSTEM-BLUEPRINT W1): parameter selection on in-sample
windows + OOS evaluation on untouched blocks + Deflated Sharpe reported
AS-IS (an evaluation statistic, not a promise of profitability). The
promotion ladder (E6-PAPER → SHADOW → owner ≤5/wk → LIVE) still applies.

Pipeline (one machine for every symbol — "add symbol = config"):
  1. resolve the dataset from D3 (registered + verified + B-GATE clean)
  2. verify the D0 parquet sha256 against the registry row (lineage)
  3. read bars through DuckDB over the D0 parquet (D6-class research path)
  4. grid of SMA-cross rules; params selected by in-sample Sharpe per fold
     (train) — 27 trials; N deflates the Sharpe
  5. evaluate OOS on the following block with a 2-bar embargo (PIT rule:
     decision bar t uses closes ≤ t-1; the OOS block never leaks)
  6. costs: 8 bps per flip (conservative daily-bar estimate)
  7. Deflated Sharpe Ratio (Bailey & López de Prado): PSR of the selected
     OOS Sharpe vs the expected-max SR of 27 trials, skew/kurt-adjusted
  8. emit artifact JSON (frozen params + feature hash + D3 lineage)

The trainer NEVER writes to D4/D5 itself — the gate (w1_gates.py) signs
and promotes, keeping this trainer pure, rerun-idempotent output.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import subprocess
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

GRID = tuple(itertools.product((3, 5, 8), (10, 20, 40), (0.0, 0.001, 0.005)))
COST_BPS = 8.0
EMBARGO_BARS = 2


def psql_query(sql: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "-f", str(REPO / "compose" / "docker-compose.yml"),
         "exec", "-T", "postgres", "psql", "-U", "gfti_app", "-d", "gfti",
         "-At", "-F", "|", "-c", sql],
        capture_output=True, text=True, timeout=120, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()}")
    return r.stdout.strip()


def resolve_dataset(dataset_key: str) -> tuple[Path, str]:
    """D3 row must exist, be verified, and be B-GATE-clean."""
    row = psql_query(
        "SELECT d0_path, sha256, verified FROM d3.datasets "
        f"WHERE dataset_key = '{dataset_key}'")
    if not row:
        raise RuntimeError(f"dataset {dataset_key} is not registered in D3")
    d0_path, sha256, verified = row.split("|")
    if verified != "t":
        raise RuntimeError(f"dataset {dataset_key} NOT verified in D3 "
                           "(B-GATE refuses training until gaps are closed)")
    gaps = psql_query(
        "SELECT count(*) FROM d3.gaps WHERE dataset_key = "
        f"'{dataset_key}' AND status = 'open' AND justification = ''")
    if gaps != "0":
        raise RuntimeError(f"{gaps} open unjustified gaps on {dataset_key} "
                           "— training locked by B-GATE")
    p = (REPO / d0_path).resolve()
    if not p.exists():
        raise RuntimeError(f"d0 artifact missing: {p}")
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    if actual != sha256:
        raise RuntimeError(
            f"d0 sha256 mismatch: registry {sha256[:16]} vs file {actual[:16]}")
    return p, sha256


def load_close(d0_path: Path) -> list[float]:
    """Close series via DuckDB (D6-class read: never the live tables)."""
    import duckdb

    rel = duckdb.read_parquet(str(d0_path))
    cols = [c[0] for c in rel.description]
    if "ts_day" not in cols or "close" not in cols:
        raise RuntimeError(f"unexpected parquet schema: {cols}")
    out = [
        float(r[0])
        for r in duckdb.sql("SELECT close FROM rel ORDER BY ts_day").fetchall()
    ]
    if len(out) < 2 or any(math.isnan(c) or c <= 0 for c in out):
        raise RuntimeError("d0 close series too short / non-positive / NaN")
    return out


def sma_signals(closes: list[float], fast: int, slow: int, eps: float
                ) -> list[int]:
    """E2's rule family on a close series: +1 fast>SMA_slow*(1+eps), -1
    below, hold otherwise. Signals decided on bar t apply to t+1 (PIT)."""
    sig = [0] * len(closes)
    prev = 0
    warm = max(fast, slow)
    for t in range(warm - 1, len(closes)):
        f = sum(closes[t - fast + 1: t + 1]) / fast
        s = sum(closes[t - slow + 1: t + 1]) / slow
        if f > s * (1.0 + eps):
            prev = 1
        elif f < s * (1.0 - eps):
            prev = -1
        sig[t] = prev
    return sig


def stats(sig: list[int], closes: list[float], lo: int, hi: int) -> dict:
    """OOS/IS stats on [lo,hi) — position decided on t-1 earns bar t's
    close-to-close return; each flip costs COST_BPS. Returns `rets` so the
    caller can pool the REAL daily stream (never a flattened mean)."""
    rets: list[float] = []
    pos = 0.0
    flips = 0
    for t in range(max(lo, 1), hi):
        want = sig[t - 1]
        if want != (1 if pos > 0 else (-1 if pos < 0 else 0)):
            flips += 1
            rets.append(-COST_BPS / 10_000.0)
            pos = float(want)
        rets.append(pos * (closes[t] / closes[t - 1] - 1.0))
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    ann = math.sqrt(252.0) if n else 0.0
    return {"ret": mean * n, "sharpe": (mean / sd * ann) if sd > 0 else 0.0,
            "flips": flips, "n_obs": n, "rets": rets}


def walk_forward(closes: list[float], folds: int) -> dict:
    """Roll train→embargo→OOS; OOS blocks are contiguous, non-overlapping."""
    n = len(closes)
    test_len = max(60, n // (folds + 2))
    train_len = 4 * test_len
    fold_rows: list[dict] = []
    trial_sharpes_all: list[float] = []
    start = train_len
    while start + test_len <= n and len(fold_rows) < folds:
        t_lo, t_hi = start - train_len, start - EMBARGO_BARS
        o_lo, o_hi = start, min(start + test_len, n)
        best: tuple[float, tuple[int, int, float]] | None = None
        for params in GRID:
            sig = sma_signals(closes[t_lo:t_hi], *params)
            st = stats(sig, closes[t_lo:t_hi], 0, t_hi - t_lo)
            trial_sharpes_all.append(st["sharpe"])
            if best is None or st["sharpe"] > best[0]:
                best = (st["sharpe"], params)
        sel = best[1]
        full_sig = sma_signals(closes, *sel)
        st = stats(full_sig, closes, o_lo, o_hi)
        st.update({"fold": len(fold_rows) + 1, "oos_from": o_lo,
                   "oos_to": o_hi, "params": sel,
                   "train_sharpe": round(best[0], 4)})
        fold_rows.append(st)
        start += test_len
    if not fold_rows:
        raise RuntimeError("data too short for even one fold")
    return {"folds": fold_rows, "fold_count": len(fold_rows),
            "test_len": test_len, "train_len": train_len,
            "trial_sharpes": trial_sharpes_all}


def deflated_sharpe(oos_rets: list[float], trial_sharpes: list[float]) -> float:
    """DSR of the selected strategy's pooled OOS Sharpe (annualized) against
    the expected max of len(GRID) trials, σ(SR) from cross-trial variance.
    Bailey & López de Prado 2014; returns NaN when inputs are degenerate —
    honest NaN, never a fake pass."""
    n = len(oos_rets)
    if n < 30 or len(trial_sharpes) < 3:
        return float("nan")
    mean = sum(oos_rets) / n
    var = sum((r - mean) ** 2 for r in oos_rets) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return float("nan")
    sr_daily = mean / sd
    ann = math.sqrt(252.0)
    skew = (sum((r - mean) ** 3 for r in oos_rets) / n) / sd ** 3
    kurt = (sum((r - mean) ** 4 for r in oos_rets) / n) / sd ** 4
    # expected max SR under N trials: σ(SR)≈stdev of trial Sharpes
    # (annualized units throughout)
    sr_trials = [s for s in trial_sharpes]
    sigma_sr_ann = (
        (sum((s - sum(sr_trials) / len(sr_trials)) ** 2 for s in sr_trials)
         / (len(sr_trials) - 1)) ** 0.5
        if len(sr_trials) > 2 else 0.0
    )
    if sigma_sr_ann == 0:
        return float("nan")
    ntr = len(GRID) * max(1, 1)
    euler = 0.5772156649015329
    p = 1.0 - 1.0 / ntr
    z_hi = _norm_ppf(p)                     # (1-1/N) quantile
    z_lo = _norm_ppf(1.0 - 1.0 / (math.e * ntr))
    sr0_ann = sigma_sr_ann * ((1.0 - euler) * z_lo + euler * z_hi)
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr_daily
                          + (kurt - 1.0) / 4.0 * sr_daily * sr_daily))
    dsr_daily = _norm_cdf(
        math.sqrt(n - 1) * (sr_daily - sr0_ann / ann) / denom)
    return dsr_daily


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam's inverse-normal approximation (good to ~1e-9)."""
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732529341770e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q
                + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q
                 + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r
            + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r
                            + b[4]) * r + 1)


def build_artifact(dataset_key: str, dataset_sha256: str, best_params,
                   oos_sharpe: float, dsr: float, fold_count: int) -> dict:
    fast, slow, eps = best_params
    feature_hash = hashlib.sha256(
        f"sma-cross:{fast}:{slow}:{eps}".encode()).hexdigest()[:32]
    # artifact id is run-scoped: d5.artifacts is append-only (UPDATE/DELETE
    # denied by the D4 deny trigger), so each gate run records its own row.
    # Identity is pinned by discriminator + params + dataset_sha256 +
    # feature_hash + the sha256 of the file bytes, not by uniqueness of runs.
    run_tag = uuid.uuid4().hex[:8]
    return {
        "artifact_id": "sma-cross-" + dataset_key.replace("/", "_") + "-"
                       + run_tag,
        "kind": "rule", "discriminator": "sma-cross",
        "params": {"fast": fast, "slow": slow, "eps": eps},
        "feature_hash": feature_hash,
        "dataset_key": dataset_key, "dataset_sha256": dataset_sha256,
        "wf": {"oos_sharpe": round(oos_sharpe, 4), "dsr": round(dsr, 4),
               "folds": fold_count, "grid_trials": len(GRID),
               "cost_bps_flip": COST_BPS, "embargo_bars": EMBARGO_BARS},
    }


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--dataset", required=True,
                   help="D3 dataset_key, e.g. hyperliquid/BTC-USD/1d")
    a.add_argument("--folds", type=int, default=6)
    a.add_argument("--out", required=True, help="artifact JSON path")
    args = a.parse_args()

    d0_path, dsha = resolve_dataset(args.dataset)
    closes = load_close(d0_path)
    wf = walk_forward(closes, args.folds)

    # pooled OOS series of the final selected signal: concatenate the REAL
    # daily returns of each OOS window (train gaps excluded — no fabrication)
    sel = wf["folds"][-1]["params"]
    full_sig = sma_signals(closes, *sel)
    oos_rets: list[float] = []
    for f in wf["folds"]:
        st = stats(full_sig, closes, f["oos_from"], f["oos_to"])
        oos_rets.extend(st["rets"])
    mean = sum(oos_rets) / len(oos_rets)
    var = sum((r - mean) ** 2 for r in oos_rets) / (len(oos_rets) - 1)
    pooled_sharpe = mean / math.sqrt(var) * math.sqrt(252.0) if var > 0 else 0.0
    dsr = deflated_sharpe(oos_rets, wf["trial_sharpes"])

    artifact = build_artifact(args.dataset, dsha, sel, pooled_sharpe, dsr,
                              wf["fold_count"])
    Path(args.out).write_text(json.dumps(artifact, indent=2, sort_keys=True))
    summary = {
        "dataset_key": args.dataset, "d0_path": str(d0_path),
        "bars": len(closes), "folds": wf["folds"], "artifact_file": args.out,
        "grid_trials": len(GRID), "params_selected": sel,
        "oos_sharpe_pooled": round(pooled_sharpe, 4),
        "dsr": None if math.isnan(dsr) else round(dsr, 4),
        "honesty_note": "statistics reported AS-IS: an evaluation of "
                        "parameter selection across 27 trials — not a "
                        "profitability or live-readiness claim",
    }
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
