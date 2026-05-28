"""
FXAlexG + ICT Killzone Monitor
================================
Strategy 1 - FXAlexG Set & Forget (all 9 pairs)
  - Top-down analysis W/D/4H/1H
  - AOIs max 60 pips, min 3 touches
  - 50 EMA filter
  - Head & Shoulders pattern
  - Grades A+/B+/C+/D+

Strategy 2 - ICT Killzone (XAU/USD only)
  - Asian session range detection
  - London killzone (08:00-11:00 UTC)
  - New York killzone (13:00-16:00 UTC)
  - Liquidity sweep detection
  - Fair Value Gap detection
  - Alerts only inside killzones
"""

import requests
import time
import os
from datetime import datetime, timezone

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
OANDA_API_KEY      = os.environ.get("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID   = os.environ.get("OANDA_ACCOUNT_ID", "")
OANDA_BASE_URL     = "https://api-fxpractice.oanda.com"

GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL       = 'gemini-2.5-flash-lite'
GEMINI_URL         = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    + GEMINI_MODEL + ":generateContent"
)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# STRATEGY 1 - FXALEXG SETTINGS
# ─────────────────────────────────────────────
PAIRS = [
    'EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD',
    'USD_CHF', 'NZD_USD', 'USD_CAD', 'XAU_USD', 'BTC_USD'
]  # XAU_USD runs in both FXAlexG and ICT strategies
TIMEFRAMES         = ['W', 'D', 'H4', 'H1', 'M15']
EMA_PERIOD         = 50
MAX_AOI_PIPS       = 60
MIN_AOI_TOUCHES    = 3
MIN_GRADE_TO_ALERT = ['A+', 'B+']

# ─────────────────────────────────────────────
# STRATEGY 2 - ICT KILLZONE SETTINGS (XAU/USD)
# ─────────────────────────────────────────────
ASIAN_START_UTC    = 0    # 00:00 UTC
ASIAN_END_UTC      = 8    # 08:00 UTC
LONDON_START_UTC   = 8    # 08:00 UTC
LONDON_END_UTC     = 11   # 11:00 UTC
NY_START_UTC       = 13   # 13:00 UTC
NY_END_UTC         = 16   # 16:00 UTC
SWEEP_BUFFER_GOLD  = 0.50 # $0.50 buffer for sweep detection
FVG_MIN_SIZE_GOLD  = 0.30 # Minimum $0.30 FVG size
ICT_COOLDOWN_MIN   = 120  # 2 hour cooldown for ICT alerts

# ─────────────────────────────────────────────
# PIP HELPERS
# ─────────────────────────────────────────────
def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    if 'BTC' in pair: return 1.00
    return 0.0001

def to_pips(price_diff, pair):
    return price_diff / pip_size(pair)

def from_pips(pips, pair):
    return pips * pip_size(pair)

# ─────────────────────────────────────────────
# OANDA DATA FETCHER
# ─────────────────────────────────────────────
def fetch_candles(pair, granularity, count=120):
    url     = OANDA_BASE_URL + "/v3/instruments/" + pair + "/candles"
    headers = {"Authorization": "Bearer " + OANDA_API_KEY}
    params  = {"granularity": granularity, "count": count, "price": "M"}
    try:
        r = None
        for attempt in range(3):
            try:
                r = requests.get(url, headers=headers, params=params, timeout=30)
                break
            except Exception:
                print("  Fetch timeout " + pair + "/" + granularity +
                      " attempt " + str(attempt + 1) + "/3 - retrying...")
                if attempt < 2:
                    time.sleep(3)
        if r is None:
            print("  Fetch error " + pair + "/" + granularity + ": all retries failed")
            return []
        if r.status_code != 200:
            print("  OANDA error " + str(r.status_code) + " for " + pair + "/" + granularity)
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
        print("  Fetch error " + pair + "/" + granularity + ": " + str(e))
        return []

# ─────────────────────────────────────────────
# SWING POINT DETECTOR
# ─────────────────────────────────────────────
def body_high(candle):
    return max(candle['open'], candle['close'])

def body_low(candle):
    return min(candle['open'], candle['close'])

def swing_points(candles, lookback=2):
    # Body prices only - FXAlexG rule (no wicks)
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        window = candles[i - lookback: i + lookback + 1]
        if body_high(candles[i]) == max(body_high(c) for c in window):
            highs.append({'i': i, 'price': body_high(candles[i])})
        if body_low(candles[i]) == min(body_low(c) for c in window):
            lows.append({'i': i, 'price': body_low(candles[i])})
    return highs, lows

def snake_trick_hl(bar_i, lows):
    # Snake goes back from new HH - first turning point = Higher Low
    lows_before = sorted(
        [l for l in lows if l['i'] < bar_i],
        key=lambda x: x['i'], reverse=True
    )
    return lows_before[0] if lows_before else None

def snake_trick_lh(bar_i, highs):
    # Snake goes back from new LL - first turning point = Lower High
    highs_before = sorted(
        [h for h in highs if h['i'] < bar_i],
        key=lambda x: x['i'], reverse=True
    )
    return highs_before[0] if highs_before else None

# ─────────────────────────────────────────────
# MARKET STRUCTURE - FXAlexG Snake Trick
# State machine: tracks HH/HL and LH/LL
# Body close confirmation required to shift state
# Snake trick used to place HL and LH correctly
# ─────────────────────────────────────────────
def trend(highs, lows, candles=None):
    if len(highs) < 2 or len(lows) < 2:
        return 'NEUTRAL'
    if candles is None or len(candles) < 10:
        return 'NEUTRAL'

    # Merge and sort all swing points chronologically
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
                current_hl = snake_trick_hl(bar_i, lows)
                state = 'BULLISH'
            else:
                current_ll = {'i': bar_i, 'price': price}
                current_lh = snake_trick_lh(bar_i, highs)
                state = 'BEARISH'

        elif state == 'BULLISH':
            if ptype == 'H' and price > current_hh['price']:
                # New Higher High confirmed - update HH and find new HL
                current_hh = {'i': bar_i, 'price': price}
                new_hl = snake_trick_hl(bar_i, lows)
                if new_hl:
                    current_hl = new_hl
            elif ptype == 'L':
                if current_hl and price < current_hl['price']:
                    # Body closed BELOW Higher Low - shift BEARISH
                    current_ll = {'i': bar_i, 'price': price}
                    current_lh = snake_trick_lh(bar_i, highs)
                    state      = 'BEARISH'
                    current_hh = None
                    current_hl = None

        elif state == 'BEARISH':
            if ptype == 'L' and price < current_ll['price']:
                # New Lower Low confirmed - update LL and find new LH
                current_ll = {'i': bar_i, 'price': price}
                new_lh = snake_trick_lh(bar_i, highs)
                if new_lh:
                    current_lh = new_lh
            elif ptype == 'H':
                if current_lh and price > current_lh['price']:
                    # Body closed ABOVE Lower High - shift BULLISH
                    current_hh = {'i': bar_i, 'price': price}
                    current_hl = snake_trick_hl(bar_i, lows)
                    state      = 'BULLISH'
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
# AOI DETECTOR (FXAlexG)
# ─────────────────────────────────────────────
def count_touches_and_reversals(candles, zone_top, zone_bot, pair):
    # Count how many times price ENTERED the zone (touches)
    # and how many times it REVERSED direction after entering (reversals)
    # Rule: reversals can NEVER exceed touches
    mid          = (zone_top + zone_bot) / 2
    touches      = 0
    reversals    = 0
    inside       = False
    entered_from = None

    for c in candles:
        # Use BODY closes only - price must actually close inside zone
        bh      = body_high(c)
        bl      = body_low(c)
        close   = c['close']

        # Price is inside zone if body close is within zone bounds
        in_zone = (close >= zone_bot and close <= zone_top)

        if in_zone and not inside:
            # Price just entered the zone
            inside       = True
            touches     += 1
            # Entered from above if it came down into zone
            entered_from = 'above' if c['open'] > zone_top else 'below'

        elif not in_zone and inside:
            # Price just left the zone
            inside = False
            # Only count reversal if it left the OPPOSITE side it entered from
            if entered_from == 'above' and close > zone_top:
                # Entered from above, left from above = REVERSAL (bounced up)
                reversals += 1
            elif entered_from == 'below' and close < zone_bot:
                # Entered from below, left from below = REVERSAL (bounced down)
                reversals += 1
            # else: price broke through = NOT a reversal

    # Safety cap - reversals cannot exceed touches
    reversals = min(reversals, touches)
    return touches, reversals

def detect_aois(candles, pair):
    # Two-tier AOI system:
    # STRONG AOI: >= 5 touches, >= 4 reversals, <= 60 pips -> 2.5 pts
    # GOOD AOI:   >= 3 touches, >= 3 reversals, <= 60 pips -> 1.5 pts
    # touches and reversals from SAME loop - always consistent
    highs, lows = swing_points(candles, lookback=2)
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
                # Single unified function - reversals can never exceed touches
                touches, rev = count_touches_and_reversals(candles, top, bot, pair)
                if touches >= 5 and rev >= 4 and sz_pips <= 60:
                    tier = 'STRONG'
                    pts  = 2.5
                elif touches >= 3 and rev >= 3 and sz_pips <= 60:
                    tier = 'GOOD'
                    pts  = 1.5
                else:
                    i = j
                    continue
                zones.append({
                    'top':       top,
                    'bottom':    bot,
                    'mid':       (top + bot) / 2,
                    'touches':   touches,
                    'reversals': rev,
                    'tier':      tier,
                    'aoi_pts':   pts,
                    'size_pips': round(sz_pips, 1),
                })
        i = j
    zones.sort(key=lambda z: z['aoi_pts'], reverse=True)
    return zones


def at_aoi(price, zones, pair):
    # Find all zones price is currently inside
    buf      = from_pips(5, pair)
    matches  = [z for z in zones if (z['bottom'] - buf) <= price <= (z['top'] + buf)]
    if not matches:
        return None
    # Return the STRONGEST matching zone (highest aoi_pts, then most reversals)
    return max(matches, key=lambda z: (z.get('aoi_pts', 1.0), z.get('reversals', 0)))

