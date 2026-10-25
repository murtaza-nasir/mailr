// Speakr recordings through mailr's own routes (/api/v1/speakr). No request goes from the web to Speakr: content comes through mailr, and "Open in
// Speakr" is a plain link to the deep link in mailr's answer.

import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

export interface RecordingLink {
  id: number
  connector_id: number
  recording_id: number
  kind: 'event' | 'conversation' | 'task'
  target: string
  occurrence: string | null
  state: 'suggested' | 'confirmed' | 'dismissed'
  score: number | null
  source: string
  title?: string | null
  start?: string | null
  conversation_id?: number
}

export interface Segment {
  t: number | null
  end: number | null
  speaker: string
  text: string
}

export interface Candidate {
  uid: string
  occurrence: string
  summary: string
  start: string | null
  end: string | null
  score: number
}

export interface Recording {
  connector_id: number
  recording_id: number
  title: string
  meeting_date: string | null
  created_at?: string | null
  duration_s: number | null
  participants: string[]
  tags: string[]
  folder: string | null
  status: string
  has_transcript: boolean
  has_summary: boolean
  audio_available: boolean
  is_archived: boolean
  local_only: boolean
  url: string
  summary?: string
  notes?: string
  events?: { title: string; start: string | null; end: string | null; location: string }[]
  speakers?: { label: string; name: string }[]
  links?: RecordingLink[]
  candidates?: Candidate[]
  unavailable?: Record<string, string>
  summary_excerpt?: string | null
  link?: RecordingLink
  /** The connector card's switches: edits to Speakr and public share links (off by default). */
  write_back?: boolean
  share_links?: boolean
  /** A recording another Speakr user shared with you (Include recordings shared with me). */
  is_shared?: boolean
  owner_name?: string | null
  can_edit?: boolean
  /** Speakr lists and revokes the recording's public links (v1 share links). */
  share_list?: boolean
  /** The reader card: Speakr reports the transcription failed / it is still processing. */
  failed?: boolean
  processing?: boolean
  /** "Last time": why this recording, and the summary's first line (fetched on demand). */
  why?: string
  summary_line?: string | null
}

export interface Transcript {
  segments: Segment[]
  truncated: boolean
  next_start: number | null
  total_segments: number
  timed: boolean
  url: string
}

export interface ActionItem {
  text: string
  owner: string
  mine: boolean
  due: string | null
  t: number | null
  checked: boolean
  already_added: boolean
  url: string
}

export const speakrKeys = {
  status: ['speakr', 'status'] as const,
  recording: (cid: number, rid: number) => ['speakr', 'recording', cid, rid] as const,
  transcript: (cid: number, rid: number) => ['speakr', 'transcript', cid, rid] as const,
  event: (uid: string, occurrence: string | null | undefined) => ['speakr', 'event', uid, occurrence ?? ''] as const,
  list: (q: string) => ['speakr', 'list', q] as const,
  mailCard: (messageId: number) => ['speakr', 'mail-card', messageId] as const,
  linked: ['speakr', 'linked-conversations'] as const,
  lastTime: (uid: string, occurrence: string | null | undefined) => ['speakr', 'last-time', uid, occurrence ?? ''] as const,
  people: (cid: number, rid: number) => ['speakr', 'people', cid, rid] as const,
  person: (email: string) => ['speakr', 'person', email] as const,
  task: (uid: string) => ['speakr', 'task', uid] as const,
}

