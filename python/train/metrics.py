"""Cost-aware trading and risk metrics.

DSR certification is deliberately disabled until a published reference
implementation and its test vectors are checked into this project.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import mean, stdev

DSR_CERTIFICATION_BLOCKER = (
    "DSR certification blocked: no validated reference implementation/test vectors "
    "and no verified effective decorrelated trial count."
)


def evaluate_trades(
    trades: Sequence[dict],
    equity_curve: Sequence[float],
    periods_per_year: float,
    trials: int,
    *,
    periodic_returns: Sequence[float] | None = None,
    fold_returns: Sequence[Sequence[float]] | None = None,
    regime_returns: dict[str, Sequence[float]] | None = None,
    gap_metadata: dict | None = None,
) -> dict:
    """Calculate performance using net P&L (gross P&L less explicit friction).

    Trade rows require ``gross_pnl`` and may carry ``cost``, ``risk``,
    ``turnover`` and ``regime``. Costs must be nonnegative. DSR remains None
    and certification false until reference vectors and effective trial
    dependence are supplied by a validated implementation.
    """
    if periods_per_year <= 0 or trials < 0:
        raise ValueError("periods_per_year must be positive and trials non-negative")
    net: list[float] = []
    risks: list[float] = []
    turnover = 0.0
    regimes: dict[str, dict[str, int]] = {}
    for trade in trades:
        if "gross_pnl" not in trade:
            raise ValueError("each trade requires gross_pnl")
        gross, cost = float(trade["gross_pnl"]), float(trade.get("cost", 0.0))
        risk = float(trade.get("risk", 0.0))
        turn = float(trade.get("turnover", 0.0))
        if not all(math.isfinite(x) for x in (gross, cost, risk, turn)):
            raise ValueError("trade values must be finite")
        if cost < 0 or risk < 0 or turn < 0:
            raise ValueError("cost, risk and turnover must be non-negative")
        pnl = gross - cost
        net.append(pnl)
        risks.append(risk)
        turnover += turn
        regime = str(trade.get("regime", "unknown"))
        summary = regimes.setdefault(regime, {"trade_count": 0, "wins": 0})
        summary["trade_count"] += 1
        summary["wins"] += int(pnl > 0)

    wins = [value for value in net if value > 0]
    losses = [-value for value in net if value < 0]
    count = len(net)
    total_wins, total_losses = sum(wins), sum(losses)
    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean(losses) if losses else 0.0
    win_rate = len(wins) / count if count else 0.0
    risk_reward = avg_win / avg_loss if avg_loss else None
    breakeven = avg_loss / (avg_win + avg_loss) if avg_win + avg_loss else None
    profit_factor = (
        total_wins / total_losses
        if total_losses
        else (math.inf if total_wins else None)
    )
    r_values = [pnl / risk for pnl, risk in zip(net, risks) if risk > 0]
    expectancy_r = mean(r_values) if r_values else None

    if periodic_returns is not None:
        periodic = [float(value) for value in periodic_returns]
        if any(not math.isfinite(value) for value in periodic):
            raise ValueError("periodic returns must be finite")
    else:
        periodic = []
    periodic_mean = mean(periodic) if periodic else 0.0
    periodic_std = stdev(periodic) if len(periodic) > 1 else 0.0
    sharpe = (
        periodic_mean / periodic_std * math.sqrt(periods_per_year)
        if periodic_std
        else None
    )
    psr = _probabilistic_sharpe(periodic) if periodic else None
    fold_metrics = [
        _periodic_summary(values, periods_per_year) for values in fold_returns or ()
    ]
    regime_metrics = {
        name: _periodic_summary(values, periods_per_year)
        for name, values in (regime_returns or {}).items()
    }
    drawdown, underwater, recovery = _drawdown_metrics(equity_curve)
    cvar = _lower_tail_es(net)
    pnl_mean = mean(net) if count else 0.0
    return {
        "net_expectancy_per_trade": pnl_mean if count else None,
        "expectancy_r": expectancy_r,
        "win_rate": win_rate if count else None,
        "avg_win": avg_win if wins else None,
        "avg_loss": avg_loss if losses else None,
        "risk_reward": risk_reward,
        "breakeven_win_rate": breakeven,
        "profit_factor": profit_factor,
        "sharpe": sharpe,
        "psr": psr,
        "dsr": None,
        "certified": False,
        "certification_blocker": DSR_CERTIFICATION_BLOCKER,
        "max_drawdown": drawdown,
        "time_underwater": underwater,
        "recovery": recovery,
        "cvar_es": cvar,
        "turnover": turnover,
        "sample_count": count,
        "periodic_sample_count": len(periodic),
        "observation_semantics": (
            "equally_spaced_periodic_net_returns"
            if periodic_returns is not None
            else "per_trade_net_pnl; periodic risk metrics unavailable"
        ),
        "fold_metrics": fold_metrics,
        "regime_metrics": regime_metrics,
        "trial_count": trials,
        "effective_trial_count": None,
        "regimes": regimes,
        "gap_coverage": _gap_coverage(gap_metadata),
    }


def _probabilistic_sharpe(returns: Sequence[float]) -> float | None:
    """PSR against zero using unannualized Sharpe and Pearson kurtosis."""
    n = len(returns)
    if n < 2:
        return None
    sd = stdev(returns)
    if sd == 0:
        return None
    avg = mean(returns)
    centered = [x - avg for x in returns]
    m2 = sum(x**2 for x in centered) / n
    if m2 == 0:
        return None
    skew = (sum(x**3 for x in centered) / n) / m2**1.5
    kurtosis = (sum(x**4 for x in centered) / n) / m2**2
    sr = avg / sd
    variance = (1 - skew * sr + ((kurtosis - 1) / 4) * sr**2) / (n - 1)
    if variance <= 0:
        return None
    z = sr / math.sqrt(variance)
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _drawdown_metrics(
    equity: Sequence[float],
) -> tuple[float | None, int | None, int | None]:
    values = [float(value) for value in equity]
    if not values:
        return None, None, None
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("equity values must be finite and positive")
    peak = values[0]
    max_dd = 0.0
    underwater_now = 0
    longest_underwater = 0
    recovery_duration = 0
    current_drawdown_age = 0
    for value in values:
        if value >= peak:
            if current_drawdown_age:
                recovery_duration = max(recovery_duration, current_drawdown_age)
            peak = value
            underwater_now = 0
            current_drawdown_age = 0
        else:
            dd = (peak - value) / peak
            max_dd = max(max_dd, dd)
            underwater_now += 1
            current_drawdown_age += 1
            longest_underwater = max(longest_underwater, underwater_now)
    recovery_duration = max(recovery_duration, current_drawdown_age)
    return max_dd, longest_underwater, recovery_duration


def _lower_tail_es(values: Sequence[float], tail: float = 0.05) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    n_tail = max(1, math.ceil(len(ordered) * tail))
    return -mean(ordered[:n_tail])


def _periodic_summary(values: Sequence[float], periods_per_year: float) -> dict:
    returns = [float(value) for value in values]
    if any(not math.isfinite(value) for value in returns):
        raise ValueError("periodic returns must be finite")
    sd = stdev(returns) if len(returns) > 1 else 0.0
    return {
        "sample_count": len(returns),
        "sharpe": mean(returns) / sd * math.sqrt(periods_per_year) if sd else None,
        "psr": _probabilistic_sharpe(returns),
    }


def _gap_coverage(metadata: dict | None) -> float | None:
    """Return coverage only from explicit, verified expected/covered counts."""
    if not isinstance(metadata, dict) or metadata.get("verified") is not True:
        return None
    expected, covered = metadata.get("expected"), metadata.get("covered")
    if (
        not isinstance(expected, int)
        or isinstance(expected, bool)
        or expected <= 0
        or not isinstance(covered, int)
        or isinstance(covered, bool)
        or not 0 <= covered <= expected
    ):
        return None
    return covered / expected
