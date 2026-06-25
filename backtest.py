# ===================================================================
# STRATEGY: Simplified iFVG Setup (Sanos' Inner Circle PDF)
# Adapted from NQ futures to FXAlexG forex/XAU pairs.
# See chat explanation for all definitions/assumptions made.
#
# Liquidity Sweep : M5 swing point (wick fractal) near a prior FVG,
#                   swept by a wick that closes back the other side.
# Delivery        : M1 inverse-FVG (iFVG) confirms direction.
# FVG Size        : >= 0.03% of price (scaled equivalent of "9 points")
# Targets         : nearest M5 swing beyond entry giving >=1.5R, else skip
# SL              : just beyond the swept wick
# No SMT / Premium-Discount bonus filter (optional in source PDF)
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

MIN_FVG_PCT   = 0.03    # minimum FVG size as % of price (scaled "9 points" rule)
MIN_RR        = 1.5     # minimum reward:risk to accept a target
SWEEP_LOOKBACK_M5   = 40   # how many M5 candles back to search for a validating FVG near a swing
ENTRY_SEARCH_WINDOW = 120  # how many M1 candles forward to search for iFVG confirmation after a sweep
TARGET_MAX_MULT     = 20   # cap target search distance at 20x risk (avoid unrealistic far targets)

# --- PIP HELPERS ---------------------------------------------------
def pip_size(pair):
    if 'JPY' in pair: return 0.01
    if 'XAU' in pair: return 0.10
    return 0.0001

def to_pips(diff, pair):
    return round(diff / pip_size(pair), 1)

# --- FETCH CANDLES (chunked over 6 months) --------------------------
def fetch_chunk(pair, granularity, from_dt):
    url     = OANDA_BASE_URL + '/v3/instruments/' + pair + '/candles'
    headers = {'Authorization': 'Bearer ' + OANDA_API_KEY}
    params  = {
        'granularity': granularity,
        'from':        from_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'count':       5000,
        'price':       'M',
    }
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

# --- FVG DETECTION ---------------------------------------------------
def detect_fvgs(candles):
    # Standard ICT 3-candle FVG. Returns list of dicts with formation index = i (3rd candle)
    fvgs = []
    for i in range(2, len(candles)):
        c1, c3 = candles[i-2], candles[i]
        if c1['high'] < c3['low']:
            fvgs.append({'type': 'bullish', 'top': c3['low'], 'bottom': c1['high'],
                         'formed_i': i, 'inverted_i': None, 'inverted_to': None})
        elif c1['low'] > c3['high']:
            fvgs.append({'type': 'bearish', 'top': c1['low'], 'bottom': c3['high'],
                         'formed_i': i, 'inverted_i': None, 'inverted_to': None})
    return fvgs

def mark_inversions(fvgs, candles):
    # A bearish FVG inverts to bullish when a candle CLOSES above its top.
    # A bullish FVG inverts to bearish when a candle CLOSES below its bottom.
    for fvg in fvgs:
        for j in range(fvg['formed_i'] + 1, len(candles)):
            c = candles[j]
            if fvg['type'] == 'bearish' and c['close'] > fvg['top']:
                fvg['inverted_i']  = j
                fvg['inverted_to'] = 'bullish'
                break
            if fvg['type'] == 'bullish' and c['close'] < fvg['bottom']:
                fvg['inverted_i']  = j
                fvg['inverted_to'] = 'bearish'
                break
    return fvgs

# --- SWING POINTS (wick-based, for sweep detection) ------------------
def swing_points_wick(candles, lookback=2):
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        window = candles[i-lookback:i+lookback+1]
        if candles[i]['high'] == max(c['high'] for c in window):
            highs.append({'i': i, 'price': candles[i]['high']})
        if candles[i]['low'] == min(c['low'] for c in window):
            lows.append({'i': i, 'price': candles[i]['low']})
    return highs, lows

# --- FIND FVG-VALIDATED SWINGS --------------------------------------
def swing_near_fvg(swing_i, swing_price, fvgs, fvg_type_needed, pair, lookback=SWEEP_LOOKBACK_M5):
    # Checks if an FVG of the needed type formed shortly before this swing, with the
    # swing price sitting inside/near that FVG zone (proxy for "swing bounced from a FVG")
    buffer = pip_size(pair) * 10
    for fvg in fvgs:
        if fvg['type'] != fvg_type_needed:
            continue
        if not (swing_i - lookback <= fvg['formed_i'] <= swing_i):
            continue
        if fvg['bottom'] - buffer <= swing_price <= fvg['top'] + buffer:
            return True
    return False

