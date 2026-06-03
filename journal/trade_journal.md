# Trade Journal

Human-readable record of live (paper) sessions, newest first. Maintained by the
agent from the Telegram session reports. **Authoritative source of truth is the
live DB on Railway (`/app/db/trades.db`)** — this file is a convenience cache so
trade context survives across chat sessions. All sessions PAPER unless noted.

---

## 2026-06-02 (Tue) — PAPER

**Session start:** equity $100,033.70 (Mon ≈ +$19) · feed IEX · WATCHLIST still all 5.

**Trades:**
| Symbol | Qty | Entry | Exit | PnL | Notes |
|--------|-----|-------|------|-----|-------|
| AAPL | 2 sh | $308.97 | $314.78 (time exit) | **+$11.62** | win |
| TSLA | 1 sh | $416.58 | $420.40 (time exit) | +$3.82 | win; entered at RSI 38 (near oversold) |
| NVDA | 3 sh | $229.77 | $225.20 (**STOP-LOSS**) | **−$13.71** | hit −2% stop (~225.17) mid-morning; entered at RSI 70.0 (boundary) |

**Net ≈ +$1.73.** SPY not traded (qty 0 again). No QQQ signal. So once more, **only the single-stock names traded** — the flat-to-losing universe.

**Alert-duplication (cosmetic, NOT multiple instances) — diagnosed from bot.log.** Session-start fired 5× and the noon ping 5× with different positions (None,None,AAPL,None,TSLA). Initially suspected multiple instances — **disproven by the logs.** bot.log shows a SINGLE boot (Mon 06-01 13:30, one `Lock acquired (PID 1)`) and **no `main:` boot on 06-02** — the same process ran continuously (WS reconnect at 07:34, then the day's trades). Cause: there are **5 Loop A threads (one per watchlist symbol)**, each with its own `_session_alerted_date`/`_noon_alerted_date` ([loop_a.py:104-105](../scheduler/loop_a.py#L104-L105), [311-319](../scheduler/loop_a.py#L311-L319)). Since `main()` blocks past close, the process spans days; on a new day all 5 threads each fire the daily session-start + noon alert, and each noon ping reports ITS symbol's position (SPY/QQQ/NVDA→None, AAPL/TSLA→held — exact match). Monday showed 1 because that was the boot day (main.py sends it once). **No concurrency, no double-trade risk — purely cosmetic alert spam.** Fix (low priority): send the daily session-start/noon ping once per day, not once per symbol-thread.

**🔬 Research confirmed live — the −2% stop chops volatile single-stocks.** NVDA stopped out −$13.71 mid-morning (it's volatile enough that the stop actually fires, unlike SPY where it never does). NVDA: +$15 Mon (lucky) / −$13.71 Tue (stopped) = noise → matches the 13-month "NVDA flat/no-edge" finding. Also: NVDA entered at RSI **70.0** — the research-preferred `RSI_HIGH=65` would have *rejected* this entry and avoided the loss (one trade = noise, but directionally consistent).

---

## 2026-06-01 (Mon) — PAPER

**Session start:** equity $100,014.49 · feed IEX · AUTO_EXECUTE on · WATCHLIST still all 5 (SPY,QQQ,NVDA,AAPL,TSLA — narrowing not yet applied).

**Trades:**
| Symbol | Qty | Entry | Exit | PnL | Notes |
|--------|-----|-------|------|-----|-------|
| NVDA | 3 sh | $218.97 | $223.99 (time exit) | **+$15.06** | rel-vol 3.70x; big winner |
| AAPL | 2 sh | $307.87 | $307.38 (time exit) | −$0.98 | entered 14:30 ET |
| QQQ | 1 sh | $738.79 | time-exit submitted (fill not in paste) | ~flat | rel-vol 2.31x |
| SPY | — | — | — | — | **DROPPED — qty 0 (see finding)** |

**🚩 Finding — SPY is un-tradeable at $750 notional.** SPY signalled 3× (≈$757–759) and each was approved, but `submit_bracket_entry` computes `qty = int(750 / 757) = 0` → order skipped (returns None) every time. **The strategy's single best symbol (per backtest) literally never trades**, because `MVP_POSITION_NOTIONAL_USD=750` < 1 SPY share (~$757). QQQ barely clears (750/739 → qty 1); if QQQ rises above ~$750 it drops to 0 too. Both index ETFs (the only viable symbols per research) are at/above the notional → fragile-to-zero quantity. To actually trade them, notional must be raised (e.g. ≥ ~$1,600 for ≥2 sh) — fractional shares won't work because bracket orders require whole shares.

**Note — NVDA won today (+$15), but that's one session of noise.** The 13-month walk-forward says NVDA is flat/no-edge; one green day does not refute it. Do not read it as edge.

**Time-exit:** AAPL and QQQ both hit the `held_for_orders` first-attempt failure again (bracket legs reserve the shares); the built-in retry recovered both. NVDA exited clean. Known transient.

**Reporting caveat:** midday showed "3 taken" but one of those was an SPY signal that never became an order (qty 0). The `taken` counter likely overcounts qty-0 drops — the disposition is stamped SUBMITTED in Loop A before Loop B drops it.

---

## 2026-05-29 (Fri) — PAPER

**Session start:** equity $100,023.31 · loss limit $3,000.70 (3%) · feed IEX · AUTO_EXECUTE on.

**Incident (open):** Morning reconciliation HALT storm — broker held `{NVDA, AAPL}`
positions + orders `aeedccdf`, `25e70c74` that the DB had no record of. Bot
crash-restarted ~every 36s (Railway `restartPolicyType=ALWAYS`) re-alerting each
time. Self-recovered once the broker went flat (NVDA cleared, then AAPL), then
reconciled CLEAN and resumed. Root cause of the divergence NOT yet confirmed
(needs live DB dump). See [incidents/2026-05-29.md](../incidents/2026-05-29.md).

**Trades (both time-exited at session end):**
| Symbol | Side | Qty | Avg entry | Exit | client_order_id | Signal | Realized PnL |
|--------|------|-----|-----------|------|-----------------|--------|--------------|
| AAPL | long | 2 sh | $313.99 | $311.41 (time exit) | AAPL_20260529_ENTER_1330 | MACD 0.242, RSI 68.9, rel-vol 2.94x | −$5.16 |
| TSLA | long | 1 sh | $437.98 | $434.32 (time exit) | TSLA_20260529_ENTER_1600 | MACD 0.072, RSI 52.1, rel-vol 1.50x | −$3.66 |

**Session result (daily report):** 115 scans · 4 signals · **2 taken** · **2 rejected** · 4 fills · realized PnL **−$8.81**.
Both positions closed by time-exit; neither TP nor SL hit. Feed IEX.
(My −$8.82 estimate from rounded alert prices matched the official −$8.81 to the cent. Paper — validates plumbing, not edge.)

**Both rejections were `SYMBOL_ALREADY_OPEN`** (re-entry guard working): AAPL re-signalled
on the 14:00 bar while already held (entered 13:45); TSLA re-signalled on the 16:30 bar
while already held (entered 16:15). RSI/vol/MACD all passed both times — the strategy keeps
signalling a held symbol on consecutive bars. This is the exact pattern that, *without* the
guard, caused the 05-28 double-entry → HALT incident. Today it was correctly blocked.

**Time-exit note (AAPL):** first market-sell attempt was rejected —
`insufficient qty available (requested 2, available 0), held_for_orders: 2` — the
shares were reserved by the open bracket TP/SL legs (related order
`537a7372`). Alpaca's bracket-cancel is async; the built-in retry succeeded on a
later attempt and AAPL sold 2 @ $311.41. Transient, self-recovered. The
`⚠️ TIME EXIT FAILED` alert is misleading because it then succeeded.

**Reconciler note:** no mid-session restart occurred, so the phantom-position race
(#3) did NOT fire today. AAPL held bracket legs for the whole hold — real-world
proof that reconciler fix #1 matters: a restart mid-hold would have flagged those
legs as orphans and HALTed under the *current* (deployed) code.

Notes: live `MVP_POSITION_NOTIONAL_USD` ≈ $750 (Railway env), not the 500 in local `.env`.
