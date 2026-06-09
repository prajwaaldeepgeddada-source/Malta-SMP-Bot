# 🎮 Malta SMP Discord Bot

A fully-featured, production-ready Discord bot for the Malta SMP Minecraft server. Built with Python 3.11+, discord.py 2.x, SQLite, and GitHub Models (GPT-4o). Runs on any Ubuntu VPS with systemd.

---

## ✨ Features

| Module | Description |
|---|---|
| 🤖 AI Chatbot | Dedicated AI chat channel powered by GitHub Models (GPT-4o) — Minecraft-focused assistant with conversation memory |
| 🛡️ AI Moderation | Hybrid AI + local content moderation — detects toxicity, harassment, hate speech, threats, NSFW |
| 🚫 Spam Detection | Hybrid flood/repeat/emoji/character/ad spam detection with configurable thresholds |
| 🔐 Phishing Detection | URL scanning for scam links, Nitro scams, token grabbers, fake logins — local + AI analysis |
| 🚨 Raid Detection | 4-level automated raid protection — join raids, bot raids, copy-pasta raids, mention raids |
| 📢 Announcements | Rich embed announcements with scheduling, templates, role pings, and auto-publish for news channels |
| 📖 Help System | Interactive `/help` with category dropdown and permission badges |
| 🎫 Tickets | Panel with 4 categories, claim/close/reopen/delete, HTML transcripts, inactivity timer |
| ⚔️ Moderation | Warn, timeout, kick, ban, unban, lock, unlock, slowmode with full logging |
| 📋 Logging | Join/leave, messages, voice, roles, nicknames, channels, security events |
| 📨 Invites | Inviter tracking, invite leaderboard, per-member stats |
| 👋 Welcome | Custom welcome/goodbye embeds with variable substitution, auto-role assignment |
| ⚙️ Admin | Full configuration via slash commands, view config, bot stats |

---

## 🚀 Quick Start

### 1. Prerequisites

