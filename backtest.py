# ═══════════════════════════════════════════════════════════════
# STRATEGY: Marubozu Continuation Pullback (M1)
#
# BULLISH:
#   - Green candle followed by a GREEN MARUBOZU (zero wick)
#     marubozu: high == close AND low == open (perfect body)
#   - Entry: price pulls back DOWN to the marubozu's OPEN (body bottom)
#   - SL: low of the candle BEFORE the marubozu
#   - TP: 1:2 RR (entry + 2 x risk)
#
# BEARISH (mirror):
#   - Red candle followed by a RED MARUBOZU (zero wick)
#     marubozu: high == open AND low == close
#   - Entry: price pulls back UP to the marubozu's OPEN (body top)
#   - SL: high of the candle BEFORE the marubozu
#   - TP: 1:2 RR (entry - 2 x risk)
#
# Entry valid until SL or TP hit (no expiry)
# Timeframe: M5 | Period: last 6 months | All 8 FXAlexG FX pairs
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

RR = 2.0  # 1:2 risk-reward

# ─── PIP HELPERS ─────────────────────────────────────────────
def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    return 0.0001

def to_pips(diff, pair):
    return round(diff / pip_size(pair), 1)

# ─── FETCH M1 DATA (chunked over 6 months) ──────────────────
def fetch_chunk(pair, from_dt):
    url     = OANDA_BASE_URL + '/v3/instruments/' + pair + '/candles'
    headers = {'Authorization': 'Bearer ' + OANDA_API_KEY}
    params  = {
        'granularity': 'M5',
        'from':        from_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'count':       5000,
        'price':       'M',
    }
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code != 200:
                print('    OANDA ' + str(r.status_code))
                time.sleep(2)
                continue
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
            time.sleep(3)
    return []

def fetch_6months(pair):
    now    = datetime.now(timezone.utc)
    start  = now - timedelta(days=183)   # ~6 months
    all_c  = []
    cursor = start
    requests_made = 0
    while cursor < now:
        chunk = fetch_chunk(pair, cursor)
        requests_made += 1
        if not chunk:
            break
        all_c.extend(chunk)
        last_dt = datetime.strptime(chunk[-1]['time'][:19],
                                    '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        next_cur = last_dt + timedelta(seconds=1)
        if next_cur <= cursor:
            break
        cursor = next_cur
        if len(chunk) < 100:
            break
        # gentle pacing
        if requests_made % 10 == 0:
            time.sleep(0.5)
    # dedupe
    seen, unique = set(), []
    for c in all_c:
        if c['time'] not in seen:
            seen.add(c['time'])
            unique.append(c)
    return sorted(unique, key=lambda x: x['time'])

# ─── MARUBOZU DETECTION (zero wick) ─────────────────────────
def is_green_marubozu(c):
    # Green body with no wicks: high == close, low == open, close > open
    return (c['close'] > c['open'] and
            c['high'] == c['close'] and
            c['low']  == c['open'])

def is_red_marubozu(c):
    # Red body with no wicks: high == open, low == close, close < open
    return (c['close'] < c['open'] and
            c['high'] == c['open'] and
            c['low']  == c['close'])

def is_green(c):
    return c['close'] > c['open']

def is_red(c):
    return c['close'] < c['open']

# ─── SIMULATE TRADE (no expiry, wick-based fills) ───────────
def simulate(direction, entry, sl, tp, future):
    for c in future:
        if direction == 'BUY':
            # SL and TP can both be touched in same candle - assume SL first (conservative)
            if c['low'] <= sl:
                return 'SL', sl
            if c['high'] >= tp:
                return 'TP', tp
        else:
            if c['high'] >= sl:
                return 'SL', sl
            if c['low'] <= tp:
                return 'TP', tp
    return 'OPEN', future[-1]['close'] if future else entry

# ─── BACKTEST ONE PAIR ──────────────────────────────────────
def backtest_pair(pair, candles):
    trades   = []
    n        = len(candles)
    # i = index of the marubozu candle; need i-1 (prev) and i-2? No:
    #   prev-candle = candle before marubozu (i-1) -> must be same colour
    #   SL candle   = candle before the marubozu = i-1
    i = 2
    while i < n - 1:
        prev_c = candles[i-1]   # candle before marubozu (colour confirm + SL source)
        maru_c = candles[i]     # the marubozu candle

        direction = None

        # ── BULLISH: green candle then green marubozu ──
        if is_green(prev_c) and is_green_marubozu(maru_c):
            entry = maru_c['open']            # body bottom of marubozu
            sl    = prev_c['low']             # low of candle before marubozu
            if entry > sl:
                risk = entry - sl
                tp   = entry + risk * RR
                direction = 'BUY'

        # ── BEARISH: red candle then red marubozu ──
        elif is_red(prev_c) and is_red_marubozu(maru_c):
            entry = maru_c['open']            # body top of marubozu
            sl    = prev_c['high']            # high of candle before marubozu
            if entry < sl:
                risk = sl - entry
                tp   = entry - risk * RR
                direction = 'SELL'

        if direction:
            risk_pips = to_pips(abs(entry - sl), pair)
            # skip absurdly tiny setups (< 0.3 pip risk) to avoid noise
            if risk_pips >= 0.3:
                # entry trigger: price must pull back to touch entry AFTER marubozu
                # search forward for the pullback candle, then simulate from there
                entered_idx = None
                for j in range(i+1, n):
                    c = candles[j]
                    if direction == 'BUY' and c['low'] <= entry:
                        entered_idx = j
                        break
                    if direction == 'SELL' and c['high'] >= entry:
                        entered_idx = j
                        break

                if entered_idx is not None:
                    future = candles[entered_idx:]
                    result, exit_px = simulate(direction, entry, sl, tp, future)
                    if direction == 'BUY':
                        pips = (to_pips(tp - entry, pair) if result == 'TP'
                                else -to_pips(entry - sl, pair) if result == 'SL'
                                else to_pips(exit_px - entry, pair))
                    else:
                        pips = (to_pips(entry - tp, pair) if result == 'TP'
                                else -to_pips(sl - entry, pair) if result == 'SL'
                                else to_pips(entry - exit_px, pair))
                    if result in ('TP', 'SL'):
                        trades.append({
                            'pair': pair, 'direction': direction,
                            'time': maru_c['time'][:16].replace('T', ' '),
                            'entry': round(entry, 5), 'sl': round(sl, 5),
                            'tp': round(tp, 5), 'risk_pips': risk_pips,
                            'result': result, 'pips': round(pips, 1),
                        })
        i += 1
    return trades

# ─── TELEGRAM ────────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = 'https://api.telegram.org/bot' + TELEGRAM_BOT_TOKEN + '/sendMessage'
        requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg,
                                 'parse_mode': 'HTML'}, timeout=30)
    except Exception as e:
        print('  Telegram error: ' + str(e))

