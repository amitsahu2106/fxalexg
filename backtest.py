# ===================================================================
# STRATEGY: 5:30 AM IST (00:00 UTC) H1 Candle Range Breakout+Retrace
# XAU/USD only
#
# Range candle : the H1 candle starting 00:00 UTC (= 5:30 AM IST)
# Long setup   : a later candle CLOSES above the range candle's HIGH
#                -> wait for retrace down to 75% level of the range
#                -> entry there, SL at 25% level, TP = 1:2 RR
# Short setup  : a later candle CLOSES below the range candle's LOW
#                -> wait for retrace up to 25% level of the range
#                -> entry there, SL at 75% level, TP = 1:2 RR
#
# ASSUMPTIONS (flagging clearly):
#  - Breakout confirmation and entry/retrace timing evaluated on M15
#    candles (close-based breakout, wick-based retrace fill)
#  - One trade per day maximum (first valid breakout+retrace only)
#  - Entry stays valid until SL or TP is hit - no expiry
#  - "75% of the candle" = low + 0.75*(high-low); "25%" = low + 0.25*(high-low)
# ===================================================================
import requests, os, time
from datetime import datetime, timezone, timedelta

OANDA_API_KEY      = os.environ.get('OANDA_API_KEY', '')
OANDA_BASE_URL     = 'https://api-fxpractice.oanda.com'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')

PAIR = 'XAU_USD'
RANGE_CANDLE_UTC_HOUR = 0   # 00:00 UTC = 5:30 AM IST

# --- FETCH (chunked) --------------------------------------------------
def fetch_chunk(pair, granularity, from_dt):
    url     = OANDA_BASE_URL + '/v3/instruments/' + pair + '/candles'
    headers = {'Authorization': 'Bearer ' + OANDA_API_KEY}
    params  = {'granularity': granularity, 'from': from_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
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

def fetch_period(pair, granularity, days=183):
    now    = datetime.now(timezone.utc)
    start  = now - timedelta(days=days)
    all_c  = []
    cursor = start
    reqs   = 0
    while cursor < now:
        chunk = fetch_chunk(pair, granularity, cursor)
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

# --- FIND EACH DAY'S 00:00 UTC RANGE CANDLE (from H1 data) -----------
def find_range_candles(h1):
    ranges = []
    for c in h1:
        dt = time_to_dt(c['time'])
        if dt.hour == RANGE_CANDLE_UTC_HOUR and dt.weekday() < 5:
            rng = c['high'] - c['low']
            if rng <= 0:
                continue
            ranges.append({
                'date':  dt.strftime('%Y-%m-%d'),
                'dt':    dt,
                'high':  c['high'],
                'low':   c['low'],
                'range': rng,
            })
    return ranges

# --- SIMULATE (wick-based, no expiry) ---------------------------------
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
    return 'OPEN', None

# --- BACKTEST -----------------------------------------------------------
def backtest(h1, m15):
    ranges  = find_range_candles(h1)
    m15_times = [time_to_dt(c['time']) for c in m15]

    def m15_index_at_or_after(target_dt):
        lo, hi = 0, len(m15_times) - 1
        if lo > hi:
            return None
        while lo < hi:
            mid = (lo + hi) // 2
            if m15_times[mid] < target_dt:
                lo = mid + 1
            else:
                hi = mid
        return lo if m15_times[lo] >= target_dt else None

    trades = []
    for rng in ranges:
        high, low, r = rng['high'], rng['low'], rng['range']
        buy_entry,  buy_sl  = low + 0.75 * r, low + 0.25 * r
        sell_entry, sell_sl = low + 0.25 * r, low + 0.75 * r

        # search window: from 1h after range candle to end of that trading day (~20h)
        search_start = rng['dt'] + timedelta(hours=1)
        start_idx = m15_index_at_or_after(search_start)
        if start_idx is None:
            continue
        end_idx = min(start_idx + 80, len(m15))   # ~20h of M15 bars

        traded = False
        for j in range(start_idx, end_idx):
            if traded:
                break
            c = m15[j]
            if c['close'] > high:
                # look forward for retrace to buy_entry
                for k in range(j + 1, min(j + 200, len(m15))):
                    ck = m15[k]
                    if ck['low'] <= buy_entry:
                        risk = buy_entry - buy_sl
                        if risk <= 0:
                            break
                        tp = buy_entry + risk * 2
                        future = m15[k+1:k+1+2000]
                        result, exit_px = simulate('BUY', buy_entry, buy_sl, tp, future)
                        if result == 'OPEN':
                            break
                        rr = 2.0 if result == 'TP' else -1.0
                        trades.append({
                            'date': rng['date'], 'direction': 'BUY',
                            'entry': round(buy_entry, 2), 'sl': round(buy_sl, 2),
                            'tp': round(tp, 2), 'result': result, 'rr': rr,
                        })
                        traded = True
                        break
                break
            elif c['close'] < low:
                for k in range(j + 1, min(j + 200, len(m15))):
                    ck = m15[k]
                    if ck['high'] >= sell_entry:
                        risk = sell_sl - sell_entry
                        if risk <= 0:
                            break
                        tp = sell_entry - risk * 2
                        future = m15[k+1:k+1+2000]
                        result, exit_px = simulate('SELL', sell_entry, sell_sl, tp, future)
                        if result == 'OPEN':
                            break
                        rr = 2.0 if result == 'TP' else -1.0
                        trades.append({
                            'date': rng['date'], 'direction': 'SELL',
                            'entry': round(sell_entry, 2), 'sl': round(sell_sl, 2),
                            'tp': round(tp, 2), 'result': result, 'rr': rr,
                        })
                        traded = True
                        break
                break
    return trades

# --- TELEGRAM ------------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = 'https://api.telegram.org/bot' + TELEGRAM_BOT_TOKEN + '/sendMessage'
        requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg,
                                 'parse_mode': 'HTML'}, timeout=30)
    except Exception as e:
        print('  Telegram error: ' + str(e))

