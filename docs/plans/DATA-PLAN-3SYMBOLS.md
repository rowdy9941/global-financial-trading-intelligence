# Data plan — E2E proof on 3 symbols (NIFTY, BTC, GOLD)

Status: **LOCKED** (user decisions 2026-11-02). Scope of every data download and
(non-LLM) training run until the user lifts it: exactly these 3 symbols.
LLM seats are Claude-Code-only. The full clone→compose→collect→train→verify
path must run on Windows/macOS/Linux.

## Locked sources (with probe evidence, 2026-11-02)

| Symbol | Source | Pattern verified live |
|---|---|---|
| **BTC** | Hyperliquid **BTC-USD perp** (Jan 2024→present) via REST `candleSnapshot`, key-free | `POST api.hyperliquid.xyz/info {"type":"candleSnapshot","req":{"coin":"BTC","interval":"1d",…}}` → 200; **full history back to perp inception 2023-11-09** in one ≤5000-candle call |
| **NIFTY** | Official NSE **F&O UDiFF bhavcopy** (options: strikes/expiries/OI/premiums) | `https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip` → 200 (1.13 MB, valid zip, 2025-01-02 file) |
| **NIFTY legacy** | Pre-2024-07-08 only: `https://archives.nseindia.com/content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip` | → 200 (Feb 2024 file, valid zip) |
| **GOLD** | cTrader Open API (locked O2 venue) — **needs owner OpenAPI app credentials** | blocked; fallback while waiting (see below) |

Old `fo01JAN2025bhav.csv.zip` / `…foDDMMMYYYYbhav.csv.zip` on
`nsearchives.nseindia.com/content/fo/` are **discontinued (NSE circular 62424,
July 2024)** — the 404 is expected, not a network failure.

## Decisions recorded during the probe

1. **Hyperliquid spot BTC does not exist.** `spotMeta` (349 pairs / 511 tokens,
   probed) exposes no native BTC/USDC — only third-party wrapped `UBTC/USDC`
   (`@142`, thin liquidity). The user-locked "BTC/USDC perp + spot" therefore
   resolves to the **BTC-USD perp only** as the real, deep BTC venue on
   Hyperliquid. Wrapped UBTC is explicitly *not* used. The requester-pays S3
   archives (`hyperliquid-archive`, `hl-mainnet-node-data`) are **out of scope**
   (require AWS credentials + billing charges; fills-only, no candles).
2. **NSE downloads need browser-like `User-Agent`** (curl with plain `Mozilla/5.0`
   works today; keep the collector UA + retry).
3. **`claude` CLI**: not installed (the `fcc-*` binaries in `~/.local/bin` are an
   *unofficial* "free Claude Code" proxy — rejected for the seat lane).
   Official `@anthropic-ai/claude-code` npm package is the required runtime.

## GOLD fallback (while cTrader credentials are pending)

Free XAU/USD (t+0) daily archive from HISTDATA.COM (public GDOFD files) —
attribution-required license class. Registered as its own source; training
targets an IC-broker-quality venue later via cTrader once provisioned.

## Collector plan (order)

1. `hyperliquid_daily.py` — BTC perp (1d, paginated `startTime` windows),
   Decimal px/vol, int64-ns timestamps, parquet → D0 + `register_b1`.
2. `nse_fo_bhavcopy.py` — UDiFF zip (2024-07-08→present) + legacy zip
   (2020→2024-07-05), filtered to NIFTY instruments, options contracts only,
   As-Of keys per (strike/expiry/type/day) preserving as-reported PIT.
3. `gold_histdata.py` skeleton — XAUUSD 1d, license `open_free_attribution`.
