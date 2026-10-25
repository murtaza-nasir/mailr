"""Shared helpers, from test_speakr_record.py."""



AUDIO = b"ID3\x03\x00\x00\x00" + b"\x00" * 4096


URL = "/api/v1/speakr/recordings"


def _form(**extra):
    data = {"title": "Advising: Mara", "participants": "Mara Lee, Rowan", "meeting_date": "2026-09-30T14:00:00-05:00",
            "notes": "## Agenda\n- thesis plan", "folder": "Advising", "tags": "Work, 2", **extra}
    return {"data": data, "files": {"file": ("mara.m4a", AUDIO, "audio/mp4")}}
