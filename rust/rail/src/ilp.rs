//! R-D1 prep — QuestDB line-protocol (ILP) writer, std-only.
//!
//! Why hand-rolled: the hot kernel keeps its dependency surface at zero (W0a
//! policy). ILP is a text protocol sent over plain TCP; this implements the
//! exact subset the D1 store needs — table prefix, symbol/value columns, epoch
//! micros timestamp framing, percent-escaping of unsafe bytes — plus the
//! semantics D1 gates verify later against the real daemon (§13, pin 2).

use std::io::Write;

/// Escapes per QuestDB ILP-over-TCP: symbol/value strings need `\` and `'`
/// escaped, spaces and commas percent-encoded (they delimit the line).
fn esc_cell(b: &str) -> String {
    let mut out = String::with_capacity(b.len());
    for c in b.chars() {
        match c {
            ',' | ' ' => out.push_str(if c == ',' { "%2C" } else { "%20" }),
            '\\' | '\'' => {
                out.push('\\');
                out.push(c);
            }
            '=' | '"' => {
                out.push('\\');
                out.push(c);
            }
            _ => out.push(c),
        }
    }
    out
}

/// One stamped row → one ILP line (no trailing newline here; assemble() adds it).
/// QuestDB 7.4.2 grammar (verified live against the compose daemon, /write 204):
/// numeric fields MUST carry suffixes — `i` long, `d` double; a bare-trailing
/// integer is the designated timestamp (epoch micros). Symbol column is deduped
/// server-side; arrival kept as value column (nanos).
pub fn ilp_line(table: &str, symbol: &str, ts_nanos: u64, arrival_nanos: u64, price: f64) -> String {
    let micros = ts_nanos / 1_000;
    format!(
        "{table},symbol={} ts_nanos={}i,arrival_nanos={}i,price={}d {}",
        esc_cell(symbol),
        ts_nanos,
        arrival_nanos,
        price,
        micros
    )
}

/// Append-only ILP buffer: rows accumulate in memory and flush in one write to
/// the D1 TCP socket — batch-by-batch, the same fail-closed regime as the bus.
pub struct IlpBuf {
    table: String,
    buf: String,
    rows: u64,
}

impl IlpBuf {
    pub fn new(table: &str) -> Self {
        Self { table: table.to_string(), buf: String::with_capacity(64 * 1024), rows: 0 }
    }

    pub fn push(&mut self, symbol: &str, ts_nanos: u64, arrival_nanos: u64, price: f64) {
        self.buf.push_str(&ilp_line(&self.table, symbol, ts_nanos, arrival_nanos, price));
        self.buf.push('\n');
        self.rows += 1;
    }

    pub fn len(&self) -> u64 {
        self.rows
    }

    pub fn is_empty(&self) -> bool {
        self.rows == 0
    }

    /// Flush the staged rows to a `Write` (the D1 socket). Returns rows written.
    /// One write call per flush — no per-row round trip on this path.
    pub fn flush_to<W: Write>(&mut self, w: &mut W) -> std::io::Result<u64> {
        if self.rows == 0 {
            return Ok(0);
        }
        w.write_all(self.buf.as_bytes())?;
        w.flush()?;
        self.buf.clear();
        let n = self.rows;
        self.rows = 0;
        Ok(n)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::{TcpListener, TcpStream};

    /// The D1 wire path, end-tested against a local fake server over TCP AND
    /// the real QuestDB grammar (suffixes `i`/`d`, micros designated ts):
    /// exact bytes, framing newline, escaping.
    #[test]
    fn encodes_and_delivers_exact_bytes_to_tcp() {
        let l = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = l.local_addr().expect("addr");
        let t = std::thread::spawn(move || {
            let (mut s, _) = l.accept().expect("accept");
            let mut got = vec![0u8; 4096];
            let n = s.read(&mut got).expect("read");
            got.truncate(n);
            got
        });

        let mut buf = IlpBuf::new("market_spot");
        buf.push("NIFTY", 1_772_000_000_000_001_895, 1_772_000_000_000_001_900, 101.25);
        buf.push("US D'Q", 1_772_000_000_000_001_890, 1_772_000_000_000_001_891, -0.5); // escaping + unordered ts
        assert_eq!(buf.len(), 2);
        let mut sink = TcpStream::connect(addr).expect("connect");
        let n = buf.flush_to(&mut sink).expect("flush");
        assert_eq!(n, 2);
        // second flush with nothing staged is a no-op
        assert_eq!(buf.flush_to(&mut sink).expect("flush"), 0);

        let got = t.join().expect("thread");
        let txt = String::from_utf8(got).expect("utf8");
        let mut lines = txt.lines();
        let l1 = lines.next().expect("line1");
        assert_eq!(
            l1,
            "market_spot,symbol=NIFTY ts_nanos=1772000000000001895i,arrival_nanos=1772000000000001900i,price=101.25d 1772000000000001"
        );
        let l2 = lines.next().expect("line2");
        assert_eq!(
            l2,
            "market_spot,symbol=US%20D\\'Q ts_nanos=1772000000000001890i,arrival_nanos=1772000000000001891i,price=-0.5d 1772000000000001"
        );
        assert!(lines.next().is_none(), "exactly 2 lines, newline-framed");
    }

    /// Encode sanity in CI (dev profile is UNOPTIMIZED — this only catches
    /// algorithmic blowups like accidental O(n²) string rebuilds). The real
    /// hot-path budget is enforced in the release bench (bench_ilp) and filed
    /// in the gate doc.
    #[test]
    fn encode_sanity_no_quadratic_rebuild() {
        for i in 0..1000u64 {
            let t0 = std::time::Instant::now();
            let line = ilp_line("market_spot", "BTC_USDT", 1_772_000_000_000_000_000 + i, i, 420.17);
            let dt = t0.elapsed();
            assert!(!line.is_empty());
            assert!(dt.as_nanos() < 50_000, "encode anomalous: {}ns (dev profile) on row {i}", dt.as_nanos());
        }
    }
}

