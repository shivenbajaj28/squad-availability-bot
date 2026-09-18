# Squad Availability Bot

A Telegram bot that collects match availability in one tap and chases whoever has not
answered. Built for the NUS Cricket squad to replace chasing 25 people by hand before
every fixture.

## What it does

- An admin posts a fixture with one command
- Everyone taps Yes, No or Maybe on inline buttons
- The message edits itself into a live tally, so nobody scrolls to count
- Anyone who has not answered gets a private nudge 24 hours before the deadline
- If a player has never DMed the bot, the nudge falls back to tagging them in the group

## Commands

| Command | Who | What |
| --- | --- | --- |
| `/fixture Name \| match time \| deadline` | Group admins | Posts and pins the fixture |
| `/squad` | Anyone | Prints the current Yes / No / Maybe / No response lists |
| `/start` | Anyone, in DM | Lets the bot message you privately |
| `/help` | Anyone | Command reference |

Example:

```
/fixture NUS vs NTU (Inter-Uni) | 2026-09-19 14:00 | 2026-09-18 20:00
```

The deadline is optional. Leave it out and it defaults to the match time.
Dates accept `YYYY-MM-DD HH:MM` or `DD/MM/YYYY HH:MM`.

## Setup, about 20 minutes

### 1. Create the bot

1. Message [@BotFather](https://t.me/BotFather) and send `/newbot`
2. Pick a name and a username, copy the token it gives you
3. Send `/setprivacy`, pick your bot, choose **Disable**

Step 3 matters. With privacy mode on, the bot cannot see ordinary group messages,
which is how it learns who is in the squad. Without it, the "No response" list stays
empty.

### 2. Run it locally

```bash
git clone <your repo url>
cd squad-availability-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # paste your token into BOT_TOKEN
python bot.py
```

Get your numeric Telegram id from [@userinfobot](https://t.me/userinfobot) and put it
in `ADMIN_IDS` if you want to post fixtures without being a group admin.

### 3. Test with 3 friends

1. Make a test group, add the bot, promote it to admin (it needs that to pin)
2. Everyone sends one message in the group so the bot records them
3. Everyone DMs the bot `/start`
4. Set `REMINDER_LEAD_HOURS=0.05` in `.env` and restart, so reminders fire after
   about 3 minutes instead of 24 hours
5. Post a fixture with a deadline a few hours away, have one person stay silent, and
   watch the DM land
6. Set `REMINDER_LEAD_HOURS` back to `24`

There is also an offline check that needs no Telegram token:

```bash
python smoke_test.py
```

## Deploy on Railway

Railway keeps the bot running around the clock and, unlike Render's free web tier,
does not spin down when idle. The bot uses long polling, so it needs no public URL.

1. Push this repo to GitHub (see below)
2. On [railway.app](https://railway.app), create a project from your GitHub repo
3. Under Variables, add `BOT_TOKEN`, `ADMIN_IDS`, `TZ_NAME=Asia/Singapore`,
   `REMINDER_LEAD_HOURS=24`, and `DB_PATH=/data/squad.db`
4. Under Settings, add a Volume mounted at `/data`
5. Deploy

The volume in steps 3 and 4 is what stops your responses disappearing on every
redeploy. Container filesystems are wiped on restart, the volume is not.

Render works too: create a **Background Worker**, not a Web Service, with build
command `pip install -r requirements.txt` and start command `python bot.py`. Add a
persistent disk for the database.

## Push to GitHub

```bash
git init
git add .
git commit -m "Squad availability bot v1"
git branch -M main
git remote add origin https://github.com/<your-username>/squad-availability-bot.git
git push -u origin main
```

`.env` and `*.db` are already in `.gitignore`. Check `git status` before the first
commit anyway. A leaked bot token means anyone can post fixtures as you. If it does
leak, send `/revoke` to BotFather.

## How it stores things

SQLite, three tables in `db.py`:

- `fixtures` - one row per match, including the deadline and whether the reminder fired
- `responses` - one row per player per fixture, updated in place when someone changes
  their mind
- `users` - the roster, built from anyone who speaks in the group, so "No response"
  means something

Pending reminders are rebuilt from the database when the bot starts, so a redeploy in
the middle of a fixture week does not lose the chase.

## Known limits

- A player only appears in "No response" once the bot has seen them speak in the group
  or tap a button. The first fixture will undercount. It settles after a week.
- Reminders arrive as a DM only if the player has sent the bot `/start` at least once.
  Everyone else gets tagged in the group instead.
- One active fixture per group. `/squad` always shows the most recent one.