# --- TRADE SIMULATION (no expiry, wick-based fills) -----------------
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

# --- BACKTEST ONE PAIR ----------------------------------------------
def backtest_pair(pair, m1, m5):
    print('  Detecting M5 FVGs + swings...')
    fvgs_m5          = detect_fvgs(m5)
    highs_m5, lows_m5 = swing_points_wick(m5, lookback=2)

    print('  Detecting M1 FVGs + inversions...')
    fvgs_m1 = detect_fvgs(m1)
    fvgs_m1 = mark_inversions(fvgs_m1, m1)
    # Index M1 fvgs by inversion bar for fast lookup
    inv_by_bar = {}
    for fvg in fvgs_m1:
        if fvg['inverted_i'] is not None:
            inv_by_bar.setdefault(fvg['inverted_i'], []).append(fvg)

    # Build M1 time -> index map for aligning M5 sweep time to M1 bars
    m1_times = [time_to_dt(c['time']) for c in m1]

    def m1_index_at_or_after(target_dt):
        lo, hi = 0, len(m1_times) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if m1_times[mid] < target_dt:
                lo = mid + 1
            else:
                hi = mid
        return lo

    trades = []
    used_sweep_bars = set()

    # --- BULLISH sweeps: validated swing LOWS swept then bullish iFVG on M1 ---
    for low in lows_m5:
        si, sp = low['i'], low['price']
        if not swing_near_fvg(si, sp, fvgs_m5, 'bullish', pair):
            continue
        # Search forward on M5 for a sweep+reversal candle
        for j in range(si + 1, min(si + 60, len(m5))):
            c = m5[j]
            if c['low'] < sp and c['close'] > sp:
                # Sweep confirmed at M5 bar j
                if j in used_sweep_bars:
                    break
                used_sweep_bars.add(j)
                sweep_dt = time_to_dt(c['time'])
                m1_start = m1_index_at_or_after(sweep_dt)
                # Search M1 forward for a bullish inversion (iFVG) confirmation
                for k in range(m1_start, min(m1_start + ENTRY_SEARCH_WINDOW, len(m1))):
                    if k in inv_by_bar:
                        for fvg in inv_by_bar[k]:
                            if fvg['inverted_to'] != 'bullish':
                                continue
                            size_pct = (fvg['top'] - fvg['bottom']) / m1[k]['close'] * 100
                            if size_pct < MIN_FVG_PCT:
                                continue
                            entry = m1[k]['close']
                            sl    = sp - pip_size(pair) * 2   # buffer beyond swept low
                            risk  = entry - sl
                            if risk <= 0:
                                continue
                            # Find target: nearest M5 swing HIGH beyond entry giving >=MIN_RR
                            target = None
                            for h in highs_m5:
                                if h['i'] <= si:
                                    continue
                                if h['price'] <= entry:
                                    continue
                                rr = (h['price'] - entry) / risk
                                if rr >= MIN_RR and (h['price'] - entry) <= risk * TARGET_MAX_MULT:
                                    target = h['price']
                                    break
                            if target is None:
                                break  # no clear target -> skip trade entirely
                            future = m1[k+1:k+1+5000]
                            result, exit_px = simulate('BUY', entry, sl, target, future)
                            if result == 'OPEN':
                                break
                            pips = (to_pips(target - entry, pair) if result == 'TP'
                                    else -to_pips(entry - sl, pair))
                            trades.append({
                                'pair': pair, 'direction': 'BUY',
                                'time': m1[k]['time'][:16].replace('T', ' '),
                                'entry': round(entry, 5), 'sl': round(sl, 5),
                                'tp': round(target, 5), 'rr': round((target-entry)/risk, 2),
                                'fvg_pct': round(size_pct, 4),
                                'result': result, 'pips': round(pips, 1),
                            })
                            break
                        break
                break

    # --- BEARISH sweeps: validated swing HIGHS swept then bearish iFVG on M1 ---
    for high in highs_m5:
        si, sp = high['i'], high['price']
        if not swing_near_fvg(si, sp, fvgs_m5, 'bearish', pair):
            continue
        for j in range(si + 1, min(si + 60, len(m5))):
            c = m5[j]
            if c['high'] > sp and c['close'] < sp:
                if j in used_sweep_bars:
                    break
                used_sweep_bars.add(j)
                sweep_dt = time_to_dt(c['time'])
                m1_start = m1_index_at_or_after(sweep_dt)
                for k in range(m1_start, min(m1_start + ENTRY_SEARCH_WINDOW, len(m1))):
                    if k in inv_by_bar:
                        for fvg in inv_by_bar[k]:
                            if fvg['inverted_to'] != 'bearish':
                                continue
                            size_pct = (fvg['top'] - fvg['bottom']) / m1[k]['close'] * 100
                            if size_pct < MIN_FVG_PCT:
                                continue
                            entry = m1[k]['close']
                            sl    = sp + pip_size(pair) * 2
                            risk  = sl - entry
                            if risk <= 0:
                                continue
                            target = None
                            for l in lows_m5:
                                if l['i'] <= si:
                                    continue
                                if l['price'] >= entry:
                                    continue
                                rr = (entry - l['price']) / risk
                                if rr >= MIN_RR and (entry - l['price']) <= risk * TARGET_MAX_MULT:
                                    target = l['price']
                                    break
                            if target is None:
                                break
                            future = m1[k+1:k+1+5000]
                            result, exit_px = simulate('SELL', entry, sl, target, future)
                            if result == 'OPEN':
                                break
                            pips = (to_pips(entry - target, pair) if result == 'TP'
                                    else -to_pips(sl - entry, pair))
                            trades.append({
                                'pair': pair, 'direction': 'SELL',
                                'time': m1[k]['time'][:16].replace('T', ' '),
                                'entry': round(entry, 5), 'sl': round(sl, 5),
                                'tp': round(target, 5), 'rr': round((sl-entry)/risk*-1 if risk else 0, 2),
                                'fvg_pct': round(size_pct, 4),
                                'result': result, 'pips': round(pips, 1),
                            })
                            break
                        break
                break

    trades.sort(key=lambda t: t['time'])
    return trades

