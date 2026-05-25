# ═══════════════════════════════════════════════════════════════
# STRATEGY S5: M15 VWAP MEAN REVERSION - 3 RR VARIANTS
# High win rate strategy using VWAP + Bollinger Bands + RSI
# Tests 3 RR settings simultaneously to find optimal balance
#
# Logic:
# 1. Price touches/breaks outer Bollinger Band (2.5 std)
# 2. RSI at extreme (< 25 oversold / > 75 overbought)
# 3. M15 candle closes back INSIDE the bands (rejection)
# 4. Entry at close of that rejection candle
# 5. SL = candle extreme + buffer
# 6. TP at 3 different levels: 0.5R, 0.75R, 1.0R
# 7. Only trade during London + NY sessions (08:00-20:00 UTC)
# 8. Max 2 trades per session per pair
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

# Three RR variants tested simultaneously
RR_VARIANTS = [0.5, 0.75, 1.0]

# Session filter
SESSION_START = 8   # 08:00 UTC (London open)
SESSION_END   = 20  # 20:00 UTC

# ─── PIP HELPERS ─────────────────────────────────────────────
def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    return 0.0001

def to_pips(diff, pair):
    return round(diff / pip_size(pair), 1)

def from_pips(pips, pair):
    return pips * pip_size(pair)

# ─── INDICATORS ──────────────────────────────────────────────
def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def stddev(values, period):
    if len(values) < period:
        return None
    avg = sma(values, period)
    if avg is None:
        return None
    var = sum((v - avg) ** 2 for v in values[-period:]) / period
    return var ** 0.5

def bollinger(values, period=20, num_std=2.5):
    mid = sma(values, period)
    sd  = stddev(values, period)
    if mid is None or sd is None:
        return None, None, None
    return mid + num_std * sd, mid, mid - num_std * sd

def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains = losses = 0
    for i in range(1, period + 1):
        ch = values[i] - values[i-1]
        if ch > 0: gains += ch
        else: losses -= ch
    ag = gains / period
    al = losses / period
    for i in range(period + 1, len(values)):
        ch   = values[i] - values[i-1]
        g, l = max(ch, 0), max(-ch, 0)
        ag   = (ag * (period - 1) + g) / period
        al   = (al * (period - 1) + l) / period
    if al == 0:
        return 100
    return 100 - (100 / (1 + ag / al))

