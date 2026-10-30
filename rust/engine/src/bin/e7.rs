//! E7 ledger cell skeleton — separate async process per §2.1: consumes
//! `order.spot.<SYM>` order intents produced by E6, appends SIGNED fill rows
//! to the D4 append-only ledger (owner role via LEDGER_SECRET_OWNER env).
use ledger::*;
use rail::wire::Nats;
use std::io::BufRead;

fn main() {
    let nats_url = std::env::var("RAIL_NATS_URL").unwrap_or_else(|_| "nats://127.0.0.1:4222".into());
    let subj = std::env::var("E7_SUBJECT").unwrap_or_else(|_| "order.spot.>".into());
    let want: u64 = std::env::var("E7_EXPECT").ok().and_then(|s| s.parse().ok()).unwrap_or(1);

    let mut nats = match Nats::connect(&nats_url, "engine-e7") {
        Ok(n) => n,
        Err(e) => { eprintln!("e7: FAIL-CLOSED — no bus {nats_url}: {e}"); std::process::exit(2); }
    };
    if let Err(e) = nats.subscribe(&subj) {
        eprintln!("e7: subscribe fail: {e}"); std::process::exit(2);
    }
    let mut c = match connect() {
        Ok(c) => c,
        Err(e) => { eprintln!("e7: ledger connect fail: {e}"); std::process::exit(2); }
    };
    let std_in = std::io::stdin();
    let mut line = String::new();
    let _ = std_in.lock().read_line(&mut line); // gate handshake: "GO" or "" (skip)

    let mut symsubj = String::new();
    let mut payload: Vec<u8> = Vec::new();
    let mut got = 0u64;
    while got < want {
        match Nats::next_message(&mut nats, &mut symsubj, &mut payload) {
            Ok(()) => {}
            Err(e) => { eprintln!("e7: recv fail: {e}"); std::process::exit(2); }
        }
        let text = String::from_utf8_lossy(&payload).to_string();
        let f: Vec<&str> = text.split_whitespace().collect();
        if f.len() != 4 {
            eprintln!("e7: skip malformed intent {text:?}");
            continue;
        }
        let (side, qty_s, px_s, dec_id) = (f[0].to_string(), f[1].to_string(), f[2].to_string(), f[3].to_string());
        let symbol = symsubj.rsplit('.').next().unwrap_or("UNKNOWN").to_string();
        let qty: f64 = match qty_s.parse() { Ok(q) => q, Err(_) => continue };
        let px: f64 = match px_s.parse() { Ok(p) => p, Err(_) => continue };
        // fail-closed: refuse intents while the kill state is not open
        if !matches!(kill_state(&mut c), Kill::Open) {
            eprintln!("e7: REFUSE intent dec={dec_id} — kill state not open (fail-closed)");
            std::process::exit(7);
        }
        match append_fill(&mut c, "cell-e6", &symbol, &side, qty, px, "owner") {
            Ok((fill_id, sig)) => {
                println!("LEDGER-E7-FILL {{\"dec_id\":{dec_id},\"symbol\":\"{symbol}\",\"fill_id\":{fill_id},\"sig16\":\"{}\"}}", &sig[..16]);
                use std::io::Write as _;
                let _ = std::io::stdout().flush();
            }
            Err(e) => { eprintln!("e7: append fail: {e}"); std::process::exit(2); }
        }
        got += 1;
    }
    eprintln!("LEDGER-E7-DONE {{\"fills\":{got},\"expected\":{want}}}");
}