# --- TELEGRAM --------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = 'https://api.telegram.org/bot' + TELEGRAM_BOT_TOKEN + '/sendMessage'
        requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg,
                                 'parse_mode': 'HTML'}, timeout=30)
    except Exception as e:
        print('  Telegram error: ' + str(e))

# --- MAIN -------------------------------------------------------------
def main():
    NL  = chr(10)
    SEP = '=' * 70
    print(SEP)
    print('  SIMPLIFIED iFVG SETUP BACKTEST (6 months)')
    print('  Sweep: M5 | Delivery/Entry: M1 | Min FVG: ' + str(MIN_FVG_PCT) + '% | Min RR: ' + str(MIN_RR))
    print(SEP)

    all_trades = []
    per_pair   = {}

    for pair in PAIRS:
        print(NL + 'Fetching ' + pair + '...')
        m1 = fetch_period(pair, 'M1', days=183)
        m5 = fetch_period(pair, 'M5', days=183)
        print('  M1: ' + str(len(m1)) + '  M5: ' + str(len(m5)))
        if len(m1) < 500 or len(m5) < 200:
            print('  Not enough data, skipping')
            continue
        trades = backtest_pair(pair, m1, m5)
        all_trades.extend(trades)
        per_pair[pair] = trades
        tps = [t for t in trades if t['result'] == 'TP']
        wr  = round(len(tps) / len(trades) * 100, 1) if trades else 0
        pl  = round(sum(t['pips'] for t in trades), 1)
        print('  Trades: ' + str(len(trades)) + ' | WR: ' + str(wr) + '% | P&L: ' + str(pl) + 'p')

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

    tg  = '<b>Simplified iFVG Setup - 6mo Backtest</b>' + NL
    tg += 'Sweep M5 | Entry/Delivery M1 | MinFVG ' + str(MIN_FVG_PCT) + '% | MinRR ' + str(MIN_RR) + NL + NL
    tg += '<b>OVERALL:</b>' + NL
    tg += 'Trades: ' + str(total) + NL
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