# ─────────────────────────────────────────────
# HEAD & SHOULDERS DETECTOR (FXAlexG)
# ─────────────────────────────────────────────
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
        results.append({
            'type': 'HEAD & SHOULDERS', 'direction': 'BEARISH',
            'left_shoulder': round(ls['price'], 5),
            'head': round(hd['price'], 5),
            'right_shoulder': round(rs['price'], 5),
            'neckline': round(neckline, 5), 'complete': complete,
        })
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
        results.append({
            'type': 'INVERSE H&S', 'direction': 'BULLISH',
            'left_shoulder': round(ls['price'], 5),
            'head': round(hd['price'], 5),
            'right_shoulder': round(rs['price'], 5),
            'neckline': round(neckline, 5), 'complete': complete,
        })
    complete_ones = [p for p in results if p['complete']]
    return complete_ones[0] if complete_ones else (results[-1] if results else None)

# ─────────────────────────────────────────────
# CANDLESTICK ENTRY SIGNAL DETECTOR
# FXAlexG rule: need engulfing to enter
# Primary:   Engulfing (bearish/bullish)
# Secondary: Pin Bar
# Tertiary:  Evening Star / Morning Star
# Other:     Marubozu
# All use BODY closes only - no wicks
# ─────────────────────────────────────────────
def detect_entry_signal(candles, direction):
    if len(candles) < 3:
        return None

    c1   = candles[-3]
    prev = candles[-2]
    curr = candles[-1]

    prev_body_high = max(prev['open'], prev['close'])
    prev_body_low  = min(prev['open'], prev['close'])
    curr_body_high = max(curr['open'], curr['close'])
    curr_body_low  = min(curr['open'], curr['close'])
    prev_body_size = abs(prev['close'] - prev['open'])
    curr_body_size = abs(curr['close'] - curr['open'])
    prev_is_bull   = prev['close'] > prev['open']
    prev_is_bear   = prev['close'] < prev['open']
    curr_is_bull   = curr['close'] > curr['open']
    curr_is_bear   = curr['close'] < curr['open']
    total_range    = curr['high'] - curr['low']

    # 1. ENGULFING - Primary signal Alex always looks for
    if direction == 'SELL':
        if (prev_is_bull and curr_is_bear and
                curr_body_high >= prev_body_high and
                curr_body_low <= prev_body_low and
                curr_body_size > prev_body_size * 0.8):
            return {'pattern': 'BEARISH ENGULFING', 'strength': 'STRONG',
                    'direction': 'SELL', 'entry': round(curr['close'], 5)}

    if direction == 'BUY':
        if (prev_is_bear and curr_is_bull and
                curr_body_high >= prev_body_high and
                curr_body_low <= prev_body_low and
                curr_body_size > prev_body_size * 0.8):
            return {'pattern': 'BULLISH ENGULFING', 'strength': 'STRONG',
                    'direction': 'BUY', 'entry': round(curr['close'], 5)}

    # 2. PIN BAR
    if total_range > 0:
        upper_wick = curr['high'] - curr_body_high
        lower_wick = curr_body_low - curr['low']

        if (direction == 'SELL' and
                upper_wick >= curr_body_size * 2 and
                upper_wick >= total_range * 0.6 and
                curr_body_size <= total_range * 0.35):
            return {'pattern': 'BEARISH PIN BAR', 'strength': 'MODERATE',
                    'direction': 'SELL', 'entry': round(curr['close'], 5)}

        if (direction == 'BUY' and
                lower_wick >= curr_body_size * 2 and
                lower_wick >= total_range * 0.6 and
                curr_body_size <= total_range * 0.35):
            return {'pattern': 'BULLISH PIN BAR', 'strength': 'MODERATE',
                    'direction': 'BUY', 'entry': round(curr['close'], 5)}

    # 3. EVENING STAR / MORNING STAR
    c1_body_size = abs(c1['close'] - c1['open'])
    c2_body_size = abs(prev['close'] - prev['open'])
    c3_body_size = curr_body_size

    if direction == 'SELL':
        if (c1['close'] > c1['open'] and
                c2_body_size < c1_body_size * 0.3 and
                curr_is_bear and
                curr['close'] <= (c1['open'] + c1['close']) / 2 and
                c3_body_size >= c1_body_size * 0.5):
            return {'pattern': 'EVENING STAR', 'strength': 'STRONG',
                    'direction': 'SELL', 'entry': round(curr['close'], 5)}

    if direction == 'BUY':
        if (c1['close'] < c1['open'] and
                c2_body_size < c1_body_size * 0.3 and
                curr_is_bull and
                curr['close'] >= (c1['open'] + c1['close']) / 2 and
                c3_body_size >= c1_body_size * 0.5):
            return {'pattern': 'MORNING STAR', 'strength': 'STRONG',
                    'direction': 'BUY', 'entry': round(curr['close'], 5)}

    # 4. MARUBOZU
    if total_range > 0 and curr_body_size / total_range >= 0.85:
        if direction == 'SELL' and curr_is_bear:
            return {'pattern': 'BEARISH MARUBOZU', 'strength': 'STRONG',
                    'direction': 'SELL', 'entry': round(curr['close'], 5)}
        if direction == 'BUY' and curr_is_bull:
            return {'pattern': 'BULLISH MARUBOZU', 'strength': 'STRONG',
                    'direction': 'BUY', 'entry': round(curr['close'], 5)}

    return None

# ─────────────────────────────────────────────
# CONFLUENCE SCORER (FXAlexG)
# ─────────────────────────────────────────────
def score(trends, aoi_zone, hs, ema_sig, entry_signal=None, entry_tf=None):
    # ─── POINT SYSTEM ─────────────────────────────
    # Weekly Trend:     1.5
    # Daily Trend:      1.5
    # H4 Trend:         1.0
    # H1 Trend:         1.0
    # Strong AOI:       2.5  (5+ touches, 4+ reversals, <=45 pips)
    # Good AOI:         1.5  (3+ touches, 3+ reversals, <=60 pips)
    # 50 EMA:           1.0  (price on correct side for direction)
    # Head & Shoulders: 1.0
    # Entry Signal:     0.5
    # MAX TOTAL:        10.0  A+ >= 8.0  B+ >= 7.0
    # ──────────────────────────────────────────────
    pts     = 0.0
    reasons = []

    # Calculate majority direction ONCE before loop
    bullish_tfs = sum(1 for tf in ['W','D','H4','H1'] if trends.get(tf) == 'BULLISH')
    bearish_tfs = sum(1 for tf in ['W','D','H4','H1'] if trends.get(tf) == 'BEARISH')
    majority_dir = 'BULLISH' if bullish_tfs >= bearish_tfs else 'BEARISH'
    majority_tfs = max(bullish_tfs, bearish_tfs)

    # TF points - only W/D/H4/H1, NOT M15
    tf_pts   = {'W': 1.5, 'D': 1.5, 'H4': 1.0, 'H1': 1.0}
    tf_names = {'W': 'Weekly', 'D': 'Daily', 'H4': '4H', 'H1': '1H'}

    for tf in ['W', 'D', 'H4', 'H1']:
        t = trends.get(tf, 'NEUTRAL')
        if t == majority_dir:
            pts += tf_pts[tf]
            reasons.append(tf_names[tf] + ': ' + t + ' (+' + str(tf_pts[tf]) + ')')
        elif t == 'NEUTRAL':
            reasons.append(tf_names[tf] + ': NEUTRAL')
        else:
            # Opposite direction - no points, flag it
            reasons.append(tf_names[tf] + ': ' + t + ' (against majority)')

    # AOI points
    if aoi_zone:
        aoi_pts = aoi_zone.get('aoi_pts', 1.0)
        tier    = aoi_zone.get('tier', 'GOOD')
        pts    += aoi_pts
        reasons.append(
            tier + ' AOI ' + str(aoi_zone['size_pips']) + 'p, ' +
            str(aoi_zone['touches']) + ' touches, ' +
            str(aoi_zone.get('reversals', 0)) + ' reversals (+' + str(aoi_pts) + ')'
        )
    else:
        reasons.append('Not at AOI')

    # EMA - only counts if price is on correct side for the direction
    if ema_sig:
        ema_correct = (majority_dir == 'BULLISH' and ema_sig == 'ABOVE') or                       (majority_dir == 'BEARISH' and ema_sig == 'BELOW')
        if ema_correct:
            pts += 1.0
            side = 'above' if ema_sig == 'ABOVE' else 'below'
            reasons.append('H1 price ' + side + ' 50 EMA (+1.0)')
        else:
            side = 'above' if ema_sig == 'ABOVE' else 'below'
            reasons.append('H1 price ' + side + ' 50 EMA (against direction)')

    # H&S
    if hs and hs['complete']:
        pts += 1.0
        reasons.append(hs['type'] + ' (' + hs['direction'] + ') COMPLETE (+1.0)')
    elif hs:
        reasons.append(hs['type'] + ' (' + hs['direction'] + ') forming')

    # Entry signal
    if entry_signal and entry_tf:
        pts += 0.5
        reasons.append(entry_signal['pattern'] + ' on ' + entry_tf + ' (' + entry_signal['strength'] + ') (+0.5)')
    else:
        reasons.append('No entry signal yet')

    # Cap at 10
    pts = round(min(pts, 10.0), 1)

    # Grade based purely on score (honest display in scorecard)
    grade = 'A+' if pts >= 8.0 else 'B+' if pts >= 7.0 else 'C+' if pts >= 5.0 else 'D+'

    # Alert eligibility: need 3+ TFs aligned AND A+/B+ score
    # Store tf count in reasons for transparency
    if majority_tfs < 3:
        reasons.append('Only ' + str(majority_tfs) + '/4 TFs aligned - need 3 minimum (no alert)')

    return grade, pts, reasons

# ─────────────────────────────────────────────
# ICT - ASIAN SESSION RANGE
# ─────────────────────────────────────────────
def get_asian_range(m15_candles):
    asian_candles = []
    for c in m15_candles:
        try:
            hour = int(c['time'][11:13])
            if ASIAN_START_UTC <= hour < ASIAN_END_UTC:
                asian_candles.append(c)
        except:
            continue
    if len(asian_candles) < 4:
        return None
    return {
        'high': max(c['high'] for c in asian_candles),
        'low':  min(c['low']  for c in asian_candles),
        'candles': len(asian_candles),
    }

# ─────────────────────────────────────────────
# ICT - KILLZONE CHECK
# ─────────────────────────────────────────────
def in_killzone(now_utc):
    h = now_utc.hour
    if LONDON_START_UTC <= h < LONDON_END_UTC:
        return 'LONDON'
    if NY_START_UTC <= h < NY_END_UTC:
        return 'NEW YORK'
    return None

