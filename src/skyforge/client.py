"""FlightDeck API client for Skyforge CLI."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from skyforge.config import SkyforgeConfig


class FlightDeckError(Exception):
    """Error communicating with FlightDeck API."""


class FlightDeckUnavailableError(FlightDeckError):
    """FlightDeck API is not reachable."""


@dataclass
class JobStatus:
    """Status of a FlightDeck processing job."""

    job_id: str
    status: str  # pending | processing | completed | failed
    progress: float = 0.0  # 0-100
    message: str = ""
    result_url: str | None = None


class FlightDeckClient:
    """Client for the FlightDeck REST API.

    Supports context-manager usage for automatic connection cleanup:

        with FlightDeckClient(config) as client:
            ok = client.health_check()
    """

    def __init__(self, config: SkyforgeConfig) -> None:
        self.config = config
        self.base_url = config.api_url.rstrip("/")
        self._client: httpx.Client | None = None

    # ── Internal HTTP client ────────────────────────────────────────────────

    @property
    def client(self) -> httpx.Client:
        """Lazily initialise the underlying httpx.Client."""
        if self._client is None:
            headers: dict[str, str] = {}
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> FlightDeckClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ── Health ──────────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Return True if the FlightDeck API is reachable and responding."""
        try:
            resp = self.client.get("/health")
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    # ── Upload ──────────────────────────────────────────────────────────────

    def upload(self, file_path: Path, metadata: dict | None = None) -> str:
        """Upload a media file to FlightDeck.

        Uses multipart streaming to handle large files without loading them
        fully into memory.

        Returns:
            asset_id assigned by FlightDeck.

        Raises:
            FlightDeckUnavailableError: API is not reachable.
            FlightDeckError: Upload was rejected (HTTP error).
        """
        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, "video/mp4")}
                data = metadata or {}
                resp = self.client.post(
                    "/api/v1/upload",
                    files=files,
                    data=data,
                    timeout=httpx.Timeout(600.0, connect=10.0),  # 10 min for large files
                )
            resp.raise_for_status()
            return resp.json()["asset_id"]
        except httpx.ConnectError as e:
            raise FlightDeckUnavailableError(f"Cannot reach FlightDeck at {self.base_url}") from e
        except httpx.HTTPStatusError as e:
            raise FlightDeckError(
                f"Upload failed: {e.response.status_code} {e.response.text}"
            ) from e

    # ── Processing jobs ─────────────────────────────────────────────────────

    def start_processing(self, asset_id: str, options: dict | None = None) -> str:
        """Submit a processing job for an uploaded asset.

        Returns:
            job_id for the created processing job.
        """
        try:
            payload: dict = {"asset_id": asset_id, **(options or {})}
            resp = self.client.post("/api/v1/processing/jobs", json=payload)
            resp.raise_for_status()
            return resp.json()["job_id"]
        except httpx.ConnectError as e:
            raise FlightDeckUnavailableError(f"Cannot reach FlightDeck at {self.base_url}") from e
        except httpx.HTTPStatusError as e:
            raise FlightDeckError(
                f"Processing failed: {e.response.status_code} {e.response.text}"
            ) from e

    def get_job_status(self, job_id: str) -> JobStatus:
        """Fetch the current status of a processing job."""
        try:
            resp = self.client.get(f"/api/v1/processing/jobs/{job_id}")
            resp.raise_for_status()
            data = resp.json()
            return JobStatus(
                job_id=data["job_id"],
                status=data["status"],
                progress=data.get("progress", 0.0),
                message=data.get("message", ""),
                result_url=data.get("result_url"),
            )
        except httpx.ConnectError as e:
            raise FlightDeckUnavailableError(f"Cannot reach FlightDeck at {self.base_url}") from e
        except httpx.HTTPStatusError as e:
            raise FlightDeckError(
                f"Status check failed: {e.response.status_code} {e.response.text}"
            ) from e

    def poll_job(
        self,
        job_id: str,
        interval: float = 2.0,
        timeout: float = 600.0,
        callback: Callable[[JobStatus], None] | None = None,
    ) -> JobStatus:
        """Poll a job until it reaches a terminal state (completed or failed).

        Args:
            job_id: Job to poll.
            interval: Seconds between polls.
            timeout: Maximum seconds to wait before raising.
            callback: Optional function called with each JobStatus update.

        Returns:
            Final JobStatus once the job reaches a terminal state.

        Raises:
            FlightDeckError: Job did not complete within ``timeout`` seconds.
        """
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            status = self.get_job_status(job_id)
            if callback is not None:
                callback(status)
            if status.status in ("completed", "failed"):
                return status
            time.sleep(interval)
        raise FlightDeckError(f"Job {job_id} timed out after {timeout}s")

    # ── Quality analysis ────────────────────────────────────────────────────

    def start_quality_analysis(self, asset_id: str) -> str:
        """Submit a quality analysis job for an asset.

        Returns:
            job_id for the analysis job.
        """
        try:
            resp = self.client.post(f"/api/v1/quality/analyze/{asset_id}")
            resp.raise_for_status()
            return resp.json()["job_id"]
        except httpx.ConnectError as e:
            raise FlightDeckUnavailableError(f"Cannot reach FlightDeck at {self.base_url}") from e
        except httpx.HTTPStatusError as e:
            raise FlightDeckError(
                f"Analysis request failed: {e.response.status_code} {e.response.text}"
            ) from e

    def get_quality_report(self, asset_id: str) -> dict:
        """Retrieve the completed quality analysis report for an asset."""
        try:
            resp = self.client.get(f"/api/v1/quality/report/{asset_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as e:
            raise FlightDeckUnavailableError(f"Cannot reach FlightDeck at {self.base_url}") from e
        except httpx.HTTPStatusError as e:
            raise FlightDeckError(
                f"Quality report failed: {e.response.status_code} {e.response.text}"
            ) from e

    # ── Deliverables ────────────────────────────────────────────────────────

    def export_deliverable(self, segment_id: str, options: dict | None = None) -> str:
        """Request a report-ready deliverable export for a segment.

        Returns:
            job_id for the export job.
        """
        try:
            payload: dict = {"segment_id": segment_id, **(options or {})}
            resp = self.client.post("/api/v1/deliverables/export", json=payload)
            resp.raise_for_status()
            return resp.json()["job_id"]
        except httpx.ConnectError as e:
            raise FlightDeckUnavailableError(f"Cannot reach FlightDeck at {self.base_url}") from e
        except httpx.HTTPStatusError as e:
            raise FlightDeckError(
                f"Export request failed: {e.response.status_code} {e.response.text}"
            ) from e

    def get_deliverable(self, segment_id: str) -> dict:
        """Get deliverable status and download URL for a segment."""
        try:
            resp = self.client.get(f"/api/v1/deliverables/{segment_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as e:
            raise FlightDeckUnavailableError(f"Cannot reach FlightDeck at {self.base_url}") from e
        except httpx.HTTPStatusError as e:
            raise FlightDeckError(
                f"Deliverable fetch failed: {e.response.status_code} {e.response.text}"
            ) from e

    # ── Asset listing ────────────────────────────────────────────────────────

    def list_assets(self, page: int = 1, per_page: int = 20) -> dict:
        """List assets stored in FlightDeck with pagination.

        Returns:
            Parsed JSON response containing ``items``, ``total``, and ``page``.
        """
        try:
            resp = self.client.get(
                "/api/v1/assets",
                params={"page": page, "per_page": per_page},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as e:
            raise FlightDeckUnavailableError(f"Cannot reach FlightDeck at {self.base_url}") from e
        except httpx.HTTPStatusError as e:
            raise FlightDeckError(
                f"List assets failed: {e.response.status_code} {e.response.text}"
            ) from e
