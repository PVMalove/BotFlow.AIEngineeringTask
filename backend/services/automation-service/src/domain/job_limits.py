"""Unfinished jobs allowed at once (ADR-0007); voice jobs also count in the totals."""

from dataclasses import dataclass

from kernel_domain.errors import Error

from domain.errors import AutomationErrors


@dataclass(frozen=True, slots=True)
class JobCounts:
    """Unfinished jobs, read inside the acceptance lock."""

    total: int
    of_author: int
    voice_total: int
    voice_of_author: int


@dataclass(frozen=True, slots=True)
class JobLimits:
    total: int = 100
    per_author: int = 10
    voice_total: int = 10
    voice_per_author: int = 2

    def check(self, counts: JobCounts, *, voice: bool) -> Error | None:
        """Common limits first, then the voice ones."""
        if counts.total >= self.total or counts.of_author >= self.per_author:
            return AutomationErrors.job_limit()
        if voice and (
            counts.voice_total >= self.voice_total
            or counts.voice_of_author >= self.voice_per_author
        ):
            return AutomationErrors.voice_limit()
        return None
