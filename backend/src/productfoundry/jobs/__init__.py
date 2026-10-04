"""The job queue: long stages run in a worker, not in the API process.

`JobQueue` is the seam. `RqQueue` is the real one (RQ on Redis); its worker runs
in Docker, because RQ's worker does not run natively on Windows.
"""

from typing import Protocol

QUEUE_NAME = "productfoundry"
ADVANCE_RUN = "productfoundry.jobs.worker.advance_run"
# A stage can wait a long time on rate-limited providers and on review fetching.
JOB_TIMEOUT_SECONDS = 6 * 60 * 60


class JobQueue(Protocol):
    def enqueue_resume(self, run_id: str, from_stage: str | None = None) -> None:
        """Ask a worker to run the stages of this run until it stops again."""
        ...


class RecordingQueue:
    """A queue for tests: it only remembers what was asked."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, str | None]] = []

    def enqueue_resume(self, run_id: str, from_stage: str | None = None) -> None:
        self.jobs.append((run_id, from_stage))


class RqQueue:
    def __init__(self, redis_url: str) -> None:
        # Imported here: only the API and the worker need Redis.
        from redis import Redis
        from rq import Queue

        self._queue = Queue(QUEUE_NAME, connection=Redis.from_url(redis_url))

    def enqueue_resume(self, run_id: str, from_stage: str | None = None) -> None:
        self._queue.enqueue(ADVANCE_RUN, run_id, from_stage, job_timeout=JOB_TIMEOUT_SECONDS)
