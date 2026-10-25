"""Speakr: recordings, transcripts and summaries from a self-hosted Speakr server.

- client.py: the HTTP client (Bearer token, pagination, errors, transcript normalisation);
- store.py: the connector, the metadata mirror, the short-lived content cache and visibility;
- sync.py: the `speakr.sync` job and its polling;
- matching.py: recordings matched to calendar events (suggested links);
- followup.py and action_items.py: the follow-up draft and the proposed tasks;
- routes.py: the REST routes under /api/v1/speakr.
"""
