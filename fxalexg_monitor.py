# “””
FXAlexG Set & Forget Strategy Monitor

Monitors 8 forex pairs across 4 timeframes.
Detects AOIs (max 60 pips), market structure, H&S patterns,
scores confluence, calls Gemini AI, sends Telegram alerts.
“””

import requests
import time
import os
from datetime import datetime, timezone

# ─────────────────────────────────────────────

# CONFIGURATION — loaded from GitHub Secrets

# ─────────────────────────────────────────────

OANDA_API_KEY      = os.environ.get(“OANDA_API_KEY”, “”)
OANDA_ACCOUNT_ID   = os.environ.get(“OANDA_ACCOUNT_ID”, “”)
OANDA_BASE_URL     = “https://api-fxpractice.oanda.com”

GEMINI_API_KEY     = os.environ.get(“GEMINI_API_KEY”, “”)
GEMINI_MODEL       = “gemini-2.0-flash”
GEMINI_URL         = (
f”https://generativelanguage.googleapis.com/v1beta/models/”
f”{GEMINI_MODEL}:generateContent”
)

TELEGRAM_BOT_TOKEN = os.environ.get(“TELEGRAM_BOT_TOKEN”, “”)
TELEGRAM_CHAT_ID   = os.environ.get(“TELEGRAM_CHAT_ID”, “”)

# ─────────────────────────────────────────────

# STRATEGY SETTINGS

# ─────────────────────────────────────────────

PAIRS = [
‘EUR_USD’, ‘GBP_USD’, ‘USD_JPY’, ‘AUD_USD’,
‘USD_CHF’, ‘NZD_USD’, ‘USD_CAD’, ‘XAU_USD’, ‘BTC_USD’
]
TIMEFRAMES         = [‘W’, ‘D’, ‘H4’, ‘H1’]
EMA_PERIOD         = 50
MAX_AOI_PIPS       = 60
MIN_AOI_TOUCHES    = 3
MIN_GRADE_TO_ALERT = [‘A+’, ‘B+’]
ALERT_COOLDOWN_MIN = 60

# ─────────────────────────────────────────────

# PIP HELPERS

# ─────────────────────────────────────────────

def pip_size(pair):
if ‘JPY’ in pair: return 0.01
if ‘XAU’ in pair: return 0.10
if ‘BTC’ in pair: return 1.00
return 0.0001

def to_pips(price_diff, pair):
return price_diff / pip_size(pair)

def from_pips(pips, pair):
return pips * pip_size(pair)

# ─────────────────────────────────────────────

# OANDA DATA FETCHER

# ─────────────────────────────────────────────

def fetch_candles(pair, granularity, count=120):
url     = f”{OANDA_BASE_URL}/v3/instruments/{pair}/candles”
headers = {“Authorization”: f”Bearer {OANDA_API_KEY}”}
params  = {“granularity”: granularity, “count”: count, “price”: “M”}
try:
r = requests.get(url, headers=headers, params=params, timeout=10)
if r.status_code != 200:
print(f”  OANDA error {r.status_code} for {pair}/{granularity}”)
return []
candles = []
for c in r.json().get(‘candles’, []):
if c.get(‘complete’):
candles.append({
‘time’:  c[‘time’],
‘open’:  float(c[‘mid’][‘o’]),
‘high’:  float(c[‘mid’][‘h’]),
‘low’:   float(c[‘mid’][‘l’]),
‘close’: float(c[‘mid’][‘c’]),
})
return candles
except Exception as e:
print(f”  Fetch error {pair}/{granularity}: {e}”)
return []

# ─────────────────────────────────────────────

# SWING POINT DETECTOR

# ─────────────────────────────────────────────

def swing_points(candles, lookback=5):
highs, lows = [], []
for i in range(lookback, len(candles) - lookback):
window = candles[i - lookback: i + lookback + 1]
if candles[i][‘high’] == max(c[‘high’] for c in window):
highs.append({‘i’: i, ‘price’: candles[i][‘high’]})
if candles[i][‘low’] == min(c[‘low’] for c in window):
lows.append({‘i’: i, ‘price’: candles[i][‘low’]})
return highs, lows

# ─────────────────────────────────────────────

# MARKET STRUCTURE (HH/HL = BULLISH, LH/LL = BEARISH)

# ─────────────────────────────────────────────

def trend(highs, lows):
if len(highs) < 2 or len(lows) < 2:
return ‘NEUTRAL’
h  = sorted(highs[-2:], key=lambda x: x[‘i’])
l  = sorted(lows[-2:],  key=lambda x: x[‘i’])
hh = h[1][‘price’] > h[0][‘price’]
hl = l[1][‘price’] > l[0][‘price’]
lh = h[1][‘price’] < h[0][‘price’]
ll = l[1][‘price’] < l[0][‘price’]
if hh and hl: return ‘BULLISH’
if lh and ll: return ‘BEARISH’
return ‘NEUTRAL’

# ─────────────────────────────────────────────

# 50 EMA

