//! R-D1 — QuestDB line-protocol (ILP) writer, std-only.
//!
//! Why hand-rolled: the hot kernel keeps its dependency surface at zero (W0a
//! policy). ILP is a text protocol sent over plain TCP; this implements the
//! exact subset the D1 store needs — table prefix, symbol/value columns, epoch
//! micros timestamp framing, percent-escaping of unsafe bytes — plus the
//! semantics D1 gates verify later against the real daemon (§13, pin 2).

use std::io::Write;
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

/// The live D1 table (A2 A-9 item 31). One name, one definition: `gfti d1 apply`
/// declares this table, the rail writes it, and the health view measures it.
pub const LIVE_TABLE: &str = "ticks";

/// Rows staged before a flush is due. D1 is a milliseconds store, not the hot
/// path — the pin is the *bus*, so the rail batches here instead of paying a
/// write syscall per tick.
pub const FLUSH_ROWS: u64 = 512;

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
/// QuestDB 7.4.2 grammar, re-verified live on 2026-11-06: numeric fields MUST carry
/// suffixes — `i` long, `d` double; a bare-trailing integer is the designated
/// timestamp and **the wire unit is nanoseconds**, not micros.
///
/// That unit was wrong here until A-9 and nothing noticed, because no gate read the
/// designated column: a row sent as `ts_nanos / 1_000` lands in the right order with
/// `ts_nanos` intact and a `ts` 56 years in the past. It matters as soon as `ticks`
/// declares `timestamp(ts) PARTITION BY DAY` — a live table whose designated
/// timestamp is garbage partitions into one ancient day, and every time-range scan
/// on D1 silently returns nothing.
pub fn ilp_line(
    table: &str,
    symbol: &str,
    ts_nanos: u64,
    arrival_nanos: u64,
    price: f64,
) -> String {
    format!(
        "{table},symbol={} ts_nanos={}i,arrival_nanos={}i,price={}d {}",
        esc_cell(symbol),
        ts_nanos,
        arrival_nanos,
        price,
        ts_nanos
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
        Self {
            table: table.to_string(),
            buf: String::with_capacity(64 * 1024),
            rows: 0,
        }
    }

    pub fn push(&mut self, symbol: &str, ts_nanos: u64, arrival_nanos: u64, price: f64) {
        self.buf.push_str(&ilp_line(
            &self.table,
            symbol,
            ts_nanos,
            arrival_nanos,
            price,
        ));
        self.buf.push('\n');
        self.rows += 1;
    }

    pub fn len(&self) -> u64 {
        self.rows
    }

    pub fn is_empty(&self) -> bool {
        self.rows == 0
    }

    /// Throw away staged rows without writing them. Used when a flush cannot be
    /// completed: the rows are a counted loss, and keeping them would let the next
    /// flush credit them a second time.
    pub fn discard(&mut self) {
        self.buf.clear();
        self.rows = 0;
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

/// The D1 write path itself (A2 A-9 item 31): the rail is D1's only writer, so
/// this is the only place live market data reaches `ticks`.
///
/// Three properties it must keep, each one measured by `d1_gates.py F`:
///
/// * **Fail-closed at boot.** `connect` is called before the first row is read; a
///   rail that cannot reach D1 exits instead of ingesting into nothing.
/// * **Off the critical path.** Rows are staged in memory and written in batches,
///   so the bus publish is not coupled to a store round trip.
/// * **Losses are counted, never swallowed.** A batch that cannot be written is
///   added to `failed` and dropped; `RAIL-INGEST` reports the number and the rail
///   exits non-zero, because "accepted" with rows missing from the store is a lie.
pub struct Sink {
    addr: String,
    stream: Option<TcpStream>,
    buf: IlpBuf,
    rows: u64,
    failed: u64,
    reconnects: u64,
}

impl Sink {
    /// Connect once, up front. A timeout bounds the wait so a wedged peer cannot
    /// hang the ingest loop forever.
    pub fn connect(addr: &str) -> std::io::Result<Self> {
        Ok(Self {
            addr: addr.to_string(),
            stream: Some(Self::open(addr)?),
            buf: IlpBuf::new(LIVE_TABLE),
            rows: 0,
            failed: 0,
            reconnects: 0,
        })
    }

    fn open(addr: &str) -> std::io::Result<TcpStream> {
        let mut last = std::io::Error::new(
            std::io::ErrorKind::NotFound,
            format!("{addr}: no address resolved"),
        );
        for sa in addr.to_socket_addrs()? {
            match TcpStream::connect_timeout(&sa, Duration::from_secs(5)) {
                Ok(s) => {
                    s.set_write_timeout(Some(Duration::from_secs(5)))?;
                    let _ = s.set_nodelay(true);
                    return Ok(s);
                }
                Err(e) => last = e,
            }
        }
        Err(last)
    }

    pub fn rows(&self) -> u64 {
        self.rows
    }

    pub fn failed(&self) -> u64 {
        self.failed
    }

    #[cfg(test)]
    pub fn reconnects(&self) -> u64 {
        self.reconnects
    }

    /// Stage one accepted tick. One string append — no I/O, no syscall.
    pub fn push(&mut self, symbol: &str, ts_nanos: u64, arrival_nanos: u64, price: f64) {
        self.buf.push(symbol, ts_nanos, arrival_nanos, price);
    }

    /// True when a batch is worth a write. The caller also flushes on its idle
    /// tick, so a slow feed still lands within one tick interval.
    pub fn due(&self) -> bool {
        self.buf.len() >= FLUSH_ROWS
    }

    /// Write the staged batch. Rows that cannot be written are counted once and
    /// dropped; the connection is discarded so the next flush dials again rather
    /// than retrying on a socket that may have taken half a batch.
    pub fn flush(&mut self) -> std::io::Result<u64> {
        let staged = self.buf.len();
        if staged == 0 {
            return Ok(0);
        }
        if self.stream.is_none() {
            match Self::open(&self.addr) {
                Ok(s) => {
                    self.stream = Some(s);
                    self.reconnects += 1;
                }
                Err(e) => {
                    self.buf.discard();
                    self.failed += staged;
                    return Err(e);
                }
            }
        }
        let sock = self
            .stream
            .as_mut()
            .expect("stream was opened directly above when absent");
        match self.buf.flush_to(sock) {
            Ok(n) => {
                self.rows += n;
                Ok(n)
            }
            Err(e) => {
                self.stream = None;
                self.buf.discard();
                self.failed += staged;
                Err(e)
            }
        }
    }

    /// A-7/A-9: the receipt line's fields. `null` when the sink is off.
    pub fn receipt(&self) -> String {
        format!(
            "{{\"table\":\"{LIVE_TABLE}\",\"rows\":{},\"failed\":{},\"reconnects\":{}}}",
            self.rows, self.failed, self.reconnects
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::{TcpListener, TcpStream};

    /// The D1 wire path, end-tested against a local fake server over TCP AND
    /// the real QuestDB grammar (suffixes `i`/`d`, nanos designated ts):
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
        buf.push(
            "NIFTY",
            1_772_000_000_000_001_895,
            1_772_000_000_000_001_900,
            101.25,
        );
        buf.push(
            "US D'Q",
            1_772_000_000_000_001_890,
            1_772_000_000_000_001_891,
            -0.5,
        ); // escaping + unordered ts
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
            "market_spot,symbol=NIFTY ts_nanos=1772000000000001895i,arrival_nanos=1772000000000001900i,price=101.25d 1772000000000001895"
        );
        let l2 = lines.next().expect("line2");
        assert_eq!(
            l2,
            "market_spot,symbol=US%20D\\'Q ts_nanos=1772000000000001890i,arrival_nanos=1772000000000001891i,price=-0.5d 1772000000000001890"
        );
        assert!(lines.next().is_none(), "exactly 2 lines, newline-framed");
    }

    /// The live sink: batch, write once, count what landed. This is the path D1
    /// actually takes (A-9 item 31), so it is tested as a sink and not only as a
    /// serializer.
    #[test]
    fn sink_batches_then_writes_exactly_the_staged_rows() {
        let l = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = l.local_addr().expect("addr").to_string();
        let server = std::thread::spawn(move || {
            let (mut s, _) = l.accept().expect("accept");
            s.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
            let mut got = String::new();
            let mut chunk = [0u8; 8192];
            while got.matches('\n').count() < 600 {
                match std::io::Read::read(&mut s, &mut chunk) {
                    Ok(0) => break,
                    Ok(n) => got.push_str(&String::from_utf8_lossy(&chunk[..n])),
                    Err(_) => break,
                }
            }
            got
        });

        let mut sink = Sink::connect(&addr).expect("connect");
        for i in 0..600u64 {
            sink.push("GATE_LIVE", 1_780_000_000_000_000_000 + i, i, 100.0);
        }
        assert!(sink.due(), "600 staged rows must exceed the {FLUSH_ROWS} batch");
        assert_eq!(sink.flush().expect("flush"), 600);
        assert_eq!(sink.rows(), 600);
        assert_eq!(sink.failed(), 0);
        assert!(!sink.due(), "a completed flush empties the buffer");
        // an empty flush is a no-op, not a stray write
        assert_eq!(sink.flush().expect("noop flush"), 0);

        let text = server.join().expect("server thread");
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines.len(), 600);
        assert!(lines[0].starts_with(&format!("{LIVE_TABLE},symbol=GATE_LIVE ")));
        assert!(lines[599].starts_with(&format!("{LIVE_TABLE},symbol=GATE_LIVE ")));
    }

    /// A batch that cannot be written is counted exactly once and never re-sent on
    /// a socket that may have taken half of it.
    #[test]
    fn sink_counts_one_failed_batch_and_does_not_double_count() {
        let l = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = l.local_addr().expect("addr").to_string();
        drop(l); // nobody listening → connect refused, the boot fail-closed path

        assert!(
            Sink::connect(&addr).is_err(),
            "connect to a dead D1 must fail, not silently start"
        );

        let mut sink = Sink {
            addr,
            stream: None,
            buf: IlpBuf::new(LIVE_TABLE),
            rows: 0,
            failed: 0,
            reconnects: 0,
        };
        for i in 0..3u64 {
            sink.push("GATE_LIVE", i, i, 1.0);
        }
        assert!(sink.flush().is_err());
        assert_eq!(sink.failed(), 3);
        assert_eq!(sink.rows(), 0);
        // nothing staged any more → a second attempt neither writes nor recounts
        assert_eq!(sink.flush().expect("empty"), 0);
        assert_eq!(sink.failed(), 3);
    }

    /// Encode sanity in CI (dev profile is UNOPTIMIZED — this only catches    /// algorithmic blowups like accidental O(n²) string rebuilds). The real
    /// hot-path budget is enforced in the release bench (bench_ilp) and filed
    /// in the gate doc.
    #[test]
    fn encode_sanity_no_quadratic_rebuild() {
        // Total-time budget rather than per-row: one scheduler preemption on a
        // busy dev box must not fail a perf-sanity test (W0b lesson).
        let t_all = std::time::Instant::now();
        for i in 0..1000u64 {
            let line = ilp_line(
                "market_spot",
                "BTC_USDT",
                1_772_000_000_000_000_000 + i,
                i,
                420.17,
            );
            assert!(!line.is_empty());
        }
        let dt = t_all.elapsed();
        // 1000 rows in < 50 ms ⇒ ~50 µs/row amortized — far from quadratic.
        assert!(
            dt.as_millis() < 50,
            "encode anomalous: 1000 rows in {dt:?} (dev profile)"
        );
    }
}
