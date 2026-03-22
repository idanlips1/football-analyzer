"""Worker runner — polls queue, runs pipeline, updates job state."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from models.job import JobResult, JobStatus
from utils.job_queue import JobQueue
from utils.job_store import JobStore
from utils.storage import StorageBackend
from utils.webhook import deliver_webhook

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5


def _run_pipeline(
    query: str,
    storage: StorageBackend,
    progress_callback: Any = None,
    kickoff_first_override: float | None = None,
    kickoff_second_override: float | None = None,
) -> dict[str, Any]:
    """Run the full highlights pipeline for a query.

    This wraps the existing pipeline modules (match_finder, match_events,
    transcription, event_aligner, clip_builder) into a single function.
    """
    from pipeline.match_finder import download_and_save, find_match, is_url

    if progress_callback:
        progress_callback("searching")

    if is_url(query):
        url = query
    else:
        result = find_match(query, storage)
        candidates = result.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"No match videos found for: {query}")
        url = candidates[0]["url"]

    if progress_callback:
        progress_callback("downloading")

    metadata = download_and_save(url, storage, skip_duration_check=False)
    video_id = metadata["video_id"]

    if progress_callback:
        progress_callback("fetching_events")

    from pipeline.match_events import fetch_match_events
    from pipeline.match_finder import resolve_fixture_for_video

    try:
        res = resolve_fixture_for_video("", metadata.get("video_filename", ""))
        if res.fixture_id:
            metadata["fixture_id"] = res.fixture_id
    except Exception:  # noqa: BLE001
        pass

    match_events = fetch_match_events(metadata, storage)

    if progress_callback:
        progress_callback("transcribing")

    from pipeline.transcription import transcribe

    transcription = transcribe(metadata, storage)
    kickoff_first = kickoff_first_override or transcription.get("kickoff_first_half")
    kickoff_second = kickoff_second_override or transcription.get("kickoff_second_half")

    if kickoff_first is None or kickoff_second is None:
        raise RuntimeError(
            "Could not auto-detect kickoff timestamps. "
            "Re-submit with kickoff_first_half and kickoff_second_half overrides."
        )

    if progress_callback:
        progress_callback("aligning")

    from pipeline.event_aligner import align_events

    align_events(match_events, metadata, storage, kickoff_first, kickoff_second)

    from models.game import GameState

    game = GameState(
        video_id=video_id,
        home_team="",
        away_team="",
        league="",
        date="",
        fixture_id=int(metadata.get("fixture_id") or 0),
        video_filename=metadata.get("video_filename", ""),
        source=f"https://www.youtube.com/watch?v={video_id}",
        duration_seconds=metadata["duration_seconds"],
        kickoff_first_half=kickoff_first,
        kickoff_second_half=kickoff_second,
    )
    storage.write_json(video_id, "game.json", game.to_dict())

    if progress_callback:
        progress_callback("building_clips")

    from models.events import AlignedEvent
    from models.highlight_query import HighlightQuery, QueryType
    from pipeline.clip_builder import build_highlights
    from pipeline.event_filter import filter_events

    aligned_data = storage.read_json(video_id, "aligned_events.json")
    aligned_events = [AlignedEvent.from_dict(e) for e in aligned_data.get("events", [])]

    try:
        from pipeline.query_interpreter import interpret_query

        hq = interpret_query(query, game, aligned_events)
    except Exception:  # noqa: BLE001
        hq = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=query)

    filtered = filter_events(aligned_events, hq)
    result = build_highlights(filtered, game, hq, storage)
    return result


def process_job(
    job_id: str,
    query: str,
    webhook_url: str | None,
    store: JobStore,
    storage: StorageBackend,
    kickoff_first_override: float | None = None,
    kickoff_second_override: float | None = None,
) -> None:
    """Process a single job — runs pipeline, updates state, fires webhook."""
    store.update(job_id, status=JobStatus.PROCESSING, progress="starting")

    def on_progress(stage: str) -> None:
        store.update(job_id, progress=stage)

    try:
        result = _run_pipeline(
            query,
            storage,
            progress_callback=on_progress,
            kickoff_first_override=kickoff_first_override,
            kickoff_second_override=kickoff_second_override,
        )

        job_result = JobResult(
            download_url=result["highlights_path"],
            duration_seconds=result.get("total_duration_seconds", 0.0),
            clip_count=result.get("clip_count", 0),
            expires_at="",
        )

        store.update(
            job_id,
            status=JobStatus.COMPLETED,
            progress=None,
            result=job_result,
        )

        asyncio.run(
            deliver_webhook(
                webhook_url,
                {
                    "job_id": job_id,
                    "status": "completed",
                    "result": job_result.to_dict(),
                },
            )
        )

    except Exception as exc:
        log.exception("Job %s failed: %s", job_id, exc)
        store.update(
            job_id,
            status=JobStatus.FAILED,
            progress=None,
            error=str(exc),
        )
        asyncio.run(
            deliver_webhook(
                webhook_url,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "error": str(exc),
                },
            )
        )
    finally:
        if hasattr(storage, "cleanup_temp"):
            storage.cleanup_temp(job_id)


def run_worker(queue: JobQueue, store: JobStore, storage: StorageBackend) -> None:
    """Main worker loop — polls queue, processes jobs."""
    log.info("Worker started, polling queue every %ds", POLL_INTERVAL_SECONDS)
    while True:
        msg = queue.receive(visibility_timeout=3900)
        if msg is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        job_id = msg.body["job_id"]
        log.info("Processing job %s", job_id)

        process_job(
            job_id=job_id,
            query=msg.body["query"],
            webhook_url=msg.body.get("webhook_url"),
            store=store,
            storage=storage,
            kickoff_first_override=msg.body.get("kickoff_first_half"),
            kickoff_second_override=msg.body.get("kickoff_second_half"),
        )

        queue.delete(msg)
        log.info("Job %s complete, message deleted", job_id)


def main() -> None:
    """Entrypoint for `python -m worker.runner`."""
    from api.dependencies import get_job_queue, get_job_store, get_storage
    from utils.logger import setup_logging

    setup_logging()
    run_worker(
        queue=get_job_queue(),
        store=get_job_store(),
        storage=get_storage(),
    )


if __name__ == "__main__":
    main()
