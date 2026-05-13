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
]
TIMEFRAMES         = ['W', 'D', 'H4', 'H1']
EMA_PERIOD         = 50
MAX_AOI_PIPS       = 60
MIN_AOI_TOUCHES    = 3
MIN_GRADE_TO_ALERT = ['A+', 'B+']
ALERT_COOLDOWN_MIN = 60

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
        r = requests.get(url, headers=headers, params=params, timeout=10)
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

def swing_points(candles, lookback=5):
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
def score(trends, aoi_zone, hs, ema_sig, entry_signal=None):
    pts, reasons = 0, []
    aligned = [tf for tf, t in trends.items() if t != "NEUTRAL"]
    pts += len(aligned)

    # Show each timeframe individually with its direction
    tf_labels = {"W": "Weekly", "D": "Daily", "H4": "4H", "H1": "1H"}
    tf_order  = ["W", "D", "H4", "H1"]
    for tf in tf_order:
        t = trends.get(tf, "NEUTRAL")
        if t == "BULLISH":
            reasons.append(tf_labels[tf] + ": BULLISH")
        elif t == "BEARISH":
            reasons.append(tf_labels[tf] + ": BEARISH")
        else:
            reasons.append(tf_labels[tf] + ": NEUTRAL")
    if aoi_zone:
        pts += 2
        reasons.append("At AOI " + str(aoi_zone["size_pips"]) + "p wide, " + str(aoi_zone["touches"]) + " touches")
    else:
        reasons.append("Not at AOI")
    if hs:
        pts += 3 if hs["complete"] else 2
        tag = "COMPLETE" if hs["complete"] else "forming"
        reasons.append(hs["type"] + " (" + hs["direction"] + ") " + tag)
    if ema_sig:
        pts += 1
        side = "above" if ema_sig == "ABOVE" else "below"
        reasons.append("H1 price " + side + " 50 EMA")
    if entry_signal:
        strength_pts = 2 if entry_signal["strength"] == "STRONG" else 1
        pts += strength_pts
        reasons.append(entry_signal["pattern"] + " on H1 (" + entry_signal["strength"] + ")")
    else:
        reasons.append("No entry signal yet")
    grade = "A+" if pts >= 9 else "B+" if pts >= 7 else "C+" if pts >= 5 else "D+"
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
    es = data.get('entry_signal')
    es_txt = (
        es['pattern'] + ' (' + es['strength'] + ') Entry at ' + str(es['entry'])
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
        "GRADE: " + data['grade'] + " Score: " + str(data['pts']) + "/12\n"
        "CONFLUENCES: " + ", ".join(data['reasons']) + "\n\n"
        "Reply with EXACTLY this format:\n"
        "DIRECTION: [BUY / SELL / WAIT]\n"
        "ENTRY: [price or zone]\n"
        "STOP LOSS: [price]\n"
        "TAKE PROFIT 1: [price 2R]\n"
        "TAKE PROFIT 2: [price 3R]\n"
        "ANALYSIS: [2-3 sentences]\n"
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
            timeout=10,
        )
        if r.status_code != 200:
            print("  Telegram error " + str(r.status_code))
    except Exception as e:
        print("  Telegram failed: " + str(e))

# ─────────────────────────────────────────────
# ALERT COOLDOWN
# ─────────────────────────────────────────────
_last_alert     = {}
_last_ict_alert = {}

def can_alert(pair):
    return (time.time() - _last_alert.get(pair, 0)) > ALERT_COOLDOWN_MIN * 60

def mark_alerted(pair):
    _last_alert[pair] = time.time()

def can_ict_alert():
    return (time.time() - _last_ict_alert.get('XAU', 0)) > ICT_COOLDOWN_MIN * 60

def mark_ict_alerted():
    _last_ict_alert['XAU'] = time.time()