# ─────────────────────────────────────────────

def ema50(candles):
closes = [c[‘close’] for c in candles]
if len(closes) < EMA_PERIOD:
return None
m   = 2 / (EMA_PERIOD + 1)
val = sum(closes[:EMA_PERIOD]) / EMA_PERIOD
for p in closes[EMA_PERIOD:]:
val = (p - val) * m + val
return val

# ─────────────────────────────────────────────

# AOI DETECTOR — max 60 pips wide, min 3 touches

# ─────────────────────────────────────────────

def detect_aois(candles, pair):
highs, lows = swing_points(candles, lookback=3)
max_zone    = from_pips(MAX_AOI_PIPS, pair)
levels      = sorted([p[‘price’] for p in highs + lows])
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
‘top’:       top,
‘bottom’:    bot,
‘mid’:       (top + bot) / 2,
‘touches’:   len(cluster),
‘size_pips’: round(sz_pips, 1),
})
i = j
return zones

def at_aoi(price, zones, pair):
buf = from_pips(5, pair)
for z in zones:
if (z[‘bottom’] - buf) <= price <= (z[‘top’] + buf):
return z
return None

# ─────────────────────────────────────────────

# HEAD & SHOULDERS PATTERN DETECTOR

# ─────────────────────────────────────────────

def detect_hs(candles, highs, lows):
results = []

```
# Bearish H&S
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
        'type':           'HEAD & SHOULDERS',
        'direction':      'BEARISH',
        'left_shoulder':  round(ls['price'], 5),
        'head':           round(hd['price'], 5),
        'right_shoulder': round(rs['price'], 5),
        'neckline':       round(neckline, 5),
        'complete':       complete,
    })

# Bullish Inverse H&S
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
        'type':           'INVERSE H&S',
        'direction':      'BULLISH',
        'left_shoulder':  round(ls['price'], 5),
        'head':           round(hd['price'], 5),
        'right_shoulder': round(rs['price'], 5),
        'neckline':       round(neckline, 5),
        'complete':       complete,
    })

complete_ones = [p for p in results if p['complete']]
return complete_ones[0] if complete_ones else (results[-1] if results else None)
```

# ─────────────────────────────────────────────

# CONFLUENCE SCORER  A+ / B+ / C+ / D+

# ─────────────────────────────────────────────

def score(trends, aoi_zone, hs, ema_sig):
pts, reasons = 0, []

```
# Timeframe alignment
aligned = [tf for tf, t in trends.items() if t != 'NEUTRAL']
pts += len(aligned)
if len(aligned) == 4:
    reasons.append("✅ All 4 TFs aligned")
elif len(aligned) >= 3:
    reasons.append(f"✅ {len(aligned)}/4 TFs aligned")
elif len(aligned) >= 2:
    reasons.append(f"⚠️ {len(aligned)}/4 TFs aligned")
else:
    reasons.append("❌ TFs not aligned")

# AOI
if aoi_zone:
    pts += 2
    reasons.append(
        f"✅ At AOI — {aoi_zone['size_pips']}p wide, "
        f"{aoi_zone['touches']} touches"
    )
else:
    reasons.append("❌ Not at AOI")

# H&S
if hs:
    pts += 3 if hs['complete'] else 2
    tag = "COMPLETE 🎯" if hs['complete'] else "forming 🔄"
    reasons.append(f"✅ {hs['type']} ({hs['direction']}) — {tag}")

# 50 EMA
if ema_sig:
    pts += 1
    side = 'above' if ema_sig == 'ABOVE' else 'below'
    reasons.append(f"✅ H1 price {side} 50 EMA")

grade = 'A+' if pts >= 8 else 'B+' if pts >= 6 else 'C+' if pts >= 4 else 'D+'
return grade, pts, reasons
```

# ─────────────────────────────────────────────

# GEMINI AI ANALYSIS

# ─────────────────────────────────────────────

def ask_gemini(pair, data):
aoi_txt = (
f”YES — {data[‘aoi’][‘bottom’]:.5f} to {data[‘aoi’][‘top’]:.5f} “
f”({data[‘aoi’][‘size_pips’]} pips, {data[‘aoi’][‘touches’]} touches)”
if data[‘aoi’] else “No — price not at a key zone”
)
hs_txt = (
f”{data[‘hs’][‘type’]} ({data[‘hs’][‘direction’]}) | “
f”Neckline: {data[‘hs’][‘neckline’]} | “
f”{‘COMPLETE’ if data[‘hs’][‘complete’] else ‘Forming’}”
if data[‘hs’] else “Not detected”
)
prompt = f””“You are an expert forex analyst using the FXAlexG Set & Forget strategy.
Analyse this setup and give a clear, mobile-friendly trade recommendation.

PAIR: {pair.replace(’_’,’/’)}
PRICE: {data[‘price’]}
TIME: {datetime.now(timezone.utc).strftime(’%Y-%m-%d %H:%M UTC’)}

