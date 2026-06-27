# ===================================================================
# STRATEGY: ORB Sniper-Style Breakout (independent reimplementation)
# Inspired by the publicly described concept of TradeX ORB Sniper v3.6.
# NOT the actual proprietary algorithm (closed-source, inaccessible).
# See chat for full list of assumptions made.
#
# Modes tested : 5-min ORB and 15-min ORB
# Sessions     : London open (07:00 UTC) and NY open (13:30 UTC)
# Entry        : candle CLOSES beyond the opening range high/low
# Wick filter  : breakout candle must close in outer 30% of its range
# SL           : opposite side of the opening range
# TP1 / TP2    : range height projected 1x / 2x from breakout point
# One trade per session per pair (first breakout only)
# ===================================================================
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

SESSIONS = {
    'LONDON': 7,    # 07:00 UTC
    'NY':     13,   # 13:30 UTC -> handled with minute offset below
}
SESSION_MINUTE = {'LONDON': 0, 'NY': 30}

RANGE_MODES   = [5, 15]   # minutes
WICK_FILTER   = True
WICK_MIN_BODY_POS = 0.30  # close must be in outer 30% of candle range

# --- PIP HELPERS ----------------------------------------------------
def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    return 0.0001

def to_pips(diff, pair):
    return round(diff / pip_size(pair), 1)

# --- FETCH M5 (chunked over 6 months) -------------------------------
def fetch_chunk(pair, from_dt):
    url     = OANDA_BASE_URL + '/v3/instruments/' + pair + '/candles'
    headers = {'Authorization': 'Bearer ' + OANDA_API_KEY}
    params  = {'granularity': 'M5', 'from': from_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
               'count': 5000, 'price': 'M'}
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code != 200:
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
    start  = now - timedelta(days=183)
    all_c  = []
    cursor = start
    reqs   = 0
    while cursor < now:
        chunk = fetch_chunk(pair, cursor)
        reqs += 1
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
        if reqs % 10 == 0:
            time.sleep(0.5)
    seen, unique = set(), []
    for c in all_c:
        if c['time'] not in seen:
            seen.add(c['time'])
            unique.append(c)
    return sorted(unique, key=lambda x: x['time'])

def time_to_dt(t):
    return datetime.strptime(t[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)

# --- BUILD OPENING RANGE for each session-day -----------------------
def find_sessions(candles, session_hour, session_minute, range_minutes):
    # Returns list of {range_start_i, range_end_i, high, low}
    sessions = []
    n_range_bars = range_minutes // 5   # how many M5 bars make up the range
    i = 0
    seen_days = set()
    while i < len(candles):
        dt = time_to_dt(candles[i]['time'])
        day_key = dt.strftime('%Y-%m-%d') + '_' + str(session_hour)
        if (dt.hour == session_hour and dt.minute == session_minute
                and day_key not in seen_days
                and dt.weekday() < 5):
            seen_days.add(day_key)
            range_end = i + n_range_bars
            if range_end < len(candles):
                window = candles[i:range_end]
                sessions.append({
                    'start_i': i,
                    'end_i':   range_end,
                    'high':    max(c['high'] for c in window),
                    'low':     min(c['low']  for c in window),
                })
        i += 1
    return sessions

# --- WICK REJECTION FILTER -------------------------------------------
def passes_wick_filter(candle, direction):
    rng = candle['high'] - candle['low']
    if rng <= 0:
        return True
    if direction == 'BUY':
        close_pos = (candle['close'] - candle['low']) / rng
        return close_pos >= (1 - WICK_MIN_BODY_POS)
    else:
        close_pos = (candle['close'] - candle['low']) / rng
        return close_pos <= WICK_MIN_BODY_POS

# --- SIMULATE TRADE (wick-based, no expiry within remaining session) -
def simulate(direction, entry, sl, tp, future):
    for c in future:
        if direction == 'BUY':
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

# --- BACKTEST ONE PAIR / SESSION / RANGE MODE ------------------------
def backtest_combo(pair, candles, session_name, session_hour, session_minute, range_minutes):
    sessions = find_sessions(candles, session_hour, session_minute, range_minutes)
    trades = []

    for sess in sessions:
        end_i = sess['end_i']
        high, low = sess['high'], sess['low']
        range_height = high - low
        if range_height <= 0:
            continue

        traded_this_session = False
        # Search forward up to end of trading day (next ~16h = 192 M5 bars) for first breakout
        for j in range(end_i, min(end_i + 192, len(candles))):
            if traded_this_session:
                break
            c = candles[j]

            direction = None
            if c['close'] > high:
                direction = 'BUY'
            elif c['close'] < low:
                direction = 'SELL'
            if not direction:
                continue
            if WICK_FILTER and not passes_wick_filter(c, direction):
                continue

            entry = c['close']
            if direction == 'BUY':
                sl  = low
                tp1 = entry + range_height
                tp2 = entry + range_height * 2
            else:
                sl  = high
                tp1 = entry - range_height
                tp2 = entry - range_height * 2

            risk = abs(entry - sl)
            if risk <= 0:
                continue

            future = candles[j+1:j+1+5000]
            result, exit_px = simulate(direction, entry, sl, tp1, future)
            if result == 'OPEN':
                break  # ran out of data, skip (rare, end of dataset)

            if direction == 'BUY':
                pips = (to_pips(tp1 - entry, pair) if result == 'TP'
                        else -to_pips(entry - sl, pair))
            else:
                pips = (to_pips(entry - tp1, pair) if result == 'TP'
                        else -to_pips(sl - entry, pair))

            trades.append({
                'pair': pair, 'session': session_name, 'mode': str(range_minutes) + 'm',
                'time': c['time'][:16].replace('T', ' '),
                'direction': direction,
                'entry': round(entry, 5), 'sl': round(sl, 5), 'tp1': round(tp1, 5),
                'range_pips': to_pips(range_height, pair),
                'result': result, 'pips': round(pips, 1),
            })
            traded_this_session = True

    return trades

# --- TELEGRAM ----------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = 'https://api.telegram.org/bot' + TELEGRAM_BOT_TOKEN + '/sendMessage'
        requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg,
                                 'parse_mode': 'HTML'}, timeout=30)
    except Exception as e:
        print('  Telegram error: ' + str(e))

