from fastapi import Response


async def no_store(response: Response) -> None:
    """Router dependency: nothing the API returns may be kept by a cache."""
    response.headers["Cache-Control"] = "no-store"
