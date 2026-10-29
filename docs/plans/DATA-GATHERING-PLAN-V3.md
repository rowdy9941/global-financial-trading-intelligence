---
tags: [GFI, data, todo, plan, worldwide, historical]
aliases: [data-plan-v3, gathering-plan-v3]
created: 2026-10-24
---

# Data-Gathering Plan v3 — dataset-grain worldwide TODO (historical included)

> **Method (this build).** 12 parallel domain-expert agents + 2 verified library censuses
> (CCXT `exchanges.json`, AKShare docs) → 13-column dataset-grain TODO rows
> (`| # | dataset | class | source & route | granularity | history depth | latency |
> integration pattern | license class | quality/PIT gates | wave | TODO action | verification |`),
> machine-merged, deduped, schema-validated into
> [[Research/data/todo/DATASET-TODO|DATASET-TODO]] (+ PDF, landscape) and
> `Research/data/extras/data-catalog.jsonl`. **No dataset downloads** — doc pages only.
> Row verdicts: VERIFIED-UPSTREAM-DOC **357** · SURVEY-EVIDENCE **295** · UNVERIFIED-CANDIDATE **348** (each row labelled; verified rows carry per-volume URL footnotes). Wave mix: W0 **311** / W1 **294** / W2 **243** / W3 **152**. 1,000 dataset rows across
> 12 volumes.

## 1. Goal and scope framing

The catalogue targets a collection surface that unions **every tradeable venue class, every
filings jurisdiction, and the non-market dataset classes that single vendors do not combine**
(registries, litigation, sanctions, patents, clinical, procurement, power-grid ops, shipping,
satellites, own-capture moats) — with **historical/past availability stated on every row**
(years + whether the backfill is obtainable now). Where a dataset cannot be obtained today,
the row says so and names the wave/gate that unlocks it (`quote`, `license-review`).

## 2. Integration patterns (how a row lands in the system)

| pattern | mechanics | auth / rate budget | landing format |
|---|---|---|---|
| REST bulk / paging | page-walk with checkpoint table (`dataset_key`, last pointer) | API key + per-minute cap tracked per token | parquet staging → L0 |
| WS stream | persistent sockets, reconnect-safe seq gaps | per-conn instrument budget (e.g. Dhan 5×5 000) | append-only event table |
| S3-archive walk | day-partitioned object listing → ranged GET | keyless or signed | file → parquet + hash |
| SFTP / file-drop | schedule-based directory diff | cred rotation | file → schema-checked table |
| BigQuery / table export | read API cron, column-pruned | quota caps | parquet via Arrow |
| library (CCXT / akshare / yfinance) | versioned wrapper configs, function=endpoint map | shared politeness budget | normalized OHLCV/event frame |
| own-node / subgraph | chain sync + eth_getLogs / subgraph polling | disk budget, RPC caps | derived tables only (PB-raw never stored) |
| own-capture | cron fetcher + fetch-ts + raw-blob store | politeness + robots respect | treasure L0 with Two-Clicks lineage |
| licensed terminal / feed | vendor SDK, per-seat | seat + redistribution contract | internal-only landing (no-local-store respected) |
| STAC / object search | catalog search → item fetch | public | GeoParquet/COG |

Storage spine stays vol-10's `L0 VAULT → L1 REFINERY → L2a/b/c BRAIN → L3 FACES`: append-only
parquet + hash + versioned, golden-file tested marts, factors/events/edges leaves, one face.

## 3. Cross-cutting hard gates

1. **Two-Clicks lineage** — every table keeps source-URL + fetch-ts + parser-version columns.
2. **Honest freshness** — declared latency + *upstream publication lag*; freshness class per row.
3. **License is a build gate** — free / internal-only / commercial-redistribution-ok /
   no-local-store / vendor-license / ⚠-blocked-never-commercial. `no-local-store` rows never
   persist as files; NSE scraping rows are ⚠-blocked-never-commercial (display path = TrueData/
   GlobalDatafeeds-class W1 rows).
4. **PIT / survivorship** — ⚠NEVER-PIT rows (FMP/EODHD/Tiingo/SimFin overwrite-in-place) never
   feed the backtest factor spine; survivorship-correct universes (norgate/CRSP-class) gate any
   factor backtest.
5. **Same skeleton** — 13-column row contract everywhere; new sources enter as rows, not code.
6. **QA in the merge** — schema check (13 cols), integer #, wave/action/verification allow-lists,
   garble scan, dedupe on canonical dataset key (dupes dropped with build-log note).

## 4. Backfill order (what unblocks what)

