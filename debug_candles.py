# ═══════════════════════════════════════════════════════════════
# DEBUG TOOL: Verify OANDA candle data vs TradingView
# Prints last 10 candles + swing points + trend for each TF
# Run via GitHub Actions with pair input
# ═══════════════════════════════════════════════════════════════
import requests, os
from datetime import datetime, timezone

OANDA_API_KEY      = os.environ.get('OANDA_API_KEY', '')
OANDA_BASE_URL     = 'https://api-fxpractice.oanda.com'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')
DEBUG_PAIR         = os.environ.get('DEBUG_PAIR', 'EUR_USD').upper().replace('/', '_')

TIMEFRAMES = ['W', 'D', 'H4', 'H1']
TF_NAMES   = {'W': 'Weekly', 'D': 'Daily', 'H4': '4-Hour', 'H1': '1-Hour'}
TF_COUNT   = {'W': 30, 'D': 60, 'H4': 120, 'H1': 120}

def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    return 0.0001

def fetch_candles(pair, granularity, count=120):
    url     = OANDA_BASE_URL + '/v3/instruments/' + pair + '/candles'
    headers = {'Authorization': 'Bearer ' + OANDA_API_KEY}
    params  = {'granularity': granularity, 'count': count, 'price': 'M'}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            print('OANDA error: ' + str(r.status_code))
            return []
        out = []
        for c in r.json().get('candles', []):
            if c.get('complete'):
                out.append({
                    'time':  c['time'][:16].replace('T', ' '),
                    'open':  float(c['mid']['o']),
                    'high':  float(c['mid']['h']),
                    'low':   float(c['mid']['l']),
                    'close': float(c['mid']['c']),
                })
        return out
    except Exception as e:
        print('Fetch error: ' + str(e))
        return []

def body_high(c): return max(c['open'], c['close'])
def body_low(c):  return min(c['open'], c['close'])

def swing_points(candles, lookback=5):
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        window = candles[i - lookback: i + lookback + 1]
        if body_high(candles[i]) == max(body_high(c) for c in window):
            highs.append({'i': i, 'price': body_high(candles[i]), 'time': candles[i]['time']})
        if body_low(candles[i]) == min(body_low(c) for c in window):
            lows.append({'i': i, 'price': body_low(candles[i]), 'time': candles[i]['time']})
    return highs, lows

def snake_trick_hl(bar_i, lows):
    lows_before = sorted([l for l in lows if l['i'] < bar_i], key=lambda x: x['i'], reverse=True)
    return lows_before[0] if lows_before else None

def snake_trick_lh(bar_i, highs):
    highs_before = sorted([h for h in highs if h['i'] < bar_i], key=lambda x: x['i'], reverse=True)
    return highs_before[0] if highs_before else None

