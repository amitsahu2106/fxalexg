# ═══════════════════════════════════════════════════════════════
# FXAlexG Backtest - 2 Years - Exact Same Logic as Live Monitor
# Entry on candle CLOSE only - no look-ahead bias
# Results printed to GitHub Actions logs
# ═══════════════════════════════════════════════════════════════
import requests, os, time
from datetime import datetime, timezone, timedelta

OANDA_API_KEY  = os.environ.get('OANDA_API_KEY', '')
OANDA_BASE_URL = 'https://api-fxpractice.oanda.com'

PAIRS = [
    'EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD',
    'USD_CHF', 'NZD_USD', 'USD_CAD', 'XAU_USD', 'BTC_USD'
]

EMA_PERIOD = 50

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

# ─── FETCH HISTORICAL DATA ───────────────────────────────────
def fetch_range(pair, granularity, from_dt, to_dt):
    url     = OANDA_BASE_URL + '/v3/instruments/' + pair + '/candles'
    headers = {'Authorization': 'Bearer ' + OANDA_API_KEY}
    params  = {
        'granularity': granularity,
        'from':        from_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'to':          to_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'price':       'M',
        'count':       5000,
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code != 200:
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
    # Fetch 2 years in chunks of 5000 candles
    now    = datetime.now(timezone.utc)
    start  = now - timedelta(days=730)
    all_c  = []
    chunk_start = start
    while chunk_start < now:
        chunk_end = min(chunk_start + timedelta(days=180), now)
        candles   = fetch_range(pair, granularity, chunk_start, chunk_end)
        all_c.extend(candles)
        if not candles:
            break
        chunk_start = chunk_end + timedelta(seconds=1)
        time.sleep(0.3)  # Rate limit protection
    # Deduplicate by time
    seen, unique = set(), []
    for c in all_c:
        if c['time'] not in seen:
            seen.add(c['time'])
            unique.append(c)
    return sorted(unique, key=lambda x: x['time'])

# ─── BODY HELPERS ────────────────────────────────────────────
def body_high(c): return max(c['open'], c['close'])
def body_low(c):  return min(c['open'], c['close'])

# ─── SWING POINTS (body only) ────────────────────────────────
def swing_points(candles, lookback=5):
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        w = candles[i - lookback: i + lookback + 1]
        if body_high(candles[i]) == max(body_high(c) for c in w):
            highs.append({'i': i, 'price': body_high(candles[i])})
        if body_low(candles[i]) == min(body_low(c) for c in w):
            lows.append({'i': i, 'price': body_low(candles[i])})
    return highs, lows

# ─── SNAKE TRICK MARKET STRUCTURE ────────────────────────────
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
    state = 'NEUTRAL'
    cur_hh = cur_hl = cur_ll = cur_lh = None
    for (pt, bi, price) in all_pts:
        if state == 'NEUTRAL':
            if pt == 'H':
                cur_hh = {'i': bi, 'price': price}
                cur_hl = snake_hl(bi, lows)
                state  = 'BULLISH'
            else:
                cur_ll = {'i': bi, 'price': price}
                cur_lh = snake_lh(bi, highs)
                state  = 'BEARISH'
        elif state == 'BULLISH':
            if pt == 'H' and price > cur_hh['price']:
                cur_hh = {'i': bi, 'price': price}
                hl = snake_hl(bi, lows)
                if hl: cur_hl = hl
            elif pt == 'L' and cur_hl and price < cur_hl['price']:
                cur_ll = {'i': bi, 'price': price}
                cur_lh = snake_lh(bi, highs)
                state  = 'BEARISH'
                cur_hh = cur_hl = None
        elif state == 'BEARISH':
            if pt == 'L' and price < cur_ll['price']:
                cur_ll = {'i': bi, 'price': price}
                lh = snake_lh(bi, highs)
                if lh: cur_lh = lh
            elif pt == 'H' and cur_lh and price > cur_lh['price']:
                cur_hh = {'i': bi, 'price': price}
                cur_hl = snake_hl(bi, lows)
                state  = 'BULLISH'
                cur_ll = cur_lh = None
    return state

# ─── 50 EMA ──────────────────────────────────────────────────
def ema50(candles):
    closes = [c['close'] for c in candles]
    if len(closes) < EMA_PERIOD:
        return None
    m   = 2 / (EMA_PERIOD + 1)
    val = sum(closes[:EMA_PERIOD]) / EMA_PERIOD
    for p in closes[EMA_PERIOD:]:
        val = (p - val) * m + val
    return val

# ─── AOI DETECTION ───────────────────────────────────────────
def count_touches_and_reversals(candles, zone_top, zone_bot, pair):
    touches = reversals = 0
    inside  = False
    entered_from = None
    for c in candles:
        close   = c['close']
        in_zone = (close >= zone_bot and close <= zone_top)
        if in_zone and not inside:
            inside       = True
            touches     += 1
            entered_from = 'above' if c['open'] > zone_top else 'below'
        elif not in_zone and inside:
            inside = False
            if entered_from == 'above' and close > zone_top:
                reversals += 1
            elif entered_from == 'below' and close < zone_bot:
                reversals += 1
    reversals = min(reversals, touches)
    return touches, reversals

def detect_aois(candles, pair):
    highs, lows = swing_points(candles, lookback=3)
    max_zone    = from_pips(60, pair)
    levels      = sorted([p['price'] for p in highs + lows])
    zones, i    = [], 0
    while i < len(levels):
        base = levels[i]
        j    = i + 1
        while j < len(levels) and levels[j] - base <= max_zone:
            j += 1
        cluster = levels[i:j]
        if len(cluster) >= 3:
            top     = max(cluster)
            bot     = min(cluster)
            sz_pips = to_pips(top - bot, pair)
            if sz_pips <= 60:
                touches, rev = count_touches_and_reversals(candles, top, bot, pair)
                if touches >= 5 and rev >= 4 and sz_pips <= 60:
                    tier, pts = 'STRONG', 2.5
                elif touches >= 3 and rev >= 3 and sz_pips <= 60:
                    tier, pts = 'GOOD', 1.5
                else:
                    i = j
                    continue
                zones.append({
                    'top': top, 'bottom': bot, 'mid': (top + bot) / 2,
                    'touches': touches, 'reversals': rev,
                    'tier': tier, 'aoi_pts': pts,
                    'size_pips': round(sz_pips, 1),
                })
        i = j
    zones.sort(key=lambda z: z['aoi_pts'], reverse=True)
    return zones

def at_aoi(price, zones, pair):
    buf     = from_pips(5, pair)
    matches = [z for z in zones if (z['bottom'] - buf) <= price <= (z['top'] + buf)]
    if not matches:
        return None
    return max(matches, key=lambda z: (z.get('aoi_pts', 1.0), z.get('reversals', 0)))

# ─── HEAD & SHOULDERS ────────────────────────────────────────
def detect_hs(candles, highs, lows):
    results = []
    sh = sorted(highs, key=lambda x: x['i'])
    for i in range(len(sh) - 2):
        ls, hd, rs = sh[i], sh[i+1], sh[i+2]
        if hd['price'] <= ls['price'] or hd['price'] <= rs['price']:
            continue
        diff = abs(ls['price'] - rs['price'])
        h    = hd['price'] - min(ls['price'], rs['price'])
        if diff > h * 0.5:
            continue
        seg1     = [c['low'] for c in candles[ls['i']:hd['i']]] or [0]
        seg2     = [c['low'] for c in candles[hd['i']:rs['i']]] or [0]
        neckline = (min(seg1) + min(seg2)) / 2
        complete = candles[-1]['close'] < neckline
        results.append({'type': 'HEAD & SHOULDERS', 'direction': 'BEARISH',
                        'neckline': neckline, 'complete': complete})
    sl = sorted(lows, key=lambda x: x['i'])
    for i in range(len(sl) - 2):
        ls, hd, rs = sl[i], sl[i+1], sl[i+2]
        if hd['price'] >= ls['price'] or hd['price'] >= rs['price']:
            continue
        diff     = abs(ls['price'] - rs['price'])
        d        = max(ls['price'], rs['price']) - hd['price']
        if diff > d * 0.5:
            continue
        seg1     = [c['high'] for c in candles[ls['i']:hd['i']]] or [0]
        seg2     = [c['high'] for c in candles[hd['i']:rs['i']]] or [0]
        neckline = (max(seg1) + max(seg2)) / 2
        complete = candles[-1]['close'] > neckline
        results.append({'type': 'INVERSE H&S', 'direction': 'BULLISH',
                        'neckline': neckline, 'complete': complete})
    complete_ones = [p for p in results if p['complete']]
    return complete_ones[0] if complete_ones else (results[-1] if results else None)

# ─── ENTRY SIGNAL ────────────────────────────────────────────
def detect_entry_signal(candles, direction):
    if len(candles) < 3:
        return None
    c1, prev, curr = candles[-3], candles[-2], candles[-1]
    ph   = max(prev['open'], prev['close'])
    pl   = min(prev['open'], prev['close'])
    ch   = max(curr['open'], curr['close'])
    cl   = min(curr['open'], curr['close'])
    ps   = abs(prev['close'] - prev['open'])
    cs   = abs(curr['close'] - curr['open'])
    tr   = curr['high'] - curr['low']
    pb   = prev['close'] < prev['open']
    cb   = curr['close'] > curr['open']

    # Engulfing
    if direction == 'BUY' and pb and cb and ch >= ph and cl <= pl and cs > ps * 0.8:
        return {'pattern': 'BULLISH ENGULFING', 'strength': 'STRONG'}
    if direction == 'SELL' and not pb and not cb and ch >= ph and cl <= pl and cs > ps * 0.8:
        return {'pattern': 'BEARISH ENGULFING', 'strength': 'STRONG'}

    # Pin Bar
    if tr > 0:
        uw = curr['high'] - ch
        lw = cl - curr['low']
        if direction == 'BUY' and lw >= cs * 2 and lw >= tr * 0.6 and cs <= tr * 0.35:
            return {'pattern': 'BULLISH PIN BAR', 'strength': 'MODERATE'}
        if direction == 'SELL' and uw >= cs * 2 and uw >= tr * 0.6 and cs <= tr * 0.35:
            return {'pattern': 'BEARISH PIN BAR', 'strength': 'MODERATE'}

    # Morning / Evening Star
    c1s = abs(c1['close'] - c1['open'])
    c2s = abs(prev['close'] - prev['open'])
    if direction == 'BUY' and c1['close'] < c1['open'] and c2s < c1s * 0.3 and cb and curr['close'] >= (c1['open'] + c1['close']) / 2 and cs >= c1s * 0.5:
        return {'pattern': 'MORNING STAR', 'strength': 'STRONG'}
    if direction == 'SELL' and c1['close'] > c1['open'] and c2s < c1s * 0.3 and not cb and curr['close'] <= (c1['open'] + c1['close']) / 2 and cs >= c1s * 0.5:
        return {'pattern': 'EVENING STAR', 'strength': 'STRONG'}

    # Marubozu
    if tr > 0 and cs / tr >= 0.85:
        if direction == 'BUY' and cb:
            return {'pattern': 'BULLISH MARUBOZU', 'strength': 'STRONG'}
        if direction == 'SELL' and not cb:
            return {'pattern': 'BEARISH MARUBOZU', 'strength': 'STRONG'}

    return None

# ─── SCORE - EXACT SAME AS MONITOR ──────────────────────────
def score(trends, aoi_zone, hs, ema_sig, entry_signal=None):
    pts = 0.0
    bullish_tfs = sum(1 for tf in ['W','D','H4','H1'] if trends.get(tf) == 'BULLISH')
    bearish_tfs = sum(1 for tf in ['W','D','H4','H1'] if trends.get(tf) == 'BEARISH')
    majority_dir = 'BULLISH' if bullish_tfs >= bearish_tfs else 'BEARISH'
    majority_tfs = max(bullish_tfs, bearish_tfs)

    tf_pts = {'W': 1.5, 'D': 1.5, 'H4': 1.0, 'H1': 1.0}
    for tf in ['W', 'D', 'H4', 'H1']:
        if trends.get(tf) == majority_dir:
            pts += tf_pts[tf]

    if aoi_zone:
        pts += aoi_zone.get('aoi_pts', 1.5)

    if ema_sig:
        ema_correct = (majority_dir == 'BULLISH' and ema_sig == 'ABOVE') or \
                      (majority_dir == 'BEARISH' and ema_sig == 'BELOW')
        if ema_correct:
            pts += 1.0

    if hs and hs.get('complete'):
        pts += 1.0

    if entry_signal:
        pts += 0.5

    pts = round(min(pts, 10.0), 1)

    if majority_tfs < 3:
        return 'D+', pts, majority_dir
    grade = 'A+' if pts >= 8.0 else 'B+' if pts >= 7.0 else 'C+' if pts >= 5.0 else 'D+'
    return grade, pts, majority_dir

# ─── ATR ─────────────────────────────────────────────────────
def calc_atr(candles, period=14):
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

# ─── SL/TP - EXACT SAME AS MONITOR ──────────────────────────
def calc_sl_tp(direction, price, aoi_zone, pair, all_aois, h1_candles):
    atr     = calc_atr(h1_candles)
    atr_buf = atr * 1.5 if atr else from_pips(10, pair)

    if direction == 'BUY' and aoi_zone:
        sl      = round(aoi_zone['bottom'] - atr_buf, 5)
        risk    = abs(price - sl)
        min_tp1 = price + risk * 2.0
        higher  = sorted([z for z in all_aois if z['bottom'] > aoi_zone['top']], key=lambda z: z['bottom'])
        tp1 = tp2 = None
        for idx, z in enumerate(higher):
            if z['top'] >= min_tp1:
                tp1 = round(max(min_tp1, z['bottom']), 5)
                tp2 = round(higher[idx+1]['bottom'], 5) if idx+1 < len(higher) else round(z['top'] + from_pips(5, pair), 5)
                break
        if tp1 is None:
            tp1 = round(price + risk * 2.0, 5)
            tp2 = round(price + risk * 3.0, 5)

    elif direction == 'SELL' and aoi_zone:
        sl      = round(aoi_zone['top'] + atr_buf, 5)
        risk    = abs(sl - price)
        min_tp1 = price - risk * 2.0
        lower   = sorted([z for z in all_aois if z['top'] < aoi_zone['bottom']], key=lambda z: z['top'], reverse=True)
        tp1 = tp2 = None
        for idx, z in enumerate(lower):
            if z['bottom'] <= min_tp1:
                tp1 = round(min(min_tp1, z['top']), 5)
                tp2 = round(lower[idx+1]['top'], 5) if idx+1 < len(lower) else round(z['bottom'] - from_pips(5, pair), 5)
                break
        if tp1 is None:
            tp1 = round(price - risk * 2.0, 5)
            tp2 = round(price - risk * 3.0, 5)
    else:
        atr_buf = atr * 1.5 if atr else from_pips(15, pair)
        sl   = round(price + atr_buf if direction == 'SELL' else price - atr_buf, 5)
        risk = abs(price - sl)
        tp1  = round(price - risk * 2.0 if direction == 'SELL' else price + risk * 2.0, 5)
        tp2  = round(price - risk * 3.0 if direction == 'SELL' else price + risk * 3.0, 5)

    return sl, tp1, tp2

# ─── SIMULATE TRADE ON CANDLE CLOSES ONLY ───────────────────
def simulate_trade(direction, entry, sl, tp1, tp2, future_candles):
    tp1_hit = False
    for c in future_candles:
        close = c['close']  # Use CLOSE only - no look-ahead
        if direction == 'BUY':
            if close <= sl:
                return 'SL', c['time'], close, tp1_hit
            if not tp1_hit and close >= tp1:
                tp1_hit = True  # Partial close at TP1, ride to TP2
            if tp1_hit and close >= tp2:
                return 'TP2', c['time'], close, tp1_hit
        else:  # SELL
            if close >= sl:
                return 'SL', c['time'], close, tp1_hit
            if not tp1_hit and close <= tp1:
                tp1_hit = True
            if tp1_hit and close <= tp2:
                return 'TP2', c['time'], close, tp1_hit
    # Check if TP1 hit but TP2 not reached by end of data
    if tp1_hit:
        return 'TP1', future_candles[-1]['time'], tp1, True
    return 'OPEN', None, entry, False

# ─── BACKTEST ONE PAIR ───────────────────────────────────────
def backtest_pair(pair, h1_all, w_all, d_all, h4_all):
    print(chr(10) + '  ' + pair.replace('_', '/') + '...')
    trades     = []
    open_trade = None
    WARMUP     = 120  # candles needed for indicators to stabilise

    for i in range(WARMUP, len(h1_all) - 5):
        candle_time = h1_all[i]['time']
        price       = h1_all[i]['close']  # Entry on CLOSE of candle

        # If trade open - check close-based SL/TP
        if open_trade:
            result, res_time, res_price, tp1_was_hit = simulate_trade(
                open_trade['direction'],
                open_trade['entry'],
                open_trade['sl'],
                open_trade['tp1'],
                open_trade['tp2'],
                h1_all[i:i+500]
            )
            if result != 'OPEN':
                entry = open_trade['entry']
                sl    = open_trade['sl']
                tp1   = open_trade['tp1']
                tp2   = open_trade['tp2']
                if result == 'TP2':
                    pips = to_pips(abs(res_price - entry), pair)
                    rr   = 3.0
                elif result == 'TP1':
                    pips = to_pips(abs(tp1 - entry), pair)
                    rr   = 2.0
                else:
                    pips = -to_pips(abs(entry - sl), pair)
                    rr   = -1.0
                open_trade.update({'result': result, 'exit_time': res_time,
                                   'exit_price': res_price, 'pips': pips, 'rr': rr})
                trades.append(open_trade)
                open_trade = None
            continue

        # Get candle windows up to current bar (no look-ahead)
        h1_w = h1_all[max(0, i-150):i+1]
        h4_w = [c for c in h4_all  if c['time'] <= candle_time][-80:]
        d_w  = [c for c in d_all   if c['time'] <= candle_time][-60:]
        w_w  = [c for c in w_all   if c['time'] <= candle_time][-30:]

        if len(h1_w) < 30 or len(h4_w) < 10:
            continue

        # Market structure each TF
        h1_h, h1_l = swing_points(h1_w)
        h4_h, h4_l = swing_points(h4_w)
        d_h,  d_l  = swing_points(d_w)  if len(d_w) > 10 else ([], [])
        w_h,  w_l  = swing_points(w_w)  if len(w_w) > 5  else ([], [])

        trends = {
            'H1': market_structure(h1_h, h1_l, h1_w),
            'H4': market_structure(h4_h, h4_l, h4_w),
            'D':  market_structure(d_h,  d_l,  d_w)  if d_w  else 'NEUTRAL',
            'W':  market_structure(w_h,  w_l,  w_w)  if w_w  else 'NEUTRAL',
        }

        bullish = sum(1 for t in ['W','D','H4','H1'] if trends.get(t) == 'BULLISH')
        bearish = sum(1 for t in ['W','D','H4','H1'] if trends.get(t) == 'BEARISH')
        if max(bullish, bearish) < 3:
            continue

        direction = 'BUY' if bullish > bearish else 'SELL'

        # AOI on D and W only
        all_aois = []
        if d_w:  all_aois.extend(detect_aois(d_w, pair))
        if w_w:  all_aois.extend(detect_aois(w_w, pair))
        aoi_zone = at_aoi(price, all_aois, pair)
        if not aoi_zone:
            continue

        # H&S on H4 and H1
        best_hs = None
        for (hx, lx, cx) in [(h4_h, h4_l, h4_w), (h1_h, h1_l, h1_w)]:
            hs = detect_hs(cx, hx, lx)
            if hs:
                if direction == 'SELL' and hs['direction'] != 'BEARISH': continue
                if direction == 'BUY'  and hs['direction'] != 'BULLISH': continue
                if best_hs is None or (hs['complete'] and not best_hs['complete']):
                    best_hs = hs

        # EMA
        h1_ema  = ema50(h1_w)
        ema_sig = None
        if h1_ema:
            ema_sig = 'ABOVE' if price > h1_ema else 'BELOW'

        # Entry signal - check H4 then H1 then wait up to 5 candles
        entry_signal = None
        entry_bar    = i
        entry_price  = price

        for tf_candles in [h4_w[-3:], h1_w[-3:]]:
            sig = detect_entry_signal(tf_candles, direction)
            if sig:
                entry_signal = sig
                break

        if not entry_signal:
            for j in range(1, 6):
                if i + j >= len(h1_all):
                    break
                fut = h1_all[max(0, i+j-2): i+j+1]
                if len(fut) < 3:
                    continue
                sig = detect_entry_signal(fut, direction)
                if sig:
                    entry_signal = sig
                    entry_bar    = i + j
                    entry_price  = h1_all[i + j]['close']
                    break

        if not entry_signal:
            continue

        # Score
        grade, pts, _ = score(trends, aoi_zone, best_hs, ema_sig, entry_signal)
        if grade != 'A+':
            continue

        # SL/TP
        sl, tp1, tp2 = calc_sl_tp(direction, entry_price, aoi_zone, pair, all_aois, h1_w)
        risk = abs(entry_price - sl)
        if risk == 0:
            continue

        open_trade = {
            'pair':         pair,
            'entry_time':   h1_all[entry_bar]['time'],
            'direction':    direction,
            'entry':        entry_price,
            'sl':           sl,
            'tp1':          tp1,
            'tp2':          tp2,
            'grade':        grade,
            'score':        pts,
            'pattern':      entry_signal['pattern'],
            'aoi_tier':     aoi_zone['tier'],
            'result':       'OPEN',
            'exit_time':    None,
            'exit_price':   None,
            'pips':         0,
            'rr':           0,
        }

    # Close any still-open trade as OPEN
    if open_trade:
        open_trade['result'] = 'OPEN'
        trades.append(open_trade)

    return trades

# ─── PRINT RESULTS ───────────────────────────────────────────
def print_results(all_trades):
    NL = chr(10)
    print(NL + '=' * 65)
    print('  FXAlexG BACKTEST RESULTS - A+ Trades - 2 Years')
    print('  Entry on candle CLOSE only - No look-ahead bias')
    print('=' * 65)

    if not all_trades:
        print('No trades found.')
        return

    closed = [t for t in all_trades if t['result'] != 'OPEN']
    tp2s   = [t for t in closed if t['result'] == 'TP2']
    tp1s   = [t for t in closed if t['result'] == 'TP1']
    sls    = [t for t in closed if t['result'] == 'SL']
    wins   = tp1s + tp2s

    total   = len(closed)
    wr      = round(len(wins) / total * 100, 1) if total else 0
    tot_pip = round(sum(t['pips'] for t in closed), 1)
    avg_win = round(sum(t['pips'] for t in wins) / len(wins), 1) if wins else 0
    avg_sl  = round(sum(t['pips'] for t in sls)  / len(sls),  1) if sls  else 0
    pf      = round(sum(t['pips'] for t in wins) / abs(sum(t['pips'] for t in sls)), 2) \
              if sls and sum(t['pips'] for t in sls) != 0 else 0

    # Max consecutive losses
    max_cl = cl = 0
    for t in closed:
        if t['result'] == 'SL':
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0

    print(NL + 'OVERALL:')
    print('  A+ signals found:       ' + str(len(all_trades)))
    print('  Closed trades:          ' + str(total))
    print('  Still open (end data):  ' + str(len(all_trades) - total))
    print('  TP2 (full target):      ' + str(len(tp2s)))
    print('  TP1 (half target):      ' + str(len(tp1s)))
    print('  SL (stopped out):       ' + str(len(sls)))
    print('  Win Rate (TP1+TP2):     ' + str(wr) + '%')
    print('  Avg Win (pips):         +' + str(avg_win))
    print('  Avg Loss (pips):        ' + str(avg_sl))
    print('  Total Pips:             ' + str(tot_pip))
    print('  Profit Factor:          ' + str(pf))
    print('  Max Consec. Losses:     ' + str(max_cl))

    print(NL + 'BY PAIR:')
    for pair in sorted(set(t['pair'] for t in closed)):
        pt = [t for t in closed if t['pair'] == pair]
        pw = [t for t in pt if t['result'] != 'SL']
        wr_p = round(len(pw) / len(pt) * 100, 1) if pt else 0
        pp_p = round(sum(t['pips'] for t in pt), 1)
        print('  ' + pair.replace('_', '/').ljust(10) +
              ' Trades:' + str(len(pt)).rjust(3) +
              '  Wins:' + str(len(pw)).rjust(3) +
              '  WR:' + str(wr_p).rjust(6) + '%' +
              '  Pips:' + str(pp_p).rjust(8))

    print(NL + 'LAST 30 TRADES:')
    print('  Date        Pair        Dir   Score  Pattern             Result  Pips')
    print('  ' + '-' * 63)
    for t in closed[-30:]:
        date    = t['entry_time'][:10]
        pattern = t.get('pattern', 'N/A')[:18].ljust(18)
        print('  ' + date + '  ' +
              t['pair'].replace('_', '/').ljust(10) + '  ' +
              t['direction'].ljust(5) + ' ' +
              str(t['score']).ljust(5) + '  ' +
              pattern + '  ' +
              t['result'].ljust(6) + '  ' +
              str(t['pips']))

    print(NL + '=' * 65)

# ─── MAIN ────────────────────────────────────────────────────
def main():
    print('=' * 65)
    print('  FXAlexG Backtester - A+ Only - 2 Years')
    print('  Same exact logic as live monitor')
    print('  Candle CLOSE used for all decisions')
    print('=' * 65)
    print('Fetching 2 years of data per pair...')
    print('Expected time: 5-8 minutes')

    all_trades = []
    for pair in PAIRS:
        try:
            print(chr(10) + 'Fetching ' + pair + '...')
            h1  = fetch_2years(pair, 'H1')
            h4  = fetch_2years(pair, 'H4')
            d   = fetch_2years(pair, 'D')
            w   = fetch_2years(pair, 'W')
            print('  H1:' + str(len(h1)) + ' H4:' + str(len(h4)) +
                  ' D:' + str(len(d)) + ' W:' + str(len(w)))

            if len(h1) < 200:
                print('  Not enough H1 data - skipping')
                continue

            trades = backtest_pair(pair, h1, w, d, h4)
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
