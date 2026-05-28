
import asyncio
from src.runtime import db
from src.tools._helpers import _format_date

async def main():
    await db.connect()
    try:
        # Get all package IDs for openssl
        async with db.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, distro FROM packages WHERE name = 'openssl'")
        
        print("Recent releases for openssl by distro:")
        for r in rows:
            pkg_id = r['id']
            distro = r['distro']
            print(f"\nDistro: {distro}")
            
            entries = await db.fetch_entries(pkg_id, limit=3)
            if not entries:
                print("  (no entries)")
                continue
            
            for e in entries:
                print(f"  === {e['version']} ({_format_date(e['entry_date'])}) ===")
                # Print first line of content as a preview
                content = e['content'].strip().splitlines()[0]
                print(f"  {content}")
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(main())
