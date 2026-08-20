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
  type ReferenceConfig,
  type Season,
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

/**
 * Sections, languages and artwork specs come from `reference.json`, served by
 * `GET /api/reference`. Nothing in this app restates them — adding a language or
 * retargeting a poster size is a change to that one file, not a frontend deploy.
 */
function useReference() {
  return useQuery({ queryKey: ['reference'], queryFn: api.reference, staleTime: Infinity })
}

/** Keeps typing smooth while the server sees one request per pause, not one per key. */
function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return debounced
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
  const section = params.get('section') ?? ''
  const status = params.get('status') ?? ''
  const language = params.get('language') ?? ''
  const page = Number(params.get('page') ?? '1')
  const reference = useReference()

  // The box is driven locally and pushed to the URL after a pause: typing stays smooth,
  // the URL stays shareable, and Back doesn't walk through every half-typed query.
  const [typed, setTyped] = useState(params.get('q') ?? '')
  const q = useDebounced(typed)

  useEffect(() => {
    if (q === (params.get('q') ?? '')) return
    const next = new URLSearchParams(params)
    if (q) next.set('q', q)
    else next.delete('q')
    next.delete('page')
    setParams(next, { replace: true })
  }, [q, params, setParams])

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
        <Link className="btn primary" to="/shows/new">
          New show
        </Link>
      </div>

      <div className="filters">
        <input
          placeholder="Search titles…"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
        />
        <select value={section} onChange={(e) => update('section', e.target.value)}>
          <option value="">All sections</option>
          {(reference.data?.sections ?? []).map((s) => (
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
          {(reference.data?.languages ?? []).map((l) => (
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

// -------------------------------------------------------------------- create show
function NewShow() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const reference = useReference()
  const [title, setTitle] = useState('')
  const [synopsis, setSynopsis] = useState('')
  const [section, setSection] = useState('')
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () =>
      api.createShow({ title: title.trim(), synopsis: synopsis || null, section: section || null }),
    onSuccess: (show) => {
      void qc.invalidateQueries({ queryKey: ['shows'] })
      // Straight into the editor: a show is never finished at the moment it is created,
      // and artwork and episodes are the next thing an editor needs.
      navigate(`/shows/${show.id}`)
    },
    onError: (e) => setError((e as ApiError).message),
  })

  return (
    <div>
      <div className="page-head">
        <div>
          <Link to="/shows" className="muted small">
            ← All shows
          </Link>
          <h1>New show</h1>
        </div>
      </div>

      <section className="panel">
        <div className="form">
          <label>
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
          </label>
          <label>
            Synopsis
            <textarea rows={3} value={synopsis} onChange={(e) => setSynopsis(e.target.value)} />
          </label>
          <label>
            Section
            <select value={section} onChange={(e) => setSection(e.target.value)}>
              <option value="">— choose later —</option>
              {(reference.data?.sections ?? []).map((sec) => (
                <option key={sec} value={sec}>
                  {sec}
                </option>
              ))}
            </select>
          </label>
          {error && <p className="err small">{error}</p>}
          <button
            className="btn primary"
            disabled={!title.trim() || create.isPending}
            onClick={() => {
              setError(null)
              create.mutate()
            }}
          >
            {create.isPending ? 'Creating…' : 'Create show'}
          </button>
          <p className="muted small">
            New shows start as drafts. Add artwork, seasons and episodes on the next
            screen, then publish when the checks pass.
          </p>
        </div>
      </section>
    </div>
  )
}

// ------------------------------------------------------------------- show editor
function EpisodeEditor({
  episode,
  reference,
  onSaved,
  notify,
}: {
  episode: ShowDetail['seasons'][number]['episodes'][number]
  reference: ReferenceConfig
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

  // The report tells editors to "fix it or delete it" for a row that arrived broken,
  // so deleting has to be something they can actually do. Confirmed, because it isn't
  // undoable.
  const remove = useMutation({
    mutationFn: () => api.deleteEpisode(episode.id),
    onSuccess: () => {
      notify('ok', `Deleted '${episode.title}'.`)
      onSaved()
    },
    onError: (e) => notify('err', (e as ApiError).message),
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
              {reference.languages.map((l) => (
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
            spec={reference.artwork_specs.thumbnail}
            onUploaded={onSaved}
          />

          <div className="head-actions">
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
            <button
              className="btn danger"
              disabled={remove.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    `Delete '${episode.title}'? This removes the episode and its ` +
                      `artwork, and can't be undone.`,
                  )
                ) {
                  remove.mutate()
                }
              }}
            >
              {remove.isPending ? 'Deleting…' : 'Delete episode'}
            </button>
          </div>
        </div>
      )}
    </li>
  )
}

/** Inline "add an episode" form, one per season. */
function AddEpisode({
  season,
  reference,
  onAdded,
  notify,
}: {
  season: Season
  reference: ReferenceConfig
  onAdded: () => void
  notify: (kind: 'ok' | 'err', text: string) => void
}) {
  const nextNumber = Math.max(0, ...season.episodes.map((e) => e.number)) + 1
  const [open, setOpen] = useState(false)
  const [number, setNumber] = useState(String(nextNumber))
  const [title, setTitle] = useState('')
  const [language, setLanguage] = useState(reference.languages[0] ?? 'en')
  const [duration, setDuration] = useState('')
  const [contentGroup, setContentGroup] = useState('')

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.createEpisode(season.id, body),
    onSuccess: () => {
      setOpen(false)
      setTitle('')
      setDuration('')
      setContentGroup('')
      setNumber(String(nextNumber + 1))
      notify('ok', 'Episode added as a draft.')
      onAdded()
    },
    onError: (e) => notify('err', (e as ApiError).message),
  })

  if (!open) {
    return (
      <button className="btn small" onClick={() => setOpen(true)}>
        + Add episode
      </button>
    )
  }

  return (
    <div className="episode-form add-form">
      <label>
        Number
        <input value={number} onChange={(e) => setNumber(e.target.value)} />
      </label>
      <label>
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
      </label>
      <label>
        Duration (mm:ss)
        <input value={duration} placeholder="8:30" onChange={(e) => setDuration(e.target.value)} />
      </label>
      <label>
        Language
        <select value={language} onChange={(e) => setLanguage(e.target.value)}>
          {reference.languages.map((l) => (
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
          Leave blank unless this is a language variant of another episode.
        </span>
      </label>
      <div className="head-actions">
        <button
          className="btn primary"
          disabled={!title.trim() || create.isPending}
          onClick={() => {
            const parsedNumber = Number(number)
            if (!Number.isInteger(parsedNumber) || parsedNumber < 0) {
              notify('err', 'Episode number has to be a whole number, like 4.')
              return
            }
            const parsed = parseDuration(duration)
            if (duration.trim() && parsed === null) {
              notify('err', 'Enter the duration as minutes and seconds, like 8:30.')
              return
            }
            create.mutate({
              number: parsedNumber,
              title: title.trim(),
              language,
              duration_seconds: parsed,
              content_group: contentGroup.trim() || null,
            })
          }}
        >
          {create.isPending ? 'Adding…' : 'Add episode'}
        </button>
        <button className="btn" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  )
}

/** The season heading, with its title editable in place. */
function SeasonHeading({
  season,
  onSaved,
  notify,
}: {
  season: Season
  onSaved: () => void
  notify: (kind: 'ok' | 'err', text: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(season.title ?? '')

  const save = useMutation({
    mutationFn: () => api.patchSeason(season.id, { title: title.trim() || null }),
    onSuccess: () => {
      setEditing(false)
      notify('ok', 'Season renamed.')
      onSaved()
    },
    onError: (e) => notify('err', (e as ApiError).message),
  })

  if (editing) {
    return (
      <div className="add-row">
        <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        <button className="btn small" disabled={save.isPending} onClick={() => save.mutate()}>
          Save
        </button>
        <button className="btn small" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
    )
  }

  return (
    <h3>
      {season.number === 0 ? 'Season 0 — Trailers' : (season.title ?? `Season ${season.number}`)}
      {season.number === 0 && (
        <span className="muted small"> (shown as trailers, not a season)</span>
      )}{' '}
      <button className="linkish small" onClick={() => setEditing(true)}>
        rename
      </button>
    </h3>
  )
}

/** Inline "add a season" form. Season 0 is offered explicitly, and labelled. */
function AddSeason({
  show,
  onAdded,
  notify,
}: {
  show: ShowDetail
  onAdded: () => void
  notify: (kind: 'ok' | 'err', text: string) => void
}) {
  const taken = new Set(show.seasons.map((s) => s.number))
  const suggested = show.seasons.length === 0 ? 1 : Math.max(...show.seasons.map((s) => s.number)) + 1
  const [number, setNumber] = useState(String(suggested))

  const create = useMutation({
    mutationFn: (n: number) =>
      api.createSeason(show.id, { number: n, title: n === 0 ? 'Trailers' : `Season ${n}` }),
    onSuccess: (_created, n) => {
      setNumber(String(n + 1))
      notify('ok', 'Season added.')
      onAdded()
    },
    onError: (e) => notify('err', (e as ApiError).message),
  })

  return (
    <div className="add-row">
      <label className="inline">
        Season number
        <input
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          style={{ width: '70px' }}
        />
      </label>
      <button
        className="btn small"
        disabled={create.isPending}
        onClick={() => {
          const n = Number(number)
          if (!Number.isInteger(n) || n < 0) {
            notify('err', 'Season number has to be 0 or more. Season 0 is for trailers.')
            return
          }
          if (taken.has(n)) {
            notify('err', `This show already has a season ${n}.`)
            return
          }
          create.mutate(n)
        }}
      >
        + Add season
      </button>
      <span className="muted small">Season 0 is reserved for trailers.</span>
    </div>
  )
}

function ShowEditor({ me }: { me: Me }) {
  const { id } = useParams()
  const showId = Number(id)
  const qc = useQueryClient()
  const { toast, setToast } = useToast()
  const notify = (kind: 'ok' | 'err', text: string) => setToast({ kind, text })

  const query = useQuery({ queryKey: ['show', showId], queryFn: () => api.getShow(showId) })
  const referenceQuery = useReference()
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

  if (query.isLoading || referenceQuery.isLoading) return <Loading />
  if (query.isError)
    return <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />
  if (referenceQuery.isError)
    return (
      <ErrorState
        message={(referenceQuery.error as Error).message}
        onRetry={() => referenceQuery.refetch()}
      />
    )
  const show = query.data!
  const reference = referenceQuery.data!

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
          <ShowForm
            show={show}
            reference={reference}
            onSave={(body) => save.mutate(body)}
            saving={save.isPending}
          />
        </section>

        <section className="panel">
          <h2>Artwork</h2>
          <div className="slots">
            <ArtworkSlot
              kind="poster"
              ownerType="show"
              ownerId={show.id}
              current={show.artwork.poster}
              spec={reference.artwork_specs.poster}
              onUploaded={refresh}
            />
            <ArtworkSlot
              kind="banner"
              ownerType="show"
              ownerId={show.id}
              current={show.artwork.banner}
              spec={reference.artwork_specs.banner}
              onUploaded={refresh}
            />
            <ArtworkSlot
              kind="thumbnail"
              ownerType="show"
              ownerId={show.id}
              current={show.artwork.thumbnail}
              spec={reference.artwork_specs.thumbnail}
              onUploaded={refresh}
            />
            <p className="muted small">
              Poster and banner are required to publish. A thumbnail here is optional —
              it stands in for any episode that has none of its own, which is usually
              faster than uploading the same picture onto twenty episodes.
            </p>
          </div>
        </section>
      </div>

      <section className="panel">
        <h2>Seasons &amp; episodes</h2>
        {show.seasons.length === 0 && (
          <EmptyState message="This show has no seasons yet — add one to start adding episodes." />
        )}
        {show.seasons.map((season) => (
          <div key={season.id} className="season">
            <SeasonHeading season={season} onSaved={refresh} notify={notify} />
            <ul className="episodes">
              {season.episodes.map((ep) => (
                <EpisodeEditor
                  key={ep.id}
                  episode={ep}
                  reference={reference}
                  onSaved={refresh}
                  notify={notify}
                />
              ))}
            </ul>
            <div className="add-row">
              <AddEpisode
                season={season}
                reference={reference}
                onAdded={refresh}
                notify={notify}
              />
            </div>
          </div>
        ))}
        <AddSeason show={show} onAdded={refresh} notify={notify} />
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
  reference,
  onSave,
  saving,
}: {
  show: ShowDetail
  reference: ReferenceConfig
  onSave: (body: Record<string, unknown>) => void
  saving: boolean
}) {
  const [title, setTitle] = useState(show.title)
  const [synopsis, setSynopsis] = useState(show.synopsis ?? '')
  const [section, setSection] = useState(show.section ?? '')
  const [featured, setFeatured] = useState(show.featured)
  const [categories, setCategories] = useState<string[]>(show.categories)

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
          {reference.sections.map((s) => (
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
      <fieldset className="checks-field">
        <legend>Categories</legend>
        <div className="checks">
          {reference.categories.map((c) => (
            <label key={c} className="inline">
              <input
                type="checkbox"
                checked={categories.includes(c)}
                onChange={(e) =>
                  setCategories((prev) =>
                    e.target.checked
                      ? [...prev, c].sort()
                      : prev.filter((existing) => existing !== c),
                  )
                }
              />
              {c}
            </label>
          ))}
        </div>
        <span className="muted small">Categories drive the viewer's filters and search.</span>
      </fieldset>
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
            categories,
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
// "1 problem" / "2 problems". The publish page is read by editors all day; "1 warnings"
// reads like a defect in the tool rather than a count, which undermines trust in the
// rest of the report.
function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

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
                    ? `${plural(report.data.counts.blockers, 'problem')} ${report.data.counts.blockers === 1 ? 'is' : 'are'} blocking publish`
                    : 'Everything is ready to publish'}
                </strong>
                <p className="muted small">
                  {plural(report.data.counts.warnings, 'warning')} ·{' '}
                  {plural(report.data.counts.shows_with_issues, 'show with issues', 'shows with issues')}
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
            <p className="muted small">
              Draft shows are listed too. A draft can't block a publish it isn't part of,
              so its problems appear as warnings — that list is what it would take to
              bring the show live.
            </p>
            {report.data.shows.length === 0 && <EmptyState message="Nothing to fix." />}
            {report.data.shows.map((s) => (
              <div key={s.show_id} className="report-show">
                <h3>
                  <Link to={`/shows/${s.show_id}`}>{s.title}</Link>{' '}
                  {s.status === 'draft' && <span className="chip draft">draft</span>}{' '}
                  <span className="muted small">
                    {plural(s.blocker_count, 'blocker')} · {plural(s.warning_count, 'warning')}
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
          <Route path="/shows/new" element={<NewShow />} />
          <Route path="/shows/:id" element={<ShowEditor me={me} />} />
          <Route path="/publish" element={<PublishPage me={me} />} />
        </Routes>
      </main>
    </div>
  )
}
