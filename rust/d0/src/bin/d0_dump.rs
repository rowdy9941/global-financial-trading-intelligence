//! d0_dump — Parquet cold-store writer CLI (R-D0). See the library for the
//! contract: deterministic ordering, read-back verified, SNAPPY.

use d0::{bench, run_stdin};
use std::io::Write;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let usage = "usage: d0_dump --stdin <out.parquet> | --bench <out.parquet> <rows>";
    let resp = match args.len() {
        3 if args[1] == "--stdin" => match run_stdin(&args[2]) {
            Ok((n, rej, bytes)) => {
                format!("D0 {{\"rows_written\":{n},\"rejected\":{rej},\"file_bytes\":{bytes}}}")
            }
            Err(e) => format!("D0-FAIL {e}"),
        },
        4 if args[1] == "--bench" => {
            let n: u64 = args[3].parse().unwrap_or_default();
            if n == 0 {
                eprintln!("{usage}");
                std::process::exit(2);
            }
            match bench(&args[2], n) {
                Ok(s) => {
                    println!("D0-BENCH {s}");
                    std::process::exit(0);
                }
                Err(e) => format!("D0-FAIL {e}"),
            }
        }
        _ => {
            eprintln!("{usage}");
            std::process::exit(2);
        }
    };
    let mut out = std::io::stdout().lock();
    writeln!(out, "{resp}").ok();
    let _ = out.flush();
}
