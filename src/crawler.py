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
    KST,
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
    Fetch active events from Lotte Cinema across:
    1) `GetEventSummaryLists` (Mobile Web 'MW' & Mobile App 'MA' - where '121: 스피드형_멀티' 무비싸다구 events live)
    2) `GetEventLists` across classification codes
    and return those matching `keywords` (defaults to '무비싸다구', '싸다구').
    """
    codes = classification_codes or EVENT_CLASSIFICATION_CODES
    seen_ids: dict[str, dict[str, Any]] = {}

    # 1. Query GetEventSummaryLists on Mobile Web (MW) and Mobile App (MA)
    for channel_type in ("MW", "MA", "HO"):
        summary_payload = {
            "MethodName": "GetEventSummaryLists",
            "channelType": channel_type,
            "osType": "W" if channel_type == "HO" else "I",
            "osVersion": DEFAULT_HEADERS["User-Agent"],
            "MemberNo": "0",
        }
        try:
            data = post_lotte_event_api(summary_payload)
            if str(data.get("IsOK", "")).lower() == "true":
                for item in data.get("Items") or []:
                    eid = str(item.get("EventID", "")).strip()
                    ename = str(item.get("EventName", "")).strip()
                    if eid and eid not in seen_ids and is_target_event(ename, keywords):
                        seen_ids[eid] = item
        except Exception as exc:
            logger.warning("Failed GetEventSummaryLists (channel=%s): %s", channel_type, exc)

    # 2. Query GetEventLists across classification codes
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


def _parse_speed_multi_schedules(
    target_event_id: str,
    item_groups: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """
    Extract exact coupon open timestamp(s), price tiers, and remaining/used counts
    from `GetSpeedEventDetailMulti`'s `ItemGroup` array for `target_event_id`.
    """
    grouped_by_time: dict[str, dict[str, Any]] = {}
    movie_name = ""

    for group in item_groups:
        if str(group.get("EventID", "")).strip() != target_event_id:
            continue
        for sub_item in group.get("Items") or []:
            if not movie_name and sub_item.get("MovieNm"):
                movie_name = str(sub_item["MovieNm"]).strip()

            start_date = str(sub_item.get("ProgressStartDate") or "").strip().replace(".", "-")
            start_time = str(sub_item.get("ProgressStartTime") or "15:00:00").strip()
            if not start_date:
                continue
            if len(start_time) == 5:
                start_time += ":00"
            iso_kst = f"{start_date}T{start_time}+09:00"

            raw_price = str(sub_item.get("DisplayCouponName") or "").strip()
            if raw_price.isdigit():
                price_str = f"{int(raw_price):,}원 관람권"
            elif raw_price:
                price_str = f"{raw_price} 관람권"
            else:
                price_str = "할인 관람권"

            usable_max = str(sub_item.get("CpnUsableMaxCnt") or "").strip()
            used_cnt = str(sub_item.get("CpnUsedCnt") or "").strip()
            cur_yn = str(sub_item.get("CpnCurYn") or "").strip()

            status_suffix = ""
            if usable_max.isdigit() and used_cnt.isdigit():
                max_n = int(usable_max)
                used_n = int(used_cnt)
                if cur_yn == "0" or used_n >= max_n:
                    status_suffix = f" (선착순 {max_n:,}명 - 마감)"
                else:
                    remain_n = max(0, max_n - used_n)
                    status_suffix = f" (선착순 {max_n:,}명 / 잔여 {remain_n:,}매)"

            tier_desc = f"{price_str}{status_suffix}"

            if iso_kst not in grouped_by_time:
                grouped_by_time[iso_kst] = {
                    "round_label": f"{len(grouped_by_time) + 1}차 오픈" if grouped_by_time else "무비싸다구 오픈",
                    "open_time_kst": iso_kst,
                    "tiers": [tier_desc],
                }
            else:
                if tier_desc not in grouped_by_time[iso_kst]["tiers"]:
                    grouped_by_time[iso_kst]["tiers"].append(tier_desc)

    schedules: list[dict[str, Any]] = []
    for iso_kst, info in sorted(grouped_by_time.items()):
        schedules.append(
            {
                "round_label": info["round_label"],
                "open_time_kst": info["open_time_kst"],
                "price_info": " / ".join(info["tiers"]),
                "notified_60m": False,
                "notified_5m": False,
            }
        )

    return schedules, movie_name


def fetch_event_detail(event_id: str, list_item: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Fetch detailed event metadata, structured schedules (for 121 SpeedMulti events),
    and banner image URLs for a specific EventID.
    """
    eid = str(event_id).strip()
    item = list_item or {}
    event_type_code = str(
        item.get("EventTypeCode")
        or (eid[2:5] if len(eid) >= 5 else "121")
    )

    merged_detail: dict[str, Any] = {}
    api_schedules: list[dict[str, Any]] = []
    api_movie_name = ""

    # 1. Try GetSpeedEventDetailMulti first for '121' (무비싸다구 스피드형_멀티) events
    try:
        speed_res = post_lotte_event_api(
            {
                "MethodName": "GetSpeedEventDetailMulti",
                "channelType": "MW",
                "osType": "I",
                "osVersion": DEFAULT_HEADERS["User-Agent"],
                "EventID": eid,
                "MainEventID": "",
            }
        )
        if str(speed_res.get("IsOK", "")).lower() == "true":
            records = speed_res.get("SpeedEventDetail") or []
            if records and isinstance(records[0], dict):
                merged_detail.update(records[0])
                item_groups = records[0].get("ItemGroup") or []
                if item_groups:
                    api_schedules, api_movie_name = _parse_speed_multi_schedules(eid, item_groups)
    except Exception as exc:
        logger.warning("GetSpeedEventDetailMulti(%s) failed: %s", eid, exc)

    # 2. Also try standard detail endpoints if needed
    if not merged_detail.get("ImgUrl"):
        detail_methods = [
            ("GetButtonEventDetail", "ButtonEventDetail"),
            ("GetInfomationDeliveryEventDetail", "InfomationDeliveryEventDetail"),
            ("GetStageGreetingEventDetail", "StageGreetingEventDetail"),
        ]
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

    title = (
        merged_detail.get("EventName")
        or item.get("EventName")
        or f"롯데시네마 이벤트 ({eid})"
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
        "movie_title": api_movie_name or title.strip(),
        "event_type_code": event_type_code,
        "progress_start_date": progress_start,
        "progress_end_date": progress_end,
        "image_urls": image_urls,
        "list_image_url": list_img_url,
        "img_alt": img_alt,
        "event_contents": str(merged_detail.get("EventContents") or item.get("EventCntnt") or "").strip(),
        "event_notice": str(merged_detail.get("EventNotice") or item.get("EventNtc") or "").strip(),
        "button_setting": merged_detail.get("ButtonSetting") or [],
        "api_schedules": api_schedules,
        "mobile_url": urls["mobile_url"],
        "web_url": urls["web_url"],
    }
