import hashlib
import re
from urllib.parse import parse_qs, urlparse


def sha256_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_section(markdown: str, section_name: str) -> str:
    pattern = rf"##\s+{re.escape(section_name)}\b(.*?)(?=\n##\s+|\Z)"
    match = re.search(pattern, markdown, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def extract_youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/")
    if "youtube.com" in parsed.netloc:
        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if query_id:
            return query_id
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]
    return sha256_hash(url)[:12]


def is_fake_mode() -> bool:
    from research.config import RESEARCH_LIVE_MODE

    return not RESEARCH_LIVE_MODE
