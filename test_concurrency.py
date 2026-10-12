import sys
import io
import asyncio
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
from src.tools.web import search_web

async def test():
    results = await asyncio.gather(
        asyncio.to_thread(search_web, 'apple'),
        asyncio.to_thread(search_web, 'banana')
    )
    print("Success")

asyncio.run(test())

