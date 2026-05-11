“””
FXAlexG Connection Tester
Tests OANDA, Gemini and Telegram one by one
Run this manually from GitHub Actions to diagnose issues
“””

import requests
import os

OANDA_API_KEY      = os.environ.get(“OANDA_API_KEY”, “”)
OANDA_ACCOUNT_ID   = os.environ.get(“OANDA_ACCOUNT_ID”, “”)
OANDA_BASE_URL     = “https://api-fxpractice.oanda.com”

GEMINI_API_KEY     = os.environ.get(“GEMINI_API_KEY”, “”)
GEMINI_URL         = “https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent”

TELEGRAM_BOT_TOKEN = os.environ.get(“TELEGRAM_BOT_TOKEN”, “”)
TELEGRAM_CHAT_ID   = os.environ.get(“TELEGRAM_CHAT_ID”, “”)

passed = 0
failed = 0

def ok(msg):
global passed
passed += 1
print(“PASS: “ + msg)

def fail(msg):
global failed
failed += 1
print(“FAIL: “ + msg)

print(”=” * 50)
print(”  FXAlexG Connection Diagnostic Test”)
print(”=” * 50)

# ─────────────────────────────

# TEST 1 — Check secrets exist

# ─────────────────────────────

print(”\n[1] Checking GitHub Secrets…”)

if OANDA_API_KEY:
ok(“OANDA_API_KEY is set (” + str(len(OANDA_API_KEY)) + “ chars)”)
else:
fail(“OANDA_API_KEY is MISSING or empty”)

if OANDA_ACCOUNT_ID:
ok(“OANDA_ACCOUNT_ID is set: “ + OANDA_ACCOUNT_ID[:8] + “…”)
else:
fail(“OANDA_ACCOUNT_ID is MISSING or empty”)

if GEMINI_API_KEY:
ok(“GEMINI_API_KEY is set (” + str(len(GEMINI_API_KEY)) + “ chars)”)
else:
fail(“GEMINI_API_KEY is MISSING or empty”)

if TELEGRAM_BOT_TOKEN:
ok(“TELEGRAM_BOT_TOKEN is set (” + str(len(TELEGRAM_BOT_TOKEN)) + “ chars)”)
else:
fail(“TELEGRAM_BOT_TOKEN is MISSING or empty”)

if TELEGRAM_CHAT_ID:
ok(“TELEGRAM_CHAT_ID is set: “ + TELEGRAM_CHAT_ID)
else:
fail(“TELEGRAM_CHAT_ID is MISSING or empty”)

# ─────────────────────────────

# TEST 2 — OANDA Account

# ─────────────────────────────

print(”\n[2] Testing OANDA Connection…”)
try:
r = requests.get(
OANDA_BASE_URL + “/v3/accounts/” + OANDA_ACCOUNT_ID,
headers={“Authorization”: “Bearer “ + OANDA_API_KEY},
timeout=10
)
if r.status_code == 200:
data = r.json()
balance = data.get(‘account’, {}).get(‘balance’, ‘N/A’)
currency = data.get(‘account’, {}).get(‘currency’, ‘N/A’)
ok(“OANDA account connected! Balance: “ + str(balance) + “ “ + currency)
elif r.status_code == 401:
fail(“OANDA 401 — API Key is wrong. Regenerate it on oanda.com”)
elif r.status_code == 404:
fail(“OANDA 404 — Account ID is wrong. Check it on oanda.com dashboard”)
else:
fail(“OANDA error “ + str(r.status_code) + “: “ + r.text[:100])
except Exception as e:
fail(“OANDA connection failed: “ + str(e))

# ─────────────────────────────

# TEST 3 — OANDA Live Price

# ─────────────────────────────

print(”\n[3] Testing OANDA Live Data (EUR/USD)…”)
try:
r = requests.get(
OANDA_BASE_URL + “/v3/instruments/EUR_USD/candles”,
headers={“Authorization”: “Bearer “ + OANDA_API_KEY},
params={“granularity”: “H1”, “count”: “3”, “price”: “M”},
timeout=10
)
if r.status_code == 200:
candles = r.json().get(‘candles’, [])
if candles:
last = candles[-1][‘mid’][‘c’]
ok(“OANDA live data working! EUR/USD latest close: “ + str(last))
else:
fail(“OANDA returned empty candles”)
else:
fail(“OANDA candle fetch error “ + str(r.status_code))
except Exception as e:
fail(“OANDA live data failed: “ + str(e))

# ─────────────────────────────

# TEST 4 — OANDA XAU/USD

# ─────────────────────────────

