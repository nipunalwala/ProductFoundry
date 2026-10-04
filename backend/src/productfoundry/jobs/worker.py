"""The queue worker: runs the same orchestrator the CLI runs, one job per run.

Start it with `python -m productfoundry.jobs.worker` (in Docker: the `worker`
service). It also keeps one scheduled job alive that re-queues runs paused for
quota once the providers' daily quota has reset.
"""

import logging
from datetime import UTC, datetime, timedelta

from productfoundry.core.clock import utcnow
from productfoundry.core.errors import ProductFoundryError
from productfoundry.jobs import QUEUE_NAME, JobQueue, RqQueue
from productfoundry.orchestrator import RunStatus, RunStore
from productfoundry.settings import Settings

log = logging.getLogger("productfoundry.worker")
REQUEUE_PAUSED = "productfoundry.jobs.worker.requeue_paused_runs"
REQUEUE_JOB_ID = "requeue-paused-runs"
# The gateway counts usage per UTC day; a few minutes' margin keeps the retry after the reset.
RESET_MARGIN = timedelta(minutes=5)


def advance_run(run_id: str, from_stage: str | None = None) -> str:
    """The job: run the stages of one run until a checkpoint, a pause, a failure or the end."""
    from productfoundry import runtime, storage

    settings = Settings()
    engine = storage.make_engine()
    try:
        storage.check_ready(engine)
        sessions = storage.make_sessions(engine)
        orchestrator = runtime.build_orchestrator(
            storage.PostgresRunStore(sessions),
            sessions,
            fake_stages=settings.fake_stages,
            review_cap=settings.review_cap,
        )
        try:
            run = orchestrator.resume(run_id, from_stage=from_stage)
        except ProductFoundryError as exc:
            # For example the run is already waiting for approval: nothing to do, not a crash.
            log.warning("run %s was not advanced: %s", run_id, exc)
            return f"skipped: {exc}"
        log.info("run %s is now %s", run_id, run.status)
        return str(run.status)
    finally:
        engine.dispose()


def next_quota_reset(now: datetime) -> datetime:
    """When runs paused for quota are tried again: just after the next UTC midnight."""
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(days=1) + RESET_MARGIN


def requeue_paused(store: RunStore, queue: JobQueue) -> list[str]:
    """Queue every run that is paused for quota. Returns their ids."""
    paused = [run.id for run in store.list_runs() if run.status is RunStatus.PAUSED_QUOTA]
    for run_id in paused:
        queue.enqueue_resume(run_id)
    return paused


def requeue_paused_runs() -> list[str]:
    """The scheduled job: re-queue paused runs, then schedule itself for the next reset."""
    from productfoundry import storage

    settings = Settings()
    engine = storage.make_engine()
    try:
        storage.check_ready(engine)
        store = storage.PostgresRunStore(storage.make_sessions(engine))
        paused = requeue_paused(store, RqQueue(settings.redis_url))
    finally:
        engine.dispose()
    log.info("re-queued %d run(s) paused for quota", len(paused))
    schedule_requeue(settings.redis_url)
    return paused


def schedule_requeue(redis_url: str) -> datetime:
    """Make sure the re-queue job is scheduled for the next quota reset, exactly once."""
    from redis import Redis
    from rq import Queue
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    connection = Redis.from_url(redis_url)
    try:
        Job.fetch(REQUEUE_JOB_ID, connection=connection).delete()
    except NoSuchJobError:
        pass
    when = next_quota_reset(utcnow())
    Queue(QUEUE_NAME, connection=connection).enqueue_at(when, REQUEUE_PAUSED, job_id=REQUEUE_JOB_ID)
    return when


def main() -> None:
    from redis import Redis
    from rq import Queue, Worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings()
    connection = Redis.from_url(settings.redis_url)
    when = schedule_requeue(settings.redis_url)
    log.info("paused runs are re-queued at %s", when.isoformat())
    if settings.fake_stages:
        log.info("FAKE_STAGES is set: every stage is a stand-in, no outside request is made")
    Worker([Queue(QUEUE_NAME, connection=connection)], connection=connection).work(
        with_scheduler=True
    )


if __name__ == "__main__":
    main()
