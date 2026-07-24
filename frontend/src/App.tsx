import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowDownToLine,
  Ban,
  Box,
  Check,
  ChevronDown,
  CircleX,
  Clock3,
  Cloud,
  Download,
  Filter,
  HardDrive,
  KeyRound,
  ListFilter,
  LoaderCircle,
  PackageCheck,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  SlidersHorizontal,
  Wifi,
} from 'lucide-react'
import {
  HashRouter,
  Navigate,
  Route,
  Routes,
  useSearchParams,
} from 'react-router-dom'
import { api } from './api'
import { AccountAdmin, AuthScreen, SavedPage, UploadsPage } from './components/AccountPages'
import { LocalDrawer, ModelDrawer } from './components/Drawers'
import { HubModelRow, LocalModelRow } from './components/RepositoryRows'
import Shell from './components/Shell'
import type {
  AuthStatus,
  DownloadJob,
  Health,
  HubModel,
  LocalModel,
  RuntimeJob,
  RuntimeTarget,
  User,
} from './types'
import { formatBytes, relativeTime } from './utils'

type ToastTone = 'success' | 'error'
type ToastHandler = (message: string, tone?: ToastTone) => void

const taskOptions = [
  ['text-generation', 'Text Generation'],
  ['image-text-to-text', 'Image-Text-to-Text'],
  ['text-to-image', 'Text-to-Image'],
  ['feature-extraction', 'Embeddings'],
  ['automatic-speech-recognition', 'Speech Recognition'],
]
const libraryOptions = [
  ['transformers', 'Transformers'],
  ['gguf', 'GGUF'],
  ['diffusers', 'Diffusers'],
  ['safetensors', 'Safetensors'],
]
const appOptions = [
  ['vllm', 'vLLM'],
  ['llama.cpp', 'llama.cpp'],
  ['ollama', 'Ollama'],
  ['lm-studio', 'LM Studio'],
]
const parameterOptions = [
  ['max:1B', '< 1B'],
  ['min:1B,max:7B', '1B – 7B'],
  ['min:7B,max:32B', '7B – 32B'],
  ['min:32B,max:128B', '32B – 128B'],
  ['min:128B', '> 128B'],
]

function FilterGroup({
  title,
  options,
  value,
  onChange,
}: {
  title: string
  options: string[][]
  value: string
  onChange: (value: string) => void
}) {
  return (
    <section className="filter-group">
      <h3>{title}</h3>
      {options.map(([id, label]) => (
        <button
          type="button"
          key={id}
          className={value === id ? 'selected' : ''}
          onClick={() => onChange(value === id ? '' : id)}
        >
          <span className="filter-check">{value === id && <Check size={12} />}</span>
          {label}
        </button>
      ))}
    </section>
  )
}

