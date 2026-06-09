"""
migrate.py
──────────
Standalone database migration script.
Run this once after deploying the updated bot to add the new AI/security tables.

Usage:
    python migrate.py

This is idempotent — safe to run multiple times.
"""

import asyncio
import os
import sys

# Allow running from the project root
sys.path.insert(0, os.path.dirname(__file__))

from utils.database import DatabaseManager


async def main():
    db = DatabaseManager()
    print("Running base migration (initialize)...")
    await db.initialize()
    print("Running v2 migration (AI / security tables)...")
    await db.migrate_v2()
    print("Migration complete. All tables are up to date.")


if __name__ == "__main__":
    asyncio.run(main())
