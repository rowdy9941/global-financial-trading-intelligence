# P1 data — Hyperliquid BTC perp, BTC-USDC identity (measured 2026-11-03)

Pilot 1 (A0 §4) is the **Hyperliquid BTC perpetual, venue quote USDC**. This note
records what the public API can and cannot supply, what was actually captured and
registered, and what is still missing. No claim here extends past the evidence.

## 1 · API retention (measured, not assumed)

`POST https://api.hyperliquid.xyz/info {"type":"candleSnapshot"}` — public,
key-free. Probed with 1-hour windows at increasing age:

| Interval | Served at | Empty at | Verdict |
|---|---|---|---|
| `1m` | 3 days | 4 days | **~3-day retention** |
| `5m` | 7 days | 30 days | ~1–4 weeks |
| `15m` | 30 days | 90 days | months |
| `1h` | 90 days | ~1 year | months |
| `1d` | perp inception 2023-11-09 | — | full history |

**Consequence:** tick/1m history beyond ~3 days cannot be backfilled from the
public API. A PIT intraday series must be *captured going forward*
(`scripts/hl_capture.sh`). The legacy `hyperliquid/BTC-USD/1d` daily row is
unaffected and remains a separate identity.

## 2 · Capture + registration (real run)

`python/collect/hyperliquid_daily.py --coin BTC --interval 1m --symbol BTC-USDC`

| Field | Value |
|---|---|
| dataset_key | `hyperliquid/BTC-USDC/1m` |
| window | 2026-10-31 → 2026-11-03 (3 days, aligned) |
| bars | 4,320 requested / 4,320 returned — **0 missing slots** |
| integrity | PASS (closed bars only, monotonic, OHLC bounds, venue-onset filtered) |
| sha256 | `f300a77ac4a38ffbab3c845bf987c57e21ed2e55884c2a9a573b52913701e62c` |
| D0 path | `d0/hyperliquid/BTC-USDC/1m.parquet` (local, not tracked in git) |
| D3 volume metadata | `volume_available=true`, `volume_kind=base_asset_volume`, `volume_unit=BTC` |
| D3 `verified` | **true** — set by the B-GATE rule (no open unjustified gaps), not by assertion |

Determinism: a second, independent capture of the **same fixed window** produced a
byte-identical parquet (`sha256` equal). Capture is reproducible.

Registry honesty: the pre-first_day gap row is `justified` with the *measured*
reason (API retention), not the generic "deeper history in another row" text the
registrar defaults to for daily spot captures. `register_b1.py` gained
`--pre-inception-note` for exactly this; a misleading justification is worse than
no row.

## 2.1 · The archive must MERGE, or accumulation never happens (fixed 2026-11-04)

The first version of `scripts/hl_capture.sh` re-captured the trailing 3-day window
and **rewrote** the artifact. Because the venue retains only ~3 days of 1m bars,
that window is all the archive could ever hold: measured on 2026-11-04, every run
produced exactly **4,320 rows = 3 days**, no matter how long cron ran. The plan's
"accumulate toward the 300-bar WF minimum" was therefore unreachable — the series
would have stayed at 2–3 usable days forever, and nothing in the pipeline would
have complained.

Fix: `hyperliquid_daily.py --merge` unions the captured window with the existing
archive by `open_ms` (a closed bar is immutable; if the venue's numbers for an
already-captured minute differ, the existing row is kept and the difference is
reported as a `revision`, never silently adopted). `hl_capture.sh` now always passes
`--merge` and holds an `flock` so two runs can never rewrite one artifact.

Proof on live data (window deliberately shortened to 1 day against the 3-day
archive):

```
capture window 1 day  →  archive rows 4,322   (a rewrite would have left 1,440)
capture window 3 days →  archive rows 4,320   (sha identical, so merge is idempotent)
```

The remaining 2 rows are the minutes captured between the two runs.

## 3 · Derived daily projection (how accumulation reaches the training lane)

The WF lane is day-bar based and **refuses intraday datasets**
(`walk_forward.require_daily_lane`, added after this dataset produced all-zero
Sharpes with `dsr=NaN` — the bug was the lane, not the data). So the 1m capture is
projected to 1d by `python/collect/resample_1m_to_1d.py`, which never manufactures a
day bar:

- a day is kept only if **complete** — first slot is the UTC midnight open, every
  expected slot present, no interior hole;
- a partial **edge** day (capture window cuts it) is **dropped**: its last 1m close
  is not the day's close;
- a partial **interior** day is a real hole and is **refused** unless
  `--allow-partial`, in which case it is dropped and listed in the summary.

Run (also done automatically at the end of `scripts/hl_capture.sh`):

```
uv run resample_1m_to_1d.py --d0-path ../../d0/hyperliquid/BTC-USDC/1m.parquet \
    --dataset-key hyperliquid/BTC-USDC/1m \
    --out ../../d0/hyperliquid/BTC-USDC/1d.parquet --summary /tmp/hl_1d.json
```

