//! Minimal NATS core-protocol client (std-only) for the rail.
//!
//! Why hand-rolled: the hot-path kernel keeps its dependency surface at zero
//! until a gate forces a real crate (W0a policy, rail/Cargo.toml). The NATS wire
//! protocol is text and small: this implements exactly what R1 needs —
//! connect (PING/PONG handshake), publish-with-ack, subscribe, receive.
//! No TLS/auth here (loopback foundation); production wiring re-verifies.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

pub struct Nats {
    reader: BufReader<TcpStream>,
    writer: TcpStream,
    buf: String,
    /// Messages delivered on our subscriptions while an ack path waited for a
    /// PONG — they are REAL deliveries, never discarded (a cell that publishes
    /// mid-stream would otherwise drop inbound ticks; W0b bug, unit-covered).
    pending: VecDeque<(String, Vec<u8>)>,
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
        let mut nats = Nats {
            reader,
            writer: stream_w,
            buf: String::with_capacity(128),
            pending: VecDeque::new(),
        };
        nats.wait_pong()?;
        Ok(nats)
    }

    /// Publish one payload and wait for the server to acknowledge it (PING→PONG).
    /// The PONG *is* the durable-ish ack for core NATS: the server has processed
    /// our bytes before we return. JetStream durability arrives in the next wave.
    pub fn publish(&mut self, subject: &str, payload: &[u8]) -> std::io::Result<()> {
        let head = format!("PUB {subject} {}\r\n", payload.len());
        self.writer.write_all(head.as_bytes())?;
        self.writer.write_all(payload)?;
        self.writer.write_all(b"\r\nPING\r\n")?;
        // The PONG is the ack; deliveries seen en route are queued, not lost.
        self.wait_pong()?;
        Ok(())
    }

    /// Subscribe (flushed via PING/PONG).
    pub fn subscribe(&mut self, subject: &str) -> std::io::Result<()> {
        let head = format!("SUB {subject} g1\r\n");
        self.writer.write_all(head.as_bytes())?;
        self.writer.write_all(b"PING\r\n")?;
        self.wait_pong()?;
        Ok(())
    }

    /// One PING flushes the whole batch — NATS answers exactly one PONG per PING, so the single
    /// PONG acks every frame in the batch (fail-closed at batch granularity).
    /// For backfill/replay paths; the live rail keeps per-message acks.
    pub fn publish_batch(&mut self, batch: &[(String, Vec<u8>)]) -> std::io::Result<()> {
        for (subject, payload) in batch {
            let head = format!("PUB {subject} {}\r\n", payload.len());
            self.writer.write_all(head.as_bytes())?;
            self.writer.write_all(payload)?;
            self.writer.write_all(b"\r\n")?;
        }
        self.writer.write_all(b"PING\r\n")?;
        self.wait_pong()?;
        Ok(())
    }

    /// Blocking receive of the next message on the subscribed deliveries.
    /// Returns `(subject, payload)`. Responds to server PINGs with PONG.
    /// Deliveries that arrived while an ack path waited go FIRST (FIFO),
    /// preserving server-side delivery order.
    pub fn next_message(&mut self, subject_buf: &mut String, payload: &mut Vec<u8>) -> std::io::Result<()> {
        loop {
            if let Some((s, p)) = self.pending.pop_front() {
                subject_buf.clear();
                subject_buf.push_str(&s);
                *payload = p;
                return Ok(());
            }
            self.buf.clear();
            self.reader.read_line(&mut self.buf)?;
            if let Some(msgpart) = self.buf.strip_prefix("MSG ").map(|s| s.to_string()) {
                let f: Vec<&str> = msgpart.split_whitespace().collect();
                subject_buf.clear();
                subject_buf.push_str(f[0]);
                self.read_msg_body(&msgpart, payload)?;
                return Ok(());
            }
            if self.buf.starts_with("PING") {
                self.writer.write_all(b"PONG\r\n")?;
            }
        }
    }

    /// Parse an already-read `MSG` header line and read its payload (plus the
    /// trailing CRLF) off the wire. `msgpart` = text after "MSG ".
    fn read_msg_body(&mut self, msgpart: &str, payload: &mut Vec<u8>) -> std::io::Result<()> {
        let f: Vec<&str> = msgpart.split_whitespace().collect();
        // MSG <subject> <sid> <size>  [reply] \r\n
        let size: usize = f
            .iter().next_back().copied()
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| io_err("bad MSG line"))?;
        payload.resize(size + 2, 0);
        self.reader.read_exact(payload)?;
        payload.truncate(size);
        Ok(())
    }

    /// Acks wait for PONG while PRESERVING deliveries: MSG lines seen en route
    /// are parsed and queued into `pending` (FIFO), never dropped.
    fn wait_pong(&mut self) -> std::io::Result<()> {
        loop {
            self.buf.clear();
            self.reader.read_line(&mut self.buf)?;
            if self.buf.starts_with("PONG") {
                return Ok(());
            }
            if self.buf.starts_with("PING") {
                self.writer.write_all(b"PONG\r\n")?;
                continue;
            }
            if let Some(msgpart) = self.buf.strip_prefix("MSG ").map(|s| s.to_string()) {
                let f: Vec<&str> = msgpart.split_whitespace().collect();
                let subject = f[0].to_string();
                let mut p = Vec::new();
                self.read_msg_body(&msgpart, &mut p)?;
                self.pending.push_back((subject, p));
                continue;
            }
            // INFO / HMSG etc. — ignored
        }
    }
}

