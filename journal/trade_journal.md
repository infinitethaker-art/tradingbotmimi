# Trade Journal

Human-readable record of live (paper) sessions, newest first. Maintained by the
agent from the Telegram session reports. **Authoritative source of truth is the
live DB on Railway (`/app/db/trades.db`)** — this file is a convenience cache so
trade context survives across chat sessions. All sessions PAPER unless noted.

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
