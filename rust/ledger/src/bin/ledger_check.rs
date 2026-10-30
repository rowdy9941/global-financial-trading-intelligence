//! ledger_check — engine-shaped kill voter: exit 0 = risk allowed, 7 = refuse.
//! DB unreachable => ENGAGED (exit 7): the fail-closed drill case.
use ledger::*;

fn main() {
    let mut c = match connect() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("LEDGER-FAIL-CLOSED db unreachable: {e}");
            eprintln!("LEDGER-CHECK {{\"state\":\"failsafe_engaged\",\"risk_allowed\":false}}");
            std::process::exit(7);
        }
    };
    match kill_state(&mut c) {
        Kill::Open => {
            println!("LEDGER-CHECK {{\"state\":\"open\",\"risk_allowed\":true}}");
            std::process::exit(0);
        }
        Kill::Engaged { level, reason, source } => {
            println!("LEDGER-CHECK {{\"state\":\"engaged\",\"level\":\"{level}\",\"source\":\"{source}\",\"reason\":{:?},\"risk_allowed\":false}}", reason);
            std::process::exit(7);
        }
        Kill::FailsafeEngaged => {
            println!("LEDGER-CHECK {{\"state\":\"failsafe_engaged\",\"risk_allowed\":false}}");
            std::process::exit(7);
        }
    }
}