fn io_err(msg: &str) -> std::io::Error {
    std::io::Error::other(msg)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// W0b regression: while a subscriber waits for a publish-ack PONG, the
    /// server may deliver MSG lines on the same connection. They must be QUEUED
    /// (FIFO), never discarded — otherwise a cell publishing mid-stream drops
    /// its inbound ticks.
    #[test]
    fn deliveries_en_route_to_a_pong_are_queued_not_dropped() {
        // fake server: handshake; SUB handshake; then for the client's publish:
        // deliver an interleaved market MSG *before* the PONG.
        let l = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = l.local_addr().expect("addr");
        let payload = b"ts arr price"; // 12 bytes
        let t = std::thread::spawn(move || {
            let (mut s, _) = l.accept().expect("accept");
            use std::io::{BufRead, Write as IoWrite};
            let mut b = std::io::BufReader::new(s.try_clone().expect("clone"));
            let mut line = String::new();
            // greeting FIRST (client waits for INFO before sending CONNECT)
            s.write_all(b"INFO {}\r\n").expect("greet");
            // CONNECT line + PING line
            b.read_line(&mut line).expect("connect");
            b.read_line(&mut line).expect("connect ping");
            s.write_all(b"PONG\r\n").expect("ack connect");
            // SUB line + PING
            b.read_line(&mut line).expect("sub");
            b.read_line(&mut line).expect("sub ping");
            s.write_all(b"PONG\r\n").expect("ack sub");
            // the publish: parse "PUB <subj> <n>CRLF", read n-byte payload + CRLF,
            // then the PING line.
            b.read_line(&mut line).expect("pub head");
            let n: usize = line.split_whitespace().last().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
            assert_eq!(n, 9, "client published 9 bytes");
            let mut body = vec![0u8; n + 2];
            b.read_exact(&mut body).expect("pub body");
            b.read_line(&mut line).expect("pub ping");
            s.write_all(b"MSG market.spot.X g1 12\r\n").expect("deliver head");
            s.write_all(payload).expect("deliver body");
            s.write_all(b"\r\n").expect("deliver crlf"); // MSG body terminator!
            s.write_all(b"PONG\r\n").expect("ack publish");
        });
        let mut n = Nats::connect(&format!("nats://{addr}"), "t").expect("connect");
        n.subscribe("market.spot.>").expect("sub");
        n.publish("order.spot.X", b"buy 1 2 0").expect("pub");
        let mut subj = String::new();
        let mut pay = Vec::new();
        n.next_message(&mut subj, &mut pay).expect("recv");
        assert_eq!(subj, "market.spot.X");
        assert_eq!(pay.as_slice(), payload);
        assert!(t.join().is_ok());
    }
}
