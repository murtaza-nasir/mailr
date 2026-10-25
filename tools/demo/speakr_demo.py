"""Load the demo recordings into a Speakr DEVELOPMENT server. Internal.

    cd backend && SPEAKR_TOKEN=<dev token>.venv/bin/python../tools/demo/speakr_demo.py \
        --url http://localhost:8899 [--token-file FILE] [--now 2026-10-02T10:40]
        [--reset] [--audio-dir DIR] [--scripts-dir DIR] [--no-wait] [--summary speakr|scripted]

Four short, fictional meetings of the demo world (mailr/dev/demo_content.py), dated relative to
--now (default: the real now; use the --now of `mailr demo-seed`) so the mailr matcher links them
to the demo calendar: the Lumen kickoff call, the Bluefin content review, today's studio standup
and the Casa Verde catalogue call. Each is uploaded with its title, meeting date, participants,
notes and a "[mailr-demo]" marker in the notes; --reset first deletes the recordings that carry
the marker (the token needs the "delete" scope for that), so running it again gives the same set.

The token comes from $SPEAKR_TOKEN, --token-file (a plain token, or JSON with a "token" key) or
--token, in that order of preference; the first two keep it off the command line.

Audio: without --audio-dir each recording is a few seconds of quiet tone (a WAV made here), so
Speakr has a file to keep; its transcript will be empty. For real transcripts, read each script
aloud (--scripts-dir writes them out as <slug>.txt; a TTS reading with one voice per speaker, see
VOICES, works well), save <slug>.wav/.mp3/.m4a in a folder and pass --audio-dir: Speakr
transcribes them with its own ASR settings and diarization (min/max speakers = the script's).

Unless --no-wait, the loader then waits for each transcript, names Speakr's speaker labels by
matching the transcribed segments against the script lines (Alex is "me"), and has Speakr
summarise again with the names. --summary scripted replaces Speakr's summary with the one below
(also the fallback when Speakr produced none).

Never point this at a production Speakr: it creates and (with --reset) deletes recordings.
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import math
import os
import re
import struct
import sys
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

MARK = "[mailr-demo]"
TZ = ZoneInfo("America/Chicago")

# Dates follow the demo seed's calendar (mailr/dev/demo.py, _events) for a --now on a Friday such as
# 2026-10-02T10:40: the Lumen kickoff on Monday, after Dana's brief; the Bluefin call in the slot of
# Wednesday's "Bluefin content review"; Casa Verde yesterday ("Thanks for the call yesterday" in
# Lucía's mail); this morning's occurrence of the recurring studio standup. "days" counts from --now.
# For TTS readings (one voice per speaker): an OpenAI voice name and a pitch shift in semitones,
# spaced so that Speakr's diarization tells the speakers of one meeting apart.
VOICES = {"Marco Silva": ("onyx", -2), "Jordan Lee": ("fable", -2), "Alex Rivera": ("echo", 0),
          "Sam Okafor": ("alloy", 0), "Dana Whitfield": ("nova", 6), "Mei Tanaka": ("nova", 6),
          "Lucía Romero": ("shimmer", 5), "Priya Shah": ("shimmer", 8)}

RECORDINGS = [
    {"slug": "lumen-kickoff", "title": "Lumen Coffee: kickoff call", "days": -4, "at": (10, 0), "minutes": 32,
     "participants": ["Dana Whitfield", "Jordan Lee", "Alex Rivera"],
     "notes": "Wholesale launch drives the timeline. Van wrap in scope, last.",
     "summary": ("## Lumen Coffee kickoff\n\n- Lumen is moving from one café to wholesale; the logo must work on bags, "
                 "cups and the delivery van.\n- Two directions due before the board meeting.\n- Budget and timeline "
                 "as in the brief.\n\n### Action items\n- Alex: share two directions by Thursday.\n- Mei: moodboard "
                 "by Monday.\n- Dana: send the updated packaging guidelines."),
     "script": ("Dana: Thanks for making time this morning. The short version is that we are going wholesale in the spring.\n"
                "Alex: Congratulations. So the new mark has to work on a coffee bag, a paper cup and the side of a van, "
                "not just on the café sign.\n"
                "Dana: Exactly. The current logo falls apart when it gets small, and it looks cold next to the big chains.\n"
                "Jordan: What does the board need to see, and when do they meet?\n"
                "Dana: They meet next week. I would love to show them a first direction, even a rough one.\n"
                "Jordan: Then we will prepare two directions and share them before the board meets.\n"
                "Alex: Mei will start a moodboard today, warm colours, hand drawn type, nothing too polished.\n"
                "Alex: One question from the brief. Is the van wrap part of this phase?\n"
                "Dana: It is in scope, but it can come last. The bags matter first.\n"
                "Dana: I will send our new packaging guidelines this week, with the new bag sizes.\n"
                "Jordan: Great. I will put the review on the calendar once we have a date from you.\n"
                "Dana: Perfect. Thank you both.")},
    {"slug": "bluefin-sync", "title": "Bluefin content review", "days": -2, "at": (13, 0), "minutes": 24,
     "participants": ["Marco Silva", "Jordan Lee", "Sam Okafor", "Alex Rivera"],
     "notes": "Content entry moves a week earlier; sign-off needed on templates.",
     "summary": ("## Bluefin content review\n\n- Content entry moves one week earlier.\n- Product page templates need "
                 "design sign-off within two days.\n- Pricing page in review (PR #142).\n\n### Action items\n"
                 "- Alex: sign off on the timeline.\n- Sam: fix the plan card breakpoint.\n"
                 "- Marco: confirm the quote for the landing page."),
     "script": ("Marco: Good news first. Our content team can start a full week earlier than planned.\n"
                "Jordan: That helps the launch, but it moves our deadline too. We need design sign-off on the "
                "product page templates within two days.\n"
                "Alex: That is fine. The templates are nearly done. I will review the revised timeline today and sign off.\n"
                "Marco: How is the pricing page coming along?\n"
                "Sam: It is in review now, pull request one forty-two. One problem: the plan cards wrap badly at "
                "around nine hundred pixels.\n"
                "Alex: I will look at that breakpoint with you this afternoon.\n"
                "Sam: Thanks. The fix should be small, probably just the card width.\n"
                "Jordan: Marco, did you have a chance to look at our quote for the landing page?\n"
                "Marco: I read it last night. I need to confirm it with finance, and I will get back to you by Monday.\n"
                "Jordan: Great. Then we can hold the team for the start date.\n"
                "Marco: Sounds good. Same time next week.")},
    {"slug": "standup", "title": "Studio standup", "days": 0, "at": (9, 30), "minutes": 12,
     "participants": ["Priya Shah", "Jordan Lee", "Sam Okafor", "Mei Tanaka", "Alex Rivera"],
     "notes": "Short one; Mei out after lunch.",
     "summary": ("## Studio standup\n\n- Mei finished the Bluefin hero options (A: trail first, B: products first).\n"
                 "- Sam is fixing a failed preview build.\n- Alex presents the Lumen concepts at Thursday's review."),
     "script": ("Priya: Good morning, everyone. Quick round, please, we have a busy day.\n"
                "Mei: Two hero options for the Bluefin homepage are in Alex's inbox. Option A leads with the trail "
                "photos, option B with the products. I lean towards A. I am out after lunch today.\n"
                "Sam: The preview build failed overnight on a type error. I am fixing it now, it should be green "
                "within the hour.\n"
                "Jordan: Bluefin content entry starts a week early, so the templates need sign-off soon. "
                "I will chase Marco about the quote.\n"
                "Alex: I am presenting the Lumen concepts at the studio review on Thursday, and I have the Lumen "
                "moodboard session this morning.\n"
                "Priya: Thanks, everyone. Alex, twenty minutes is plenty for Lumen. Let's go.")},
    {"slug": "casa-verde", "title": "Casa Verde catalogue call", "days": -1, "at": (15, 0), "minutes": 18,
     "participants": ["Lucía Romero", "Alex Rivera"],
     "notes": "Catalogue grows from 16 to 24 pages; updated quote before Friday.",
     "summary": ("## Casa Verde catalogue\n\n- The catalogue grows from 16 to 24 pages, with a new outdoor section.\n"
                 "- Photos arrive in three days, same photographer as last season.\n\n### Action items\n"
                 "- Alex: send an updated quote before Friday."),
     "script": ("Lucía: Hi Alex, thank you for calling. We have news about the new catalogue.\n"
                "Alex: Hi Lucía. Good news, I hope?\n"
                "Lucía: Very good. We want the outdoor collection in this catalogue too, with its own section.\n"
                "Alex: That takes us from sixteen to twenty-four pages. The layout system can handle it, "
                "but the quote will change.\n"
                "Lucía: Of course. When can we have the photos to you? The photographer is the same as last season.\n"
                "Alex: Whenever they are ready. When do you expect them?\n"
                "Lucía: In three days. Could you send an updated quote before Friday?\n"
                "Alex: Yes, I will send it this week, with the new page count and a revised schedule.\n"
                "Lucía: Wonderful. Thank you, Alex.")},
]


def tone(seconds: float = 4.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(800 * math.sin(2 * math.pi * 220 * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


def _token(a: argparse.Namespace) -> str:
    if os.environ.get("SPEAKR_TOKEN"):
        return os.environ["SPEAKR_TOKEN"].strip()
    if a.token_file:
        raw = a.token_file.read_text().strip()
        return json.loads(raw)["token"] if raw.startswith("{") else raw
    if a.token:
        return a.token
    sys.exit("no token: set SPEAKR_TOKEN, or pass --token-file or --token")


def _now(v: str | None) -> datetime:
    if not v:
        return datetime.now(TZ)
    t = datetime.fromisoformat(v)
    return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)


def _lines(r: dict) -> list[tuple[str, str]]:
    """(participant, text) per script line; a line starts with the speaker's first name."""
    first = {n.split()[0]: n for n in r["participants"]}
    return [(first[w.strip()], t.strip()) for w, t in (ln.split(":", 1) for ln in r["script"].strip().splitlines())]