# ─────────────────────────────────────────────
# STRATEGY 1 - FXALEXG ANALYSER
# ─────────────────────────────────────────────
def analyse_fxalexg(pair):
    print("  [FXAlexG] " + pair)
    trends    = {}
    all_aois  = []
    best_hs   = None
    h1_ema    = None
    ema_sig   = None
    h1_candles = []

    for tf in TIMEFRAMES:
        candles = fetch_candles(pair, tf)
        if len(candles) < 20:
            continue
        h, l = swing_points(candles)
        trends[tf] = trend(h, l, candles)
        if tf in ('D', 'W'):
            all_aois.extend(detect_aois(candles, pair))
        if tf in ('H4', 'H1'):
            hs = detect_hs(candles, h, l)
            if hs and (best_hs is None or
                       (hs['complete'] and not best_hs['complete'])):
                best_hs = hs
        if tf == 'H1':
            h1_ema = ema50(candles)
            if h1_ema:
                last_close = candles[-1]['close']
                ema_sig = 'ABOVE' if last_close > h1_ema else 'BELOW'
            h1_candles = candles

    if not trends:
        return

    latest = fetch_candles(pair, 'H1', count=2)
    if not latest:
        return
    price = latest[-1]['close']

    # Determine trade direction from majority trend
    bullish_count = sum(1 for t in trends.values() if t == 'BULLISH')
    bearish_count = sum(1 for t in trends.values() if t == 'BEARISH')
    direction = 'BUY' if bullish_count > bearish_count else 'SELL' if bearish_count > bullish_count else None

    # Detect entry signal on H1 candles
    entry_signal = None
    if direction and h1_candles and len(h1_candles) >= 3:
        entry_signal = detect_entry_signal(h1_candles, direction)

    aoi_zone        = at_aoi(price, all_aois, pair)
    grade, pts, reasons = score(trends, aoi_zone, best_hs, ema_sig, entry_signal)
    print("     Grade: " + grade + " | Score: " + str(pts) + " | Trends: " + str(trends))

    if grade not in MIN_GRADE_TO_ALERT:
        return
    if not can_alert(pair):
        print("     Skipped - cooldown active")
        return

    data = {
        'price':        price,
        'trends':       trends,
        'aoi':          aoi_zone,
        'ema':          round(h1_ema, 5) if h1_ema else 'N/A',
        'ema_sig':      ema_sig or 'N/A',
        'hs':           best_hs,
        'entry_signal': entry_signal,
        'grade':        grade,
        'pts':          pts,
        'reasons':      reasons,
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

    es = data.get('entry_signal')
    es_line = (
        "Entry Signal: " + es['pattern'] + " @ " + str(es['entry']) + "\n"
        if es else "Entry Signal: Waiting for confirmation\n"
    )

    msg = (
        emojis[emoji] + " <b>FXAlexG Alert</b>\n"
        "<b>" + pair.replace('_', '/') + "  [" + grade + "]  Score: " + str(pts) + "/12</b>\n\n"
        "Price: <code>" + str(price) + "</code>\n"
        "Time: " + datetime.now(timezone.utc).strftime('%H:%M UTC') + "\n"
        + es_line +
        "\n<b>Confluences:</b>\n" + reasons_txt + "\n"
        "<b>Analysis:</b>\n" + analysis
    )
    send_telegram(msg)
    mark_alerted(pair)
    print("     Alert sent [" + grade + "]")

# ─────────────────────────────────────────────
# STRATEGY 2 - ICT KILLZONE ANALYSER (XAU/USD)
# ─────────────────────────────────────────────
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
def main():
    print("=" * 55)
    print("  FXAlexG + ICT Killzone Monitor - Single Scan")
    print("=" * 55)
    print("Scan @ " + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))

    print("\n--- Strategy 1: FXAlexG (All 9 Pairs) ---")
    for pair in PAIRS:
        try:
            analyse_fxalexg(pair)
            time.sleep(1)
        except Exception as e:
            print("  ERROR " + pair + ": " + str(e))

    print("\n--- Strategy 2: ICT Killzone (XAU/USD) ---")
    try:
        analyse_ict_gold()
    except Exception as e:
        print("  ERROR ICT: " + str(e))

    print("\nScan complete.")

if __name__ == "__main__":
    main()
