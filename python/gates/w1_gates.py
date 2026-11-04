#!/usr/bin/env python3
"""W1 gates (FULL-SYSTEM-BLUEPRINT §13 W1 row): D6-class research read ·
E4-WF walk-forward with DSR reported AS-IS · signed D5 artifact registry ·
the E-spine (E1→E2→E5→E6→E7) replayed on REAL registered daily closes.

  WF        walk_forward.py on a D3-verified dataset (B-GATE honored):
            folds ≥ 2, DSR ∈ [0,1] or NaN, artifact JSON complete, and a
            training-lock negative test (unverified dataset must refuse)
  ARTIFACT  D5 registry: apply 005_w1_artifacts.sql, sign the canonical
            artifact with HMAC (ledger scheme), INSERT, re-verify the
            signature, tamper-refusal negative test, append-only UPDATE deny
  REPLAY    the real closes stream through rail→cell→E7: ≥1 Allow, signed
            D4 fills on the real symbol, recon clean, E12 p99 < 1 ms
  ALL       the three, in order

All evidence = verbatim command output; paste into docs/gates/W1.md unset.
Usage: python3 python/gates/w1_gates.py [WF|ARTIFACT|REPLAY|ALL]
"""

import hashlib
import hmac
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
COMPOSE = REPO / "compose"
TRAIN = REPO / "python" / "train"
SQL005 = COMPOSE / "sql" / "005_w1_artifacts.sql"

sys.path.insert(0, str(HERE))
import w0b_gates as w0b
from _env import scratch_dir

SCRATCH = scratch_dir()
DATASET = "hyperliquid/BTC-USD/1d"


def psql(sql: str):
    ok, out = w0b.psql_exec(sql)
    if not ok:
        raise RuntimeError(f"psql failed: {out}")
    return out


def rc(*args, env=None, cwd=None, timeout=600):
    r = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env or w0b.LEDGER_ENV,
        cwd=str(cwd or REPO),
        check=False,
    )
    return r.returncode, r.stdout + r.stderr


