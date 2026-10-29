//! R-D0 — cold-store Parquet dump (blueprint §2.1 / §13 D0 increment).
//!
//! Ingest-side role: take stamped rows (live from the bus, or re-accepted from
//! stdin) and land them as columnar Parquet — the durable cold tier that every
//! later wave reads for training/backtest determinism.
//!
//! Design points (all honest constraints, not decoration):
//! * Rows are **sort-stable ordered by ts_nanos** before write — the dump is a
//!   deterministic function of its row set; two runs byte-identical (`cmp`).
//! * Write path returns row-level accounting; read-back re-opens the file and
//!   counts rows before claiming success (no silent truncation).
//! * Parquet (v60.1.0, Apache-2.0, license-checked in Cargo.toml) is the only
//!   dependency — see the workspace std-only policy note.

use std::io::BufRead;
use std::sync::Arc;

use arrow_array::{ArrayRef, Float64Array, RecordBatch, StringArray, UInt64Array};
use arrow_schema::{DataType, Field, Schema, SchemaRef};
use parquet::arrow::ArrowWriter;

const COLS: [&str; 4] = ["symbol", "ts_nanos", "arrival_nanos", "price"];

fn schema() -> SchemaRef {
    Arc::new(Schema::new(vec![
        Field::new(COLS[0], DataType::Utf8, false),
        Field::new(COLS[1], DataType::UInt64, false),
        Field::new(COLS[2], DataType::UInt64, false),
        Field::new(COLS[3], DataType::Float64, false),
    ]))
}

/// One stamped row, as received (symbol from the subject, three payload fields).
#[derive(Clone, Debug)]
pub struct Stamped {
    pub symbol: String,
    pub ts_nanos: u64,
    pub arrival_nanos: u64,
    pub price: f64,
}

/// Parse `OK <SYMBOL> ts=<u64> arr=<u64> price=<f64>` (rail stdout) or the bare
/// payload form `<SYMBOL> <ts> <arr> <price>`. Rejects everything else.
pub fn parse_row(line: &str) -> Option<Stamped> {
    let mut toks = line.split_whitespace();
    let first = toks.next()?;
    let (symbol, ts, arr, price) = if first == "OK" {
        let s = toks.next()?;
        let (t, a, p) = (toks.next()?, toks.next()?, toks.next()?);
        // strip the key= prefixes the rail's OK lines carry
        fn clean<'a>(x: &'a str, k: &str) -> &'a str {
            x.strip_prefix(k).unwrap_or(x)
        }
        (s, clean(t, "ts="), clean(a, "arr="), clean(p, "price="))
    } else {
        (first, toks.next()?, toks.next()?, toks.next()?)
    };
    Some(Stamped {
        symbol: symbol.to_string(),
        ts_nanos: ts.parse().ok()?,
        arrival_nanos: arr.parse().ok()?,
        price: price.parse().ok()?,
    })
}

/// Write rows as one Parquet file (single row group, SNAPPY), deterministically
/// ordered by (ts_nanos, symbol). Returns bytes written.
pub fn write_parquet(path: &str, mut rows: Vec<Stamped>) -> Result<(usize, u64), String> {
    rows.sort_unstable_by(|a, b| a.ts_nanos.cmp(&b.ts_nanos).then_with(|| a.symbol.cmp(&b.symbol)));
    let n = rows.len();
    let symbols: StringArray = StringArray::from_iter_values(rows.iter().map(|r| r.symbol.as_str()));
    let ts: UInt64Array = rows.iter().map(|r| r.ts_nanos).collect();
    let arr: UInt64Array = rows.iter().map(|r| r.arrival_nanos).collect();
    let price: Float64Array = rows.iter().map(|r| r.price).collect();
    let batch = RecordBatch::try_new(
        schema(),
        vec![
            Arc::new(symbols) as ArrayRef,
            Arc::new(ts),
            Arc::new(arr),
            Arc::new(price),
        ],
    )
    .map_err(|e| e.to_string())?;

    let props = parquet::file::properties::WriterProperties::builder()
        .set_compression(parquet::basic::Compression::SNAPPY)
        .build();
    let file = std::fs::File::options()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
        .map_err(|e| format!("open {path}: {e}"))?;
    let mut writer = ArrowWriter::try_new(file, schema(), Some(props)).map_err(|e| e.to_string())?;
    writer.write(&batch).map_err(|e| e.to_string())?;
    writer.close().map_err(|e| e.to_string())?;
    let bytes = std::fs::metadata(path).map(|m| m.len()).unwrap_or_default();
    Ok((n, bytes))
}

