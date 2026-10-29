//! W0a e2e consumer: subscribe to a subject, receive N messages, report
//! transit (recv_nanos − arrival_nanos carried in the payload) and re-publish
//! nothing. Used by docs/gates/W0a.md pin 3 (wire) e2e measurement.

use rail::wire::Nats;
use std::process::exit;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (subject, want, url) = match args.len() {
        4 => (args[1].clone(), args[2].parse::<u32>().expect("n msgs"), args[3].clone()),
        _ => {
            eprintln!("usage: rail-sub <subject> <n-msgs> <nats-url>");
            exit(2);
        }
    };
    let mut nats = match Nats::connect(&url, "rail-sub") {
        Ok(n) => n,
        Err(e) => { eprintln!("sub: cannot connect {url}: {e}"); exit(2); }
    };
    if let Err(e) = nats.subscribe(&subject) {
        eprintln!("sub: cannot subscribe: {e}");
        exit(2);
    }
    let mut subject_rx = String::with_capacity(64);
    let mut payload = Vec::with_capacity(64);
    let mut got: u32 = 0;
    let mut transits: Vec<u64> = Vec::with_capacity(want as usize * 2);
    while got < want {
        if nats.next_message(&mut subject_rx, &mut payload).is_err() {
            eprintln!("sub: receive error");
            exit(2);
        }
        got += 1;
        let line = String::from_utf8_lossy(&payload);
        // payload = "ts arrival price" — transit = now − arrival
        if let Some(arr) = line.split_whitespace().nth(1).and_then(|s| s.parse::<u64>().ok()) {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u64)
                .unwrap_or(0);
            transits.push(now.saturating_sub(arr));
        }
    }
    transits.sort_unstable();
    let n = transits.len();
    let avg = transits.iter().sum::<u64>() / n.max(1) as u64;
    let p50 = transits[(n as f64 * 0.50) as usize % n.max(1)];
    let p99 = transits[(n as f64 * 0.99) as usize % n.max(1)];
    eprintln!("WIRE-E2E {{\"received\":{got},\"n\":{n},\"transit_ns_avg\":{avg},\"p50\":{p50},\"p99\":{p99}}}");
    println!("received={got}");
}
