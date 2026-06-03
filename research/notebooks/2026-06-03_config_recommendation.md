# Config Recommendation — Narrow Universe + Fix Position Sizing

**Date:** 2026-06-03
**Analyst:** Claude (research layer)
**Status:** Awaiting user approval — **NOT applied.** These are Railway env-var changes for the user to make.

---

> Per `CLAUDE.md`: recommendations live here; `config.py`/Railway env is changed only by the user.
> PAPER throughout. This does NOT mean "go live" — the 90-day reliability gate still applies.

---

## Summary

Two Railway environment-variable changes, both evidence-backed:

| Variable | Current (live) | **Recommended** | Why |
|----------|----------------|-----------------|-----|
| `WATCHLIST` | `SPY,QQQ,NVDA,AAPL,TSLA` | **`SPY,QQQ`** | Only SPY/QQQ have edge; the single stocks are flat-to-losing (TSLA negative gross). |
| `MVP_POSITION_NOTIONAL_USD` | `750` | **`1600`** | At $750 a SPY share (~$758) rounds to **qty 0** → SPY never trades. $1,600 → 2 whole shares of SPY/QQQ. |

Optional/marginal (your call, within noise): `RSI_HIGH` `70 → 65` — highest-Sharpe in the validation study, and it would have rejected Tuesday's losing NVDA entry (RSI 70.0). Not required.

## Evidence

**Universe (why SPY,QQQ only)** — `2026-05-30_universe_cost_viability.md`, walk-forward 11 OOS folds, 13mo IEX:
| Symbol | Gross Sharpe | Gross Ret% | Verdict |
|--------|--------------|-----------|---------|
| SPY | 0.223 | +4.47 | viable |
| QQQ | 0.131 | +3.59 | viable |
| AAPL | 0.04 | +0.86 | flat — dies after cost |
| NVDA | 0.01 | +0.30 | no edge |
| TSLA | −0.095 | **−6.47** | loses GROSS, before any cost |

Live confirmation (3 sessions, 2026-05-29/06-01/06-02): NVDA +$15 / −$13.71 = noise; TSLA mixed; the −2% stop chops the volatile single-stocks (NVDA stopped out Tue). 3-day P&L ≈ +$7 on $100k = flat.

**Position sizing (why $750 fails)** — `order_manager.py:127` computes `qty = int(notional / price)`. At `$750`:
- SPY ~$758 → `int(750/758) = 0` → order skipped every time (live: SPY signalled 3× Mon, 0 orders).
- QQQ ~$739 → `int(750/739) = 1` → barely 1 share; if QQQ ticks above ~$750 it also drops to 0.

At `$1,600`: SPY → 2 sh (~$1,516), QQQ → 2 sh (~$1,478). Robust to upward price drift. Fractional shares are NOT an option — bracket orders require whole shares.

## Risk / impact of the changes

- **Sizing:** $750 → $1,600 ≈ **doubles** per-trade dollar exposure. Per-trade risk at the −2% stop ≈ **$30** (2 SPY sh). With only 2 symbols and `MAX_OPEN_POSITIONS=3`, max deployed ≈ 2 positions ≈ $3,000 ≈ 3% of equity. Daily loss limit is 3% ($3,000) — comfortably within. Still conservative.
- **Universe:** fewer symbols = fewer trades, but the dropped names weren't contributing edge. Side benefit: the per-symbol session-start/noon alert spam drops from 5× to 2×.
- **Alternative (minimal change):** `MVP_POSITION_NOTIONAL_USD=800` → 1 share each. Smaller risk bump, but fragile (drops to qty 0 if a price exceeds $800) and wastes more to rounding. **$1,600 recommended** for robustness.

## How to apply (Railway — your action)

1. Railway dashboard → service **worker** → **Variables**.
2. Set `WATCHLIST=SPY,QQQ` and `MVP_POSITION_NOTIONAL_USD=1600` (optionally `RSI_HIGH=65`).
3. Save → Railway redeploys. **Do it outside market hours** (the redeploy restarts the bot; with the reconciler fix it reconciles clean, but cleanest when flat).
4. Confirm next `SESSION START` shows `Symbols: SPY, QQQ` and watch for whole-share SPY/QQQ fills.

## Caveats
- SPY/QQQ edge is still **thin** (Sharpe ~0.13–0.22, pre-richer-cost). This change concentrates on the *least-bad* symbols and makes them tradeable — it does not create a strong edge.
- Backtest is SPY/QQQ-as-traded on IEX, walk-forward; excludes fees beyond modeled slippage. Past ≠ future.
- Reliability gate unaffected — this is a paper-config refinement, not a go-live step.

---

**Decision:** approve `WATCHLIST=SPY,QQQ` + `MVP_POSITION_NOTIONAL_USD=1600` (and optionally `RSI_HIGH=65`)? I will not apply it — you make the Railway change when ready.