# ─────────────────────────────────────────────
# ICT - LIQUIDITY SWEEP DETECTOR
# ─────────────────────────────────────────────
def detect_sweep(m15_candles, asian_range):
    if not asian_range or len(m15_candles) < 3:
        return None
    asian_high = asian_range['high']
    asian_low  = asian_range['low']
    recent     = m15_candles[-6:]

    for i in range(1, len(recent) - 1):
        prev  = recent[i - 1]
        curr  = recent[i]
        nxt   = recent[i + 1]

        # Bearish sweep - spike above Asian high then close back below
        if (curr['high'] > asian_high + SWEEP_BUFFER_GOLD and
                curr['close'] < asian_high and
                nxt['close'] < curr['close']):
            return {
                'type':      'BEARISH SWEEP',
                'direction': 'SELL',
                'swept':     round(curr['high'], 2),
                'level':     round(asian_high, 2),
                'entry':     round(curr['close'], 2),
            }

        # Bullish sweep - spike below Asian low then close back above
        if (curr['low'] < asian_low - SWEEP_BUFFER_GOLD and
                curr['close'] > asian_low and
                nxt['close'] > curr['close']):
            return {
                'type':      'BULLISH SWEEP',
                'direction': 'BUY',
                'swept':     round(curr['low'], 2),
                'level':     round(asian_low, 2),
                'entry':     round(curr['close'], 2),
            }
    return None

# ─────────────────────────────────────────────
# ICT - FAIR VALUE GAP DETECTOR
# ─────────────────────────────────────────────
def detect_fvg(m15_candles):
    if len(m15_candles) < 3:
        return None
    fvgs = []
    for i in range(1, len(m15_candles) - 1):
        c1 = m15_candles[i - 1]
        c3 = m15_candles[i + 1]

        # Bullish FVG - gap between c1 high and c3 low
        if c3['low'] > c1['high'] and (c3['low'] - c1['high']) >= FVG_MIN_SIZE_GOLD:
            fvgs.append({
                'type':      'BULLISH FVG',
                'direction': 'BUY',
                'top':       round(c3['low'], 2),
                'bottom':    round(c1['high'], 2),
                'size':      round(c3['low'] - c1['high'], 2),
            })

        # Bearish FVG - gap between c1 low and c3 high
        if c1['low'] > c3['high'] and (c1['low'] - c3['high']) >= FVG_MIN_SIZE_GOLD:
            fvgs.append({
                'type':      'BEARISH FVG',
                'direction': 'SELL',
                'top':       round(c1['low'], 2),
                'bottom':    round(c3['high'], 2),
                'size':      round(c1['low'] - c3['high'], 2),
            })
    return fvgs[-1] if fvgs else None

