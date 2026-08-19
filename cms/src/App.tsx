import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Link,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router-dom'
import {
  ApiError,
  api,
  tokenStore,
  type Issue,
  type Me,
  type ShowDetail,
} from './api'
import {
  ArtworkSlot,
  EmptyState,
  ErrorState,
  Loading,
  PermissionDenied,
  StatusChip,
  Toast,
  formatDuration,
  parseDuration,
} from './components'

// reference.json values are served to the UI by the API rather than duplicated here,
// so adding a section or language never requires a frontend change.
const SECTIONS = ['featured', 'series', 'minisodes', 'songs']
const LANGUAGES = ['en', 'hi']
const SPECS = {
  poster: { target_px: [600, 900] as [number, number], aspect: '2:3', max_kb: 200 },
  banner: { target_px: [1280, 720] as [number, number], aspect: '16:9', max_kb: 200 },
  thumbnail: { target_px: [640, 360] as [number, number], aspect: '16:9', max_kb: 200 },
}

function useToast() {
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])
  return { toast, setToast }
}

// ------------------------------------------------------------------------- login
function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [email, setEmail] = useState('admin@peblo.test')
  const [password, setPassword] = useState('admin123')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await api.login(email, password)
      tokenStore.set(res.access_token)
      onLogin(res.user)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not sign in.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <form className="panel" onSubmit={submit}>
        <h1>Peblo TV — CMS</h1>
        <label>
          Email
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="err">{error}</p>}
        <button className="btn primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        {/* Take-home convenience only — real credentials would never be printed. */}
        <p className="muted small">
          Demo logins: <code>admin@peblo.test / admin123</code> ·{' '}
          <code>editor@peblo.test / editor123</code>
        </p>
      </form>
    </div>
  )
}

