from __future__ import annotations

import base64
from datetime import datetime
import io
import json
import logging
import re
from typing import Any
import urllib.error
import urllib.request

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]

from src.config import GEMINI_API_KEY, GEMINI_MODEL, KST

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def download_and_prepare_image(url: str, max_dimension: int = 3072) -> tuple[str, str] | None:
    """
    Download an event banner image and return (mime_type, base64_data).
    If Pillow is installed, resizes any dimension exceeding `max_dimension`
    while preserving aspect ratio.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.lottecinema.co.kr/",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")

        mime_type = "image/png" if "png" in content_type.lower() or url.lower().endswith(".png") else "image/jpeg"

        if Image is not None:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                width, height = img.size
                if width > max_dimension or height > max_dimension:
                    scale = min(max_dimension / width, max_dimension / height)
                    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                    img = img.convert("RGB").resize(new_size, Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=88)
                    raw_bytes = buf.getvalue()
                    mime_type = "image/jpeg"
                else:
                    fmt = (img.format or "JPEG").upper()
                    mime_type = "image/png" if fmt == "PNG" else "image/jpeg"

        b64_str = base64.b64encode(raw_bytes).decode("ascii")
        return mime_type, b64_str
    except Exception as exc:
        logger.warning("Failed to download/prepare image %s: %s", url, exc)
        return None


def normalize_kst_iso(dt_str: str) -> str | None:
    """Validate and normalize an ISO-8601 datetime string into YYYY-MM-DDTHH:MM:00+09:00."""
    if not dt_str:
        return None
    cleaned = dt_str.strip()
    try:
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+09:00"
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        else:
            dt = dt.astimezone(KST)
        return dt.replace(second=0, microsecond=0).isoformat()
    except ValueError:
        return None


def extract_schedules_from_text(
    text: str,
    reference_start_date: str = "",
) -> list[dict[str, Any]]:
    """
    Fallback regex parser for Korean date/time expressions such as:
    - '10월 28일(수) 오후 3시'
    - '10/28(수) 15:00 오픈'
    - '10.28 14시'
    """
    if not text:
        return []

    ref_year = datetime.now(KST).year
    if reference_start_date and len(reference_start_date) >= 4 and reference_start_date[:4].isdigit():
        ref_year = int(reference_start_date[:4])

    pattern = re.compile(
        r"(?:(?P<year>20\d{2})[\.\-/년\s]+)?"
        r"(?P<month>1[0-2]|0?[1-9])\s*(?:월|[\./\-])\s*"
        r"(?P<day>[12]\d|3[01]|0?[1-9])\s*일?"
        r"(?:\s*\([월화수목금토일]\))?\s*"
        r"(?P<ampm>오전|오후|낮|밤|AM|PM|am|pm)?\s*"
        r"(?P<hour>[012]?\d)\s*(?:시|:)\s*"
        r"(?:(?P<minute>[0-5]?\d)\s*분?)?"
    )

    schedules: list[dict[str, Any]] = []
    seen_times: set[str] = set()

    for match in pattern.finditer(text):
        year = int(match.group("year") or ref_year)
        month = int(match.group("month"))
        day = int(match.group("day"))
        ampm = (match.group("ampm") or "").upper()
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)

        if ampm in ("오후", "낮", "밤", "PM") and hour < 12:
            hour += 12
        elif ampm in ("오전", "AM") and hour == 12:
            hour = 0

        try:
            dt = datetime(year, month, day, hour, minute, 0, tzinfo=KST)
            iso_str = dt.isoformat()
            if iso_str not in seen_times:
                seen_times.add(iso_str)
                schedules.append(
                    {
                        "round_label": f"{len(schedules) + 1}차 오픈" if len(schedules) > 0 else "티켓 오픈",
                        "open_time_kst": iso_str,
                        "price_info": "선착순 할인 관람권",
                    }
                )
        except ValueError:
            continue

    return schedules


def parse_event_with_gemini(
    event_detail: dict[str, Any],
    api_key: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    """
    Use Gemini Vision API to read the Lotte Cinema '무비싸다구' event banner image(s)
    and extract the exact coupon drop date/time(s) in KST.
    """
    key = (api_key if api_key is not None else GEMINI_API_KEY).strip()
    if not key:
        logger.warning("GEMINI_API_KEY is not set; skipping Gemini Vision extraction.")
        return None

    primary_model = (model_name or GEMINI_MODEL or "gemini-2.5-flash").strip()
    candidate_models = [primary_model]
    if "gemini-2.0-flash" not in candidate_models:
        candidate_models.append("gemini-2.0-flash")

    now_kst_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    prompt = f"""당신은 롯데시네마 '무비싸다구' (선착순 영화 관람 쿠폰 다운로드 이벤트) 일정 추출기입니다.
