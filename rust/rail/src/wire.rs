//! Minimal NATS core-protocol client (std-only) for the rail.
//!
//! Why hand-rolled: the hot-path kernel keeps its dependency surface at zero
//! until a gate forces a real crate (W0a policy, rail/Cargo.toml). The NATS wire
//! protocol is text and small: this implements exactly what R1 needs —
//! connect (PING/PONG handshake), publish-with-ack, subscribe, receive.
//! No TLS/auth here (loopback foundation); production wiring re-verifies.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

pub struct Nats {
    reader: BufReader<TcpStream>,
    writer: TcpStream,
    buf: String,
}

impl Nats {
    /// Connect to `nats://host:port` and complete the PING/PONG handshake.
    pub fn connect(url: &str, name: &str) -> std::io::Result<Nats> {
        let addr = url
            .strip_prefix("nats://")
            .map(|s| s.to_socket_addrs())
            .transpose()?
            .ok_or_else(|| io_err("nats:// url required"))?
            .next()
            .ok_or_else(|| io_err("no addr"))?;
        let stream = TcpStream::connect_timeout(&addr, Duration::from_secs(3))?;
        stream.set_nodelay(true)?;
        let mut reader = BufReader::new(stream.try_clone()?);
        // Server greets with an INFO line first.
        let mut info = String::new();
        reader.read_line(&mut info)?;
        if !info.starts_with("INFO ") {
            return Err(io_err("expected INFO"));
        }
        let connect = format!(
            "CONNECT {{\"verbose\":false,\"pedantic\":false,\"tls_required\":false,\
             \"name\":\"{name}\",\"lang\":\"rust\",\"version\":\"0.1.0\",\"protocol\":1}}\r\n"
        );
        let mut stream_w = stream.try_clone()?;
        stream_w.write_all(connect.as_bytes())?;
        stream_w.write_all(b"PING\r\n")?;
        // wait through any INFO/PING chatter until the PONG
        skip_to_pong(&mut reader, &mut stream_w)?;
        Ok(Nats {
            reader,
            writer: stream_w,
            buf: String::with_capacity(128),
        })
    }

    /// Publish one payload and wait for the server to acknowledge it (PING→PONG).
    /// The PONG *is* the durable-ish ack for core NATS: the server has processed
    /// our bytes before we return. JetStream durability arrives in the next wave.
    pub fn publish(&mut self, subject: &str, payload: &[u8]) -> std::io::Result<()> {
        let head = format!("PUB {subject} {}\r\n", payload.len());
        self.writer.write_all(head.as_bytes())?;
        self.writer.write_all(payload)?;
        self.writer.write_all(b"\r\nPING\r\n")?;
        // reuse buffered reader for the PONG
        self.buf.clear();
        skip_to_pong(&mut self.reader, &mut self.writer)?;
        Ok(())
    }

    /// Subscribe (flushed via PING/PONG).
    pub fn subscribe(&mut self, subject: &str) -> std::io::Result<()> {
        let head = format!("SUB {subject} g1\r\n");
        self.writer.write_all(head.as_bytes())?;
        self.writer.write_all(b"PING\r\n")?;
        self.buf.clear();
        skip_to_pong(&mut self.reader, &mut self.writer)?;
        Ok(())
    }

    /// Blocking receive of the next message on the subscribed deliveries.
    /// Returns `(subject, payload)`. Responds to server PINGs with PONG.
    pub fn next_message(&mut self, subject_buf: &mut String, payload: &mut Vec<u8>) -> std::io::Result<()> {
        loop {
            self.buf.clear();
            self.reader.read_line(&mut self.buf)?;
            let line = self.buf.as_str();
            if let Some(msgpart) = line.strip_prefix("MSG ") {
                let f: Vec<&str> = msgpart.split_whitespace().collect();
                // MSG <subject> <sid> <size>  [reply] \r\n
                let size: usize = f
                    .iter().next_back().copied()
                    .and_then(|s| s.parse().ok())
                    .ok_or_else(|| io_err("bad MSG line"))?;
                subject_buf.clear();
                subject_buf.push_str(f[0]);
                payload.resize(size + 2, 0);
                self.reader.read_exact(payload)?;
                payload.truncate(size);
                return Ok(());
            }
            if line.starts_with("PING") {
                self.writer.write_all(b"PONG\r\n")?;
            }
        }
    }
}

/// NATS 2.15 can interleave INFO lines anywhere (e.g. right after the first
/// SUB). Every ack path must wait through them until PONG.
fn skip_to_pong<R: BufRead, W: Write>(reader: &mut R, writer: &mut W) -> std::io::Result<()> {
    loop {
        let mut l = String::new();
        reader.read_line(&mut l)?;
        if l.starts_with("PONG") {
            return Ok(());
        }
        if l.starts_with("PING") {
            writer.write_all(b"PONG\r\n")?;
        }
    }
}

fn io_err(msg: &str) -> std::io::Error {
    std::io::Error::other(msg)
}
