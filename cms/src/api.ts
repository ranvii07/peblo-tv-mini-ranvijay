/**
 * CMS API client.
 *
 * Every error the UI shows comes from `error.message` in the API's response envelope.
 * The CMS does not invent its own copy for server-side failures — messages are written
 * once, on the server, so the API and the UI can never disagree about what went wrong.
 */

const TOKEN_KEY = 'peblo.token'

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

export class ApiError extends Error {
  code: string
  status: number
  details?: unknown
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = tokenStore.get()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  const res = await fetch(path, { ...init, headers })

  if (res.status === 401) {
    // The session is gone; drop the stale token so the app returns to login.
    tokenStore.clear()
    window.dispatchEvent(new Event('peblo:unauthorized'))
  }

  if (!res.ok) {
    let code = 'error'
    let message = 'Something went wrong. Please try again.'
    let details: unknown
    try {
      const body = await res.json()
      code = body?.error?.code ?? code
      message = body?.error?.message ?? message
      details = body?.error?.details
    } catch {
      /* keep defaults for non-JSON responses */
    }
    throw new ApiError(res.status, code, message, details)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ------------------------------------------------------------------ types
export interface ArtworkRecord {
  url: string
  width: number
  height: number
  size_bytes: number
}

export interface Episode {
  id: number
  season_id: number
  external_id: string | null
  number: number
  title: string
  synopsis: string | null
  duration_seconds: number | null
  language: string
  content_group: string | null
  status: 'draft' | 'published'
  seed_issue: string | null
  artwork: Record<string, ArtworkRecord>
}

export interface Season {
  id: number
  number: number
  title: string | null
  episodes: Episode[]
}

export interface ShowDetail {
  id: number
  slug: string
  title: string
  synopsis: string | null
  section: string | null
  categories: string[]
  status: 'draft' | 'published'
  featured: boolean
  artwork: Record<string, ArtworkRecord>
  seasons: Season[]
}

export interface ShowListItem {
  id: number
  slug: string
  title: string
  section: string | null
  status: 'draft' | 'published'
  featured: boolean
  categories: string[]
  episode_count: number
  languages: string[]
  updated_at: string | null
  artwork: Record<string, ArtworkRecord>
}

export interface Issue {
  code: string
  severity: 'blocker' | 'warning'
  message: string
  entity: { type: string; id: number | null }
  details?: Record<string, unknown>
}

export interface ValidationReport {
  blocking_publish: boolean
  counts: { shows_with_issues: number; blockers: number; warnings: number }
  shows: {
    show_id: number
    title: string
    section: string | null
    blocker_count: number
    warning_count: number
    issues: Issue[]
  }[]
  global_issues: Issue[]
}

export interface PublishRun {
  id: number
  status: 'running' | 'succeeded' | 'failed' | 'noop'
  started_at: string | null
  finished_at: string | null
  actor: string | null
  counts: Record<string, number> | null
  checksum: string
  error: string | null
  is_current: boolean
}

export interface Me {
  id: number
  email: string
  role: 'editor' | 'admin'
}

// ------------------------------------------------------------------ calls
export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string; user: Me }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  me: () => request<Me>('/api/auth/me'),

  listShows: (params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== '') qs.set(k, String(v))
    }
    return request<{
      items: ShowListItem[]
      page: number
      page_size: number
      total: number
      pages: number
    }>(`/api/shows?${qs.toString()}`)
  },

  getShow: (id: number) => request<ShowDetail>(`/api/shows/${id}`),

  patchShow: (id: number, body: Record<string, unknown>) =>
    request<ShowDetail>(`/api/shows/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  patchEpisode: (id: number, body: Record<string, unknown>) =>
    request<Episode>(`/api/episodes/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  uploadArtwork: (form: FormData) =>
    request<ArtworkRecord & { id: number; kind: string }>('/api/artwork', {
      method: 'POST',
      body: form,
    }),

  validationReport: () => request<ValidationReport>('/api/admin/validation-report'),

  publish: () =>
    request<{ status: string; run_id: number; message: string; counts: Record<string, number> }>(
      '/api/admin/catalog/publish',
      { method: 'POST' },
    ),

  publishRuns: () => request<{ items: PublishRun[] }>('/api/admin/publish-runs'),
}