# --- MAIN ------------------------------------------------------------------
def main():
    NL  = chr(10)
    SEP = '=' * 70
    print(SEP)
    print('  5:30 AM IST RANGE BREAKOUT+RETRACE BACKTEST - XAU/USD')
    print('  Range candle: 00:00 UTC H1 | Entry: 75%/25% retrace | RR 1:2')
    print(SEP)

    print(NL + 'Fetching XAU_USD H1 (6 months)...')
    h1 = fetch_period(PAIR, 'H1', days=183)
    print('  H1 candles: ' + str(len(h1)))

    print('Fetching XAU_USD M15 (6 months)...')
    m15 = fetch_period(PAIR, 'M15', days=183)
    print('  M15 candles: ' + str(len(m15)))

    if len(h1) < 100 or len(m15) < 500:
        print('Not enough data.')
        return

    trades = backtest(h1, m15)
    total  = len(trades)
    wins   = [t for t in trades if t['result'] == 'TP']
    losses = [t for t in trades if t['result'] == 'SL']
    wr     = round(len(wins) / total * 100, 1) if total else 0
    net_r  = round(sum(t['rr'] for t in trades), 2)

    print(NL + SEP)
    print('  OVERALL (6 months)')
    print(SEP)
    print('  Total trades: ' + str(total))
    print('  Wins: ' + str(len(wins)) + '  Losses: ' + str(len(losses)))
    print('  Win Rate: ' + str(wr) + '%')
    print('  Net R: ' + str(net_r) + 'R')

    # --- Monthly breakdown ---
    monthly = {}
    for t in trades:
        mkey = t['date'][:7]   # YYYY-MM
        monthly.setdefault(mkey, []).append(t)

    print(NL + '  MONTHLY BREAKDOWN:')
    print('  ' + 'Month'.ljust(9) + 'Trades'.ljust(8) + 'Wins'.ljust(6) +
          'Losses'.ljust(8) + 'WR%'.ljust(8) + 'Net R')
    print('  ' + '-' * 55)
    for mkey in sorted(monthly.keys()):
        mt = monthly[mkey]
        mw = [t for t in mt if t['result'] == 'TP']
        ml = [t for t in mt if t['result'] == 'SL']
        mwr = round(len(mw) / len(mt) * 100, 1) if mt else 0
        mr  = round(sum(t['rr'] for t in mt), 2)
        print('  ' + mkey.ljust(9) + str(len(mt)).ljust(8) + str(len(mw)).ljust(6) +
              str(len(ml)).ljust(8) + (str(mwr) + '%').ljust(8) + str(mr) + 'R')

    print(NL + '  TRADE LOG:')
    for t in trades:
        print('  ' + t['date'] + ' ' + t['direction'].ljust(4) +
              ' E:' + str(t['entry']) + ' SL:' + str(t['sl']) + ' TP:' + str(t['tp']) +
              ' ' + t['result'].ljust(4) + ' ' + str(t['rr']) + 'R')

    # --- Telegram ---
    tg  = '<b>5:30 AM IST Range Breakout - XAU/USD Backtest</b>' + NL
    tg += '6 months | RR 1:2 | 75%/25% retrace entries' + NL + NL
    tg += '<b>OVERALL:</b>' + NL
    tg += 'Trades: ' + str(total) + NL
    tg += 'Win Rate: <b>' + str(wr) + '%</b>' + NL
    tg += 'Net R: <b>' + str(net_r) + 'R</b>' + NL + NL
    tg += '<b>MONTHLY:</b>' + NL
    for mkey in sorted(monthly.keys()):
        mt = monthly[mkey]
        mw = [t for t in mt if t['result'] == 'TP']
        mwr = round(len(mw) / len(mt) * 100, 1) if mt else 0
        mr  = round(sum(t['rr'] for t in mt), 2)
        tg += (mkey + ': T:' + str(len(mt)) + ' WR:' + str(mwr) + '% Net:' + str(mr) + 'R' + NL)
    send_telegram(tg)
    print(NL + 'Done.')

if __name__ == '__main__':
    main()
