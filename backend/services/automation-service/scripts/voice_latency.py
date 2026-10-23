"""Voice latency report from the Automation database: `voice_latency.py [--last N]`.

Run it where the Automation database secret is mounted, for example
`make voice-latency`. It only reads; times come from Automation's own rows.
"""

import argparse
import asyncio
import inspect

from sqlalchemy import and_, case, select

from api.containers import Container
from application.queries.voice_latency import VoiceRecord, analyze, render
from infrastructure.db.models import JobModel, OutboxModel, VoiceJobModel


async def main(last: int) -> None:
    container = Container()
    try:
        database = await container.database.async_()
        async with database.sessions() as session:
            completed_at = case(
                (JobModel.status == "completed", JobModel.updated_at)
            ).label("completed_at")
            rows = (
                await session.execute(
                    select(
                        JobModel.id.label("job_id"),
                        JobModel.status,
                        VoiceJobModel.duration_seconds,
                        VoiceJobModel.cold_start,
                        JobModel.received_at,
                        JobModel.acked_at,
                        OutboxModel.published_at,
                        VoiceJobModel.started_at,
                        VoiceJobModel.stored_at,
                        VoiceJobModel.stt_started_at,
                        VoiceJobModel.stt_finished_at,
                        completed_at,
                    )
                    .join(JobModel.voice)
                    .outerjoin(
                        OutboxModel,
                        and_(
                            OutboxModel.correlation_id == JobModel.id,
                            OutboxModel.type == "TranscribeVoice",
                        ),
                    )
                    .order_by(JobModel.received_at.desc())
                    .limit(last)
                )
            ).all()
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown
    print(render(analyze([VoiceRecord(**row._asdict()) for row in rows])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--last", type=int, default=50, help="how many latest voice jobs to read"
    )
    options = parser.parse_args()
    asyncio.run(main(max(1, options.last)))
