# ═══════════════════════════════════════════════════════════════
# MULTI-STRATEGY BACKTEST - Hybrid Trend Continuation + Pullback
# Goal: High win rate + minimum 2RR
#
# Strategy Logic:
#   1. TREND IDENTIFIED on Daily (50 EMA + market structure)
#   2. PULLBACK on H4 to discount/premium zone or 50 EMA
#   3. CONFIRMATION on H1 (engulfing/pin bar with momentum)
#   4. RR: TP1=2R (min), TP2=3R, TP3=4R or next AOI
# Entry on candle CLOSE only. No look-ahead.
# 2 Years - From Jan 2024
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
    m   = 2 / (period + 1)
    val = sum(values[:period]) / period
    for p in values[period:]:
        val = (p - val) * m + val
    return val

def ema_series(values, period):
    # Returns full EMA series for plotting/lookup
    if len(values) < period:
        return []
    m   = 2 / (period + 1)
    series = [None] * (period - 1)
    val = sum(values[:period]) / period
    series.append(val)
    for p in values[period:]:
        val = (p - val) * m + val
        series.append(val)
    return series

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

def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains = losses = 0
    for i in range(1, period + 1):
        change = values[i] - values[i-1]
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        change = values[i] - values[i-1]
        gain   = max(change, 0)
        loss   = max(-change, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

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

# ─── DAILY TREND IDENTIFICATION ─────────────────────────────
def get_daily_trend(daily_candles):
    # Trend defined by 50 EMA slope + price position
    # Strong bullish: price above 50 EMA AND 50 EMA rising
    # Strong bearish: price below 50 EMA AND 50 EMA falling
    if len(daily_candles) < 60:
        return 'NEUTRAL'

    closes = [c['close'] for c in daily_candles]
    ema_now  = ema(closes, 50)
    ema_5ago = ema(closes[:-5], 50)
    if ema_now is None or ema_5ago is None:
        return 'NEUTRAL'

    price = daily_candles[-1]['close']
    ema_rising  = ema_now > ema_5ago
    ema_falling = ema_now < ema_5ago

    if price > ema_now and ema_rising:
        return 'BULLISH'
    if price < ema_now and ema_falling:
        return 'BEARISH'
    return 'NEUTRAL'

# ─── H4 PULLBACK ZONE CHECK ─────────────────────────────────
def is_pullback_to_ema(h4_candles, direction):
    # Pullback to 50 EMA means price has come back near the dynamic support/resistance
    # within last 5 H4 candles
    if len(h4_candles) < 60:
        return False
    closes = [c['close'] for c in h4_candles]
    h4_ema = ema(closes, 50)
    if h4_ema is None:
        return False

    # Check if recent 5 candles touched or got near the EMA
    pair_atr = atr(h4_candles, 14) or 0
    if pair_atr == 0:
        return False

    for c in h4_candles[-5:]:
        # Within 1 ATR of the EMA = qualifies as pullback
        if abs(c['low'] - h4_ema) < pair_atr or abs(c['high'] - h4_ema) < pair_atr:
            if direction == 'BUY' and c['low'] <= h4_ema * 1.005:
                return True
            if direction == 'SELL' and c['high'] >= h4_ema * 0.995:
                return True
    return False

# ─── H1 ENTRY CONFIRMATION ──────────────────────────────────
def h1_bullish_confirmation(h1_candles):
    # Need:
    # 1. Last candle is bullish AND closes in upper 60% of its range
    # 2. Body >= 50% of range (strong)
    # 3. RSI < 65 (not extreme overbought)
    if len(h1_candles) < 20:
        return False
    curr = h1_candles[-1]
    if curr['close'] <= curr['open']:
        return False
    rng  = curr['high'] - curr['low']
    if rng <= 0:
        return False
    body = abs(curr['close'] - curr['open'])
    close_pos = (curr['close'] - curr['low']) / rng

    if body < rng * 0.5:
        return False
    if close_pos < 0.6:
        return False

    closes = [c['close'] for c in h1_candles]
    h1_rsi = rsi(closes, 14)
    if h1_rsi is None or h1_rsi > 65:
        return False

    return True

def h1_bearish_confirmation(h1_candles):
    if len(h1_candles) < 20:
        return False
    curr = h1_candles[-1]
    if curr['close'] >= curr['open']:
        return False
    rng  = curr['high'] - curr['low']
    if rng <= 0:
        return False
    body = abs(curr['close'] - curr['open'])
    close_pos = (curr['close'] - curr['low']) / rng

    if body < rng * 0.5:
        return False
    if close_pos > 0.4:
        return False

    closes = [c['close'] for c in h1_candles]
    h1_rsi = rsi(closes, 14)
    if h1_rsi is None or h1_rsi < 35:
        return False

    return True

# ─── H4 ENTRY (STRONG TREND CONTINUATION) ───────────────────
def h4_bullish_confirmation(h4_candles):
    # Same logic on H4 - stronger signal because higher TF
    if len(h4_candles) < 20:
        return False
    curr = h4_candles[-1]
    if curr['close'] <= curr['open']:
        return False
    rng  = curr['high'] - curr['low']
    if rng <= 0:
        return False
    body = abs(curr['close'] - curr['open'])
    close_pos = (curr['close'] - curr['low']) / rng

    return body >= rng * 0.55 and close_pos >= 0.6

def h4_bearish_confirmation(h4_candles):
    if len(h4_candles) < 20:
        return False
    curr = h4_candles[-1]
    if curr['close'] >= curr['open']:
        return False
    rng  = curr['high'] - curr['low']
    if rng <= 0:
        return False
    body = abs(curr['close'] - curr['open'])
    close_pos = (curr['close'] - curr['low']) / rng

    return body >= rng * 0.55 and close_pos <= 0.4

# ─── RECENT SWING POINTS FOR TP ─────────────────────────────
def recent_swing_high(candles, lookback=20):
    if len(candles) < lookback:
        return None
    return max(c['high'] for c in candles[-lookback:])

def recent_swing_low(candles, lookback=20):
    if len(candles) < lookback:
        return None
    return min(c['low'] for c in candles[-lookback:])

# ─── SIMULATE TRADE ─────────────────────────────────────────
def simulate_trade(direction, entry, sl, tp1, tp2, tp3, future_candles):
    tp1_hit = tp2_hit = False
    tp1_time = tp2_time = None

    for c in future_candles:
        close = c['close']
        if direction == 'BUY':
            effective_sl = entry if tp1_hit else sl
            if close <= effective_sl:
                if tp1_hit and tp2_hit:
                    return 'TP2', c['time'], tp2, True, True
                if tp1_hit:
                    return 'TP1', tp1_time, tp1, True, False
                return 'SL', c['time'], close, False, False
            if not tp1_hit and close >= tp1:
                tp1_hit, tp1_time = True, c['time']
            if tp1_hit and not tp2_hit and close >= tp2:
                tp2_hit, tp2_time = True, c['time']
            if tp2_hit and close >= tp3:
                return 'TP3', c['time'], tp3, True, True
        else:
            effective_sl = entry if tp1_hit else sl
            if close >= effective_sl:
                if tp1_hit and tp2_hit:
                    return 'TP2', c['time'], tp2, True, True
                if tp1_hit:
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

# ─── BACKTEST ONE PAIR ──────────────────────────────────────
def backtest_pair(pair, h1_all, h4_all, d_all):
    print(chr(10) + '  ' + pair.replace('_', '/') + '...')
    if len(h1_all) < 200 or len(d_all) < 60:
        print('    Not enough data')
        return []

    trades   = []
    exit_bar = -1

    for i in range(120, len(h1_all) - 5):
        if i <= exit_bar:
            continue

        candle_time = h1_all[i]['time']
        curr_dt     = datetime.strptime(candle_time[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)

        # Get candles up to current point only (no lookahead)
        h1_window = h1_all[max(0, i-100):i+1]
        h4_window = [c for c in h4_all if c['time'] <= candle_time][-100:]
        d_window  = [c for c in d_all  if c['time'] <= candle_time][-80:]

        if len(h4_window) < 60 or len(d_window) < 60:
            continue

        # Step 1: Daily trend
        trend = get_daily_trend(d_window)
        if trend == 'NEUTRAL':
            continue

        direction = 'BUY' if trend == 'BULLISH' else 'SELL'

        # Step 2: H4 pullback to EMA
        if not is_pullback_to_ema(h4_window, direction):
            continue

        # Step 3: Try H4 confirmation first (higher conviction)
        # then H1 confirmation
        entry_tf = None
        if direction == 'BUY':
            if h4_bullish_confirmation(h4_window):
                entry_tf = 'H4'
            elif h1_bullish_confirmation(h1_window):
                entry_tf = 'H1'
        else:
            if h4_bearish_confirmation(h4_window):
                entry_tf = 'H4'
            elif h1_bearish_confirmation(h1_window):
                entry_tf = 'H1'

        if not entry_tf:
            continue

        # Step 4: Calculate SL and TPs
        entry = h1_all[i]['close']
        h4_atr = atr(h4_window, 14) or from_pips(20, pair)

        if direction == 'BUY':
            # SL below recent swing low or 1.5 ATR below entry whichever lower
            recent_low = recent_swing_low(h1_window, 20) or entry
            sl_struct  = recent_low - from_pips(3, pair)
            sl_atr     = entry - h4_atr * 1.5
            sl         = min(sl_struct, sl_atr)
            risk = entry - sl
            if risk <= 0:
                continue
            if to_pips(risk, pair) < 10:
                continue
            tp1 = entry + risk * 2.0   # MIN 2R
            tp2 = entry + risk * 3.0
            tp3 = entry + risk * 4.0
        else:
            recent_high = recent_swing_high(h1_window, 20) or entry
            sl_struct   = recent_high + from_pips(3, pair)
            sl_atr      = entry + h4_atr * 1.5
            sl          = max(sl_struct, sl_atr)
            risk = sl - entry
            if risk <= 0:
                continue
            if to_pips(risk, pair) < 10:
                continue
            tp1 = entry - risk * 2.0
            tp2 = entry - risk * 3.0
            tp3 = entry - risk * 4.0

        # Simulate
        result, ex_time, ex_price, _, _ = simulate_trade(
            direction, entry, sl, tp1, tp2, tp3, h1_all[i+1:i+500]
        )

        if direction == 'BUY':
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

        trades.append({
            'pair':       pair,
            'entry_time': candle_time,
            'direction':  direction,
            'entry':      round(entry, 5),
            'sl':         round(sl, 5),
            'tp1':        round(tp1, 5),
            'tp2':        round(tp2, 5),
            'tp3':        round(tp3, 5),
            'result':     result,
            'exit_time':  ex_time,
            'exit_price': round(ex_price, 5) if ex_price else None,
            'pips':       pips,
            'pattern':    'TREND+PULLBACK ' + entry_tf,
        })

        if result != 'OPEN':
            exit_bar = next(
                (j for j in range(i+1, min(i+500, len(h1_all)))
                 if h1_all[j]['time'] >= (ex_time or candle_time)),
                i + 1
            )

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
    print('  TREND + PULLBACK BACKTEST - 2 Years')
    print('  Daily trend + H4 EMA pullback + H1/H4 confirmation')
    print('  TP1=2R | TP2=3R | TP3=4R')
    print(SEP)

    if not all_trades:
        send_telegram('<b>Trend+Pullback Backtest</b>' + NL + 'No trades found.')
        return

    closed = [t for t in all_trades if t['result'] != 'OPEN']
    if not closed:
        send_telegram('<b>Trend+Pullback Backtest</b>' + NL + 'All trades still open.')
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

    print(NL + 'OVERALL:')
    print('  Signals: ' + str(len(all_trades)))
    print('  Closed:  ' + str(total))
    print('  TP3: ' + str(len(tp3s)) + ' | TP2: ' + str(len(tp2s)) +
          ' | TP1: ' + str(len(tp1s)) + ' | SL: ' + str(len(sls)))
    print('  Win Rate: ' + str(wr) + '%')
    print('  Avg Win:  +' + str(avg_win) + 'p')
    print('  Avg Loss: ' + str(avg_sl) + 'p')
    print('  Total P&L: ' + str(tot_pip) + 'p')
    print('  Profit Factor: ' + str(pf))
    print('  Max Consec SL: ' + str(max_cl))

    print(NL + 'BY PAIR:')
    print('  ' + 'Pair'.ljust(10) + 'Trades  Wins   WR%    TP1  TP2  TP3   SL   Pips')
    for pair in sorted(set(t['pair'] for t in closed)):
        pt = [t for t in closed if t['pair'] == pair]
        pw = [t for t in pt if t['result'] != 'SL']
        pt1 = [t for t in pt if t['result'] == 'TP1']
        pt2 = [t for t in pt if t['result'] == 'TP2']
        pt3 = [t for t in pt if t['result'] == 'TP3']
        psl = [t for t in pt if t['result'] == 'SL']
        wrp = round(len(pw) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        print('  ' + pair.replace('_', '/').ljust(10) +
              str(len(pt)).rjust(5) + str(len(pw)).rjust(6) +
              str(wrp).rjust(8) + '%' +
              str(len(pt1)).rjust(5) + str(len(pt2)).rjust(5) +
              str(len(pt3)).rjust(5) + str(len(psl)).rjust(5) +
              str(ppp).rjust(8))

    print(NL + 'TRADES:')
    for t in all_trades:
        date = t['entry_time'][:10]
        print('  ' + date + ' ' + t['pair'].replace('_', '/').ljust(8) + ' ' +
              t['direction'].ljust(5) + ' E:' + str(t['entry']) +
              ' SL:' + str(t['sl']) + ' TP1:' + str(t['tp1']) +
              ' TP2:' + str(t['tp2']) + ' TP3:' + str(t['tp3']) +
              ' ' + t['result'].ljust(6) + ' ' + str(t['pips']) + 'p')

    print(NL + SEP)

    # ── Telegram summary ──
    tg = (
        '<b>Trend+Pullback Backtest - 2 Years</b>' + NL +
        'Daily trend + H4 EMA pullback + H1/H4 entry' + NL +
        'TP1=2R | TP2=3R | TP3=4R' + NL + NL +
        '<b>OVERALL:</b>' + NL +
        'Signals: ' + str(len(all_trades)) + NL +
        'Closed: ' + str(total) + NL +
        'TP3: ' + str(len(tp3s)) + NL +
        'TP2: ' + str(len(tp2s)) + NL +
        'TP1: ' + str(len(tp1s)) + NL +
        'SL:  ' + str(len(sls)) + NL +
        'Win Rate: <b>' + str(wr) + '%</b>' + NL +
        'Avg Win:  +' + str(avg_win) + 'p' + NL +
        'Avg Loss: ' + str(avg_sl) + 'p' + NL +
        'Total P&L: ' + str(tot_pip) + 'p' + NL +
        'Profit Factor: ' + str(pf) + NL +
        'Max Consec SL: ' + str(max_cl) + NL + NL +
        '<b>BY PAIR:</b>' + NL
    )
    for pair in sorted(set(t['pair'] for t in closed)):
        pt = [t for t in closed if t['pair'] == pair]
        pw = [t for t in pt if t['result'] != 'SL']
        wrp = round(len(pw) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        tg += (pair.replace('_', '/').ljust(10) +
               ' T:' + str(len(pt)) + ' W:' + str(len(pw)) +
               ' WR:' + str(wrp) + '%' +
               ' P&L:' + str(ppp) + 'p' + NL)
    send_telegram(tg)

    # ── Trade chunks ──
    chunk_size = 30
    for cs in range(0, len(all_trades), chunk_size):
        chunk = all_trades[cs:cs + chunk_size]
        m = ('<b>Trades ' + str(cs+1) + '-' + str(min(cs+chunk_size, len(all_trades))) +
             ' of ' + str(len(all_trades)) + '</b>' + NL +
             '-' * 60 + NL)
        for t in chunk:
            m += (t['entry_time'][:10] + ' ' +
                  t['pair'].replace('_', '/').ljust(8) + ' ' +
                  t['direction'].ljust(4) + ' ' +
                  str(t['entry']).ljust(7) + ' SL:' + str(t['sl']) +
                  ' TP1:' + str(t['tp1']) + ' ' +
                  t['result'].ljust(5) + ' ' + str(t['pips']) + 'p' + NL)
        send_telegram(m)

# ─── MAIN ────────────────────────────────────────────────────
def main():
    print('=' * 65)
    print('  TREND + PULLBACK Backtester - 2 Years')
    print('  High Win Rate Target - Min 2RR per Trade')
    print('=' * 65)

    all_trades = []
    for pair in PAIRS:
        try:
            print(chr(10) + 'Fetching ' + pair + '...')
            h1 = fetch_2years(pair, 'H1')
            h4 = fetch_2years(pair, 'H4')
            d  = fetch_2years(pair, 'D')
            print('  H1:' + str(len(h1)) + ' H4:' + str(len(h4)) + ' D:' + str(len(d)))

            if len(h1) < 200 or len(d) < 60:
                continue

            trades = backtest_pair(pair, h1, h4, d)
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
