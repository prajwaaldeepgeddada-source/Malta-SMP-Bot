import aiosqlite
import os
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("MaltaSMP.Database")

# Use an absolute path relative to this file so the bot works correctly
# regardless of the working directory (e.g. when launched via systemd).
_BASE_DIR = Path(__file__).resolve().parent.parent
_DB_DIR   = _BASE_DIR / "database"
DB_PATH   = str(_DB_DIR / "bot.db")


class DatabaseManager:
    def __init__(self):
        _DB_DIR.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript("""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id        INTEGER NOT NULL,
    key             TEXT    NOT NULL,
    value           TEXT,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    username        TEXT,
    joined_at       TEXT,
    left_at         TEXT,
    total_joins     INTEGER DEFAULT 1,
    PRIMARY KEY (user_id, guild_id)
);

CREATE TABLE IF NOT EXISTS warnings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    moderator_id    INTEGER NOT NULL,
    reason          TEXT,
    evidence        TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    active          INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS moderation_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    action          TEXT NOT NULL,
    moderator_id    INTEGER NOT NULL,
    target_id       INTEGER NOT NULL,
    reason          TEXT,
    duration        TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id       TEXT UNIQUE NOT NULL,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    creator_id      INTEGER NOT NULL,
    claimer_id      INTEGER,
    category        TEXT NOT NULL,
    reason          TEXT,
    status          TEXT DEFAULT 'open',
    created_at      TEXT DEFAULT (datetime('now')),
    closed_at       TEXT,
    closer_id       INTEGER,
    close_reason    TEXT,
    last_activity   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ticket_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id       TEXT NOT NULL,
    message_id      INTEGER NOT NULL,
    author_id       INTEGER NOT NULL,
    author_name     TEXT,
    content         TEXT,
    attachments     TEXT,
    embeds          TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
);

CREATE TABLE IF NOT EXISTS invites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    invite_code     TEXT NOT NULL,
    inviter_id      INTEGER NOT NULL,
    uses            INTEGER DEFAULT 0,
    max_uses        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(guild_id, invite_code)
);

CREATE TABLE IF NOT EXISTS invite_uses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    invite_code     TEXT NOT NULL,
    inviter_id      INTEGER NOT NULL,
    invitee_id      INTEGER NOT NULL,
    joined_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS security_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    user_id         INTEGER,
    details         TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS staff_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    staff_id        INTEGER NOT NULL,
    action          TEXT NOT NULL,
    target_id       INTEGER,
    details         TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS automod_violations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    violation_type  TEXT NOT NULL,
    count           INTEGER DEFAULT 1,
    last_violation  TEXT DEFAULT (datetime('now')),
    UNIQUE(guild_id, user_id, violation_type)
);

CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_guild ON tickets(guild_id);
CREATE INDEX IF NOT EXISTS idx_tickets_creator ON tickets(creator_id);
CREATE INDEX IF NOT EXISTS idx_modlogs_guild ON moderation_logs(guild_id);
CREATE INDEX IF NOT EXISTS idx_invites_guild ON invites(guild_id);
""")
            await db.commit()
        log.info("All database tables created/verified.")

    # ── Generic helpers ───────────────────────────────────────────────────────

    async def fetchone(self, query: str, params=()) -> aiosqlite.Row | None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                return await cur.fetchone()

    async def fetchall(self, query: str, params=()) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                return await cur.fetchall()

    async def execute(self, query: str, params=()) -> int:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(query, params) as cur:
                await db.commit()
                return cur.lastrowid

    # ── Config ────────────────────────────────────────────────────────────────

    async def get_config(self, guild_id: int, key: str) -> str | None:
        row = await self.fetchone(
            "SELECT value FROM guild_config WHERE guild_id=? AND key=?",
            (guild_id, key),
        )
        return row["value"] if row else None

    async def set_config(self, guild_id: int, key: str, value: str):
        await self.execute(
            "INSERT INTO guild_config(guild_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value",
            (guild_id, key, value),
        )

    async def get_config_int(self, guild_id: int, key: str) -> int | None:
        val = await self.get_config(guild_id, key)
        return int(val) if val else None

    # ── Users ─────────────────────────────────────────────────────────────────

    async def upsert_user(self, guild_id: int, user_id: int, username: str, joined_at: str):
        await self.execute(
            "INSERT INTO users(user_id,guild_id,username,joined_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,guild_id) DO UPDATE SET "
            "username=excluded.username, joined_at=excluded.joined_at, "
            "left_at=NULL, total_joins=total_joins+1",
            (user_id, guild_id, username, joined_at),
        )

    async def set_user_left(self, guild_id: int, user_id: int):
        await self.execute(
            "UPDATE users SET left_at=datetime('now') WHERE user_id=? AND guild_id=?",
            (user_id, guild_id),
        )

    # ── Warnings ──────────────────────────────────────────────────────────────

    async def add_warning(self, guild_id: int, user_id: int, mod_id: int, reason: str, evidence: str = None) -> int:
        return await self.execute(
            "INSERT INTO warnings(guild_id,user_id,moderator_id,reason,evidence) VALUES(?,?,?,?,?)",
            (guild_id, user_id, mod_id, reason, evidence),
        )

    async def get_warnings(self, guild_id: int, user_id: int) -> list:
        return await self.fetchall(
            "SELECT * FROM warnings WHERE guild_id=? AND user_id=? AND active=1 ORDER BY created_at DESC",
            (guild_id, user_id),
        )

    async def count_warnings(self, guild_id: int, user_id: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) as c FROM warnings WHERE guild_id=? AND user_id=? AND active=1",
            (guild_id, user_id),
        )
        return row["c"] if row else 0

    async def remove_warning(self, warning_id: int, guild_id: int) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "UPDATE warnings SET active=0 WHERE id=? AND guild_id=?",
                (warning_id, guild_id),
            ) as cur:
                await db.commit()
                return cur.rowcount > 0

    # ── Moderation Logs ───────────────────────────────────────────────────────

    async def log_moderation(self, guild_id: int, action: str, mod_id: int, target_id: int, reason: str = None, duration: str = None):
        await self.execute(
            "INSERT INTO moderation_logs(guild_id,action,moderator_id,target_id,reason,duration) VALUES(?,?,?,?,?,?)",
            (guild_id, action, mod_id, target_id, reason, duration),
        )

    # ── Tickets ───────────────────────────────────────────────────────────────

    async def create_ticket(self, ticket_id: str, guild_id: int, channel_id: int, creator_id: int, category: str, reason: str = None):
        await self.execute(
            "INSERT INTO tickets(ticket_id,guild_id,channel_id,creator_id,category,reason) VALUES(?,?,?,?,?,?)",
            (ticket_id, guild_id, channel_id, creator_id, category, reason),
        )

    async def get_ticket(self, ticket_id: str) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))

    async def get_ticket_by_channel(self, channel_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM tickets WHERE channel_id=?", (channel_id,))

    async def get_open_ticket_by_user(self, guild_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM tickets WHERE guild_id=? AND creator_id=? AND status='open'",
            (guild_id, user_id),
        )

    async def update_ticket_status(self, ticket_id: str, status: str, closer_id: int = None, close_reason: str = None):
        if status == "closed":
            await self.execute(
                "UPDATE tickets SET status=?,closed_at=datetime('now'),closer_id=?,close_reason=? WHERE ticket_id=?",
                (status, closer_id, close_reason, ticket_id),
            )
        else:
            await self.execute("UPDATE tickets SET status=? WHERE ticket_id=?", (status, ticket_id))

    async def claim_ticket(self, ticket_id: str, claimer_id: int):
        await self.execute("UPDATE tickets SET claimer_id=? WHERE ticket_id=?", (claimer_id, ticket_id))

    async def update_ticket_activity(self, ticket_id: str):
        await self.execute(
            "UPDATE tickets SET last_activity=datetime('now') WHERE ticket_id=?", (ticket_id,)
        )

    async def get_inactive_tickets(self, days: int) -> list:
        return await self.fetchall(
            "SELECT * FROM tickets WHERE status='open' AND "
            "last_activity <= datetime('now', ? || ' days')",
            (f"-{days}",),
        )

    async def save_ticket_message(self, ticket_id: str, message_id: int, author_id: int, author_name: str, content: str, attachments: str, embeds: str):
        await self.execute(
            "INSERT INTO ticket_messages(ticket_id,message_id,author_id,author_name,content,attachments,embeds,created_at) "
            "VALUES(?,?,?,?,?,?,?,datetime('now'))",
            (ticket_id, message_id, author_id, author_name, content, attachments, embeds),
        )

    async def get_ticket_messages(self, ticket_id: str) -> list:
        return await self.fetchall(
            "SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY created_at ASC",
            (ticket_id,),
        )

    async def get_ticket_stats(self, guild_id: int) -> dict:
        total = await self.fetchone("SELECT COUNT(*) as c FROM tickets WHERE guild_id=?", (guild_id,))
        open_ = await self.fetchone("SELECT COUNT(*) as c FROM tickets WHERE guild_id=? AND status='open'", (guild_id,))
        closed = await self.fetchone("SELECT COUNT(*) as c FROM tickets WHERE guild_id=? AND status='closed'", (guild_id,))
        return {
            "total": total["c"] if total else 0,
            "open": open_["c"] if open_ else 0,
            "closed": closed["c"] if closed else 0,
        }

    # ── Invites ───────────────────────────────────────────────────────────────

    async def upsert_invite(self, guild_id: int, code: str, inviter_id: int, uses: int, max_uses: int):
        await self.execute(
            "INSERT INTO invites(guild_id,invite_code,inviter_id,uses,max_uses) VALUES(?,?,?,?,?) "
            "ON CONFLICT(guild_id,invite_code) DO UPDATE SET uses=excluded.uses",
            (guild_id, code, inviter_id, uses, max_uses),
        )

    async def get_invites(self, guild_id: int) -> list:
        return await self.fetchall("SELECT * FROM invites WHERE guild_id=?", (guild_id,))

    async def get_invite(self, guild_id: int, code: str) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM invites WHERE guild_id=? AND invite_code=?", (guild_id, code)
        )

    async def record_invite_use(self, guild_id: int, code: str, inviter_id: int, invitee_id: int):
        await self.execute(
            "INSERT INTO invite_uses(guild_id,invite_code,inviter_id,invitee_id) VALUES(?,?,?,?)",
            (guild_id, code, inviter_id, invitee_id),
        )
        await self.execute(
            "UPDATE invites SET uses=uses+1 WHERE guild_id=? AND invite_code=?",
            (guild_id, code),
        )

    async def get_invite_leaderboard(self, guild_id: int, limit: int = 10) -> list:
        return await self.fetchall(
            "SELECT inviter_id, COUNT(*) as total FROM invite_uses WHERE guild_id=? "
            "GROUP BY inviter_id ORDER BY total DESC LIMIT ?",
            (guild_id, limit),
        )

    async def get_user_invites(self, guild_id: int, user_id: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) as c FROM invite_uses WHERE guild_id=? AND inviter_id=?",
            (guild_id, user_id),
        )
        return row["c"] if row else 0

    async def get_invited_by(self, guild_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT inviter_id, invite_code FROM invite_uses WHERE guild_id=? AND invitee_id=? ORDER BY joined_at DESC LIMIT 1",
            (guild_id, user_id),
        )

    # ── Security Logs ─────────────────────────────────────────────────────────

    async def log_security(self, guild_id: int, event_type: str, user_id: int = None, details: str = None):
        await self.execute(
            "INSERT INTO security_logs(guild_id,event_type,user_id,details) VALUES(?,?,?,?)",
            (guild_id, event_type, user_id, details),
        )

    # ── Staff Logs ────────────────────────────────────────────────────────────

    async def log_staff_action(self, guild_id: int, staff_id: int, action: str, target_id: int = None, details: str = None):
        await self.execute(
            "INSERT INTO staff_logs(guild_id,staff_id,action,target_id,details) VALUES(?,?,?,?,?)",
            (guild_id, staff_id, action, target_id, details),
        )

    # ── AutoMod ───────────────────────────────────────────────────────────────

    async def increment_violation(self, guild_id: int, user_id: int, violation_type: str) -> int:
        await self.execute(
            "INSERT INTO automod_violations(guild_id,user_id,violation_type) VALUES(?,?,?) "
            "ON CONFLICT(guild_id,user_id,violation_type) DO UPDATE SET "
            "count=count+1, last_violation=datetime('now')",
            (guild_id, user_id, violation_type),
        )
        row = await self.fetchone(
            "SELECT count FROM automod_violations WHERE guild_id=? AND user_id=? AND violation_type=?",
            (guild_id, user_id, violation_type),
        )
        return row["count"] if row else 1

    async def reset_violations(self, guild_id: int, user_id: int, violation_type: str):
        await self.execute(
            "UPDATE automod_violations SET count=0 WHERE guild_id=? AND user_id=? AND violation_type=?",
            (guild_id, user_id, violation_type),
        )


    # ── AI & New-system tables migration ──────────────────────────────────────
    # Called from initialize() — adds new tables without touching existing ones.

    async def migrate_v2(self):
        """
        Add tables for AI chat, AI moderation, spam detection, phishing,
        and raid detection. Safe to call multiple times (IF NOT EXISTS).
        """
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executescript("""
PRAGMA journal_mode=WAL;

-- AI Chat session stats
CREATE TABLE IF NOT EXISTS ai_chat_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    message_count   INTEGER DEFAULT 0,
    last_used       TEXT DEFAULT (datetime('now')),
    UNIQUE(guild_id, user_id)
);

-- AI Moderation incidents
CREATE TABLE IF NOT EXISTS ai_mod_incidents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    category        TEXT,
    severity        TEXT,
    action          TEXT,
    reason          TEXT,
    message_content TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Phishing incidents
CREATE TABLE IF NOT EXISTS phishing_incidents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    url             TEXT,
    detection_type  TEXT,
    reason          TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Raid events
CREATE TABLE IF NOT EXISTS raid_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    level           INTEGER NOT NULL,
    trigger_reason  TEXT,
    started_at      TEXT DEFAULT (datetime('now')),
    ended_at        TEXT,
    lifted_by       TEXT
);

-- Announcements
CREATE TABLE IF NOT EXISTS announcements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    author_id       INTEGER NOT NULL,
    title           TEXT,
    body            TEXT,
    ping            TEXT DEFAULT 'none',
    scheduled       INTEGER DEFAULT 0,
    scheduled_for   TEXT,
    status          TEXT DEFAULT 'sent',
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Announcement templates
CREATE TABLE IF NOT EXISTS announcement_templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    data            TEXT NOT NULL,
    author_id       INTEGER NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(guild_id, name)
);

CREATE INDEX IF NOT EXISTS idx_ai_mod_guild ON ai_mod_incidents(guild_id);
CREATE INDEX IF NOT EXISTS idx_phishing_guild ON phishing_incidents(guild_id);
CREATE INDEX IF NOT EXISTS idx_raid_guild ON raid_events(guild_id);
CREATE INDEX IF NOT EXISTS idx_announcements_guild ON announcements(guild_id);
""")
            await db.commit()
        log.info("Database migration v2 complete.")

    # ── AI Chat stat helpers ───────────────────────────────────────────────────

    async def increment_ai_usage(self, guild_id: int, user_id: int):
        await self.execute(
            "INSERT INTO ai_chat_stats(guild_id,user_id,message_count) VALUES(?,?,1) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET "
            "message_count=message_count+1, last_used=datetime('now')",
            (guild_id, user_id),
        )

    async def get_ai_stats(self, guild_id: int) -> dict:
        total_row = await self.fetchone(
            "SELECT SUM(message_count) as total, COUNT(*) as users FROM ai_chat_stats WHERE guild_id=?",
            (guild_id,),
        )
        return {
            "total_messages": total_row["total"] or 0 if total_row else 0,
            "unique_users": total_row["users"] or 0 if total_row else 0,
        }
