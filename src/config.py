from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# Base directories
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
EVENTS_FILE = DATA_DIR / "events.json"

# Load local .env file if it exists (for local testing convenience)
_env_path = ROOT_DIR / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Timezone
KST = ZoneInfo("Asia/Seoul")

# Credentials & API keys
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

# Lotte Cinema API constants
LOTTE_EVENT_API_URL = "https://www.lottecinema.co.kr/LCWS/Event/EventData.aspx"
LOTTE_WEB_BASE_URL = "https://www.lottecinema.co.kr/NLCHS/Event"
LOTTE_MOBILE_BASE_URL = "https://www.lottecinema.co.kr/NLCMW/Event"

# Event classification codes:
# 10: HOT, 20: 영화(Movie), 40: 무대인사/시사회, 50: 제휴할인
EVENT_CLASSIFICATION_CODES = ["10", "20", "40", "50"]

# Keywords to identify '무비싸다구' events
_raw_keywords = os.getenv("EVENT_KEYWORDS", "무비싸다구,싸다구")
EVENT_KEYWORDS = [k.strip() for k in _raw_keywords.split(",") if k.strip()]

# Alarm offsets (in minutes before open_time)
ALARM_OFFSETS_MINUTES = [
    {"minutes": 60, "flag": "notified_60m", "label": "1시간 전"},
    {"minutes": 5, "flag": "notified_5m", "label": "5분 전"},
]

# Mapping from EventTypeCode (event_id[2:5]) to Lotte Cinema URL template path
EVENT_TYPE_TEMPLATE_MAP = {
    "101": "EventTemplateInfo",
    "102": "EventTemplateBooking",
    "103": "EventTemplateBuy",
    "104": "EventTemplateSurvey",
    "105": "EventTemplateQuiz",
    "106": "EventTemplateComment",
    "107": "EventTemplateStageGreeting",
    "108": "EventTemplatePreview",
    "109": "EventTemplateGalleryList",
    "111": "EventTemplateButton",
    "112": "EventTemplateStamp",
    "113": "EventTemplateAttendance",
    "114": "EventTemplateEnter",
    "115": "EventTemplateInfo",
    "120": "EventTemplateAllnGift",
    "122": "EventTemplateRandomBox",
    "124": "EventTemplateInitialQuiz",
    "127": "EventTemplateMultiGift",
}


def build_event_urls(event_id: str, event_type_code: str = "") -> dict[str, str]:
    """Build both Mobile Web and PC Web URLs for a given Lotte Cinema EventID."""
    eid = str(event_id).strip()
    type_code = str(event_type_code).strip() or (eid[2:5] if len(eid) >= 5 else "111")
    template = EVENT_TYPE_TEMPLATE_MAP.get(type_code, "EventTemplateButton")
    return {
        "mobile_url": f"{LOTTE_MOBILE_BASE_URL}/{template}?eventId={eid}",
        "web_url": f"{LOTTE_WEB_BASE_URL}/{template}?eventId={eid}",
    }
