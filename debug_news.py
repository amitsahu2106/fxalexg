# Debug script - run via GitHub Actions to see raw FF feed data
import requests, json
from datetime import datetime, timezone, timedelta

ff_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.forexfactory.com/',
    'Accept': 'application/json, text/plain, */*',
}

now_utc = datetime.now(timezone.utc)
now_ist = now_utc + timedelta(hours=5, minutes=30)

print('Current time UTC:', now_utc.strftime('%Y-%m-%d %H:%M UTC'))
print('Current time IST:', now_ist.strftime('%Y-%m-%d %I:%M %p IST'))
print()

# Fetch both feeds
all_events = []
for url in [
    'https://nfs.faireconomy.media/ff_calendar_thisweek.json',
    'https://nfs.faireconomy.media/ff_calendar_nextweek.json',
]:
    try:
        r = requests.get(url, headers=ff_headers, timeout=20)
        print('URL:', url.split('/')[-1], '→ HTTP', r.status_code)
        if r.status_code == 200:
            data = r.json()
            all_events.extend(data)
            print('  Events fetched:', len(data))
        else:
            print('  Response:', r.text[:100])
    except Exception as e:
        print('  ERROR:', str(e))

print()
print('TOTAL events:', len(all_events))
print()

# Show ALL unique impact values
impacts = {}
for e in all_events:
    imp = str(e.get('impact', 'MISSING'))
    impacts[imp] = impacts.get(imp, 0) + 1
print('Impact values in feed:')
for k, v in sorted(impacts.items()):
    print(' ', repr(k), '->', v, 'events')

print()
print('ALL events (raw):')
for e in all_events:
    print(json.dumps(e))