def _norm(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", t.lower()))


def _summary(c: httpx.Client, rid: int) -> str:
    s = c.get(f"/api/v1/recordings/{rid}/summary")
    return ((s.json() or {}).get("summary") or "") if s.status_code == 200 else ""


def _wait(c: httpx.Client, rid: int, want_summary: bool, timeout: float = 900, old: str = "") -> dict:
    """Poll the recording until Speakr has finished it (and, with want_summary, written a summary
    other than `old`)."""
    end = time.monotonic() + timeout
    while True:
        rec = c.get(f"/api/v1/recordings/{rid}", params={"format": "minimal"}).json()
        st = str(rec.get("status") or "").upper()
        if st == "FAILED":
            sys.exit(f"  {rid}: Speakr failed: {rec.get('error_message') or rec}")
        if st == "COMPLETED":
            if not want_summary:
                return rec
            text = _summary(c, rid)
            if text and text != old:
                return rec
        if time.monotonic() > end:
            sys.exit(f"  {rid}: still {st or 'unknown'} after {timeout:.0f}s")
        time.sleep(5)


def _segments(c: httpx.Client, rid: int) -> list[dict]:
    t = c.get(f"/api/v1/recordings/{rid}/transcript", params={"format": "json"})
    if t.status_code != 200:
        return []
    body = t.json()
    segs = (body.get("segments") or body.get("transcription") or []) if isinstance(body, dict) else body
    if isinstance(segs, str):
        try:
            segs = json.loads(segs)
        except ValueError:
            return []
    return [s for s in segs if isinstance(s, dict)] if isinstance(segs, list) else []


def _speaker_map(r: dict, segs: list[dict]) -> dict[str, str]:
    """Speakr's labels named by the script: each segment votes, by its words, for the closest line's
    speaker; a label goes to its strongest name, each name once."""
    lines = [(who, _norm(t)) for who, t in _lines(r)]
    votes: dict[str, dict[str, float]] = {}
    for seg in segs:
        label, text = seg.get("speaker"), _norm(seg.get("sentence") or seg.get("text") or "")
        if not label or not text:
            continue
        best = max(lines, key=lambda wl: difflib.SequenceMatcher(None, text, wl[1]).ratio()
                   + (0.5 if text in wl[1] else 0))
        votes.setdefault(label, {}).setdefault(best[0], 0.0)
        votes[label][best[0]] += len(text)
    pairs = sorted(((n, lab, who) for lab, v in votes.items() for who, n in v.items()), reverse=True)
    out: dict[str, str] = {}
    for _n, lab, who in pairs:
        if lab not in out and who not in out.values():
            out[lab] = who
    return out


def finish(c: httpx.Client, rid: int, r: dict, summary: str) -> None:
    rec = _wait(c, rid, want_summary=False)
    segs = _segments(c, rid)
    names = _speaker_map(r, segs)
    old = _summary(c, rid)
    if names:
        smap = {lab: ({"name": who, "isMe": True} if who == "Alex Rivera" else who) for lab, who in names.items()}
        c.put(f"/api/v1/recordings/{rid}/speakers/assign",
              json={"speaker_map": smap, "regenerate_summary": summary == "speakr"}).raise_for_status()
    # Speakr may fill participants from the speaker names; the script's list is the full one.
    c.patch(f"/api/v1/recordings/{rid}", json={"participants": ", ".join(r["participants"])}).raise_for_status()
    if summary == "speakr":
        try:
            _wait(c, rid, want_summary=True, timeout=180, old=old if names else "")
        except SystemExit as e:
            print(f"{e}")
            if not _summary(c, rid):
                summary = "scripted"
    if summary == "scripted":
        c.put(f"/api/v1/recordings/{rid}/summary", json={"summary": r["summary"]}).raise_for_status()
    print(f"  {rid}: {len(segs)} segments, speakers {names or 'none'}, "
          f"{rec.get('audio_duration') or '?'}s, summary from {summary}")


def _marked(c: httpx.Client) -> list[tuple[int, str]]:
    """(id, title) of the recordings that carry the demo marker in their notes."""
    page, out = 1, []
    while True:
        rows = c.get("/api/v1/recordings", params={"page": page, "per_page": 100}).json().get("recordings") or []
        for rec in rows:
            full = c.get(f"/api/v1/recordings/{rec['id']}/notes")
            notes = (full.json() or {}).get("notes", "") if full.status_code == 200 else ""
            if MARK in (notes or "") or MARK in (rec.get("notes") or ""):
                out.append((rec["id"], rec.get("title") or ""))
        if len(rows) < 100:
            return out
        page += 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", required=True, help="the Speakr development server, e.g. http://localhost:8899")
    ap.add_argument("--token", help="an API token of that server (prefer $SPEAKR_TOKEN or --token-file)")
    ap.add_argument("--token-file", type=Path, help="a file with the token, plain or JSON with a 'token' key")
    ap.add_argument("--now", help="the moment the dates are relative to (the seed's --now; no zone = America/Chicago)")
    ap.add_argument("--reset", action="store_true", help="delete the demo recordings loaded before")
    ap.add_argument("--audio-dir", type=Path, help="folder with <slug>.wav/.mp3/.m4a readings of the scripts")
    ap.add_argument("--scripts-dir", type=Path, help="write the reading scripts here and stop")
    ap.add_argument("--no-wait", action="store_true", help="upload only; do not wait, name speakers or summarise")
    ap.add_argument("--finish", action="store_true",
                    help="upload nothing; wait for, name and summarise the demo recordings already there")
    ap.add_argument("--summary", choices=("speakr", "scripted"), default="speakr",
                    help="keep Speakr's own summary (default) or replace it with the scripted one")
    a = ap.parse_args()

    if a.scripts_dir:
        a.scripts_dir.mkdir(parents=True, exist_ok=True)
        for r in RECORDINGS:
            (a.scripts_dir / f"{r['slug']}.txt").write_text(f"{r['title']}\n\n{r['script']}\n")
        print(f"wrote {len(RECORDINGS)} scripts to {a.scripts_dir}")
        return

    base = a.url.rstrip("/")
    c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {_token(a)}", "Accept": "application/json"},
                     timeout=120)
    me = c.get("/api/v1/users/me")
    if me.status_code != 200:
        sys.exit(f"{base} did not accept the token ({me.status_code}): is this a Speakr server?")
    print(f"signed in to {base} as {me.json().get('username')}")

    if a.finish:
        by_title = {r["title"]: r for r in RECORDINGS}
        for rid, title in _marked(c):
            if title in by_title:
                finish(c, rid, by_title[title], a.summary)
        return

    if a.reset:
        doomed = [rid for rid, _ in _marked(c)]
        for rid in doomed:
            res = c.delete(f"/api/v1/recordings/{rid}")
            if res.status_code == 403:
                sys.exit(f"cannot delete recording {rid}: the token lacks the 'delete' scope")
            res.raise_for_status()
        print(f"deleted {len(doomed)} demo recording(s)")

    today = _now(a.now).date()
    done: list[tuple[int, dict]] = []
    for r in RECORDINGS:
        d = today + timedelta(days=r["days"])
        when = datetime(d.year, d.month, d.day, *r["at"], tzinfo=TZ)
        audio, name, ctype = tone(), f"{r['slug']}.wav", "audio/wav"
        if a.audio_dir:
            found = next((p for ext in ("wav", "mp3", "m4a") if (p := a.audio_dir / f"{r['slug']}.{ext}").exists()), None)
            if found:
                audio, name = found.read_bytes(), found.name
                ctype = {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4"}[found.suffix[1:]]
        n = len({who for who, _ in _lines(r)})
        up = c.post("/api/v1/recordings/upload", files={"file": (name, audio, ctype)},
                    data={"title": r["title"], "meeting_date": when.isoformat(), "notes": f"{r['notes']}\n\n{MARK}",
                          "min_speakers": str(n), "max_speakers": str(n), "language": "en"})
        up.raise_for_status()
        rid = up.json()["id"]
        c.patch(f"/api/v1/recordings/{rid}", json={"title": r["title"], "participants": ", ".join(r["participants"]),
                                                   "meeting_date": when.isoformat()}).raise_for_status()
        print(f"  {rid}: {r['title']} ({when:%a %d %b %H:%M})")
        done.append((rid, r))
    if not a.no_wait:
        print("waiting for Speakr to transcribe and summarise")
        for rid, r in done:
            finish(c, rid, r, a.summary)
    print("done: sync the Speakr connector in mailr (Settings, Accounts) or run `mailr demo-seed` with "
          "--speakr-url and --speakr-token")


if __name__ == "__main__":
    main()
