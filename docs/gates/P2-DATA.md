# P2 data — NSE NIFTY options (measured 2026-11-03)

Pilot 2 (A0 §4) is the **NSE NIFTY option chain**. This note records what was
actually probed from this box, what is blocked and why, and what would unblock it.
No identity is frozen here.

## 1 · What works: official EOD contract-level data

`python/collect/nse_fo_bhavcopy.py` (UDiFF era from 2024-07-08, legacy era before)
collects the official NSE F&O bhavcopy and filters to NIFTY index options. Real probe:

```
uv run nse_fo_bhavcopy.py --start 2026-10-28 --until 2026-10-30 \\
    --out /tmp/nifty_probe.parquet --summary /tmp/nifty_probe.json
→ rows 5,060  first_day 2026-10-28  last_day 2026-10-30
  integrity PASS  holes []  holiday_master_reachable true
  scope "NIFTY index options only (UDiFF era IDO, legacy era OPTIDX)"
```

So contract-level history (strike, expiry, type, OHLC, OI, volume) is obtainable
and is a legitimate source for **research/labels**. It is **not** used to claim the
live chain identity (see §2): it is one end-of-day snapshot per contract, with no
bid/ask and no intraday state.

## 2 · What is blocked: the live option chain

```
curl -A "Mozilla/5.0" "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
→ HTTP 404
```

The NSE website's chain endpoint (which the public site calls after a cookie
handshake) does not serve this client from this box. This is an access-control
result, not an outage: the archive hosts are reachable from the same machine in the
same run (`nsearchives.nseindia.com` UDiFF zips returned 200, holiday master
reachable). P2's frozen requirement is the **live chain** (bid/ask per strike,
OI deltas, both legs) — EOD bhavcopy cannot substitute for it, and A0 excludes
relabelling one as the other.

## 3 · What would unblock it

A broker API which serves the NSE option chain to an authenticated account —
**Upstox** is the candidate (its `/v2/option/chain` endpoint covers NSE indices and
provides bid/ask, OI, greeks). Required from the owner:

1. an Upstox API key + secret and an authenticated access token (OAuth login must be
   performed by the owner; an agent cannot complete a browser login);
2. a decision on the data licence class for the broker feed (it is contractually
   distinct from NSE public dissemination);
3. confirmation that storing per-minute chain snapshots is permitted by the broker
   agreement.

Until 1–3 exist, P2's identity stays **unfrozen** and no P2 dataset row is
registered. The G-PILOT-E2E fixture arm already proves the P2 code path
(`symbol=NIFTY`) end to end on labelled synthetic data — the gap is data, not code.

## 4 · Not claimed

- no live NIFTY chain data has been captured;
- the 5,060-row bhavcopy probe is **not** registered in D3 (it was a 3-day probe to
  the wrong granularity for the chain identity);
- nothing here says the strategy is profitable; the DSR gate stays the authority.