print(”\n[4] Testing OANDA XAU/USD (Gold)…”)
try:
r = requests.get(
OANDA_BASE_URL + “/v3/instruments/XAU_USD/candles”,
headers={“Authorization”: “Bearer “ + OANDA_API_KEY},
params={“granularity”: “H1”, “count”: “3”, “price”: “M”},
timeout=10
)
if r.status_code == 200:
candles = r.json().get(‘candles’, [])
if candles:
last = candles[-1][‘mid’][‘c’]
ok(“XAU/USD data working! Gold latest close: $” + str(last))
else:
fail(“XAU/USD returned empty candles”)
elif r.status_code == 400:
fail(“XAU/USD not available on your OANDA demo — instrument may need enabling”)
else:
fail(“XAU/USD error “ + str(r.status_code))
except Exception as e:
fail(“XAU/USD data failed: “ + str(e))

# ─────────────────────────────

# TEST 5 — Gemini AI

# ─────────────────────────────

print(”\n[5] Testing Gemini AI…”)
try:
r = requests.post(
GEMINI_URL + “?key=” + GEMINI_API_KEY,
json={“contents”: [{“parts”: [{“text”: “Reply with exactly: GEMINI_OK”}]}]},
timeout=30
)
if r.status_code == 200:
reply = r.json()[‘candidates’][0][‘content’][‘parts’][0][‘text’]
ok(“Gemini AI working! Response: “ + reply.strip()[:50])
elif r.status_code == 400:
fail(“Gemini 400 — Bad request. Model name may be wrong”)
elif r.status_code == 403:
fail(“Gemini 403 — API key invalid or API not enabled. Check aistudio.google.com”)
elif r.status_code == 404:
fail(“Gemini 404 — Model not found. Check gemini-2.0-flash is available”)
elif r.status_code == 429:
fail(“Gemini 429 — Rate limit hit. Wait a minute and try again”)
else:
fail(“Gemini error “ + str(r.status_code) + “: “ + r.text[:150])
except Exception as e:
fail(“Gemini connection failed: “ + str(e))

# ─────────────────────────────

# TEST 6 — Telegram

# ─────────────────────────────

print(”\n[6] Testing Telegram Bot…”)
try:
r = requests.get(
“https://api.telegram.org/bot” + TELEGRAM_BOT_TOKEN + “/getMe”,
timeout=10
)
if r.status_code == 200:
bot = r.json().get(‘result’, {})
ok(“Telegram bot connected! Bot name: @” + bot.get(‘username’, ‘unknown’))
elif r.status_code == 401:
fail(“Telegram 401 — Bot token is wrong. Get a new one from @BotFather”)
else:
fail(“Telegram error “ + str(r.status_code))
except Exception as e:
fail(“Telegram connection failed: “ + str(e))

# ─────────────────────────────

# TEST 7 — Send Test Message

# ─────────────────────────────

print(”\n[7] Sending Test Message to Telegram…”)
try:
msg = (
“FXAlexG Diagnostic Test\n\n”
“If you see this message all 3 components are working:\n”
“OANDA — connected\n”
“Gemini AI — connected\n”
“Telegram — connected\n\n”
“Your bot is alive and scanning!\n”
“You will only get trade alerts when A+ or B+ setups appear.\n”
“No signal = no high quality setup right now. That is correct.”
)
r = requests.post(
“https://api.telegram.org/bot” + TELEGRAM_BOT_TOKEN + “/sendMessage”,
json={“chat_id”: TELEGRAM_CHAT_ID, “text”: msg},
timeout=10
)
if r.status_code == 200:
ok(“Test message sent to Telegram! Check your phone now.”)
elif r.status_code == 400:
resp = r.json()
fail(“Telegram 400 — “ + resp.get(‘description’, ‘Bad request’))
print(”     Hint: Chat ID may be wrong, or you never pressed START on your bot”)
elif r.status_code == 403:
fail(“Telegram 403 — Bot blocked or chat not found. Open Telegram and press START on your bot”)
else:
fail(“Telegram send error “ + str(r.status_code) + “: “ + r.text[:100])
except Exception as e:
fail(“Telegram send failed: “ + str(e))

# ─────────────────────────────

# FINAL SUMMARY

# ─────────────────────────────

print(”\n” + “=” * 50)
print(”  RESULTS: “ + str(passed) + “ passed, “ + str(failed) + “ failed”)
print(”=” * 50)

if failed == 0:
print(“All systems working! Your bot will alert you when”)
print(“A+ or B+ setups appear on your watchlist pairs.”)
else:
print(“Fix the FAIL items above then re-run this test.”)
print(“Screenshot the output and share with Claude.”)
