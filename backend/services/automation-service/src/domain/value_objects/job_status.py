from enum import StrEnum


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
