from __future__ import annotations

import json
import logging
import re
from typing import Any
import urllib.parse
import urllib.request

from src.config import (
    EVENT_CLASSIFICATION_CODES,
    EVENT_KEYWORDS,
    LOTTE_EVENT_API_URL,
    build_event_urls,
)

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
    "Referer": "https://www.lottecinema.co.kr/NLCMW/Event",
    "Accept": "application/json, text/plain, */*",
}


def normalize_image_url(url: str | None) -> str:
    """Normalize Lotte Cinema CDN image URLs."""
    if not url:
        return ""
    cleaned = url.strip()
    cleaned = re.sub(r"^http://cf\.lottecinema\.co\.kr/+", "https://cf.lottecinema.co.kr/", cleaned)
    cleaned = re.sub(r"^https://cf\.lottecinema\.co\.kr/+", "https://cf.lottecinema.co.kr/", cleaned)
    return cleaned


def post_lotte_event_api(
    payload_dict: dict[str, Any],
    timeout: int = 15,
) -> dict[str, Any]:
    """Send a POST request with `paramList` JSON string to Lotte Cinema EventData.aspx."""
    form_data = urllib.parse.urlencode(
        {"paramList": json.dumps(payload_dict, ensure_ascii=False)}
    ).encode("utf-8")
    req = urllib.request.Request(
        LOTTE_EVENT_API_URL,
        data=form_data,
        headers=DEFAULT_HEADERS,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def is_target_event(event_name: str, keywords: list[str] | None = None) -> bool:
    """Check if an event title matches any of the target keywords (e.g. '무비싸다구')."""
    if not event_name:
        return False
    target_keywords = keywords if keywords is not None else EVENT_KEYWORDS
    normalized_title = re.sub(r"\s+", "", event_name).lower()
    for kw in target_keywords:
        normalized_kw = re.sub(r"\s+", "", kw).lower()
        if normalized_kw and normalized_kw in normalized_title:
            return True
    return False


def fetch_matching_events(
    keywords: list[str] | None = None,
    classification_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch active events from Lotte Cinema across Mobile App (MA) and Web (HO) channels
    and return those matching `keywords` (defaults to '무비싸다구', '싸다구').
    """
    codes = classification_codes or EVENT_CLASSIFICATION_CODES
    seen_ids: dict[str, dict[str, Any]] = {}

    # Query both Mobile App ('MA') and Web ('HO') channels so app-only events are included
    for channel_type, os_type in [("MA", "I"), ("HO", "W")]:
        for code in codes:
            payload = {
                "MethodName": "GetEventLists",
                "channelType": channel_type,
                "osType": os_type,
                "osVersion": DEFAULT_HEADERS["User-Agent"],
                "EventClassificationCode": code,
                "SearchText": "",
                "CinemaID": "",
                "PageNo": 1,
                "PageSize": 100,
                "MemberNo": "0",
            }
            try:
                data = post_lotte_event_api(payload)
                if str(data.get("IsOK", "")).lower() != "true":
                    continue
                items = data.get("Items") or []
                for item in items:
                    eid = str(item.get("EventID", "")).strip()
                    ename = str(item.get("EventName", "")).strip()
                    if eid and eid not in seen_ids and is_target_event(ename, keywords):
                        seen_ids[eid] = item
            except Exception as exc:
                logger.warning(
                    "Failed fetching event list (channel=%s, code=%s): %s",
                    channel_type,
                    code,
                    exc,
                )

    logger.info("Discovered %d matching target event(s) from Lotte Cinema.", len(seen_ids))
    return list(seen_ids.values())


def fetch_event_detail(event_id: str, list_item: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Fetch detailed event metadata and banner image URLs for a specific EventID.
    Tries GetButtonEventDetail, GetInfomationDeliveryEventDetail, and GetStageGreetingEventDetail.
    """
    eid = str(event_id).strip()
    detail_methods = [
        ("GetButtonEventDetail", "ButtonEventDetail"),
        ("GetInfomationDeliveryEventDetail", "InfomationDeliveryEventDetail"),
        ("GetStageGreetingEventDetail", "StageGreetingEventDetail"),
    ]

    merged_detail: dict[str, Any] = {}
    for method_name, response_key in detail_methods:
        payload = {
            "MethodName": method_name,
            "channelType": "MA",
            "osType": "I",
            "osVersion": DEFAULT_HEADERS["User-Agent"],
            "EventID": eid,
            "MemberNo": "0",
        }
        try:
            res = post_lotte_event_api(payload)
            if str(res.get("IsOK", "")).lower() == "true":
                records = res.get(response_key) or []
                if records and isinstance(records, list) and isinstance(records[0], dict):
                    for k, v in records[0].items():
                        if v not in (None, "", []) or k not in merged_detail:
                            merged_detail[k] = v
                    if merged_detail.get("ImgUrl"):
                        break
        except Exception as exc:
            logger.warning("Detail call %s(%s) failed: %s", method_name, eid, exc)

    item = list_item or {}
    title = (
        merged_detail.get("EventName")
        or item.get("EventName")
        or f"롯데시네마 이벤트 ({eid})"
    )
    event_type_code = str(
        item.get("EventTypeCode")
        or (eid[2:5] if len(eid) >= 5 else "111")
    )
    urls = build_event_urls(eid, event_type_code)

    image_urls: list[str] = []
    for img_key in ("ImgUrl", "ImgUrl2", "ImgUrl3"):
        norm_url = normalize_image_url(merged_detail.get(img_key))
        if norm_url and norm_url not in image_urls:
            image_urls.append(norm_url)

    list_img_url = normalize_image_url(
        merged_detail.get("ListImgUrl") or item.get("ImageUrl")
    )
    if not image_urls and list_img_url:
        image_urls.append(list_img_url)

    alt_parts = [
        str(merged_detail.get(k) or "").strip()
        for k in ("ImgAlt", "ImgALT2", "ImgALT3")
    ]
    if item.get("ImageAlt"):
        alt_parts.append(str(item.get("ImageAlt")).strip())
    img_alt = "\n".join(p for p in alt_parts if p)

    progress_start = str(
        merged_detail.get("ProgressStartDate") or item.get("ProgressStartDate") or ""
    ).replace(".", "-")
    progress_end = str(
        merged_detail.get("ProgressEndDate") or item.get("ProgressEndDate") or ""
    ).replace(".", "-")

    return {
        "event_id": eid,
        "title": title.strip(),
        "event_type_code": event_type_code,
        "progress_start_date": progress_start,
        "progress_end_date": progress_end,
        "image_urls": image_urls,
        "list_image_url": list_img_url,
        "img_alt": img_alt,
        "event_contents": str(merged_detail.get("EventContents") or item.get("EventCntnt") or "").strip(),
        "event_notice": str(merged_detail.get("EventNotice") or item.get("EventNtc") or "").strip(),
        "button_setting": merged_detail.get("ButtonSetting") or [],
        "mobile_url": urls["mobile_url"],
        "web_url": urls["web_url"],
    }
