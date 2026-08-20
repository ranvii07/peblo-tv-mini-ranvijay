import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Link,
  Route,
  Routes,
  useParams,
  useSearchParams,
} from 'react-router-dom'
import { fetchCatalog, fetchSearch, type Catalog, type Show } from './api'
import {
  Artwork,
  EmptyState,
  EpisodeRow,
  ErrorState,
  LanguageBadges,
  Loading,
  formatDuration,
} from './components'

function useCatalog() {
  return useQuery({ queryKey: ['catalog'], queryFn: fetchCatalog, retry: 1 })
}

/** One search request per pause in typing, rather than one per keystroke. */
function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return debounced
}

function allShows(catalog: Catalog): Show[] {
  return catalog.sections.flatMap((s) => s.shows)
}

/** Hero picks the first featured show, falling back to the first show published. */
function pickHero(catalog: Catalog): Show | null {
  const shows = allShows(catalog)
  return shows.find((s) => s.featured) ?? shows[0] ?? null
}

function Header() {
  return (
    <header className="topbar">
      <Link to="/" className="brand">
        Peblo<span>TV</span>
      </Link>
      <nav>
        <Link to="/search">Search</Link>
      </nav>
    </header>
  )
}

function ShowCard({ show }: { show: Show }) {
  return (
    <Link to={`/show/${show.slug}`} className="card">
      <Artwork src={show.artwork.poster} alt={show.title} ratio="2 / 3" />
      <span className="card-title">{show.title}</span>
    </Link>
  )
}

function Home() {
  const { data, isLoading, isError, error, refetch } = useCatalog()

  if (isLoading) return <Loading label="Loading shows…" />
  if (isError)
    return (
      <ErrorState
        message={(error as Error)?.message ?? 'We could not load the catalogue.'}
        onRetry={() => refetch()}
      />
    )
  if (!data || data.sections.length === 0)
    return <EmptyState message="There's nothing to watch just yet. Check back soon!" />

  const hero = pickHero(data)

  return (
    <div>
      {hero && (
        <section className="hero">
          {/* The hero uses the banner (16:9), per surface. */}
          <Artwork src={hero.artwork.banner} alt={hero.title} ratio="16 / 9" className="hero-art" />
          <div className="hero-copy">
            <h1>{hero.title}</h1>
            {hero.synopsis && <p>{hero.synopsis}</p>}
            <Link className="btn primary" to={`/show/${hero.slug}`}>
              View episodes
            </Link>
          </div>
        </section>
      )}

      {data.sections.map((section) => (
        <section key={section.section} className="row">
          <h2>{section.section}</h2>
          {/* Rows use posters (2:3), per surface. */}
          <div className="row-scroll">
            {section.shows.map((show) => (
              <ShowCard key={show.id} show={show} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function ShowDetail() {
  const { slug } = useParams()
  const { data, isLoading, isError, error, refetch } = useCatalog()
  const [seasonNumber, setSeasonNumber] = useState<number | null>(null)

  if (isLoading) return <Loading />
  if (isError)
    return <ErrorState message={(error as Error)?.message ?? 'Could not load.'} onRetry={() => refetch()} />

  const show = data ? allShows(data).find((s) => s.slug === slug) : undefined
  if (!show) return <EmptyState message="We couldn't find that show." />

  const active =
    show.seasons.find((s) => s.number === seasonNumber) ?? show.seasons[0] ?? null

  return (
    <div className="detail">
      <Artwork src={show.artwork.banner} alt={show.title} ratio="16 / 9" className="detail-banner" />
      <div className="detail-body">
        <h1>{show.title}</h1>
        {show.synopsis && <p className="lede">{show.synopsis}</p>}
        <div className="chips">
          {show.categories.map((c) => (
            <span key={c} className="chip">
              {c}
            </span>
          ))}
        </div>

        {/* Season 0 never appears as a season — it is a separate Trailers row. */}
        {show.trailers.length > 0 && (
          <section>
            <h2>Trailers</h2>
            <ul className="episodes">
              {show.trailers.map((t) => (
                <EpisodeRow key={t.entry_id} entry={t} />
              ))}
            </ul>
          </section>
        )}

        {show.seasons.length > 0 && active && (
          <section>
            <div className="season-tabs">
              {show.seasons.map((s) => (
                <button
                  key={s.number}
                  className={`tab ${s.number === active.number ? 'active' : ''}`}
                  onClick={() => setSeasonNumber(s.number)}
                >
                  {s.title ?? `Season ${s.number}`}
                </button>
              ))}
            </div>
            <ul className="episodes">
              {active.entries.map((e) => (
                <EpisodeRow key={e.entry_id} entry={e} />
              ))}
            </ul>
          </section>
        )}

        <p className="muted small">
          This is a browse-only catalogue — video playback is out of scope for this build.
        </p>
      </div>
    </div>
  )
}

function Search() {
  const [params, setParams] = useSearchParams()
  const category = params.get('category') ?? ''
  const language = params.get('language') ?? ''

  // Typed locally, written to the URL after a pause. The URL stays shareable without
  // the back button collecting one entry per letter.
  const [typed, setTyped] = useState(params.get('q') ?? '')
  const q = useDebounced(typed)

  useEffect(() => {
    if (q === (params.get('q') ?? '')) return
    const next = new URLSearchParams(params)
    if (q) next.set('q', q)
    else next.delete('q')
    setParams(next, { replace: true })
  }, [q, params, setParams])

  const query = useQuery({
    queryKey: ['search', q, category, language],
    queryFn: () => fetchSearch({ q, category, language }),
    retry: 1,
  })

  const facets = query.data?.facets
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  const results = useMemo(() => query.data?.results ?? [], [query.data])

  return (
    <div className="search">
      <h1>Search</h1>
      <div className="filters">
        <input
          value={typed}
          placeholder="Search shows and episodes"
          onChange={(e) => setTyped(e.target.value)}
        />
        <select value={category} onChange={(e) => update('category', e.target.value)}>
          <option value="">All categories</option>
          {facets?.categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select value={language} onChange={(e) => update('language', e.target.value)}>
          <option value="">All languages</option>
          {facets?.languages.map((l) => (
            <option key={l} value={l}>
              {l.toUpperCase()}
            </option>
          ))}
        </select>
      </div>

      {query.isLoading && <Loading label="Searching…" />}
      {query.isError && (
        <ErrorState
          message={(query.error as Error)?.message ?? 'Search failed.'}
          onRetry={() => query.refetch()}
        />
      )}
      {query.isSuccess && results.length === 0 && (
        <EmptyState message="No shows match — try clearing a filter." />
      )}

      <ul className="results">
        {results.map((r) => (
          <li key={`${r.show_id}-${r.entry.entry_id}`}>
            <Link to={`/show/${r.show_slug}`} className="result">
              <Artwork src={r.poster} alt={r.show_title} ratio="2 / 3" className="result-art" />
              <div>
                <strong>{r.show_title}</strong>
                <div className="muted">
                  {r.is_trailer ? 'Trailer' : `S${r.season_number} E${r.entry.episode_number}`} ·{' '}
                  {r.entry.title}
                </div>
                <div className="episode-meta">
                  <span className="muted">{formatDuration(r.entry.duration_seconds)}</span>
                  <LanguageBadges languages={r.entry.languages} />
                </div>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function App() {
  return (
    <div className="app">
      <Header />
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/show/:slug" element={<ShowDetail />} />
          <Route path="/search" element={<Search />} />
        </Routes>
      </main>
    </div>
  )
}
