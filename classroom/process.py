import asyncio

async def terminate_and_drain(process, timeout=3):
    """Terminate a task-owned child and drain pipes to avoid asyncio wait deadlocks."""
    if process.returncode is None:
        try:process.terminate()
        except ProcessLookupError:pass
    try:
        await asyncio.wait_for(process.communicate(),timeout)
    except asyncio.TimeoutError:
        if process.returncode is None:
            try:process.kill()
            except ProcessLookupError:pass
        await asyncio.wait_for(process.communicate(),timeout)
