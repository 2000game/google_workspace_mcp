"""
Unit tests for Google Calendar Working Location MCP tools.

Tests the manage_working_location helpers with mocked API responses,
verifying the exact API payloads sent to Google Calendar.
"""

import os
import sys
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import (
    _build_working_location_properties,
    _create_working_location_event_impl,
    _delete_working_location_event_impl,
    _list_working_location_events_impl,
    _update_working_location_event_impl,
    _working_location_time_entry,
)


def _create_mock_service():
    mock_service = Mock()
    mock_service.events().insert().execute = Mock(return_value={})
    mock_service.events().list().execute = Mock(return_value={"items": []})
    mock_service.events().get().execute = Mock(return_value={})
    mock_service.events().patch().execute = Mock(return_value={})
    mock_service.events().delete().execute = Mock(return_value=None)
    return mock_service


class TestWorkingLocationTimeEntry:
    def test_date_only_stays_all_day(self):
        assert _working_location_time_entry("2026-04-05") == {"date": "2026-04-05"}

    def test_datetime_with_timezone(self):
        assert _working_location_time_entry(
            "2026-04-05T09:00:00", timezone="Europe/Berlin"
        ) == {"dateTime": "2026-04-05T09:00:00", "timeZone": "Europe/Berlin"}

    def test_rejects_naive_datetime_without_timezone(self):
        with pytest.raises(ValueError, match="require either a timezone"):
            _working_location_time_entry("2026-04-05T09:00:00")


class TestBuildWorkingLocationProperties:
    def test_home(self):
        assert _build_working_location_properties("home", None, "t") == {
            "type": "homeOffice",
            "homeOffice": {},
        }

    def test_office_and_custom_use_label(self):
        assert _build_working_location_properties("office", "Büro", "t") == {
            "type": "officeLocation",
            "officeLocation": {"label": "Büro"},
        }
        assert _build_working_location_properties("custom", "Café", "t") == {
            "type": "customLocation",
            "customLocation": {"label": "Café"},
        }

    def test_office_requires_label(self):
        with pytest.raises(ValueError, match="label is required"):
            _build_working_location_properties("office", None, "t")

    def test_rejects_invalid_type(self):
        with pytest.raises(ValueError, match="Invalid location_type"):
            _build_working_location_properties("moon", "x", "t")


@pytest.mark.asyncio
async def test_create_all_day_office_with_recurrence():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "wl1",
            "htmlLink": "https://calendar.google.com/event?eid=wl1",
            "start": {"date": "2026-04-06"},
            "end": {"date": "2026-04-07"},
        }
    )

    result = await _create_working_location_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        start_time="2026-04-06",
        end_time="2026-04-07",
        location_type="office",
        label="Büro",
        recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO"],
    )

    call_args = mock_service.events().insert.call_args
    body = call_args[1]["body"]

    assert body["eventType"] == "workingLocation"
    assert body["start"] == {"date": "2026-04-06"}
    assert body["end"] == {"date": "2026-04-07"}
    assert body["workingLocationProperties"] == {
        "type": "officeLocation",
        "officeLocation": {"label": "Büro"},
    }
    assert body["transparency"] == "transparent"
    assert body["visibility"] == "public"
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]
    assert call_args[1]["calendarId"] == "primary"
    assert "officeLocation (Büro)" in result


@pytest.mark.asyncio
async def test_create_timed_home():
    mock_service = _create_mock_service()

    await _create_working_location_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        start_time="2026-04-05T09:00:00",
        end_time="2026-04-05T12:00:00",
        location_type="home",
        timezone="Europe/Berlin",
    )

    body = mock_service.events().insert.call_args[1]["body"]
    assert body["start"] == {
        "dateTime": "2026-04-05T09:00:00",
        "timeZone": "Europe/Berlin",
    }
    assert body["workingLocationProperties"] == {
        "type": "homeOffice",
        "homeOffice": {},
    }


@pytest.mark.asyncio
async def test_list_passes_event_type_filter():
    mock_service = _create_mock_service()
    mock_service.events().list().execute = Mock(
        return_value={
            "items": [
                {
                    "id": "wl1",
                    "start": {"date": "2026-04-06"},
                    "end": {"date": "2026-04-07"},
                    "workingLocationProperties": {
                        "type": "customLocation",
                        "customLocation": {"label": "Café"},
                    },
                }
            ]
        }
    )

    result = await _list_working_location_events_impl(
        service=mock_service,
        user_google_email="user@example.com",
        time_min="2026-04-01",
        time_max="2026-04-30",
    )

    params = mock_service.events().list.call_args[1]
    assert params["eventTypes"] == ["workingLocation"]
    assert params["singleEvents"] is True
    assert "customLocation (Café)" in result
    assert "wl1" in result


@pytest.mark.asyncio
async def test_list_empty():
    result = await _list_working_location_events_impl(
        service=_create_mock_service(), user_google_email="user@example.com"
    )
    assert "No Working Location events found" in result


@pytest.mark.asyncio
async def test_update_label_keeps_existing_type():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "eventType": "workingLocation",
            "workingLocationProperties": {
                "type": "officeLocation",
                "officeLocation": {"label": "Büro"},
            },
        }
    )

    await _update_working_location_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="wl1",
        label="Büro Mombach",
    )

    body = mock_service.events().patch.call_args[1]["body"]
    assert body == {
        "workingLocationProperties": {
            "type": "officeLocation",
            "officeLocation": {"label": "Büro Mombach"},
        }
    }


@pytest.mark.asyncio
async def test_update_rejects_other_event_type():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(return_value={"eventType": "default"})

    with pytest.raises(ValueError, match="not a Working Location event"):
        await _update_working_location_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="x",
            label="Büro",
        )


@pytest.mark.asyncio
async def test_update_without_changes():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={"eventType": "workingLocation"}
    )

    result = await _update_working_location_event_impl(
        service=mock_service, user_google_email="user@example.com", event_id="wl1"
    )
    assert "No changes specified" in result


@pytest.mark.asyncio
async def test_delete_calls_delete_with_correct_ids():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={"eventType": "workingLocation"}
    )

    result = await _delete_working_location_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="wl1",
    )

    mock_service.events().delete.assert_called_with(calendarId="primary", eventId="wl1")
    assert "Successfully deleted Working Location event" in result


@pytest.mark.asyncio
async def test_delete_event_not_found_raises():
    mock_service = _create_mock_service()
    resp = Mock(status=404)
    mock_service.events().get().execute = Mock(
        side_effect=HttpError(resp, b"Not Found")
    )

    with pytest.raises(Exception, match="Event not found"):
        await _delete_working_location_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="missing",
        )