function ModelsPage({
  onToast,
  refreshDownloads,
}: {
  onToast: ToastHandler
  refreshDownloads: () => void
}) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState(searchParams.get('search') || '')
  const [task, setTask] = useState('')
  const [library, setLibrary] = useState('')
  const [appFilter, setAppFilter] = useState('')
  const [parameters, setParameters] = useState('')
  const [sort, setSort] = useState('trending')
  const [models, setModels] = useState<HubModel[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false)
  const [saving, setSaving] = useState<string | null>(null)

  useEffect(() => {
    const next = searchParams.get('search') || ''
    setSearch(next)
  }, [searchParams])

  const fetchModels = useCallback(() => {
    const params = new URLSearchParams({
      search,
      sort,
      limit: '30',
    })
    if (task) params.set('task', task)
    if (library) params.set('library', library)
    if (appFilter) params.set('app', appFilter)
    if (parameters) params.set('parameters', parameters)
    setLoading(true)
    setError('')
    api
      .searchModels(params)
      .then((payload) => setModels(payload.items))
      .catch((reason) => setError(reason.message))
      .finally(() => setLoading(false))
  }, [appFilter, library, parameters, search, sort, task])

  useEffect(() => {
    const timer = window.setTimeout(fetchModels, 350)
    return () => window.clearTimeout(timer)
  }, [fetchModels])

  function submitSearch() {
    const next = new URLSearchParams(searchParams)
    if (search.trim()) next.set('search', search.trim())
    else next.delete('search')
    setSearchParams(next)
  }

  const activeFilters = [task, library, appFilter, parameters].filter(Boolean).length

  async function toggleSaved(model: HubModel) {
    setSaving(model.id)
    try {
      if (model.saved) {
        await api.unsaveModel(model.id)
        onToast(`${model.id} was removed from your saved library.`)
      } else {
        await api.saveModel({
          repo_id: model.id,
          metadata: {
            author: model.author,
            pipeline_tag: model.pipeline_tag,
            library_name: model.library_name,
            license: model.license,
            parameter_count: model.parameter_count,
            last_modified: model.last_modified,
            local: model.local,
          },
        })
        onToast(`${model.id} was saved for later.`)
      }
      setModels((current) =>
        current.map((item) =>
          item.id === model.id ? { ...item, saved: !model.saved } : item,
        ),
      )
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to update saved models', 'error')
    } finally {
      setSaving(null)
    }
  }

  return (
    <>
      <div className="catalog-layout">
        <aside className={mobileFiltersOpen ? 'filters mobile-open' : 'filters'}>
          <div className="filters-heading">
            <Filter size={16} />
            <span>Models</span>
            {activeFilters > 0 && <em>{activeFilters}</em>}
            <button
              type="button"
              className="filter-mobile-close"
              onClick={() => setMobileFiltersOpen(false)}
              aria-label="Close filters"
            >
              <CircleX size={18} />
            </button>
          </div>
          <FilterGroup title="Tasks" options={taskOptions} value={task} onChange={setTask} />
          <FilterGroup
            title="Libraries & formats"
            options={libraryOptions}
            value={library}
            onChange={setLibrary}
          />
          <FilterGroup title="Local apps" options={appOptions} value={appFilter} onChange={setAppFilter} />
          <FilterGroup
            title="Parameters"
            options={parameterOptions}
            value={parameters}
            onChange={setParameters}
          />
          {activeFilters > 0 && (
            <button
              className="clear-filters"
              onClick={() => {
                setTask('')
                setLibrary('')
                setAppFilter('')
                setParameters('')
              }}
            >
              Reset filters
            </button>
          )}
        </aside>

        <section className="catalog-content">
          <div className="page-heading catalog-heading">
            <div>
              <span className="eyebrow">Live from the Hugging Face Hub</span>
              <h1>Explore models</h1>
              <p>Compare useful metadata at a glance, then choose the exact files your local stack needs.</p>
            </div>
            <a href="https://huggingface.co/models" target="_blank" rel="noreferrer" className="quiet-link">
              View on Hugging Face
            </a>
          </div>

          <div className="catalog-tools">
            <div className="catalog-search">
              <Search size={18} />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') submitSearch()
                }}
                placeholder="Search model names, authors, and tags"
              />
              {search && (
                <button type="button" onClick={() => setSearch('')} aria-label="Clear search">
                  <CircleX size={16} />
                </button>
              )}
            </div>
            <label className="sort-control">
              <SlidersHorizontal size={15} />
              <select value={sort} onChange={(event) => setSort(event.target.value)} aria-label="Sort models">
                <option value="trending">Trending</option>
                <option value="downloads">Most downloaded</option>
                <option value="updated">Recently updated</option>
                <option value="likes">Most liked</option>
              </select>
              <ChevronDown size={14} />
            </label>
            <button
              type="button"
              className="secondary-button mobile-filter-button"
              onClick={() => setMobileFiltersOpen(true)}
            >
              <ListFilter size={15} />
              Filters {activeFilters > 0 ? `(${activeFilters})` : ''}
            </button>
          </div>

          <div className="results-line">
            <span>{loading ? 'Contacting the Hub…' : `${models.length} models shown`}</span>
            <span>Public metadata is read live from huggingface.co</span>
          </div>

          {error && (
            <div className="page-error">
              <AlertCircle size={18} />
              <div>
                <strong>Could not reach Hugging Face</strong>
                <p>{error}</p>
              </div>
              <button onClick={fetchModels}>Retry</button>
            </div>
          )}
          {loading ? (
            <div className="model-card-grid skeleton-card-grid" aria-label="Loading models">
              {Array.from({ length: 8 }).map((_, index) => (
                <div key={index} className="model-card skeleton-card">
                  <div className="skeleton skeleton-visual" />
                  <div className="model-card-body">
                    <div className="skeleton line-short" />
                    <div className="skeleton line-strong" />
                    <div className="skeleton line-medium" />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="model-card-grid">
              {models.map((model) => (
                <HubModelRow
                  key={model.id}
                  model={model}
                  onOpen={setSelected}
                  onDownload={setSelected}
                  onSave={toggleSaved}
                  saving={saving === model.id}
                />
              ))}
              {!error && models.length === 0 && (
                <div className="empty-state">
                  <Box size={30} />
                  <h2>No matching models</h2>
                  <p>Clear a filter or try a broader repository name.</p>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
      <ModelDrawer
        repoId={selected}
        onClose={() => setSelected(null)}
        onQueued={(repoId) => {
          onToast(`${repoId} was added to the download queue.`)
          refreshDownloads()
        }}
      />
    </>
  )
}

function LocalPage({ onToast, user }: { onToast: ToastHandler; user: User }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [models, setModels] = useState<LocalModel[]>([])
  const [totalBytes, setTotalBytes] = useState(0)
  const [query, setQuery] = useState('')
  const [scanning, setScanning] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setError('')
    Promise.all([api.health(), api.localModels(query)])
      .then(([healthPayload, modelPayload]) => {
        setHealth(healthPayload)
        setModels(modelPayload.items)
        setTotalBytes(modelPayload.total_bytes)
      })
      .catch((reason) => setError(reason.message))
  }, [query])

  useEffect(() => {
    const timer = window.setTimeout(load, 250)
    return () => window.clearTimeout(timer)
  }, [load])

  async function scan() {
    setScanning(true)
    try {
      const result = await api.scanLocalModels()
      onToast(`Storage scan complete: ${result.count} model${result.count === 1 ? '' : 's'} indexed.`)
      load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'NAS scan failed', 'error')
    } finally {
      setScanning(false)
    }
  }

  const capacityPercent = health
    ? Math.min(100, ((health.storage.total_bytes - health.storage.free_bytes) / health.storage.total_bytes) * 100)
    : 0

  return (
    <>
      <div className="standard-page">
        <div className="page-heading">
          <div>
            <span className="eyebrow">Indexed from local cache and durable storage</span>
            <h1>Local library</h1>
            <p>Browse models on disk, NAS, or S3 and restore remote copies only when you need them.</p>
          </div>
          <button className="secondary-button" onClick={scan} disabled={scanning}>
            {scanning ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}
            {scanning ? 'Scanning…' : 'Scan storage'}
          </button>
        </div>

        <section className="storage-strip">
          <div className="storage-icon">
            {health?.object_storage.enabled ? <Cloud size={23} /> : <HardDrive size={23} />}
          </div>
          <div className="storage-main">
            <div className="storage-title">
              <strong>
                {health?.object_storage.enabled
                  ? health.object_storage.connected ? 'S3 storage online' : 'S3 storage needs attention'
                  : health?.storage.writable ? 'Model storage online' : 'Model storage needs attention'}
              </strong>
              <code>
                {health?.object_storage.enabled
                  ? `s3://${health.object_storage.bucket}/${health.object_storage.prefix || ''}`
                  : health?.storage.path || '/models'}
              </code>
            </div>
            <div className="capacity-track" aria-label={`${capacityPercent.toFixed(0)} percent of volume used`}>
              <span style={{ width: `${capacityPercent}%` }} />
            </div>
            <div className="storage-meta">
              <span>{health ? `${formatBytes(health.storage.free_bytes)} free in local cache` : 'Reading volume…'}</span>
              <span>{formatBytes(totalBytes)} indexed models</span>
            </div>
          </div>
          <span className={
            health?.storage.writable && health?.object_storage.connected
              ? 'status-pill ok'
              : 'status-pill danger'
          }>
            {health?.storage.writable && health?.object_storage.connected
              ? <Check size={13} />
              : <AlertCircle size={13} />}
            {health?.object_storage.enabled
              ? health.object_storage.connected ? 'Connected' : 'Offline'
              : health?.storage.writable ? 'Writable' : 'Read only'}
          </span>
        </section>

        <div className="local-tools">
          <div className="catalog-search">
            <Search size={18} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search local models" />
          </div>
          <span>{models.length} indexed</span>
        </div>

        {error && <div className="inline-error">{error}</div>}
        <div className="repo-list bordered-list">
          {models.map((model) => (
            <LocalModelRow key={model.repo_id} model={model} onOpen={setSelected} />
          ))}
          {!error && models.length === 0 && (
            <div className="empty-state spacious">
              <HardDrive size={34} />
              <h2>Your model library is empty</h2>
              <p>Download a model, copy a repository into the cache, or connect an S3 bucket.</p>
            </div>
          )}
        </div>
      </div>
      <LocalDrawer
        repoId={selected}
        onClose={() => setSelected(null)}
        onChanged={load}
        onToast={onToast}
        canManageRuntimes={user.role === 'admin'}
      />
    </>
  )
}

const activeDownloadStatuses = ['queued', 'preparing', 'downloading']

const downloadModeLabels = {
  full: 'Full repository',
  safetensors: 'SafeTensors',
  gguf: 'GGUF selection',
  metadata: 'Metadata only',
  custom: 'Custom selection',
}

function DownloadStatus({
  job,
  onCancel,
  cancelling,
}: {
  job: DownloadJob
  onCancel?: (job: DownloadJob) => void
  cancelling?: boolean
}) {
  const isActive = activeDownloadStatuses.includes(job.status)
  const remaining = Math.max(0, job.total_bytes - job.downloaded_bytes)
  const eta = job.speed_bps > 0 ? Math.round(remaining / job.speed_bps) : 0
  const etaLabel = eta
    ? eta > 3600
      ? `${Math.round(eta / 3600)}h remaining`
      : eta > 60
        ? `${Math.round(eta / 60)}m remaining`
        : `${eta}s remaining`
    : ''
  const modeLabel = downloadModeLabels[job.payload.mode || 'full']

  return (
    <article className="download-row">
      <div className={`download-state-icon ${job.status}`}>
        {job.status === 'complete' ? (
          <PackageCheck size={19} />
        ) : job.status === 'failed' ? (
          <AlertCircle size={18} />
        ) : job.status === 'cancelled' ? (
          <Ban size={18} />
        ) : (
          <ArrowDownToLine size={18} />
        )}
      </div>
      <div className="download-main">
        <div className="download-title">
          <div>
            <h3>{job.repo_id}</h3>
            <span>{modeLabel} · revision {job.revision}</span>
          </div>
          <div className="download-title-actions">
            <strong className={`job-status ${job.status}`}>{job.status}</strong>
            {isActive && onCancel && (
              <button
                type="button"
                className="cancel-download-button"
                onClick={() => onCancel(job)}
                disabled={cancelling}
              >
                {cancelling ? <LoaderCircle size={14} className="spin" /> : <Ban size={14} />}
                {cancelling ? 'Cancelling…' : 'Cancel'}
              </button>
            )}
          </div>
        </div>
        {isActive && (
          <>
            <div className="job-progress" aria-label={`${job.progress.toFixed(0)} percent downloaded`}>
              <span style={{ width: `${Math.max(job.progress, job.status === 'preparing' ? 2 : 0)}%` }} />
            </div>
            <div className="download-stats">
              <span>
                {formatBytes(job.downloaded_bytes)}
                {job.total_bytes > 0 && ` of ${formatBytes(job.total_bytes)}`}
              </span>
              <span>{job.speed_bps > 0 ? `${formatBytes(job.speed_bps)}/s` : 'Preparing repository…'}</span>
              {etaLabel && <span>{etaLabel}</span>}
              {typeof job.metadata.file_count === 'number' && <span>{job.metadata.file_count} repository files</span>}
            </div>
          </>
        )}
        {job.status === 'complete' && (
          <div className="download-stats">
            <span>{formatBytes(job.downloaded_bytes)} stored</span>
            <span>Completed {relativeTime(job.completed_at)}</span>
            <code>{job.target_path}</code>
          </div>
        )}
        {job.status === 'cancelled' && (
          <div className="download-stats cancelled-copy">
            <span>{formatBytes(job.downloaded_bytes)} retained</span>
            <span>Partial files stay in place so a future download can resume.</span>
          </div>
        )}
        {job.status === 'failed' && (
          <div className="download-error">
            <AlertCircle size={15} />
            <span>{job.error}</span>
          </div>
        )}
      </div>
    </article>
  )
}

function DownloadsPage({
  jobs,
  onToast,
  refreshDownloads,
}: {
  jobs: DownloadJob[]
  onToast: ToastHandler
  refreshDownloads: () => void
}) {
  const [cancelling, setCancelling] = useState<string | null>(null)
  const active = jobs.filter((job) => activeDownloadStatuses.includes(job.status))
  const finished = jobs.filter((job) => !active.includes(job))
  const totalSpeed = active.reduce((total, job) => total + job.speed_bps, 0)
  const remainingBytes = active.reduce(
    (total, job) => total + Math.max(0, job.total_bytes - job.downloaded_bytes),
    0,
  )
  const completedCount = jobs.filter((job) => job.status === 'complete').length

  async function cancel(job: DownloadJob) {
    setCancelling(job.id)
    try {
      await api.cancelDownload(job.id)
      onToast(`${job.repo_id} was cancelled. Partial files were kept for resume.`)
      refreshDownloads()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to cancel download', 'error')
    } finally {
      setCancelling(null)
    }
  }

  return (
    <div className="standard-page downloads-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Persistent background transfers</span>
          <h1>Downloads</h1>
          <p>Monitor precise transfer activity, stop work safely, and keep partial files ready to resume.</p>
        </div>
      </div>

      <section className="transfer-overview" aria-label="Download activity summary">
        <div><span>Active transfers</span><strong>{active.length}</strong><small>queued or downloading</small></div>
        <div><span>Combined speed</span><strong>{totalSpeed > 0 ? `${formatBytes(totalSpeed)}/s` : '—'}</strong><small>across active jobs</small></div>
        <div><span>Remaining</span><strong>{remainingBytes > 0 ? formatBytes(remainingBytes) : '—'}</strong><small>known repository data</small></div>
        <div><span>Completed</span><strong>{completedCount}</strong><small>stored in the library</small></div>
      </section>

      <section className="download-section">
        <h2>Active</h2>
        <div className="download-list">
          {active.map((job) => (
            <DownloadStatus
              key={job.id}
              job={job}
              onCancel={cancel}
              cancelling={cancelling === job.id}
            />
          ))}
          {active.length === 0 && (
            <div className="empty-compact">
              <Clock3 size={18} /> No active downloads.
            </div>
          )}
        </div>
      </section>
      <section className="download-section">
        <h2>History</h2>
        <div className="download-list">
          {finished.map((job) => (
            <DownloadStatus key={job.id} job={job} />
          ))}
          {finished.length === 0 && <div className="empty-compact">Completed, cancelled, and failed jobs appear here.</div>}
        </div>
      </section>
    </div>
  )
}

const runtimeActiveStatuses = ['queued', 'preparing', 'transferring', 'loading']

function RuntimesPage() {
  const [targets, setTargets] = useState<RuntimeTarget[]>([])
  const [jobs, setJobs] = useState<RuntimeJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setError('')
    Promise.all([api.runtimeTargets(), api.runtimeJobs()])
      .then(([targetPayload, jobPayload]) => {
        setTargets(targetPayload.items)
        setJobs(jobPayload.items)
      })
      .catch((reason) => {
        const message = reason instanceof Error ? reason.message : 'Unable to read runtime targets.'
        setError(message)
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, 2000)
    return () => window.clearInterval(timer)
  }, [load])

  return (
    <div className="standard-page runtimes-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Network inference destinations</span>
          <h1>Runtimes</h1>
          <p>Send cached models to Ollama over HTTP or switch a vLLM rig through the authenticated runtime agent.</p>
        </div>
        <button type="button" className="secondary-button" onClick={load} disabled={loading}>
          {loading ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}
          Refresh
        </button>
      </div>

      {error && <div className="inline-error">{error}</div>}

      <section className="runtime-target-grid">
        {targets.map((target) => (
          <article key={target.id} className="runtime-target-card">
            <div className={`runtime-kind-icon ${target.kind}`}>
              {target.kind === 'ollama' ? <Cloud size={20} /> : <Server size={20} />}
            </div>
            <div>
              <span>{target.kind === 'ollama' ? 'Ollama' : 'vLLM agent'}</span>
              <h2>{target.name}</h2>
              <code>{target.base_url}</code>
              <p>
                {target.transfer_mode === 'blob-upload'
                  ? `Model blobs transfer over the LAN · keep alive ${target.keep_alive || '5m'}`
                  : `Shared model root ${target.remote_model_root}`}
              </p>
            </div>
            <span className="status-pill ok"><Check size={13} /> Configured</span>
          </article>
        ))}
        {!loading && targets.length === 0 && (
          <div className="empty-state spacious runtime-empty">
            <Server size={34} />
            <h2>No runtime destinations configured</h2>
            <p>Add Ollama or vLLM targets through <code>RUNTIME_TARGETS_JSON</code>, then restart HuggingHack.</p>
          </div>
        )}
      </section>

      <section className="runtime-history">
        <div className="section-heading-line">
          <div>
            <span className="eyebrow">Persistent history</span>
            <h2>Runtime jobs</h2>
          </div>
          <span>{jobs.filter((job) => runtimeActiveStatuses.includes(job.status)).length} active</span>
        </div>
        <div className="download-list">
          {jobs.map((job) => (
            <article key={job.id} className="runtime-job-row">
              <div className={`runtime-state-icon ${job.status}`}>
                {job.status === 'failed'
                  ? <AlertCircle size={18} />
                  : job.status === 'ready'
                    ? <Check size={18} />
                    : <Server size={18} />}
              </div>
              <div className="runtime-job-main">
                <div className="runtime-job-heading">
                  <div>
                    <h3>{job.runtime_model_name}</h3>
                    <span>{job.repo_id} → {job.target_name}</span>
                  </div>
                  <strong className={`job-status ${job.status}`}>{job.status}</strong>
                </div>
                <p>{job.error || job.message}</p>
                {runtimeActiveStatuses.includes(job.status) && (
                  <>
                    <div className="job-progress">
                      <span style={{ width: `${job.progress}%` }} />
                    </div>
                    <div className="download-stats">
                      <span>{job.progress.toFixed(0)}%</span>
                      {job.target_kind === 'ollama' && job.total_bytes > 0 && (
                        <span>{formatBytes(job.processed_bytes)} of {formatBytes(job.total_bytes)}</span>
                      )}
                      <span>Updated {relativeTime(job.updated_at)}</span>
                    </div>
                  </>
                )}
                {!runtimeActiveStatuses.includes(job.status) && (
                  <div className="download-stats">
                    <span>{job.target_kind}</span>
                    <span>Finished {relativeTime(job.completed_at || job.updated_at)}</span>
                    {job.source_file && <code>{job.source_file}</code>}
                  </div>
                )}
              </div>
            </article>
          ))}
          {!loading && jobs.length === 0 && (
            <div className="empty-compact">Load a model from its local-library drawer to create the first runtime job.</div>
          )}
        </div>
      </section>
    </div>
  )
}

function SettingsPage({
  user,
  onToast,
}: {
  user: User
  onToast: ToastHandler
}) {
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined)
  }, [])
  return (
    <div className="standard-page settings-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Runtime configuration</span>
          <h1>Settings</h1>
          <p>HuggingHack is configured with environment variables so credentials never enter the browser.</p>
        </div>
      </div>

      <div className="settings-grid">
        <section className="settings-section">
          <div className="settings-section-title">
            <Server size={20} />
            <div>
              <h2>{health?.object_storage.enabled ? 'S3 + local cache' : 'Storage mount'}</h2>
              <p>
                {health?.object_storage.enabled
                  ? 'S3 is durable storage; /models is the working cache used by inference engines.'
                  : 'The container sees your configured host folder as /models.'}
              </p>
            </div>
          </div>
          <dl className="settings-list">
            <div>
              <dt>Cache path</dt>
              <dd><code>{health?.storage.path || '/models'}</code></dd>
            </div>
            <div>
              <dt>Metadata database</dt>
              <dd>{health?.database_backend === 'postgresql' ? 'PostgreSQL' : 'SQLite'}</dd>
            </div>
            {health?.object_storage.enabled && (
              <>
                <div>
                  <dt>S3 location</dt>
                  <dd>
                    <code>
                      s3://{health.object_storage.bucket}/{health.object_storage.prefix || ''}
                    </code>
                  </dd>
                </div>
                <div>
                  <dt>Connection</dt>
                  <dd className={health.object_storage.connected ? 'good-text' : 'danger-text'}>
                    {health.object_storage.connected ? 'Connected' : health.object_storage.error || 'Unavailable'}
                  </dd>
                </div>
              </>
            )}
            <div>
              <dt>Write access</dt>
              <dd className={health?.storage.writable ? 'good-text' : 'danger-text'}>
                {health?.storage.writable ? 'Ready' : 'Not writable'}
              </dd>
            </div>
            <div>
              <dt>Free space</dt>
              <dd>{health ? formatBytes(health.storage.free_bytes) : 'Reading…'}</dd>
            </div>
          </dl>
          <div className="code-block">
            <span>.env</span>
            <code>
              {health?.object_storage.enabled
                ? 'MODEL_STORAGE_BACKEND=s3\nS3_BUCKET=my-models\nS3_PREFIX=models'
                : 'MODEL_STORAGE_PATH=/volume1/AI/models'}
            </code>
          </div>
        </section>

        <section className="settings-section">
          <div className="settings-section-title">
            <KeyRound size={20} />
            <div>
              <h2>Hugging Face access</h2>
              <p>A read token is only needed for private or gated repositories.</p>
            </div>
          </div>
          <dl className="settings-list">
            <div>
              <dt>Endpoint</dt>
              <dd><code>{health?.hf_endpoint || 'https://huggingface.co'}</code></dd>
            </div>
            <div>
              <dt>HF token</dt>
              <dd className={health?.hf_token_configured ? 'good-text' : ''}>
                {health?.hf_token_configured ? 'Configured' : 'Not configured'}
              </dd>
            </div>
          </dl>
          <div className="code-block">
            <span>.env</span>
            <code>HF_TOKEN=hf_your_read_token</code>
          </div>
        </section>
        <section className="settings-section">
          <div className="settings-section-title">
            <Server size={20} />
            <div>
              <h2>Network runtimes</h2>
              <p>Configured destinations can receive cached models through the runtime API.</p>
            </div>
          </div>
          <dl className="settings-list">
            <div>
              <dt>Destinations</dt>
              <dd>{health?.runtime_target_count ?? 0} configured</dd>
            </div>
            <div>
              <dt>Automation token</dt>
              <dd className={health?.runtime_api_token_configured ? 'good-text' : ''}>
                {health?.runtime_api_token_configured ? 'Configured' : 'Browser session only'}
              </dd>
            </div>
          </dl>
          <div className="code-block">
            <span>.env</span>
            <code>{'RUNTIME_TARGETS_JSON=[...]\nRUNTIME_API_TOKEN=use-a-long-random-secret'}</code>
          </div>
        </section>
        <AccountAdmin user={user} onToast={onToast} />
      </div>

      <section className="settings-section security-section">
        <div className="settings-section-title">
          <ShieldAlert size={20} />
          <div>
            <h2>Local network safety</h2>
            <p>Downloaded model files are data until another program loads them.</p>
          </div>
        </div>
        <div className="security-columns">
          <div>
            <strong>HuggingHack never</strong>
            <p>imports model code, unpickles weights, executes repositories, or sends your NAS files elsewhere.</p>
          </div>
          <div>
            <strong>Before exposing it publicly</strong>
            <p>keep accounts enabled, serve it through an HTTPS reverse proxy, and turn on secure cookies.</p>
          </div>
          <div>
            <strong>For gated models</strong>
            <p>accept the publisher's terms on Hugging Face first, then use a read-only token.</p>
          </div>
        </div>
      </section>

      <div className="runtime-line">
        <Wifi size={15} />
        HuggingHack {health?.version || '1.0.0'} · unofficial, local-first, and not affiliated with Hugging Face
      </div>
    </div>
  )
}

