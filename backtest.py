# FXAlexG Strategy Backtester
# Tests A+ signals on 1 year of historical data
# Results printed directly to GitHub Actions logs

import requests
import os
import json
from datetime import datetime, timezone, timedelta

OANDA_API_KEY    = os.environ.get('OANDA_API_KEY', '')
OANDA_BASE_URL   = 'https://api-fxpractice.oanda.com'

PAIRS = [
    'EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD',
    'USD_CHF', 'NZD_USD', 'USD_CAD', 'XAU_USD', 'BTC_USD'
]

EMA_PERIOD     = 50
MAX_AOI_PIPS   = 60
MIN_AOI_TOUCHES = 3
MIN_TFS_ALIGNED = 3

# ─────────────────────────────────────────────
# PIP HELPERS
# ─────────────────────────────────────────────
def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    if 'BTC' in pair: return 1.00
    return 0.0001

def to_pips(diff, pair):
    return round(diff / pip_size(pair), 1)

def from_pips(pips, pair):
    return pips * pip_size(pair)

# ─────────────────────────────────────────────
# OANDA HISTORICAL DATA
# ─────────────────────────────────────────────
def fetch_candles(pair, granularity, count=500):
    url     = OANDA_BASE_URL + '/v3/instruments/' + pair + '/candles'
    headers = {'Authorization': 'Bearer ' + OANDA_API_KEY}
    params  = {'granularity': granularity, 'count': count, 'price': 'M'}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            return []
        candles = []
        for c in r.json().get('candles', []):
            if c.get('complete'):
                candles.append({
                    'time':  c['time'],
                    'open':  float(c['mid']['o']),
                    'high':  float(c['mid']['h']),
                    'low':   float(c['mid']['l']),
                    'close': float(c['mid']['c']),
                })
        return candles
    except Exception as e:
        print('Fetch error ' + pair + '/' + granularity + ': ' + str(e))
        return []

