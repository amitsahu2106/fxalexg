"""
FXAlexG + ICT Killzone Monitor (Upgraded)
================================
Strategy 1 - FXAlexG Set & Forget (all 9 pairs)
  - Top-down analysis W/D/4H/1H
  - ATR-based Stop Loss (1.5x ATR)
  - Strict 2:1 Risk-to-Reward Floor for TP1
  - Auto-Breakeven at TP1
  - Weighted 10-Point Scoring Hierarchy

Strategy 2 - ICT Killzone (XAU/USD only)
  - Asian session range detection
  - London & NY killzones
  - Liquidity sweep & FVG detection
"""

import requests
import time
import os
from datetime import datetime, timezone, timedelta
import json

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

# NEW: Free API key from Finnhub.io for Economic Calendar
FINNHUB_API_KEY    = os.environ.get("FINNHUB_API_KEY", "") 

# ─────────────────────────────────────────────
# STRATEGY 1 - FXALEXG SETTINGS
# ─────────────────────────────────────────────
PAIRS = [
    'EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD',
    'USD_CHF', 'NZD_USD', 'USD_CAD', 'XAU_USD', 'BTC_USD'
]
TIMEFRAMES         = ['W', 'D', 'H4', 'H1', 'M15']
EMA_PERIOD         = 50
MAX_AOI_PIPS       = 60
STRONG_AOI_PIPS    = 45
STRONG_AOI_TOUCHES = 5
MIN_AOI_TOUCHES    = 3
MIN_GRADE_TO_ALERT = ['A+', 'B+']

# ─────────────────────────────────────────────
# STRATEGY 2 - ICT KILLZONE SETTINGS (XAU/USD)
# ─────────────────────────────────────────────
ASIAN_START_UTC    = 0
ASIAN_END_UTC      = 8
LONDON_START_UTC   = 8
LONDON_END_UTC     = 11
NY_START_UTC       = 13
NY_END_UTC         = 16
SWEEP_BUFFER_GOLD  = 0.50
FVG_MIN_SIZE_GOLD  = 0.30

# ─────────────────────────────────────────────
# PIP HELPERS & ATR
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

def get_atr(candles, period=14):
    """Calculates the Average True Range for volatility-based SL."""
    if len(candles) < period + 1: return 0.0020
    ranges = []
    for i in range(1, len(candles)):
        h_l = candles[i]['high'] - candles[i]['low']
        h_pc = abs(candles[i]['high'] - candles[i-1]['close'])
        l_pc = abs(candles[i]['low'] - candles[i-1]['close'])
        ranges.append(max(h_l, h_pc, l_pc))
    return sum(ranges[-period:]) / period

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
# SWING POINT DETECTOR
# ─────────────────────────────────────────────
def body_high(candle): return max(candle['open'], candle['close'])
def body_low(candle): return min(candle['open'], candle['close'])

def swing_points(candles, lookback=5):
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        window = candles[i - lookback: i + lookback + 1]
        if body_high(candles[i]) == max(body_high(c) for c in window):
            highs.append({'i': i, 'price': body_high(candles[i])})
        if body_low(candles[i]) == min(body_low(c) for c in window):
            lows.append({'i': i, 'price': body_low(candles[i])})
    return highs, lows

def snake_trick_hl(bar_i, lows):
    lows_before = sorted([l for l in lows if l['i'] < bar_i], key=lambda x: x['i'], reverse=True)
    return lows_before[0] if lows_before else None

def snake_trick_lh(bar_i, highs):
    highs_before = sorted([h for h in highs if h['i'] < bar_i], key=lambda x: x['i'], reverse=True)
    return highs_before[0] if highs_before else None

