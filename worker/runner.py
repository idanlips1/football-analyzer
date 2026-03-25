"""Worker runner — polls queue, runs pipeline, updates job state."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from config.settings import SAS_EXPIRY_HOURS
from models.job import JobResult, JobStatus
from utils.job_queue import JobQueue
from utils.job_store import JobStore
from utils.storage import StorageBackend
from utils.webhook import deliver_webhook

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5


def _run_pipeline(
    match_id: str,
    highlights_query: str,
    storage: StorageBackend,
    progress_callback: Any = None,
    kickoff_first_override: float | None = None,
    kickoff_second_override: float | None = None,
) -> dict[str, Any]:
    """Run the full highlights pipeline for a catalog match."""
    from pipeline.catalog_pipeline import run_catalog_pipeline

    return run_catalog_pipeline(
        match_id,
        highlights_query,
        storage,
        progress_callback=progress_callback,
        kickoff_first_override=kickoff_first_override,
        kickoff_second_override=kickoff_second_override,
    )


def process_job(
    job_id: str,
    match_id: str,
    highlights_query: str,
    webhook_url: str | None,
    store: JobStore,
    storage: StorageBackend,
    kickoff_first_override: float | None = None,
    kickoff_second_override: float | None = None,
) -> None:
    """Process a single job — runs pipeline, updates state, fires webhook."""
    store.update(job_id, status=JobStatus.PROCESSING, progress="starting")
    video_id_for_cleanup: str | None = None

    def on_progress(stage: str) -> None:
        store.update(job_id, progress=stage)

    try:
        result = _run_pipeline(
            match_id,
            highlights_query,
            storage,
            progress_callback=on_progress,
            kickoff_first_override=kickoff_first_override,
            kickoff_second_override=kickoff_second_override,
        )

        video_id_for_cleanup = str(result.get("video_id") or "")
        download_url = str(result["highlights_path"])
        expires_at = ""

        if (
            hasattr(storage, "upload_highlights")
            and hasattr(storage, "generate_sas_url")
            and video_id_for_cleanup
        ):
            cache_key = f"{match_id.strip().lower()}::{highlights_query.strip().lower()}"
            query_hash = hashlib.sha256(cache_key.encode()).hexdigest()[:16]
            blob_name = storage.upload_highlights(  # type: ignore[attr-defined]
                video_id_for_cleanup,
                query_hash,
                Path(download_url),
            )
            download_url = storage.generate_sas_url(  # type: ignore[attr-defined]
                blob_name,
                expiry_hours=SAS_EXPIRY_HOURS,
            )
            expires_at = (
                datetime.now(UTC) + timedelta(hours=SAS_EXPIRY_HOURS)
            ).isoformat()

        job_result = JobResult(
            download_url=download_url,
            duration_seconds=result.get("total_duration_seconds", 0.0),
            clip_count=result.get("clip_count", 0),
            expires_at=expires_at,
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
        if hasattr(storage, "cleanup_temp") and video_id_for_cleanup:
            storage.cleanup_temp(video_id_for_cleanup)  # type: ignore[attr-defined]


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

        match_id = str(msg.body.get("match_id") or "")
        if not match_id:
            log.error("Job %s missing match_id", job_id)
            store.update(
                job_id,
                status=JobStatus.FAILED,
                progress=None,
                error="Job message missing match_id (redeploy API/worker).",
            )
            queue.delete(msg)
            continue

        highlights_query = str(
            msg.body.get("highlights_query") or "full match highlights"
        )

        process_job(
            job_id=job_id,
            match_id=match_id,
            highlights_query=highlights_query,
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
