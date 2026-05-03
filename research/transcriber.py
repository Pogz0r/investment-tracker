from research.utils import extract_youtube_video_id, is_fake_mode


def fetch_transcript(youtube_url: str) -> dict:
    video_id = extract_youtube_video_id(youtube_url)
    if is_fake_mode():
        transcript = (
            "Speaker: Today we discuss AI infrastructure, power constraints, "
            "semiconductor valuation, and second-order public equity beneficiaries."
        )
        return {
            "video_id": video_id,
            "title": f"Fake transcript for {video_id}",
            "transcript": transcript,
            "source": "fake",
            "word_count": len(transcript.split()),
        }

    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        rows = YouTubeTranscriptApi.get_transcript(video_id)
        transcript = "\n".join(row.get("text", "") for row in rows)
        return {
            "video_id": video_id,
            "title": f"YouTube video {video_id}",
            "transcript": transcript,
            "source": "youtube_captions",
            "word_count": len(transcript.split()),
        }
    except Exception as exc:
        raise RuntimeError(
            "No YouTube transcript was available. Whisper fallback can be added when OPENAI_API_KEY and ffmpeg are configured."
        ) from exc

