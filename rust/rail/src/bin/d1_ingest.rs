//! W0a pin-2 helper: D1 (QuestDB) ingest exerciser. Feeds n deterministic
//! synthetic rows into the ILP-TCP socket using the production `rail::ilp`
//! writer — the same path live data will take. Deterministic row generator =
//! the determinism gate can compare two independent ingests.

use rail::ilp::IlpBuf;
use std::io::Write;
use std::net::TcpStream;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let usage = "usage: d1_ingest <n-rows> <table> [host:port=127.0.0.1:9009]";
    if args.len() < 3 {
        eprintln!("{usage}");
        std::process::exit(2);
    }
    let n: u64 = args[1].parse().unwrap_or(0);
    let table = args[2].clone();
    let addr = args.get(3).cloned().unwrap_or_else(|| "127.0.0.1:9009".to_string());
    if n == 0 {
        eprintln!("{usage}");
        std::process::exit(2);
    }
    let mut sock = match TcpStream::connect(&addr) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("d1_ingest: cannot connect {addr}: {e}");
            std::process::exit(2);
        }
    };
    let mut buf = IlpBuf::new(&table);
    let t0 = std::time::Instant::now();
    for i in 0..n {
        let ts = 1_772_000_000_000_000_000u64 + i * 1_000_000_000;
        let sym = ["NIFTY", "USDJPY", "BTC_USDT"][i as usize % 3];
        buf.push(sym, ts, ts + 1, 100.0 + i as f64 / 100.0);
    }
    // flush through the socket
    if let Err(e) = buf.flush_to(&mut sock) {
        eprintln!("d1_ingest: flush failed: {e}");
        std::process::exit(2);
    }
    // hard drop the socket: QuestDB treats connection close as commit checkpoint
    let _ = sock.shutdown(std::net::Shutdown::Write);
    let dt_ns = t0.elapsed().as_nanos() as u64;
    println!(
        "{{\"n\":{n},\"table\":\"{table}\",\"ingest_ns_total\":{dt_ns},\"ns_per_row\":{}}}",
        dt_ns / n.max(1)
    );
}
