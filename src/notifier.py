from __future__ import annotations

from datetime import datetime
import html
import json
import logging
from typing import Any
import urllib.error
import urllib.request

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]

from src.config import KST, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]


def format_korean_datetime(iso_str: str) -> str:
    """Format an ISO-8601 KST timestamp into a readable Korean date/time string."""
    dt = datetime.fromisoformat(iso_str).astimezone(KST)
    wd = WEEKDAYS_KO[dt.weekday()]
    return f"{dt.year}.{dt.month:02d}.{dt.day:02d}({wd}) {dt.hour:02d}:{dt.minute:02d}"


def build_alarm_message(
    event_record: dict[str, Any],
    schedule_item: dict[str, Any],
    minutes_before: int,
) -> tuple[str, dict[str, Any]]:
    """
    Build the HTML message text and Telegram inline keyboard markup
    for either the 60-minute (1 hour) or 5-minute alarm.
    """
    title = html.escape(event_record.get("title") or "무비싸다구 이벤트")
    round_label = html.escape(schedule_item.get("round_label") or "티켓 오픈")
    price_info = html.escape(
        schedule_item.get("price_info")
        or event_record.get("summary")
        or "선착순 할인 관람권"
    )
    open_time_str = format_korean_datetime(schedule_item["open_time_kst"])
    mobile_url = event_record.get("mobile_url", "https://www.lottecinema.co.kr/NLCMW/Event")
    web_url = event_record.get("web_url", "https://www.lottecinema.co.kr/NLCHS/Event")

    if minutes_before >= 60:
        header = "⏰ <b>[무비싸다구 1시간 전 알림]</b>"
        urgency = "1시간 뒤 무비싸다구 선착순 쿠폰이 오픈됩니다!"
        tip = "💡 <i>미리 롯데시네마 앱 업데이트 및 로그인 상태를 확인해 두세요.</i>"
    else:
        header = f"🚨 <b>[무비싸다구 {minutes_before}분 전 긴급 알림!]</b>"
        urgency = f"🔥 <b>잠시 후 {minutes_before}분 뒤 정각에 선착순 오픈됩니다! 지금 대기하세요!</b>"
        tip = "⚡ <i>지금 바로 롯데시네마 앱을 열고 이벤트 페이지에서 대기하세요!</i>"

    text = (
        f"{header}\n\n"
        f"{urgency}\n\n"
        f"🎬 <b>이벤트:</b> {title}\n"
        f"📌 <b>구분:</b> {round_label}\n"
        f"🎟 <b>혜택:</b> {price_info}\n"
        f"🕒 <b>오픈 시각:</b> <code>{open_time_str} (KST)</code>\n\n"
        f"🔗 <a href=\"{html.escape(mobile_url)}\">모바일 이벤트 페이지</a> | "
        f"<a href=\"{html.escape(web_url)}\">PC 이벤트 페이지</a>\n\n"
        f"{tip}"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📲 모바일 이벤트 바로가기", "url": mobile_url},
                {"text": "💻 PC 웹 바로가기", "url": web_url},
            ]
        ]
    }
    return text, reply_markup


def send_telegram_message(
    text: str,
    reply_markup: dict[str, Any] | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Send an HTML-formatted message via the Telegram Bot API."""
    token = (bot_token if bot_token is not None else TELEGRAM_BOT_TOKEN).strip()
    target_chat = (chat_id if chat_id is not None else TELEGRAM_CHAT_ID).strip()

    if dry_run:
        logger.info("[DRY-RUN] Would send Telegram message:\n%s", text)
        return True

    if not token or not target_chat:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        if _requests is not None:
            resp = _requests.post(
                url,
                json=payload,
                headers={"Connection": "close"},
                timeout=(5, 15),
            )
            if resp.status_code != 200:
                logger.error("Telegram API HTTP error (%s): %s", resp.status_code, resp.text)
                return False
            data = resp.json()
        else:
            encoded_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=encoded_payload,
                headers={
                    "Content-Type": "application/json",
                    "Connection": "close",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as raw_resp:
                data = json.loads(raw_resp.read().decode("utf-8"))

        if not data.get("ok"):
            logger.error("Telegram response not ok: %s", data)
            return False
        logger.info("Telegram notification sent successfully to chat_id=%s.", target_chat)
        return True
    except urllib.error.HTTPError as http_err:
        err_body = http_err.read().decode("utf-8", errors="ignore")
        logger.error("Telegram API HTTP error (%s): %s", http_err.code, err_body)
        return False
    except Exception as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


def send_test_notification(dry_run: bool = False) -> bool:
    """Send a test Telegram message to verify bot token and chat ID configuration."""
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    sample_event = {
        "title": "<테스트 영화> 무비싸다구",
        "summary": "선착순 2,000원 관람 쿠폰 (연동 테스트)",
        "mobile_url": "https://www.lottecinema.co.kr/NLCMW/Event",
        "web_url": "https://www.lottecinema.co.kr/NLCHS/Event",
    }
    sample_schedule = {
        "round_label": "테스트 알림 (1시간 전 & 5분 전 설정됨)",
        "open_time_kst": datetime.now(KST).replace(second=0, microsecond=0).isoformat(),
        "price_info": f"봇 정상 작동 확인 ({now_str})",
    }
    text, markup = build_alarm_message(sample_event, sample_schedule, minutes_before=5)
    return send_telegram_message(text, reply_markup=markup, dry_run=dry_run)
