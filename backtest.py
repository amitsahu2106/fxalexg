# ═══════════════════════════════════════════════════════════════
# STRATEGY S4: 15-MIN OPENING RANGE BREAKOUT (ORB)
# Proven strategy with 55-60% WR at 1:1 RR
# 
# Rules:
# 1. Wait for first 15-min M15 candle of London session (08:00 UTC)
# 2. Mark the HIGH and LOW of that candle = "Opening Range"
# 3. When price closes ABOVE the range high → BUY entry
#    When price closes BELOW the range low → SELL entry
# 4. SL = Opposite side of opening range
# 5. TP = Same size as range (1:1 RR)
# 6. Only one trade per day, only first breakout
# 7. Expire at 16:00 UTC if neither side hits
# 
# 2 Years from Jan 2024
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

# London session opens at 08:00 UTC
SESSION_OPEN_HOUR = 8
EXPIRY_HOUR       = 16
MIN_RANGE_PIPS    = 8   # Skip dead days
MAX_RANGE_PIPS    = 60  # Skip news spikes

# ─── PIP HELPERS ─────────────────────────────────────────────
def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    return 0.0001

def to_pips(diff, pair):
    return round(diff / pip_size(pair), 1)

def from_pips(pips, pair):
    return pips * pip_size(pair)

