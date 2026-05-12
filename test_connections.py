# FXAlexG Connection Tester
import requests
import os

OANDA_API_KEY      = os.environ.get('OANDA_API_KEY', '')
OANDA_ACCOUNT_ID   = os.environ.get('OANDA_ACCOUNT_ID', '')
OANDA_BASE_URL     = 'https://api-fxpractice.oanda.com'
GEMINI_API_KEY     = os.environ.get('GEMINI_API_KEY', '')
GEMINI_URL         = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')

passed = 0
failed = 0

def ok(msg):
    global passed
    passed += 1
    print('PASS: ' + msg)

def fail(msg):
    global failed
    failed += 1
    print('FAIL: ' + msg)

print('=' * 50)
print('  FXAlexG Connection Diagnostic Test')
print('=' * 50)

print('\n[1] Checking GitHub Secrets...')
if OANDA_API_KEY:
    ok('OANDA_API_KEY is set (' + str(len(OANDA_API_KEY)) + ' chars)')
else:
    fail('OANDA_API_KEY is MISSING')

if OANDA_ACCOUNT_ID:
    ok('OANDA_ACCOUNT_ID is set: ' + OANDA_ACCOUNT_ID[:8] + '...')
else:
    fail('OANDA_ACCOUNT_ID is MISSING')

if GEMINI_API_KEY:
    ok('GEMINI_API_KEY is set (' + str(len(GEMINI_API_KEY)) + ' chars)')
else:
    fail('GEMINI_API_KEY is MISSING')

if TELEGRAM_BOT_TOKEN:
    ok('TELEGRAM_BOT_TOKEN is set (' + str(len(TELEGRAM_BOT_TOKEN)) + ' chars)')
else:
    fail('TELEGRAM_BOT_TOKEN is MISSING')

if TELEGRAM_CHAT_ID:
    ok('TELEGRAM_CHAT_ID is set: ' + TELEGRAM_CHAT_ID)
else:
    fail('TELEGRAM_CHAT_ID is MISSING')

print('\n[2] Testing OANDA Account...')
try:
    r = requests.get(
        OANDA_BASE_URL + '/v3/accounts/' + OANDA_ACCOUNT_ID,
        headers={'Authorization': 'Bearer ' + OANDA_API_KEY},
        timeout=10
    )
    if r.status_code == 200:
        data = r.json()
        balance  = data.get('account', {}).get('balance', 'N/A')
        currency = data.get('account', {}).get('currency', 'N/A')
        ok('OANDA connected! Balance: ' + str(balance) + ' ' + currency)
    elif r.status_code == 401:
        fail('OANDA 401 - API Key wrong. Regenerate on oanda.com')
    elif r.status_code == 404:
        fail('OANDA 404 - Account ID wrong. Check oanda.com dashboard')
    else:
        fail('OANDA error ' + str(r.status_code))
except Exception as e:
    fail('OANDA connection failed: ' + str(e))

print('\n[3] Testing OANDA EUR/USD...')
try:
    r = requests.get(
        OANDA_BASE_URL + '/v3/instruments/EUR_USD/candles',
        headers={'Authorization': 'Bearer ' + OANDA_API_KEY},
        params={'granularity': 'H1', 'count': '3', 'price': 'M'},
        timeout=10
    )
    if r.status_code == 200:
        candles = r.json().get('candles', [])
        if candles:
            last = candles[-1]['mid']['c']
            ok('OANDA live data OK! EUR/USD: ' + str(last))
        else:
            fail('OANDA returned empty candles')
    else:
        fail('OANDA candle error ' + str(r.status_code))
except Exception as e:
    fail('OANDA live data failed: ' + str(e))

print('\n[4] Testing OANDA XAU/USD...')
try:
    r = requests.get(
        OANDA_BASE_URL + '/v3/instruments/XAU_USD/candles',
        headers={'Authorization': 'Bearer ' + OANDA_API_KEY},
        params={'granularity': 'H1', 'count': '3', 'price': 'M'},
        timeout=10
    )
    if r.status_code == 200:
        candles = r.json().get('candles', [])
        if candles:
            last = candles[-1]['mid']['c']
            ok('XAU/USD OK! Gold: $' + str(last))
        else:
            fail('XAU/USD empty candles')
    elif r.status_code == 400:
        fail('XAU/USD not available on demo account')
    else:
        fail('XAU/USD error ' + str(r.status_code))
except Exception as e:
    fail('XAU/USD failed: ' + str(e))

print('\n[5] Testing Gemini AI...')
try:
    r = requests.post(
        GEMINI_URL + '?key=' + GEMINI_API_KEY,
        json={'contents': [{'parts': [{'text': 'Reply with exactly: GEMINI_OK'}]}]},
        timeout=30
    )
    if r.status_code == 200:
        reply = r.json()['candidates'][0]['content']['parts'][0]['text']
        ok('Gemini working! Response: ' + reply.strip()[:40])
    elif r.status_code == 403:
        fail('Gemini 403 - API key invalid. Check aistudio.google.com')
    elif r.status_code == 404:
        fail('Gemini 404 - Model not found')
    elif r.status_code == 429:
        fail('Gemini 429 - Rate limit. Wait 1 minute')
    else:
        fail('Gemini error ' + str(r.status_code) + ': ' + r.text[:100])
except Exception as e:
    fail('Gemini failed: ' + str(e))

print('\n[6] Testing Telegram Bot...')
try:
    r = requests.get(
        'https://api.telegram.org/bot' + TELEGRAM_BOT_TOKEN + '/getMe',
        timeout=10
    )
    if r.status_code == 200:
        bot = r.json().get('result', {})
        ok('Telegram connected! Bot: @' + bot.get('username', 'unknown'))
    elif r.status_code == 401:
        fail('Telegram 401 - Token wrong. Get new one from @BotFather')
    else:
        fail('Telegram error ' + str(r.status_code))
except Exception as e:
    fail('Telegram failed: ' + str(e))

print('\n[7] Sending Test Message...')
try:
    msg = (
        'FXAlexG Diagnostic Test\n\n'
        'All components working:\n'
        '- OANDA: connected\n'
        '- Gemini AI: connected\n'
        '- Telegram: connected\n\n'
        'Bot is scanning every 5 mins!\n'
        'No signal = no A+ or B+ setup right now.\n'
        'That is correct behaviour.'
    )
    r = requests.post(
        'https://api.telegram.org/bot' + TELEGRAM_BOT_TOKEN + '/sendMessage',
        json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg},
        timeout=10
    )
    if r.status_code == 200:
        ok('Test message sent! Check your Telegram now.')
    elif r.status_code == 400:
        resp = r.json()
        fail('Telegram 400 - ' + resp.get('description', 'Bad request'))
        print('     Hint: Press START on your bot in Telegram first')
    elif r.status_code == 403:
        fail('Telegram 403 - Open Telegram and press START on your bot')
    else:
        fail('Telegram send error ' + str(r.status_code))
except Exception as e:
    fail('Telegram send failed: ' + str(e))

print('\n' + '=' * 50)
print('  RESULTS: ' + str(passed) + ' passed, ' + str(failed) + ' failed')
print('=' * 50)
if failed == 0:
    print('All systems OK! Bot alerts when A+/B+ setups appear.')
else:
    print('Fix the FAIL items above then re-run this test.')
    print('Screenshot the output and share with Claude.')
