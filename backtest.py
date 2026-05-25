# ═══════════════════════════════════════════════════════════════
# STRATEGY S6: EMA 200 + MACD PULLBACK + ATR STOP
# 
# Rules:
# LONG:  Price > EMA200 → pullback → MACD hist crosses back above 0
# SHORT: Price < EMA200 → rally  → MACD hist crosses back below 0
# SL:    1.5 × ATR from entry
# TP:    1.5 × risk (1:1.5 RR)
# BE:    Move SL to breakeven when 1:1 reached
# Session: London (07:00-15:30 UTC) + NY (13:30-21:00 UTC)
# Timeframe: M15
# 2 Years from Jan 2024
# ═══════════════════════════════════════════════════════════════
import requests, os, time
from datetime import datetime, timezone, timedelta

OANDA_API_KEY      = os.environ.get('OANDA_API_KEY', '')
OANDA_BASE_URL     = 'https://api-fxpractice.oanda.com'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')

PAIRS = [
    'EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD',
    'USD_CHF', 'NZD_USD', 'USD_CAD', 'XAU_USD'
]

# IST = UTC + 5:30 → converted to UTC
# London: 07:00-15:30 UTC | NY: 13:30-21:00 UTC
# Combined active window: 07:00-21:00 UTC
SESSION_START_UTC = 7
SESSION_END_UTC   = 21

# ─── PIP HELPERS ─────────────────────────────────────────────
def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    return 0.0001

def to_pips(diff, pair):
    return round(diff / pip_size(pair), 1)

def from_pips(pips, pair):
    return pips * pip_size(pair)

# ─── INDICATORS ──────────────────────────────────────────────
def ema(values, period):
    if len(values) < period:
        return None
    m   = 2.0 / (period + 1)
    val = sum(values[:period]) / period
    for p in values[period:]:
        val = (p - val) * m + val
    return val

def ema_series(values, period):
    if len(values) < period:
        return []
    m      = 2.0 / (period + 1)
    result = [None] * (period - 1)
    val    = sum(values[:period]) / period
    result.append(val)
    for p in values[period:]:
        val = (p - val) * m + val
        result.append(val)
    return result

def macd_series(values, fast=12, slow=26, signal=9):
    # Returns (macd_line, signal_line, histogram) as full series
    ema_fast   = ema_series(values, fast)
    ema_slow   = ema_series(values, slow)
    if not ema_fast or not ema_slow:
        return [], [], []

    # MACD line = EMA12 - EMA26
    macd_line = []
    for i in range(len(values)):
        f = ema_fast[i]
        s = ema_slow[i]
        if f is None or s is None:
            macd_line.append(None)
        else:
            macd_line.append(f - s)

    # Signal = EMA9 of MACD line (skip Nones)
    valid_indices  = [i for i, v in enumerate(macd_line) if v is not None]
    valid_macd     = [macd_line[i] for i in valid_indices]
    signal_vals    = ema_series(valid_macd, signal)
    signal_series  = [None] * len(values)
    for idx, vi in enumerate(valid_indices):
        if idx < len(signal_vals) and signal_vals[idx] is not None:
            signal_series[vi] = signal_vals[idx]

    # Histogram = MACD - Signal
    hist_series = []
    for i in range(len(values)):
        m_val = macd_line[i]
        s_val = signal_series[i]
        if m_val is None or s_val is None:
            hist_series.append(None)
        else:
            hist_series.append(m_val - s_val)

    return macd_line, signal_series, hist_series