- Python 3.11+ (Python 3.12 also supported)
- A Discord bot token ([create one here](https://discord.com/developers/applications))
- A GitHub personal access token with `models:read` scope ([create one here](https://github.com/settings/tokens)) — required for all AI features
- **All Privileged Gateway Intents must be enabled** in the Developer Portal

### 2. Clone & Install

```bash
git clone https://github.com/your-repo/Malta-SMP-Bot
cd Malta-SMP-Bot
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
DISCORD_TOKEN=your_discord_bot_token_here
GITHUB_TOKEN=your_github_token_here
GITHUB_MODEL=gpt-4o
```

### 4. Run

```bash
python main.py
```

The database initialises automatically on first run — no manual setup needed.

---

## 🖥️ VPS Deployment (Ubuntu 22.04 / 24.04)

See the full step-by-step guide below in the **VPS Deployment Steps** section.

---

## ⚙️ First-Time Setup (Discord)

Run these slash commands in your server after inviting the bot (requires Administrator):

### 1. Set Roles
```
/setstaffrole role:@Staff
/setmodrole role:@Moderator
/setautorole role:@Member
```

### 2. Set Log Channels
```
/setlogs join_log:#join-logs leave_log:#leave-logs message_log:#message-logs mod_log:#mod-logs voice_log:#voice-logs security_log:#security-logs automod_log:#automod-logs
```

### 3. Tickets
```
/settickets category:Tickets log_channel:#ticket-logs
/settranscripts channel:#transcripts
/ticketpanel   ← run in your #support channel
```

### 4. Welcome & Goodbye
```
/setwelcome channel:#welcome message:Welcome {user} to {server}! You are member #{count}.
/setgoodbye channel:#goodbye
```

### 5. AI Chatbot
```
/ai setup channel:#ai-chat
/ai setlogchannel channel:#ai-logs
```
Members can now chat with MaltaBot in #ai-chat. No further setup needed.

### 6. AI Moderation
```
/automodai enable
/automodai sensitivity level:Medium
/automodai setlogchannel channel:#mod-logs
```

### 7. Spam Detection
```
/spamconfig enable
/spamconfig thresholds flood_messages:7 flood_window:5 repeat_count:3 emoji_count:12
```

### 8. Raid Detection
```
/raid enable
/raid setlogchannel channel:#security-logs
/raid configure joins:10 window:10
```

### 9. Announcements
```
/announce setchannel channel:#announcements
```

### 10. View Full Config
```
/viewconfig
```

---

## 📋 Command Reference

### 📖 General
| Command | Description | Permission |
|---|---|---|
| `/help [category]` | Browse all commands with interactive dropdown | Everyone |
| `/ping` | Check bot latency | Everyone |
| `/botstats` | View bot statistics | Everyone |

---

### 🤖 AI Chatbot

The AI chatbot responds to every message sent in the configured AI channel — no slash command needed. Just send a message and MaltaBot replies.

| Command | Description | Permission |
|---|---|---|
| `/ai setup <channel>` | Set the dedicated AI chat channel | Admin |
| `/ai disable` | Disable the AI chatbot | Admin |
| `/ai model` | View the active GitHub Models model and token status | Staff |
| `/ai reset` | Clear conversation memory for this server | Staff |
| `/ai stats` | View cache size, model, rate limits, memory | Staff |
| `/ai setlogchannel <channel>` | Log every AI interaction to a staff channel | Admin |

**How it works:**
- Only responds inside the configured AI channel
- Maintains memory of the last 10 conversation turns
- 5-second per-user cooldown to prevent API abuse
- Prompt injection protection built in
- Friendly Minecraft/Malta SMP personality

---

### 🛡️ AI Moderation

Hybrid system: fast local heuristics run first (zero API cost), AI is only called for messages that score above the suspicion threshold.

| Command | Description | Permission |
|---|---|---|
| `/automodai enable` | Enable AI-powered content moderation | Admin |
| `/automodai disable` | Disable AI-powered content moderation | Admin |
| `/automodai sensitivity <level>` | Set sensitivity: `low` / `medium` / `high` | Admin |
| `/automodai setlogchannel <channel>` | Set the AI moderation log channel | Admin |
| `/automodai stats` | View session violation counts | Staff |

**Detects:** Toxicity · Harassment · Hate speech · Threats · Spam · Advertising · Scam attempts · NSFW content · Mass mentions

**Actions:** Warn · Delete · Timeout · Escalate to staff

---

### 🚫 Spam Detection

| Command | Description | Permission |
|---|---|---|
| `/spamconfig enable` | Enable enhanced spam detection | Admin |
| `/spamconfig disable` | Disable enhanced spam detection | Admin |
| `/spamconfig thresholds` | Set flood/repeat/emoji thresholds | Admin |
| `/spamconfig status` | View current config | Staff |

**Detects:** Message flooding · Repeated messages · Character spam · Emoji spam · Advertisement patterns · Copy-pasta

---

### 🔐 Phishing & Security

| Command | Description | Permission |
|---|---|---|
| `/security scan <text>` | Manually scan a message for threats | Staff |
| `/security whitelist <domain>` | Add a domain to the safe whitelist | Admin |
| `/security blacklist <domain>` | Permanently block a domain | Admin |
| `/security whitelist_remove <domain>` | Remove a domain from the whitelist | Admin |
| `/security lists` | View current whitelist and blacklist | Staff |
| `/lockdown [reason]` | Manually lock all server channels | Staff |
| `/unlockdown [reason]` | Lift an active lockdown | Staff |
| `/securitystatus` | View overall security status | Staff |
| `/setminaccountage <days>` | Require minimum account age to join (0 = off) | Admin |
| `/setraidthreshold <joins> <window>` | Configure legacy raid threshold | Admin |

**Phishing detection catches:** Discord Nitro scams · Fake giveaways · Crypto scams · Token grabbers · Fake Steam/Minecraft links · URL shortener abuse · Known scam domains

---

### 🚨 Raid Detection

4-level automatic escalation based on join rate:

| Level | Trigger (default) | Action |
|---|---|---|
| 1 | 5 joins / 10s | Staff alert |
| 2 | 10 joins / 10s | Slowmode enabled on all channels |
| 3 | 15 joins / 10s | All channels locked |
| 4 | 25 joins / 10s | Emergency mode (lock + disable invites + restrict perms) |

Also detects: copy-pasta message raids · mention raids · bulk new-account joins

| Command | Description | Permission |
|---|---|---|
| `/raid status` | View current raid level and config | Staff |
| `/raid enable` | Enable advanced raid detection | Admin |
| `/raid disable` | Disable advanced raid detection | Admin |
| `/raid emergency [reason]` | Trigger Level 4 emergency immediately | Staff |
| `/raid unlock [reason]` | Lift raid mode and restore server | Staff |
| `/raid configure [joins] [window]` | Customise Level 1 threshold | Admin |
| `/raid setlogchannel <channel>` | Set raid alert channel | Admin |

---

### 📢 Announcements

| Command | Description | Permission |
|---|---|---|
| `/announce send` | Send a rich embedded announcement (modal) | Staff |
| `/announce plain` | Send a plain-text announcement (modal) | Staff |
| `/announce schedule` | Schedule for a future date/time (UTC) | Staff |
| `/announce cancel <id>` | Cancel a pending scheduled announcement | Staff |
| `/announce list` | List pending scheduled announcements | Staff |
| `/announce history [limit]` | View recently sent announcements | Staff |
| `/announce setchannel <channel>` | Set the default announcement channel | Admin |
| `/announce template save` | Save a reusable template | Staff |
| `/announce template use <name>` | Send from a saved template | Staff |
| `/announce template list` | List all saved templates | Staff |
| `/announce template delete <name>` | Delete a template | Staff |

**Features:** Rich embeds · Colour presets (`blue`, `green`, `red`, `gold`, `purple`, hex) · Optional image · Role pinging (`everyone` / `here` / role name) · Auto-publish for News channels · Full history stored in DB

---

### ⚔️ Moderation
| Command | Description | Permission |
|---|---|---|
| `/warn <member> <reason>` | Issue a formal warning | Moderator |
| `/warnings <member>` | View active warnings | Moderator |
| `/delwarn <id>` | Remove a warning by ID | Moderator |
| `/clear <amount>` | Delete 1–100 messages | Moderator |
| `/timeout <member> <minutes>` | Temporarily mute a member | Moderator |
| `/untimeout <member>` | Remove a timeout | Moderator |
| `/kick <member>` | Kick a member | Moderator |
| `/ban <member>` | Ban a member | Moderator |
| `/unban <user_id>` | Unban by user ID | Moderator |
| `/lock [reason]` | Lock the current channel | Moderator |
| `/unlock [reason]` | Unlock the current channel | Moderator |
| `/slowmode <seconds>` | Set channel slowmode (0 = off) | Moderator |

---

### 🎫 Tickets
| Command | Description | Permission |
|---|---|---|
| `/ticket` | Open a new support ticket | Everyone |
| `/ticketpanel` | Post the ticket panel in a channel | Staff |
| `/ticketstats` | View ticket statistics | Staff |
| `/adduser <member>` | Add a user to this ticket | Staff |
| `/removeuser <member>` | Remove a user from this ticket | Staff |

---

### 📨 Invites
| Command | Description | Permission |
|---|---|---|
| `/invites [member]` | Check invite count | Everyone |
| `/inviteleaderboard` | Top inviters leaderboard | Everyone |
| `/inviteinfo <member>` | Who invited this member | Staff |

---

### 👋 Welcome
| Command | Description | Permission |
|---|---|---|
| `/setwelcome <channel> [message]` | Configure welcome message | Admin |
| `/setgoodbye <channel> [message]` | Configure goodbye message | Admin |
| `/setautorole <role>` | Assign a role to new members automatically | Admin |
| `/testwelcome` | Preview the welcome message | Staff |
| `/testgoodbye` | Preview the goodbye message | Staff |

**Message variables:** `{user}` `{server}` `{count}` `{user_id}`

---

### 🔧 Base AutoMod
| Command | Description | Permission |
|---|---|---|
| `/automod <true/false>` | Enable or disable base AutoMod | Admin |
| `/setlinkwhitelist <domains>` | Comma-separated whitelisted domains | Admin |
| `/setspamthreshold <messages> [window]` | Fast spam threshold | Admin |

---

### ⚙️ Admin Setup
| Command | Description | Permission |
|---|---|---|
| `/setup` | View the setup guide | Admin |
| `/setlogs` | Configure all log channels at once | Admin |
| `/settranscripts <channel>` | Set transcript channel | Admin |
| `/settickets <category>` | Configure ticket system | Admin |
| `/setstaffrole <role>` | Set the staff role | Admin |
| `/setmodrole <role>` | Set the moderator role | Admin |
| `/viewconfig` | View all current settings | Staff |
| `/botstats` | Bot statistics | Everyone |
| `/ping` | Bot latency | Everyone |

---

## 🌐 Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ Yes | — | Your Discord bot token |
| `GITHUB_TOKEN` | ✅ Yes (AI features) | — | GitHub personal access token (`models:read` scope) |
| `GITHUB_MODEL` | No | `openai/gpt-4o` | GitHub Models model to use |
| `LOG_LEVEL` | No | `INFO` | Logging level: DEBUG / INFO / WARNING / ERROR |

---

## 🗄️ Database Schema

All tables are auto-created on startup. New tables are added by `migrate_v2()` which also runs automatically.

**Core tables:** `guild_config` · `users` · `warnings` · `moderation_logs` · `tickets` · `ticket_messages` · `invites` · `invite_uses` · `security_logs` · `staff_logs` · `automod_violations`

**AI & Security tables:** `ai_chat_stats` · `ai_mod_incidents` · `phishing_incidents` · `raid_events` · `announcements` · `announcement_templates`

---

## 🔐 Required Bot Permissions

In the Discord Developer Portal, set these permissions when generating your invite link:

- Read Messages / View Channels
- Send Messages
- Embed Links
- Attach Files
- Read Message History
- Manage Messages
- Manage Channels
- Manage Roles
- Kick Members
- Ban Members
- Moderate Members (Timeout)
- View Audit Log
- Manage Guild (invite tracking)

**Required Privileged Intents (enable in Developer Portal → Bot → Privileged Gateway Intents):**
- ✅ Server Members Intent
- ✅ Message Content Intent
- ✅ Presence Intent

---

## 📁 Project Structure

```
Malta-SMP-Bot/
├── cogs/
│   ├── admin.py           # Configuration commands
│   ├── ai_chat.py         # AI chatbot (GitHub Models / GPT-4o)
│   ├── ai_moderation.py   # AI-powered content moderation
│   ├── announcements.py   # Announcement system
│   ├── automod.py         # Base anti-spam / anti-link
│   ├── help.py            # /help command
│   ├── invites.py         # Invite tracking
│   ├── logs.py            # Event logging
│   ├── moderation.py      # Mod commands
│   ├── phishing.py        # Phishing & scam detection
│   ├── raid_detection.py  # Advanced raid protection
│   ├── security.py        # Lockdown & account age
│   ├── spam_detection.py  # Enhanced spam detection
│   ├── tickets.py         # Ticket system
│   └── welcome.py         # Welcome / goodbye / auto-role
├── utils/
│   ├── ai_service.py      # GitHub Models service (rate limit, cache, retry)
│   ├── database.py        # Async SQLite manager
│   ├── embeds.py          # Embed builder helpers
│   ├── permissions.py     # Permission check decorators
│   └── transcript.py      # HTML transcript generator
├── database/
│   └── bot.db             # SQLite database (auto-created)
├── config/
│   └── config.json
├── main.py                # Entry point
├── migrate.py             # Standalone migration script
├── malta-bot.service         # systemd service file for VPS
├── requirements.txt
└── .env.example
```

---

## 🛠️ Tech Stack

| Library | Version | Purpose |
|---|---|---|
| discord.py | 2.3.2 | Discord API |
| aiosqlite | 0.20.0 | Async SQLite database |
| aiohttp | 3.9.5 | HTTP client for GitHub Models API |
| python-dotenv | 1.0.1 | Environment variable loading |

**Deployment:** Ubuntu VPS via systemd (`malta-bot.service`)  
**AI Provider:** [GitHub Models](https://github.com/marketplace/models) — OpenAI-compatible API, default model is `openai/gpt-4o`

---

## 📝 License

MIT License — Free to use and modify for your server.

---

## 🖥️ VPS Deployment Steps

Tested on Ubuntu 22.04 LTS and 24.04 LTS. Run all commands as a user with `sudo` access.

### 1. Install Python 3.11+

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip git
```

Verify:
```bash
python3.11 --version
```

### 2. Create a dedicated bot user

Running as a non-root user is strongly recommended:

```bash
sudo useradd -r -m -s /bin/bash maltabot
```

### 3. Upload / clone the bot

```bash
# Switch to the bot user
sudo su - maltabot

# Clone from your repo (or upload the zip and unzip it)
git clone https://github.com/YOUR_USERNAME/Malta-SMP-Bot.git
# Or: unzip Malta-SMP-Bot-VPS.zip && mv malta-smp-vps Malta-SMP-Bot

cd Malta-SMP-Bot
```

### 4. Create a virtual environment and install dependencies

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Configure environment variables

```bash
cp .env.example .env
nano .env
```

Fill in your values:

```env
DISCORD_TOKEN=your_discord_bot_token_here
GITHUB_TOKEN=your_github_personal_access_token
GITHUB_MODEL=openai/gpt-4o
LOG_LEVEL=INFO
```

Save and exit (`Ctrl+O`, `Ctrl+X`).

### 6. Run the database migration

```bash
source venv/bin/activate
python migrate.py
```

Expected output:
```
Running base migration (initialize)...
Running v2 migration (AI / security tables)...
Migration complete. All tables are up to date.
```

### 7. Test the bot manually

```bash
source venv/bin/activate
python main.py
```

You should see:
```
Starting Malta SMP Bot (log level: INFO)
Database initialised.
Loaded cog: cogs.admin
...
Synced XX slash commands globally.
Logged in as MaltaBot#XXXX (ID: XXXXXXXXXX)
```

Press `Ctrl+C` to stop. If it works, proceed to step 8.

### 8. Install the systemd service

```bash
# Exit back to your sudo user
exit

# Copy the service file (edit WorkingDirectory/User paths if different)
sudo cp /home/maltabot/Malta-SMP-Bot/malta-bot.service /etc/systemd/system/malta-bot.service

# Edit paths if your install location is different
sudo nano /etc/systemd/system/malta-bot.service
```

Key lines to verify in the service file:
```ini
User=maltabot
WorkingDirectory=/home/maltabot/Malta-SMP-Bot
ExecStart=/home/maltabot/Malta-SMP-Bot/venv/bin/python main.py
```

### 9. Enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable malta-bot      # auto-start on reboot
sudo systemctl start malta-bot       # start now
sudo systemctl status malta-bot      # confirm it's running
```

### 10. View live logs

```bash
# Live systemd journal (Ctrl+C to stop)
sudo journalctl -u malta-bot -f

# Or read the bot's own log file
tail -f /home/maltabot/Malta-SMP-Bot/logs/bot.log
```

---

## 🔧 Service Management

```bash
sudo systemctl start malta-bot       # start
sudo systemctl stop malta-bot        # stop
sudo systemctl restart malta-bot     # restart (e.g. after config changes)
sudo systemctl status malta-bot      # current status
sudo journalctl -u malta-bot -n 100  # last 100 log lines
```

---

## 🔄 Updating the Bot

```bash
sudo su - maltabot
cd Malta-SMP-Bot
git pull                             # or re-upload files
source venv/bin/activate
pip install -r requirements.txt      # install any new deps
python migrate.py                    # run if DB schema changed
exit

sudo systemctl restart malta-bot
sudo systemctl status malta-bot
```

---

## 🗂️ File Layout on VPS

```
/home/maltabot/Malta-SMP-Bot/
├── .env                  ← your secrets (never commit this)
├── main.py
├── requirements.txt
├── malta-bot.service
├── migrate.py
├── cogs/
├── utils/
├── config/
├── database/
│   └── bot.db            ← SQLite database (auto-created)
└── logs/
    └── bot.log           ← persistent log file (auto-created)
```

---

## 🛡️ Firewall Notes

The bot only makes **outbound** connections (Discord API, GitHub Models API). No inbound ports need to be opened. If you have `ufw` enabled:

```bash
sudo ufw status    # check — no rule changes needed for the bot
```
