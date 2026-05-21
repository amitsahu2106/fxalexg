# ═══════════════════════════════════════════════════════════════
# FXAlexG Backtest - 2 Years - Exact Same Logic as Live Monitor
# Entry on candle CLOSE only - no look-ahead bias
# Results printed to GitHub Actions logs
# ═══════════════════════════════════════════════════════════════
import requests, os, time
from datetime import datetime, timezone, timedelta

OANDA_API_KEY      = os.environ.get('OANDA_API_KEY', '')
OANDA_BASE_URL     = 'https://api-fxpractice.oanda.com'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')

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
def fetch_chunk(pair, granularity, from_dt):
    # Fetch up to 5000 candles starting from from_dt
    # OANDA rule: use 'from' + 'count' only (never mix with 'to')
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
    # Paginate from 2 years ago to now using from+count
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=730)
    all_c = []
    cursor = start

    while cursor < now:
        chunk = fetch_chunk(pair, granularity, cursor)
        if not chunk:
            break
        all_c.extend(chunk)
        # Move cursor to just after last candle returned
        last_time = chunk[-1]['time']
        # Parse last time and advance by 1 second
        last_dt = datetime.strptime(last_time[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        next_cursor = last_dt + timedelta(seconds=1)
        if next_cursor <= cursor:
            break  # Safety - no progress
        cursor = next_cursor
        if len(chunk) < 100:
            break  # Last page
        time.sleep(0.5)

    # Deduplicate and sort
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

# ─── SL/TP - 1R and 2R based on ATR ─────────────────────────
# SL  = ATR * 1.5 beyond AOI edge
# TP1 = entry + risk * 1  (1:1 RR)
# TP2 = entry + risk * 2  (2:1 RR)
def calc_sl_tp(direction, price, aoi_zone, pair, all_aois, h1_candles):
    atr     = calc_atr(h1_candles)
    atr_buf = atr * 1.5 if atr else from_pips(10, pair)

    if direction == 'BUY' and aoi_zone:
        sl   = round(aoi_zone['bottom'] - atr_buf, 5)
        risk = abs(price - sl)
        tp1  = round(price + risk * 1.0, 5)   # TP1 = 1R
        tp2  = round(price + risk * 2.0, 5)   # TP2 = 2R

    elif direction == 'SELL' and aoi_zone:
        sl   = round(aoi_zone['top'] + atr_buf, 5)
        risk = abs(sl - price)
        tp1  = round(price - risk * 1.0, 5)   # TP1 = 1R
        tp2  = round(price - risk * 2.0, 5)   # TP2 = 2R

    else:
        atr_buf = atr * 1.5 if atr else from_pips(15, pair)
        if direction == 'SELL':
            sl  = round(price + atr_buf, 5)
            risk = abs(sl - price)
            tp1 = round(price - risk * 1.0, 5)
            tp2 = round(price - risk * 2.0, 5)
        else:
            sl  = round(price - atr_buf, 5)
            risk = abs(price - sl)
            tp1 = round(price + risk * 1.0, 5)
            tp2 = round(price + risk * 2.0, 5)

    return sl, tp1, tp2

# ─── SIMULATE TRADE ON CANDLE CLOSES ONLY ───────────────────
def simulate_trade(direction, entry, sl, tp1, tp2, future_candles):
    # Check candle closes only - no wicks
    # TP1 hit = WIN immediately (close half, move SL to BE)
    # If still running after TP1 and TP2 also hit = FULL WIN
    tp1_hit    = False
    tp1_time   = None
    tp1_price  = None

    for c in future_candles:
        close = c['close']  # Candle CLOSE only

        if direction == 'BUY':
            # SL check - after TP1 hit, SL moved to breakeven (entry)
            effective_sl = entry if tp1_hit else sl
            if close <= effective_sl:
                if tp1_hit:
                    return 'TP1', tp1_time, tp1_price, True  # Stopped at BE after TP1
                return 'SL', c['time'], close, False

            if not tp1_hit and close >= tp1:
                tp1_hit   = True
                tp1_time  = c['time']
                tp1_price = tp1
                # Continue riding to TP2

            if tp1_hit and close >= tp2:
                return 'TP2', c['time'], tp2, True

        else:  # SELL
            effective_sl = entry if tp1_hit else sl
            if close >= effective_sl:
                if tp1_hit:
                    return 'TP1', tp1_time, tp1_price, True  # Stopped at BE after TP1
                return 'SL', c['time'], close, False

            if not tp1_hit and close <= tp1:
                tp1_hit   = True
                tp1_time  = c['time']
                tp1_price = tp1

            if tp1_hit and close <= tp2:
                return 'TP2', c['time'], tp2, True

    if tp1_hit:
        return 'TP1', tp1_time, tp1_price, True
    return 'OPEN', None, entry, False

# ─── BACKTEST ONE PAIR ───────────────────────────────────────
def backtest_pair(pair, h1_all, w_all, d_all, h4_all):
    print(chr(10) + '  ' + pair.replace('_', '/') + '...')
    trades        = []
    open_trade    = None
    exit_bar      = -1   # bar index where last trade closed
    WARMUP        = 120  # candles needed for indicators to stabilise

    for i in range(WARMUP, len(h1_all) - 5):
        candle_time = h1_all[i]['time']
        price       = h1_all[i]['close']  # Entry on CLOSE of candle

        # If trade open - simulate forward from current bar
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
                    rr   = 2.0
                elif result == 'TP1':
                    pips = to_pips(abs(tp1 - entry), pair)
                    rr   = 1.0
                else:
                    pips = -to_pips(abs(entry - sl), pair)
                    rr   = -1.0
                open_trade.update({'result': result, 'exit_time': res_time,
                                   'exit_price': res_price, 'pips': pips, 'rr': rr})
                trades.append(open_trade)
                open_trade = None
                # Find exit bar index so we skip analysis until after trade closed
                exit_bar = next(
                    (j for j in range(i, min(i+500, len(h1_all)))
                     if h1_all[j]['time'] >= res_time),
                    i
                )
            continue

        # Block new trade until we are past the exit bar of last trade
        if i <= exit_bar:
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

# ─── SEND TELEGRAM ───────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print('  Telegram not configured - printing only')
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
    print('  FXAlexG BACKTEST RESULTS - A+ Trades - 2 Years')
    print('  Entry on candle CLOSE only - No look-ahead bias')
    print('  TP1 = 1R  |  TP2 = 2R')
    print(SEP)

    if not all_trades:
        msg = 'No A+ trades found in 2 years.'
        print(msg)
        send_telegram('<b>FXAlexG Backtest</b>' + NL + msg)
        return

    closed = [t for t in all_trades if t['result'] != 'OPEN']

    if not closed:
        msg = 'Signals found: ' + str(len(all_trades)) + ' but all still OPEN at end of data.'
        print(msg)
        send_telegram('<b>FXAlexG Backtest</b>' + NL + msg)
        return

    tp2s  = [t for t in closed if t['result'] == 'TP2']
    tp1s  = [t for t in closed if t['result'] == 'TP1']
    sls   = [t for t in closed if t['result'] == 'SL']
    wins  = tp1s + tp2s
    total = len(closed)

    wr      = round(len(wins) / total * 100, 1) if total else 0
    tot_pip = round(sum(t['pips'] for t in closed), 1)
    avg_win = round(sum(t['pips'] for t in wins) / len(wins), 1) if wins else 0
    avg_sl  = round(sum(t['pips'] for t in sls)  / len(sls),  1) if sls  else 0
    gross_win  = sum(t['pips'] for t in wins)
    gross_loss = abs(sum(t['pips'] for t in sls))
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0

    max_cl = cl = 0
    for t in closed:
        if t['result'] == 'SL':
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0

    print(NL + 'OVERALL STATISTICS:')
    print('  A+ signals found:        ' + str(len(all_trades)))
    print('  Closed trades:           ' + str(total))
    print('  Still open (end data):   ' + str(len(all_trades) - total))
    print('  TP2 hit (2R full win):   ' + str(len(tp2s)))
    print('  TP1 hit (1R win):        ' + str(len(tp1s)))
    print('  SL hit  (loss):          ' + str(len(sls)))
    print('  Win Rate (TP1 + TP2):    ' + str(wr) + '%')
    print('  Avg Win  (pips):         +' + str(avg_win))
    print('  Avg Loss (pips):         ' + str(avg_sl))
    print('  Total Pips P&L:          ' + str(tot_pip))
    print('  Profit Factor:           ' + str(pf))
    print('  Max Consecutive Losses:  ' + str(max_cl))

    print(NL + 'BY PAIR:')
    print('  ' + 'Pair'.ljust(10) + 'Trades  Wins    WR%    TP1  TP2   SL   Pips')
    print('  ' + '-' * 60)
    for pair in sorted(set(t['pair'] for t in closed)):
        pt   = [t for t in closed if t['pair'] == pair]
        pw   = [t for t in pt if t['result'] != 'SL']
        pt1  = [t for t in pt if t['result'] == 'TP1']
        pt2  = [t for t in pt if t['result'] == 'TP2']
        psl  = [t for t in pt if t['result'] == 'SL']
        wr_p = round(len(pw) / len(pt) * 100, 1) if pt else 0
        pp_p = round(sum(t['pips'] for t in pt), 1)
        print('  ' + pair.replace('_', '/').ljust(10) +
              str(len(pt)).rjust(5) +
              str(len(pw)).rjust(6) +
              str(wr_p).rjust(8) + '%' +
              str(len(pt1)).rjust(5) +
              str(len(pt2)).rjust(5) +
              str(len(psl)).rjust(5) +
              str(pp_p).rjust(8))

    print(NL + 'FULL TRADE LOG:')
    print('  ' + '-' * 110)
    print('  ' +
          'Entry Date'.ljust(12) +
          'Pair'.ljust(10) +
          'Dir'.ljust(5) +
          'Score'.ljust(6) +
          'Entry'.ljust(10) +
          'SL'.ljust(10) +
          'TP1'.ljust(10) +
          'TP2'.ljust(10) +
          'Result'.ljust(8) +
          'Exit Price'.ljust(12) +
          'Pips'.ljust(8) +
          'Pattern')
    print('  ' + '-' * 110)
    for t in all_trades:
        date    = t['entry_time'][:10]
        result  = t['result']
        ep      = str(round(t['entry'], 5))
        sl_s    = str(round(t['sl'],    5))
        tp1_s   = str(round(t['tp1'],   5))
        tp2_s   = str(round(t['tp2'],   5))
        xp      = str(round(t.get('exit_price', t['entry']), 5)) if t.get('exit_price') else 'OPEN'
        pips    = str(t['pips']) if result != 'OPEN' else '-'
        pattern = t.get('pattern', 'N/A')[:20]
        print('  ' +
              date.ljust(12) +
              t['pair'].replace('_', '/').ljust(10) +
              t['direction'].ljust(5) +
              str(t['score']).ljust(6) +
              ep.ljust(10) +
              sl_s.ljust(10) +
              tp1_s.ljust(10) +
              tp2_s.ljust(10) +
              result.ljust(8) +
              xp.ljust(12) +
              pips.ljust(8) +
              pattern)

    print(NL + SEP)

    # ── Send Telegram AFTER all variables are computed ────────
    tg = (
        '<b>FXAlexG Backtest - 2 Years</b>' + NL2 +
        'TP1=1R, TP2=2R | Candle close only' + NL2 + NL2 +
        '<b>OVERALL:</b>' + NL2 +
        'A+ Signals: '    + str(len(all_trades)) + NL2 +
        'Closed: '        + str(total)           + NL2 +
        'TP2 (2R): '      + str(len(tp2s))       + NL2 +
        'TP1 (1R): '      + str(len(tp1s))       + NL2 +
        'SL:       '      + str(len(sls))         + NL2 +
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

    # ── Send summary first (Telegram 4096 char limit per message) ──
    send_telegram(tg)

    # ── Send all trades in chunks of 30 ──────────────────────────
    chunk_size = 30
    for chunk_start in range(0, len(all_trades), chunk_size):
        chunk = all_trades[chunk_start:chunk_start + chunk_size]
        chunk_num = (chunk_start // chunk_size) + 1
        total_chunks = (len(all_trades) + chunk_size - 1) // chunk_size
        trades_msg = (
            '<b>All Trades ' +
            str(chunk_start + 1) + '-' +
            str(min(chunk_start + chunk_size, len(all_trades))) +
            ' of ' + str(len(all_trades)) +
            ' (Part ' + str(chunk_num) + '/' + str(total_chunks) + ')</b>' + NL2 +
            'Date        Pair      Dir  Entry      SL         TP1        TP2        Result  Pips' + NL2 +
            '-' * 90 + NL2
        )
        for t in chunk:
            trades_msg += (
                t['entry_time'][:10] + '  ' +
                t['pair'].replace('_', '/').ljust(8) + '  ' +
                t['direction'].ljust(4) + ' ' +
                str(round(t['entry'], 5)).ljust(10) + ' ' +
                str(round(t['sl'],    5)).ljust(10) + ' ' +
                str(round(t['tp1'],   5)).ljust(10) + ' ' +
                str(round(t['tp2'],   5)).ljust(10) + ' ' +
                t['result'].ljust(6) + '  ' +
                str(t['pips']) + 'p' + NL2
            )
        send_telegram(trades_msg)


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
