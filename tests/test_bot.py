from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.config import KST, build_event_urls
from src.crawler import is_target_event, normalize_image_url
from src.main import process_upcoming_alarms
from src.notifier import build_alarm_message
from src.parser import extract_schedules_from_text, normalize_kst_iso


class TestMovieSaddaguBot(unittest.TestCase):
    def test_build_event_urls(self) -> None:
        urls_btn = build_event_urls("201110016926010")
        self.assertIn("EventTemplateButton?eventId=201110016926010", urls_btn["mobile_url"])
        self.assertIn("EventTemplateButton?eventId=201110016926010", urls_btn["web_url"])

        urls_info = build_event_urls("201010016926010")
        self.assertIn("EventTemplateInfo?eventId=201010016926010", urls_info["mobile_url"])

    def test_normalize_image_url(self) -> None:
        raw = "http://cf.lottecinema.co.kr//Media/Event/4fb589c8dd014f65a69d993b7cbe867e.jpg"
        self.assertEqual(
            normalize_image_url(raw),
            "https://cf.lottecinema.co.kr/Media/Event/4fb589c8dd014f65a69d993b7cbe867e.jpg",
        )

    def test_is_target_event(self) -> None:
        self.assertTrue(is_target_event("<글래디에이터 II> 무비싸다구"))
        self.assertTrue(is_target_event("[무비 싸다구] 베놈: 라스트 댄스"))
        self.assertFalse(is_target_event("<트레인스포팅> 스페셜상영회"))

    def test_extract_schedules_from_text(self) -> None:
        sample_text = (
            "1차 오픈: 10월 28일(수) 오후 3시 오픈\n"
            "2차 오픈: 10/30(금) 14:00 선착순 다운로드"
        )
        schedules = extract_schedules_from_text(sample_text, reference_start_date="2026-10-28")
        self.assertEqual(len(schedules), 2)
        self.assertEqual(schedules[0]["open_time_kst"], "2026-10-28T15:00:00+09:00")
        self.assertEqual(schedules[1]["open_time_kst"], "2026-10-30T14:00:00+09:00")

    def test_normalize_kst_iso(self) -> None:
        self.assertEqual(
            normalize_kst_iso("2026-10-28T15:00:00+09:00"),
            "2026-10-28T15:00:00+09:00",
        )
        self.assertEqual(
            normalize_kst_iso("2026-10-28T15:00:00"),
            "2026-10-28T15:00:00+09:00",
        )

    def test_build_alarm_message_60m_and_5m(self) -> None:
        event = {
            "title": "<글래디에이터 II> 무비싸다구",
            "summary": "선착순 2,000원 관람 쿠폰",
            "mobile_url": "https://www.lottecinema.co.kr/NLCMW/Event/EventTemplateButton?eventId=123",
            "web_url": "https://www.lottecinema.co.kr/NLCHS/Event/EventTemplateButton?eventId=123",
        }
        sched = {
            "round_label": "1차 오픈",
            "open_time_kst": "2026-10-28T15:00:00+09:00",
            "price_info": "2,000원 관람권 (선착순 1,500명)",
        }

        text_60, markup_60 = build_alarm_message(event, sched, minutes_before=60)
        self.assertIn("1시간 전 알림", text_60)
        self.assertIn("글래디에이터 II", text_60)
        self.assertEqual(markup_60["inline_keyboard"][0][0]["url"], event["mobile_url"])

        text_5, _ = build_alarm_message(event, sched, minutes_before=5)
        self.assertIn("5분 전 긴급 알림", text_5)

    def test_process_upcoming_alarms_60m_and_5m(self) -> None:
        now = datetime.now(KST).replace(second=0, microsecond=0)
        open_in_58m = (now + timedelta(minutes=58)).isoformat()
        open_in_4m = (now + timedelta(minutes=4)).isoformat()

        state = {
            "ev_60": {
                "event_id": "ev_60",
                "title": "<영화 A> 무비싸다구",
                "mobile_url": "https://www.lottecinema.co.kr/NLCMW/Event",
                "web_url": "https://www.lottecinema.co.kr/NLCHS/Event",
                "schedules": [
                    {
                        "round_label": "1차",
                        "open_time_kst": open_in_58m,
                        "price_info": "2,000원",
                        "notified_60m": False,
                        "notified_5m": False,
                    }
                ],
            },
            "ev_5": {
                "event_id": "ev_5",
                "title": "<영화 B> 무비싸다구",
                "mobile_url": "https://www.lottecinema.co.kr/NLCMW/Event",
                "web_url": "https://www.lottecinema.co.kr/NLCHS/Event",
                "schedules": [
                    {
                        "round_label": "1차",
                        "open_time_kst": open_in_4m,
                        "price_info": "2,000원",
                        "notified_60m": True,
                        "notified_5m": False,
                    }
                ],
            },
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_events_file = Path(tmp_dir) / "events.json"
            with patch("src.main.EVENTS_FILE", temp_events_file):
                sent = process_upcoming_alarms(
                    state,
                    lookahead_minutes=30,
                    wait_for_exact_time=False,
                    dry_run=True,
                )
                self.assertEqual(sent, 2)
                self.assertTrue(state["ev_60"]["schedules"][0]["notified_60m"])
                self.assertFalse(state["ev_60"]["schedules"][0]["notified_5m"])
                self.assertTrue(state["ev_5"]["schedules"][0]["notified_5m"])

                # Second run should not send duplicate notifications
                sent_again = process_upcoming_alarms(
                    state,
                    lookahead_minutes=30,
                    wait_for_exact_time=False,
                    dry_run=True,
                )
                self.assertEqual(sent_again, 0)


if __name__ == "__main__":
    unittest.main()