def trend_with_debug(highs, lows, candles):
    if len(highs) < 2 or len(lows) < 2:
        return 'NEUTRAL', [], []

    all_pts = sorted(
        [('H', h['i'], h['price'], h['time']) for h in highs] +
        [('L', l['i'], l['price'], l['time']) for l in lows],
        key=lambda x: x[1]
    )

    state      = 'NEUTRAL'
    current_hh = None
    current_hl = None
    current_ll = None
    current_lh = None
    events     = []

    for (ptype, bar_i, price, t) in all_pts:
        if state == 'NEUTRAL':
            if ptype == 'H':
                current_hh = {'i': bar_i, 'price': price, 'time': t}
                current_hl = snake_trick_hl(bar_i, lows)
                state = 'BULLISH'
                events.append('START BULLISH - HH at ' + t + ' @ ' + str(round(price, 5)))
            else:
                current_ll = {'i': bar_i, 'price': price, 'time': t}
                current_lh = snake_trick_lh(bar_i, highs)
                state = 'BEARISH'
                events.append('START BEARISH - LL at ' + t + ' @ ' + str(round(price, 5)))

        elif state == 'BULLISH':
            if ptype == 'H' and price > current_hh['price']:
                current_hh = {'i': bar_i, 'price': price, 'time': t}
                new_hl = snake_trick_hl(bar_i, lows)
                if new_hl:
                    current_hl = new_hl
                events.append('  New HH at ' + t + ' @ ' + str(round(price, 5)))
            elif ptype == 'L' and current_hl and price < current_hl['price']:
                events.append('  BOS BEARISH at ' + t + ' @ ' + str(round(price, 5)) +
                              ' (broke HL @ ' + str(round(current_hl['price'], 5)) + ')')
                current_ll = {'i': bar_i, 'price': price, 'time': t}
                current_lh = snake_trick_lh(bar_i, highs)
                state = 'BEARISH'
                current_hh = current_hl = None

        elif state == 'BEARISH':
            if ptype == 'L' and price < current_ll['price']:
                current_ll = {'i': bar_i, 'price': price, 'time': t}
                new_lh = snake_trick_lh(bar_i, highs)
                if new_lh:
                    current_lh = new_lh
                events.append('  New LL at ' + t + ' @ ' + str(round(price, 5)))
            elif ptype == 'H' and current_lh and price > current_lh['price']:
                events.append('  BOS BULLISH at ' + t + ' @ ' + str(round(price, 5)) +
                              ' (broke LH @ ' + str(round(current_lh['price'], 5)) + ')')
                current_hh = {'i': bar_i, 'price': price, 'time': t}
                current_hl = snake_trick_hl(bar_i, highs)
                state = 'BULLISH'
                current_ll = current_lh = None

    return state, events, all_pts

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return
    try:
        url  = 'https://api.telegram.org/bot' + TELEGRAM_BOT_TOKEN + '/sendMessage'
        data = {'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}
        r    = requests.post(url, json=data, timeout=30)
        if r.status_code != 200:
            print('Telegram error: ' + str(r.status_code))
    except Exception as e:
        print('Telegram error: ' + str(e))

def main():
    pair = DEBUG_PAIR
    now  = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    NL   = chr(10)

    print('=' * 70)
    print('  CANDLE DEBUG - ' + pair.replace('_', '/') + ' @ ' + now)
    print('=' * 70)

    header_msg = ('<b>Debug Report: ' + pair.replace('_', '/') + '</b>' + NL +
                  'Time: ' + now + NL + NL +
                  'Comparing OANDA data with TradingView' + NL +
                  'Using BODY prices only (no wicks) for structure')
    send_telegram(header_msg)

    for tf in TIMEFRAMES:
        count    = TF_COUNT[tf]
        tf_name  = TF_NAMES[tf]
        candles  = fetch_candles(pair, tf, count)

        if not candles:
            send_telegram('<b>' + tf_name + '</b>' + NL + 'No data fetched')
            continue

        highs, lows = swing_points(candles)
        state, events, _ = trend_with_debug(highs, lows, candles)

        # Build message
        msg = '<b>' + tf_name + ' (' + tf + ') → ' + state + '</b>' + NL
        msg += 'Fetched: ' + str(len(candles)) + ' candles' + NL + NL

        # Last 10 candles
        msg += '<b>Last 10 Candles (Body prices):</b>' + NL
        for c in candles[-10:]:
            bh = round(body_high(c), 5)
            bl = round(body_low(c), 5)
            arrow = 'G' if c['close'] >= c['open'] else 'R'
            msg += (c['time'][:16] + ' ' + arrow +
                    ' O:' + str(round(c['open'], 5)) +
                    ' H:' + str(round(c['high'], 5)) +
                    ' L:' + str(round(c['low'], 5)) +
                    ' C:' + str(round(c['close'], 5)) + NL)

        # Recent swing points (last 6)
        msg += NL + '<b>Recent Swing Highs (Body):</b>' + NL
        for h in highs[-6:]:
            msg += '  ' + h['time'] + ' @ ' + str(round(h['price'], 5)) + NL

        msg += NL + '<b>Recent Swing Lows (Body):</b>' + NL
        for l in lows[-6:]:
            msg += '  ' + l['time'] + ' @ ' + str(round(l['price'], 5)) + NL

        # Last 5 structure events (what caused the trend)
        msg += NL + '<b>Structure Events (last 5):</b>' + NL
        for e in events[-5:]:
            msg += '  ' + e + NL

        msg += NL + '<b>FINAL STATE: ' + state + '</b>'

        send_telegram(msg)
        print(NL + tf_name + ': ' + state)
        for e in events[-5:]:
            print('  ' + e)

    print(NL + 'Debug complete.')

if __name__ == '__main__':
    main()
