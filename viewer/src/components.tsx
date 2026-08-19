import { useState } from 'react'
import type { Entry } from './api'

/**
 * Every image on the viewer goes through this component.
 *
 * Slow or missing images are the stated concern, and the answer is three things:
 * the box reserves its aspect ratio before the image loads (so nothing on the page
 * jumps once it arrives), images below the fold load lazily, and a failed load falls
 * back to a titled tile rather than a broken-image icon. A child on a slow connection
 * sees a stable, labelled layout instead of a page that reflows under their finger.
 */
export function Artwork({
  src,
  alt,
  ratio,
  className = '',
}: {
  src?: string | null
  alt: string
  ratio: string
  className?: string
}) {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)

  return (
    <div className={`artwork ${className}`} style={{ aspectRatio: ratio }}>
      {src && !failed ? (
        <>
          {!loaded && <div className="artwork-placeholder" aria-hidden="true" />}
          <img
            src={src}
            alt={alt}
            loading="lazy"
            onLoad={() => setLoaded(true)}
            onError={() => setFailed(true)}
            style={{ opacity: loaded ? 1 : 0 }}
          />
        </>
      ) : (
        <div className="artwork-fallback">
          <span>{alt}</span>
        </div>
      )}
    </div>
  )
}

export function LanguageBadges({ languages }: { languages: string[] }) {
  return (
    <span className="badges">
      {languages.map((l) => (
        <span key={l} className="badge">
          {l.toUpperCase()}
        </span>
      ))}
    </span>
  )
}

export function formatDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return ''
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/**
 * An episode row. When an entry has more than one language, the toggle switches which
 * variant's metadata is displayed — that is what the grouping is *for*, so it has to be
 * visible in the UI, not just in the JSON.
 */
export function EpisodeRow({ entry }: { entry: Entry }) {
  const [lang, setLang] = useState(
    entry.languages.includes('en') ? 'en' : entry.languages[0],
  )
  const variant = entry.variants[lang] ?? {
    title: entry.title,
    duration_seconds: entry.duration_seconds,
    thumbnail: entry.thumbnail,
    episode_id: entry.entry_id,
  }

  return (
    <li className="episode">
      <Artwork src={variant.thumbnail} alt={variant.title} ratio="16 / 9" className="episode-thumb" />
      <div className="episode-body">
        <div className="episode-head">
          <h4>
            {entry.episode_number}. {variant.title}
          </h4>
          <LanguageBadges languages={entry.languages} />
        </div>
        {entry.synopsis && <p className="muted">{entry.synopsis}</p>}
        <div className="episode-meta">
          <span className="muted">{formatDuration(variant.duration_seconds)}</span>
          {entry.languages.length > 1 && (
            <label className="lang-select">
              <span className="sr-only">Language for {entry.title}</span>
              <select value={lang} onChange={(e) => setLang(e.target.value)}>
                {entry.languages.map((l) => (
                  <option key={l} value={l}>
                    {l.toUpperCase()}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>
    </li>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <div className="state">{label}</div>
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state error">
      <p>{message}</p>
      {onRetry && (
        <button onClick={onRetry} className="btn">
          Try again
        </button>
      )}
    </div>
  )
}

export function EmptyState({ message }: { message: string }) {
  return <div className="state">{message}</div>
}
