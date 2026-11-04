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
    pub fn next_message(
        &mut self,
        subject_buf: &mut String,
        payload: &mut Vec<u8>,
    ) -> std::io::Result<()> {
        match self.next_message_timeout(subject_buf, payload, None)? {
            true => Ok(()),
            // unreachable with timeout=None: fill_line only yields false when a
            // timeout is set
            false => Err(io_err("receive returned no message without a timeout")),
        }
    }

    /// Like [`Nats::next_message`], but returns `Ok(false)` when `timeout`
    /// elapses with no message. This is the shape a resident service needs: its
    /// loop can observe a shutdown flag instead of parking forever in a blocking
    /// read (A2 §4 A-1).
    ///
    /// Partial lines survive a timeout — the accumulator lives in `self.buf`, so
    /// an MSG header split across TCP segments can never desync the stream.
    pub fn next_message_timeout(
        &mut self,
        subject_buf: &mut String,
        payload: &mut Vec<u8>,
        timeout: Option<Duration>,
    ) -> std::io::Result<bool> {
        loop {
            if let Some((s, p)) = self.pending.pop_front() {
                subject_buf.clear();
                subject_buf.push_str(&s);
                *payload = p;
                return Ok(true);
            }
            if !self.fill_line(timeout)? {
                return Ok(false);
            }
            let line = std::mem::take(&mut self.buf);
            if line.starts_with("PING") {
                self.writer.write_all(b"PONG\r\n")?;
                continue;
            }
            if let Some(msgpart) = line.strip_prefix("MSG ") {
                subject_buf.clear();
                subject_buf.push_str(msgpart.split_whitespace().next().unwrap_or(""));
                // The header already arrived; a stalled body means a dead link,
                // so the body read blocks rather than half-timing-out.
                self.reader.get_ref().set_read_timeout(None)?;
                self.read_msg_body(msgpart, payload)?;
                return Ok(true);
            }
            // INFO / HMSG / -ERR: ignored, as in the pre-A-1 loop.
        }
    }

    /// Read until `self.buf` holds one complete (`\n`-terminated) line.
    /// `Ok(false)` = timeout before a newline; partial bytes are kept in `buf`.
    fn fill_line(&mut self, timeout: Option<Duration>) -> std::io::Result<bool> {
        self.reader.get_ref().set_read_timeout(timeout)?;
        loop {
            if self.buf.ends_with('\n') {
                return Ok(true);
            }
            let mut chunk = String::new();
            let r = self.reader.read_line(&mut chunk);
            // Append BEFORE inspecting the error: a timed-out read may still have
            // delivered bytes, and dropping them would desync the protocol.
            if !chunk.is_empty() {
                self.buf.push_str(&chunk);
            }
            match r {
                Ok(0) => return Err(io_err("connection closed by peer")),
                Ok(_) => {}
                Err(e)
                    if e.kind() == std::io::ErrorKind::WouldBlock
                        || e.kind() == std::io::ErrorKind::TimedOut =>
                {
                    return Ok(false);
                }
                Err(e) => return Err(e),
            }
        }
    }

    /// Parse an already-read `MSG` header line and read its payload (plus the
    /// trailing CRLF) off the wire. `msgpart` = text after "MSG ".
    fn read_msg_body(&mut self, msgpart: &str, payload: &mut Vec<u8>) -> std::io::Result<()> {
        let f: Vec<&str> = msgpart.split_whitespace().collect();
        // MSG <subject> <sid> <size>  [reply] \r\n
        let size: usize = f
            .iter()
            .next_back()
            .copied()
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
            // An ack blocks: the PONG either arrives or the link is broken.
            self.fill_line(None)?;
            let line = std::mem::take(&mut self.buf);
            if line.starts_with("PONG") {
                return Ok(());
            }
            if line.starts_with("PING") {
                self.writer.write_all(b"PONG\r\n")?;
                continue;
            }
            if let Some(msgpart) = line.strip_prefix("MSG ") {
                let subject = msgpart.split_whitespace().next().unwrap_or("").to_string();
                let mut p = Vec::new();
                self.reader.get_ref().set_read_timeout(None)?;
                self.read_msg_body(msgpart, &mut p)?;
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
            let n: usize = line
                .split_whitespace()
                .last()
                .and_then(|s| s.trim().parse().ok())
                .unwrap_or(0);
            assert_eq!(n, 9, "client published 9 bytes");
            let mut body = vec![0u8; n + 2];
            b.read_exact(&mut body).expect("pub body");
            b.read_line(&mut line).expect("pub ping");
            s.write_all(b"MSG market.spot.X g1 12\r\n")
                .expect("deliver head");
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

    /// A-1 regression: a resident service must be able to ask "anything yet?" and
    /// get `false` instead of blocking forever, and a MSG header split across TCP
    /// segments by the timeout must not desync the stream.
    #[test]
    fn timeout_returns_idle_and_preserves_a_split_line() {
        let l = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = l.local_addr().expect("addr");
        let t = std::thread::spawn(move || {
            let (mut s, _) = l.accept().expect("accept");
            use std::io::{BufRead, Write as IoWrite};
            let mut b = std::io::BufReader::new(s.try_clone().expect("clone"));
            let mut line = String::new();
            s.write_all(b"INFO {}\r\n").expect("greet");
            b.read_line(&mut line).expect("connect");
            b.read_line(&mut line).expect("connect ping");
            s.write_all(b"PONG\r\n").expect("ack connect");
            b.read_line(&mut line).expect("sub");
            b.read_line(&mut line).expect("sub ping");
            s.write_all(b"PONG\r\n").expect("ack sub");
            // let the client's first read time out
            std::thread::sleep(Duration::from_millis(120));
            // then deliver a MSG header SPLIT across two writes
            s.write_all(b"MSG market.spot.Y").expect("head part 1");
            s.flush().expect("flush");
            std::thread::sleep(Duration::from_millis(60));
            s.write_all(b" g1 3\r\nabc\r\n")
                .expect("head part 2 + body");
            s.flush().expect("flush");
            std::thread::sleep(Duration::from_millis(150));
        });
        let mut n = Nats::connect(&format!("nats://{addr}"), "t").expect("connect");
        n.subscribe("market.spot.>").expect("sub");
        let mut subj = String::new();
        let mut pay = Vec::new();
        let t0 = std::time::Instant::now();
        assert!(
            !n.next_message_timeout(&mut subj, &mut pay, Some(Duration::from_millis(50)))
                .expect("idle read"),
            "an idle socket must report idle, not a message"
        );
        assert!(
            t0.elapsed() < Duration::from_millis(600),
            "the idle read must return on its timeout"
        );
        let got = loop {
            if n.next_message_timeout(&mut subj, &mut pay, Some(Duration::from_millis(80)))
                .expect("second read")
            {
                break true;
            }
            if t0.elapsed() > Duration::from_secs(5) {
                break false;
            }
        };
        assert!(got, "the split MSG header was lost across the timeout");
        assert_eq!(subj, "market.spot.Y");
        assert_eq!(pay.as_slice(), b"abc");
        assert!(t.join().is_ok());
    }
}
