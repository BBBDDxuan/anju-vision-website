"""One durable notification lane and a separate cloud-review lane."""
import asyncio
import logging
from .providers import ReviewUnavailable
from .repository import Missing
logger = logging.getLogger(__name__)

class Worker:
    def __init__(self, repo, providers):
        self.repo, self.providers = repo, providers

    def step(self, kinds=("notify", "escalate")):
        job = self.repo.claim(kinds)
        if not job:
            return False
        event_id = job["event_id"]
        try:
            body = self.repo.get_event(event_id)
            if job["kind"] == "review":
                result = self.providers.review(body)
                self.repo.patch_event(event_id, review_status="completed", review_summary=result)
            elif body["acknowledgement_status"] != "acknowledged" and body["state"] != "resolved":
                status = self.providers.notify(body, job["contact_id"])
                changes = {"notification_status": status}
                if status == "sent":
                    changes["state"] = "notified"
                self.repo.patch_event(event_id, **changes)
            self.repo.finish(job)
        except Missing:
            self.repo.finish(job)
        except ReviewUnavailable as exc:
            self.repo.patch_event(event_id, review_status="unavailable", review_summary={"summary": "云复核不可用，本地告警继续", "error": str(exc)})
            self.repo.finish(job)
        except Exception as exc:
            # Error text can contain RTSP credentials, API keys, or FCM tokens: persist only safe code/type.
            from .providers import ProviderError
            error = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
            status = self.repo.finish(job, error=error)
            logger.warning("Task %s failed (%s), attempt=%s", job["kind"], error, job["attempts"])
            if job["kind"] == "review" and status == "failed":
                self.repo.patch_event(event_id, review_status="failed", review_summary={"summary": "云复核失败，本地告警继续", "error": error})
            elif job["kind"] in {"notify", "escalate"} and status == "failed":
                self.repo.patch_event(event_id, notification_status="failed")
        return True

    def maintenance(self):
        self.repo.offline_devices()
        self.repo.expire_evidence()

    async def run(self, kinds):
        while True:
            try:
                did_work = await asyncio.to_thread(self.step, kinds)
                await asyncio.sleep(0.05 if did_work else 0.5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Worker loop failure: %s", type(exc).__name__)
                await asyncio.sleep(1)

    async def monitor(self):
        while True:
            try:
                await asyncio.to_thread(self.maintenance)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Maintenance failure: %s", type(exc).__name__)
            await asyncio.sleep(2)