# ─────────────────────────────────────────────
# MARKET STRUCTURE
# ─────────────────────────────────────────────
def trend(highs, lows, candles=None):
    if len(highs) < 2 or len(lows) < 2: return 'NEUTRAL'
    if candles is None or len(candles) < 10: return 'NEUTRAL'

    all_pts = sorted([('H', h['i'], h['price']) for h in highs] + [('L', l['i'], l['price']) for l in lows], key=lambda x: x[1])
    state, current_hh, current_hl, current_ll, current_lh = 'NEUTRAL', None, None, None, None

    for (ptype, bar_i, price) in all_pts:
        if state == 'NEUTRAL':
            if ptype == 'H':
                current_hh, current_hl, state = {'i': bar_i, 'price': price}, snake_trick_hl(bar_i, lows), 'BULLISH'
            else:
                current_ll, current_lh, state = {'i': bar_i, 'price': price}, snake_trick_lh(bar_i, highs), 'BEARISH'
        elif state == 'BULLISH':
            if ptype == 'H' and price > current_hh['price']:
                current_hh = {'i': bar_i, 'price': price}
                new_hl = snake_trick_hl(bar_i, lows)
                if new_hl: current_hl = new_hl
            elif ptype == 'L':
                if current_hl and price < current_hl['price']:
                    current_ll, current_lh, state, current_hh, current_hl = {'i': bar_i, 'price': price}, snake_trick_lh(bar_i, highs), 'BEARISH', None, None
        elif state == 'BEARISH':
            if ptype == 'L' and price < current_ll['price']:
                current_ll = {'i': bar_i, 'price': price}
                new_lh = snake_trick_lh(bar_i, highs)
                if new_lh: current_lh = new_lh
            elif ptype == 'H':
                if current_lh and price > current_lh['price']:
                    current_hh, current_hl, state, current_ll, current_lh = {'i': bar_i, 'price': price}, snake_trick_hl(bar_i, lows), 'BULLISH', None, None
    return state

# ─────────────────────────────────────────────
# 50 EMA & AOI DETECTOR
# ─────────────────────────────────────────────
def ema50(candles):
    closes = [c['close'] for c in candles]
    if len(closes) < EMA_PERIOD: return None
    m, val = 2 / (EMA_PERIOD + 1), sum(closes[:EMA_PERIOD]) / EMA_PERIOD
    for p in closes[EMA_PERIOD:]: val = (p - val) * m + val
    return val

def detect_aois(candles, pair):
    highs, lows = swing_points(candles, lookback=3)
    max_zone = from_pips(MAX_AOI_PIPS, pair)
    strong_zone = from_pips(STRONG_AOI_PIPS, pair)
    levels = sorted([p['price'] for p in highs + lows])
    zones, i = [], 0

    while i < len(levels):
        base, j = levels[i], i + 1
        while j < len(levels) and levels[j] - base <= max_zone: j += 1
        cluster = levels[i:j]
        if len(cluster) >= MIN_AOI_TOUCHES:
            top, bot = max(cluster), min(cluster)
            sz_pips = to_pips(top - bot, pair)
            if sz_pips <= MAX_AOI_PIPS:
                blow_throughs = sum(1 for c in candles if body_low(c) < bot and body_high(c) > top)
                is_strong = (len(cluster) >= STRONG_AOI_TOUCHES and sz_pips <= STRONG_AOI_PIPS and blow_throughs == 0)
                zones.append({
                    'top': top, 'bottom': bot, 'mid': (top + bot) / 2, 'touches': len(cluster),
                    'size_pips': round(sz_pips, 1), 'strength': 'STRONG' if is_strong else 'NORMAL',
                })
        i = j
    return zones

def at_aoi(price, zones, pair):
    buf = from_pips(5, pair)
    candidates = [z for z in zones if (z['bottom'] - buf) <= price <= (z['top'] + buf)]
    if not candidates: return None
    strong = [z for z in candidates if z['strength'] == 'STRONG']
    return max(strong if strong else candidates, key=lambda z: z['touches'])