def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        tr = max(
            candles[i]['high'] - candles[i]['low'],
            abs(candles[i]['high'] - candles[i-1]['close']),
            abs(candles[i]['low']  - candles[i-1]['close'])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period

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
        last_dt  = datetime.strptime(chunk[-1]['time'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        next_cur = last_dt + timedelta(seconds=1)
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

# ─── SIMULATE TRADE ──────────────────────────────────────────
def simulate_trade(direction, entry, sl, tp, future_candles):
    risk = abs(entry - sl)
    for c in future_candles:
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
    # Expired
    last = future_candles[-1]['close'] if future_candles else entry
    return 'EXPIRED', last

# ─── BACKTEST ONE PAIR ──────────────────────────────────────
def backtest_pair(pair, m15_all):
    print(chr(10) + '  ' + pair.replace('_', '/') + '...')
    if len(m15_all) < 100:
        return {rr: [] for rr in RR_VARIANTS}

    # Results dict: one list per RR variant
    results = {rr: [] for rr in RR_VARIANTS}

    WARMUP = 40  # Need 40 candles for indicators

    for i in range(WARMUP, len(m15_all) - 5):
        curr_time = datetime.strptime(
            m15_all[i]['time'][:19], '%Y-%m-%dT%H:%M:%S'
        ).replace(tzinfo=timezone.utc)

        # Session filter: only trade 08:00-20:00 UTC weekdays
        if curr_time.weekday() >= 5:
            continue
        if curr_time.hour < SESSION_START or curr_time.hour >= SESSION_END:
            continue

        window   = m15_all[max(0, i-39):i+1]
        curr     = m15_all[i]
        prev     = m15_all[i-1]
        closes   = [c['close'] for c in window]

        # Bollinger Bands (2.5 std for stronger signal)
        upper, mid, lower = bollinger(closes, 20, 2.5)
        if upper is None:
            continue

        # RSI
        h1_rsi = rsi(closes, 14)
        if h1_rsi is None:
            continue

        # ATR for SL sizing
        curr_atr = atr(window, 14)
        if not curr_atr:
            continue

        direction = None

        # ── BUY SIGNAL ───────────────────────────────────────
        # Previous candle pierced lower band (extreme oversold)
        # Current candle closes BACK INSIDE (rejection)
        if (prev['low'] < lower and          # Prev pierced lower band
                curr['close'] > lower and    # Curr rejected back inside
                curr['close'] > curr['open'] and  # Curr is bullish
                h1_rsi < 25):               # RSI extreme oversold
            direction = 'BUY'

        # ── SELL SIGNAL ──────────────────────────────────────
        elif (prev['high'] > upper and
                curr['close'] < upper and
                curr['close'] < curr['open'] and
                h1_rsi > 75):
            direction = 'SELL'

        if not direction:
            continue

        # ── ENTRY ────────────────────────────────────────────
        entry = curr['close']

        if direction == 'BUY':
            # SL below the rejection candle low + 0.3 ATR buffer
            sl   = curr['low'] - curr_atr * 0.3
            risk = entry - sl
        else:
            sl   = curr['high'] + curr_atr * 0.3
            risk = sl - entry

        if risk <= 0 or to_pips(risk, pair) < 5:
            continue

        # Max SL of 30 pips (avoids oversized risk)
        if to_pips(risk, pair) > 30:
            continue

        # Find expiry bar (end of session)
        expiry_idx = None
        for j in range(i+1, min(i+50, len(m15_all))):
            t = datetime.strptime(
                m15_all[j]['time'][:19], '%Y-%m-%dT%H:%M:%S'
            ).replace(tzinfo=timezone.utc)
            if t.hour >= SESSION_END:
                expiry_idx = j
                break
        future = m15_all[i+1:expiry_idx] if expiry_idx else m15_all[i+1:i+33]

        if not future:
            continue

        # ── SIMULATE EACH RR VARIANT ─────────────────────────
        for rr in RR_VARIANTS:
            if direction == 'BUY':
                tp = entry + risk * rr
            else:
                tp = entry - risk * rr

            res, exit_px = simulate_trade(direction, entry, sl, tp, future)

            if direction == 'BUY':
                pips = to_pips(exit_px - entry, pair) if res == 'TP' else \
                       -to_pips(entry - exit_px, pair) if res == 'SL' else \
                       to_pips(exit_px - entry, pair)
            else:
                pips = to_pips(entry - exit_px, pair) if res == 'TP' else \
                       -to_pips(exit_px - entry, pair) if res == 'SL' else \
                       to_pips(entry - exit_px, pair)

            results[rr].append({
                'pair':       pair,
                'date':       curr_time.strftime('%Y-%m-%d'),
                'entry_time': curr['time'],
                'direction':  direction,
                'entry':      round(entry, 5),
                'sl':         round(sl, 5),
                'tp':         round(tp, 5),
                'rr':         rr,
                'result':     res,
                'exit_price': round(exit_px, 5),
                'pips':       round(pips, 1),
            })

    return results

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
def print_variant(rr, trades, NL):
    label   = str(rr) + ':1'
    tps     = [t for t in trades if t['result'] == 'TP']
    sls     = [t for t in trades if t['result'] == 'SL']
    expired = [t for t in trades if t['result'] == 'EXPIRED']
    total   = len(trades)
    if total == 0:
        print('  No trades for RR ' + label)
        return None

    wr      = round(len(tps) / total * 100, 1)
    tot_pip = round(sum(t['pips'] for t in trades), 1)
    avg_win = round(sum(t['pips'] for t in tps) / len(tps), 1) if tps else 0
    avg_sl  = round(sum(t['pips'] for t in sls) / len(sls), 1) if sls else 0
    gw      = sum(t['pips'] for t in tps)
    gl      = abs(sum(t['pips'] for t in sls)) or 1
    pf      = round(gw / gl, 2)
    expectancy = round((len(tps)/total * abs(avg_win)) - (len(sls)/total * abs(avg_sl)), 2) if total else 0

    max_cl = cl = 0
    for t in trades:
        if t['result'] == 'SL':
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0

    print('  RR ' + label + ':')
    print('    Trades: ' + str(total) +
          '  TP: ' + str(len(tps)) +
          '  SL: ' + str(len(sls)) +
          '  Exp: ' + str(len(expired)))
    print('    Win Rate:     ' + str(wr) + '%')
    print('    Avg Win:      +' + str(avg_win) + 'p')
    print('    Avg Loss:     ' + str(avg_sl) + 'p')
    print('    Total Pips:   ' + str(tot_pip) + 'p')
    print('    Profit Factor:' + str(pf))
    print('    Expectancy:   ' + str(expectancy) + 'p/trade')
    print('    Max Consec SL:' + str(max_cl))
    return {
        'label': label, 'total': total, 'tps': len(tps), 'sls': len(sls),
        'wr': wr, 'avg_win': avg_win, 'avg_sl': avg_sl,
        'tot_pip': tot_pip, 'pf': pf, 'expectancy': expectancy,
        'max_cl': max_cl
    }

def print_results(all_results):
    NL  = chr(10)
    SEP = '=' * 70
    print(NL + SEP)
    print('  STRATEGY S5: VWAP MEAN REVERSION - 3 RR VARIANTS')
    print('  Bollinger Band 2.5 std + RSI extreme + rejection candle')
    print('  London + NY session only (08:00-20:00 UTC)')
    print(SEP)

    summary_stats = {}

    for rr in RR_VARIANTS:
        trades = all_results[rr]
        print(NL + '─' * 70)
        stats = print_variant(rr, trades, NL)
        if stats:
            summary_stats[rr] = stats

        # By pair breakdown
        if trades:
            print('    By Pair:')
            for pair in sorted(set(t['pair'] for t in trades)):
                pt  = [t for t in trades if t['pair'] == pair]
                pw  = [t for t in pt if t['result'] == 'TP']
                wrp = round(len(pw) / len(pt) * 100, 1) if pt else 0
                ppp = round(sum(t['pips'] for t in pt), 1)
                print('      ' + pair.replace('_', '/').ljust(10) +
                      ' T:' + str(len(pt)).rjust(3) +
                      ' W:' + str(len(pw)).rjust(3) +
                      ' WR:' + str(wrp).rjust(6) + '%' +
                      ' P&L:' + str(ppp).rjust(8) + 'p')

    # ── Best variant recommendation ──────────────────────────
    print(NL + SEP)
    print('  COMPARISON + RECOMMENDATION:')
    print(NL + '  ' + 'RR'.ljust(8) + 'Trades'.ljust(8) + 'WR%'.ljust(8) +
          'Pips'.ljust(10) + 'PF'.ljust(8) + 'Expect'.ljust(10))
    print('  ' + '-' * 55)
    best_expect = -999
    best_rr     = None
    for rr, s in summary_stats.items():
        print('  ' + (str(rr)+':1').ljust(8) +
              str(s['total']).ljust(8) +
              str(s['wr']).ljust(8) +
              str(s['tot_pip']).ljust(10) +
              str(s['pf']).ljust(8) +
              str(s['expectancy']) + 'p')
        if s['expectancy'] > best_expect:
            best_expect = s['expectancy']
            best_rr     = rr

    if best_rr:
        print(NL + '  WINNER: ' + str(best_rr) + ':1 RR with ' +
              str(summary_stats[best_rr]['wr']) + '% WR and +' +
              str(summary_stats[best_rr]['expectancy']) + 'p expectancy per trade')

    print(NL + SEP)

    # ── Trade log for all variants ────────────────────────────
    for rr in RR_VARIANTS:
        trades = all_results[rr]
        if not trades:
            continue
        print(NL + 'TRADE LOG RR ' + str(rr) + ':1:')
        print('  Date       Pair     Dir   Entry    SL       TP       Result Pips')
        for t in trades:
            print('  ' + t['date'] + ' ' +
                  t['pair'].replace('_', '/').ljust(8) + ' ' +
                  t['direction'].ljust(5) +
                  str(t['entry']).ljust(8) + ' ' +
                  str(t['sl']).ljust(8) + ' ' +
                  str(t['tp']).ljust(8) + ' ' +
                  t['result'].ljust(6) + ' ' +
                  str(t['pips']) + 'p')

    # ── Telegram ─────────────────────────────────────────────
    tg = '<b>S5: VWAP Mean Reversion - 3 RR Variants</b>' + NL
    tg += 'BB 2.5std + RSI extreme + Rejection | 08-20 UTC' + NL + NL
    tg += '<b>COMPARISON:</b>' + NL

    for rr, s in summary_stats.items():
        tg += (NL + '<b>RR ' + str(rr) + ':1</b>' + NL +
               'Trades: ' + str(s['total']) + NL +
               'Win Rate: ' + str(s['wr']) + '%' + NL +
               'Total Pips: ' + str(s['tot_pip']) + 'p' + NL +
               'Profit Factor: ' + str(s['pf']) + NL +
               'Expectancy: +' + str(s['expectancy']) + 'p/trade' + NL +
               'Max Consec SL: ' + str(s['max_cl']) + NL)

    if best_rr:
        tg += (NL + 'BEST: ' + str(best_rr) + ':1 RR' + NL +
               'WR: ' + str(summary_stats[best_rr]['wr']) + '%' + NL +
               'Expectancy: +' + str(summary_stats[best_rr]['expectancy']) + 'p/trade')
    send_telegram(tg)

    # Trade chunks per variant
    for rr in RR_VARIANTS:
        trades = all_results[rr]
        if not trades:
            continue
        chunk_size = 30
        for cs in range(0, len(trades), chunk_size):
            chunk = trades[cs:cs + chunk_size]
            m = ('<b>RR ' + str(rr) + ':1 Trades ' +
                 str(cs+1) + '-' + str(min(cs+chunk_size, len(trades))) +
                 ' of ' + str(len(trades)) + '</b>' + NL + '-' * 50 + NL)
            for t in chunk:
                m += (t['date'] + ' ' +
                      t['pair'].replace('_', '/').ljust(8) + ' ' +
                      t['direction'].ljust(4) + ' ' +
                      str(t['pips']) + 'p ' + t['result'] + NL)
            send_telegram(m)

# ─── MAIN ────────────────────────────────────────────────────
def main():
    print('=' * 70)
    print('  S5: M15 VWAP MEAN REVERSION - TESTING 3 RR VARIANTS')
    print('  RR: 0.5:1 | 0.75:1 | 1.0:1')
    print('  Signal: BB 2.5std touch + RSI extreme + rejection candle')
    print('=' * 70)

    # Combined results across all pairs
    all_results = {rr: [] for rr in RR_VARIANTS}

    for pair in PAIRS:
        try:
            print(chr(10) + 'Fetching ' + pair + '...')
            m15 = fetch_2years(pair, 'M15')
            print('  M15:' + str(len(m15)))
            if len(m15) < 100:
                continue
            pair_results = backtest_pair(pair, m15)
            for rr in RR_VARIANTS:
                all_results[rr].extend(pair_results[rr])
                trades = pair_results[rr]
                wins   = [t for t in trades if t['result'] == 'TP']
                wr     = round(len(wins) / len(trades) * 100, 1) if trades else 0
                print('  RR ' + str(rr) + ':1 → ' + str(len(trades)) +
                      ' trades | WR: ' + str(wr) + '%')
        except Exception as e:
            print('  ERROR: ' + str(e))

    try:
        print_results(all_results)
    except Exception as e:
        print('ERROR in print_results: ' + str(e))
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
