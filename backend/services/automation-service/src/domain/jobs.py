"""Jobs: accepted work whose slot frees when it completes or finally fails."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid5

# Fixed namespaces make job and outbox ids a function of their source, so a
# redelivered update addresses the same job and the same outbox row.
_JOB_NAMESPACE = UUID("2c9d6f4e-5a0b-4b57-8e57-0e6f1b3a7d01")
_OUTBOX_NAMESPACE = UUID("2c9d6f4e-5a0b-4b57-8e57-0e6f1b3a7d02")
_RESULT_INTENT_NAMESPACE = UUID("2c9d6f4e-5a0b-4b57-8e57-0e6f1b3a7d03")


class JobStatus(StrEnum):
    ACCEPTED = "accepted"  # unfinished: holds a slot
    COMPLETED = "completed"
    FAILED = "failed"


class JobKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class VoiceStage(StrEnum):
    QUEUED = "queued"  # accepted, audio not fetched yet
    STORED = "stored"  # audio checkpointed in object storage
    DONE = "done"  # transcript saved, CreateTask handed to the outbox
    FAILED = "failed"  # a final error ended the job


@dataclass(frozen=True, slots=True)
class JobLimits:
    """Unfinished jobs allowed at once (ADR-0007); voice jobs also count here."""

    total: int = 100
    per_author: int = 10
    voice_total: int = 10
    voice_per_author: int = 2


def job_id_for(bot_id: int, chat_id: int, message_id: int) -> UUID:
    return uuid5(_JOB_NAMESPACE, f"{bot_id}:{chat_id}:{message_id}")


def outbox_id_for(job_id: UUID) -> UUID:
    return uuid5(_OUTBOX_NAMESPACE, str(job_id))


def result_intent_id_for(job_id: UUID) -> UUID:
    """One result notification per job, whichever message reports the result."""
    return uuid5(_RESULT_INTENT_NAMESPACE, str(job_id))
