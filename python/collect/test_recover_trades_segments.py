"""Unit tests for recover_trades_segments.py — bytes in, rows out, no files."""

from __future__ import annotations

import gzip
import json

import pytest
import recover_trades_segments as rc


def _row(ts: int, tid: int | None = None, px: str = "77000.0") -> str:
    return json.dumps(
        {
            "ts_ms": ts,
            "px": px,
            "sz": "0.01",
            "side": "buy",
            "tid": tid if tid is not None else ts,
            "hash": "0xabc",
        },
        sort_keys=True,
    )


def _member(lines: list[str]) -> bytes:
    return gzip.compress(("\n".join(lines) + "\n").encode())


def test_intact_segment_is_reported_intact_and_unchanged():
    data = _member([_row(100), _row(200)])
    out = rc.recover_bytes(data)
    assert out["truncated_tail_dropped"] == 0
    assert out["rows_recovered"] == 2
    assert [r["ts_ms"] for r in out["rows"]] == [100, 200]


def test_truncated_tail_member_is_dropped_and_earlier_members_survive():
    data = _member([_row(100)]) + _member([_row(200)])[:20]  # half a member
    out = rc.recover_bytes(data)
    assert out["truncated_tail_dropped"] == 1
    assert out["members_decoded"] == 1
    assert out["rows_recovered"] == 1
    assert out["rows"][0]["ts_ms"] == 100


def test_duplicate_tids_are_collapsed_and_order_is_restored():
    data = _member([_row(300), _row(100), _row(100)])
    out = rc.recover_bytes(data)
    assert out["rows_read"] == 3
    assert out["duplicate_rows_dropped"] == 1
    assert [r["ts_ms"] for r in out["rows"]] == [100, 300]


def test_half_written_line_is_counted_not_crashed_on():
    data = _member([_row(100), '{"ts_ms": 200, "px": "77'])
    out = rc.recover_bytes(data)
    assert out["bad_lines_dropped"] == 1
    assert out["rows_recovered"] == 1


def test_empty_segment_is_not_an_error():
    out = rc.recover_bytes(b"")
    assert out["rows_recovered"] == 0
    assert out["members_decoded"] == 0


@pytest.mark.parametrize("px", ["77000.0", "0.00001"])
def test_decimal_strings_survive_recovery_verbatim(px):
    out = rc.recover_bytes(_member([_row(100, px=px)]))
    assert out["rows"][0]["px"] == px


def test_rewrite_is_deterministic_and_strictly_valid(tmp_path):
    path = tmp_path / "seg.jsonl.gz"
    rows = rc.recover_bytes(_member([_row(100), _row(200)]))["rows"]
    rc.write_segment(rows, path)
    first = path.read_bytes()
    rc.write_segment(rows, path)
    assert path.read_bytes() == first, "recovery must be idempotent"
    assert gzip.decompress(first).count(b"\n") == 2
    assert rc.recover_bytes(first)["truncated_tail_dropped"] == 0


def test_truncated_single_member_prefix_is_salvaged():
    """The real failure shape on 2026-11-03: one long-lived gzip handle killed
    mid-stream — 100% of one member, truncated. Its prefix must be recovered."""
    rows = [_row(1000 + i) for i in range(200)]
    data = _member(rows)
    cut = data[: int(len(data) * 0.7)]
    out = rc.recover_bytes(cut)
    assert out["truncated_tail_dropped"] == 1
    assert out["rows_recovered"] > 100, out
    assert out["rows_recovered"] < 200
    # recovered rows are a contiguous prefix, in order
    got = [r["ts_ms"] for r in out["rows"]]
    assert got == sorted(got)
    assert got[0] == 1000


def test_member_larger_than_the_feed_block_does_not_hide_later_members():
    """Regression (2026-11-04): `unused_data` is only the tail of the last block
    fed, so everything after the first 64 KiB was discarded — a HEALTHY 71 KB
    segment was reported truncated and would have been rewritten down to 1969 of
    2101 rows. Cross-check against `gzip.decompress`, which is the authority."""
    rows_a = [
        _row(1000 + i, tid=1000 + i, px=f"{70000 + i % 97}.0") for i in range(20_000)
    ]
    rows_b = [_row(900_000 + i, tid=900_000 + i) for i in range(10)]
    member_a = _member(rows_a)
    assert len(member_a) > rc.FEED, "the fixture must exceed one feed block"
    data = member_a + _member(rows_b)

    out = rc.recover_bytes(data)
    assert out["truncated_tail_dropped"] == 0
    assert out["members_decoded"] == 2
    assert out["rows_recovered"] == 20_010
    assert gzip.decompress(data).count(b"\n") == out["rows_recovered"]