제공된 이벤트 상세 이미지와 메타데이터를 확인하여, 선착순 쿠폰 다운로드가 시작(오픈)되는 정확한 날짜와 시간을 모두 추출하세요.

[이벤트 메타데이터]
- 현재 시각(KST): {now_kst_str}
- 이벤트 제목: {event_detail.get("title", "")}
- 이벤트 진행 기간: {event_detail.get("progress_start_date", "")} ~ {event_detail.get("progress_end_date", "")}
- 이미지 대체 텍스트(Alt): {event_detail.get("img_alt", "")}
- 이벤트 본문/유의사항: {event_detail.get("event_contents", "")} / {event_detail.get("event_notice", "")}

[추출 규칙]
1. 이미지 내 '오픈', '선착순', '다운로드 시작', '1차/2차', '오후 3시(15:00)', '오전 11시(11:00)', '14시' 등의 날짜 및 시각 표기를 정확히 찾으세요.
2. 연도가 생략된 경우(예: '10/28(수) 오후 3시') 이벤트 진행 기간({event_detail.get("progress_start_date", "")})의 연도를 사용하세요.
3. 모든 시각은 한국 표준시(KST, +09:00) 기준 ISO-8601 형식(`YYYY-MM-DDTHH:MM:00+09:00`)으로 변환하세요. (예: 오후 3시 -> `15:00:00+09:00`, 오후 2시 -> `14:00:00+09:00`).
4. 1차, 2차 등 여러 번 오픈하는 경우 `schedules` 배열에 각각 별도 항목으로 모두 포함하세요. 같은 시각에 여러 가격대(예: 2,000원 / 4,000원)가 동시에 오픈하면 하나의 시각 항목으로 묶고 `price_info`에 함께 적으세요.
"""

    parts: list[dict[str, Any]] = [{"text": prompt}]
    for img_url in event_detail.get("image_urls") or []:
        prepared = download_and_prepare_image(img_url)
        if prepared:
            mime_type, b64_data = prepared
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": b64_data,
                    }
                }
            )

    response_schema = {
        "type": "OBJECT",
        "properties": {
            "movie_title": {
                "type": "STRING",
                "description": "영화 제목 (예: 글래디에이터 II)",
            },
            "summary": {
                "type": "STRING",
                "description": "이벤트 혜택 한줄 요약 (예: 선착순 2,000원 / 4,000원 관람 쿠폰)",
            },
            "schedules": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "round_label": {
                            "type": "STRING",
                            "description": "차수 또는 구분 (예: 1차 오픈, 선착순 쿠폰 오픈)",
                        },
                        "open_time_kst": {
                            "type": "STRING",
                            "description": "ISO 8601 KST 오픈 일시 (YYYY-MM-DDTHH:MM:00+09:00)",
                        },
                        "price_info": {
                            "type": "STRING",
                            "description": "할인 금액 및 수량 정보 (예: 2,000원 관람권 1,000명)",
                        },
                    },
                    "required": ["round_label", "open_time_kst", "price_info"],
                },
            },
        },
        "required": ["movie_title", "summary", "schedules"],
    }

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
        },
    }

    encoded_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for model in candidate_models:
        url = f"{GEMINI_API_BASE}/{model}:generateContent?key={key}"
        req = urllib.request.Request(
            url,
            data=encoded_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            candidates = body.get("candidates") or []
            if not candidates:
                continue
            text_out = (
                candidates[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            if not text_out:
                continue
            return json.loads(text_out)
        except urllib.error.HTTPError as http_err:
            err_body = http_err.read().decode("utf-8", errors="ignore")
            logger.warning("Gemini model %s returned HTTP %s: %s", model, http_err.code, err_body[:300])
        except Exception as exc:
            logger.warning("Gemini request failed with model %s: %s", model, exc)

    return None


def extract_event_schedules(event_detail: dict[str, Any]) -> dict[str, Any]:
    """
    Extract structured schedule information from an event detail record.
    1) Uses structured `api_schedules` from `GetSpeedEventDetailMulti` when available.
    2) Otherwise tries Gemini Vision on the banner image, then falls back to regex text extraction.
    Returns an enriched event record ready for persistence in `data/events.json`.
    """
    normalized_schedules: list[dict[str, Any]] = []
    seen_open_times: set[str] = set()

    movie_title = event_detail.get("movie_title") or event_detail.get("title", "")
    summary = "선착순 무비싸다구 할인 쿠폰"

    # 1. Use structured API schedules (e.g. from GetSpeedEventDetailMulti) if present
    for item in event_detail.get("api_schedules") or []:
        iso_time = normalize_kst_iso(item.get("open_time_kst", ""))
        if iso_time and iso_time not in seen_open_times:
            seen_open_times.add(iso_time)
            price_info = item.get("price_info") or summary
            summary = price_info
            normalized_schedules.append(
                {
                    "round_label": item.get("round_label") or "무비싸다구 오픈",
                    "open_time_kst": iso_time,
                    "price_info": price_info,
                    "notified_60m": False,
                    "notified_5m": False,
                }
            )

    # 2. If API schedules are not present, call Gemini Vision on the banner image
    if not normalized_schedules:
        gemini_result = parse_event_with_gemini(event_detail)
        if gemini_result:
            movie_title = gemini_result.get("movie_title") or movie_title
            summary = gemini_result.get("summary") or summary
            for item in gemini_result.get("schedules") or []:
                iso_time = normalize_kst_iso(item.get("open_time_kst", ""))
                if iso_time and iso_time not in seen_open_times:
                    seen_open_times.add(iso_time)
                    normalized_schedules.append(
                        {
                            "round_label": item.get("round_label") or "티켓 오픈",
                            "open_time_kst": iso_time,
                            "price_info": item.get("price_info") or summary,
                            "notified_60m": False,
                            "notified_5m": False,
                        }
                    )

    if not normalized_schedules:
        combined_text = "\n".join(
            filter(
                None,
                [
                    event_detail.get("img_alt", ""),
                    event_detail.get("event_contents", ""),
                    event_detail.get("event_notice", ""),
                ],
            )
        )
        fallback_items = extract_schedules_from_text(
            combined_text,
            reference_start_date=event_detail.get("progress_start_date", ""),
        )
        for item in fallback_items:
            iso_time = item["open_time_kst"]
            if iso_time not in seen_open_times:
                seen_open_times.add(iso_time)
                normalized_schedules.append(
                    {
                        "round_label": item["round_label"],
                        "open_time_kst": iso_time,
                        "price_info": item["price_info"],
                        "notified_60m": False,
                        "notified_5m": False,
                    }
                )

    return {
        "event_id": event_detail["event_id"],
        "title": event_detail["title"],
        "movie_title": movie_title,
        "summary": summary,
        "progress_start_date": event_detail.get("progress_start_date", ""),
        "progress_end_date": event_detail.get("progress_end_date", ""),
        "mobile_url": event_detail["mobile_url"],
        "web_url": event_detail["web_url"],
        "image_urls": event_detail.get("image_urls", []),
        "schedules": normalized_schedules,
        "updated_at_kst": datetime.now(KST).replace(microsecond=0).isoformat(),
    }