# --- MAIN ---------------------------------------------------------------
def main():
    NL  = chr(10)
    SEP = '=' * 70
    print(SEP)
    print('  ORB SNIPER-STYLE BREAKOUT BACKTEST (independent reimplementation)')
    print('  Modes: 5m / 15m ORB | Sessions: London 07:00 UTC, NY 13:30 UTC')
    print('  Wick filter: ' + str(WICK_FILTER) + ' | TP1 = 1x range | TP2 = 2x range')
    print(SEP)

    all_trades = []
    summary    = {}  # key: (session, mode) -> list of trades

    for pair in PAIRS:
        print(NL + 'Fetching ' + pair + ' M5 (6 months)...')
        candles = fetch_6months(pair)
        print('  Candles: ' + str(len(candles)))
        if len(candles) < 500:
            print('  Not enough data, skipping')
            continue

        for session_name, s_hour in SESSIONS.items():
            s_min = SESSION_MINUTE[session_name]
            for r_min in RANGE_MODES:
                trades = backtest_combo(pair, candles, session_name, s_hour, s_min, r_min)
                all_trades.extend(trades)
                key = (session_name, str(r_min) + 'm')
                summary.setdefault(key, []).extend(trades)

    # --- Overall ---
    total = len(all_trades)
    tps   = [t for t in all_trades if t['result'] == 'TP']
    sls   = [t for t in all_trades if t['result'] == 'SL']
    wr    = round(len(tps) / total * 100, 1) if total else 0
    pl    = round(sum(t['pips'] for t in all_trades), 1)
    gw    = sum(t['pips'] for t in tps)
    gl    = abs(sum(t['pips'] for t in sls)) or 1
    pf    = round(gw / gl, 2)

    print(NL + SEP)
    print('  OVERALL (all sessions, all modes, all pairs)')
    print(SEP)
    print('  Total trades: ' + str(total))
    print('  Win Rate:     ' + str(wr) + '%')
    print('  Total P&L:    ' + str(pl) + ' pips')
    print('  Profit Factor:' + str(pf))

    print(NL + '  BY SESSION + MODE:')
    for (session_name, mode), trades in summary.items():
        if not trades:
            continue
        w  = [t for t in trades if t['result'] == 'TP']
        wp = round(len(w) / len(trades) * 100, 1)
        pp = round(sum(t['pips'] for t in trades), 1)
        print('  ' + session_name.ljust(8) + mode.ljust(5) +
              ' T:' + str(len(trades)).rjust(4) +
              ' WR:' + str(wp).rjust(6) + '%' +
              ' P&L:' + str(pp).rjust(9) + 'p')

    print(NL + '  BY PAIR:')
    for pair in PAIRS:
        t = [x for x in all_trades if x['pair'] == pair]
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

    # --- Telegram ---
    tg  = '<b>ORB Sniper-Style Backtest (6mo)</b>' + NL
    tg += '5m/15m ORB | London+NY open | Wick filter ON' + NL + NL
    tg += '<b>OVERALL:</b>' + NL
    tg += 'Trades: ' + str(total) + NL
    tg += 'Win Rate: <b>' + str(wr) + '%</b>' + NL
    tg += 'Total P&L: ' + str(pl) + ' pips' + NL
    tg += 'Profit Factor: ' + str(pf) + NL + NL
    tg += '<b>BY SESSION+MODE:</b>' + NL
    for (session_name, mode), trades in summary.items():
        if not trades:
            continue
        w  = [t for t in trades if t['result'] == 'TP']
        wp = round(len(w) / len(trades) * 100, 1)
        pp = round(sum(t['pips'] for t in trades), 1)
        tg += (session_name + ' ' + mode + ': T:' + str(len(trades)) +
               ' WR:' + str(wp) + '% P&L:' + str(pp) + 'p' + NL)
    send_telegram(tg)
    print(NL + 'Done.')

if __name__ == '__main__':
    main()
