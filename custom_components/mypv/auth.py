"""Shared authentication helpers for my-PV HTTP sessions."""

from aiohttp import ClientTimeout


async def authenticate_session(session, host: str, update_key: str, ssl_context) -> bool:
    """Authenticate against /auth.jsn and keep cookie in the session."""
    if not update_key:
        return True

    timeout = ClientTimeout(total=5)
    async with session.post(
        f"https://{host}/auth.jsn",
        timeout=timeout,
        ssl=ssl_context,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"pw": update_key},
    ) as response:
        if response.status != 200:
            return False

        payload = await response.json(content_type=None)
        auth_flag = payload.get("auth")
        return auth_flag in (1, True, "1")