// -------------------------------------------------------------------- shows list
function ShowsList() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const section = params.get('section') ?? ''
  const status = params.get('status') ?? ''
  const language = params.get('language') ?? ''
  const page = Number(params.get('page') ?? '1')

  const query = useQuery({
    queryKey: ['shows', q, section, status, language, page],
    queryFn: () => api.listShows({ q, section, status, language, page, page_size: 20 }),
  })

  function update(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page') next.delete('page')
    setParams(next)
  }

  return (
    <div>
      <div className="page-head">
        <h1>Shows</h1>
      </div>

      <div className="filters">
        <input
          placeholder="Search titles…"
          value={q}
          onChange={(e) => update('q', e.target.value)}
        />
        <select value={section} onChange={(e) => update('section', e.target.value)}>
          <option value="">All sections</option>
          {SECTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select value={status} onChange={(e) => update('status', e.target.value)}>
          <option value="">Any status</option>
          <option value="draft">draft</option>
          <option value="published">published</option>
        </select>
        <select value={language} onChange={(e) => update('language', e.target.value)}>
          <option value="">Any language</option>
          {LANGUAGES.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </div>

      {query.isLoading && <Loading />}
      {query.isError && (
        <ErrorState
          message={(query.error as Error).message}
          onRetry={() => query.refetch()}
        />
      )}
      {query.isSuccess && query.data.items.length === 0 && (
        <EmptyState message="No shows match these filters." />
      )}

      {query.isSuccess && query.data.items.length > 0 && (
        <>
          <table className="table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Section</th>
                <th>Status</th>
                <th>Episodes</th>
                <th>Languages</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link to={`/shows/${s.id}`}>{s.title}</Link>
                  </td>
                  <td>{s.section ?? <span className="err">— none —</span>}</td>
                  <td>
                    <StatusChip status={s.status} />
                  </td>
                  <td>{s.episode_count}</td>
                  <td>{s.languages.join(', ')}</td>
                  <td className="muted small">
                    {s.updated_at ? new Date(s.updated_at).toLocaleDateString() : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="pager">
            <button
              className="btn"
              disabled={page <= 1}
              onClick={() => update('page', String(page - 1))}
            >
              Previous
            </button>
            <span className="muted small">
              Page {query.data.page} of {query.data.pages || 1} · {query.data.total} shows
            </span>
            <button
              className="btn"
              disabled={page >= query.data.pages}
              onClick={() => update('page', String(page + 1))}
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------------- show editor
function EpisodeEditor({
  episode,
  onSaved,
  notify,
}: {
  episode: ShowDetail['seasons'][number]['episodes'][number]
  onSaved: () => void
  notify: (kind: 'ok' | 'err', text: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(episode.title)
  const [duration, setDuration] = useState(formatDuration(episode.duration_seconds))
  const [language, setLanguage] = useState(episode.language)
  const [contentGroup, setContentGroup] = useState(episode.content_group ?? '')
  const [issues, setIssues] = useState<Issue[]>([])

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patchEpisode(episode.id, body),
    onSuccess: () => {
      setIssues([])
      notify('ok', 'Episode saved.')
      onSaved()
    },
    onError: (e) => {
      const err = e as ApiError
      const details = err.details as { issues?: Issue[] } | undefined
      setIssues(details?.issues ?? [])
      notify('err', err.message)
    },
  })

  return (
    <li className={`episode ${episode.seed_issue ? 'flagged' : ''}`}>
      <div className="episode-row">
        <button className="linkish" onClick={() => setOpen((o) => !o)}>
          {open ? '▾' : '▸'} {episode.number}. {episode.title}
        </button>
        <span className="muted small">{episode.language.toUpperCase()}</span>
        <span className="muted small">{formatDuration(episode.duration_seconds) || '—'}</span>
        <StatusChip status={episode.status} />
        {episode.status === 'draft' ? (
          <button
            className="btn small"
            disabled={save.isPending}
            onClick={() => save.mutate({ status: 'published' })}
          >
            Publish
          </button>
        ) : (
          <button
            className="btn small"
            disabled={save.isPending}
            onClick={() => save.mutate({ status: 'draft' })}
          >
            Unpublish
          </button>
        )}
      </div>

      {episode.seed_issue && (
        <p className="warn small">Imported with a problem: {episode.seed_issue}</p>
      )}

      {issues.length > 0 && (
        <ul className="issues small">
          {issues.map((i, idx) => (
            <li key={idx} className="err">
              {i.message}
            </li>
          ))}
        </ul>
      )}

      {open && (
        <div className="episode-form">
          <label>
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            Duration (mm:ss)
            <input
              value={duration}
              placeholder="8:30"
              onChange={(e) => setDuration(e.target.value)}
            />
          </label>
          <label>
            Language
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANGUAGES.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            Content group
            <input
              value={contentGroup}
              placeholder="shared id for language variants"
              onChange={(e) => setContentGroup(e.target.value)}
            />
            <span className="muted small">
              Episodes sharing this become one entry with both languages.
            </span>
          </label>

          <ArtworkSlot
            kind="thumbnail"
            ownerType="episode"
            ownerId={episode.id}
            current={episode.artwork.thumbnail}
            spec={SPECS.thumbnail}
            onUploaded={onSaved}
          />

          <button
            className="btn primary"
            disabled={save.isPending}
            onClick={() => {
              const parsed = parseDuration(duration)
              if (duration.trim() && parsed === null) {
                notify('err', 'Enter the duration as minutes and seconds, like 8:30.')
                return
              }
              save.mutate({
                title,
                language,
                duration_seconds: parsed,
                content_group: contentGroup.trim() || null,
              })
            }}
          >
            {save.isPending ? 'Saving…' : 'Save episode'}
          </button>
        </div>
      )}
    </li>
  )
}

function ShowEditor({ me }: { me: Me }) {
  const { id } = useParams()
  const showId = Number(id)
  const qc = useQueryClient()
  const { toast, setToast } = useToast()
  const notify = (kind: 'ok' | 'err', text: string) => setToast({ kind, text })

  const query = useQuery({ queryKey: ['show', showId], queryFn: () => api.getShow(showId) })
  const [issues, setIssues] = useState<Issue[]>([])

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patchShow(showId, body),
    onSuccess: () => {
      setIssues([])
      notify('ok', 'Show saved.')
      void qc.invalidateQueries({ queryKey: ['show', showId] })
      void qc.invalidateQueries({ queryKey: ['shows'] })
    },
    onError: (e) => {
      const err = e as ApiError
      const details = err.details as { issues?: Issue[] } | undefined
      setIssues(details?.issues ?? [])
      notify('err', err.message)
    },
  })

  if (query.isLoading) return <Loading />
  if (query.isError)
    return <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />
  const show = query.data!

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['show', showId] })
  }

  return (
    <div>
      <Toast toast={toast} />
      <div className="page-head">
        <div>
          <Link to="/shows" className="muted small">
            ← All shows
          </Link>
          <h1>{show.title}</h1>
        </div>
        <div className="head-actions">
          <StatusChip status={show.status} />
          {show.status === 'draft' ? (
            <button
              className="btn primary"
              disabled={save.isPending}
              onClick={() => save.mutate({ status: 'published' })}
            >
              Publish show
            </button>
          ) : (
            <button
              className="btn"
              disabled={save.isPending}
              onClick={() => save.mutate({ status: 'draft' })}
            >
              Unpublish
            </button>
          )}
        </div>
      </div>

      {issues.length > 0 && (
        <div className="panel danger">
          <strong>This show can't be published yet</strong>
          <ul>
            {issues.map((i, idx) => (
              <li key={idx}>{i.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid-2">
        <section className="panel">
          <h2>Details</h2>
          <ShowForm show={show} onSave={(body) => save.mutate(body)} saving={save.isPending} />
        </section>

        <section className="panel">
          <h2>Artwork</h2>
          <div className="slots">
            <ArtworkSlot
              kind="poster"
              ownerType="show"
              ownerId={show.id}
              current={show.artwork.poster}
              spec={SPECS.poster}
              onUploaded={refresh}
            />
            <ArtworkSlot
              kind="banner"
              ownerType="show"
              ownerId={show.id}
              current={show.artwork.banner}
              spec={SPECS.banner}
              onUploaded={refresh}
            />
          </div>
        </section>
      </div>

      <section className="panel">
        <h2>Seasons &amp; episodes</h2>
        {show.seasons.length === 0 && <EmptyState message="This show has no seasons yet." />}
        {show.seasons.map((season) => (
          <div key={season.id} className="season">
            <h3>
              {season.number === 0 ? 'Season 0 — Trailers' : (season.title ?? `Season ${season.number}`)}
              {season.number === 0 && (
                <span className="muted small"> (shown as trailers, not a season)</span>
              )}
            </h3>
            <ul className="episodes">
              {season.episodes.map((ep) => (
                <EpisodeEditor key={ep.id} episode={ep} onSaved={refresh} notify={notify} />
              ))}
            </ul>
          </div>
        ))}
      </section>

      {me.role !== 'admin' && (
        <p className="muted small">
          You're signed in as an editor. You can make every change here; an admin
          publishes the catalogue.
        </p>
      )}
    </div>
  )
}

function ShowForm({
  show,
  onSave,
  saving,
}: {
  show: ShowDetail
  onSave: (body: Record<string, unknown>) => void
  saving: boolean
}) {
  const [title, setTitle] = useState(show.title)
  const [synopsis, setSynopsis] = useState(show.synopsis ?? '')
  const [section, setSection] = useState(show.section ?? '')
  const [featured, setFeatured] = useState(show.featured)

  return (
    <div className="form">
      <label>
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        Synopsis
        <textarea rows={3} value={synopsis} onChange={(e) => setSynopsis(e.target.value)} />
      </label>
      <label>
        Section
        <select value={section} onChange={(e) => setSection(e.target.value)}>
          <option value="">— none —</option>
          {SECTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {!show.section && (
          <span className="err small">
            This show has no section, so it can't be published.
          </span>
        )}
      </label>
      <label className="inline">
        <input
          type="checkbox"
          checked={featured}
          onChange={(e) => setFeatured(e.target.checked)}
        />
        Featured (used for the viewer's hero)
      </label>
      <button
        className="btn primary"
        disabled={saving}
        onClick={() =>
          onSave({
            title,
            synopsis: synopsis || null,
            section: section || null,
            featured,
          })
        }
      >
        {saving ? 'Saving…' : 'Save details'}
      </button>
    </div>
  )
}

// ------------------------------------------------------------------ publish page
function PublishPage({ me }: { me: Me }) {
  const qc = useQueryClient()
  const { toast, setToast } = useToast()
  const report = useQuery({ queryKey: ['report'], queryFn: api.validationReport })
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.publishRuns })

  const publish = useMutation({
    mutationFn: api.publish,
    onSuccess: (res) => {
      setToast({ kind: 'ok', text: res.message })
      void qc.invalidateQueries({ queryKey: ['runs'] })
      void qc.invalidateQueries({ queryKey: ['report'] })
    },
    onError: (e) => setToast({ kind: 'err', text: (e as ApiError).message }),
  })

  return (
    <div>
      <Toast toast={toast} />
      <div className="page-head">
        <h1>Publish</h1>
      </div>

      {me.role !== 'admin' && (
        <PermissionDenied message="Publishing requires the admin role. You can review everything below and fix what's blocking, but an admin has to run the publish." />
      )}

      {report.isLoading && <Loading label="Checking what's ready…" />}
      {report.isError && (
        <ErrorState message={(report.error as Error).message} onRetry={() => report.refetch()} />
      )}

      {report.isSuccess && (
        <>
          <section className="panel">
            <div className="publish-head">
              <div>
                <strong>
                  {report.data.blocking_publish
                    ? `${report.data.counts.blockers} problems are blocking publish`
                    : 'Everything is ready to publish'}
                </strong>
                <p className="muted small">
                  {report.data.counts.warnings} warnings · {report.data.counts.shows_with_issues}{' '}
                  shows with issues
                </p>
              </div>
              <button
                className="btn primary"
                disabled={
                  report.data.blocking_publish || me.role !== 'admin' || publish.isPending
                }
                onClick={() => publish.mutate()}
              >
                {publish.isPending ? 'Publishing…' : 'Publish catalogue'}
              </button>
            </div>

            {/* The requirement is explicit: when the button is disabled, the reasons
                must be visible right underneath it. */}
            {report.data.blocking_publish && (
              <div className="blockers">
                <p className="muted small">Publishing is blocked because:</p>
                <ul>
                  {report.data.shows.flatMap((s) =>
                    s.issues
                      .filter((i) => i.severity === 'blocker')
                      .map((i, idx) => (
                        <li key={`${s.show_id}-${idx}`}>
                          <Link to={`/shows/${s.show_id}`}>{s.title}</Link> — {i.message}
                        </li>
                      )),
                  )}
                </ul>
              </div>
            )}
          </section>

          {report.data.global_issues.length > 0 && (
            <section className="panel">
              <h2>Things worth a look</h2>
              <ul>
                {report.data.global_issues.map((i, idx) => (
                  <li key={idx} className="warn">
                    {i.message}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="panel">
            <h2>By show</h2>
            {report.data.shows.length === 0 && <EmptyState message="Nothing to fix." />}
            {report.data.shows.map((s) => (
              <div key={s.show_id} className="report-show">
                <h3>
                  <Link to={`/shows/${s.show_id}`}>{s.title}</Link>{' '}
                  <span className="muted small">
                    {s.blocker_count} blockers · {s.warning_count} warnings
                  </span>
                </h3>
                <ul className="issues">
                  {s.issues.map((i, idx) => (
                    <li key={idx} className={i.severity === 'blocker' ? 'err' : 'warn'}>
                      {i.message}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        </>
      )}

      <section className="panel">
        <h2>Run history</h2>
        {runs.isLoading && <Loading />}
        {runs.isSuccess && runs.data.items.length === 0 && (
          <EmptyState message="Nothing has been published yet." />
        )}
        {runs.isSuccess && runs.data.items.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>Status</th>
                <th>Shows</th>
                <th>Entries</th>
                <th>Checksum</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.items.map((r) => (
                <tr key={r.id}>
                  <td className="small">
                    {r.started_at ? new Date(r.started_at).toLocaleString() : ''}
                  </td>
                  <td className="small">{r.actor ?? 'system'}</td>
                  <td>
                    <StatusChip status={r.status} />
                    {r.is_current && <span className="chip live">live</span>}
                  </td>
                  <td>{r.counts?.shows ?? '—'}</td>
                  <td>{r.counts?.entries ?? '—'}</td>
                  <td className="mono small">{r.checksum}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

// -------------------------------------------------------------------------- shell
export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [checking, setChecking] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    // A stored token might be expired; ask the server rather than trusting it.
    if (!tokenStore.get()) {
      setChecking(false)
      return
    }
    api
      .me()
      .then(setMe)
      .catch(() => tokenStore.clear())
      .finally(() => setChecking(false))
  }, [])

  useEffect(() => {
    const onUnauthorized = () => {
      setMe(null)
      navigate('/')
    }
    window.addEventListener('peblo:unauthorized', onUnauthorized)
    return () => window.removeEventListener('peblo:unauthorized', onUnauthorized)
  }, [navigate])

  if (checking) return <Loading />
  if (!me) return <Login onLogin={setMe} />

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          Peblo<span>CMS</span>
        </div>
        <nav>
          <Link to="/shows">Shows</Link>
          <Link to="/publish">Publish</Link>
        </nav>
        <div className="who">
          <div className="small">{me.email}</div>
          <div className="muted small">{me.role}</div>
          <button
            className="linkish small"
            onClick={() => {
              tokenStore.clear()
              setMe(null)
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/shows" replace />} />
          <Route path="/shows" element={<ShowsList />} />
          <Route path="/shows/:id" element={<ShowEditor me={me} />} />
          <Route path="/publish" element={<PublishPage me={me} />} />
        </Routes>
      </main>
    </div>
  )
}