function Application({
  authStatus,
  onAuthChange,
}: {
  authStatus: AuthStatus
  onAuthChange: (status: AuthStatus) => void
}) {
  const [jobs, setJobs] = useState<DownloadJob[]>([])
  const [toast, setToast] = useState<{ message: string; tone: ToastTone } | null>(null)

  const showToast = useCallback((message: string, tone: ToastTone = 'success') => {
    setToast({ message, tone })
  }, [])

  const refreshDownloads = useCallback(() => {
    api
      .downloads()
      .then((payload) => setJobs(payload.items))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    refreshDownloads()
    const timer = window.setInterval(refreshDownloads, 1800)
    return () => window.clearInterval(timer)
  }, [refreshDownloads])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 4500)
    return () => window.clearTimeout(timer)
  }, [toast])

  const activeDownloads = useMemo(
    () => jobs.filter((job) => ['queued', 'preparing', 'downloading'].includes(job.status)).length,
    [jobs],
  )

  const user = authStatus.user as User

  async function logout() {
    try {
      await api.logout()
    } finally {
      onAuthChange({ ...authStatus, user: null, csrf_token: null })
    }
  }

  return (
    <Shell activeDownloads={activeDownloads} user={user} onLogout={logout}>
      <Routes>
        <Route path="/" element={<Navigate to="/models" replace />} />
        <Route
          path="/models"
          element={<ModelsPage onToast={showToast} refreshDownloads={refreshDownloads} />}
        />
        <Route path="/local" element={<LocalPage onToast={showToast} user={user} />} />
        <Route path="/saved" element={<SavedPage onToast={showToast} />} />
        <Route path="/uploads" element={<UploadsPage user={user} onToast={showToast} />} />
        <Route
          path="/downloads"
          element={
            <DownloadsPage
              jobs={jobs}
              onToast={showToast}
              refreshDownloads={refreshDownloads}
            />
          }
        />
        <Route
          path="/runtimes"
          element={
            user.role === 'admin'
              ? <RuntimesPage />
              : <Navigate to="/local" replace />
          }
        />
        <Route path="/settings" element={<SettingsPage user={user} onToast={showToast} />} />
        <Route path="*" element={<Navigate to="/models" replace />} />
      </Routes>
      {toast && (
        <div className={`toast ${toast.tone}`} role="status">
          {toast.tone === 'error' ? <AlertCircle size={16} /> : <Check size={16} />}
          {toast.message}
        </div>
      )}
    </Shell>
  )
}

