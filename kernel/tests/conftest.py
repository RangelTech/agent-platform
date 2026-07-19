import asyncio
import sys

# psycopg's async support cannot run on Windows' default ProactorEventLoop.
# Production runs on Linux; this only affects the local dev test run.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