# ─── MAIN ────────────────────────────────────────────────────
def main():
    NL  = chr(10)
    SEP = '=' * 70
    print(SEP)
    print('  MARUBOZU CONTINUATION PULLBACK BACKTEST (M5, 6 months, RR 1:2)')
    print(SEP)

    all_trades = []
    per_pair   = {}

    for pair in PAIRS:
        print(NL + 'Fetching ' + pair + ' M5 (6 months)...')
        candles = fetch_6months(pair)
        print('  Candles: ' + str(len(candles)))
        if len(candles) < 100:
            print('  Not enough data, skipping')
            continue
        trades = backtest_pair(pair, candles)
        all_trades.extend(trades)
        per_pair[pair] = trades
        tps = [t for t in trades if t['result'] == 'TP']
        wr  = round(len(tps) / len(trades) * 100, 1) if trades else 0
        pl  = round(sum(t['pips'] for t in trades), 1)
        print('  Trades: ' + str(len(trades)) + ' | WR: ' + str(wr) +
              '% | P&L: ' + str(pl) + 'p')

    # ── Overall ──
    total = len(all_trades)
    tps   = [t for t in all_trades if t['result'] == 'TP']
    sls   = [t for t in all_trades if t['result'] == 'SL']
    wr    = round(len(tps) / total * 100, 1) if total else 0
    pl    = round(sum(t['pips'] for t in all_trades), 1)
    gw    = sum(t['pips'] for t in tps)
    gl    = abs(sum(t['pips'] for t in sls)) or 1
    pf    = round(gw / gl, 2)

    print(NL + SEP)
    print('  OVERALL RESULTS')
    print(SEP)
    print('  Total trades: ' + str(total))
    print('  Wins (TP):    ' + str(len(tps)))
    print('  Losses (SL):  ' + str(len(sls)))
    print('  Win Rate:     ' + str(wr) + '%')
    print('  Total P&L:    ' + str(pl) + ' pips')
    print('  Profit Factor:' + str(pf))

    print(NL + '  BY PAIR:')
    for pair in PAIRS:
        t = per_pair.get(pair, [])
        if not t:
            print('  ' + pair.replace('_', '/').ljust(9) + ' no trades')
            continue
        w  = [x for x in t if x['result'] == 'TP']
        wp = round(len(w) / len(t) * 100, 1)
        pp = round(sum(x['pips'] for x in t), 1)
        print('  ' + pair.replace('_', '/').ljust(9) +
              ' T:' + str(len(t)).rjust(4) +
              ' W:' + str(len(w)).rjust(4) +
              ' WR:' + str(wp).rjust(6) + '%' +
              ' P&L:' + str(pp).rjust(9) + 'p')

    # ── Telegram summary ──
    tg  = '<b>Marubozu Continuation Pullback - M1 Backtest</b>' + NL
    tg += 'M5 | Last 6 months | RR 1:2 | Zero-wick marubozu' + NL + NL
    tg += '<b>OVERALL:</b>' + NL
    tg += 'Trades: ' + str(total) + NL
    tg += 'Wins (TP): ' + str(len(tps)) + NL
    tg += 'Losses (SL): ' + str(len(sls)) + NL
    tg += 'Win Rate: <b>' + str(wr) + '%</b>' + NL
    tg += 'Total P&L: ' + str(pl) + ' pips' + NL
    tg += 'Profit Factor: ' + str(pf) + NL + NL
    tg += '<b>BY PAIR:</b>' + NL
    for pair in PAIRS:
        t = per_pair.get(pair, [])
        if not t:
            tg += pair.replace('_', '/') + ': no trades' + NL
            continue
        w  = [x for x in t if x['result'] == 'TP']
        wp = round(len(w) / len(t) * 100, 1)
        pp = round(sum(x['pips'] for x in t), 1)
        tg += (pair.replace('_', '/') + ': T:' + str(len(t)) +
               ' WR:' + str(wp) + '% P&L:' + str(pp) + 'p' + NL)
    send_telegram(tg)
    print(NL + 'Done.')

if __name__ == '__main__':
    main()