/** Whether the user has a Speakr connector: recording features show only then. */
export function useSpeakrStatus(enabled = true) {
  return useQuery({
    queryKey: speakrKeys.status,
    queryFn: ({ signal }) => api.get<{ connected: boolean; connectors: { id: number; name: string; base_url: string; search?: boolean }[] }>('/speakr/status', undefined, signal),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

// ACT recording.view @web.recording
export function useRecording(cid: number, rid: number, enabled = true) {
  return useQuery({
    queryKey: speakrKeys.recording(cid, rid),
    queryFn: ({ signal }) => api.get<Recording>(`/speakr/recordings/${cid}/${rid}`, { include: 'summary,notes,links' }, signal),
    enabled,
    staleTime: 30_000,
    retry: false,
  })
}

export function useTranscript(cid: number, rid: number, enabled: boolean) {
  return useQuery({
    queryKey: speakrKeys.transcript(cid, rid),
    queryFn: ({ signal }) => api.get<Transcript>(`/speakr/recordings/${cid}/${rid}/transcript`, { max_chars: 20000 }, signal),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

export function useEventRecordings(uid: string, occurrence: string | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: speakrKeys.event(uid, occurrence),
    queryFn: ({ signal }) => api.get<{ connected: boolean; items: Recording[] }>('/speakr/events/recordings', { uid, occurrence: occurrence ?? '' }, signal),
    enabled,
    staleTime: 30_000,
    retry: false,
  })
}

export function useRecordingList(q: string, enabled: boolean) {
  return useQuery({
    queryKey: speakrKeys.list(q),
    queryFn: ({ signal }) => api.get<{ items: Recording[] }>('/speakr/recordings', { q, limit: 8 }, signal),
    enabled,
    staleTime: 30_000,
    retry: false,
  })
}

// ACT recording.link @web.recording @web.calendar
export const linkRecording = (body: { connector_id: number; recording_id: number; kind: 'event' | 'conversation' | 'task'; target?: string; occurrence?: string; conversation_id?: number }) =>
  api.post<RecordingLink>('/speakr/links', body)

// ACT recording.unlink @web.recording @web.calendar
export const unlinkRecording = (id: number) => api.del<RecordingLink>(`/speakr/links/${id}`)

export interface FollowUp {
  draft_id: number
  conversation_id: number | null
  payload: import('../../api/types').ComposePayload
  to: string[]
  reply: boolean
}

// ACT recording.follow_up @web.recording
export const followUp = (cid: number, rid: number, body: { mode: 'summary' | 'assistant'; event_uid?: string; occurrence?: string | null }) =>
  api.post<FollowUp>(`/speakr/recordings/${cid}/${rid}/follow-up`, body)

// ACT recording.to_tasks @web.recording
export const proposeActionItems = (cid: number, rid: number) =>
  api.post<{ items: ActionItem[]; note?: string; source?: string }>(`/speakr/recordings/${cid}/${rid}/action-items`, {})

export const createTasks = (cid: number, rid: number, body: { items: { text: string; due: string | null; t: number | null }[]; event_uid?: string; occurrence?: string | null }) =>
  api.post<{ created: { uid: string; summary: string }[]; pending: number }>(`/speakr/recordings/${cid}/${rid}/tasks`, body)

// ---------------------------------------------------------------- writes

// ACT recording.view @web.reader (the reader's card)
export function useMailCard(messageId: number, enabled: boolean) {
  return useQuery({
    queryKey: speakrKeys.mailCard(messageId),
    queryFn: ({ signal }) => api.get<{ connected: boolean; items: Recording[] }>('/speakr/mail-card', { message_id: messageId }, signal),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

/** Conversations linked to a recording (the list's ▶ chip); one request for the whole list. */
export function useLinkedConversations(enabled: boolean) {
  return useQuery({
    queryKey: speakrKeys.linked,
    queryFn: ({ signal }) => api.get<{ conversation_ids: number[] }>('/speakr/linked-conversations', undefined, signal).then((r) => new Set(r.conversation_ids)),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

// ACT event.last_recording @web.calendar @web.panel @web.sheet
export function useLastTime(uid: string, occurrence: string | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: speakrKeys.lastTime(uid, occurrence),
    queryFn: ({ signal }) => api.get<{ connected: boolean; item: Recording | null; upcoming?: boolean }>('/speakr/events/last-time', { uid, occurrence: occurrence ?? '' }, signal),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

/** A candidate for a speaker (backend speakr/people.py): one person, with every address People holds
 * for them under the same name, and why they are offered. */
export interface SpeakerCandidate {
  email: string
  name: string
  organization?: string
  emails?: string[]
  hints?: string[]
  why: string
}

export interface SpeakerPerson {
  label: string
  name: string
  /** The connector-wide key (name:<name>), or label:<recording>:<label> for an unnamed label. */
  key: string
  /** The key a choice is stored under: `key`, or the recording's override when this recording differs. */
  map_key?: string
  /** The key of "Not them in this recording" (null for an unnamed label). */
  override_key?: string | null
  /** The speaker is the user. */
  me?: boolean
  /** `all`: the person applies to every recording with this speaker name. */
  applies?: 'all' | 'recording'
  /** The person comes from a merge in the Recordings app's Participants list: the name merged into. */
  via_alias?: string | null
  speaker_id: number | null
  person: { email: string; name: string; organization?: string; me?: boolean } | null
  suggestions: SpeakerCandidate[]
  /** Candidates beyond the ones shown. */
  more?: number
}

// ACT speaker.map @web.recording
export function useRecordingPeople(cid: number, rid: number, enabled: boolean) {
  return useQuery({
    queryKey: speakrKeys.people(cid, rid),
    queryFn: ({ signal }) => api.get<{ speakers: SpeakerPerson[]; guests: { email: string; name: string }[]; me?: { email: string; name: string }; speakr_email?: boolean }>(`/speakr/recordings/${cid}/${rid}/people`, undefined, signal),
    enabled,
    staleTime: 30_000,
    retry: false,
  })
}

export const mapSpeaker = (cid: number, body: { key: string; speaker_name: string; email: string; name?: string; speaker_id?: number | null; recording_id?: number }) =>
  api.post<{ written_to_speakr?: boolean | null }>(`/speakr/speakers/${cid}/person`, body)

export const unmapSpeaker = (cid: number, key: string, email: string) =>
  api.del(`/speakr/speakers/${cid}/person?key=${encodeURIComponent(key)}&email=${encodeURIComponent(email)}`)

export interface Upload {
  connector_id: number
  recording_id: number
  title: string
  status: string
  url: string
}

// ACT mail.to_speakr @web.reader
export const transcribeAttachment = (messageId: number, part: number) => api.post<Upload>('/speakr/upload', { message_id: messageId, part })

// ACT recording.edit @web.recording
export const editRecording = (cid: number, rid: number, body: { title?: string; participants?: string[]; append_notes?: string; tags?: string[] }) =>
  api.patch<{ recording: Recording; previous: { title: string; participants: string[]; tags?: string[] }; notes_appended: boolean }>(`/speakr/recordings/${cid}/${rid}`, body)

export interface ShareOut {
  share_url: string
  existing: boolean
  /** A Speakr with v1 share links reports the link's own flags; a reused link keeps them. */
  share_id?: number
  share_notes?: boolean
  flags_differ?: boolean
}

// ACT recording.share_link @web.recording
export const shareRecording = (cid: number, rid: number, shareNotes: boolean) =>
  api.post<ShareOut>(`/speakr/recordings/${cid}/${rid}/share`, { share_notes: shareNotes })

export interface ShareLink {
  id: number
  share_url: string
  share_summary: boolean
  share_notes: boolean
  created_at: string | null
  expires_at: string | null
}

// ACT recording.share_link @web.recording (the links of a recording, and revoking one)
export function useShareLinks(cid: number, rid: number, enabled: boolean) {
  return useQuery({
    queryKey: ['speakr', 'shares', cid, rid],
    queryFn: ({ signal }) => api.get<{ supported: boolean; shares: ShareLink[] }>(`/speakr/recordings/${cid}/${rid}/shares`, undefined, signal),
    enabled,
    retry: false,
  })
}
export const revokeShareLink = (cid: number, rid: number, shareId: number) => api.del(`/speakr/recordings/${cid}/${rid}/shares/${shareId}`)

export interface ExtractedEvent {
  title: string
  start: string | null
  end: string | null
  location: string
  description: string
  /** A newer Speakr: guests (with the address mailr found for a name) and the reminder it heard. */
  attendees?: { name: string; email: string | null }[]
  reminder_minutes?: number | null
  /** Wall-clock times without a zone, to be read in the user's own zone. */
  floating?: boolean | null
}

export function useRecordingEvents(cid: number, rid: number, enabled: boolean) {
  return useQuery({
    queryKey: ['speakr', 'events', cid, rid],
    queryFn: ({ signal }) => api.get<{ events: ExtractedEvent[] }>(`/speakr/recordings/${cid}/${rid}/events`, undefined, signal),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

// ACT recording.view @web.people (the person card's Recordings list, web.people)
export function usePersonRecordings(email: string, enabled: boolean) {
  return useQuery({
    queryKey: speakrKeys.person(email),
    queryFn: ({ signal }) => api.get<{ count: number; items: Recording[]; local_only?: boolean }>('/speakr/people/recordings', { email, limit: 5 }, signal),
    enabled: enabled && !!email,
    staleTime: 60_000,
    retry: false,
  })
}

// ACT recording.view @web.pane (the task pane's "From:" line, web.pane)
export function useTaskRecordings(uid: string, enabled: boolean) {
  return useQuery({
    queryKey: speakrKeys.task(uid),
    queryFn: ({ signal }) => api.get<{ connected: boolean; items: (Recording & { t: number | null })[] }>('/speakr/tasks/recordings', { uid }, signal),
    enabled: enabled && !!uid,
    staleTime: 60_000,
    retry: false,
  })
}

export interface SearchHit {
  recording_id: number
  connector_id: number
  title: string
  where: string
  t: number | null
  speaker: string
  text: string
  cite: string
}

// ACT recording.search @web.list (content hits for the Recordings section of mail search; Speakr's own search)
export function useRecordingHits(q: string, enabled: boolean) {
  return useQuery({
    queryKey: ['speakr', 'hits', q],
    queryFn: ({ signal }) => api.get<{ hits: SearchHit[]; full_text?: boolean }>('/speakr/search', { q, top_k: 5 }, signal),
    enabled: enabled && q.trim().length >= 2,
    staleTime: 30_000,
    retry: false,
  })
}
