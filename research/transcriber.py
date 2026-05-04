"""Transcript fetching: youtube-transcript-api (free) -> Supadata (fallback)."""
import re
import time
from typing import Optional

from research.utils import sha256_hash

SUPADATA_BASE_URL = "https://api.supadata.ai/v1"


def extract_youtube_video_id(url: str) -> str:
    """Extract YouTube video ID from any YouTube URL format."""
    patterns = [
        r"(?:v=|/)([0-9A-Za-z_-]{11}).*",
        r"(?:embed/)([0-9A-Za-z_-]{11})",
        r"(?:youtu\.be/)([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def fetch_transcript(youtube_url: str) -> dict:
    """
    Fetch transcript for a YouTube video.

    Returns dict: {video_id, title, transcript, source, word_count}

    Strategy:
    1. Fake mode short-circuit (when RESEARCH_LIVE_MODE != true)
    2. youtube-transcript-api (free, no key - try first)
    3. Supadata API (handles bot detection + AI fallback for videos without captions)
    """
    from research import config

    if not config.is_live_mode():
        try:
            video_id = extract_youtube_video_id(youtube_url)
        except ValueError:
            video_id = sha256_hash(youtube_url)[:12]
        return {
            "video_id": video_id,
            "title": "Fake Podcast Title",
            "transcript": (
                "This is a fake transcript for testing purposes. "
                "AI infrastructure demand is accelerating. NVDA leads the GPU market. "
                "Data center power constraints are an emerging bottleneck. "
                "Valuation discipline matters despite strong secular demand."
            ),
            "source": "fake",
            "word_count": 32,
        }

    video_id = extract_youtube_video_id(youtube_url)

    captured_error: Optional[str] = None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        try:
            transcript = transcript_list.find_manually_created_transcript(["en"])
        except Exception:
            pass
        if not transcript:
            try:
                transcript = transcript_list.find_generated_transcript(["en"])
            except Exception:
                pass
        if not transcript:
            for candidate in transcript_list:
                transcript = candidate
                break

        if transcript:
            rows = transcript.fetch()
            text = " ".join(row.get("text", "") for row in rows)
            if text.strip():
                return {
                    "video_id": video_id,
                    "title": f"YouTube {video_id}",
                    "transcript": text,
                    "source": "youtube_captions",
                    "word_count": len(text.split()),
                }
    except Exception as exc:
        captured_error = str(exc)[:300]

    if config.SUPADATA_API_KEY:
        return _supadata_fetch(youtube_url, video_id)

    raise RuntimeError(
        "Could not fetch transcript via youtube-transcript-api"
        + (f" ({captured_error})" if captured_error else "")
        + ". Set SUPADATA_API_KEY to enable Supadata fallback "
        "(free tier: 100 requests/month at supadata.ai)."
    )


def _supadata_fetch(youtube_url: str, video_id: str) -> dict:
    """
    Fetch transcript via Supadata universal /v1/transcript endpoint.

    - mode=auto: tries native captions first, AI generation if unavailable
    - text=true: returns plain string content (not segmented)
    - HTTP 200: synchronous result
    - HTTP 202: async job (videos >20 min) - poll /v1/transcript/{jobId}
    """
    import httpx
    from research import config

    headers = {"x-api-key": config.SUPADATA_API_KEY}
    params = {
        "url": youtube_url,
        "text": "true",
        "mode": "auto",
        "lang": "en",
    }

    response = httpx.get(
        f"{SUPADATA_BASE_URL}/transcript",
        params=params,
        headers=headers,
        timeout=60,
    )

    if response.status_code == 200:
        data = response.json()
        text = data.get("content", "")
        if not text or not text.strip():
            raise RuntimeError("Supadata returned empty transcript content")
        return {
            "video_id": video_id,
            "title": f"YouTube {video_id}",
            "transcript": text,
            "source": "supadata",
            "word_count": len(text.split()),
        }

    if response.status_code == 202:
        job_data = response.json()
        job_id = job_data.get("jobId")
        if not job_id:
            raise RuntimeError(
                f"Supadata returned 202 but no jobId in response: {response.text[:300]}"
            )
        return _supadata_poll_job(job_id, video_id, headers)

    if response.status_code == 404:
        raise RuntimeError(
            f"Supadata 404: video not found, private, or restricted ({video_id})"
        )

    raise RuntimeError(
        f"Supadata API error {response.status_code}: {response.text[:300]}"
    )


def _supadata_poll_job(
    job_id: str,
    video_id: str,
    headers: dict,
    max_wait: int = 600,
    interval: int = 10,
) -> dict:
    """
    Poll Supadata async job by job_id.

    Polls /v1/transcript/{jobId} every `interval` seconds, up to `max_wait` seconds.
    Returns transcript dict on completion. Raises on failure or timeout.

    The status field in the response body drives polling, NOT HTTP status codes.
    Possible status values: 'queued', 'active', 'completed', 'failed'.
    """
    import httpx

    elapsed = 0
    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval

        response = httpx.get(
            f"{SUPADATA_BASE_URL}/transcript/{job_id}",
            headers=headers,
            timeout=30,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Supadata job poll error {response.status_code}: {response.text[:300]}"
            )

        data = response.json()
        status = data.get("status")

        if status == "completed":
            text = data.get("content", "")
            if not text or not text.strip():
                raise RuntimeError("Supadata job completed but transcript is empty")
            return {
                "video_id": video_id,
                "title": f"YouTube {video_id}",
                "transcript": text,
                "source": "supadata",
                "word_count": len(text.split()),
            }

        if status == "failed":
            error = data.get("error", {})
            error_message = (
                error.get("message", "unknown error")
                if isinstance(error, dict)
                else str(error)
            )
            raise RuntimeError(f"Supadata job failed: {error_message}")

    raise RuntimeError(
        f"Supadata transcription timed out after {max_wait}s (job {job_id})"
    )
