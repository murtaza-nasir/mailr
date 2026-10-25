# Dictation server

Requests to Ambrose can be dictated instead of typed. In the web app, a microphone button is shown in Ambrose when a model endpoint can do the **Speech to text** task. With the Android app, speech is turned into text on the phone when possible. When it is not, or when the user chooses the server under the app's **Dictation** settings, a short clip is sent to Mailr and passed on to the Speech to text endpoint.

The endpoint can be a speech server on your network, set up with `MAILR_ASR_URL` below, or any endpoint added under **Settings → Admin → Models**, including a hosted speech service ([Models](models.md#speech-to-text)).

## Set it up

Run a speech server with the OpenAI transcription API (`POST /v1/audio/transcriptions` with a `file` and a `model`). WhisperX and faster-whisper servers have this API. Then set in `.env`:

```ini
MAILR_ASR_URL=http://asr.local:9000
# MAILR_ASR_MODEL=whisper-1
# MAILR_ASR_MAX_BYTES=8000000
# MAILR_ASR_MAX_SECONDS=120
```

| Variable | Description |
|---|---|
| `MAILR_ASR_URL` | The base URL of the speech server, on the local network. It appears in Admin → Models as the local endpoint **Speech server (server)**; its address can be changed only here. When it is empty, that endpoint is not used. |
| `MAILR_ASR_MODEL` | The model of that endpoint, sent with each clip (`whisper-1`). |
| `MAILR_ASR_MAX_BYTES` | The largest clip accepted, in bytes (8 MB). |
| `MAILR_ASR_MAX_SECONDS` | The longest clip accepted, in seconds (120). |

Restart with `docker compose up -d`. The microphone button then appears in the web app, and the server option in the Android app.

## Privacy

- The endpoint of `MAILR_ASR_URL` is a local endpoint. Its addresses are checked each time a connection is made; when its host resolves to a public address, the clip is refused.
- A clip spoken into a chat that has read local-only items, into a draft of a local-only account, a local-only note or a task in a local-only calendar goes only to a local endpoint. Other clips may go to a hosted speech service when one is set up for the task. With **Local models only** (for everyone, or a person's own switch), every clip goes to a local endpoint.
- Each clip is held in memory for the one request. It is never stored or logged.
- Audio from the Android app is AAC in an `.m4a` container.
