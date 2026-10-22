"""Voice latency report from the Automation database: `voice_latency.py [--last N]`.

Run it where the Automation database secret is mounted, for example
`make voice-latency`. It only reads; times come from Automation's own rows.
"""

import argparse
import asyncio
import inspect

from sqlalchemy import text

from api.containers import Container
from application.queries.voice_latency import VoiceRecord, analyze, render

_QUERY = text(
    """
    SELECT j.id, j.status, v.duration_seconds, v.cold_start, j.received_at,
           j.acked_at, o.published_at, v.started_at, v.stored_at,
           v.stt_started_at, v.stt_finished_at,
           CASE WHEN j.status = 'completed' THEN j.updated_at END
    FROM jobs j
    JOIN voice_jobs v ON v.job_id = j.id
    LEFT JOIN outbox o ON o.correlation_id = j.id AND o.type = 'TranscribeVoice'
    ORDER BY j.received_at DESC
    LIMIT :last
    """
)


async def main(last: int) -> None:
    container = Container()
    try:
        database = await container.database.async_()
        async with database.sessions() as session:
            rows = (await session.execute(_QUERY, {"last": last})).all()
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown
    print(render(analyze([VoiceRecord(*row) for row in rows])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--last", type=int, default=50, help="how many latest voice jobs to read"
    )
    options = parser.parse_args()
    asyncio.run(main(max(1, options.last)))
