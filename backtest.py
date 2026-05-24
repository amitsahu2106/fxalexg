# ═══════════════════════════════════════════════════════════════
# STRATEGY S3: 50 PIPS A DAY
# At 07:00 UTC H1 candle close, place two orders simultaneously:
#   BUY:  TP = close + 50 pips | SL = close - 10 pips
#   SELL: TP = close - 50 pips | SL = close + 10 pips
# Whichever side moves 50 pips first = WIN on that leg
# The other leg gets stopped for -10 pips
# If price moves against BOTH before hitting 50 pips = both SL
# Trade expires at 20:00 UTC same day (London/NY session end)
# Results: Win (TP50 - SL10 = +40 net) | Loss (both SL = -20)
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

# ─── SIMULATE BOTH ORDERS FOR ONE DAY ───────────────────────
def simulate_day(entry, buy_tp, buy_sl, sell_tp, sell_sl, future_candles, expire_time, pair):
    # Walk candle by candle
    # Track status of both legs simultaneously
    # First leg to hit TP cancels the opposite leg's TP
    # (but the other leg's SL may still get hit - OCO means SAME direction only)
    # Actually: both orders are live independently
    # If BUY hits TP50 first = BUY wins (+50p), SELL still live but cancel it now
    # If SELL hits TP50 first = SELL wins (+50p), BUY still live but cancel it now
    # Both can hit SL if price is choppy (ranging market)

    buy_active  = True
    sell_active = True
    buy_result  = None
    sell_result = None
    buy_exit_price  = None
    sell_exit_price = None

    for c in future_candles:
        # Stop scanning after expiry time
        c_time = datetime.strptime(c['time'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        if c_time >= expire_time:
            break

        high  = c['high']
        low   = c['low']
        close = c['close']

        # Check BUY leg
        if buy_active:
            if low <= buy_sl:
                buy_result     = 'SL'
                buy_exit_price = buy_sl
                buy_active     = False
            elif high >= buy_tp:
                buy_result     = 'TP'
                buy_exit_price = buy_tp
                buy_active     = False
                sell_active    = False  # Cancel other leg on first TP hit

        # Check SELL leg
        if sell_active:
            if high >= sell_sl:
                sell_result     = 'SL'
                sell_exit_price = sell_sl
                sell_active     = False
            elif low <= sell_tp:
                sell_result     = 'TP'
                sell_exit_price = sell_tp
                sell_active     = False
                buy_active      = False  # Cancel other leg

    # Expire any still-active legs at close
    last_close = future_candles[-1]['close'] if future_candles else entry
    if buy_active:
        buy_result     = 'EXPIRED'
        buy_exit_price = last_close
        buy_pips_exp   = to_pips(last_close - entry, pair)

    if sell_active:
        sell_result     = 'EXPIRED'
        sell_exit_price = last_close
        sell_pips_exp   = to_pips(entry - last_close, pair)

    # ── Calculate net pips for the day ──────────────────────
    buy_pips  = 0
    sell_pips = 0

    if buy_result == 'TP':
        buy_pips = to_pips(buy_tp - entry, pair)
    elif buy_result == 'SL':
        buy_pips = -to_pips(entry - buy_sl, pair)
    elif buy_result == 'EXPIRED':
        buy_pips = to_pips((buy_exit_price or entry) - entry, pair)

    if sell_result == 'TP':
        sell_pips = to_pips(entry - sell_tp, pair)
    elif sell_result == 'SL':
        sell_pips = -to_pips(sell_sl - entry, pair)
    elif sell_result == 'EXPIRED':
        sell_pips = to_pips(entry - (sell_exit_price or entry), pair)

    net_pips = round(buy_pips + sell_pips, 1)

    # Determine overall day result
    if buy_result == 'TP' or sell_result == 'TP':
        if buy_result == 'SL' or sell_result == 'SL':
            day_result = 'WIN_SL'   # One TP + one SL = net +40p normally
        else:
            day_result = 'WIN'      # TP hit, other cancelled (best case)
    elif buy_result == 'EXPIRED' or sell_result == 'EXPIRED':
        day_result = 'EXPIRED'
    else:
        day_result = 'DOUBLE_SL'    # Both stopped out = worst case

    return day_result, net_pips, buy_result, sell_result, buy_pips, sell_pips

# ─── BACKTEST ONE PAIR ──────────────────────────────────────
def backtest_pair(pair, h1_all):
    print(chr(10) + '  ' + pair.replace('_', '/') + '...')
    if len(h1_all) < 50:
        return []

    trades = []

    for i, candle in enumerate(h1_all):
        # Find the 07:00 UTC candle
        c_time = datetime.strptime(candle['time'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)

        # Only trade weekdays at exactly 07:00 UTC
        if c_time.hour != 7:
            continue
        if c_time.weekday() >= 5:  # Skip weekends
            continue

        entry    = candle['close']
        tp_pips  = 50
        sl_pips  = 10

        buy_tp  = entry + from_pips(tp_pips, pair)
        buy_sl  = entry - from_pips(sl_pips, pair)
        sell_tp = entry - from_pips(tp_pips, pair)
        sell_sl = entry + from_pips(sl_pips, pair)

        # Expiry: 20:00 UTC same day (13 hours of trading window)
        expire_time = c_time.replace(hour=20, minute=0, second=0)

        # Gather H1 candles from 08:00 to 20:00 that day
        future_candles = []
        for j in range(i + 1, min(i + 14, len(h1_all))):
            fc_time = datetime.strptime(h1_all[j]['time'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
            if fc_time > expire_time:
                break
            future_candles.append(h1_all[j])

        if not future_candles:
            continue

        day_result, net_pips, buy_res, sell_res, buy_pips, sell_pips = simulate_day(
            entry, buy_tp, buy_sl, sell_tp, sell_sl,
            future_candles, expire_time, pair
        )

        trades.append({
            'pair':       pair,
            'date':       c_time.strftime('%Y-%m-%d'),
            'entry_time': candle['time'],
            'entry':      round(entry, 5),
            'buy_tp':     round(buy_tp, 5),
            'buy_sl':     round(buy_sl, 5),
            'sell_tp':    round(sell_tp, 5),
            'sell_sl':    round(sell_sl, 5),
            'buy_result': buy_res,
            'sell_result':sell_res,
            'buy_pips':   buy_pips,
            'sell_pips':  sell_pips,
            'result':     day_result,
            'pips':       net_pips,
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
    print('  STRATEGY S3: 50 PIPS A DAY - 2 Years Backtest')
    print('  Entry: 07:00 UTC H1 close | Expiry: 20:00 UTC')
    print('  BUY:  TP=+50 pips | SL=-10 pips')
    print('  SELL: TP=-50 pips | SL=+10 pips')
    print(SEP)

    if not all_trades:
        send_telegram('<b>S3 50 Pips/Day Backtest</b>' + NL + 'No trades.')
        return

    wins       = [t for t in all_trades if t['result'] in ('WIN', 'WIN_SL')]
    double_sls = [t for t in all_trades if t['result'] == 'DOUBLE_SL']
    expired    = [t for t in all_trades if t['result'] == 'EXPIRED']
    clean_wins = [t for t in all_trades if t['result'] == 'WIN']
    win_sl     = [t for t in all_trades if t['result'] == 'WIN_SL']

    total    = len(all_trades)
    wr       = round(len(wins) / total * 100, 1) if total else 0
    tot_pip  = round(sum(t['pips'] for t in all_trades), 1)
    avg_win  = round(sum(t['pips'] for t in wins) / len(wins), 1) if wins else 0
    avg_dsl  = round(sum(t['pips'] for t in double_sls) / len(double_sls), 1) if double_sls else 0
    gw       = max(sum(t['pips'] for t in wins), 0)
    gl_raw   = sum(t['pips'] for t in double_sls)
    gl       = abs(gl_raw) if gl_raw < 0 else 1
    pf       = round(gw / gl, 2) if gl > 0 else 0

    max_cl = cl = 0
    for t in all_trades:
        if t['result'] == 'DOUBLE_SL':
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0

    print(NL + 'OVERALL:')
    print('  Total trading days:     ' + str(total))
    print('  WIN (TP + cancel):      ' + str(len(clean_wins)))
    print('  WIN_SL (TP + one SL):   ' + str(len(win_sl)))
    print('  DOUBLE SL (both stop):  ' + str(len(double_sls)))
    print('  EXPIRED (no hit):       ' + str(len(expired)))
    print('  Win Rate:               ' + str(wr) + '%')
    print('  Avg Win day (pips):     +' + str(avg_win))
    print('  Avg Double SL (pips):   ' + str(avg_dsl))
    print('  Total Pips P&L:         ' + str(tot_pip))
    print('  Profit Factor:          ' + str(pf))
    print('  Max Consec Double SL:   ' + str(max_cl))

    print(NL + 'BY PAIR:')
    print('  ' + 'Pair'.ljust(10) +
          'Days'.rjust(5) +
          'Wins'.rjust(5) +
          'WR%'.rjust(8) +
          'DSL'.rjust(5) +
          'Exp'.rjust(5) +
          'Pips'.rjust(8))
    print('  ' + '-' * 50)
    for pair in sorted(set(t['pair'] for t in all_trades)):
        pt  = [t for t in all_trades if t['pair'] == pair]
        pw  = [t for t in pt if t['result'] in ('WIN', 'WIN_SL')]
        pdsl = [t for t in pt if t['result'] == 'DOUBLE_SL']
        pexp = [t for t in pt if t['result'] == 'EXPIRED']
        wrp = round(len(pw) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        print('  ' + pair.replace('_', '/').ljust(10) +
              str(len(pt)).rjust(5) +
              str(len(pw)).rjust(5) +
              str(wrp).rjust(8) + '%' +
              str(len(pdsl)).rjust(5) +
              str(len(pexp)).rjust(5) +
              str(ppp).rjust(8))

    print(NL + 'FULL TRADE LOG:')
    print('  ' + '-' * 90)
    print('  Date        Pair      Entry    BuyTP    BuySL    SellTP   SellSL   BuyRes  SellRes  Net')
    print('  ' + '-' * 90)
    for t in all_trades:
        print('  ' + t['date'] + '  ' +
              t['pair'].replace('_', '/').ljust(8) + '  ' +
              str(t['entry']).ljust(8) + ' ' +
              str(t['buy_tp']).ljust(8) + ' ' +
              str(t['buy_sl']).ljust(8) + ' ' +
              str(t['sell_tp']).ljust(8) + ' ' +
              str(t['sell_sl']).ljust(8) + ' ' +
              t['buy_result'].ljust(7) + ' ' +
              t['sell_result'].ljust(7) + ' ' +
              str(t['pips']) + 'p')

    print(NL + SEP)

    # Telegram summary
    tg = (
        '<b>S3: 50 Pips A Day - 2 Years</b>' + NL +
        '07:00 UTC entry | Expire 20:00 UTC' + NL +
        'BUY +50p/-10p | SELL -50p/+10p' + NL + NL +
        '<b>OVERALL:</b>' + NL +
        'Trading Days: ' + str(total) + NL +
        'WIN (TP+cancel): ' + str(len(clean_wins)) + NL +
        'WIN_SL (TP+SL):  ' + str(len(win_sl)) + NL +
        'DOUBLE SL:       ' + str(len(double_sls)) + NL +
        'EXPIRED:         ' + str(len(expired)) + NL +
        'Win Rate: <b>' + str(wr) + '%</b>' + NL +
        'Avg Win:  +' + str(avg_win) + 'p' + NL +
        'Avg Dbl SL: ' + str(avg_dsl) + 'p' + NL +
        'Total P&L: ' + str(tot_pip) + 'p' + NL +
        'Profit Factor: ' + str(pf) + NL +
        'Max Consec DSL: ' + str(max_cl) + NL + NL +
        '<b>BY PAIR:</b>' + NL
    )
    for pair in sorted(set(t['pair'] for t in all_trades)):
        pt  = [t for t in all_trades if t['pair'] == pair]
        pw  = [t for t in pt if t['result'] in ('WIN', 'WIN_SL')]
        wrp = round(len(pw) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        tg += (pair.replace('_', '/').ljust(10) +
               ' D:' + str(len(pt)) + ' W:' + str(len(pw)) +
               ' WR:' + str(wrp) + '% P&L:' + str(ppp) + 'p' + NL)
    send_telegram(tg)

    # Trade chunks
    chunk_size = 30
    for cs in range(0, len(all_trades), chunk_size):
        chunk = all_trades[cs:cs + chunk_size]
        m = ('<b>Trades ' + str(cs+1) + '-' +
             str(min(cs+chunk_size, len(all_trades))) +
             ' of ' + str(len(all_trades)) + '</b>' + NL + '-' * 50 + NL)
        for t in chunk:
            m += (t['date'] + ' ' +
                  t['pair'].replace('_', '/').ljust(8) + ' ' +
                  'E:' + str(t['entry']) + ' ' +
                  'B:' + t['buy_result'].ljust(3) + '(' + str(t['buy_pips']) + 'p) ' +
                  'S:' + t['sell_result'].ljust(3) + '(' + str(t['sell_pips']) + 'p) ' +
                  'Net:' + str(t['pips']) + 'p' + NL)
        send_telegram(m)

# ─── MAIN ────────────────────────────────────────────────────
def main():
    print('=' * 65)
    print('  STRATEGY S3: 50 Pips A Day - 2 Years')
    print('  07:00 UTC entry - TP 50 pips - SL 10 pips each side')
    print('=' * 65)

    all_trades = []
    for pair in PAIRS:
        try:
            print(chr(10) + 'Fetching ' + pair + '...')
            h1 = fetch_2years(pair, 'H1')
            print('  H1:' + str(len(h1)))
            if len(h1) < 50:
                continue
            trades = backtest_pair(pair, h1)
            all_trades.extend(trades)
            wins = [t for t in trades if t['result'] in ('WIN', 'WIN_SL')]
            wr   = round(len(wins) / len(trades) * 100, 1) if trades else 0
            tot  = round(sum(t['pips'] for t in trades), 1)
            print('  Days:' + str(len(trades)) +
                  '  Wins:' + str(len(wins)) +
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