| order | domain | sizes (owner-claim sizing) | unblocks |
|---|---|---|---|
| 1 | benchmark bundle < 5 GB (Pink Sheet, EIA, IMF/WB, FAO, OWID, WHO) | instant | cross-edge joins |
| 2 | India eq + F&O bhavcopy archives (1994/2000→) | ~150 GB | India depth product |
| 3 | crypto tick crown-jewel (binance.vision top-300 ≈ 600 GB; on-chain derived) | L0-alpha | microstructure research |
| 4 | FX tick (Dukascopy 2003→) + fixes | ~50 GB | intraday FX |
| 5 | GDELT retro news spine (1979→) + day-1 self-archive RSS rails | index-scale | event factors |
| 6 | filings (EDGAR full, EDINET, DART XBRL, Companies House, cninfo) | ~30 GB | fundamentals PIT |
| 7 | power/grid + weather grids | streaming+history | infra factors |
| 8 | registries/legal/IP/clinical/procurement archives | ~10 GB | moat joins (L2c edges) |

## 5. Census instruments (verified this session)

- **CCXT `exchanges.json`** — **101 exchange ids with unified REST + 71 with WebSocket** verified live from upstream `exchanges.json` (2026-10-24), plus a native **prediction** interface family covering 9 venues (polymarket, kalshi, limitless, myriad, opinion, predictfun, sxbet-class) — closes most of the prediction-market gap without separate vendor work.
- **AKShare docs** — open Python interface library for stocks/futures/options/funds/FX/bonds/
  indices/crypto from public sources, per-endpoint docs, HTTP wrapper (AKTools); docs updated
  2026-10-23. Academic-research framing — treat redistribution like scraped-source re-syndication
  (attribute, no commercial resale of raw rows).
- 12 agent volumes with per-row verification labels and ≤30-fetch budgets each.

## 6. The TODO table

The actionable artefact: [[Research/data/todo/DATASET-TODO|DATASET-TODO.md]] —
1,000 rows, one per dataset, with `TODO action` per row
(connect / backfill / verify / quote / license-review / build-parser). PDF:
`Research/data/todo/DATASET-TODO.pdf` (landscape). Domain volumes:

| volume | domain | rows | volume file |
|---|---|--:|---|
| 01 | India equities & F&O | 50 | [[Research/data/todo/01-india-equities|01-*.md]] |
| 02 | Asia-Pacific equities | 90 | [[Research/data/todo/02-india-equities|02-*.md]] |
| 03 | US equities & microstructure | 81 | [[Research/data/todo/03-india-equities|03-*.md]] |
| 04 | EU & global-rest equities | 72 | [[Research/data/todo/04-india-equities|04-*.md]] |
| 05 | Fundamentals · filings · PIT | 121 | [[Research/data/todo/05-india-equities|05-*.md]] |
| 06 | Derivatives (options/futures) | 84 | [[Research/data/todo/06-india-equities|06-*.md]] |
| 07 | FX · rates · macro · credit · ESG | 103 | [[Research/data/todo/07-india-equities|07-*.md]] |
| 08 | Commodities · energy · power · shipping | 86 | [[Research/data/todo/08-india-equities|08-*.md]] |
| 09 | Alternative data | 75 | [[Research/data/todo/09-india-equities|09-*.md]] |
| 10 | Registries · legal · IP · clinical · procurement | 72 | [[Research/data/todo/10-india-equities|10-*.md]] |
| 11 | Crypto full-stack | 90 | [[Research/data/todo/11-india-equities|11-*.md]] |
| 12 | Cross-domain historical archives | 76 | [[Research/data/todo/12-india-equities|12-*.md]] |

## 7. Verdicts (honest counts)

- VERIFIED-UPSTREAM-DOC 357 rows (live-fetched pages this session, URLs footnoted per volume).
- SURVEY-EVIDENCE 295 rows (traced to the committed 108-dossier corpus/matrix).
- UNVERIFIED-CANDIDATE 348 rows — candidates with `verify`/`quote` actions, labelled as such.
- Build-log: 0 schema-malformed rows (all 1,000 parse 13/13 columns) schema problems, 1 (FINRA daily short-sale volume — kept in the 03 market-structure volume, dropped from 05) duplicates dropped (transparent, in build log).

## 8. Annexes

- [[Research/data/ALPHALENS-INTAKE|ALPHALENS-INTAKE]] — requirements source (6 classes, spine, hard rules)
- [[Research/data/DATA-SOURCE-MATRIX|DATA-SOURCE-MATRIX]] / [[Research/data/DATA-SOURCE-MATRIX-V2|V2]] — vendor-grain lineage (273 rows)
- `Research/data/streams/01…06` — the first-pass vendor census feeding V2
- `Research/data/extras/data-catalog.jsonl` — this plan's machine-readable rows
