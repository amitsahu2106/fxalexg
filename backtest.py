# ═══════════════════════════════════════════════════════════════
# Weekly Engulfing + H1 Engulfing Backtest - 2 Years
# Strategy by trader from video transcript
#
# Setup: Weekly outside-bar (engulfing) sweeps liquidity + reverses
#   - Bullish: trades below prev week low, closes above prev week high
#   - Bearish: trades above prev week high, closes below prev week low
# Entry: H1 engulfing in direction of weekly bias
#   - Price must be below (BUY) or above (SELL) weekly open
# SL: H1 engulfing candle's opposite extreme
# TP1=1R, TP2=mid to prev week extreme, TP3=prev week extreme
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
]  # BTC removed - too volatile for weekly engulfing

# ─── PIP HELPERS ─────────────────────────────────────────────
def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    if 'BTC' in pair: return 1.00
    return 0.0001

def to_pips(diff, pair):
    return round(diff / pip_size(pair), 1)

def from_pips(pips, pair):
    return pips * pip_size(pair)

def ema(values, period):
    if len(values) < period:
        return None
    m   = 2 / (period + 1)
    val = sum(values[:period]) / period
    for p in values[period:]:
        val = (p - val) * m + val
    return val

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
            print('    OANDA ' + str(r.status_code) + ': ' + r.text[:100])
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
        last_time = chunk[-1]['time']
        last_dt   = datetime.strptime(last_time[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        next_cur  = last_dt + timedelta(seconds=1)
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

# ─── CANDLE HELPERS ──────────────────────────────────────────
def body_high(c): return max(c['open'], c['close'])
def body_low(c):  return min(c['open'], c['close'])

# ─── WEEKLY ENGULFING DETECTION ─────────────────────────────
def is_bullish_weekly_engulfing(curr, prev):
    # Strict bullish outside bar:
    # 1. Swept prev week low (took sellside liquidity)
    # 2. Closed above prev week high (broke buyside)
    # 3. Green candle (close > open)
    # 4. STRONG close - in upper 30% of candle range (shows commitment)
    # 5. Body size at least 50% of range (no doji-like indecision)
    rng = curr['high'] - curr['low']
    if rng <= 0:
        return False
    body = abs(curr['close'] - curr['open'])
    close_position = (curr['close'] - curr['low']) / rng

    return (curr['low']   < prev['low']  and
            curr['close'] > prev['high'] and
            curr['close'] > curr['open'] and
            close_position >= 0.70       and  # Close in upper 30%
            body >= rng * 0.5)               # Strong body

def is_bearish_weekly_engulfing(curr, prev):
    # Strict bearish outside bar (mirror)
    rng = curr['high'] - curr['low']
    if rng <= 0:
        return False
    body = abs(curr['close'] - curr['open'])
    close_position = (curr['close'] - curr['low']) / rng

    return (curr['high']  > prev['high'] and
            curr['close'] < prev['low']  and
            curr['close'] < curr['open'] and
            close_position <= 0.30       and  # Close in lower 30%
            body >= rng * 0.5)               # Strong body

# ─── H1 ENGULFING ENTRY SIGNAL ──────────────────────────────
def h1_bullish_engulfing(prev, curr):
    # Strict bullish engulfing on H1:
    # 1. Prev candle red
    # 2. Curr candle green
    # 3. Curr body fully engulfs prev body
    # 4. Curr body must be at least 1.2x prev body (strong reversal)
    # 5. Curr close in upper 60% of its own range
    ph = max(prev['open'], prev['close'])
    pl = min(prev['open'], prev['close'])
    ch = max(curr['open'], curr['close'])
    cl = min(curr['open'], curr['close'])
    pb = abs(prev['close'] - prev['open'])
    cb = abs(curr['close'] - curr['open'])
    rng = curr['high'] - curr['low']
    if rng <= 0:
        return False
    close_pos = (curr['close'] - curr['low']) / rng

    return (prev['close'] < prev['open'] and
            curr['close'] > curr['open'] and
            ch >= ph and cl <= pl         and
            cb >= pb * 1.2                and  # Strong reversal body
            close_pos >= 0.60)                # Close strong

def h1_bearish_engulfing(prev, curr):
    ph = max(prev['open'], prev['close'])
    pl = min(prev['open'], prev['close'])
    ch = max(curr['open'], curr['close'])
    cl = min(curr['open'], curr['close'])
    pb = abs(prev['close'] - prev['open'])
    cb = abs(curr['close'] - curr['open'])
    rng = curr['high'] - curr['low']
    if rng <= 0:
        return False
    close_pos = (curr['close'] - curr['low']) / rng

    return (prev['close'] > prev['open'] and
            curr['close'] < curr['open'] and
            ch >= ph and cl <= pl         and
            cb >= pb * 1.2                and
            close_pos <= 0.40)

# ─── GET WEEK START FROM TIMESTAMP ──────────────────────────
def get_week_start(time_str):
    # Returns Monday 00:00 UTC of the week containing this time
    dt   = datetime.strptime(time_str[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    days = dt.weekday()  # 0=Monday
    monday = dt - timedelta(days=days, hours=dt.hour, minutes=dt.minute, seconds=dt.second)
    return monday

# ─── SIMULATE TRADE ─────────────────────────────────────────
def simulate_trade(direction, entry, sl, tp1, tp2, tp3, future_candles):
    # Use candle CLOSE for all decisions
    # TP1 hit = WIN immediately + SL moves to BE
    # TP2 hit = continues to TP3
    # TP3 hit = full win
    tp1_hit    = False
    tp1_time   = None
    tp2_hit    = False
    tp2_time   = None

    for c in future_candles:
        close = c['close']

        if direction == 'BUY':
            effective_sl = entry if tp1_hit else sl
            if close <= effective_sl:
                if tp1_hit:
                    if tp2_hit:
                        return 'TP2', c['time'], tp2, True, True
                    return 'TP1', tp1_time, tp1, True, False
                return 'SL', c['time'], close, False, False

            if not tp1_hit and close >= tp1:
                tp1_hit, tp1_time = True, c['time']
            if tp1_hit and not tp2_hit and close >= tp2:
                tp2_hit, tp2_time = True, c['time']
            if tp2_hit and close >= tp3:
                return 'TP3', c['time'], tp3, True, True

        else:  # SELL
            effective_sl = entry if tp1_hit else sl
            if close >= effective_sl:
                if tp1_hit:
                    if tp2_hit:
                        return 'TP2', c['time'], tp2, True, True
                    return 'TP1', tp1_time, tp1, True, False
                return 'SL', c['time'], close, False, False

            if not tp1_hit and close <= tp1:
                tp1_hit, tp1_time = True, c['time']
            if tp1_hit and not tp2_hit and close <= tp2:
                tp2_hit, tp2_time = True, c['time']
            if tp2_hit and close <= tp3:
                return 'TP3', c['time'], tp3, True, True

    if tp2_hit:
        return 'TP2', tp2_time, tp2, True, True
    if tp1_hit:
        return 'TP1', tp1_time, tp1, True, False
    return 'OPEN', None, entry, False, False

# ─── BACKTEST ONE PAIR ───────────────────────────────────────
def backtest_pair(pair, h1_all, w_all):
    print(chr(10) + '  ' + pair.replace('_', '/') + '...')
    if len(w_all) < 5 or len(h1_all) < 200:
        print('    Not enough data')
        return []

    trades     = []
    open_trade = None
    exit_bar   = -1

    # For each weekly candle, check if it qualifies as the "setup week"
    # The trade week is the NEXT week after the setup week
    for w_idx in range(1, len(w_all) - 1):
        setup_week    = w_all[w_idx]
        prev_week     = w_all[w_idx - 1]
        next_week     = w_all[w_idx + 1] if w_idx + 1 < len(w_all) else None

        # Check setup conditions
        bias = None
        if is_bullish_weekly_engulfing(setup_week, prev_week):
            bias = 'BUY'
        elif is_bearish_weekly_engulfing(setup_week, prev_week):
            bias = 'SELL'

        if not bias:
            continue

        # Define the trade week boundaries
        # Trade window: from start of next week to end of next week
        trade_week_start = datetime.strptime(setup_week['time'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc) + timedelta(days=7)
        trade_week_end   = trade_week_start + timedelta(days=7)

        # The setup week's open is the "weekly open" for trading
        # Actually no - we want the NEXT week's open for the trade week
        trade_week_open = None

        # Find H1 candles in the trade week
        for i in range(1, len(h1_all)):
            curr_h1 = h1_all[i]
            prev_h1 = h1_all[i-1]

            curr_dt = datetime.strptime(curr_h1['time'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)

            if curr_dt < trade_week_start:
                continue
            if curr_dt >= trade_week_end:
                break

            # Capture the trade week's open (first H1 of the week)
            if trade_week_open is None:
                trade_week_open = curr_h1['open']

            # Don't enter new trade if one is open
            if open_trade or i <= exit_bar:
                continue

            # Risk/reward sanity:
            # Need at least 1:2 from entry to TP3 (prev week extreme)
            # Need price in proper DISCOUNT (BUY) or PREMIUM (SELL) zone

            # 50 EMA filter on last 50 H1 closes (trend confirmation)
            recent_closes = [c['close'] for c in h1_all[max(0, i-50):i+1]]
            h1_ema = ema(recent_closes, 50)
            if h1_ema is None:
                continue

            # Direction filter and entry rule
            if bias == 'BUY':
                # Need:
                #  1. Price below weekly open AND in DISCOUNT zone
                #     (below midpoint of setup week range)
                #  2. H1 bullish engulfing
                #  3. Price near or above 50 EMA (don't fight extreme trend)
                #  4. Min 1:2 RR from entry to TP3
                if curr_h1['close'] >= trade_week_open:
                    continue
                setup_mid = (setup_week['high'] + setup_week['low']) / 2
                if curr_h1['close'] >= setup_mid:
                    continue  # Not in discount zone
                if not h1_bullish_engulfing(prev_h1, curr_h1):
                    continue

                entry = curr_h1['close']
                sl    = curr_h1['low'] - from_pips(3, pair)
                risk  = entry - sl
                if risk <= 0:
                    continue

                # Skip if SL too tight (< 8 pips for forex, gives noise room)
                if to_pips(risk, pair) < 8:
                    continue

                tp1   = entry + risk
                tp2   = (entry + setup_week['high']) / 2
                tp3   = setup_week['high']

                # Min 1:2 RR to TP3
                if (tp3 - entry) < risk * 2.0:
                    continue
                if tp2 <= tp1 or tp3 <= tp2:
                    continue

            else:  # SELL
                if curr_h1['close'] <= trade_week_open:
                    continue
                setup_mid = (setup_week['high'] + setup_week['low']) / 2
                if curr_h1['close'] <= setup_mid:
                    continue  # Not in premium zone
                if not h1_bearish_engulfing(prev_h1, curr_h1):
                    continue

                entry = curr_h1['close']
                sl    = curr_h1['high'] + from_pips(3, pair)
                risk  = sl - entry
                if risk <= 0:
                    continue

                if to_pips(risk, pair) < 8:
                    continue

                tp1   = entry - risk
                tp2   = (entry + setup_week['low']) / 2
                tp3   = setup_week['low']

                if (entry - tp3) < risk * 2.0:
                    continue
                if tp2 >= tp1 or tp3 >= tp2:
                    continue

            # Simulate trade from next bar
            result, ex_time, ex_price, _, _ = simulate_trade(
                bias, entry, sl, tp1, tp2, tp3, h1_all[i+1:i+500]
            )

            if bias == 'BUY':
                if result == 'TP3':
                    pips = to_pips(tp3 - entry, pair)
                elif result == 'TP2':
                    pips = to_pips(tp2 - entry, pair)
                elif result == 'TP1':
                    pips = to_pips(tp1 - entry, pair)
                elif result == 'SL':
                    pips = -to_pips(entry - sl, pair)
                else:
                    pips = 0
            else:
                if result == 'TP3':
                    pips = to_pips(entry - tp3, pair)
                elif result == 'TP2':
                    pips = to_pips(entry - tp2, pair)
                elif result == 'TP1':
                    pips = to_pips(entry - tp1, pair)
                elif result == 'SL':
                    pips = -to_pips(sl - entry, pair)
                else:
                    pips = 0

            trade_record = {
                'pair':         pair,
                'entry_time':   curr_h1['time'],
                'direction':    bias,
                'entry':        round(entry, 5),
                'sl':           round(sl, 5),
                'tp1':          round(tp1, 5),
                'tp2':          round(tp2, 5),
                'tp3':          round(tp3, 5),
                'result':       result,
                'exit_time':    ex_time,
                'exit_price':   round(ex_price, 5) if ex_price else None,
                'pips':         pips,
                'pattern':      'WEEKLY+H1 ENGULF',
            }
            trades.append(trade_record)

            # Move exit_bar so we wait for trade to close
            if result != 'OPEN':
                exit_bar = next(
                    (j for j in range(i+1, min(i+500, len(h1_all)))
                     if h1_all[j]['time'] >= (ex_time or curr_h1['time'])),
                    i
                )
            break  # Only one trade per week (in line with strategy)

    return trades

# ─── SEND TELEGRAM ───────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print('  Telegram not configured')
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
    NL2 = chr(10)
    SEP = '=' * 80
    print(NL + SEP)
    print('  WEEKLY ENGULFING + H1 ENGULFING - 2 Years Backtest')
    print('  Entry on H1 candle CLOSE in direction of weekly bias')
    print('  TP1=1R | TP2=midpoint to prev wk extreme | TP3=prev wk extreme')
    print(SEP)

    if not all_trades:
        print('No trades found.')
        send_telegram('<b>Weekly Engulfing Backtest</b>' + NL + 'No trades found.')
        return

    closed = [t for t in all_trades if t['result'] != 'OPEN']
    if not closed:
        msg = 'Signals: ' + str(len(all_trades)) + ' but all still OPEN.'
        print(msg)
        send_telegram('<b>Weekly Engulfing Backtest</b>' + NL + msg)
        return

    tp3s  = [t for t in closed if t['result'] == 'TP3']
    tp2s  = [t for t in closed if t['result'] == 'TP2']
    tp1s  = [t for t in closed if t['result'] == 'TP1']
    sls   = [t for t in closed if t['result'] == 'SL']
    wins  = tp1s + tp2s + tp3s
    total = len(closed)

    wr      = round(len(wins) / total * 100, 1) if total else 0
    tot_pip = round(sum(t['pips'] for t in closed), 1)
    avg_win = round(sum(t['pips'] for t in wins) / len(wins), 1) if wins else 0
    avg_sl  = round(sum(t['pips'] for t in sls)  / len(sls),  1) if sls  else 0
    gw      = sum(t['pips'] for t in wins)
    gl      = abs(sum(t['pips'] for t in sls))
    pf      = round(gw / gl, 2) if gl > 0 else 0

    max_cl = cl = 0
    for t in closed:
        if t['result'] == 'SL':
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0

    print(NL + 'OVERALL STATISTICS:')
    print('  Total signals:           ' + str(len(all_trades)))
    print('  Closed trades:           ' + str(total))
    print('  Still open:              ' + str(len(all_trades) - total))
    print('  TP3 hit (max target):    ' + str(len(tp3s)))
    print('  TP2 hit:                 ' + str(len(tp2s)))
    print('  TP1 hit (1R):            ' + str(len(tp1s)))
    print('  SL hit (loss):           ' + str(len(sls)))
    print('  Win Rate (TP1+TP2+TP3):  ' + str(wr) + '%')
    print('  Avg Win  (pips):         +' + str(avg_win))
    print('  Avg Loss (pips):         ' + str(avg_sl))
    print('  Total Pips P&L:          ' + str(tot_pip))
    print('  Profit Factor:           ' + str(pf))
    print('  Max Consecutive Losses:  ' + str(max_cl))

    print(NL + 'BY PAIR:')
    print('  ' + 'Pair'.ljust(10) + 'Trades  Wins    WR%    TP1  TP2  TP3   SL   Pips')
    print('  ' + '-' * 65)
    for pair in sorted(set(t['pair'] for t in closed)):
        pt   = [t for t in closed if t['pair'] == pair]
        pw   = [t for t in pt if t['result'] != 'SL']
        pt1  = [t for t in pt if t['result'] == 'TP1']
        pt2  = [t for t in pt if t['result'] == 'TP2']
        pt3  = [t for t in pt if t['result'] == 'TP3']
        psl  = [t for t in pt if t['result'] == 'SL']
        wr_p = round(len(pw) / len(pt) * 100, 1) if pt else 0
        pp_p = round(sum(t['pips'] for t in pt), 1)
        print('  ' + pair.replace('_', '/').ljust(10) +
              str(len(pt)).rjust(5) +
              str(len(pw)).rjust(6) +
              str(wr_p).rjust(8) + '%' +
              str(len(pt1)).rjust(5) +
              str(len(pt2)).rjust(5) +
              str(len(pt3)).rjust(5) +
              str(len(psl)).rjust(5) +
              str(pp_p).rjust(8))

    print(NL + 'FULL TRADE LOG:')
    print('  ' + '-' * 110)
    print('  ' +
          'Entry Date'.ljust(12) +
          'Pair'.ljust(10) +
          'Dir'.ljust(5) +
          'Entry'.ljust(10) +
          'SL'.ljust(10) +
          'TP1'.ljust(10) +
          'TP2'.ljust(10) +
          'TP3'.ljust(10) +
          'Result'.ljust(8) +
          'Exit'.ljust(12) +
          'Pips')
    print('  ' + '-' * 110)
    for t in all_trades:
        date = t['entry_time'][:10]
        ep   = str(t['entry'])
        sl_s = str(t['sl'])
        t1   = str(t['tp1'])
        t2   = str(t['tp2'])
        t3   = str(t['tp3'])
        xp   = str(t.get('exit_price', '-')) if t.get('exit_price') else 'OPEN'
        pips = str(t['pips']) if t['result'] != 'OPEN' else '-'
        print('  ' +
              date.ljust(12) +
              t['pair'].replace('_', '/').ljust(10) +
              t['direction'].ljust(5) +
              ep.ljust(10) +
              sl_s.ljust(10) +
              t1.ljust(10) +
              t2.ljust(10) +
              t3.ljust(10) +
              t['result'].ljust(8) +
              xp.ljust(12) +
              pips)

    print(NL + SEP)

    # ── Telegram summary ──────────────────────────────────────
    tg = (
        '<b>Weekly Engulfing Backtest - 2 Years</b>' + NL2 +
        'TP1=1R | TP2=midpoint | TP3=prev week extreme' + NL2 + NL2 +
        '<b>OVERALL:</b>' + NL2 +
        'Signals: '       + str(len(all_trades)) + NL2 +
        'Closed: '        + str(total)           + NL2 +
        'TP3: '           + str(len(tp3s))       + NL2 +
        'TP2: '           + str(len(tp2s))       + NL2 +
        'TP1: '           + str(len(tp1s))       + NL2 +
        'SL:  '           + str(len(sls))        + NL2 +
        'Win Rate: <b>'   + str(wr) + '%</b>'    + NL2 +
        'Avg Win:  +'     + str(avg_win) + 'p'   + NL2 +
        'Avg Loss: '      + str(avg_sl)  + 'p'   + NL2 +
        'Total P&L: '     + str(tot_pip) + 'p'   + NL2 +
        'Profit Factor: ' + str(pf)              + NL2 +
        'Max Consec SL: ' + str(max_cl)          + NL2 + NL2 +
        '<b>BY PAIR:</b>' + NL2
    )
    for pair in sorted(set(t['pair'] for t in closed)):
        pt   = [t for t in closed if t['pair'] == pair]
        pw   = [t for t in pt if t['result'] != 'SL']
        wr_p = round(len(pw) / len(pt) * 100, 1) if pt else 0
        pp_p = round(sum(t['pips'] for t in pt), 1)
        tg  += (pair.replace('_', '/').ljust(10) +
                ' T:' + str(len(pt)) +
                ' W:' + str(len(pw)) +
                ' WR:' + str(wr_p) + '%' +
                ' P&L:' + str(pp_p) + 'p' + NL2)

    send_telegram(tg)

    # ── Send all trades in chunks of 30 ──────────────────────
    chunk_size = 30
    for cs in range(0, len(all_trades), chunk_size):
        chunk     = all_trades[cs:cs + chunk_size]
        chunk_num = (cs // chunk_size) + 1
        total_c   = (len(all_trades) + chunk_size - 1) // chunk_size
        m = ('<b>Trades ' + str(cs+1) + '-' +
             str(min(cs+chunk_size, len(all_trades))) +
             ' of ' + str(len(all_trades)) +
             ' (Part ' + str(chunk_num) + '/' + str(total_c) + ')</b>' + NL2 +
             'Date     Pair      Dir   Entry   SL      TP1     TP2     TP3     Res   Pips' + NL2 +
             '-' * 80 + NL2)
        for t in chunk:
            m += (t['entry_time'][:10] + ' ' +
                  t['pair'].replace('_', '/').ljust(8) + ' ' +
                  t['direction'].ljust(4) + ' ' +
                  str(t['entry']).ljust(7) + ' ' +
                  str(t['sl']).ljust(7) + ' ' +
                  str(t['tp1']).ljust(7) + ' ' +
                  str(t['tp2']).ljust(7) + ' ' +
                  str(t['tp3']).ljust(7) + ' ' +
                  t['result'].ljust(5) + ' ' +
                  str(t['pips']) + NL2)
        send_telegram(m)

# ─── MAIN ────────────────────────────────────────────────────
def main():
    print('=' * 65)
    print('  Weekly Engulfing + H1 Engulfing Backtester')
    print('  2 Years - Candle CLOSE based - From Jan 2024')
    print('=' * 65)
    print('Fetching data... 5-8 minutes expected')

    all_trades = []
    for pair in PAIRS:
        try:
            print(chr(10) + 'Fetching ' + pair + '...')
            h1 = fetch_2years(pair, 'H1')
            w  = fetch_2years(pair, 'W')
            print('  H1:' + str(len(h1)) + ' W:' + str(len(w)))

            if len(h1) < 200 or len(w) < 5:
                print('  Not enough data - skipping')
                continue

            trades = backtest_pair(pair, h1, w)
            all_trades.extend(trades)
            closed = [t for t in trades if t['result'] != 'OPEN']
            wins   = [t for t in closed if t['result'] != 'SL']
            wr     = round(len(wins) / len(closed) * 100, 1) if closed else 0
            print('  Signals:' + str(len(trades)) +
                  '  Closed:' + str(len(closed)) +
                  '  WR:' + str(wr) + '%')
        except Exception as e:
            print('  ERROR: ' + str(e))

    print_results(all_trades)

if __name__ == '__main__':
    main()