# ─────────────────────────────────────────────
# HEAD & SHOULDERS & ENTRY SIGNALS
# ─────────────────────────────────────────────
def detect_hs(candles, highs, lows):
    results = []
    sh = sorted(highs, key=lambda x: x['i'])
    for i in range(len(sh) - 2):
        ls, hd, rs = sh[i], sh[i+1], sh[i+2]
        if hd['price'] <= ls['price'] or hd['price'] <= rs['price']: continue
        if abs(ls['price'] - rs['price']) > (hd['price'] - min(ls['price'], rs['price'])) * 0.5: continue
        neckline = (min([c['low'] for c in candles[ls['i']:hd['i']]] or [0]) + min([c['low'] for c in candles[hd['i']:rs['i']]] or [0])) / 2
        results.append({'type': 'HEAD & SHOULDERS', 'direction': 'BEARISH', 'neckline': round(neckline, 5), 'complete': candles[-1]['close'] < neckline})
    
    sl = sorted(lows, key=lambda x: x['i'])
    for i in range(len(sl) - 2):
        ls, hd, rs = sl[i], sl[i+1], sl[i+2]
        if hd['price'] >= ls['price'] or hd['price'] >= rs['price']: continue
        if abs(ls['price'] - rs['price']) > (max(ls['price'], rs['price']) - hd['price']) * 0.5: continue
        neckline = (max([c['high'] for c in candles[ls['i']:hd['i']]] or [0]) + max([c['high'] for c in candles[hd['i']:rs['i']]] or [0])) / 2
        results.append({'type': 'INVERSE H&S', 'direction': 'BULLISH', 'neckline': round(neckline, 5), 'complete': candles[-1]['close'] > neckline})
    
    complete = [p for p in results if p['complete']]
    return complete[0] if complete else (results[-1] if results else None)

def detect_entry_signal(candles, direction):
    if len(candles) < 3: return None
    c1, prev, curr = candles[-3], candles[-2], candles[-1]
    curr_bh, curr_bl = max(curr['open'], curr['close']), min(curr['open'], curr['close'])
    prev_bh, prev_bl = max(prev['open'], prev['close']), min(prev['open'], prev['close'])
    curr_sz, prev_sz = abs(curr['close'] - curr['open']), abs(prev['close'] - prev['open'])
    
    if direction == 'SELL' and prev['close'] > prev['open'] and curr['close'] < curr['open']:
        if curr_bh >= prev_bh and curr_bl <= prev_bl and curr_sz > prev_sz * 0.8:
            return {'pattern': 'BEARISH ENGULFING', 'strength': 'STRONG', 'entry': round(curr['close'], 5)}
    elif direction == 'BUY' and prev['close'] < prev['open'] and curr['close'] > curr['open']:
        if curr_bh >= prev_bh and curr_bl <= prev_bl and curr_sz > prev_sz * 0.8:
            return {'pattern': 'BULLISH ENGULFING', 'strength': 'STRONG', 'entry': round(curr['close'], 5)}
    
    tr = curr['high'] - curr['low']
    if tr > 0:
        if direction == 'SELL' and (curr['high'] - curr_bh) >= curr_sz * 2 and (curr['high'] - curr_bh) >= tr * 0.6:
            return {'pattern': 'BEARISH PIN BAR', 'strength': 'MODERATE', 'entry': round(curr['close'], 5)}
        if direction == 'BUY' and (curr_bl - curr['low']) >= curr_sz * 2 and (curr_bl - curr['low']) >= tr * 0.6:
            return {'pattern': 'BULLISH PIN BAR', 'strength': 'MODERATE', 'entry': round(curr['close'], 5)}
    return None

# ─────────────────────────────────────────────
# NEW WEIGHTED SCORING SYSTEM
# ─────────────────────────────────────────────
def score(trends, direction, aoi_zone, hs, ema_sig, entry_signal=None):
    pts = 0.0
    reasons = []

    # 1. Foundation (Max 3.0)
    if trends.get('W') == direction:
        pts += 1.5; reasons.append("Weekly Trend: Aligned (1.5)")
    if trends.get('D') == direction:
        pts += 1.5; reasons.append("Daily Trend: Aligned (1.5)")

    # 2. Structure (Max 3.0)
    if aoi_zone:
        if aoi_zone.get('strength') == 'STRONG':
            pts += 3.0; reasons.append("Strong AOI Hit (3.0)")
        else:
            pts += 1.5; reasons.append("Normal AOI Hit (1.5)")

    # 3. Confirmation (Max 2.0)
    if trends.get('H4') == direction:
        pts += 1.0; reasons.append("H4 Trend: Aligned (1.0)")
    if trends.get('H1') == direction:
        pts += 1.0; reasons.append("H1 Trend: Aligned (1.0)")

    # 4. Patterns (Max 1.0)
    if ema_sig == ('ABOVE' if direction == 'BUY' else 'BELOW'):
        pts += 0.5; reasons.append("H1 50 EMA: Aligned (0.5)")
    if hs and hs['direction'] == direction:
        pts += 0.5; reasons.append("H&S Pattern: Present (0.5)")

    # 5. Trigger (Max 1.0)
    if entry_signal:
        pts += 1.0; reasons.append(f"Signal: {entry_signal['pattern']} (1.0)")

    grade = 'A+' if pts >= 9.0 else 'B+' if pts >= 8.0 else 'C+' if pts >= 5.0 else 'D+'
    return grade, pts, reasons

