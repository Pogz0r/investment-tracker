import os

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

    last_error = "unknown error"

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
            text = "\n".join(row.get("text", "") for row in rows)
            return {
                "video_id": video_id,
                "title": f"YouTube video {video_id}",
                "transcript": text,
                "source": "youtube_captions",
                "word_count": len(text.split()),
            }
    except Exception as exc:
        last_error = str(exc)

    if os.environ.get("OPENAI_API_KEY"):
        return _whisper_fallback(youtube_url, video_id)

    raise RuntimeError(
        f"Could not fetch transcript. YouTube API error: {last_error}. "
        "To enable fallback transcription, set OPENAI_API_KEY and ensure ffmpeg is available."
    )


def _whisper_fallback(youtube_url: str, video_id: str) -> dict:
    """Download audio with yt-dlp, transcribe with OpenAI Whisper."""
    import tempfile

    import yt_dlp
    from openai import OpenAI

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = os.path.join(tmp, f"{video_id}.mp3")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmp, f"{video_id}.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }],
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            title = info.get("title", f"YouTube {video_id}")

        oai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        with open(audio_path, "rb") as audio_file:
            result = oai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )

        text = result.text
        return {
            "video_id": video_id,
            "title": title,
            "transcript": text,
            "source": "whisper",
            "word_count": len(text.split()),
        }