def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        tr = max(
            candles[i]['high'] - candles[i]['low'],
            abs(candles[i]['high'] - candles[i-1]['close']),
            abs(candles[i]['low']  - candles[i-1]['close'])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period

# ─── FETCH DATA ──────────────────────────────────────────────
def fetch_chunk(pair, granularity, from_dt):
    url     = OANDA_BASE_URL + '/v3/instruments/' + pair + '/candles'
    headers = {'Authorization': 'Bearer ' + OANDA_API_KEY}
    params  = {
        'granularity': granularity,
        'from':        from_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'count':       5000,
        'price':       'M',
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            print('    OANDA ' + str(r.status_code))
            return []
        out = []
        for c in r.json().get('candles', []):
            if c.get('complete'):
                out.append({
                    'time':  c['time'],
                    'open':  float(c['mid']['o']),
                    'high':  float(c['mid']['h']),
                    'low':   float(c['mid']['l']),
                    'close': float(c['mid']['c']),
                })
        return out
    except Exception as e:
        print('    Fetch error: ' + str(e))
        return []

def fetch_2years(pair, granularity):
    now    = datetime.now(timezone.utc)
    start  = datetime(2024, 1, 1, tzinfo=timezone.utc)
    all_c  = []
    cursor = start
    while cursor < now:
        chunk = fetch_chunk(pair, granularity, cursor)
        if not chunk:
            break
        all_c.extend(chunk)
        last_dt  = datetime.strptime(chunk[-1]['time'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        next_cur = last_dt + timedelta(seconds=1)
        if next_cur <= cursor:
            break
        cursor = next_cur
        if len(chunk) < 100:
            break
        time.sleep(0.5)
    seen, unique = set(), []
    for c in all_c:
        if c['time'] not in seen:
            seen.add(c['time'])
            unique.append(c)
    return sorted(unique, key=lambda x: x['time'])

# ─── SIMULATE TRADE WITH BREAKEVEN ───────────────────────────
def simulate_trade(direction, entry, sl, tp, future_candles, pair):
    risk     = abs(entry - sl)
    be_level = entry  # breakeven trigger at 1:1
    be_moved = False

    for c in future_candles:
        high  = c['high']
        low   = c['low']
        close = c['close']

        if direction == 'BUY':
            current_sl = entry if be_moved else sl
            # Check if 1:1 reached - move SL to BE
            if not be_moved and high >= entry + risk:
                be_moved = True
            # SL check
            if low <= current_sl:
                return ('BE' if be_moved else 'SL'), current_sl, c['time']
            # TP check
            if high >= tp:
                return 'TP', tp, c['time']
        else:
            current_sl = entry if be_moved else sl
            if not be_moved and low <= entry - risk:
                be_moved = True
            if high >= current_sl:
                return ('BE' if be_moved else 'SL'), current_sl, c['time']
            if low <= tp:
                return 'TP', tp, c['time']

    last = future_candles[-1]['close'] if future_candles else entry
    return 'EXPIRED', last, future_candles[-1]['time'] if future_candles else None

# ─── BACKTEST ONE PAIR ──────────────────────────────────────
def backtest_pair(pair, m15_all):
    print(chr(10) + '  ' + pair.replace('_', '/') + '...')
    if len(m15_all) < 250:
        return []

    trades   = []
    exit_bar = -1
    WARMUP   = 230  # Need 200 bars for EMA200 + MACD warmup

    closes = [c['close'] for c in m15_all]

    # Pre-compute MACD histogram series for efficiency
    print('    Computing MACD...')
    _, _, hist_all = macd_series(closes, 12, 26, 9)
    print('    Computing EMA200...')
    ema200_all = ema_series(closes, 200)

    for i in range(WARMUP, len(m15_all) - 5):
        if i <= exit_bar:
            continue

        curr_time = datetime.strptime(
            m15_all[i]['time'][:19], '%Y-%m-%dT%H:%M:%S'
        ).replace(tzinfo=timezone.utc)

        # Weekend skip
        if curr_time.weekday() >= 5:
            continue

        # Session filter: London + NY = 07:00-21:00 UTC
        if curr_time.hour < SESSION_START_UTC or curr_time.hour >= SESSION_END_UTC:
            continue

        # Get indicators at current bar
        ema200 = ema200_all[i]
        hist_curr = hist_all[i]
        hist_prev = hist_all[i-1]

        if ema200 is None or hist_curr is None or hist_prev is None:
            continue

        price = m15_all[i]['close']
        curr_atr = atr(m15_all[max(0, i-20):i+1], 14)
        if not curr_atr:
            continue

        direction = None

        # ── LONG SETUP ───────────────────────────────────────
        # 1. Price above EMA200 (trend bullish)
        # 2. MACD histogram was below zero (pullback exhaustion)
        # 3. MACD histogram crosses back above zero (momentum resuming)
        if (price > ema200 and
                hist_prev < 0 and
                hist_curr > 0):
            direction = 'BUY'

        # ── SHORT SETUP ──────────────────────────────────────
        # 1. Price below EMA200
        # 2. MACD was above zero (rally exhaustion)
        # 3. MACD crosses back below zero
        elif (price < ema200 and
                hist_prev > 0 and
                hist_curr < 0):
            direction = 'SELL'

        if not direction:
            continue

        # ── RISK MANAGEMENT ──────────────────────────────────
        entry = m15_all[i]['close']

        if direction == 'BUY':
            # SL: 1.5 ATR below entry (also look at recent swing low)
            recent_low = min(c['low'] for c in m15_all[max(0,i-10):i+1])
            sl_atr     = entry - curr_atr * 1.5
            sl         = min(sl_atr, recent_low - from_pips(2, pair))
            risk       = entry - sl
        else:
            recent_high = max(c['high'] for c in m15_all[max(0,i-10):i+1])
            sl_atr      = entry + curr_atr * 1.5
            sl          = max(sl_atr, recent_high + from_pips(2, pair))
            risk        = sl - entry

        if risk <= 0:
            continue

        risk_pips = to_pips(risk, pair)
        if risk_pips < 5 or risk_pips > 50:
            continue

        # TP = 1.5 × risk (1:1.5 RR)
        if direction == 'BUY':
            tp = entry + risk * 1.5
        else:
            tp = entry - risk * 1.5

        # Simulate
        result, exit_px, exit_time = simulate_trade(
            direction, entry, sl, tp,
            m15_all[i+1:i+300], pair
        )

        if direction == 'BUY':
            if result == 'TP':
                pips = to_pips(tp - entry, pair)
            elif result in ('SL', 'BE'):
                pips = to_pips(exit_px - entry, pair)
            else:
                pips = to_pips(exit_px - entry, pair)
        else:
            if result == 'TP':
                pips = to_pips(entry - tp, pair)
            elif result in ('SL', 'BE'):
                pips = to_pips(entry - exit_px, pair)
            else:
                pips = to_pips(entry - exit_px, pair)

        pips = round(pips, 1)

        trades.append({
            'pair':       pair,
            'date':       curr_time.strftime('%Y-%m-%d'),
            'time':       curr_time.strftime('%H:%M'),
            'entry_time': m15_all[i]['time'],
            'direction':  direction,
            'entry':      round(entry, 5),
            'sl':         round(sl, 5),
            'tp':         round(tp, 5),
            'risk_pips':  risk_pips,
            'ema200':     round(ema200, 5),
            'result':     result,
            'exit_price': round(exit_px, 5),
            'pips':       pips,
        })

        # Advance exit bar to avoid re-entering during open trade
        if exit_time:
            for j in range(i+1, min(i+300, len(m15_all))):
                if m15_all[j]['time'] >= exit_time:
                    exit_bar = j
                    break

    return trades

# ─── SEND TELEGRAM ───────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url  = 'https://api.telegram.org/bot' + TELEGRAM_BOT_TOKEN + '/sendMessage'
        data = {'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print('  Telegram error: ' + str(e))

# ─── PRINT RESULTS ───────────────────────────────────────────
def print_results(all_trades):
    NL  = chr(10)
    SEP = '=' * 80
    print(NL + SEP)
    print('  STRATEGY S6: EMA200 + MACD PULLBACK + ATR STOP')
    print('  M15 | 1:1.5 RR | London + NY sessions | BE at 1:1')
    print(SEP)

    if not all_trades:
        send_telegram('<b>S6 Backtest</b>' + NL + 'No trades found.')
        return

    tps     = [t for t in all_trades if t['result'] == 'TP']
    sls     = [t for t in all_trades if t['result'] == 'SL']
    bes     = [t for t in all_trades if t['result'] == 'BE']
    expired = [t for t in all_trades if t['result'] == 'EXPIRED']
    wins    = tps + bes   # TP + Breakeven are both non-losses
    total   = len(all_trades)

    wr      = round(len(tps) / total * 100, 1) if total else 0
    wr_inc_be = round(len(wins) / total * 100, 1) if total else 0
    tot_pip = round(sum(t['pips'] for t in all_trades), 1)
    avg_tp  = round(sum(t['pips'] for t in tps) / len(tps), 1) if tps else 0
    avg_sl  = round(sum(t['pips'] for t in sls) / len(sls), 1) if sls else 0
    avg_be  = round(sum(t['pips'] for t in bes) / len(bes), 1) if bes else 0
    gw      = sum(t['pips'] for t in tps)
    gl      = abs(sum(t['pips'] for t in sls)) or 1
    pf      = round(gw / gl, 2)
    exp     = round(tot_pip / total, 2) if total else 0

    max_cl = cl = 0
    for t in all_trades:
        if t['result'] == 'SL':
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0

    print(NL + 'OVERALL:')
    print('  Total trades:       ' + str(total))
    print('  TP (1.5R wins):     ' + str(len(tps)))
    print('  BE (breakeven):     ' + str(len(bes)))
    print('  SL (full loss):     ' + str(len(sls)))
    print('  Expired:            ' + str(len(expired)))
    print('  Win Rate (TP only): ' + str(wr) + '%')
    print('  Win Rate (TP+BE):   ' + str(wr_inc_be) + '%')
    print('  Avg TP:             +' + str(avg_tp) + 'p')
    print('  Avg BE:             ' + str(avg_be) + 'p')
    print('  Avg SL:             ' + str(avg_sl) + 'p')
    print('  Total Pips:         ' + str(tot_pip) + 'p')
    print('  Profit Factor:      ' + str(pf))
    print('  Expectancy:         ' + str(exp) + 'p/trade')
    print('  Max Consec SL:      ' + str(max_cl))

    print(NL + 'BY PAIR:')
    print('  ' + 'Pair'.ljust(10) + 'Trades  TP   BE   SL   WR%    Pips')
    print('  ' + '-' * 58)
    for pair in sorted(set(t['pair'] for t in all_trades)):
        pt  = [t for t in all_trades if t['pair'] == pair]
        ptp = [t for t in pt if t['result'] == 'TP']
        pbe = [t for t in pt if t['result'] == 'BE']
        psl = [t for t in pt if t['result'] == 'SL']
        wrp = round(len(ptp) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        print('  ' + pair.replace('_', '/').ljust(10) +
              str(len(pt)).rjust(5) +
              str(len(ptp)).rjust(5) +
              str(len(pbe)).rjust(5) +
              str(len(psl)).rjust(5) +
              str(wrp).rjust(8) + '%' +
              str(ppp).rjust(8))

    print(NL + 'FULL TRADE LOG:')
    print('  Date       Time  Pair     Dir   Risk   Entry     SL        TP        Result Pips')
    print('  ' + '-' * 85)
    for t in all_trades:
        print('  ' + t['date'] + ' ' + t['time'] + ' ' +
              t['pair'].replace('_', '/').ljust(8) + ' ' +
              t['direction'].ljust(5) +
              str(t['risk_pips']).rjust(4) + 'p ' +
              str(t['entry']).ljust(9) +
              str(t['sl']).ljust(9) +
              str(t['tp']).ljust(9) +
              t['result'].ljust(6) + ' ' +
              str(t['pips']) + 'p')

    print(NL + SEP)

    # ── Telegram ─────────────────────────────────────────────
    tg = (
        '<b>S6: EMA200 + MACD Pullback + ATR Stop</b>' + NL +
        'M15 | RR 1:1.5 | BE at 1:1 | London+NY' + NL + NL +
        '<b>OVERALL:</b>' + NL +
        'Trades: ' + str(total) + NL +
        'TP (1.5R): ' + str(len(tps)) + NL +
        'BE (0R):   ' + str(len(bes)) + NL +
        'SL (-1R):  ' + str(len(sls)) + NL +
        'Expired:   ' + str(len(expired)) + NL +
        'Win Rate (TP only): <b>' + str(wr) + '%</b>' + NL +
        'Win Rate (TP+BE):   <b>' + str(wr_inc_be) + '%</b>' + NL +
        'Avg TP: +' + str(avg_tp) + 'p' + NL +
        'Avg BE: ' + str(avg_be) + 'p' + NL +
        'Avg SL: ' + str(avg_sl) + 'p' + NL +
        'Total P&L: ' + str(tot_pip) + 'p' + NL +
        'Profit Factor: ' + str(pf) + NL +
        'Expectancy: +' + str(exp) + 'p/trade' + NL +
        'Max Consec SL: ' + str(max_cl) + NL + NL +
        '<b>BY PAIR:</b>' + NL
    )
    for pair in sorted(set(t['pair'] for t in all_trades)):
        pt  = [t for t in all_trades if t['pair'] == pair]
        ptp = [t for t in pt if t['result'] == 'TP']
        wrp = round(len(ptp) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        tg += (pair.replace('_', '/').ljust(10) +
               ' T:' + str(len(pt)) + ' TP:' + str(len(ptp)) +
               ' WR:' + str(wrp) + '% P&L:' + str(ppp) + 'p' + NL)
    send_telegram(tg)

    chunk_size = 30
    for cs in range(0, len(all_trades), chunk_size):
        chunk = all_trades[cs:cs + chunk_size]
        m = ('<b>Trades ' + str(cs+1) + '-' +
             str(min(cs+chunk_size, len(all_trades))) +
             ' of ' + str(len(all_trades)) + '</b>' + NL + '-' * 55 + NL)
        for t in chunk:
            m += (t['date'] + ' ' + t['time'] + ' ' +
                  t['pair'].replace('_', '/').ljust(8) + ' ' +
                  t['direction'].ljust(4) + ' ' +
                  'E:' + str(t['entry']) + ' ' +
                  'SL:' + str(t['sl']) + ' ' +
                  'TP:' + str(t['tp']) + ' ' +
                  t['result'].ljust(5) + ' ' + str(t['pips']) + 'p' + NL)
        send_telegram(m)

# ─── MAIN ────────────────────────────────────────────────────
def main():
    print('=' * 70)
    print('  STRATEGY S6: EMA200 + MACD PULLBACK + ATR STOP')
    print('  M15 | 1:1.5 RR | Breakeven at 1:1')
    print('  London 07:00-15:30 UTC | NY 13:30-21:00 UTC')
    print('=' * 70)

    all_trades = []
    for pair in PAIRS:
        try:
            print(chr(10) + 'Fetching ' + pair + '...')
            m15 = fetch_2years(pair, 'M15')
            print('  M15: ' + str(len(m15)) + ' candles')
            if len(m15) < 250:
                continue
            trades = backtest_pair(pair, m15)
            all_trades.extend(trades)
            tps = [t for t in trades if t['result'] == 'TP']
            bes = [t for t in trades if t['result'] == 'BE']
            sls = [t for t in trades if t['result'] == 'SL']
            wr  = round(len(tps) / len(trades) * 100, 1) if trades else 0
            tot = round(sum(t['pips'] for t in trades), 1)
            print('  Trades:' + str(len(trades)) +
                  ' TP:' + str(len(tps)) +
                  ' BE:' + str(len(bes)) +
                  ' SL:' + str(len(sls)) +
                  ' WR:' + str(wr) + '%' +
                  ' P&L:' + str(tot) + 'p')
        except Exception as e:
            print('  ERROR: ' + str(e))

    try:
        print_results(all_trades)
    except Exception as e:
        print('ERROR in print_results: ' + str(e))
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
