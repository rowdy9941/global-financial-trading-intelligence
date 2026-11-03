"""Unit tests for nse_fo_bhavcopy.py — fixture strings of the REAL file
formats (UDiFF schema and legacy HO schema, both captured from official
files), no network."""

from __future__ import annotations

import nse_fo_bhavcopy as nse
import pytest

UDIFF_CSV = """TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
2025-01-02,2025-01-02,NSE,1,STF,53213,,RELIANCE,FUTIDX,2025-02-27,2025-02-27,0.00,XX,STF 53213,1,2,0.5,1.5,1.5,1.4,1500,1.6,300,-10,8000,1200000,90,NO,250,x,,,,,
2025-01-02,2025-01-02,NSE,1,IDF,90001,,BANKNIFTY,IF,2025-01-30,2025-01-30,0.00,XX,BANKNIFTY FUT,1,2,0.5,1.5,1.5,1.4,52000,1.6,300,-10,8000,1200000,90,NO,15,x,,,,,
2025-01-02,2025-01-02,NSE,1,IDO,99998,INF204KA01B5,NIFTY,IF,2025-01-16,2025-01-16,24800.00,PE,NIFTY 24800 PE 16 JAN 2025,0.00,0.00,0.00,533.35,533.35,520.10,24188.65,620.50,75,0,0,0,0,NO,25,x,,,,,
2025-01-02,2025-01-02,NSE,1,IDO,99999,INF204KA01B6,NIFTY,IF,2025-01-16,2025-01-16,24000.00,CE,NIFTY 24000 CE 16 JAN 2025,0.00,0.00,0.00,0.00,0.00,520.10,24188.65,620.50,30,-5,0,0,0,NO,25,x,,,,,
"""

LEGACY_CSV = """INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,
FUTIDX,RELIANCE,30-Jan-2024,,,,,1200.65,,,5,6.2,1500,-10,01-FEB-2024,
FUTIDX,BANKNIFTY,30-Jan-2024,,,,,41800.00,,,44,32.1,300,-5,01-FEB-2024,
OPTIDX,NIFTY,01-Feb-2024,19500,CE,2250,2260,2195,2200.65,2200.65,88,80.5,150,-3,01-FEB-2024,
OPTIDX,NIFTY,01-Feb-2024,19600,PE,300,310,295,302.55,302.55,42,13.0,90,2,01-FEB-2024,
"""


def test_udiff_filters_index_options_only() -> None:
    rows = nse._udiff_rows(UDIFF_CSV, "2025-01-02")
    assert len(rows) == 2  # STF/IDF excluded
    r = rows[0]
    assert r["symbol"] == "NIFTY"
    assert r["expiry"] == "20250116"
    assert r["otype"] == "PE"
    assert r["underlying"] == "24188.65"
    assert r["oi"] == "75"  # int coercion happens in canonicalize


def test_legacy_filters_and_canonicalizes() -> None:
    rows = nse._legacy_rows(LEGACY_CSV, "2024-02-01")
    assert len(rows) == 2
    r = rows[0]
    assert r["otype"] == "CE"
    assert r["expiry"] == "20240201"
    assert r["oi"] == "150"  # int coercion happens in canonicalize


def test_legacy_contradictory_timestamp_aborts() -> None:
    days_wrong = LEGACY_CSV.replace(
        "OPTIDX,NIFTY,01-Feb-2024,19500,CE,2250,2260,2195,2200.65,2200.65"
        ",88,80.5,150,-3,01-FEB-2024,",
        "OPTIDX,NIFTY,01-Feb-2024,19500,CE,2250,2260,2195,2200.65,2200.65"
        ",88,80.5,150,-3,03-FEB-2024,",
    )
    with pytest.raises(RuntimeError, match="contradicts"):
        nse._legacy_rows(days_wrong, "2024-02-01")


def test_canonicalize_decimal_prices_and_int_fields() -> None:
    from decimal import Decimal

    for era, text, day in (
        ("udiff", UDIFF_CSV, "2025-01-02"),
        ("legacy", LEGACY_CSV, "2024-02-01"),
    ):
        rows = nse.canonicalize(text, day, era)
        r = rows[0]
        assert isinstance(r["strike"], Decimal) and isinstance(r["oi"], int)
        assert isinstance(r["volume"], int)
        assert r["ts_day"] == day
        assert r["src_row"] == 0
    legacy = nse.canonicalize(LEGACY_CSV, "2024-02-01", "legacy")
    assert legacy[0]["underlying"] is None and legacy[0]["trades"] is None


def test_urls_track_the_format_switch() -> None:
    u = nse.nse_url("2025-01-02")
    assert u.endswith("BhavCopy_NSE_FO_0_0_0_20250102_F_0000.csv.zip")
    l = nse.nse_url("2024-02-01")
    assert l.endswith("fo01FEB2024bhav.csv.zip") and "FEB" in l
    u2 = nse.nse_url("2024-07-08")  # first UDiFF trading day
    assert u2.endswith("BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv.zip")
    l2 = nse.nse_url("2024-07-05")  # last legacy trading day
    assert l2.endswith("fo05JUL2024bhav.csv.zip")


def test_absence_classification_failclosed(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    art = nse._classify_absences(["2024-03-01"], None)
    assert art[0]["justification"] == ""  # holiday master unreachable: open

    art2 = nse._classify_absences(
        ["2024-03-01", "2024-03-05"], ["2024-03-01", "2024-03-07"]
    )
    kinds = {h["window_from"]: h["justification"] for h in art2}
    assert "NSE trading holiday (holiday-master verified)" in kinds["2024-03-01"]
    assert kinds["2024-03-05"] == ""  # NOT a known holiday → open gap
