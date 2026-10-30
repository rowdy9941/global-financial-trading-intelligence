//! ledger_recon — verify signatures by recomputation, report per-symbol gross
//! P&L, and land the recon verdict row. Everything error-surfaced; exit 0 only
//! when every signature checks out.
//!
//! Exit codes: 9 on mismatch, 2 on infrastructure failure.
use ledger::*;

fn main() {
    let mut c = match connect() {
        Ok(c) => c,
        Err(e) => { eprintln!("ledger_recon: connect failed: {e}"); std::process::exit(2); }
    };
    let (ok, total, bad) = match verify_fills(&mut c) {
        Ok(v) => v,
        Err(e) => { eprintln!("ledger_recon: verify_fills: {e}"); std::process::exit(2); }
    };
    let pnls = match gross_pnl(&mut c) {
        Ok(v) => v,
        Err(e) => { eprintln!("ledger_recon: gross_pnl: {e}"); std::process::exit(2); }
    };
    for (sym, n, gross) in &pnls {
        println!("PNL {sym} fills={n} gross={gross:.2}");
    }
    let payload = pnls.iter()
        .map(|(s, n, g)| format!("{s}:{n}:{g:.2}"))
        .collect::<Vec<_>>()
        .join(";");
    let net: f64 = pnls.iter().map(|(_, _, g)| g).sum();
    let nfills: i64 = pnls.iter().map(|(_, n, _)| n).sum();
    if let Err(e) = record_recon(&mut c, if ok { "match" } else { "mismatch" }, nfills, net, &format!("fills={total} bad_sigs={bad} pnl={payload}")) {
        eprintln!("ledger_recon: record_recon: {e}");
        std::process::exit(2);
    }
    println!("LEDGER-RECON {{\"fills\":{total},\"bad_sigs\":{bad},\"sig_ok\":{ok}}}");
    std::process::exit(if ok { 0 } else { 9 });
}
