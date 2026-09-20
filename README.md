# Calendar Agent

A voice-promptable calendar assistant for someone who lives in two timezones.

Speak or type a request — in Telegram from your phone, or with a hotkey from
the macOS menu bar — and it books, queries, reschedules or cancels meetings,
showing every time in **both** Toronto and Vilnius. A Raspberry Pi runs the
whole thing, so it keeps working with the MacBook closed.

```
"I have a meeting this Thursday, 3pm Lithuanian time, for Acme Corp"

  -> Acme Corp sync
     Thu 24 Sep - 15:00 EEST Vilnius / 08:00 EDT Toronto
     60 minutes
     [ Confirm ]  [ Cancel ]
```

## What it does

- **Books by voice or text**, in either timezone, including recurring series
  ("every other Thursday", "last Friday of the month").
- **Never double-books.** If the slot is taken it says so and offers real
  alternatives drawn from your actual free/busy.
- **Finds free time** — "what slots are free Thursday?" — respecting working
  hours defined per timezone, with a buffer around existing meetings.
- **Answers questions** — "what do I have today?", "what's in the next hour?"
- **Reminds you** an hour before, in both zones, via Telegram.
- **Always confirms before writing.** Nothing reaches your calendar until you
  tap Confirm.

## Architecture

```
  MacBook                          Raspberry Pi (always on)
  CalendarAgent.app     LAN        FastAPI :8000
  hotkey push-to-talk  ------->    whisper.cpp   (speech -> text, local)
  dual-zone agenda                 Claude API    (text -> typed intent)
                                   Telegram bot  (long poll, outbound only)
  Telegram (phone) ----------->    APScheduler   (reminders)
                                   SQLite        (confirmations, dedupe)
                                        |
                                        v
                                 Google Calendar
                          (syncs to Apple Calendar.app + iPhone)
```

Your **audio never leaves the network** — whisper.cpp runs on the Pi. Only the
resulting text goes to the Claude API.

Google Calendar is the source of truth rather than Apple Calendar because the
Pi cannot reach EventKit; with Google, the Pi stays authoritative while events
still appear in Apple Calendar.app and on your iPhone.

## Layout

| Path | What |
|---|---|
| `backend/app/timezones.py` | Zone resolution, DST-safe construction, dual rendering |
| `backend/app/recurrence.py` | Typed recurrence -> RRULE, DST-stable expansion |
| `backend/app/parser.py` | Claude call: transcript -> typed `Intent` |
| `backend/app/agent.py` | Orchestration; the confirm-before-write rule |
| `backend/app/slots.py` | Free-slot search and conflict detection (pure) |
| `backend/app/calendar_client.py` | Google Calendar: free/busy + CRUD |
| `backend/app/telegram_bot.py` | Telegram front end |
| `backend/app/main.py` | FastAPI service + lifecycle |
| `mac/CalendarAgent/` | SwiftUI menu-bar app |
| `deploy/` | systemd unit, Docker, Pi setup script |

## Setup

### 1. Google Calendar

Create an OAuth client (type **Desktop app**) in the Google Cloud console with
the Calendar API enabled, and save it as `backend/credentials.json`.

Run the consent flow **on the Mac** — it needs a browser:

```bash
cd backend
uv run python -c "from app.calendar_client import load_credentials; load_credentials()"
```

That writes `backend/token.json`. Copy it to the Pi later.

> A service account will not work here: it cannot see a personal Gmail
> calendar without domain-wide delegation, which a personal account cannot
> grant.

### 2. Telegram

Create a bot with [@BotFather](https://t.me/BotFather) and take the token.

```bash
cd backend
cp .env.example .env          # add ANTHROPIC_API_KEY and TELEGRAM_BOT_TOKEN
cp config.toml.example config.toml
```

Send `/start` to your bot — it replies with your chat id. Put that in
`TELEGRAM_ALLOWED_CHAT_IDS`.

> **The whitelist is not optional.** An empty whitelist denies everyone by
> design; without it, anyone who finds the bot's username could read and
> rewrite your calendar.

### 3. Run

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
curl localhost:8000/health
```

Voice needs whisper.cpp; text works without it.

### 4. macOS app

```bash
cd mac/CalendarAgent
./build-app.sh release
open build/CalendarAgent.app
```

Set the backend address in Settings. Default hotkey is **Control-Option-Space**
— press to start, press again to send.

### 5. Raspberry Pi

```bash
git clone <this repo> ~/calendar-agent
cd ~/calendar-agent/deploy
./setup-pi.sh small        # installs deps, builds whisper.cpp, fetches a model
```

Then copy `credentials.json`, `token.json`, `.env` and `config.toml` from the
Mac into `backend/`, and enable the service:

```bash
sudo cp calendar-agent.service /etc/systemd/system/
sudo systemctl enable --now calendar-agent
```

## Tests

```bash
cd backend
uv run pytest -m "not costs_money"   # 100 tests, no API calls, instant
uv run pytest -m costs_money         # golden parser set, hits the Claude API
```

The timezone tests are the ones that matter. Every constant in them was
verified against `zoneinfo` rather than reasoned about:

**North America and the EU do not change clocks on the same dates.** The
Toronto↔Vilnius gap is 7h for most of the year but **6h** between Mar 8–29 and
again Oct 25–Nov 1 (2026 dates). Code that assumes a constant 7h books meetings
an hour off, silently, twice a year. Hence two rules the code never breaks:

1. The model never does time arithmetic — it reports what you said, and
   `zoneinfo` converts.
2. Nothing is stored as a naive datetime or a fixed offset. Only IANA zone
   names survive.

## Costs and caveats

- **Claude API**: ~$0.0065 per request; roughly **$4/month** at 20 requests a
  day.
- **Latency**: a Telegram voice note takes ~10–25s end to end on a Pi 5, with
  whisper dominating. The bot says "Transcribing…" immediately.
- **Lithuanian transcription** is the weakest link. If `small` struggles on
  Lithuanian names, try `medium`, or say company names in English.
- **LAN only**: the Mac app needs to be on the home network. Telegram works
  from anywhere. Adding Tailscale later is a config change, not a rewrite.