Result on the 4,320-bar capture: **2 complete days kept**, **2 edge days dropped**
(the capture window always cuts the first and last day). Re-run on 2026-11-04 the
kept days are 11-02 and 11-03 — the pair advances with the window, which is exactly
what §2.1 fixes.
Registered as `hyperliquid/BTC-USDC/1d` with the lineage in `source`:
`derived:resample-1m-to-1d:hyperliquid/BTC-USDC/1m:sha-<digest>`.

### Cross-check against the venue's own daily bar

The derived 1d series and an independently captured `hyperliquid/BTC-USD/1d`
(the API's own daily bars) were compared on every overlapping day:

```
2026-11-01  open/high/low/close/volume/n_trades identical  (close 78326.0)
2026-11-02  open/high/low/close/volume/n_trades identical  (close 76805.0)
days_compared 2  all_identical True
```

Two independent paths agree to the last decimal, which is the strongest integrity
signal available without venue files.

## 4 · Tick capture (per-trade prints, WebSocket)

The public REST API serves **candles only**; per-trade prints exist solely as a
live subscription on `wss://api.hyperliquid.xyz/ws`
(`{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}`). Verified
live 2026-11-03: first batch 30 prints, sustained ~2.2–2.6 trades/s for BTC, message
shape `{"channel":"trades","data":[{coin,side,px,sz,time,hash,tid,users}]}` with
`side` `B`=taker buy / `A`=taker sell.

**Consequence:** tick history is not backfillable. Every hour not recorded is gone
permanently, which makes this the only P1 series where a capture failure is
irreversible — hence the durability rules below.

### Recorder

| Piece | Path | Contract |
|---|---|---|
| recorder | `python/collect/hl_trades_ws.py` | hourly gzipped JSONL, one **complete gzip member per flush batch** (200 rows), `tid` dedupe (50k window), out-of-order counted, idle timeout fails the session, bounded-backoff reconnect |
| wrapper | `scripts/hl_trades.sh` | cron `*/15`; `flock` single-instance; 55-min sessions + 5 s pause; PATH pinned for cron |
| recovery | `python/collect/recover_trades_segments.py` | member-wise + chunked-prefix salvage, dedupe by `tid`, deterministic single-member rewrite, `.corrupt` backup by default |

Segments: `d0/hyperliquid/BTC-USDC/trades/<YYYY-MM-DDTHH>.jsonl.gz` (local, **not**
git-tracked — live capture is not reproducible, so raw bytes are kept and hashed
rather than rewritten). Row: `{ts_ms, px, sz, side, tid, hash}` with `px`/`sz` as
decimal **strings** — parsed as `Decimal` in flight, never float.

### Captured so far (measured 2026-11-04T04:2xZ)

| Segment | Rows | Span (UTC) |
|---|---|---|
| `2026-11-03T12` | 100 | 12:55:59 → 12:57:12 |
| `2026-11-03T13` | 1,880 | 13:49:26 → 13:58:55 |
| `2026-11-03T14` | 2,101 | 14:00:00 → 14:12:35 |
| `2026-11-04T04` | 11,196 (live) | 04:00:13 → … |

All four pass `gzip -t` and `recover_trades_segments.py --dry-run` reports
`intact: true`. The live segment shows 23 duplicate `tid`s: dedupe is per process,
so a restart can repeat prints across the boundary — consumers must dedupe by `tid`.

**Known gap — 13 h 48 m, unrecoverable.** Nothing exists between 14:12:35Z on
11-03 and 04:00:13Z on 11-04, for two independent reasons, both recorded here
rather than smoothed over:

1. **13:00–13:49 was destroyed by two concurrent recorders.** Both opened the same
   hourly file and each appended through its own long-lived gzip handle, so their
   deflate streams interleaved *inside a single member*. Decoding that hour stops
   at `invalid block type` after 118 rows: 0 of ~7,700 prints were recoverable.
2. **The box rebooted at ~03:56Z on 11-04.** The recorder died with it; cron
   restarted capture at 04:00:13Z. Downtime is downtime — no prints were invented.

### Incident + repair record

The pre-fix writer kept one gzip handle open for the whole session and raised
`SystemExit` from the SIGTERM handler, abandoning the coroutine mid-member. Real
damage, observed not theorised: `gzip -t` on the live segment reported
`invalid compressed data--format violated`, and the second writer's interleaving
was undecodable outright.

Repair results (`recover_trades_segments.py`, damaged originals kept as
`*.corrupt`):

| Segment | Before | After | Recovered / lost |
|---|---|---|---|
| `T12` | truncated member | 100 rows, `gzip -t` OK | 100 recovered, 0 lost |
| `T13` | interleaved + truncated tail | 1,880 rows, `gzip -t` OK | 1,880 recovered; 24 duplicate `tid`s and 1 truncated tail dropped; the 13:00–13:49 interleave was unrecoverable |
| `T14` | truncated tail ×2 | 2,101 rows, `gzip -t` OK | all rows recovered |

Structural prevention (not a retry loop): a **per-directory** `fcntl` lock,
`.recorder.lock`. A second writer now exits `3` and names the holder:

```
$ uv run hl_trades_ws.py --out-dir ../../d0/hyperliquid/BTC-USDC/trades --duration 3
HL-TRADES-RECORD-FAILED another recorder holds the lock in ../../d0/hyperliquid/BTC-USDC/trades (holder pid 35961)
RC=3
```

`flock` also guards the wrapper, and `uv run` is what cron invokes, so the lock
holds regardless of who launches the process.

**Tool bug found while doing this** (documented because it would have destroyed
good data): `decode_members` used `dec.unused_data` as the remainder after a
member ended, but that field is only the tail of the *last 64 KiB block fed* — so
everything past the first block was discarded. A **healthy** 71 KB segment read as
1,969 of 2,101 rows and `intact: false`; running the tool for real would have
rewritten it down. `gzip -dc` was the cross-check that exposed it. Fixed to
`dec.unused_data + rest[pos:]`, with a regression test that round-trips 20,010 rows
across two members (`len(member_a) > FEED`) and asserts equality with
`gzip.decompress`.

## 5 · Tick → 1m bars, checked against the venue

Aggregating ticks into 1m bars is only useful if the bars are *the market's* bars.
`python/collect/derive_1m_from_trades.py` does the aggregation (de-duplicating by
`tid` across **all** segments first), then compares every fully bracketed minute with
the venue's own 1m `candleSnapshot` — an independent source, at least while the ~3-day
retention holds.

The comparison is unforgiving on purpose: all six fields (`open`, `high`, `low`,
`close`, `volume`, `n_trades`) must agree exactly. Results:

| Measurement | Result |
|---|---|
| Clean single-writer run, 140 s, both closed minutes | **2/2 identical** — `n=181, vol=15.10354` and `n=210, vol=22.47295` |
| All four historical segments, minutes the venue also has | 45 compared → **27 exact, 18 mismatched** |
| Worst historical minute (`04:18Z`) | ticks captured **23 %** of venue volume — 77 % of prints missing |

So the recorder is **lossless while the socket stays up** (the clean run proves it),
and the historical losses are capture interruptions: the repaired hour, session
boundaries, and — measured, not assumed — any minute where a stream stalled inside a
window. This is why `covered` in the tool means only *"one contiguous window brackets
this minute"*, never *"every print is present"*; the venue comparison is what decides.

Operational rule that follows (fail-closed, no judgement calls):

> a tick-derived bar is published **only** when it matches the venue candle exactly on
> all six fields; every other minute is excluded and listed by `--verified-only`.

Run and result (parquet is local, not git-tracked):

```
uv run derive_1m_from_trades.py --cross-check --verified-only \
    --out ../../d0/hyperliquid/BTC-USDC/1m-ticks-verified.parquet --summary /tmp/x.json
→ prints 17,477 · compared 45 · exact 27 · kept 27 · excluded 18
   written: 27 rows, sha256 ba1b67019524eab75132b3de04f01dc0ad7af200cbb4cdb6e2ba6e35e66def91
```

No D3 row is registered for this yet. 27 venue-verified minutes cannot support any
statistic, and registering a dataset whose value is microstructure would invite exactly
the over-claiming this repo gates against. Ticks stay raw evidence until there is enough
of it to be worth a registered derivation.

## 6 · Known limitations (explicit)

1. **D3 gap calendar is day-granular.** A missing 1-minute bar inside a captured
   day is *not* representable as a gap row; only whole-day holes are. Current
   capture has zero missing slots, so nothing is concealed — but future intraday
   holes must be caught by the collector's own `missing_bar_slots` check, not the
   registry.
2. **Not enough bars to train yet.** The derived 1d projection currently has
   **2 bars** (11-02, 11-03); the lane refuses with `data too short for even one
   fold: 2 bars at folds=6 needs at least 300 bars`. Now that §2.1 makes the
   archive grow by one day per day, this is a matter of elapsed time rather than a
   design defect — but it is still ~10 months away, and no artifact can exist
   before then. The DSR/WF gate stays the promotion authority (0.4894 on the
   legacy daily row, below the 0.95 gate).
3. **Ticks are not a registered dataset.** They are raw append-only evidence
   under `d0/` with no D3 row: `verified=true` must never be claimed on a
   self-captured feed alone. The only admissible derivation is the venue-checked
   one in §5, and it currently yields 27 minutes — evidence, not a series.
4. **Tick coverage is partial by construction.** Capture started 2026-11-03 and
   has the 13 h 48 m gap above; any statistic over ticks must state its coverage
   window, never assume the series is continuous.

## 7 · Next steps

- let `scripts/hl_capture.sh` keep running (cron, every 15 min) so the 1m series
  and its derived 1d projection grow toward the 300-bar WF minimum;
- let `scripts/hl_trades.sh` keep recording ticks; re-run the §5 venue check
  periodically, because the 3-day candle retention means a minute that is not
  verified promptly can never be verified at all;
- only then extend the WF/eval gate to 1m bars on accumulated real data.