export default function App() {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [error, setError] = useState('')

  const refreshAuth = useCallback(() => {
    setError('')
    api
      .authStatus()
      .then(setStatus)
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Unable to reach HuggingHack'))
  }, [])

  useEffect(() => {
    refreshAuth()
    window.addEventListener('hugginghack:unauthorized', refreshAuth)
    return () => window.removeEventListener('hugginghack:unauthorized', refreshAuth)
  }, [refreshAuth])

  if (error) {
    return (
      <main className="auth-layout auth-unavailable">
        <section className="auth-card">
          <AlertCircle size={28} />
          <h1>HuggingHack is unavailable</h1>
          <p>{error}</p>
          <button className="secondary-button" onClick={refreshAuth}><RefreshCw size={16} /> Retry</button>
        </section>
      </main>
    )
  }

  if (!status) {
    return (
      <main className="app-loading">
        <img src="/hugginghack-mark.svg" alt="" />
        <LoaderCircle size={23} className="spin" />
        <span>Opening your model library…</span>
      </main>
    )
  }

  if (status.setup_required || !status.user) {
    return <AuthScreen setup={status.setup_required} onAuthenticated={setStatus} />
  }

  return (
    <HashRouter>
      <Application authStatus={status} onAuthChange={setStatus} />
    </HashRouter>
  )
}
