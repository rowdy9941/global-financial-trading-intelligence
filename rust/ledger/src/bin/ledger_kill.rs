//! ledger_kill — flip the kill cascade (append-only event row).
use ledger::*;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (action, level, reason, url_ok) = (
        args.get(1).cloned().unwrap_or_default(),
        args.get(2).cloned().unwrap_or_else(|| "L0".into()),
        args.get(3).cloned().unwrap_or_else(|| "unspecified".into()),
        std::env::var("LEDGER_URL").is_ok(),
    );
    let _ = url_ok;
    let mut c = match connect() {
        Ok(c) => c,
        Err(e) => { eprintln!("ledger_kill: connect failed: {e}"); std::process::exit(2); }
    };
    let res = match action.as_str() {
        "engage" => kill_flip(&mut c, true, &level, "gate-drill", &reason),
        "resolve" => kill_flip(&mut c, false, "L4", "gate-drill", &reason),
        _ => Err("usage: ledger_kill <engage|resolve> [level] [reason]".into()),
    };
    if let Err(e) = res { eprintln!("ledger_kill: {e}"); std::process::exit(2); }
    println!("LEDGER-KILL {action} level={level}");
}