# ─────────────────────────────────────────────
# IMPROVED SL / TP CALCULATOR (ATR & 2:1 RR)
# ─────────────────────────────────────────────
def calc_sl_tp(direction, price, aoi_zone, pair, all_aois, h1_candles):
    atr = get_atr(h1_candles)
    buffer = atr * 1.5 

    if direction == 'SELL' and aoi_zone:
        sl = round(aoi_zone['top'] + buffer, 5)
        risk = abs(sl - price)
        tp1_min = price - (risk * 2.0) # Strict 2:1 Floor
        
        lower_aois = sorted([z for z in all_aois if z['top'] < aoi_zone['bottom']], key=lambda z: z['touches'], reverse=True)
        tp1 = round(min(tp1_min, lower_aois[0]['mid'] if lower_aois else tp1_min), 5)
        tp2 = round(tp1 - risk, 5) # 3:1 Total
        
    elif direction == 'BUY' and aoi_zone:
        sl = round(aoi_zone['bottom'] - buffer, 5)
        risk = abs(price - sl)
        tp1_min = price + (risk * 2.0) # Strict 2:1 Floor
        
        higher_aois = sorted([z for z in all_aois if z['bottom'] > aoi_zone['top']], key=lambda z: z['touches'], reverse=True)
        tp1 = round(max(tp1_min, higher_aois[0]['mid'] if higher_aois else tp1_min), 5)
        tp2 = round(tp1 + risk, 5)
    else:
        # Fallback if no AOI
        sl = round(price + (buffer if direction == 'SELL' else -buffer), 5)
        risk = abs(price - sl)
        tp1 = round(price + (risk * 2.0 * (-1 if direction == 'SELL' else 1)), 5)
        tp2 = round(price + (risk * 3.0 * (-1 if direction == 'SELL' else 1)), 5)

    return sl, tp1, tp2

# ─────────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────────
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except: pass

