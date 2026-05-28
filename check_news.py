import asyncio
from src.runtime import db

async def main():
    await db.connect()
    count = await db._pool.fetchval('SELECT count(*) FROM news')
    print(f'News count: {count}')
    
    if count > 0:
        rows = await db._pool.fetch('SELECT * FROM news LIMIT 5')
        for r in rows:
            print(r)
    
    await db.close()

if __name__ == "__main__":
    asyncio.run(main())