def fetch_candles_from(pair, granularity, from_date, count=500):
    url     = OANDA_BASE_URL + '/v3/instruments/' + pair + '/candles'
    headers = {'Authorization': 'Bearer ' + OANDA_API_KEY}
    params  = {
        'granularity': granularity,
        'from':        from_date,
        'count':       count,
        'price':       'M'
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            return []
        candles = []
        for c in r.json().get('candles', []):
            if c.get('complete'):
                candles.append({
                    'time':  c['time'],
                    'open':  float(c['mid']['o']),
                    'high':  float(c['mid']['h']),
                    'low':   float(c['mid']['l']),
                    'close': float(c['mid']['c']),
                })
        return candles
    except:
        return []

# ─────────────────────────────────────────────
# BODY HELPERS
# ─────────────────────────────────────────────
def body_high(c):
    return max(c['open'], c['close'])

def body_low(c):
    return min(c['open'], c['close'])

# ─────────────────────────────────────────────
# SWING POINTS (body only)
# ─────────────────────────────────────────────
def swing_points(candles, lookback=5):
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        window = candles[i - lookback: i + lookback + 1]
        if body_high(candles[i]) == max(body_high(c) for c in window):
            highs.append({'i': i, 'price': body_high(candles[i])})
        if body_low(candles[i]) == min(body_low(c) for c in window):
            lows.append({'i': i, 'price': body_low(candles[i])})
    return highs, lows

# ─────────────────────────────────────────────
# SNAKE TRICK MARKET STRUCTURE
# ─────────────────────────────────────────────
def snake_hl(bar_i, lows):
    before = sorted([l for l in lows if l['i'] < bar_i], key=lambda x: x['i'], reverse=True)
    return before[0] if before else None

def snake_lh(bar_i, highs):
    before = sorted([h for h in highs if h['i'] < bar_i], key=lambda x: x['i'], reverse=True)
    return before[0] if before else None

def market_structure(highs, lows, candles):
    if len(highs) < 2 or len(lows) < 2 or len(candles) < 10:
        return 'NEUTRAL'
    all_pts = sorted(
        [('H', h['i'], h['price']) for h in highs] +
        [('L', l['i'], l['price']) for l in lows],
        key=lambda x: x[1]
    )
    state      = 'NEUTRAL'
    current_hh = None
    current_hl = None
    current_ll = None
    current_lh = None
    for (ptype, bar_i, price) in all_pts:
        if state == 'NEUTRAL':
            if ptype == 'H':
                current_hh = {'i': bar_i, 'price': price}
                current_hl = snake_hl(bar_i, lows)
                state = 'BULLISH'
            else:
                current_ll = {'i': bar_i, 'price': price}
                current_lh = snake_lh(bar_i, highs)
                state = 'BEARISH'
        elif state == 'BULLISH':
            if ptype == 'H' and price > current_hh['price']:
                current_hh = {'i': bar_i, 'price': price}
                hl = snake_hl(bar_i, lows)
                if hl:
                    current_hl = hl
            elif ptype == 'L' and current_hl and price < current_hl['price']:
                current_ll = {'i': bar_i, 'price': price}
                current_lh = snake_lh(bar_i, highs)
                state = 'BEARISH'
                current_hh = None
                current_hl = None
        elif state == 'BEARISH':
            if ptype == 'L' and price < current_ll['price']:
                current_ll = {'i': bar_i, 'price': price}
                lh = snake_lh(bar_i, highs)
                if lh:
                    current_lh = lh
            elif ptype == 'H' and current_lh and price > current_lh['price']:
                current_hh = {'i': bar_i, 'price': price}
                current_hl = snake_hl(bar_i, lows)
                state = 'BULLISH'
                current_ll = None
                current_lh = None
    return state

# ─────────────────────────────────────────────
# 50 EMA
# ─────────────────────────────────────────────
def ema50(candles):
    closes = [c['close'] for c in candles]
    if len(closes) < EMA_PERIOD:
        return None
    m   = 2 / (EMA_PERIOD + 1)
    val = sum(closes[:EMA_PERIOD]) / EMA_PERIOD
    for p in closes[EMA_PERIOD:]:
        val = (p - val) * m + val
    return val

# ─────────────────────────────────────────────
# AOI DETECTION
# ─────────────────────────────────────────────
def detect_aois(candles, pair):
    highs, lows = swing_points(candles, lookback=3)
    max_zone    = from_pips(MAX_AOI_PIPS, pair)
    levels      = sorted([p['price'] for p in highs + lows])
    zones, i    = [], 0
    while i < len(levels):
        base = levels[i]
        j    = i + 1
        while j < len(levels) and levels[j] - base <= max_zone:
            j += 1
        cluster = levels[i:j]
        if len(cluster) >= MIN_AOI_TOUCHES:
            top     = max(cluster)
            bot     = min(cluster)
            sz_pips = to_pips(top - bot, pair)
            if sz_pips <= MAX_AOI_PIPS:
                zones.append({
                    'top':       top,
                    'bottom':    bot,
                    'mid':       (top + bot) / 2,
                    'touches':   len(cluster),
                    'size_pips': round(sz_pips, 1),
                })
        i = j
    return zones

def at_aoi(price, zones, pair):
    buf = from_pips(5, pair)
    for z in zones:
        if (z['bottom'] - buf) <= price <= (z['top'] + buf):
            return z
    return None

# ─────────────────────────────────────────────
# SL/TP CALCULATOR
# ─────────────────────────────────────────────
def calc_sl_tp(direction, price, aoi_zone, pair, all_aois):
    buf = from_pips(5, pair)
    if direction == 'SELL' and aoi_zone:
        sl   = aoi_zone['top'] + buf
        risk = abs(sl - price)
        lower = sorted(
            [z for z in all_aois if z['top'] < aoi_zone['bottom']],
            key=lambda z: z['top'], reverse=True
        )
        for z in lower:
            mid = (z['top'] + z['bottom']) / 2
            if abs(price - mid) >= risk:
                return sl, mid, z['bottom']
        return sl, price - risk * 2, price - risk * 3
    elif direction == 'BUY' and aoi_zone:
        sl   = aoi_zone['bottom'] - buf
        risk = abs(price - sl)
        higher = sorted(
            [z for z in all_aois if z['bottom'] > aoi_zone['top']],
            key=lambda z: z['bottom']
        )
        for z in higher:
            mid = (z['top'] + z['bottom']) / 2
            if abs(mid - price) >= risk:
                return sl, mid, z['top']
        return sl, price + risk * 2, price + risk * 3
    risk = from_pips(30, pair)
    if direction == 'SELL':
        return price + risk, price - risk * 2, price - risk * 3
    return price - risk, price + risk * 2, price + risk * 3

# ─────────────────────────────────────────────
# GRADE CALCULATOR
# ─────────────────────────────────────────────
def calc_grade(trends, aoi_zone, ema_sig, has_engulfing):
    pts = 0
    bullish = sum(1 for t in trends.values() if t == 'BULLISH')
    bearish = sum(1 for t in trends.values() if t == 'BEARISH')
    majority = max(bullish, bearish)
    if majority < MIN_TFS_ALIGNED:
        return 'D+', pts
    pts += majority
    if aoi_zone:
        pts += 2
    if ema_sig:
        pts += 1
    if has_engulfing:
        pts += 2
    if majority == 4:
        grade = 'A+' if pts >= 8 else 'B+' if pts >= 6 else 'C+' if pts >= 4 else 'D+'
    else:
        grade = 'A+' if pts >= 9 else 'B+' if pts >= 7 else 'C+' if pts >= 5 else 'D+'
    return grade, pts

# ─────────────────────────────────────────────
# SIMULATE ONE TRADE
# Returns: 'TP1', 'TP2', 'SL', 'OPEN'
# ─────────────────────────────────────────────
def simulate_trade(pair, direction, entry, sl, tp1, tp2, entry_time, future_candles):
    for c in future_candles:
        if c['time'] <= entry_time:
            continue
        high = c['high']
        low  = c['low']
        if direction == 'BUY':
            if low <= sl:
                return 'SL', c['time'], sl
            if high >= tp2:
                return 'TP2', c['time'], tp2
            if high >= tp1:
                return 'TP1', c['time'], tp1
        else:
            if high >= sl:
                return 'SL', c['time'], sl
            if low <= tp2:
                return 'TP2', c['time'], tp2
            if low <= tp1:
                return 'TP1', c['time'], tp1
    return 'OPEN', None, entry


# ─────────────────────────────────────────────
# ENTRY SIGNAL DETECTOR
# Checks H4, H1, M15 for candlestick patterns
# Returns signal dict or None
# ─────────────────────────────────────────────
def detect_entry_signal(candles, direction):
    if len(candles) < 3:
        return None
    c1   = candles[-3]
    prev = candles[-2]
    curr = candles[-1]
    ph   = max(prev['open'], prev['close'])
    pl   = min(prev['open'], prev['close'])
    ch   = max(curr['open'], curr['close'])
    cl   = min(curr['open'], curr['close'])
    ps   = abs(prev['close'] - prev['open'])
    cs   = abs(curr['close'] - curr['open'])
    tr   = curr['high'] - curr['low']
    prev_bull = prev['close'] > prev['open']
    prev_bear = prev['close'] < prev['open']
    curr_bull = curr['close'] > curr['open']
    curr_bear = curr['close'] < curr['open']

    # 1. Engulfing (primary)
    if direction == 'BUY' and prev_bear and curr_bull and ch >= ph and cl <= pl and cs > ps * 0.8:
        return {'pattern': 'BULLISH ENGULFING', 'strength': 'STRONG', 'entry': curr['close']}
    if direction == 'SELL' and prev_bull and curr_bear and ch >= ph and cl <= pl and cs > ps * 0.8:
        return {'pattern': 'BEARISH ENGULFING', 'strength': 'STRONG', 'entry': curr['close']}

    # 2. Pin Bar
    if tr > 0:
        uw = curr['high'] - ch
        lw = cl - curr['low']
        if direction == 'BUY' and lw >= cs * 2 and lw >= tr * 0.6 and cs <= tr * 0.35:
            return {'pattern': 'BULLISH PIN BAR', 'strength': 'MODERATE', 'entry': curr['close']}
        if direction == 'SELL' and uw >= cs * 2 and uw >= tr * 0.6 and cs <= tr * 0.35:
            return {'pattern': 'BEARISH PIN BAR', 'strength': 'MODERATE', 'entry': curr['close']}

    # 3. Morning/Evening Star
    c1s = abs(c1['close'] - c1['open'])
    c2s = abs(prev['close'] - prev['open'])
    if direction == 'BUY' and c1['close'] < c1['open'] and c2s < c1s * 0.3 and curr_bull and curr['close'] >= (c1['open'] + c1['close']) / 2 and cs >= c1s * 0.5:
        return {'pattern': 'MORNING STAR', 'strength': 'STRONG', 'entry': curr['close']}
    if direction == 'SELL' and c1['close'] > c1['open'] and c2s < c1s * 0.3 and curr_bear and curr['close'] <= (c1['open'] + c1['close']) / 2 and cs >= c1s * 0.5:
        return {'pattern': 'EVENING STAR', 'strength': 'STRONG', 'entry': curr['close']}

    # 4. Marubozu
    if tr > 0 and cs / tr >= 0.85:
        if direction == 'BUY' and curr_bull:
            return {'pattern': 'BULLISH MARUBOZU', 'strength': 'STRONG', 'entry': curr['close']}
        if direction == 'SELL' and curr_bear:
            return {'pattern': 'BEARISH MARUBOZU', 'strength': 'STRONG', 'entry': curr['close']}

    return None

# ─────────────────────────────────────────────
# BACKTEST ONE PAIR
# ─────────────────────────────────────────────
def backtest_pair(pair):
    print(chr(10) + '  Backtesting ' + pair + '...')

    # Fetch 1 year of H1 candles (8760 hours)
    # OANDA max per request is 5000, so we fetch in 2 batches
    h1_all = fetch_candles(pair, 'H1', count=5000)
    if len(h1_all) < 200:
        print('    Not enough H1 data')
        return []

    # Fetch higher TF candles for structure
    w_candles  = fetch_candles(pair, 'W',  count=100)
    d_candles  = fetch_candles(pair, 'D',  count=365)
    h4_candles = fetch_candles(pair, 'H4', count=1000)

    trades     = []
    open_trade = None  # track one trade at a time

    # Walk through H1 candles from oldest (skip first 100 for warmup)
    for i in range(100, len(h1_all) - 10):
        candle_time = h1_all[i]['time']
        price       = h1_all[i]['close']

        # Skip if trade already open
        if open_trade:
            result, res_time, res_price = simulate_trade(
                pair,
                open_trade['direction'],
                open_trade['entry'],
                open_trade['sl'],
                open_trade['tp1'],
                open_trade['tp2'],
                open_trade['time'],
                h1_all[i:i+200]
            )
            if result != 'OPEN':
                rr = 0
                if result == 'TP2':
                    rr = 2.0
                elif result == 'TP1':
                    rr = 1.0
                elif result == 'SL':
                    rr = -1.0
                pips = to_pips(
                    abs(res_price - open_trade['entry']), pair
                ) * (1 if result != 'SL' else -1)
                open_trade['result']  = result
                open_trade['res_time'] = res_time
                open_trade['pips']    = pips
                open_trade['rr']      = rr
                trades.append(open_trade)
                open_trade = None
            continue

        # Get candles up to current bar for analysis
        h1_window  = h1_all[max(0, i-120):i+1]
        h4_window  = [c for c in h4_candles if c['time'] <= candle_time][-60:]
        d_window   = [c for c in d_candles  if c['time'] <= candle_time][-60:]
        w_window   = [c for c in w_candles  if c['time'] <= candle_time][-30:]

        if len(h1_window) < 30 or len(h4_window) < 10:
            continue

        # Market structure on each TF
        h1_h, h1_l = swing_points(h1_window)
        h4_h, h4_l = swing_points(h4_window)
        d_h,  d_l  = swing_points(d_window)  if len(d_window)  > 10 else ([], [])
        w_h,  w_l  = swing_points(w_window)  if len(w_window)  > 5  else ([], [])

        trends = {
            'H1': market_structure(h1_h, h1_l, h1_window),
            'H4': market_structure(h4_h, h4_l, h4_window),
            'D':  market_structure(d_h,  d_l,  d_window)  if d_window  else 'NEUTRAL',
            'W':  market_structure(w_h,  w_l,  w_window)  if w_window  else 'NEUTRAL',
        }

        bullish = sum(1 for t in trends.values() if t == 'BULLISH')
        bearish = sum(1 for t in trends.values() if t == 'BEARISH')
        majority = max(bullish, bearish)
        if majority < MIN_TFS_ALIGNED:
            continue

        direction = 'BUY' if bullish > bearish else 'SELL'

        # AOI detection (D and W only)
        all_aois = []
        if d_window:
            all_aois.extend(detect_aois(d_window, pair))
        if w_window:
            all_aois.extend(detect_aois(w_window, pair))

        aoi_zone = at_aoi(price, all_aois, pair)
        if not aoi_zone:
            continue

        # 50 EMA
        h1_ema  = ema50(h1_window)
        ema_sig = False
        if h1_ema:
            if direction == 'BUY'  and price > h1_ema:
                ema_sig = True
            if direction == 'SELL' and price < h1_ema:
                ema_sig = True

        # Grade WITHOUT entry signal first
        grade, pts = calc_grade(trends, aoi_zone, ema_sig, False)
        if grade not in ('A+', 'B+'):
            continue

        # Wait for entry signal on H4, H1 or M15
        # Check current candle first, then next 5 candles
        entry_signal = None
        entry_price  = None
        entry_time   = None
        entry_tf     = None

        # Check H4 first (strongest conviction)
        h4_sig = detect_entry_signal(h4_window[-3:], direction) if len(h4_window) >= 3 else None
        if h4_sig:
            entry_signal = h4_sig
            entry_price  = h4_sig['entry']
            entry_time   = candle_time
            entry_tf     = 'H4'

        # Check H1
        if not entry_signal:
            h1_sig = detect_entry_signal(h1_window[-3:], direction) if len(h1_window) >= 3 else None
            if h1_sig:
                entry_signal = h1_sig
                entry_price  = h1_sig['entry']
                entry_time   = candle_time
                entry_tf     = 'H1'

        # If no signal on current candle, wait up to 5 future H1 candles
        if not entry_signal:
            for j in range(1, 6):
                if i + j >= len(h1_all):
                    break
                future_window = h1_all[max(0, i+j-2): i+j+1]
                if len(future_window) < 3:
                    continue
                fut_sig = detect_entry_signal(future_window, direction)
                if fut_sig:
                    entry_signal = fut_sig
                    entry_price  = fut_sig['entry']
                    entry_time   = h1_all[i + j]['time']
                    entry_tf     = 'H1'
                    break

        # No entry signal found in 5 candles - skip setup
        if not entry_signal:
            continue

        # Recalculate grade with entry signal
        grade, pts = calc_grade(trends, aoi_zone, ema_sig, True)
        if grade != 'A+':
            continue

        # SL/TP based on entry price
        sl, tp1, tp2 = calc_sl_tp(direction, entry_price, aoi_zone, pair, all_aois)
        risk = abs(entry_price - sl)
        if risk == 0:
            continue

        open_trade = {
            'pair':         pair,
            'time':         entry_time,
            'direction':    direction,
            'entry':        entry_price,
            'entry_signal': entry_signal['pattern'],
            'entry_tf':     entry_tf,
            'sl':           sl,
            'tp1':          tp1,
            'tp2':          tp2,
            'grade':        grade,
            'pts':          pts,
            'result':       'OPEN',
            'pips':         0,
            'rr':           0,
        }

    # Close any remaining open trade as open
    if open_trade:
        open_trade['result'] = 'OPEN'
        trades.append(open_trade)

    return trades

# ─────────────────────────────────────────────
# PRINT RESULTS
# ─────────────────────────────────────────────
def print_results(all_trades):
    print(chr(10) + '=' * 60)
    print('  BACKTEST RESULTS - FXAlexG A+ Trades - 1 Year')
    print('=' * 60)

    if not all_trades:
        print('No trades found.')
        return

    closed = [t for t in all_trades if t['result'] != 'OPEN']
    wins   = [t for t in closed if t['result'] in ('TP1', 'TP2')]
    tp1s   = [t for t in closed if t['result'] == 'TP1']
    tp2s   = [t for t in closed if t['result'] == 'TP2']
    losses = [t for t in closed if t['result'] == 'SL']

    total     = len(closed)
    win_rate  = round(len(wins) / total * 100, 1) if total > 0 else 0
    avg_win   = round(sum(t['pips'] for t in wins)   / len(wins),   1) if wins   else 0
    avg_loss  = round(sum(t['pips'] for t in losses) / len(losses), 1) if losses else 0
    total_pips = round(sum(t['pips'] for t in closed), 1)
    pf = round(
        sum(t['pips'] for t in wins) / abs(sum(t['pips'] for t in losses)), 2
    ) if losses and sum(t['pips'] for t in losses) != 0 else 0

    # Max consecutive losses
    max_consec = 0
    curr_consec = 0
    for t in closed:
        if t['result'] == 'SL':
            curr_consec += 1
            max_consec = max(max_consec, curr_consec)
        else:
            curr_consec = 0

    print(chr(10) + 'OVERALL STATISTICS:')
    print('  Total A+ signals:       ' + str(len(all_trades)))
    print('  Total closed trades:    ' + str(total))
    print('  Still open:             ' + str(len(all_trades) - len(closed)))
    print('  Wins (TP1 + TP2):       ' + str(len(wins)))
    print('  TP1 hit:                ' + str(len(tp1s)))
    print('  TP2 hit:                ' + str(len(tp2s)))
    print('  Losses (SL):            ' + str(len(losses)))
    print('  Win Rate:               ' + str(win_rate) + '%')
    print('  Avg Win (pips):         +' + str(avg_win))
    print('  Avg Loss (pips):        ' + str(avg_loss))
    print('  Total Pips:             ' + str(total_pips))
    print('  Profit Factor:          ' + str(pf))
    print('  Max Consecutive Losses: ' + str(max_consec))

    print(chr(10) + 'RESULTS BY PAIR:')
    pairs = list(set(t['pair'] for t in closed))
    for pair in sorted(pairs):
        pt = [t for t in closed if t['pair'] == pair]
        pw = [t for t in pt if t['result'] in ('TP1', 'TP2')]
        wr = round(len(pw) / len(pt) * 100, 1) if pt else 0
        pp = round(sum(t['pips'] for t in pt), 1)
        print('  ' + pair.replace('_', '/') + ':')
        print('    Trades: ' + str(len(pt)) +
              '  Wins: ' + str(len(pw)) +
              '  Win Rate: ' + str(wr) + '%' +
              '  Total Pips: ' + str(pp))

    print(chr(10) + 'TRADE LOG (last 20 trades):')
    print('  Date                 Pair        Dir   Grade  Result  Pips')
    print('  ' + '-' * 58)
    for t in closed[-20:]:
        date   = t['time'][:10]
        sig    = t.get('entry_signal', 'N/A')[:18].ljust(18)
        tf     = t.get('entry_tf', 'N/A').ljust(3)
        print('  ' + date + '  ' +
              t['pair'].replace('_', '/').ljust(10) + '  ' +
              t['direction'].ljust(4) + '  ' +
              tf + '  ' +
              sig + '  ' +
              t['result'].ljust(6) + '  ' +
              str(t['pips']))

    print(chr(10) + '=' * 60)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print('=' * 60)
    print('  FXAlexG Backtester - A+ Trades Only - 1 Year')
    print('=' * 60)
    print('Pairs: ' + ', '.join(p.replace('_', '/') for p in PAIRS))
    print('Rules: Snake trick structure, AOI body closes,')
    print('       50 EMA, min 3 TFs, engulfing entry, 1:1 RR min')
    print(chr(10) + 'Fetching historical data and running backtest...')
    print('This may take 2-3 minutes.')

    all_trades = []
    for pair in PAIRS:
        try:
            trades = backtest_pair(pair)
            all_trades.extend(trades)
            closed = [t for t in trades if t['result'] != 'OPEN']
            wins   = [t for t in closed if t['result'] in ('TP1', 'TP2')]
            wr     = round(len(wins) / len(closed) * 100, 1) if closed else 0
            print('    Signals: ' + str(len(trades)) +
                  ' | Closed: ' + str(len(closed)) +
                  ' | Win Rate: ' + str(wr) + '%')
        except Exception as e:
            print('    ERROR: ' + str(e))

    print_results(all_trades)

if __name__ == '__main__':
    main()