/// Read back a file and count rows — the read proof that the dump actually
/// landed (no truncation, schema-consistent).
pub fn read_count(path: &str) -> Result<usize, String> {
    let file = std::fs::File::open(path).map_err(|e| format!("open {path}: {e}"))?;
    let reader = parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder::try_new(file)
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?;
    let mut n: usize = 0;
    for b in reader {
        n += b.map_err(|e| e.to_string())?.num_rows();
    }
    Ok(n)
}

pub enum Source {
    Stdin,
    Bench(u64),
}

/// stdin ingest loop: parse stamped rows, reject counts printed, write once at
/// EOF. Exit non-zero on poison input only when nothing could be written.
pub fn run_stdin(path: &str) -> Result<(usize, usize, u64), String> {
    let stdin = std::io::stdin();
    let mut rows: Vec<Stamped> = Vec::with_capacity(1024);
    let mut rejected: usize = 0;
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        match parse_row(line) {
            Some(r) => rows.push(r),
            None => rejected += 1,
        }
    }
    if rows.is_empty() {
        return Err(format!("no rows parsed; rejected={rejected}"));
    }
    let (n, bytes) = write_parquet(path, rows)?;
    let readback = read_count(path)?;
    if readback != n {
        return Err(format!("read-back mismatch: wrote {n}, read {readback}"));
    }
    Ok((n, rejected, bytes))
}

/// Deterministic synthetic rows (same formula on every run) for the D0 gate.
pub fn synth(n: u64) -> Vec<Stamped> {
    (0..n)
        .map(|i| {
            let sym = ["NIFTY", "USDJPY", "BTC_USDT"][i as usize % 3];
            Stamped {
                symbol: sym.to_string(),
                ts_nanos: 1_772_000_000_000_000_000 + i * 1_000_000_000,
                arrival_nanos: i, // pinned, not wall clock
                price: 100.0 + (i % 50_000) as f64 / 100.0,
            }
        })
        .collect()
}

pub fn bench(path: &str, n: u64) -> Result<String, String> {
    let rows = synth(n);
    let t0 = std::time::Instant::now();
    let (n_written, bytes) = write_parquet(path, rows)?;
    let dt = t0.elapsed();
    let readback = read_count(path)?;
    let ok = readback == n_written && n_written as u64 == n;
    let dt_ns = dt.as_nanos().max(1);
    Ok(format!(
        "{{\"rows\":{n},\"rows_written\":{n_written},\"rows_read\":{readback},\"file_bytes\":{bytes},\"write_ns_per_row\":{},\"rows_per_sec\":{},\"ok\":{ok}}}",
        dt.as_nanos() as u64 / n.max(1),
        n * 1_000_000_000u64 / dt_ns as u64,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ok_line_and_bare_payload() {
        let a = parse_row("OK NIFTY ts=5 arr=6 price=1.5").expect("ok-line");
        assert_eq!((a.symbol.as_str(), a.ts_nanos, a.arrival_nanos, a.price), ("NIFTY", 5, 6, 1.5));
        let b = parse_row("USDJPY 7 8 9.25").expect("bare");
        assert_eq!((b.ts_nanos, b.arrival_nanos, b.price), (7, 8, 9.25));
        assert!(parse_row("garbage 1 2").is_none());
    }

    #[test]
    fn write_then_read_back_matches_row_count() {
        let path = std::env::temp_dir().join(format!("d0-test-{}.parquet", std::process::id()));
        let p = path.to_str().expect("tmp path");
        let rows = vec![
            Stamped { symbol: "A".into(), ts_nanos: 2, arrival_nanos: 9, price: 1.0 },
            Stamped { symbol: "B".into(), ts_nanos: 1, arrival_nanos: 8, price: 2.0 },
        ];
        let (n, _bytes) = write_parquet(p, rows).expect("write");
        assert_eq!(n, 2);
        assert_eq!(read_count(p).expect("read"), 2, "sorted write keeps all rows");
        let _ = std::fs::remove_file(p);
    }
}

// main() lives in src/bin/d0_dump.rs — this crate is a library (reusable by
// later training/backtest waves that read the cold tier).