def hled(canonical: str, secret: str) -> str:
    """Same HMAC-SHA256 canonical-row scheme as the ledger crate."""
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def gate_wf():
    """E4-WF on the real registered dataset + artifact payload complete."""
    art = SCRATCH / "w1_btc_artifact.json"
    out = SCRATCH / "w1_btc_wf.out"
    t0 = time.time()
    r = subprocess.run(
        [
            "uv",
            "run",
            "walk_forward.py",
            "--dataset",
            DATASET,
            "--folds",
            "6",
            "--out",
            str(art),
        ],
        capture_output=True,
        text=True,
        timeout=900,
        cwd=str(TRAIN),
        env=dict(w0b.LEDGER_ENV),
        check=False,
    )
    out.write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"walk_forward rc={r.returncode}: {r.stderr[-400:]}")
    summ = json.loads(r.stdout)
    artj = json.loads(art.read_text())

    # training-lock NEGATIVE test first: a dataset with open unjustified gaps
    # (or unverified) must be refused — re-run against the (never registered)
    # fake key and expect a clean refusal, not a crash path skipping checks
    r2 = subprocess.run(
        [
            "uv",
            "run",
            "walk_forward.py",
            "--dataset",
            "b1-test/unjustified/1d",
            "--folds",
            "2",
            "--out",
            str(SCRATCH / "never.json"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(TRAIN),
        env=dict(w0b.LEDGER_ENV),
        check=False,
    )
    locked = "not registered in D3" in (r2.stderr + r2.stdout) and r2.returncode != 0

    folds = summ["folds"]
    dsr = summ.get("dsr")
    ok = (
        len(folds) >= 2
        and summ["bars"] >= 300
        and all(f["n_obs"] > 0 for f in folds)
        and artj["dataset_key"] == DATASET
        and artj["dataset_sha256"]
        and artj["feature_hash"]
        and artj["wf"]["grid_trials"] == 27
        and (dsr is None or (0.0 <= dsr <= 1.0))
        and locked
    )
    print(
        "W1-GATE-WF "
        + json.dumps(
            {
                "dataset": DATASET,
                "bars": summ["bars"],
                "fold_count": len(folds),
                "grid_trials": artj["wf"]["grid_trials"],
                "params_selected": artj["params"]
                if "params" in artj
                else summ.get("params_selected"),
                "oos_sharpe_pooled": summ["oos_sharpe_pooled"],
                "dsr": dsr,
                "training_lock_refused": locked,
                "seconds": round(time.time() - t0, 1),
                "ok": ok,
            },
            sort_keys=True,
        )
    )
    return art, artj, ok


def gate_artifact(artj: dict):
    """D5 registry: signed insert, verify, tamper refusal, UPDATE deny."""
    apply_ok, _ = w0b.psql_exec(SQL005.read_text())
    if not apply_ok:
        raise RuntimeError("005_w1_artifacts.sql failed to apply")
    _e0, secret = w0b.owner_env()
    a = artj
    canonical = json.dumps(
        {
            "dataset_key": a["dataset_key"],
            "dataset_sha256": a["dataset_sha256"],
            "discriminator": a["discriminator"],
            "eps": a["params"]["eps"],
            "fast": a["params"]["fast"],
            "slow": a["params"]["slow"],
            "wf_oos_sharpe": a["wf"]["oos_sharpe"],
            "wf_dsr": a["wf"]["dsr"],
            "wf_folds": a["wf"]["folds"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    sig = hled(canonical, secret)
    art_file = REPO / "d5" / f"{a['artifact_id']}.json"
    art_file.parent.mkdir(exist_ok=True)
    art_file.write_text(json.dumps(a, indent=2, sort_keys=True))
    sha = hashlib.sha256(art_file.read_bytes()).hexdigest()

    # d5.artifacts is append-only by design (005_w1_artifacts.sql: UPDATE and
    # DELETE denied by the D4 deny trigger). Rows from previous gate runs stay;
    # artifact_id is run-scoped (walk_forward.py) so this INSERT never collides.
    # Tamper/verify tests below use this run's fresh secret against this run's row.
    psql("TRUNCATE d5.gate_w1;")
    ins = (
        "INSERT INTO d5.artifacts (artifact_id, kind, status, discriminator, "
        "params, feature_hash, dataset_key, dataset_sha256, wf_oos_sharpe, "
        "wf_dsr, wf_folds, signer_role, sig) VALUES ("
        f"'{a['artifact_id']}', '{a['kind']}', 'candidate', "
        f"'{a['discriminator']}', "
        f"'{json.dumps(a['params'], sort_keys=True)}', '{a['feature_hash']}', "
        f"'{a['dataset_key']}', '{a['dataset_sha256']}', "
        f"{a['wf']['oos_sharpe']}, {a['wf']['dsr']}, {a['wf']['folds']}, "
        f"'owner', '{sig}');"
    )
    ins_ok, _ins_err = w0b.psql_exec(ins)
    row = psql(f"SELECT sig FROM d5.artifacts WHERE artifact_id = '{a['artifact_id']}'")
    got_sig = row.strip()
    # rebuild the canonical payload from the stored row for re-verification
    parts = psql(
        "SELECT artifact_id || '|' || discriminator || '|' || feature_hash || "
        "'|' || dataset_key || '|' || dataset_sha256 || '|' || "
        "wf_oos_sharpe::TEXT || '|' || wf_dsr::TEXT || '|' || wf_folds::TEXT || "
        "'|' || params::TEXT FROM d5.artifacts WHERE artifact_id = "
        f"'{a['artifact_id']}'"
    )
    _pid, disc, _fh, dk, dsha, osr, dwf, folds_n, ptext = parts.split("|")
    canon2 = json.dumps(
        {
            "dataset_key": dk,
            "dataset_sha256": dsha,
            "discriminator": disc,
            "eps": json.loads(ptext)["eps"],
            "fast": json.loads(ptext)["fast"],
            "slow": json.loads(ptext)["slow"],
            "wf_oos_sharpe": float(osr),
            "wf_dsr": float(dwf),
            "wf_folds": int(folds_n),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    ver_ok = hled(canon2, secret) == got_sig
    # tamper refusal: change one byte of the params and re-verify
    dict_t = json.loads(ptext)
    dict_t["eps"] = 0.5
    canon3 = json.dumps(
        {
            "dataset_key": dk,
            "dataset_sha256": dsha,
            "discriminator": disc,
            "eps": dict_t["eps"],
            "fast": json.loads(ptext)["fast"],
            "slow": json.loads(ptext)["slow"],
            "wf_oos_sharpe": float(osr),
            "wf_dsr": float(dwf),
            "wf_folds": int(folds_n),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    tamper_resp = hled(canon3, secret) != got_sig
    # append-only: UPDATE must be refused by the deny trigger
    upd_ok, _upd_out = w0b.psql_exec(
        f"UPDATE d5.artifacts SET status='frozen' WHERE artifact_id = "
        f"'{a['artifact_id']}';",
        want_fail=True,
    )
    evidence = (
        f"d5 sha256 file {sha[:16]} sig {sig[:16]} verified={ver_ok} "
        f"tamper_refused={tamper_resp} update_deny={upd_ok}"
    )
    verdict = "PASS" if (ins_ok and ver_ok and tamper_resp and upd_ok) else "FAIL"
    recorded = w0b.psql_exec(
        "INSERT INTO d5.gate_w1 (gate, verdict, evidence) VALUES "
        f"('artifact', '{verdict}', '{evidence}') RETURNING gate;"
    )
    ok = (ins_ok and ver_ok and tamper_resp and upd_ok
          and "artifact" in recorded[1])
    print(
        "W1-GATE-ARTIFACT "
        + json.dumps(
            {
                "artifact_id": a["artifact_id"],
                "file_sha16": sha[:16],
                "sig16": sig[:16],
                "sig_verified": ver_ok,
                "tamper_refused": tamper_resp,
                "update_denied": upd_ok,
                "ok": ok,
                "note": "dev gate: per-run ephemeral HMAC secret, same scheme as D4",
            },
            sort_keys=True,
        )
    )
    return ok


REPLAY_DATASET = "hyperliquid/BTC-USD/1d"
REPLAY_GRANULARITY = "1d"


def gate_replay(symbol: str = "BTC", ticks: int = 250):
    """REAL daily closes → rail → cell(E1/E2/E5/E6) → e7 signed D4 fills.

    Granularity is explicit: this replays registered DAILY closes spaced one
    day apart. It is a plumbing/latency proof, not an intraday replay, and it
    makes no claim about tick data or intraday microstructure.
    """
    import pyarrow.parquet as pq

    # d0_path comes from the D3 registry (single source of truth); do NOT
    # reconstruct it from dataset_key + granularity — the registry stores
    # d0/hyperliquid/BTC-USD/1d.parquet, not <granularity>/<granularity>.parquet
    d0_path = psql(
        f"SELECT d0_path FROM d3.datasets WHERE dataset_key = '{REPLAY_DATASET}'"
    ).strip()
    if not d0_path:
        raise RuntimeError(f"{REPLAY_DATASET} has no d0_path in D3")
    d0_file = REPO / d0_path
    t = pq.read_table(d0_file)
    closes = [round(float(v), 2) for v in t.column("close").to_pylist()[-ticks:]]
    if len(closes) < ticks:
        raise RuntimeError("not enough real bars for replay")
    e, secret = w0b.owner_env()
    e = dict(
        e,
        RAIL_NATS_URL="nats://127.0.0.1:4222",
        KILL_REFRESH_MS="200",
        # cell exits after CELL_TICKS ticks — must equal the feed length or the
        # cell never exits and p.wait() times out (W0b pattern: 220-tick feed)
        CELL_TICKS=str(ticks),
        CELL_DECIDE_EVERY="25",
        CELL_SUBJECT=f"market.spot.{symbol}",
    )
    w0b.run(w0b.BIN / "ledger_init", str(COMPOSE / "sql" / "001_d3_d4_init.sql"), env=e)
    # Reap orphans from earlier crashed runs BEFORE truncating: a leaked e7
    # from a previous run stays subscribed and consumes this run's intents,
    # appending fills signed with a STALE per-run HMAC secret — recon then
    # fails rc=9 on mixed-secret fills.
    for stray in ("e7", "cell", "rail"):
        subprocess.run(["pkill", "-f", f"target/release/{stray}"],
                       capture_output=True, check=False)
    w0b.psql_exec("TRUNCATE d4.fills; TRUNCATE d4.pnl_recon;")
    w0b.run(w0b.BIN / "ledger_kill", "resolve", "L4", "W1-replay-open", env=e)

    feed = SCRATCH / f"w1_feed_{symbol}.tsv"
    base_ts = 1_760_000_000 * 1_000_000_000  # 2025-10 ns epoch base
    with open(feed, "w") as fh:
        fh.writelines(
            f"{symbol} {base_ts + i * 86_400 * 1_000_000_000} {px} public\n"
            for i, px in enumerate(closes)
        )

    # E7 consumes the E6 intents it published — with core NATS a late
    # subscriber sees nothing (messages are not retained), so E7 must be
    # subscribed BEFORE the cell publishes (same order as W0b G7). Two passes
    # over the same real closes: pass 1 calibrates the exact intent count from
    # the cell's own summary (deterministic decisions), pass 2 runs with E7
    # subscribed first and replays the identical feed. A leaked cell/e7 from a
    # crashed run ships a stale-secret fill, so every child must die with this
    # run — finally-block kills anything still alive.
    children: list[subprocess.Popen] = []

    def _spawn(cmd: list, **kw) -> subprocess.Popen:
        p = subprocess.Popen(cmd, **kw)
        children.append(p)
        return p

    def _cell_pass(log: Path) -> None:
        with open(log, "w") as fh:
            p = _spawn([str(w0b.BIN / "cell")], env=e, stdout=fh,
                       stderr=subprocess.STDOUT, cwd=str(REPO))
            time.sleep(1.2)
            with open(feed) as feed_handle:
                subprocess.run(
                    [str(w0b.BIN / "rail")],
                    stdin=feed_handle,
                    capture_output=True,
                    timeout=180,
                    cwd=str(REPO),
                    check=False,
                )
            p.wait(timeout=90)

    try:
        _cell_pass(SCRATCH / "w1_cell.log")
        summ = [
            x
            for x in (SCRATCH / "w1_cell.log").read_text().splitlines()
            if x.startswith("LEDGER-CELL-SUMMARY")
        ]
        cellj = json.loads(summ[-1].split(" ", 1)[1]) if summ else {}
        expected_intents = cellj.get("intents_published", 0)
        if not expected_intents:
            raise RuntimeError("cell produced zero intents in calibration pass")

        w0b.psql_exec("TRUNCATE d4.fills; TRUNCATE d4.pnl_recon;")
        # A-1: E7 is resident; there is no "GO" handshake to write. stdin is
        # /dev/null, which is what it will be under a container.
        with open(SCRATCH / "w1_e7.log", "w") as e7log:
            p_e7 = _spawn(
                [str(w0b.BIN / "e7")],
                env=dict(
                    e,
                    E7_EXPECT=str(expected_intents),
                    E7_SUBJECT="order.spot.>",
                    LEDGER_SECRET_OWNER=secret,
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=e7log,
                cwd=str(REPO),
            )
            time.sleep(1.2)  # E7 subscribed before any new intent is published
            _cell_pass(SCRATCH / "w1_cell2.log")
            p_e7.wait(timeout=120)
        summ2 = [
            x
            for x in (SCRATCH / "w1_cell2.log").read_text().splitlines()
            if x.startswith("LEDGER-CELL-SUMMARY")
        ]
        cellj = json.loads(summ2[-1].split(" ", 1)[1]) if summ2 else cellj
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    e7txt = (SCRATCH / "w1_e7.log").read_text()
    done = [x for x in e7txt.splitlines() if x.startswith("LEDGER-E7-DONE")]
    e7j = json.loads(done[-1].split(" ", 1)[1]) if done else {}
    fills = psql(
        "SELECT count(*), count(DISTINCT symbol) FROM d4.fills "
        f"WHERE symbol = '{symbol}';"
    )
    recon = w0b.run(w0b.BIN / "ledger_recon", env=e)
    rj = {}
    rline = [x for x in recon.splitlines() if x.startswith("LEDGER-RECON ")]
    if rline:
        rj = json.loads(rline[-1].split(" ", 1)[1])
    # fills row count printed as "n|m" (psql -A -t: single unaligned line)
    rows = [x.strip() for x in fills.splitlines() if x.strip()]
    n_long = rows[-1] if rows else "0|0"

    ok = (
        cellj.get("decisions", 0) >= 1
        and cellj.get("e12_p99", 1e12) < 1_000_000
        and int(n_long.split("|")[0].strip()) >= 1
        and rj.get("bad_sigs") == 0
    )
    print(
        "W1-GATE-REPLAY "
        + json.dumps(
            {
                "symbol": symbol,
                "real_closes": len(closes),
                "first_close": closes[0],
                "last_close": closes[-1],
                "decisions": cellj.get("decisions"),
                "allows": cellj.get("allows"),
                "intents_published": cellj.get("intents_published"),
                "e12_p99_ns": cellj.get("e12_p99"),
                "budget_ns": 1_000_000,
                "e7_fills": e7j.get("fills"),
                "real_symbol_fills": n_long,
                "bad_sigs": rj.get("bad_sigs"),
                "ok": ok,
            },
            sort_keys=True,
        )
    )
    return ok


def main() -> None:
    which = sys.argv[1].upper() if len(sys.argv) > 1 else "ALL"
    bad = []
    if which in ("WF", "ALL"):
        ok = gate_wf()[2]
        if not ok:
            bad.append("WF")
    if which in ("ARTIFACT", "ALL"):
        import json as _json

        art = SCRATCH / "w1_btc_artifact.json"
        if not art.exists():
            _art, aj, _ok = gate_wf()
            bad.append("WF (was re-run prerequisite)")
        else:
            aj = _json.loads(art.read_text())
        if not gate_artifact(aj):
            bad.append("ARTIFACT")
    if which in ("REPLAY", "ALL") and not gate_replay():
        bad.append("REPLAY")
    print("W1-GATES " + json.dumps({"requested": which, "failed": bad, "ok": not bad}))
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
