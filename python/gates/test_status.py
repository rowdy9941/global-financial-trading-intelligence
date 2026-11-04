"""Tests for the G-STATUS aggregator (`status.py`).

`status.py` is the arbiter of every "gate green" claim in this repo: it reads each
runner's own pass marker and printed JSON and decides the verdict. A regression here
silently turns a failing gate green, which is the one failure mode the whole project
is built to avoid — so it gets tests of its own.

Motivation, measured 2026-11-04: W0B FANOUT returned 1.52 ms and 2.50 ms p99 against
the same 2.00 ms budget in different runs. A bare `FANOUT=True`/`False` cannot tell
"passed with headroom" from "passed by 20 µs" from "missed the budget under load" from
"the rail dropped prints". `explain()` must report the numbers on both outcomes, and
must never alter the verdict itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import status


def _log(tmp_path: Path, *, ok: bool, worst_ns: int, delivered: bool) -> str:
    cells = [
        {
            "subject": "market.spot.NIFTY",
            "received": 5000 if delivered else 4999,
            "n": 5000,
            "p50": 320_000,
            "expected": 5000,
            "ok": ok,
        }
    ]
    payload = {
        "rows_published": 20000,
        "cells": cells,
        "worst_p99_ns": worst_ns,
        "budget_ns": 2_000_000,
        "ok": ok,
    }
    path = tmp_path / "w0b_gates.py-ALL.log"
    path.write_text(f"noise\nW0B-G5-FANOUT {json.dumps(payload)}\nSUMMARY {{}}\n")
    return str(path)


def _part(ok: bool, log: str) -> status.Part:
    return status.Part(label="w0b FANOUT (G5)", ok=ok, detail=str(ok), log=log)


def test_explain_reports_numbers_when_fanout_passes(tmp_path):
    """A pass must still carry its margin — this gate is marginal by design."""
    out = status.explain(
        _part(True, _log(tmp_path, ok=True, worst_ns=1_520_000, delivered=True)),
        "W0B-G5-FANOUT",
    )
    assert out.ok is True
    assert "1.52 ms" in out.detail
    assert "2.00 ms budget" in out.detail
    assert "all rows delivered" in out.detail


def test_explain_distinguishes_budget_miss_from_lost_rows(tmp_path):
    """2.50 ms with all rows is a contention problem; missing rows is a rail bug."""
    out = status.explain(
        _part(False, _log(tmp_path, ok=False, worst_ns=2_501_986, delivered=False)),
        "W0B-G5-FANOUT",
    )
    assert out.ok is False, "explain() must never change the verdict"
    assert "ROWS MISSING" in out.detail
    assert "2.50 ms" in out.detail


def test_explain_marks_budget_miss_with_all_rows_delivered(tmp_path):
    out = status.explain(
        _part(False, _log(tmp_path, ok=False, worst_ns=2_501_986, delivered=True)),
        "W0B-G5-FANOUT",
    )
    assert "all rows delivered" in out.detail
    assert out.ok is False


def test_explain_leaves_part_untouched_without_a_log():
    part = status.Part(label="w0b FANOUT (G5)", ok=False, detail="False", log="")
    assert status.explain(part, "W0B-G5-FANOUT").detail == "False"


def test_explain_ignores_an_unparsable_payload(tmp_path):
    """Never invent numbers: a truncated log leaves the verdict and text alone."""
    path = tmp_path / "w0b_gates.py-ALL.log"
    path.write_text("W0B-G5-FANOUT {not json\n")
    part = status.Part(label="w0b FANOUT (G5)", ok=False, detail="False", log=str(path))
    out = status.explain(part, "W0B-G5-FANOUT")
    assert out.detail == "False"
    assert out.ok is False
