/**
 * The viewer's ENTIRE network surface.
 *
 * Two functions, two public endpoints, and no credentials of any kind. This file is
 * deliberately the only place the viewer talks to the server, so the guarantee that it
 * reads nothing but the published catalogue is verifiable by reading one short file —
 * or mechanically, by grepping this directory for privileged terms and finding none.
 * See the README's verification section for the exact command.
 */

export type Artwork = Record<string, string | undefined>

export interface Variant {
  episode_id: number
  title: string
  duration_seconds: number | null
  thumbnail: string | null
}

export interface Entry {
  entry_id: number
  content_group: string | null
  title: string
  synopsis: string | null
  episode_number: number
  languages: string[]
  duration_seconds: number | null
  thumbnail: string | null
  variants: Record<string, Variant>
}

export interface Season {
  number: number
  title: string | null
  entries: Entry[]
}

export interface Show {
  id: number
  slug: string
  title: string
  synopsis: string | null
  categories: string[]
  featured: boolean
  artwork: Artwork
  seasons: Season[]
  trailers: Entry[]
}

export interface Section {
  section: string
  shows: Show[]
}

export interface Catalog {
  catalog_version: number
  generated_at: string
  run_id: number
  counts: { shows: number; entries: number; episodes: number; languages: number }
  sections: Section[]
}

export interface SearchResult {
  show_id: number
  show_slug: string
  show_title: string
  section: string
  categories: string[]
  season_number: number | null
  is_trailer: boolean
  poster: string | null
  entry: Entry
}

export interface SearchResponse {
  query: Record<string, string | null>
  total: number
  results: SearchResult[]
  facets: { categories: string[]; languages: string[]; sections: string[] }
}

export class ApiError extends Error {
  code: string
  status: number
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    let code = 'error'
    let message = 'Something went wrong loading this page.'
    try {
      const body = await res.json()
      // The API always returns {error:{code,message}} and its messages are written
      // to be shown to a person, so they are displayed verbatim.
      code = body?.error?.code ?? code
      message = body?.error?.message ?? message
    } catch {
      /* non-JSON error body; keep the defaults */
    }
    throw new ApiError(res.status, code, message)
  }
  return res.json() as Promise<T>
}

export const fetchCatalog = () => get<Catalog>('/api/catalog')

export function fetchSearch(params: {
  q?: string
  category?: string
  language?: string
  section?: string
}): Promise<SearchResponse> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) qs.set(k, v)
  return get<SearchResponse>(`/api/catalog/search?${qs.toString()}`)
}
