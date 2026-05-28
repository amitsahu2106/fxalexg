# ═══════════════════════════════════════════════════════════════
# TEST: Find working free economic calendar source
# Run this via GitHub Actions to test in real environment
# ═══════════════════════════════════════════════════════════════
import requests, json, sys
from datetime import datetime, timezone, timedelta

now      = datetime.now(timezone.utc)
from_str = now.strftime('%Y-%m-%d')
to_str   = (now + timedelta(days=2)).strftime('%Y-%m-%d')

RESULTS = []

def test(name, url, params=None, headers=None):
    h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, params=params, headers=h, timeout=10)
        reachable = r.status_code in (200, 400, 401, 422)
        status = str(r.status_code)
        preview = r.text[:300] if r.status_code == 200 else r.text[:100]
        RESULTS.append((name, status, reachable, preview))
        marker = 'WORKS' if r.status_code == 200 else ('REACHABLE-' + status if reachable else 'BLOCKED-' + status)
        print(marker + ' | ' + name)
        if r.status_code == 200:
            print('  ' + preview[:200])
    except Exception as e:
        RESULTS.append((name, 'ERROR', False, str(e)))
        print('ERROR | ' + name + ' | ' + str(e)[:80])

# ── Test all known free calendar sources ─────────────────────
test('FF XML feed',          'https://www.forexfactory.com/ffcal_week_this.xml')
test('FF JSON (faireconomy)','https://nfs.faireconomy.media/ff_calendar_thisweek.json')
test('FF next week JSON',    'https://nfs.faireconomy.media/ff_calendar_nextweek.json')
test('FXStreet cal API',     'https://calendar.fxstreet.com/eventdate/',
     params={'dateFrom': from_str, 'dateTo': to_str, 'volatility': 'HIGH', 'view': 'json'})
test('jblanked FF week',     'https://www.jblanked.com/news/api/forex-factory/calendar/week/',
     params={'impact': 'High'})
test('jblanked FF range',    'https://www.jblanked.com/news/api/forex-factory/calendar/range/',
     params={'from': from_str, 'to': to_str, 'impact': 'High'})
test('TradingEconomics',     'https://api.tradingeconomics.com/calendar?c=guest:guest')
test('Myfxbook cal',         'https://www.myfxbook.com/api/get-economic-calendar.json',
     params={'start': from_str, 'end': to_str})
test('FX Empire cal',        f'https://www.fxempire.com/api/v1/en/economic-calendar',
     params={'from': from_str, 'to': to_str, 'importance': '3'})
test('Yahoo finance EURUSD', 'https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X')
test('Open ER API',          'https://open.er-api.com/v6/latest/USD')
test('Coinbase time',        'https://api.coinbase.com/v2/time')
test('FMP economics demo',   f'https://financialmodelingprep.com/api/v3/economic_calendar',
     params={'from': from_str, 'to': to_str, 'apikey': 'demo'})
test('Marketaux demo',       'https://api.marketaux.com/v1/news/all',
     params={'topics': 'economics', 'language': 'en', 'api_token': 'demo'})
test('Alpha Vantage news',   'https://www.alphavantage.co/query',
     params={'function': 'NEWS_SENTIMENT', 'topics': 'economy_macro', 'apikey': 'demo'})
test('FRED BLS releases',    'https://api.bls.gov/publicAPI/v2/releases/current?annualaverage=false')
test('Econoday FF data',     'https://mql.econoday.com/api/calendar',
     params={'client': 'forex_factory', 'version': '1.1.3',
             'queryType': 'GetSummaryData',
             'startDate': from_str, 'endDate': to_str, 'country': '900'})

# Summary
print(chr(10) + '=' * 60)
print('SUMMARY:')
for name, status, reachable, preview in RESULTS:
    marker = 'WORKS  ' if status == '200' else ('REACH  ' if reachable else 'BLOCK  ')
    print(marker + status + ' | ' + name)
