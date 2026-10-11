# AI Companion — a free, local AI friend + personal assistant

An AI that lives on your own computer, talks to you on **Telegram**, remembers you, texts you **first**
(check-ins, jokes, questions), and doubles as an assistant (reminders, tasks, notes, files, math, optional web search).

**Cost: $0.** AI runs locally with **Ollama**. No OpenAI / Gemini / Anthropic / n8n / paid services.
Stack: Python · FastAPI · SQLite (SQLAlchemy + Alembic) · APScheduler · python-telegram-bot · faster-whisper · Piper (optional).

---

## 1. What it does
| Friend mode | Assistant mode |
|---|---|
| Natural, casual chat with a configurable personality | Reminders (one-time, daily, weekly, monthly) |
| Remembers important things you tell it | Tasks, notes, study plans, generated files |
| Texts you first: check-ins, jokes, questions, motivation | Calculator, current time, optional web search |
| Notices when you go quiet — without nagging | Proactively reminds you |
| Respects quiet hours and "leave me alone" | Voice messages in (and optionally out) |

## 2. Requirements
* Windows 10/11 (also works on macOS/Linux), **Python 3.11+** (3.12 recommended)
* [Ollama](https://ollama.com/download) and a model (default `qwen3:8b`, ~5 GB; needs ~8 GB RAM, a GPU helps)
* A Telegram account
* Optional: `ffmpeg` (voice replies), `piper` (voice replies)

## 3. Install (Windows PowerShell)
```powershell
cd ai-companion
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

Voice input (optional, downloads a Whisper model on first use):
```powershell
pip install -r requirements-voice.txt
```

## 4. Install Ollama and pull the model
1. Install from https://ollama.com/download (it runs in the background automatically).
2. Download the model:
```powershell
ollama pull qwen3:8b
ollama run qwen3:8b "say hi"      # quick test, Ctrl+D to exit
```
Low on RAM? Use a smaller model, e.g. `ollama pull qwen3:4b` or `llama3.2:3b`, and set `OLLAMA_MODEL` accordingly.

## 5. Create your Telegram bot
1. In Telegram, open **@BotFather** → send `/newbot` → choose a name and a username ending in `bot`.
2. Copy the **token** it gives you (looks like `123456:ABC-DEF...`).
3. Get **your numeric user ID**: message **@userinfobot** — it replies with your `Id`.
4. Open your new bot and press **Start** (a bot can't message you until you've started it once).

## 6. Configure `.env`
```powershell
copy .env.example .env
notepad .env
```
Fill in at least:
```
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_USER_ID=123456789
COMPANION_NAME=Nova        # rename your friend here
USER_NAME=Iram
TIMEZONE=America/Los_Angeles   # IMPORTANT: quiet hours and reminders use this
API_TOKEN=pick-a-long-random-string   # protects the dashboard/API
```
Only `TELEGRAM_USER_ID` can talk to the bot — everyone else is silently ignored. `.env` is git-ignored; never share it.

## 7. Initialise the database
Automatic: migrations (Alembic) run on every startup. To run manually: `alembic upgrade head`.

## 8. Run it
```powershell
python run.py
```
You should see:
```
=====================================
NOVA - AI COMPANION
=====================================
Status:    ONLINE
Model:     qwen3:8b
Telegram:  CONNECTED
Database:  READY
Scheduler: RUNNING
Memory:    READY
=====================================
```
Open your bot in Telegram and say hi. Dashboard: http://127.0.0.1:8000/dashboard (asks for `API_TOKEN`).

Docker alternative (Ollama stays on your host): `docker compose up --build`.

## 9. Test
```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```
All external services (Ollama, Telegram) are mocked, so tests need no network.

---

## How it works

### Components and how they talk
```
Telegram ──► bot/handlers.py ──► ai/agent.py ──► ai/ollama.py ──► Ollama (local)
   ▲                                 │  ▲
   │                                 ▼  │ tool results
   │                           tools/* (reminders, tasks, notes, memory, files, calc, search)
   │                                 │
   │                                 ▼
   │                      database (SQLite via SQLAlchemy)  ◄── memory/ (extractor, manager)
   │                                 ▲
   └── services.deliver() ◄── scheduler/ (APScheduler: reminders every 30s, proactive every ~20 min,
                                       escalation, cleanup, memory maintenance, health)
FastAPI (app/main.py) hosts the lifecycle, JSON API and dashboard.
```
* **Agent**: first tries deterministic *fast paths* for must-work commands (remember / forget / "what do you remember" /
  pause / stop / start) so they work even if the model is weak or offline. Everything else goes to the LLM with
  native tool calling; if your model doesn't support tools, it automatically switches to a **JSON tool-selection fallback**.
* **Provider layer** (`app/ai/base.py`): swap Ollama for another LLM by implementing `LLMProvider`.
* **Messenger layer** (`app/bot/messenger.py`): swap Telegram for WhatsApp etc. the same way.

### Proactive messaging
Every `PROACTIVE_CHECK_INTERVAL_MINUTES` (with random jitter) the app runs **deterministic gates first**: proactive enabled,
not paused, not quiet hours, under `MAX_PROACTIVE_MESSAGES_PER_DAY`, `MIN_TIME_BETWEEN_PROACTIVE_MESSAGES_HOURS` elapsed,
you're not mid-conversation, and progressive back-off if you haven't answered earlier check-ins. Only if all pass is the
LLM asked (with your activity pattern, open tasks, upcoming reminders, memories, and its last messages) whether there's a
good reason to message — returning structured JSON (`should_contact`, `message_type`, `reason`, `message`). That output
is **validated** (type allow-list, length cap, near-duplicate detection against recent messages) before anything is sent.
It often chooses to stay quiet.

Controls (just say them): "Don't message me for 3 hours", "Don't disturb me tonight", "Stop proactive messages",
"Start proactive messages". Optional **escalation** (`ESCALATION_ENABLED=true`): one gentle follow-up after
`ESCALATION_WAIT_HOURS` of silence → optional voice note → optional phone adapter (see below). Off by default.

### Memory
* Say "Remember that my exam is Nov 12" → saved immediately (explicit, high confidence).
* Otherwise, after each message a cheap filter + the LLM decides if something durable is worth saving
  (facts, preferences, people, goals, projects, dates, habits, instructions). Each memory has category, confidence and
  importance; low-value or low-confidence items are dropped, near-duplicates are merged.
* "What do you remember about me?" lists everything; "Forget that" removes the latest; or "forget what I said about X".
* Per message, only the top relevant memories + the last `RECENT_MESSAGES_LIMIT` messages go to the model.
* Notes are separate from memories ("Save this as my project idea: …").

### Reminders
Say it naturally: "Remind me in 30 minutes", "Remind me tomorrow at 7 PM to study", "Remind me every Monday at 6 PM to…".
Times are parsed by deterministic code (not the LLM), stored in SQLite, and a 30-second job fires what's due — so they
**survive restarts**. If the app was off, missed reminders fire on startup (marked as delayed); one-time reminders
missed by more than 36 h are dropped quietly. The confirmation always states the exact time, and notes any assumption
(e.g. "assumed AM") so you can correct it.

### Voice
* **In**: send a voice message → faster-whisper (local) transcribes → handled like text. Config: `WHISPER_MODEL`
  (`tiny`/`base`/`small`), `WHISPER_DEVICE`. If it isn't installed you get a friendly message; text still works.
* **Out** (optional): `VOICE_RESPONSES=true` + install [Piper](https://github.com/rhasspy/piper), download a voice model, set
  `PIPER_BINARY` and `PIPER_MODEL_PATH`; install `ffmpeg` for proper Telegram voice notes. Text is always sent too.

### Web search (optional)
`SEARCH_PROVIDER=duckduckgo` (no key). Set `none` to disable. Failures never break the app.
`pip install duckduckgo-search` gives better results; otherwise a basic HTML fallback is used.

### Personality
Rename via `COMPANION_NAME`. Copy `data/personality.example.json` to `data/personality.json` to change traits, humor,
emoji use, reply length and add your own rules. The agent shifts between friend / assistant / supportive tone by context.

### Security
Telegram user-ID whitelist · secrets only in `.env` · logs redact secrets · tool arguments are validated · no shell
or arbitrary Python for the LLM · calculator uses a safe AST evaluator · generated files are restricted to
`data/generated/` with allow-listed extensions · API/dashboard bind to localhost and require `API_TOKEN`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| "my AI brain is temporarily unavailable" | Ollama isn't running or the model isn't pulled: `ollama serve`, `ollama pull qwen3:8b`, check `OLLAMA_HOST` |
| Telegram `DISABLED` in banner | Wrong/missing `TELEGRAM_BOT_TOKEN` or `TELEGRAM_USER_ID`; check the log line above the banner |
| Bot doesn't answer | You must message from the account whose ID is in `TELEGRAM_USER_ID`; make sure you pressed **Start** |
| Never texts first | It's quiet hours, you paused it, or the daily/min-gap limits apply. Check the dashboard → *Proactive activity* (it logs the reason). For testing: `PROACTIVE_CHECK_INTERVAL_MINUTES=1`, `MIN_TIME_BETWEEN_PROACTIVE_MESSAGES_HOURS=0.01`, `INACTIVITY_THRESHOLD_HOURS=0.01` |
| Reminder at the wrong hour | Set `TIMEZONE` to your IANA zone (e.g. `Asia/Karachi`) |
| Slow replies | Use a smaller model (`qwen3:4b`, `llama3.2:3b`) or enable GPU in Ollama |
| Voice: "speech engine isn't installed" | `pip install -r requirements-voice.txt` |
| Windows: `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

## Changing the AI model
Edit `OLLAMA_MODEL` in `.env` (after `ollama pull <name>`) and restart. Models without native tool support work through
the JSON fallback automatically. To use a different provider, add a class implementing `app.ai.base.LLMProvider` and
register it in `app/ai/factory.py`.

## Adding integrations later
* **PostgreSQL**: change `DATABASE_URL` (e.g. `postgresql+psycopg://…`), `pip install psycopg[binary]`, `alembic upgrade head`.
* **WhatsApp/Discord/etc.**: implement `app.bot.messenger.Messenger` and wire it in `app/main.py`.
* **New tool**: add a `Tool(...)` to a module in `app/tools/` and append it to that module's `TOOLS` list.
* **Phone calls (optional, NOT free)**: implement `app.integrations.telephony.base.TelephonyProvider` (Asterisk/SIP or a paid
  provider) and return it from `get_telephony()`. The app works fully without it.
* **Redis / cloud LLM / calendar / email**: add adapters behind the existing interfaces.

## Project layout
```
app/main.py          FastAPI app, lifecycle, API, banner      app/ai/        provider, agent, prompts, context
app/config.py        all settings                              app/bot/       Telegram adapter, handlers, keyboards
app/database/        models + repositories (+ alembic/)        app/memory/    manager, extractor
app/scheduler/       reminders, proactive, scheduler           app/tools/     one module per tool family
app/voice/           whisper STT, Piper TTS                    app/search/    optional web search
app/integrations/    optional telephony adapter                tests/         mocked pytest suite
```
