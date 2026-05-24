# ═══════════════════════════════════════════════════════════════
# STRATEGY S2: MEAN REVERSION - 65% WIN RATE TARGET
# Trades reversions at RSI extremes + Bollinger band touches
# TP1=0.75R | TP2=1.0R | TP3=1.5R (smaller targets = higher hit rate)
# Entry on candle CLOSE only. No look-ahead. 2 years from Jan 2024
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

def bollinger(values, period=20, num_std=2):
    # Returns (upper, middle, lower) Bollinger Bands
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
        change = values[i] - values[i-1]
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        change = values[i] - values[i-1]
        gain   = max(change, 0)
        loss   = max(-change, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

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

# ─── MEAN REVERSION SIGNALS ─────────────────────────────────
def is_oversold_reversal(h1_candles, h4_candles):
    # BUY signal at oversold extreme:
    # 1. H1 price touches/breaks lower Bollinger band
    # 2. H1 RSI < 30 (extreme oversold)
    # 3. H1 current candle is bullish (reversal candle)
    # 4. H4 NOT in strong bearish trend (avoid catching falling knife)
    if len(h1_candles) < 30 or len(h4_candles) < 30:
        return False

    h1_closes = [c['close'] for c in h1_candles]
    curr = h1_candles[-1]
    prev = h1_candles[-2]

    # Bollinger Band check - either current OR previous candle touched lower band
    upper, mid, lower = bollinger(h1_closes, 20, 2)
    if lower is None:
        return False
    touched_lower = (prev['low'] <= lower) or (curr['low'] <= lower)
    if not touched_lower:
        return False

    # RSI must be extreme oversold
    h1_rsi = rsi(h1_closes, 14)
    if h1_rsi is None or h1_rsi > 35:  # Must be very oversold
        return False

    # Current candle must be bullish reversal
    if curr['close'] <= curr['open']:
        return False
    # Strong body required (60%+ of range)
    rng = curr['high'] - curr['low']
    if rng <= 0:
        return False
    body = curr['close'] - curr['open']
    if body < rng * 0.5:
        return False

    # Close back inside bands (not just touching)
    if curr['close'] <= lower:
        return False

    # H4 trend filter - don't buy if H4 is in strong downtrend
    h4_closes = [c['close'] for c in h4_candles]
    h4_sma_50 = sma(h4_closes, 50)
    if h4_sma_50 and curr['close'] < h4_sma_50 * 0.985:  # >1.5% below H4 SMA = strong down
        return False

    return True

def is_overbought_reversal(h1_candles, h4_candles):
    # SELL signal at overbought extreme - mirror logic
    if len(h1_candles) < 30 or len(h4_candles) < 30:
        return False

    h1_closes = [c['close'] for c in h1_candles]
    curr = h1_candles[-1]
    prev = h1_candles[-2]

    upper, mid, lower = bollinger(h1_closes, 20, 2)
    if upper is None:
        return False
    touched_upper = (prev['high'] >= upper) or (curr['high'] >= upper)
    if not touched_upper:
        return False

    h1_rsi = rsi(h1_closes, 14)
    if h1_rsi is None or h1_rsi < 65:
        return False

    if curr['close'] >= curr['open']:
        return False
    rng = curr['high'] - curr['low']
    if rng <= 0:
        return False
    body = curr['open'] - curr['close']
    if body < rng * 0.5:
        return False

    if curr['close'] >= upper:
        return False

    h4_closes = [c['close'] for c in h4_candles]
    h4_sma_50 = sma(h4_closes, 50)
    if h4_sma_50 and curr['close'] > h4_sma_50 * 1.015:
        return False

    return True

# ─── SIMULATE TRADE ─────────────────────────────────────────
def simulate_trade(direction, entry, sl, tp1, tp2, tp3, future_candles):
    tp1_hit = tp2_hit = False
    tp1_time = tp2_time = None

    for c in future_candles:
        close = c['close']
        if direction == 'BUY':
            effective_sl = entry if tp1_hit else sl
            if close <= effective_sl:
                if tp1_hit and tp2_hit:
                    return 'TP2', c['time'], tp2, True, True
                if tp1_hit:
                    return 'TP1', tp1_time, tp1, True, False
                return 'SL', c['time'], close, False, False
            if not tp1_hit and close >= tp1:
                tp1_hit, tp1_time = True, c['time']
            if tp1_hit and not tp2_hit and close >= tp2:
                tp2_hit, tp2_time = True, c['time']
            if tp2_hit and close >= tp3:
                return 'TP3', c['time'], tp3, True, True
        else:
            effective_sl = entry if tp1_hit else sl
            if close >= effective_sl:
                if tp1_hit and tp2_hit:
                    return 'TP2', c['time'], tp2, True, True
                if tp1_hit:
                    return 'TP1', tp1_time, tp1, True, False
                return 'SL', c['time'], close, False, False
            if not tp1_hit and close <= tp1:
                tp1_hit, tp1_time = True, c['time']
            if tp1_hit and not tp2_hit and close <= tp2:
                tp2_hit, tp2_time = True, c['time']
            if tp2_hit and close <= tp3:
                return 'TP3', c['time'], tp3, True, True

    if tp2_hit:
        return 'TP2', tp2_time, tp2, True, True
    if tp1_hit:
        return 'TP1', tp1_time, tp1, True, False
    return 'OPEN', None, entry, False, False

# ─── BACKTEST ONE PAIR ──────────────────────────────────────
def backtest_pair(pair, h1_all, h4_all):
    print(chr(10) + '  ' + pair.replace('_', '/') + '...')
    if len(h1_all) < 200 or len(h4_all) < 50:
        return []

    trades = []
    exit_bar = -1
    last_signal_bar = -100

    for i in range(60, len(h1_all) - 5):
        if i <= exit_bar:
            continue
        # Cooldown - 8 hours minimum between signals
        if i - last_signal_bar < 8:
            continue

        candle_time = h1_all[i]['time']
        h1_window = h1_all[max(0, i-60):i+1]
        h4_window = [c for c in h4_all if c['time'] <= candle_time][-60:]

        if len(h4_window) < 50:
            continue

        direction = None
        if is_oversold_reversal(h1_window, h4_window):
            direction = 'BUY'
        elif is_overbought_reversal(h1_window, h4_window):
            direction = 'SELL'

        if not direction:
            continue

        # Calculate SL based on H1 ATR
        entry = h1_all[i]['close']
        h1_atr = atr(h1_window, 14) or from_pips(15, pair)

        if direction == 'BUY':
            # SL below recent low + 0.5 ATR buffer
            recent_low = min(c['low'] for c in h1_window[-10:])
            sl   = recent_low - h1_atr * 0.5
            risk = entry - sl
            if risk <= 0:
                continue
            if to_pips(risk, pair) < 8:
                continue
            # Tighter targets for higher win rate
            tp1 = entry + risk * 0.75   # 0.75R - easy first target
            tp2 = entry + risk * 1.0    # 1R
            tp3 = entry + risk * 1.5    # 1.5R
        else:
            recent_high = max(c['high'] for c in h1_window[-10:])
            sl   = recent_high + h1_atr * 0.5
            risk = sl - entry
            if risk <= 0:
                continue
            if to_pips(risk, pair) < 8:
                continue
            tp1 = entry - risk * 0.75
            tp2 = entry - risk * 1.0
            tp3 = entry - risk * 1.5

        result, ex_time, ex_price, _, _ = simulate_trade(
            direction, entry, sl, tp1, tp2, tp3, h1_all[i+1:i+200]
        )

        if direction == 'BUY':
            if result == 'TP3':
                pips = to_pips(tp3 - entry, pair)
            elif result == 'TP2':
                pips = to_pips(tp2 - entry, pair)
            elif result == 'TP1':
                pips = to_pips(tp1 - entry, pair)
            elif result == 'SL':
                pips = -to_pips(entry - sl, pair)
            else:
                pips = 0
        else:
            if result == 'TP3':
                pips = to_pips(entry - tp3, pair)
            elif result == 'TP2':
                pips = to_pips(entry - tp2, pair)
            elif result == 'TP1':
                pips = to_pips(entry - tp1, pair)
            elif result == 'SL':
                pips = -to_pips(sl - entry, pair)
            else:
                pips = 0

        trades.append({
            'pair':       pair,
            'entry_time': candle_time,
            'direction':  direction,
            'entry':      round(entry, 5),
            'sl':         round(sl, 5),
            'tp1':        round(tp1, 5),
            'tp2':        round(tp2, 5),
            'tp3':        round(tp3, 5),
            'result':     result,
            'exit_time':  ex_time,
            'exit_price': round(ex_price, 5) if ex_price else None,
            'pips':       pips,
            'pattern':    'BB+RSI MEAN REV',
        })

        last_signal_bar = i
        if result != 'OPEN':
            exit_bar = next(
                (j for j in range(i+1, min(i+200, len(h1_all)))
                 if h1_all[j]['time'] >= (ex_time or candle_time)),
                i + 1
            )

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
    print('  STRATEGY S2: MEAN REVERSION - 2 Years Backtest')
    print('  Bollinger Band + RSI extremes')
    print('  TP1=0.75R | TP2=1.0R | TP3=1.5R')
    print(SEP)

    if not all_trades:
        send_telegram('<b>S2 Mean Reversion Backtest</b>' + NL + 'No trades.')
        return

    closed = [t for t in all_trades if t['result'] != 'OPEN']
    if not closed:
        return

    tp3s  = [t for t in closed if t['result'] == 'TP3']
    tp2s  = [t for t in closed if t['result'] == 'TP2']
    tp1s  = [t for t in closed if t['result'] == 'TP1']
    sls   = [t for t in closed if t['result'] == 'SL']
    wins  = tp1s + tp2s + tp3s
    total = len(closed)

    wr      = round(len(wins) / total * 100, 1) if total else 0
    tot_pip = round(sum(t['pips'] for t in closed), 1)
    avg_win = round(sum(t['pips'] for t in wins) / len(wins), 1) if wins else 0
    avg_sl  = round(sum(t['pips'] for t in sls)  / len(sls),  1) if sls  else 0
    gw      = sum(t['pips'] for t in wins)
    gl      = abs(sum(t['pips'] for t in sls))
    pf      = round(gw / gl, 2) if gl > 0 else 0

    max_cl = cl = 0
    for t in closed:
        if t['result'] == 'SL':
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0

    print(NL + 'OVERALL:')
    print('  Signals: ' + str(len(all_trades)))
    print('  Closed:  ' + str(total))
    print('  TP3: ' + str(len(tp3s)) + ' | TP2: ' + str(len(tp2s)) +
          ' | TP1: ' + str(len(tp1s)) + ' | SL: ' + str(len(sls)))
    print('  Win Rate: ' + str(wr) + '%')
    print('  Avg Win:  +' + str(avg_win) + 'p')
    print('  Avg Loss: ' + str(avg_sl) + 'p')
    print('  Total P&L: ' + str(tot_pip) + 'p')
    print('  Profit Factor: ' + str(pf))
    print('  Max Consec SL: ' + str(max_cl))

    print(NL + 'BY PAIR:')
    for pair in sorted(set(t['pair'] for t in closed)):
        pt = [t for t in closed if t['pair'] == pair]
        pw = [t for t in pt if t['result'] != 'SL']
        wrp = round(len(pw) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        print('  ' + pair.replace('_', '/').ljust(10) +
              ' T:' + str(len(pt)).rjust(3) +
              ' W:' + str(len(pw)).rjust(3) +
              ' WR:' + str(wrp).rjust(6) + '%' +
              ' P&L:' + str(ppp).rjust(7) + 'p')

    print(NL + 'TRADES:')
    for t in all_trades:
        print('  ' + t['entry_time'][:10] + ' ' +
              t['pair'].replace('_', '/').ljust(8) + ' ' +
              t['direction'].ljust(5) +
              ' E:' + str(t['entry']) +
              ' SL:' + str(t['sl']) +
              ' TP1:' + str(t['tp1']) +
              ' TP2:' + str(t['tp2']) +
              ' TP3:' + str(t['tp3']) +
              ' ' + t['result'].ljust(6) + ' ' + str(t['pips']) + 'p')

    # Telegram
    tg = (
        '<b>S2 Mean Reversion - 2 Years</b>' + NL +
        'BB + RSI extremes | TP1=0.75R TP2=1R TP3=1.5R' + NL + NL +
        '<b>OVERALL:</b>' + NL +
        'Signals: ' + str(len(all_trades)) + NL +
        'Closed: ' + str(total) + NL +
        'TP3: ' + str(len(tp3s)) + NL +
        'TP2: ' + str(len(tp2s)) + NL +
        'TP1: ' + str(len(tp1s)) + NL +
        'SL:  ' + str(len(sls)) + NL +
        'Win Rate: <b>' + str(wr) + '%</b>' + NL +
        'Avg Win:  +' + str(avg_win) + 'p' + NL +
        'Avg Loss: ' + str(avg_sl) + 'p' + NL +
        'Total P&L: ' + str(tot_pip) + 'p' + NL +
        'Profit Factor: ' + str(pf) + NL +
        'Max Consec SL: ' + str(max_cl) + NL + NL +
        '<b>BY PAIR:</b>' + NL
    )
    for pair in sorted(set(t['pair'] for t in closed)):
        pt = [t for t in closed if t['pair'] == pair]
        pw = [t for t in pt if t['result'] != 'SL']
        wrp = round(len(pw) / len(pt) * 100, 1) if pt else 0
        ppp = round(sum(t['pips'] for t in pt), 1)
        tg += (pair.replace('_', '/').ljust(10) +
               ' T:' + str(len(pt)) + ' W:' + str(len(pw)) +
               ' WR:' + str(wrp) + '% P&L:' + str(ppp) + 'p' + NL)
    send_telegram(tg)

    chunk_size = 30
    for cs in range(0, len(all_trades), chunk_size):
        chunk = all_trades[cs:cs + chunk_size]
        m = ('<b>Trades ' + str(cs+1) + '-' + str(min(cs+chunk_size, len(all_trades))) +
             ' of ' + str(len(all_trades)) + '</b>' + NL + '-' * 60 + NL)
        for t in chunk:
            m += (t['entry_time'][:10] + ' ' +
                  t['pair'].replace('_', '/').ljust(8) + ' ' +
                  t['direction'].ljust(4) + ' E:' + str(t['entry']) +
                  ' SL:' + str(t['sl']) + ' TP1:' + str(t['tp1']) +
                  ' ' + t['result'].ljust(5) + ' ' + str(t['pips']) + 'p' + NL)
        send_telegram(m)

# ─── MAIN ────────────────────────────────────────────────────
def main():
    print('=' * 65)
    print('  STRATEGY S2: Mean Reversion - 2 Years')
    print('  Bollinger Band + RSI extremes (65% WR target)')
    print('=' * 65)

    all_trades = []
    for pair in PAIRS:
        try:
            print(chr(10) + 'Fetching ' + pair + '...')
            h1 = fetch_2years(pair, 'H1')
            h4 = fetch_2years(pair, 'H4')
            print('  H1:' + str(len(h1)) + ' H4:' + str(len(h4)))
            if len(h1) < 200:
                continue
            trades = backtest_pair(pair, h1, h4)
            all_trades.extend(trades)
            closed = [t for t in trades if t['result'] != 'OPEN']
            wins   = [t for t in closed if t['result'] != 'SL']
            wr     = round(len(wins) / len(closed) * 100, 1) if closed else 0
            print('  Signals:' + str(len(trades)) +
                  '  Closed:' + str(len(closed)) +
                  '  WR:' + str(wr) + '%')
        except Exception as e:
            print('  ERROR: ' + str(e))

    print_results(all_trades)

if __name__ == '__main__':
    main()
