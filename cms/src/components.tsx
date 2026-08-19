import { useRef, useState } from 'react'
import { ApiError, api, type ArtworkRecord } from './api'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <div className="state">{label}</div>
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state error">
      <p>{message}</p>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

export function EmptyState({ message }: { message: string }) {
  return <div className="state">{message}</div>
}

/** Shown instead of a page when the signed-in role may not use it. */
export function PermissionDenied({ message }: { message: string }) {
  return (
    <div className="panel denied">
      <strong>You don't have permission for this</strong>
      <p>{message}</p>
    </div>
  )
}

export function StatusChip({ status }: { status: string }) {
  return <span className={`chip ${status}`}>{status}</span>
}

export function Toast({ toast }: { toast: { kind: 'ok' | 'err'; text: string } | null }) {
  if (!toast) return null
  return <div className={`toast ${toast.kind}`}>{toast.text}</div>
}

/**
 * One artwork slot — built once, used for all three kinds on shows and episodes.
 *
 * It states the required dimensions up front, pre-checks the file in the browser so an
 * editor gets an instant answer, and then **always uploads anyway and shows the
 * server's verdict**. The client check is a convenience; the server is the authority,
 * and when they disagree it is the server's message that is displayed.
 */
export function ArtworkSlot({
  kind,
  ownerType,
  ownerId,
  current,
  spec,
  onUploaded,
}: {
  kind: 'poster' | 'banner' | 'thumbnail'
  ownerType: 'show' | 'episode'
  ownerId: number
  current?: ArtworkRecord
  spec: { target_px: [number, number]; aspect: string; max_kb: number }
  onUploaded: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [hint, setHint] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const [w, h] = spec.target_px

  async function preCheck(file: File): Promise<string | null> {
    if (file.size > spec.max_kb * 1024) {
      return `This file is ${Math.round(file.size / 1024)} KB — the limit is ${spec.max_kb} KB.`
    }
    return new Promise((resolve) => {
      const img = new Image()
      const url = URL.createObjectURL(file)
      img.onload = () => {
        URL.revokeObjectURL(url)
        resolve(
          img.width === w && img.height === h
            ? null
            : `This image is ${img.width}x${img.height}; it needs to be ${w}x${h}.`,
        )
      }
      img.onerror = () => {
        URL.revokeObjectURL(url)
        resolve('That file does not look like an image.')
      }
      img.src = url
    })
  }

  async function handleFile(file: File) {
    setError(null)
    setHint(null)
    setBusy(true)
    try {
      // Instant local feedback, then upload regardless — the server decides.
      const localProblem = await preCheck(file)
      if (localProblem) setHint(localProblem)

      const form = new FormData()
      form.append('file', file)
      form.append('owner_type', ownerType)
      form.append('owner_id', String(ownerId))
      form.append('kind', kind)
      await api.uploadArtwork(form)
      setHint(null)
      onUploaded()
    } catch (e) {
      setHint(null)
      setError(e instanceof ApiError ? e.message : 'The upload failed. Please try again.')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <div className="slot">
      <div className="slot-head">
        <strong>{kind}</strong>
        <span className="muted small">
          {w}x{h} ({spec.aspect}), max {spec.max_kb} KB
        </span>
      </div>

      <div
        className="slot-preview"
        style={{ aspectRatio: `${w} / ${h}` }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault()
          const file = e.dataTransfer.files?.[0]
          if (file) void handleFile(file)
        }}
      >
        {current ? (
          <img src={current.url} alt={`${kind} preview`} />
        ) : (
          <span className="muted small">No {kind} yet — drop an image here</span>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png"
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void handleFile(file)
        }}
      />

      {busy && <p className="muted small">Uploading…</p>}
      {hint && <p className="warn small">{hint}</p>}
      {error && <p className="err small">{error}</p>}
      {current && !error && (
        <p className="muted small">
          Current: {current.width}x{current.height}, {Math.round(current.size_bytes / 1024)} KB
        </p>
      )}
    </div>
  )
}

/** Duration entered as mm:ss, stored as seconds. */
export function parseDuration(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  if (/^\d+$/.test(trimmed)) return Number(trimmed)
  const m = trimmed.match(/^(\d+):([0-5]?\d)$/)
  if (!m) return null
  return Number(m[1]) * 60 + Number(m[2])
}

export function formatDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return ''
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