# ─── FETCH DATA ──────────────────────────────────────────────
def fetch_chunk(pair, granularity, from_dt):
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
    now    = datetime.now(timezone.utc)
    start  = datetime(2024, 1, 1, tzinfo=timezone.utc)
    all_c  = []
    cursor = start
    while cursor < now:
        chunk = fetch_chunk(pair, granularity, cursor)
        if not chunk:
            break
        all_c.extend(chunk)
        last_time = chunk[-1]['time']
        last_dt   = datetime.strptime(last_time[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        next_cur  = last_dt + timedelta(seconds=1)
        if next_cur <= cursor:
            break
        cursor = next_cur
        if len(chunk) < 100:
            break
        time.sleep(0.5)
    seen, unique = set(), []
    for c in all_c:
        if c['time'] not in seen:
            seen.add(c['time'])
            unique.append(c)
    return sorted(unique, key=lambda x: x['time'])

# ─── BACKTEST ONE PAIR ──────────────────────────────────────
def backtest_pair(pair, m15_all):
    print(chr(10) + '  ' + pair.replace('_', '/') + '...')
    if len(m15_all) < 100:
        return []

    trades = []

    # Group M15 candles by date
    by_date = {}
    for c in m15_all:
        dt = datetime.strptime(c['time'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        date_key = dt.strftime('%Y-%m-%d')
        if date_key not in by_date:
            by_date[date_key] = []
        by_date[date_key].append((dt, c))

    for date_key in sorted(by_date.keys()):
        day_candles = by_date[date_key]
        if not day_candles:
            continue

        # Skip weekends
        first_dt = day_candles[0][0]
        if first_dt.weekday() >= 5:
            continue

        # Find the 08:00 UTC M15 candle (opening range)
        opening_range = None
        for dt, c in day_candles:
            if dt.hour == SESSION_OPEN_HOUR and dt.minute == 0:
                opening_range = (dt, c)
                break

        if not opening_range:
            continue

        or_dt, or_candle = opening_range
        range_high = or_candle['high']
        range_low  = or_candle['low']
        range_size = range_high - range_low
        range_pips = to_pips(range_size, pair)

        # Skip if range too small (dead market) or too big (news event)
        if range_pips < MIN_RANGE_PIPS or range_pips > MAX_RANGE_PIPS:
            continue

        # Look for breakout candle after 08:00 UTC
        # Entry on candle CLOSE only (not wick)
        entered    = False
        direction  = None
        entry      = None
        sl         = None
        tp         = None
        entry_time = None

        result     = 'NO_BREAKOUT'
        exit_time  = None
        exit_price = None

        for dt, c in day_candles:
            # Only look after opening range
            if dt <= or_dt:
                continue
            # Stop scanning after expiry
            if dt.hour >= EXPIRY_HOUR:
                break

            # Look for first breakout
            if not entered:
                if c['close'] > range_high:
                    entered    = True
                    direction  = 'BUY'
                    entry      = c['close']
                    sl         = range_low
                    tp         = entry + range_size  # 1:1 RR based on range
                    entry_time = c['time']
                elif c['close'] < range_low:
                    entered    = True
                    direction  = 'SELL'
                    entry      = c['close']
                    sl         = range_high
                    tp         = entry - range_size
                    entry_time = c['time']
                continue

            # Trade is active - check TP/SL on each subsequent candle
            if direction == 'BUY':
                if c['low'] <= sl:
                    result     = 'SL'
                    exit_time  = c['time']
                    exit_price = sl
                    break
                if c['high'] >= tp:
                    result     = 'TP'
                    exit_time  = c['time']
                    exit_price = tp
                    break
            else:
                if c['high'] >= sl:
                    result     = 'SL'
                    exit_time  = c['time']
                    exit_price = sl
                    break
                if c['low'] <= tp:
                    result     = 'TP'
                    exit_time  = c['time']
                    exit_price = tp
                    break

        # If trade entered but neither TP nor SL hit before expiry
        if entered and result == 'NO_BREAKOUT':
            # Close at last candle of day
            last_candle = day_candles[-1][1]
            result     = 'EXPIRED'
            exit_time  = last_candle['time']
            exit_price = last_candle['close']

        if not entered:
            continue

        # Calculate pips
        if direction == 'BUY':
            if result == 'TP':
                pips = to_pips(tp - entry, pair)
            elif result == 'SL':
                pips = -to_pips(entry - sl, pair)
            else:  # EXPIRED
                pips = to_pips(exit_price - entry, pair)
        else:
            if result == 'TP':
                pips = to_pips(entry - tp, pair)
            elif result == 'SL':
                pips = -to_pips(sl - entry, pair)
            else:
                pips = to_pips(entry - exit_price, pair)

        trades.append({
            'pair':       pair,
            'date':       date_key,
            'or_high':    round(range_high, 5),
            'or_low':     round(range_low, 5),
            'or_pips':    range_pips,
            'direction':  direction,
            'entry':      round(entry, 5),
            'sl':         round(sl, 5),
            'tp':         round(tp, 5),
            'entry_time': entry_time,
            'result':     result,
            'exit_time':  exit_time,
            'exit_price': round(exit_price, 5) if exit_price else None,
            'pips':       round(pips, 1),
        })

    return trades

# ─── SEND TELEGRAM ───────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
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
    SEP = '=' * 80
    print(NL + SEP)
    print('  STRATEGY S4: 15-Min Opening Range Breakout - 2 Years')
    print('  M15 candle 08:00 UTC = Opening Range')
    print('  Breakout entry on close | SL=other side | TP=range size (1:1)')
    print('  Expire 16:00 UTC')
    print(SEP)

    if not all_trades:
        send_telegram('<b>S4 ORB Backtest</b>' + NL + 'No trades.')
        return

    tps     = [t for t in all_trades if t['result'] == 'TP']
    sls     = [t for t in all_trades if t['result'] == 'SL']
    expired = [t for t in all_trades if t['result'] == 'EXPIRED']

    total   = len(all_trades)
    wr      = round(len(tps) / total * 100, 1) if total else 0
    tot_pip = round(sum(t['pips'] for t in all_trades), 1)
    avg_win = round(sum(t['pips'] for t in tps) / len(tps), 1) if tps else 0
    avg_sl  = round(sum(t['pips'] for t in sls) / len(sls), 1) if sls else 0
    gw      = sum(t['pips'] for t in tps)
    gl      = abs(sum(t['pips'] for t in sls)) or 1
    pf      = round(gw / gl, 2)

    max_cl = cl = 0
    for t in all_trades:
        if t['result'] == 'SL':
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0

    print(NL + 'OVERALL:')
    print('  Total trades:        ' + str(total))
    print('  TP hit (winner):     ' + str(len(tps)))
    print('  SL hit (loser):      ' + str(len(sls)))
    print('  Expired (no hit):    ' + str(len(expired)))
    print('  Win Rate:            ' + str(wr) + '%')
    print('  Avg Win (pips):      +' + str(avg_win))
    print('  Avg Loss (pips):     ' + str(avg_sl))
    print('  Total Pips:          ' + str(tot_pip))
    print('  Profit Factor:       ' + str(pf))
    print('  Max Consec Losses:   ' + str(max_cl))

    print(NL + 'BY PAIR:')
    print('  ' + 'Pair'.ljust(10) + 'Trades  Wins   WR%    Pips')
    for pair in sorted(set(t['pair'] for t in all_trades)):
        pt = [t for t in all_trades if t['pair'] == pair]
        pw = [t for t in pt if t['result'] == 'TP']
        wrp = round(len(pw) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        print('  ' + pair.replace('_', '/').ljust(10) +
              str(len(pt)).rjust(5) +
              str(len(pw)).rjust(6) +
              str(wrp).rjust(8) + '%' +
              str(ppp).rjust(8))

    print(NL + 'FULL TRADE LOG:')
    print('  Date       Pair     Dir   ORange   Entry     SL        TP        Result  Pips')
    for t in all_trades:
        print('  ' + t['date'] + ' ' +
              t['pair'].replace('_', '/').ljust(8) + ' ' +
              t['direction'].ljust(4) + ' ' +
              str(t['or_pips']).rjust(4) + 'p ' +
              str(t['entry']).ljust(8) + ' ' +
              str(t['sl']).ljust(8) + ' ' +
              str(t['tp']).ljust(8) + ' ' +
              t['result'].ljust(7) + ' ' +
              str(t['pips']) + 'p')

    print(NL + SEP)

    # Telegram
    tg = (
        '<b>S4: 15-Min ORB - 2 Years</b>' + NL +
        '08:00 UTC range | 1:1 RR | Expire 16:00' + NL + NL +
        '<b>OVERALL:</b>' + NL +
        'Trades: ' + str(total) + NL +
        'TP:  ' + str(len(tps)) + NL +
        'SL:  ' + str(len(sls)) + NL +
        'Exp: ' + str(len(expired)) + NL +
        'Win Rate: <b>' + str(wr) + '%</b>' + NL +
        'Avg Win:  +' + str(avg_win) + 'p' + NL +
        'Avg Loss: ' + str(avg_sl) + 'p' + NL +
        'Total P&L: ' + str(tot_pip) + 'p' + NL +
        'Profit Factor: ' + str(pf) + NL +
        'Max Consec SL: ' + str(max_cl) + NL + NL +
        '<b>BY PAIR:</b>' + NL
    )
    for pair in sorted(set(t['pair'] for t in all_trades)):
        pt = [t for t in all_trades if t['pair'] == pair]
        pw = [t for t in pt if t['result'] == 'TP']
        wrp = round(len(pw) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        tg += (pair.replace('_', '/').ljust(10) +
               ' T:' + str(len(pt)) + ' W:' + str(len(pw)) +
               ' WR:' + str(wrp) + '% P&L:' + str(ppp) + 'p' + NL)
    send_telegram(tg)

    chunk_size = 30
    for cs in range(0, len(all_trades), chunk_size):
        chunk = all_trades[cs:cs + chunk_size]
        m = ('<b>Trades ' + str(cs+1) + '-' +
             str(min(cs+chunk_size, len(all_trades))) +
             ' of ' + str(len(all_trades)) + '</b>' + NL + '-' * 50 + NL)
        for t in chunk:
            m += (t['date'] + ' ' +
                  t['pair'].replace('_', '/').ljust(8) + ' ' +
                  t['direction'].ljust(4) + ' ' +
                  'E:' + str(t['entry']) + ' ' +
                  'SL:' + str(t['sl']) + ' ' +
                  'TP:' + str(t['tp']) + ' ' +
                  t['result'].ljust(5) + ' ' + str(t['pips']) + 'p' + NL)
        send_telegram(m)

# ─── MAIN ────────────────────────────────────────────────────
def main():
    print('=' * 65)
    print('  STRATEGY S4: 15-Min Opening Range Breakout - 2 Years')
    print('  Documented WR 55-60% at 1:1 RR')
    print('=' * 65)

    all_trades = []
    for pair in PAIRS:
        try:
            print(chr(10) + 'Fetching ' + pair + '...')
            m15 = fetch_2years(pair, 'M15')
            print('  M15:' + str(len(m15)))
            if len(m15) < 100:
                continue
            trades = backtest_pair(pair, m15)
            all_trades.extend(trades)
            tps = [t for t in trades if t['result'] == 'TP']
            wr  = round(len(tps) / len(trades) * 100, 1) if trades else 0
            tot = round(sum(t['pips'] for t in trades), 1)
            print('  Trades:' + str(len(trades)) +
                  '  Wins:' + str(len(tps)) +
                  '  WR:' + str(wr) + '%' +
                  '  P&L:' + str(tot) + 'p')
        except Exception as e:
            print('  ERROR: ' + str(e))

    try:
        print_results(all_trades)
    except Exception as e:
        print('ERROR in print_results: ' + str(e))
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
