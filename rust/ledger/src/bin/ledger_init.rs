//! ledger_init — bootstrap D3/D4 schema + owner role from env.
use ledger::*;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let sql = args
        .get(1)
        .cloned()
        .unwrap_or_else(|| {
            let mut p = std::env::current_dir().expect("cwd");
            p.pop(); p.pop(); // rust/ledger → rust → repo
            p.join("compose/sql/001_d3_d4_init.sql").to_string_lossy().to_string()
        });
    let mut c = match connect() {
        Ok(c) => c,
        Err(e) => { eprintln!("ledger_init: connect failed: {e}"); std::process::exit(2); }
    };
    if let Err(e) = init(&mut c, &sql) {
        eprintln!("ledger_init: {e}");
        std::process::exit(2);
    }
    // owner palm role (secret from env only — never persisted)
    match std::env::var("LEDGER_SECRET_OWNER") {
        Ok(sec) if !sec.is_empty() => { let _ = ensure_role(&mut c, "owner", &sec); }
        _ => eprintln!("ledger_init: LEDGER_SECRET_OWNER unset — owner role not registered"),
    }
    println!("LEDGER-INIT ok sql={sql}");
}