# ─────────────────────────────────────────────
# GEMINI AI CALL
# ─────────────────────────────────────────────
def ask_gemini(prompt):
    try:
        r = requests.post(
            GEMINI_URL + "?key=" + GEMINI_API_KEY,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()['candidates'][0]['content']['parts'][0]['text']
        print("  Gemini error " + str(r.status_code) + ": " + r.text[:200])
    except Exception as e:
        print("  Gemini failed: " + str(e))
    return None

def ask_gemini_fxalexg(pair, data):
    aoi_txt = (
        "YES " + str(data['aoi']['bottom']) + " to " + str(data['aoi']['top']) +
        " (" + str(data['aoi']['size_pips']) + " pips, " + str(data['aoi']['touches']) + " touches)"
        if data['aoi'] else "No"
    )
    hs_txt = (
        data['hs']['type'] + " (" + data['hs']['direction'] + ") Neckline:" +
        str(data['hs']['neckline']) + " " + ("COMPLETE" if data['hs']['complete'] else "Forming")
        if data['hs'] else "Not detected"
    )
    es    = data.get('entry_signal')
    es_tf = data.get('entry_tf', '')
    es_txt = (
        es['pattern'] + ' on ' + es_tf + ' (' + es['strength'] + ') Entry at ' + str(es['entry'])
        if es else 'None detected yet - wait for candle confirmation'
    )

    prompt = (
        "You are an expert forex analyst using the FXAlexG Set and Forget strategy.\n"
        "Analyse this setup and give a clear mobile-friendly trade recommendation.\n\n"
        "PAIR: " + pair.replace('_', '/') + "\n"
        "PRICE: " + str(data['price']) + "\n"
        "TIME: " + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') + "\n\n"
        "TREND (top-down):\n"
        "  Weekly : " + data['trends'].get('W', 'N/A') + "\n"
        "  Daily  : " + data['trends'].get('D', 'N/A') + "\n"
        "  4H     : " + data['trends'].get('H4', 'N/A') + "\n"
        "  1H     : " + data['trends'].get('H1', 'N/A') + "\n\n"
        "AOI ZONE      : " + aoi_txt + "\n"
        "50 EMA (H1)   : " + str(data['ema']) + " Price is " + str(data['ema_sig']) + "\n"
        "H&S PATTERN   : " + hs_txt + "\n"
        "ENTRY SIGNAL  : " + es_txt + "\n\n"
        "GRADE: " + data['grade'] + " Score: " + str(data['pts']) + "/10\n"
        "CONFLUENCES: " + ", ".join(data['reasons']) + "\n\n"
        "Reply with EXACTLY this format:\n"
        "DIRECTION: " + str(data.get("direction", "N/A")) + "\n"
        "ENTRY: " + str(data["price"]) + "\n"
        "STOP LOSS: " + str(data["sl"]) + " (above/below AOI)\n"
        "TAKE PROFIT 1: " + str(data["tp1"]) + " (next AOI)\n"
        "TAKE PROFIT 2: " + str(data["tp2"]) + " (next AOI far edge)\n"
        "ANALYSIS: [2-3 sentences validating the SL and TP levels]\n"
        "RISK NOTE: [one sentence]"
    )
    return ask_gemini(prompt)

def ask_gemini_ict(data):
    prompt = (
        "You are an expert gold trader using ICT (Inner Circle Trader) concepts.\n"
        "Analyse this XAU/USD killzone setup and give a trade recommendation.\n\n"
        "PAIR: XAU/USD (Gold)\n"
        "PRICE: " + str(data['price']) + "\n"
        "TIME: " + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') + "\n"
        "KILLZONE: " + data['killzone'] + "\n\n"
        "ASIAN SESSION RANGE:\n"
        "  High: " + str(data['asian']['high']) + "\n"
        "  Low : " + str(data['asian']['low']) + "\n"
        "  Range: " + str(round(data['asian']['high'] - data['asian']['low'], 2)) + " points\n\n"
        "LIQUIDITY SWEEP: " + (
            data['sweep']['type'] + " at " + str(data['sweep']['swept']) +
            " (Asian level: " + str(data['sweep']['level']) + ")"
            if data['sweep'] else "Not detected"
        ) + "\n"
        "FAIR VALUE GAP: " + (
            data['fvg']['type'] + " " + str(data['fvg']['bottom']) +
            " to " + str(data['fvg']['top']) + " (size: " + str(data['fvg']['size']) + ")"
            if data['fvg'] else "Not detected"
        ) + "\n"
        "DAILY BIAS: " + data['daily_bias'] + "\n\n"
        "Reply with EXACTLY this format:\n"
        "DIRECTION: [BUY / SELL / WAIT]\n"
        "ENTRY: [price or zone]\n"
        "STOP LOSS: [price]\n"
        "TAKE PROFIT 1: [price 2R]\n"
        "TAKE PROFIT 2: [price 3R]\n"
        "ANALYSIS: [2-3 sentences about why this is valid ICT setup]\n"
        "RISK NOTE: [one sentence]"
    )
    return ask_gemini(prompt)

# ─────────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────────
def send_telegram(msg):
    try:
        r = requests.post(
            "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=30,
        )
        if r.status_code != 200:
            print("  Telegram error " + str(r.status_code))
    except Exception as e:
        print("  Telegram failed: " + str(e))

# ─────────────────────────────────────────────
# TRADE TRACKER
# Stores active trades in trades.json on disk
# Tracks P&L, alerts on TP/SL hit
# ─────────────────────────────────────────────
import json

TRADES_FILE = 'trades.json'

def load_trades():
    try:
        with open(TRADES_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_trades(trades):
    try:
        with open(TRADES_FILE, 'w') as f:
            json.dump(trades, f, indent=2)
    except Exception as e:
        print('  Error saving trades: ' + str(e))

def add_trade(pair, direction, entry, sl, tp1, tp2, tp3, aoi_zone):
    trades  = load_trades()
    now     = datetime.now(timezone.utc)
    # Key = pair + date/time e.g. EUR_USD_2025-05-22_14:00
    trade_key = pair + '_' + now.strftime('%Y-%m-%d_%H:%M')
    trades[trade_key] = {
        'pair':       pair,
        'strategy':   'FXALEXG',
        'trade_key':  trade_key,
        'direction':  direction,
        'entry':      entry,
        'sl':         sl,
        'tp1':        tp1,
        'tp2':        tp2,
        'tp3':        tp3,
        'tp1_hit':    False,
        'tp2_hit':    False,
        'aoi_top':    aoi_zone['top']    if aoi_zone else None,
        'aoi_bottom': aoi_zone['bottom'] if aoi_zone else None,
        'opened_at':  now.strftime('%Y-%m-%d %H:%M UTC'),
        'opened_ts':  now.timestamp(),
    }
    save_trades(trades)
    print('  Trade added: ' + trade_key)

def remove_trade(trade_key):
    trades = load_trades()
    if trade_key in trades:
        del trades[trade_key]
        save_trades(trades)

def calc_pips(price_diff, pair):
    return round(price_diff / pip_size(pair), 1)

def calc_atr(candles, period=14):
    # Average True Range over last `period` candles
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        high  = candles[i]['high']
        low   = candles[i]['low']
        prev_close = candles[i-1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / period

# ─── GOLD 2R STRATEGY HELPERS ────────────────────────────────
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

def macd_histogram(values, fast=12, slow=26, signal=9):
    # Returns MACD histogram series
    ema_fast = ema_series(values, fast)
    ema_slow = ema_series(values, slow)
    if not ema_fast or not ema_slow:
        return []

    macd_line = []
    for i in range(len(values)):
        f = ema_fast[i]
        s = ema_slow[i]
        if f is None or s is None:
            macd_line.append(None)
        else:
            macd_line.append(f - s)

    valid_indices = [i for i, v in enumerate(macd_line) if v is not None]
    valid_macd    = [macd_line[i] for i in valid_indices]
    signal_vals   = ema_series(valid_macd, signal)
    signal_series = [None] * len(values)
    for idx, vi in enumerate(valid_indices):
        if idx < len(signal_vals) and signal_vals[idx] is not None:
            signal_series[vi] = signal_vals[idx]

    hist_series = []
    for i in range(len(values)):
        m_val = macd_line[i]
        s_val = signal_series[i]
        if m_val is None or s_val is None:
            hist_series.append(None)
        else:
            hist_series.append(m_val - s_val)
    return hist_series


def calc_sl_tp(direction, price, aoi_zone, pair, all_aois, candles=None):
    # TP1 = 1R (entry +/- risk)
    # TP2 = mid of first AOI beyond TP1 (was old TP1)
    # TP3 = bottom/top of that same AOI (was old TP2)
    # SL  = AOI edge - 1.5 x ATR
    atr     = calc_atr(candles) if candles else None
    atr_buf = atr * 1.5 if atr else from_pips(10, pair)

    if direction == 'BUY' and aoi_zone:
        sl   = round(aoi_zone['bottom'] - atr_buf, 5)
        risk = abs(price - sl)
        tp1  = round(price + risk, 5)              # TP1 = 1R

        # TP2 = mid of first AOI above TP1
        # TP3 = top of that same AOI
        higher_aois = sorted(
            [z for z in all_aois if z['bottom'] > tp1],
            key=lambda z: z['bottom']
        )
        if higher_aois:
            tp2 = round(higher_aois[0]['mid'], 5)
            tp3 = round(higher_aois[0]['top'], 5)
        else:
            tp2 = round(price + risk * 2.0, 5)
            tp3 = round(price + risk * 3.0, 5)

    elif direction == 'SELL' and aoi_zone:
        sl   = round(aoi_zone['top'] + atr_buf, 5)
        risk = abs(sl - price)
        tp1  = round(price - risk, 5)              # TP1 = 1R

        # TP2 = mid of first AOI below TP1
        # TP3 = bottom of that same AOI
        lower_aois = sorted(
            [z for z in all_aois if z['top'] < tp1],
            key=lambda z: z['top'], reverse=True
        )
        if lower_aois:
            tp2 = round(lower_aois[0]['mid'], 5)
            tp3 = round(lower_aois[0]['bottom'], 5)
        else:
            tp2 = round(price - risk * 2.0, 5)
            tp3 = round(price - risk * 3.0, 5)

    else:
        atr_buf = atr * 1.5 if atr else from_pips(15, pair)
        if direction == 'SELL':
            sl  = round(price + atr_buf, 5)
            risk = abs(sl - price)
            tp1 = round(price - risk, 5)
            tp2 = round(price - risk * 2.0, 5)
            tp3 = round(price - risk * 3.0, 5)
        else:
            sl  = round(price - atr_buf, 5)
            risk = abs(price - sl)
            tp1 = round(price + risk, 5)
            tp2 = round(price + risk * 2.0, 5)
            tp3 = round(price + risk * 3.0, 5)

    return sl, tp1, tp2, tp3

def check_active_trades():
    trades = load_trades()
    # Filter out non-trade keys (e.g. _ict_last_alert)
    trade_keys = [k for k in trades.keys() if not k.startswith('_')]
    if not trade_keys:
        print('  No active trades.')
        return
    print('  Checking ' + str(len(trade_keys)) + ' active trade(s)...')
    NL = chr(10)

    for trade_key in list(trade_keys):
        trades = load_trades()
        if trade_key not in trades:
            continue
        trade = trades[trade_key]

        pair      = trade['pair']
        latest    = fetch_candles(pair, 'H1', count=2)
        if not latest:
            print('  Could not fetch candles for ' + pair)
            continue

        price     = latest[-1]['close']
        direction = trade['direction']
        entry     = trade['entry']
        sl        = trade['sl']
        tp1       = trade['tp1']
        tp2       = trade['tp2']
        tp3       = trade.get('tp3', trade['tp2'])
        tp1_hit   = trade.get('tp1_hit', False)
        tp2_hit_f = trade.get('tp2_hit', False)
        opened_at = trade.get('opened_at', '')

        if direction == 'BUY':
            pips = calc_pips(price - entry, pair)
        else:
            pips = calc_pips(entry - price, pair)

        sign     = '+' if pips >= 0 else ''
        pips_str = sign + str(pips) + ' pips'
        risk     = abs(entry - sl)
        rr       = round(abs(price - entry) / risk, 2) if risk > 0 else 0
        rr_str   = ('+' if pips >= 0 else '-') + str(rr) + 'R'

        # After TP1 hit SL moves to breakeven
        effective_sl = entry if tp1_hit else sl

        sl_hit        = ((direction == 'BUY'  and price <= effective_sl) or
                         (direction == 'SELL' and price >= effective_sl))
        tp1_newly_hit = ((not tp1_hit) and
                         ((direction == 'BUY'  and price >= tp1) or
                          (direction == 'SELL' and price <= tp1)))
        tp2_newly_hit = ((tp1_hit and not tp2_hit_f) and
                         ((direction == 'BUY'  and price >= tp2) or
                          (direction == 'SELL' and price <= tp2)))
        tp3_hit       = ((direction == 'BUY'  and price >= tp3) or
                         (direction == 'SELL' and price <= tp3))

        strategy = trade.get('strategy', 'FXALEXG')
        if strategy == 'GOLD_2R':
            strategy_label = 'Gold 2R Strategy'
        elif strategy == 'ALL_PAIRS_15R':
            strategy_label = 'All Pairs 1.5R Strategy'
        else:
            strategy_label = 'FXAlexG Strategy'
        header = (strategy_label + NL +
                  pair.replace('_', '/') + ' ' + direction +
                  ' [' + opened_at + ']' + NL)

        if tp3_hit:
            send_telegram('TP3 HIT - MAX TARGET ' + NL + header +
                'Entry: ' + str(entry) + NL +
                'TP3: ' + str(tp3) + NL +
                'Profit: ' + pips_str + ' (' + rr_str + ')' + NL +
                'Trade fully closed. Excellent!')
            remove_trade(trade_key)
            print('  TP3 hit - removed: ' + trade_key)

        elif sl_hit:
            if tp1_hit:
                send_telegram('CLOSED AT BREAKEVEN ' + NL + header +
                    'TP1 was secured. Closed at entry. 0 loss.')
            else:
                send_telegram('SL HIT ' + NL + header +
                    'Entry: ' + str(entry) + NL +
                    'SL: ' + str(price) + NL +
                    'Loss: ' + pips_str + ' (' + rr_str + ')')
            remove_trade(trade_key)
            print('  SL/BE hit - removed: ' + trade_key)

        elif tp2_newly_hit:
            trades = load_trades()
            if trade_key in trades:
                trades[trade_key]['tp2_hit'] = True
                save_trades(trades)
            trades = load_trades()
            if trade_key in trades:
                trades[trade_key]['tp2_hit'] = True
                save_trades(trades)
            send_telegram('TP2 HIT ' + NL + header +
                'Entry: ' + str(entry) + NL +
                'TP2: ' + str(tp2) + NL +
                'Profit: ' + pips_str + ' (' + rr_str + ')' + NL +
                'Watching TP3: ' + str(tp3))
            print('  TP2 hit - watching TP3: ' + trade_key)

        elif tp1_newly_hit:
            trades = load_trades()
            if trade_key in trades:
                trades[trade_key]['tp1_hit'] = True
                save_trades(trades)
            send_telegram('TP1 HIT ' + NL + header +
                'Entry: ' + str(entry) + NL +
                'TP1: ' + str(tp1) + NL +
                'Profit: ' + pips_str + ' (' + rr_str + ')' + NL +
                'SL moved to breakeven. Watching TP2: ' + str(tp2))
            print('  TP1 hit - SL to BE: ' + trade_key)

        else:
            icon = '' if pips >= 0 else ''
            if tp2_hit_f:
                status = 'TP1+TP2 hit - watching TP3: ' + str(tp3)
            elif tp1_hit:
                status = 'TP1 hit - SL at BE - watching TP2: ' + str(tp2)
            else:
                status = 'Watching TP1: ' + str(tp1)
            send_telegram(icon + ' Trade Update' + NL + header +
                'Entry:   ' + str(entry) + NL +
                'Current: ' + str(price) + NL +
                'P&L:     ' + pips_str + ' (' + rr_str + ')' + NL +
                'TP1: ' + str(tp1) + ' | TP2: ' + str(tp2) + ' | TP3: ' + str(tp3) + NL +
                'SL: ' + str(effective_sl) + NL + status)
            print('  Update: ' + trade_key + ' ' + pips_str)


def can_ict_alert():
    trades = load_trades()
    last   = trades.get('_ict_last_alert', 0)
    return (time.time() - last) > ICT_COOLDOWN_MIN * 60

def mark_ict_alerted():
    trades = load_trades()
    trades['_ict_last_alert'] = time.time()
    save_trades(trades)

# ─────────────────────────────────────────────
# STRATEGY 1 - FXALEXG ANALYSER
# ─────────────────────────────────────────────
def analyse_fxalexg(pair):
    print("  [FXAlexG] " + pair)
    trends     = {}
    all_aois   = []
    best_hs    = None
    h1_ema     = None
    ema_sig    = None
    h1_candles  = []
    h4_candles  = []
    m15_candles = []

    for tf in TIMEFRAMES:
        candles = fetch_candles(pair, tf)
        if len(candles) < 20:
            continue
        h, l = swing_points(candles)
        trends[tf] = trend(h, l, candles)
        # AOI: Daily and Weekly only (FXAlexG rule)
        if tf in ('D', 'W'):
            all_aois.extend(detect_aois(candles, pair))
        # H&S: H4 and H1 only
        if tf in ('H4', 'H1'):
            hs = detect_hs(candles, h, l)
            if hs and (best_hs is None or
                       (hs['complete'] and not best_hs['complete'])):
                best_hs = hs
        # Store candles for each entry timeframe
        if tf == 'H4':
            h4_candles = candles
        if tf == 'H1':
            h1_ema = ema50(candles)
            if h1_ema:
                last_close = candles[-1]['close']
                ema_sig = 'ABOVE' if last_close > h1_ema else 'BELOW'
            h1_candles = candles
        if tf == 'M15':
            m15_candles = candles

    if not trends:
        return None

    # Use last H1 candle close - already fetched, no extra API call
    if not h1_candles:
        return None
    price = h1_candles[-1]['close']

    # Determine trade direction from majority trend
    bullish_count = sum(1 for t in trends.values() if t == 'BULLISH')
    bearish_count = sum(1 for t in trends.values() if t == 'BEARISH')
    direction = 'BUY' if bullish_count > bearish_count else 'SELL' if bearish_count > bullish_count else None

    # FXAlexG rule: need at least 3 TFs agreeing to trade
    # If majority not strong enough - skip
    majority_count = max(bullish_count, bearish_count)
    if majority_count < 3:
        print('     Skipped - less than 3 TFs aligned (need 3 minimum)')
        return None

    # Filter H&S pattern - must match majority direction
    # Bearish majority = only accept H&S (bearish)
    # Bullish majority = only accept Inverse H&S (bullish)
    if best_hs:
        if direction == 'SELL' and best_hs['direction'] != 'BEARISH':
            best_hs = None  # discard bullish pattern in bearish market
        if direction == 'BUY' and best_hs['direction'] != 'BULLISH':
            best_hs = None  # discard bearish pattern in bullish market

    # Detect entry signals on H4, H1, M15 - highest TF takes priority
    # H4 pattern = strongest conviction
    # H1 pattern = good conviction
    # M15 pattern = entry trigger
    entry_signal    = None
    entry_tf        = None

    if direction:
        if h4_candles and len(h4_candles) >= 3:
            sig = detect_entry_signal(h4_candles, direction)
            if sig:
                entry_signal = sig
                entry_tf     = 'H4'

        if entry_signal is None and h1_candles and len(h1_candles) >= 3:
            sig = detect_entry_signal(h1_candles, direction)
            if sig:
                entry_signal = sig
                entry_tf     = 'H1'

        if entry_signal is None and m15_candles and len(m15_candles) >= 3:
            sig = detect_entry_signal(m15_candles, direction)
            if sig:
                entry_signal = sig
                entry_tf     = 'M15'

    aoi_zone        = at_aoi(price, all_aois, pair)
    grade, pts, reasons = score(trends, aoi_zone, best_hs, ema_sig, entry_signal, entry_tf)
    print("     Grade: " + grade + " | Score: " + str(pts) + " | Trends: " + str(trends))

    # Block if fewer than 3 TFs aligned (regardless of score)
    majority_count = max(
        sum(1 for t in trends.values() if t == 'BULLISH'),
        sum(1 for t in trends.values() if t == 'BEARISH')
    )
    if majority_count < 3:
        print('     Blocked - only ' + str(majority_count) + '/4 TFs aligned (need 3)')
        return {'pair': pair, 'grade': grade, 'pts': pts,
                'direction': direction, 'trends': trends}

    if grade not in MIN_GRADE_TO_ALERT:
        return {'pair': pair, 'grade': grade, 'pts': pts,
                'direction': direction, 'trends': trends}

    # Signal blocking rules for FXAlexG:
    # Only look at FXAlexG trades for this pair (not Gold 2R or AP15R)
    # Allow new signal ONLY if:
    #   - TP1 hit on the MOST RECENT FXAlexG trade, OR
    #   - 24h have passed since the MOST RECENT FXAlexG signal
    active_trades = load_trades()
    pair_trades   = {k: v for k, v in active_trades.items()
                     if not k.startswith('_')
                     and v.get('pair') == pair
                     and v.get('strategy', 'FXALEXG') == 'FXALEXG'}

    if pair_trades:
        now_ts = datetime.now(timezone.utc).timestamp()

        # Sort by opened_ts - most recent first
        sorted_trades = sorted(pair_trades.values(),
                               key=lambda t: t.get('opened_ts', 0),
                               reverse=True)
        most_recent   = sorted_trades[0]
        hours_since   = (now_ts - most_recent.get('opened_ts', 0)) / 3600
        tp1_hit_recent = most_recent.get('tp1_hit', False)

        if not tp1_hit_recent and hours_since < 24:
            print('     Skipped - ' + pair +
                  ' (' + str(round(hours_since, 1)) + 'h since last FXAlexG signal, TP1 not hit)')
            return {'pair': pair, 'grade': grade, 'pts': pts,
                    'direction': direction, 'trends': trends}
        elif tp1_hit_recent:
            print('     TP1 hit on last trade - new FXAlexG signal allowed')
        else:
            print('     24h passed since last FXAlexG signal - new signal allowed')


    # Calculate SL and TP using AOI-based method
    sl, tp1, tp2, tp3 = calc_sl_tp(direction, price, aoi_zone, pair, all_aois, h1_candles)

    data = {
        'price':        price,
        'trends':       trends,
        'aoi':          aoi_zone,
        'all_aois':     all_aois,
        'ema':          round(h1_ema, 5) if h1_ema else 'N/A',
        'ema_sig':      ema_sig or 'N/A',
        'hs':           best_hs,
        'entry_signal': entry_signal,
        'entry_tf':     entry_tf,
        'grade':        grade,
        'pts':          pts,
        'reasons':      reasons,
        'direction':    direction,
        'sl':           sl,
        'tp1':          tp1,
        'tp2':          tp2,
    }

    analysis = ask_gemini_fxalexg(pair, data)
    if not analysis:
        return

    bullish = sum(1 for t in trends.values() if t == 'BULLISH')
    bearish = sum(1 for t in trends.values() if t == 'BEARISH')
    emoji   = 'G' if bullish > bearish else 'R' if bearish > bullish else 'Y'
    emojis  = {'G': '🟢', 'R': '🔴', 'Y': '🟡'}

    reasons_txt = ""
    for r in reasons:
        reasons_txt += "  " + r + "\n"

    es    = data.get('entry_signal')
    es_tf = data.get('entry_tf', '')
    es_line = (
        "Entry Signal: " + es['pattern'] + " on " + es_tf + " @ " + str(es['entry']) + "\n"
        if es else "Entry Signal: Waiting for confirmation\n"
    )

    # Risk in pips
    risk_pips = round(abs(price - sl) / pip_size(pair), 1)

    msg = (
        emojis[emoji] + " <b>FXAlexG Alert</b>\n"
        "<b>" + pair.replace('_', '/') + "  [" + grade + "]  Score: " + str(pts) + "/10</b>\n\n"
        "Price: <code>" + str(price) + "</code>\n"
        "Direction: " + direction + "\n"
        "Time: " + datetime.now(timezone.utc).strftime('%H:%M UTC') + "\n"
        + es_line +
        "\nSL:  <code>" + str(sl) + "</code> (risk " + str(risk_pips) + " pips)\n"
        "TP1: <code>" + str(tp1) + "</code> (1R)\n"
        "TP2: <code>" + str(tp2) + "</code> (mid next AOI)\n"
        "TP3: <code>" + str(tp3) + "</code> (edge next AOI)\n"
        "\n<b>Confluences:</b>\n" + reasons_txt + "\n"
        "<b>Analysis:</b>\n" + analysis
    )
    send_telegram(msg)

    # Add to trade tracker only for A+ grades
    if grade == 'A+':
        add_trade(pair, direction, price, sl, tp1, tp2, tp3, aoi_zone)
        print("     Trade added to tracker")

    print("     Alert sent [" + grade + "]")
    return {'pair': pair, 'grade': grade, 'pts': pts,
            'direction': direction, 'trends': trends}

# ─────────────────────────────────────────────
# STRATEGY 2 - ICT KILLZONE ANALYSER (XAU/USD)
# ─────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════
# GOLD 2R STRATEGY: EMA200 + MACD Pullback + ATR Stop (M15)
# ═══════════════════════════════════════════════════════════════
def analyse_gold_2r():
    pair = 'XAU_USD'
    print(chr(10) + '[Gold 2R Strategy] XAU/USD')

    # ── Fetch candles across timeframes ─────────────────────
    d_candles  = fetch_candles(pair, 'D',  count=80)
    h4_candles = fetch_candles(pair, 'H4', count=120)
    h1_candles = fetch_candles(pair, 'H1', count=120)

    if not d_candles or len(d_candles) < 60:
        print('     Not enough Daily data')
        return
    if not h4_candles or len(h4_candles) < 60:
        print('     Not enough H4 data')
        return
    if not h1_candles or len(h1_candles) < 30:
        print('     Not enough H1 data')
        return

    # ── Step 1: Daily trend via 50 EMA slope ────────────────
    d_closes  = [c['close'] for c in d_candles]
    ema50_now = ema50(d_candles)          # reuse existing ema50 helper
    if not ema50_now:
        print('     EMA50 not ready')
        return

    # Slope: compare EMA now vs 10 days ago
    ema50_10ago = None
    if len(d_closes) >= 60:
        past_closes  = d_closes[:-10]
        ema50_10ago  = sum(past_closes[-50:]) / 50
        m = 2.0 / 51
        for p in past_closes[-50:]:
            ema50_10ago = (p - ema50_10ago) * m + ema50_10ago

    price_d = d_candles[-1]['close']
    trend_dir = None

    if ema50_10ago:
        slope_pct = (ema50_now - ema50_10ago) / ema50_10ago * 100
        if price_d > ema50_now and slope_pct > 0.1:
            trend_dir = 'BUY'
        elif price_d < ema50_now and slope_pct < -0.1:
            trend_dir = 'SELL'

    if not trend_dir:
        print('     No clear daily trend - skip')
        return

    print('     Daily trend: ' + trend_dir + ' | EMA50: ' + str(round(ema50_now, 2)))

    # ── Step 2: H4 pullback to 50 EMA ───────────────────────
    h4_closes = [c['close'] for c in h4_candles]
    h4_ema50  = None
    if len(h4_closes) >= 50:
        h4_ema50 = sum(h4_closes[:50]) / 50
        m = 2.0 / 51
        for p in h4_closes[50:]:
            h4_ema50 = (p - h4_ema50) * m + h4_ema50

    if not h4_ema50:
        print('     H4 EMA50 not ready')
        return

    h4_atr_val = calc_atr(h4_candles[-20:], 14)
    if not h4_atr_val:
        print('     H4 ATR not ready')
        return

    # Check if price has pulled back to H4 EMA50 in last 10 candles
    last_10_h4 = h4_candles[-10:]
    curr_h4    = h4_candles[-1]
    pullback_valid = False

    if trend_dir == 'BUY':
        tagged   = any(c['low'] <= h4_ema50 + h4_atr_val * 0.5 for c in last_10_h4)
        rejected = curr_h4['close'] > h4_ema50
        pullback_valid = tagged and rejected
    else:
        tagged   = any(c['high'] >= h4_ema50 - h4_atr_val * 0.5 for c in last_10_h4)
        rejected = curr_h4['close'] < h4_ema50
        pullback_valid = tagged and rejected

    if not pullback_valid:
        print('     No H4 pullback to EMA50 - skip')
        return

    print('     H4 pullback confirmed | H4 EMA50: ' + str(round(h4_ema50, 2)))

    # ── Step 3: H1 entry confirmation ───────────────────────
    curr_h1  = h1_candles[-1]
    prev_h1  = h1_candles[-2]
    prev2_h1 = h1_candles[-3]
    h1_closes = [c['close'] for c in h1_candles]

    # H1 RSI
    h1_rsi_val = None
    if len(h1_closes) >= 15:
        gains = losses = 0
        for j in range(1, 15):
            ch = h1_closes[j] - h1_closes[j-1]
            if ch > 0: gains += ch
            else: losses -= ch
        ag = gains / 14
        al = losses / 14
        for j in range(15, len(h1_closes)):
            ch  = h1_closes[j] - h1_closes[j-1]
            g   = max(ch, 0)
            l   = max(-ch, 0)
            ag  = (ag * 13 + g) / 14
            al  = (al * 13 + l) / 14
        h1_rsi_val = 100 - (100 / (1 + ag / al)) if al > 0 else 100

    # H1 EMA20
    h1_ema20_val = None
    if len(h1_closes) >= 20:
        h1_ema20_val = sum(h1_closes[:20]) / 20
        m = 2.0 / 21
        for p in h1_closes[20:]:
            h1_ema20_val = (p - h1_ema20_val) * m + h1_ema20_val

    entry_confirmed = False
    if trend_dir == 'BUY':
        rng       = curr_h1['high'] - curr_h1['low']
        body      = curr_h1['close'] - curr_h1['open']
        close_pos = (curr_h1['close'] - curr_h1['low']) / rng if rng > 0 else 0
        if (curr_h1['close'] > curr_h1['open'] and
                body >= rng * 0.55 and
                close_pos >= 0.65 and
                curr_h1['close'] > prev_h1['close'] and
                (h1_rsi_val is None or 35 <= h1_rsi_val <= 65) and
                (h1_ema20_val is None or curr_h1['close'] > h1_ema20_val)):
            entry_confirmed = True
    else:
        rng       = curr_h1['high'] - curr_h1['low']
        body      = curr_h1['open'] - curr_h1['close']
        close_pos = (curr_h1['close'] - curr_h1['low']) / rng if rng > 0 else 0
        if (curr_h1['close'] < curr_h1['open'] and
                body >= rng * 0.55 and
                close_pos <= 0.35 and
                curr_h1['close'] < prev_h1['close'] and
                (h1_rsi_val is None or 35 <= h1_rsi_val <= 65) and
                (h1_ema20_val is None or curr_h1['close'] < h1_ema20_val)):
            entry_confirmed = True

    if not entry_confirmed:
        print('     No H1 confirmation candle - skip')
        return

    print('     H1 entry confirmed | RSI: ' + str(round(h1_rsi_val, 1) if h1_rsi_val else 0))

    # ── Check for existing Gold 2R trades (24h OR TP1 rule) ─────
    active_trades = load_trades()
    gold_trades   = {k: v for k, v in active_trades.items()
                     if not k.startswith('_') and v.get('strategy') == 'GOLD_2R'}

    if gold_trades:
        now_ts         = datetime.now(timezone.utc).timestamp()
        any_tp1_hit    = any(t.get('tp1_hit', False) for t in gold_trades.values())
        most_recent_ts = max(t.get('opened_ts', 0) for t in gold_trades.values())
        hours_since    = (now_ts - most_recent_ts) / 3600
        if not any_tp1_hit and hours_since < 24:
            print('     Skipped - ' + str(round(hours_since, 1)) + 'h since last signal, no TP1 yet')
            return
        elif any_tp1_hit:
            print('     TP1 already hit - new signal allowed')
        else:
            print('     24h passed - new signal allowed')

    # ── Step 4: Calculate SL and TPs ────────────────────────
    price = curr_h1['close']

    if trend_dir == 'BUY':
        recent_low = min(c['low'] for c in h1_candles[-20:])
        sl_struct  = recent_low - from_pips(5, pair)
        sl_atr     = price - h4_atr_val * 1.5
        sl         = min(sl_struct, sl_atr)
        risk       = price - sl
    else:
        recent_high = max(c['high'] for c in h1_candles[-20:])
        sl_struct   = recent_high + from_pips(5, pair)
        sl_atr      = price + h4_atr_val * 1.5
        sl          = max(sl_struct, sl_atr)
        risk        = sl - price

    if risk <= 0:
        print('     Invalid risk')
        return

    risk_pips = round(risk / pip_size(pair), 1)
    if risk_pips < 10 or risk_pips > 200:
        print('     Risk out of range: ' + str(risk_pips) + ' pips')
        return

    # TP1=2R | TP2=3R | TP3=4R
    if trend_dir == 'BUY':
        tp1 = price + risk * 2.0
        tp2 = price + risk * 3.0
        tp3 = price + risk * 4.0
    else:
        tp1 = price - risk * 2.0
        tp2 = price - risk * 3.0
        tp3 = price - risk * 4.0

    # ── Send Telegram alert ─────────────────────────────────
    now       = datetime.now(timezone.utc)
    NL        = chr(10)
    pair_disp = pair.replace('_', '/')

    msg = (
        '<b>Gold 2R Strategy Alert</b>' + NL +
        '<b>' + pair_disp + ' ' + trend_dir + '</b>' + NL + NL +
        'Time: ' + now.strftime('%H:%M UTC') + NL +
        'Entry: <code>' + str(round(price, 2)) + '</code>' + NL +
        'SL:  <code>' + str(round(sl, 2)) + '</code> (' + str(risk_pips) + ' pips)' + NL +
        'TP1: <code>' + str(round(tp1, 2)) + '</code> (2R)' + NL +
        'TP2: <code>' + str(round(tp2, 2)) + '</code> (3R)' + NL +
        'TP3: <code>' + str(round(tp3, 2)) + '</code> (4R)' + NL + NL +
        '<b>Setup:</b>' + NL +
        'Daily trend: ' + trend_dir + ' (above EMA50)' + NL +
        'H4 pullback to EMA50: confirmed' + NL +
        'H1 entry candle: confirmed' + NL +
        'RSI: ' + str(round(h1_rsi_val, 1) if h1_rsi_val else 0)
    )
    send_telegram(msg)

    # ── Save trade ───────────────────────────────────────────
    trade_key = 'GOLD2R_' + pair + '_' + now.strftime('%Y-%m-%d_%H:%M')
    trades    = load_trades()
    trades[trade_key] = {
        'pair':       pair,
        'strategy':   'GOLD_2R',
        'trade_key':  trade_key,
        'direction':  trend_dir,
        'entry':      round(price, 2),
        'sl':         round(sl, 2),
        'tp1':        round(tp1, 2),
        'tp2':        round(tp2, 2),
        'tp3':        round(tp3, 2),
        'tp1_hit':    False,
        'tp2_hit':    False,
        'aoi_top':    None,
        'aoi_bottom': None,
        'opened_at':  now.strftime('%Y-%m-%d %H:%M UTC'),
        'opened_ts':  now.timestamp(),
    }
    save_trades(trades)
    print('     Gold 2R alert sent: ' + trade_key)


# ═══════════════════════════════════════════════════════════════
# ALL PAIRS 1.5R STRATEGY: Trend + Pullback Hybrid
# Daily 50 EMA trend + H4 pullback + H1/H4 entry
# TP1=1.5R | TP2=2.5R | TP3=3.5R
# Same 24h OR TP1 signal blocking as FXAlexG
# ═══════════════════════════════════════════════════════════════
def analyse_all_pairs_15r(pair):
    print(chr(10) + '[All Pairs 1.5R] ' + pair.replace('_', '/'))

    # ── Fetch candles ────────────────────────────────────────
    d_candles  = fetch_candles(pair, 'D',  count=80)
    h4_candles = fetch_candles(pair, 'H4', count=120)
    h1_candles = fetch_candles(pair, 'H1', count=120)

    if not d_candles or len(d_candles) < 60:
        print('     Not enough Daily data')
        return
    if not h4_candles or len(h4_candles) < 60:
        print('     Not enough H4 data')
        return
    if not h1_candles or len(h1_candles) < 30:
        print('     Not enough H1 data')
        return

    # ── Step 1: Daily trend via 50 EMA + slope ───────────────
    d_closes = [c['close'] for c in d_candles]

    # EMA50 now
    m = 2.0 / 51
    ema50_val = sum(d_closes[:50]) / 50
    for p in d_closes[50:]:
        ema50_val = (p - ema50_val) * m + ema50_val

    # EMA50 10 days ago
    past_closes  = d_closes[:-10]
    ema50_10ago  = sum(past_closes[-50:]) / 50
    for p in past_closes[-50:]:
        ema50_10ago = (p - ema50_10ago) * m + ema50_10ago

    price_d   = d_candles[-1]['close']
    slope_pct = (ema50_val - ema50_10ago) / ema50_10ago * 100

    trend_dir = None
    if price_d > ema50_val and slope_pct > 0.1:
        trend_dir = 'BUY'
    elif price_d < ema50_val and slope_pct < -0.1:
        trend_dir = 'SELL'

    if not trend_dir:
        print('     No clear daily trend (slope: ' + str(round(slope_pct, 3)) + '%) - skip')
        return

    print('     Daily ' + trend_dir + ' | EMA50: ' + str(round(ema50_val, 5)) +
          ' | Slope: ' + str(round(slope_pct, 3)) + '%')

    # ── Step 2: H4 pullback to 50 EMA ───────────────────────
    h4_closes = [c['close'] for c in h4_candles]
    h4_ema50  = sum(h4_closes[:50]) / 50
    for p in h4_closes[50:]:
        h4_ema50 = (p - h4_ema50) * m + h4_ema50

    h4_atr_val = calc_atr(h4_candles[-20:], 14)
    if not h4_atr_val:
        print('     H4 ATR not ready')
        return

    last_10_h4 = h4_candles[-10:]
    curr_h4    = h4_candles[-1]

    if trend_dir == 'BUY':
        tagged   = any(c['low'] <= h4_ema50 + h4_atr_val * 0.5 for c in last_10_h4)
        rejected = curr_h4['close'] > h4_ema50
    else:
        tagged   = any(c['high'] >= h4_ema50 - h4_atr_val * 0.5 for c in last_10_h4)
        rejected = curr_h4['close'] < h4_ema50

    if not (tagged and rejected):
        print('     No H4 pullback to EMA50 - skip')
        return

    print('     H4 pullback confirmed | EMA50: ' + str(round(h4_ema50, 5)))

    # ── Step 3: H4 or H1 entry confirmation ─────────────────
    # Try H4 first (higher conviction), fall back to H1
    curr_h4  = h4_candles[-1]
    prev_h4  = h4_candles[-2]
    prev2_h4 = h4_candles[-3]
    curr_h1  = h1_candles[-1]
    prev_h1  = h1_candles[-2]

    entry_tf = None
    h1_closes = [c['close'] for c in h1_candles]

    # H1 RSI
    h1_rsi_val = None
    if len(h1_closes) >= 15:
        gains = losses = 0
        for j in range(1, 15):
            ch = h1_closes[j] - h1_closes[j-1]
            if ch > 0: gains += ch
            else: losses -= ch
        ag = gains / 14
        al = losses / 14
        for j in range(15, len(h1_closes)):
            ch = h1_closes[j] - h1_closes[j-1]
            g  = max(ch, 0)
            l  = max(-ch, 0)
            ag = (ag * 13 + g) / 14
            al = (al * 13 + l) / 14
        h1_rsi_val = (100 - (100 / (1 + ag / al))) if al > 0 else 100

    # H1 EMA20
    h1_ema20_val = None
    if len(h1_closes) >= 20:
        h1_ema20_val = sum(h1_closes[:20]) / 20
        m20 = 2.0 / 21
        for p in h1_closes[20:]:
            h1_ema20_val = (p - h1_ema20_val) * m20 + h1_ema20_val

    def check_h4_entry(c, p, p2, direction):
        rng = c['high'] - c['low']
        if rng <= 0: return False
        body = abs(c['close'] - c['open'])
        cp   = (c['close'] - c['low']) / rng
        if direction == 'BUY':
            return (c['close'] > c['open'] and body >= rng * 0.55 and
                    cp >= 0.65 and c['close'] > p['close'] and c['close'] > p2['close'])
        else:
            return (c['close'] < c['open'] and body >= rng * 0.55 and
                    cp <= 0.35 and c['close'] < p['close'] and c['close'] < p2['close'])

    def check_h1_entry(c, p, rsi_val, ema20_val, direction):
        rng = c['high'] - c['low']
        if rng <= 0: return False
        body = abs(c['close'] - c['open'])
        cp   = (c['close'] - c['low']) / rng
        rsi_ok = rsi_val is None or (35 <= rsi_val <= 65)
        if direction == 'BUY':
            ema_ok = ema20_val is None or c['close'] > ema20_val
            return (c['close'] > c['open'] and body >= rng * 0.55 and
                    cp >= 0.65 and c['close'] > p['close'] and rsi_ok and ema_ok)
        else:
            ema_ok = ema20_val is None or c['close'] < ema20_val
            return (c['close'] < c['open'] and body >= rng * 0.55 and
                    cp <= 0.35 and c['close'] < p['close'] and rsi_ok and ema_ok)

    if check_h4_entry(curr_h4, prev_h4, prev2_h4, trend_dir):
        entry_tf = 'H4'
    elif check_h1_entry(curr_h1, prev_h1, h1_rsi_val, h1_ema20_val, trend_dir):
        entry_tf = 'H1'

    if not entry_tf:
        print('     No H4/H1 confirmation - skip')
        return

    print('     Entry confirmed on ' + entry_tf +
          ' | RSI: ' + str(round(h1_rsi_val, 1) if h1_rsi_val else 0))

    # ── Check new signal blocking (24h OR TP1 hit) ───────────
    active_trades = load_trades()
    pair_trades   = {k: v for k, v in active_trades.items()
                     if not k.startswith('_') and v.get('strategy') == 'ALL_PAIRS_15R'
                     and v.get('pair') == pair}

    if pair_trades:
        now_ts         = datetime.now(timezone.utc).timestamp()
        any_tp1_hit    = any(t.get('tp1_hit', False) for t in pair_trades.values())
        most_recent_ts = max(t.get('opened_ts', 0) for t in pair_trades.values())
        hours_since    = (now_ts - most_recent_ts) / 3600
        if not any_tp1_hit and hours_since < 24:
            print('     Skipped - ' + str(round(hours_since, 1)) + 'h since last signal, no TP1 yet')
            return
        elif any_tp1_hit:
            print('     TP1 hit - new signal allowed')
        else:
            print('     24h passed - new signal allowed')

    # ── Calculate SL and TPs ────────────────────────────────
    price = curr_h1['close']

    if trend_dir == 'BUY':
        recent_low = min(c['low'] for c in h1_candles[-20:])
        sl_struct  = recent_low - from_pips(3, pair)
        sl_atr     = price - h4_atr_val * 1.5
        sl         = min(sl_struct, sl_atr)
        risk       = price - sl
    else:
        recent_high = max(c['high'] for c in h1_candles[-20:])
        sl_struct   = recent_high + from_pips(3, pair)
        sl_atr      = price + h4_atr_val * 1.5
        sl          = max(sl_struct, sl_atr)
        risk        = sl - price

    if risk <= 0:
        print('     Invalid risk')
        return

    risk_pips = to_pips(risk, pair)
    if risk_pips < 5 or risk_pips > 200:
        print('     Risk out of range: ' + str(risk_pips) + ' pips')
        return

    # TP1=1.5R | TP2=2.5R | TP3=3.5R
    if trend_dir == 'BUY':
        tp1 = price + risk * 1.5
        tp2 = price + risk * 2.5
        tp3 = price + risk * 3.5
    else:
        tp1 = price - risk * 1.5
        tp2 = price - risk * 2.5
        tp3 = price - risk * 3.5

    # ── Send Telegram alert ─────────────────────────────────
    now       = datetime.now(timezone.utc)
    NL        = chr(10)
    pair_disp = pair.replace('_', '/')

    msg = (
        '<b>All Pairs 1.5R Strategy Alert</b>' + NL +
        '<b>' + pair_disp + ' ' + trend_dir + '</b>' + NL + NL +
        'Time: ' + now.strftime('%H:%M UTC') + NL +
        'Entry: <code>' + str(round(price, 5)) + '</code>' + NL +
        'SL:  <code>' + str(round(sl, 5)) + '</code> (' + str(risk_pips) + ' pips)' + NL +
        'TP1: <code>' + str(round(tp1, 5)) + '</code> (1.5R)' + NL +
        'TP2: <code>' + str(round(tp2, 5)) + '</code> (2.5R)' + NL +
        'TP3: <code>' + str(round(tp3, 5)) + '</code> (3.5R)' + NL + NL +
        '<b>Setup:</b>' + NL +
        'Daily trend: ' + trend_dir + NL +
        'H4 pullback to EMA50: confirmed' + NL +
        'Entry TF: ' + entry_tf + NL +
        'RSI: ' + str(round(h1_rsi_val, 1) if h1_rsi_val else 0)
    )
    send_telegram(msg)

    # ── Save trade ───────────────────────────────────────────
    trade_key = 'AP15R_' + pair + '_' + now.strftime('%Y-%m-%d_%H:%M')
    trades    = load_trades()
    trades[trade_key] = {
        'pair':       pair,
        'strategy':   'ALL_PAIRS_15R',
        'trade_key':  trade_key,
        'direction':  trend_dir,
        'entry':      round(price, 5),
        'sl':         round(sl, 5),
        'tp1':        round(tp1, 5),
        'tp2':        round(tp2, 5),
        'tp3':        round(tp3, 5),
        'tp1_hit':    False,
        'tp2_hit':    False,
        'aoi_top':    None,
        'aoi_bottom': None,
        'opened_at':  now.strftime('%Y-%m-%d %H:%M UTC'),
        'opened_ts':  now.timestamp(),
    }
    save_trades(trades)
    print('     Alert sent: ' + trade_key)


def analyse_ict_gold():
    print("  [ICT Killzone] XAU_USD")
    now_utc  = datetime.now(timezone.utc)
    killzone = in_killzone(now_utc)

    if not killzone:
        print("     Not in killzone - skipping ICT analysis")
        return

    print("     In " + killzone + " killzone")

    if not can_ict_alert():
        print("     Skipped - ICT cooldown active")
        return

    # Fetch M15 candles (last 48 candles = 12 hours)
    m15_candles = fetch_candles('XAU_USD', 'M15', count=48)
    if len(m15_candles) < 10:
        print("     Not enough M15 data")
        return

    # Get daily candles for bias
    d_candles = fetch_candles('XAU_USD', 'D', count=10)
    h_d, l_d  = swing_points(d_candles) if d_candles else ([], [])
    daily_bias = trend(h_d, l_d, d_candles)

    # Asian range
    asian = get_asian_range(m15_candles)
    if not asian:
        print("     Asian range not available yet")
        return

    print("     Asian Range: " + str(asian['low']) + " - " + str(asian['high']))

    # Sweep detection
    sweep = detect_sweep(m15_candles, asian)

    # FVG detection
    fvg = detect_fvg(m15_candles[-10:])

    # Current price
    price = m15_candles[-1]['close']

    # Need at least sweep OR fvg to alert
    if not sweep and not fvg:
        print("     No sweep or FVG detected")
        return

    # Check sweep direction aligns with daily bias
    if sweep:
        if daily_bias == 'BULLISH' and sweep['direction'] == 'SELL':
            print("     Sweep against daily bias - skipping")
            return
        if daily_bias == 'BEARISH' and sweep['direction'] == 'BUY':
            print("     Sweep against daily bias - skipping")
            return

    data = {
        'price':       price,
        'killzone':    killzone,
        'asian':       asian,
        'sweep':       sweep,
        'fvg':         fvg,
        'daily_bias':  daily_bias,
    }

    analysis = ask_gemini_ict(data)
    if not analysis:
        return

    sweep_txt = ""
    fvg_txt   = ""

    if sweep:
        sweep_txt = (
            "\n<b>Liquidity Sweep:</b>\n"
            "  " + sweep['type'] + "\n"
            "  Swept: " + str(sweep['swept']) + "\n"
            "  Asian Level: " + str(sweep['level']) + "\n"
        )

    if fvg:
        fvg_txt = (
            "\n<b>Fair Value Gap:</b>\n"
            "  " + fvg['type'] + "\n"
            "  Zone: " + str(fvg['bottom']) + " - " + str(fvg['top']) + "\n"
            "  Size: " + str(fvg['size']) + " points\n"
        )

    emoji = '🟢' if daily_bias == 'BULLISH' else '🔴' if daily_bias == 'BEARISH' else '🟡'

    msg = (
        emoji + " <b>ICT Killzone Alert</b>\n"
        "<b>XAU/USD (Gold)</b>\n\n"
        "Price: <code>" + str(price) + "</code>\n"
        "Time: " + datetime.now(timezone.utc).strftime('%H:%M UTC') + "\n"
        "Session: " + killzone + " Killzone\n"
        "Daily Bias: " + daily_bias + "\n\n"
        "<b>Asian Range:</b>\n"
        "  High: " + str(asian['high']) + "\n"
        "  Low:  " + str(asian['low']) + "\n"
        + sweep_txt + fvg_txt +
        "\n<b>Analysis:</b>\n" + analysis
    )
    send_telegram(msg)
    mark_ict_alerted()
    print("     ICT Alert sent!")

# ─────────────────────────────────────────────
# MAIN - single scan, triggered by GitHub Actions
# ─────────────────────────────────────────────
def is_market_open():
    # Forex market is closed Saturday and most of Sunday UTC
    # Closed: Saturday 00:00 UTC to Sunday 21:00 UTC
    # BTC_USD trades 24/7 but we skip weekend for consistency
    now     = datetime.now(timezone.utc)
    weekday = now.weekday()  # 0=Mon, 5=Sat, 6=Sun
    hour    = now.hour

    if weekday == 5:  # Saturday - fully closed
        return False
    if weekday == 6 and hour < 21:  # Sunday before 21:00 UTC - closed
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# HIGH IMPACT NEWS ALERT - ForexFactory Data
# Source: nfs.faireconomy.media (official FF feed, works in GH Actions)
# Runs at 07:30 UTC (1:00 PM IST) daily
# Window: 08:30 UTC today to 08:30 UTC next day (2PM IST to 2PM IST)
# ═══════════════════════════════════════════════════════════════

NEWS_CURRENCY_PAIRS = {
    'USD': ['EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'USD/CHF', 'NZD/USD', 'USD/CAD', 'XAU/USD'],
    'EUR': ['EUR/USD'],
    'GBP': ['GBP/USD'],
    'JPY': ['USD/JPY'],
    'AUD': ['AUD/USD'],
    'CHF': ['USD/CHF'],
    'NZD': ['NZD/USD'],
    'CAD': ['USD/CAD'],
    'XAU': ['XAU/USD'],
    'CNY': ['AUD/USD'],
    'CNH': ['AUD/USD'],
    'ALL': ['EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'USD/CHF', 'NZD/USD', 'USD/CAD', 'XAU/USD'],
}

def et_to_utc(date_str, time_str, now_utc):
    # FF times are in Eastern Time (auto EDT/EST)
    # date_str: 'MM-DD-YYYY', time_str: '8:30am' or '12:00pm' or 'All Day' or ''
    NL = chr(10)
    if not time_str or time_str.lower() in ('all day', 'tentative', ''):
        return None
    try:
        time_str = time_str.strip().lower()
        am_pm = 'am' if 'am' in time_str else 'pm'
        time_str = time_str.replace('am', '').replace('pm', '').strip()
        parts = time_str.split(':')
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        if am_pm == 'pm' and h != 12:
            h += 12
        if am_pm == 'am' and h == 12:
            h = 0

        # Parse date
        mo, da, yr = date_str.split('-')
        from datetime import datetime as dt
        event_naive = dt(int(yr), int(mo), int(da), h, m)

        # DST: EDT (UTC-4) from second Sunday March to first Sunday November
        # Approximate: EDT if month 3(after 8th)-11(before 1st), else EST
        month = int(mo)
        is_edt = (month > 3 and month < 11) or (month == 3 and int(da) >= 8) or (month == 11 and int(da) < 1)
        offset_hours = 4 if is_edt else 5
        from datetime import timezone, timedelta
        utc_time = event_naive + timedelta(hours=offset_hours)
        return utc_time.replace(tzinfo=timezone.utc)
    except:
        return None

def send_news_alert():
    print(chr(10) + '[News Alert] Fetching ForexFactory high impact news...')
    NL = chr(10)

    ff_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.forexfactory.com/',
        'Accept': 'application/json, text/plain, */*',
    }

    now_utc    = datetime.now(timezone.utc)
    win_start  = now_utc.replace(hour=8, minute=30, second=0, microsecond=0)
    win_end    = win_start + timedelta(hours=24)

    # Fetch this week + next week to cover window boundaries
    all_events = []
    for url in [
        'https://nfs.faireconomy.media/ff_calendar_thisweek.json',
        'https://nfs.faireconomy.media/ff_calendar_nextweek.json',
    ]:
        try:
            r = requests.get(url, headers=ff_headers, timeout=20)
            if r.status_code == 200:
                all_events.extend(r.json())
                print('     Fetched ' + str(len(r.json())) + ' events from ' + url.split('/')[-1])
            else:
                print('     ' + url.split('/')[-1] + ': HTTP ' + str(r.status_code))
        except Exception as e:
            print('     Fetch error: ' + str(e))

    if not all_events:
        send_telegram('<b>News Alert</b>' + NL + 'Could not fetch ForexFactory data.')
        return

    # Filter: High impact only, within window
    high_in_window = []
    for e in all_events:
        if e.get('impact') != 'High':
            continue
        utc_dt = et_to_utc(e.get('date', ''), e.get('time', ''), now_utc)
        if utc_dt and win_start <= utc_dt < win_end:
            e['_utc_dt'] = utc_dt
            high_in_window.append(e)

    # Sort by time
    high_in_window.sort(key=lambda x: x['_utc_dt'])

    print('     High impact events in window: ' + str(len(high_in_window)))

    # Build Telegram message
    now_ist = now_utc + timedelta(hours=5, minutes=30)
    msg  = '<b>ForexFactory High Impact News</b>' + NL
    msg += now_ist.strftime('%d %b %Y, %I:%M %p IST') + NL
    msg += 'Window: 2:00 PM IST today to 2:00 PM IST tomorrow' + NL
    msg += '(Source: ForexFactory)' + NL + NL

    if not high_in_window:
        msg += 'No high impact events in this window.'
        send_telegram(msg)
        return

    # Group by date
    seen_dates = []
    for e in high_in_window:
        d = e.get('date', '')
        if d not in seen_dates:
            seen_dates.append(d)

    for date_key in seen_dates:
        day_events = [e for e in high_in_window if e.get('date') == date_key]
        if not day_events:
            continue
        # Format date label
        try:
            mo, da, yr = date_key.split('-')
            from datetime import datetime as dt
            d_obj = dt(int(yr), int(mo), int(da))
            today = now_utc.date()
            event_date = d_obj.date()
            if event_date == today:
                day_label = d_obj.strftime('%d %b') + ' (Today)'
            else:
                day_label = d_obj.strftime('%d %b') + ' (Tomorrow)'
        except:
            day_label = date_key

        msg += '<b>' + day_label + '</b>' + NL

        for e in day_events:
            utc_dt   = e['_utc_dt']
            currency = e.get('currency', '??')
            title    = e.get('title', '??')
            forecast = e.get('forecast', '') or ''
            previous = e.get('previous', '') or ''

            # Times
            utc_str  = utc_dt.strftime('%H:%M UTC')
            ist_dt   = utc_dt + timedelta(hours=5, minutes=30)
            ist_str  = ist_dt.strftime('%I:%M %p IST')

            # Affected pairs
            pairs = NEWS_CURRENCY_PAIRS.get(currency.upper(), [])
            pairs_str = ', '.join(pairs) if pairs else currency

            msg += (utc_str + ' (' + ist_str + ')' + NL +
                    currency + ' - ' + title + NL)
            if forecast:
                msg += 'Forecast: ' + forecast + NL
            if previous:
                msg += 'Previous: ' + previous + NL
            msg += 'Pairs: ' + pairs_str + NL + NL

    msg += 'Total: ' + str(len(high_in_window)) + ' high impact events'

    send_telegram(msg)
    print('     News alert sent - ' + str(len(high_in_window)) + ' events')


def main():
    print("=" * 55)
    print("  FXAlexG + ICT Killzone Monitor - Single Scan")
    print("=" * 55)
    now_utc = datetime.now(timezone.utc)
    print("Scan @ " + now_utc.strftime('%Y-%m-%d %H:%M UTC'))

    # ── News alert at 07:30 UTC (1:00 PM IST) ────────────────
    # Send once per day - the 07:30 trigger covers 2PM IST to 2PM IST
    if now_utc.hour == 7 and now_utc.minute < 60:
        try:
            send_news_alert()
        except Exception as e:
            print("  ERROR News: " + str(e))

    # Skip everything else if market is closed (weekend)
    if not is_market_open():
        now = datetime.now(timezone.utc)
        print("Market is closed (weekend). Skipping scan.")
        print("Market reopens Sunday 21:00 UTC.")
        return

    print("\n--- Checking Active Trades ---")
    try:
        check_active_trades()
    except Exception as e:
        print("  ERROR checking trades: " + str(e))

    print("\n--- Strategy 1: FXAlexG (All 9 Pairs) ---")
    scorecard = []
    for pair in PAIRS:
        try:
            result = analyse_fxalexg(pair)
            if result:
                scorecard.append(result)
            time.sleep(1)
        except Exception as e:
            print("  ERROR " + pair + ": " + str(e))

    # Send single scorecard message to Telegram
    if scorecard:
        NL  = chr(10)
        now = datetime.now(timezone.utc).strftime('%H:%M UTC')
        sc_msg = '<b>Market Scorecard ' + now + '</b>' + NL
        sc_msg += '(Needs 8.0+ for A+ alert)' + NL + NL
        for r in sorted(scorecard, key=lambda x: x['pts'], reverse=True):
            grade = r['grade']
            pts   = r['pts']
            pair  = r['pair']
            dir_  = r.get('direction', 'N/A') or 'NEUTRAL'
            emoji = '' if grade == 'A+' else '' if grade == 'B+' else ''
            sc_msg += (emoji + ' ' + pair.replace('_', '/').ljust(9) +
                       ' ' + dir_.ljust(7) +
                       ' [' + grade + '] ' +
                       str(pts) + '/10' + NL)
        send_telegram(sc_msg)
        print('  Scorecard sent for ' + str(len(scorecard)) + ' pairs')

    print("\n--- Strategy 2: ICT Killzone (XAU/USD) ---")
    try:
        analyse_ict_gold()
    except Exception as e:
        print("  ERROR ICT: " + str(e))

    print("\n--- Strategy 3: Gold 2R (XAU/USD) ---")
    try:
        analyse_gold_2r()
    except Exception as e:
        print("  ERROR Gold 2R: " + str(e))

    print("\n--- Strategy 4: All Pairs 1.5R (All 9 Pairs) ---")
    for pair in PAIRS:
        try:
            analyse_all_pairs_15r(pair)
            time.sleep(1)
        except Exception as e:
            print("  ERROR AP15R " + pair + ": " + str(e))

    print("\nScan complete.")

if __name__ == "__main__":
    main()