TREND (top-down):
Weekly : {data[‘trends’].get(‘W’,’—’)}
Daily  : {data[‘trends’].get(‘D’,’—’)}
4H     : {data[‘trends’].get(‘H4’,’—’)}
1H     : {data[‘trends’].get(‘H1’,’—’)}

AOI ZONE    : {aoi_txt}
50 EMA (H1) : {data[‘ema’]} — Price is {data[‘ema_sig’]}
H&S PATTERN : {hs_txt}

GRADE: {data[‘grade’]} | Score: {data[‘pts’]}/10
CONFLUENCES:
{chr(10).join(data[‘reasons’])}

Reply with EXACTLY this format — no extra text:
DIRECTION: [BUY / SELL / WAIT]
ENTRY: [price or zone]
STOP LOSS: [price]
TAKE PROFIT 1: [price — 2R]
TAKE PROFIT 2: [price — 3R]
ANALYSIS: [2-3 sentences max]
RISK NOTE: [one sentence]”””

```
try:
    r = requests.post(
        f"{GEMINI_URL}?key={GEMINI_API_KEY}",
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    if r.status_code == 200:
        return r.json()['candidates'][0]['content']['parts'][0]['text']
    print(f"  Gemini error {r.status_code}: {r.text[:300]}")
except Exception as e:
    print(f"  Gemini call failed: {e}")
return None
```

# ─────────────────────────────────────────────

# TELEGRAM SENDER

# ─────────────────────────────────────────────

def send_telegram(msg):
try:
r = requests.post(
f”https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage”,
json={
“chat_id”:    TELEGRAM_CHAT_ID,
“text”:       msg,
“parse_mode”: “HTML”,
},
timeout=10,
)
if r.status_code != 200:
print(f”  Telegram error {r.status_code}: {r.text[:100]}”)
except Exception as e:
print(f”  Telegram failed: {e}”)

# ─────────────────────────────────────────────

# ALERT COOLDOWN — prevents duplicate alerts

# ─────────────────────────────────────────────

_last_alert = {}

def can_alert(pair):
return (time.time() - _last_alert.get(pair, 0)) > ALERT_COOLDOWN_MIN * 60

def mark_alerted(pair):
_last_alert[pair] = time.time()

# ─────────────────────────────────────────────

# ANALYSE ONE PAIR

# ─────────────────────────────────────────────

def analyse(pair):
print(f”  → {pair}”)
trends   = {}
all_aois = []
best_hs  = None
h1_ema   = None
ema_sig  = None

```
for tf in TIMEFRAMES:
    candles = fetch_candles(pair, tf)
    if len(candles) < 20:
        continue
    h, l = swing_points(candles)
    trends[tf] = trend(h, l)

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

if not trends:
    return

latest = fetch_candles(pair, 'H1', count=2)
if not latest:
    return
price = latest[-1]['close']

aoi_zone        = at_aoi(price, all_aois, pair)
grade, pts, reasons = score(trends, aoi_zone, best_hs, ema_sig)

print(f"     Grade: {grade} | Score: {pts} | Trends: {trends}")

if grade not in MIN_GRADE_TO_ALERT:
    return
if not can_alert(pair):
    print(f"     Skipped — cooldown active")
    return

data = {
    'price':   price,
    'trends':  trends,
    'aoi':     aoi_zone,
    'ema':     round(h1_ema, 5) if h1_ema else 'N/A',
    'ema_sig': ema_sig or 'N/A',
    'hs':      best_hs,
    'grade':   grade,
    'pts':     pts,
    'reasons': reasons,
}

analysis = ask_gemini(pair, data)
if not analysis:
    return

bullish = sum(1 for t in trends.values() if t == 'BULLISH')
bearish = sum(1 for t in trends.values() if t == 'BEARISH')
emoji   = '🟢' if bullish > bearish else '🔴' if bearish > bullish else '🟡'

msg = (
    f"{emoji} <b>FXAlexG Alert</b>\n"
    f"<b>{pair.replace('_','/')}  [{grade}]  Score: {pts}/10</b>\n\n"
    f"💰 Price: <code>{price}</code>\n"
    f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
    f"<b>Confluences:</b>\n"
    f"{chr(10).join(reasons)}\n\n"
    f"<b>Gemini Analysis:</b>\n"
    f"{analysis}"
)
send_telegram(msg)
mark_alerted(pair)
print(f"     ✅ Alert sent [{grade}]")
```

# ─────────────────────────────────────────────

# MAIN — single scan, triggered by GitHub Actions

# ─────────────────────────────────────────────

def main():
print(”=” * 55)
print(”  FXAlexG Set & Forget Monitor — Single Scan”)
print(”=” * 55)
print(f”Scan @ {datetime.now(timezone.utc).strftime(’%Y-%m-%d %H:%M UTC’)}”)
for pair in PAIRS:
try:
analyse(pair)
time.sleep(1)
except Exception as e:
print(f”  ERROR {pair}: {e}”)
print(“Scan complete.”)

if **name** == “**main**”:
main()
