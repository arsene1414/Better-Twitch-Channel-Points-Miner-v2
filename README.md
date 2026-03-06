# Better Twitch Channel Points Miner — Dynamic Edition

Personal fork of [Better-Twitch-Channel-Points-Miner-v2](https://github.com/rdavydov/Twitch-Channel-Points-Miner-v2).

The idea: manage the miner entirely from Telegram without touching config files or restarting the script. Streamers live in a JSON file, everything applies on the fly.

---

## What's different from the base fork

- Streamers are configured in `streamers_config.json` instead of being hardcoded in `main.py`
- A Telegram bot lets you add/remove streamers, check status, edit settings, manage watch priority — all without restarting
- `online_at` / `offline_at` timestamps are persisted in the JSON and restored on restart
- `currently_watching` is exposed on the miner so you know exactly which streamers are being watched at any given moment
- Linux compatibility fixes (timezone, console emojis)

---

## Installation

### Requirements

- The base miner working (valid Twitch cookies, dependencies installed)
- Python 3.11+
- A Telegram bot created via [@BotFather](https://t.me/BotFather)

### Dependencies

```bash
pip install -r requirements.txt
```

On Linux, if you get timezone errors with apscheduler, the versions in `requirements.txt` are already pinned to avoid that (`apscheduler==3.9.1`, `tzlocal==2.1`).

### Files to place

At the project root:

```
main.py
TelegramBot.py
config_loader.py
auto_stats_reporter.py   # optional
```

In the `tools/` subfolder:

```
tools/migrate_to_json.py
```

Replace in `TwitchChannelPointsMiner/`:

```
TwitchChannelPointsMiner/Twitch.py
TwitchChannelPointsMiner/TwitchChannelPointsMiner.py
TwitchChannelPointsMiner/classes/entities/Streamer.py
```

### Environment variables

Create a `.env` at the project root:

```env
TWITCH_USERNAME=your_username
TWITCH_PASSWORD=your_password
TELEGRAM_BOT_TOKEN=123456:ABC-xxx
TELEGRAM_CHAT_ID=123456789
ANALYTICS_HOST=127.0.0.1
ANALYTICS_PORT=5000
```

To get your `TELEGRAM_CHAT_ID`: send a message to your bot then open `https://api.telegram.org/bot<TOKEN>/getUpdates`.

### Migrating from an existing main.py

`migrate_to_json.py` lives in the `tools/` subfolder. If you have an old `main.py` with your streamers hardcoded:

1. Drop your old `main.py` into `tools/`
2. Run the script from the project root:

```bash
python tools/migrate_to_json.py
```

It scans for `Streamer("username")` calls, shows a preview, asks for confirmation, and writes `streamers_config.json` directly to the project root. It also warns you before overwriting an existing config.

Starting from scratch, `streamers_config.json` is created automatically on first run.

### Running

```bash
python main.py
```

---

## Telegram bot

### Streamer management

**`/status`** — main view. Shows all streamers with their status (online/offline), current points, how long they've been live. Each streamer has a settings button that opens detailed settings (bet, predictions, filters...) in the same message.

**`/online`** — only currently live streamers.

**`/add <username>`** — adds a streamer with default settings, applied immediately to the running miner.

**`/remove`** — shows all streamers as buttons with a confirmation step before deleting. `/remove <username>` also works directly.

### Watch priority

Twitch limits to 2 streams watched simultaneously. By default the miner picks based on the priority order defined in `main.py` (`STREAK > DROPS > ORDER`). You can override manually:

**`/priority`** — shows the current 2 priority slots and offers online streamers as clickable buttons to fill any free slots.

**`/priority <username>`** — direct shortcut.

**`/unpriority`** — buttons to remove a priority.

**`/unpriority <username>`** — direct shortcut.

When a priority streamer goes offline, it keeps its slot for **10 minutes**. If the stream crashes and comes back within that window, the cooldown is cancelled. After 10 minutes it's dropped automatically.

### Editing settings

```
/set_bet <username> <percentage>       -- bet percentage (1-100)
/set_max_points <username> <points>    -- max points cap per bet
/enable_predictions <username>         -- enable predictions
/disable_predictions <username>        -- disable predictions
```

Changes are written to `streamers_config.json` immediately.

### Other

**`/stats`** — total points across all streamers, streamer count, miner uptime.

**`/help`** — command list.

---

## Command autocompletion in Telegram

To get Telegram to suggest commands when you type `/`, send this to [@BotFather](https://t.me/BotFather) via `/setcommands`:

```
status - All streamers status & settings
online - Currently live streamers
add - Add a streamer
remove - Remove a streamer
priority - Manage watch priority
unpriority - Remove from priority
set_bet - Modify bet percentage
set_max_points - Modify max points
enable_predictions - Enable predictions
disable_predictions - Disable predictions
stats - Global statistics
help - Show help
```

---

## streamers_config.json

The file is auto-generated, but here's the structure for reference:

```json
{
  "streamers": [
    {
      "username": "streamer_name",
      "online_at": 0,
      "offline_at": 0,
      "settings": {
        "make_predictions": false,
        "follow_raid": true,
        "claim_drops": true,
        "watch_streak": true,
        "community_goals": true,
        "bet": {
          "strategy": "SMART",
          "percentage": 5,
          "stealth_mode": true,
          "percentage_gap": 20,
          "max_points": 1000,
          "delay_mode": "FROM_END",
          "delay": 6,
          "minimum_points": 20000,
          "filter_condition": {
            "by": "TOTAL_USERS",
            "where": "LTE",
            "value": 800
          }
        }
      }
    }
  ]
}
```

`online_at` and `offline_at` are Unix timestamps updated automatically by the miner.

---

## Automatic stats report (optional)

`auto_stats_reporter.py` sends a Telegram summary at a regular interval. To enable it, add this in `main.py`:

```python
from auto_stats_reporter import start_auto_reporter

start_auto_reporter(
    miner_instance=twitch_miner,
    telegram_token=TELEGRAM_TOKEN,
    chat_id=TELEGRAM_CHAT_ID,
    interval_hours=6
)
```