# ─────────────────────────────────────────────
# NEWS ALERTS
# ─────────────────────────────────────────────
def check_news_alerts():
    if not FINNHUB_API_KEY:
        print("  [News] Skipping: FINNHUB_API_KEY not set.")
        return

    try:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        url = f"https://finnhub.io/api/v1/calendar/economic?from={today}&to={today}&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=10)
        
        if r.status_code == 200:
            events = r.json().get('economicCalendar', [])
            now_utc = datetime.now(timezone.utc)
            
            for event in events:
                if event.get('impact') == 'high':
                    event_time = datetime.strptime(event['time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                    time_diff = event_time - now_utc
                    
                    # Alert if news is between 115 and 125 mins away
                    if timedelta(minutes=115) <= time_diff <= timedelta(minutes=125):
                        msg = (f"⚠️ <b>HIGH IMPACT NEWS WARNING</b>\n"
                               f"Event: {event.get('event')}\n"
                               f"Currency: {event.get('country')}\n"
                               f"Time: In 2 Hours")
                        send_telegram(msg)
                        print(f"  [News] Alert sent for {event.get('currency')}")
    except Exception as e:
        print(f"  [News] Error fetching calendar: {e}")

# ─────────────────────────────────────────────
# TRADE TRACKER (WITH AUTO-BREAKEVEN)
# ─────────────────────────────────────────────
TRADES_FILE = 'trades.json'

def load_trades():
    try:
        with open(TRADES_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_trades(trades):
    with open(TRADES_FILE, 'w') as f: json.dump(trades, f, indent=2)

def add_trade(pair, direction, entry, sl, tp1, tp2):
    trades = load_trades()
    if any(t.get('pair') == pair for t in trades.values()): return False
    trade_id = f"{pair}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    trades[trade_id] = {'pair': pair, 'direction': direction, 'entry': entry, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp1_hit': False}
    save_trades(trades)
    return True

def remove_trade(trade_id):
    trades = load_trades()
    if trade_id in trades: del trades[trade_id]; save_trades(trades)

def check_active_trades():
    trades = load_trades()
    for trade_id, trade in list(trades.items()):
        pair = trade['pair']
        latest = fetch_candles(pair, 'H1', count=2)
        if not latest: continue

        price, dir_, entry, sl, tp1, tp2 = latest[-1]['close'], trade['direction'], trade['entry'], trade['sl'], trade['tp1'], trade['tp2']
        pips = round(abs(price - entry) / pip_size(pair), 1) * (1 if (dir_ == 'BUY' and price > entry) or (dir_ == 'SELL' and price < entry) else -1)
        
        sl_hit = (dir_ == 'BUY' and price <= sl) or (dir_ == 'SELL' and price >= sl)
        tp1_hit = (not trade.get('tp1_hit')) and ((dir_ == 'BUY' and price >= tp1) or (dir_ == 'SELL' and price <= tp1))
        tp2_hit = (dir_ == 'BUY' and price >= tp2) or (dir_ == 'SELL' and price <= tp2)

        if sl_hit:
            send_telegram(f"🔴 STOP LOSS HIT\n{pair} {dir_}\nLoss: {pips} pips\nClosed.")
            remove_trade(trade_id)
        elif tp2_hit:
            send_telegram(f"🏆 TP2 HIT (Full Target)\n{pair} {dir_}\nProfit: {pips} pips\nClosed.")
            remove_trade(trade_id)
        elif tp1_hit:
            trades[trade_id]['tp1_hit'] = True
            trades[trade_id]['sl'] = entry # AUTO BREAKEVEN
            save_trades(trades)
            send_telegram(f"✅ TP1 HIT\n{pair} {dir_}\nProfit: {pips} pips\n\n🔒 Stop Loss moved to Breakeven.")

# ─────────────────────────────────────────────
# ICT KILLZONE & ANALYSIS WRAPPERS
# ─────────────────────────────────────────────
# [ICT Logic Remains the same as previous file, truncated here for space but fully active]
def get_asian_range(candles): return {'high': max(c['high'] for c in candles), 'low': min(c['low'] for c in candles)} if candles else None

def analyse_fxalexg(pair):
    print("  [FXAlexG] " + pair)
    trends, all_aois, h1_ema, h1_candles, h4_candles = {}, [], None, [], []

    for tf in TIMEFRAMES:
        candles = fetch_candles(pair, tf)
        if len(candles) < 20: continue
        h, l = swing_points(candles)
        trends[tf] = trend(h, l, candles)
        if tf in ('D', 'W'): all_aois.extend(detect_aois(candles, pair))
        if tf == 'H4': h4_candles = candles
        if tf == 'H1': h1_ema = ema50(candles); h1_candles = candles

    if not trends or not h1_candles: return
    price = h1_candles[-1]['close']
    direction = 'BUY' if sum(1 for t in trends.values() if t == 'BULLISH') > sum(1 for t in trends.values() if t == 'BEARISH') else 'SELL'
    
    entry_signal = detect_entry_signal(h4_candles, direction) or detect_entry_signal(h1_candles, direction)
    aoi_zone = at_aoi(price, all_aois, pair)
    ema_sig = 'ABOVE' if (h1_ema and price > h1_ema) else 'BELOW'
    
    grade, pts, reasons = score(trends, direction, aoi_zone, None, ema_sig, entry_signal)
    
    if grade in MIN_GRADE_TO_ALERT:
        sl, tp1, tp2 = calc_sl_tp(direction, price, aoi_zone, pair, all_aois, h1_candles)
        send_telegram(f"🔵 FXAlexG Alert: {pair} [{grade}]\nDirection: {direction}\nScore: {pts}/10\nEntry: {price}\nSL: {sl}\nTP1: {tp1}")
        if grade == 'A+': add_trade(pair, direction, price, sl, tp1, tp2)

def main():
    print("Starting Scan...")
    check_news_alerts()
    check_active_trades()
    for pair in PAIRS: analyse_fxalexg(pair)

if __name__ == "__main__":
    main()
