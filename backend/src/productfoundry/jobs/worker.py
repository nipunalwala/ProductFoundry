"""The queue worker: runs the same orchestrator the CLI runs, one job per run.

Start it with `python -m productfoundry.jobs.worker` (in Docker: the `worker`
service). It also keeps two scheduled jobs alive: one re-queues runs paused for
quota once the providers' daily quota has reset, the other reads every tracked
pricing page again once a week.
"""

import logging
from datetime import UTC, datetime, timedelta

from productfoundry.core.clock import utcnow
from productfoundry.core.errors import ProductFoundryError
from productfoundry.jobs import JOB_TIMEOUT_SECONDS, QUEUE_NAME, JobQueue, RqQueue
from productfoundry.orchestrator import RunStatus, RunStore
from productfoundry.settings import Settings

log = logging.getLogger("productfoundry.worker")
REQUEUE_PAUSED = "productfoundry.jobs.worker.requeue_paused_runs"
REQUEUE_JOB_ID = "requeue-paused-runs"
# The gateway counts usage per UTC day; a few minutes' margin keeps the retry after the reset.
RESET_MARGIN = timedelta(minutes=5)
REFRESH_PRICING = "productfoundry.jobs.worker.refresh_tracked_pricing"
REFRESH_PRICING_JOB_ID = "refresh-tracked-pricing"
# Pricing pages are read on Mondays at 03:00 UTC. A fixed slot, not "seven days from
# now", so that a worker restarted during the week still makes its Monday pass.
PRICING_WEEKDAY, PRICING_HOUR = 0, 3


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


def _schedule_once(redis_url: str, when: datetime, function: str, job_id: str) -> datetime:
    """Schedule `function` at `when`, replacing an earlier schedule of the same job."""
    from redis import Redis
    from rq import Queue
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    connection = Redis.from_url(redis_url)
    try:
        Job.fetch(job_id, connection=connection).delete()
    except NoSuchJobError:
        pass
    Queue(QUEUE_NAME, connection=connection).enqueue_at(
        when, function, job_id=job_id, job_timeout=JOB_TIMEOUT_SECONDS
    )
    return when


def schedule_requeue(redis_url: str) -> datetime:
    """Make sure the re-queue job is scheduled for the next quota reset, exactly once."""
    return _schedule_once(redis_url, next_quota_reset(utcnow()), REQUEUE_PAUSED, REQUEUE_JOB_ID)


def next_pricing_refresh(now: datetime) -> datetime:
    """The next Monday 03:00 UTC strictly after `now`."""
    now = now.astimezone(UTC)
    slot = now.replace(hour=PRICING_HOUR, minute=0, second=0, microsecond=0)
    slot += timedelta(days=(PRICING_WEEKDAY - slot.weekday()) % 7)
    return slot if slot > now else slot + timedelta(days=7)


def refresh_tracked_pricing() -> dict[str, int]:
    """The weekly job: snapshot every tracked pricing page, collect the release items of
    every tracked source and the traction signals of every product collected before, then
    schedule the next pass."""
    from productfoundry import runtime, storage

    settings = Settings()
    engine = storage.make_engine()
    try:
        storage.check_ready(engine)
        sessions = storage.make_sessions(engine)
        outcomes = runtime.pricing_refresh(sessions)
        releases = runtime.changelog_collect(sessions)
        traction = runtime.traction_collect(sessions)
    finally:
        engine.dispose()
    counts = pricing_counts(outcomes)
    log.info("pricing refresh: %s", ", ".join(f"{n} {name}" for name, n in counts.items()))
    for outcome in outcomes:
        if outcome.skipped:
            log.warning("pricing page %s was skipped: %s", outcome.url, outcome.skipped)
    # Release items are only collected here. Matching them to a run costs LLM calls and
    # is asked for per run (`changelog match`), so a weekly pass never spends quota on
    # runs nobody is looking at.
    log.info(
        "changelog: %d source(s) read, %d new release item(s)",
        len(releases),
        sum(outcome.new for outcome in releases),
    )
    for outcome in releases:
        if outcome.skipped:
            log.warning(
                "release source %s %s was skipped: %s",
                outcome.source.kind,
                outcome.source.target,
                outcome.skipped,
            )
    log.info(
        "traction: %d product(s), %d signal(s) stored",
        len(traction),
        sum(len(collected.observations) for collected in traction.values()),
    )
    for product, collected in traction.items():
        for reason in collected.skipped:
            log.warning("traction signal of %s was skipped: %s", product, reason)
    schedule_pricing_refresh(settings.redis_url)
    return counts


def pricing_counts(outcomes) -> dict[str, int]:
    """How a pricing pass went: pages read, unchanged, changed with an alert, skipped."""
    return {
        "pages": len(outcomes),
        "unchanged": sum(outcome.unchanged for outcome in outcomes),
        "alerts": sum(outcome.alert is not None for outcome in outcomes),
        "skipped": sum(outcome.skipped is not None for outcome in outcomes),
    }


def schedule_pricing_refresh(redis_url: str) -> datetime:
    """Make sure the weekly pricing job is scheduled for its next slot, exactly once."""
    return _schedule_once(
        redis_url, next_pricing_refresh(utcnow()), REFRESH_PRICING, REFRESH_PRICING_JOB_ID
    )


def main() -> None:
    from redis import Redis
    from rq import Queue, Worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings()
    connection = Redis.from_url(settings.redis_url)
    when = schedule_requeue(settings.redis_url)
    log.info("paused runs are re-queued at %s", when.isoformat())
    when = schedule_pricing_refresh(settings.redis_url)
    log.info("tracked pricing pages are read again at %s", when.isoformat())
    if settings.fake_stages:
        log.info("FAKE_STAGES is set: every stage is a stand-in, no outside request is made")
    Worker([Queue(QUEUE_NAME, connection=connection)], connection=connection).work(
        with_scheduler=True
    )


if __name__ == "__main__":
    main()
