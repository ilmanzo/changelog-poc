
import asyncio
import structlog
from src.runtime import db, ingest_service
from src.tools.changelog import analyze_package_diff

async def main():
    await db.connect()
    try:
        # Sync from Fedora to get more history
        print("Syncing vim from Fedora...")
        await ingest_service.ingest("vim", "fedora")
        
        # Now analyze the diff
        print("Analyzing diff between 9.0 and 9.2...")
        result = await analyze_package_diff("vim", "9.0", "9.2")
        print(result)
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(main())
