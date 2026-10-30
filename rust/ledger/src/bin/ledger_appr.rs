//! LEDGER-APPR — L1–L4 approval ticket flow (W0b skeleton, blueprint §6/C8).
//!
//! Tickets are REQUESTED into the D3 registry (`d3.tickets`) and DECIDED into
//! the append-only D4 ledger (`d4.approvals`). State derived: pending until a
//! decision row exists. Authority: L2+ decisions by `owner` only; L1 by any
//! registered seat (automated sandbox checks). C8: ≤5 L3 grants / rolling
//! 7 days, fail-closed.
//!
//! Usage:
//!   ledger_appr role <role_name>            (secret via LEDGER_SECRET_<ROLE>)
//!   ledger_appr request <ticket> <L1..L4> <requester_role> <reason...>
//!   ledger_appr decide <ticket> <grant|deny> <L1..L4> <decider_role> <reason...>
//!   ledger_appr state <ticket>
//!
//! Exit codes: 0 ok · 7 C8-QUOTA-EXCEEDED · 8 unauthorized decision
//!             9 already decided · 2 other errors
use std::process::exit;

/// Exit code for a domain error string (fail-closed mapping).
fn exit_for(e: &str) -> i32 {
    if e.contains("C8-QUOTA-EXCEEDED") {
        7
    } else if e.contains("unauthorized") {
        8
    } else if e.contains("already decided") {
        9
    } else {
        2
    }
}

fn bail(e: String) -> ! {
    eprintln!("LEDGER-APPR-REFUSED {e}");
    exit(exit_for(&e));
}

fn main() {
    use ledger::{
        approval_state, decide_approval, ensure_role, l3_week_grants, request_ticket, role_secret,
    };
    let a: Vec<String> = std::env::args().skip(1).collect();
    let mut c = match ledger::connect() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("ledger-appr: FAIL-CLOSED — cannot reach DB: {e}");
            exit(2);
        }
    };
    let cmd = a.first().map(String::as_str).unwrap_or("");
    match cmd {
        "role" => {
            let role = a.get(1).cloned().unwrap_or_default();
            let secret = role_secret(&role).filter(|s| !s.is_empty());
            let Some(secret) = secret else {
                eprintln!(
                    "ledger-appr: LEDGER_SECRET_{} not set",
                    role.to_uppercase().replace('-', "_")
                );
                exit(2);
            };
            if let Err(e) = ensure_role(&mut c, &role, &secret) {
                bail(e);
            }
            println!("LEDGER-APPR-ROLE {{\"role\":\"{role}\"}}");
        }
        "request" => {
            let f = |i: usize| a.get(i).cloned().unwrap_or_default();
            let (ticket, level, req_role, reason) = (f(1), f(2), f(3), f(4));
            if let Err(e) = request_ticket(&mut c, &ticket, &level, &req_role, &reason) {
                bail(e);
            }
            println!("LEDGER-APPR-REQUEST {{\"ticket\":\"{ticket}\",\"level\":\"{level}\",\"requester\":\"{req_role}\",\"state\":\"pending\"}}");
        }
        "decide" => {
            let f = |i: usize| a.get(i).cloned().unwrap_or_default();
            let (ticket, decision, level, dec_role, reason) = (f(1), f(2), f(3), f(4), f(5));
            let appr_id = match decide_approval(&mut c, &ticket, &decision, &level, &dec_role, &reason) {
                Ok(id) => id,
                Err(e) => bail(e),
            };
            let n = l3_week_grants(&mut c).unwrap_or(-1);
            println!("LEDGER-APPR-DECISION {{\"ticket\":\"{ticket}\",\"level\":\"{level}\",\"decision\":\"{decision}\",\"decider\":\"{dec_role}\",\"appr_id\":{appr_id},\"l3_week_grants\":{n}}}");
        }
        "state" => {
            let ticket = a.get(1).cloned().unwrap_or_default();
            match approval_state(&mut c, &ticket) {
                Ok((_pending, decision)) => println!(
                    "LEDGER-APPR-STATE {{\"ticket\":\"{ticket}\",\"state\":\"{}\"}}",
                    decision.as_deref().unwrap_or("pending")
                ),
                Err(e) => bail(e),
            }
        }
        other => {
            eprintln!("ledger-appr: unknown command {other:?}");
            exit(2);
        }
    }
}
