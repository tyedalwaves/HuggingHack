import 'katex/dist/katex.min.css'
import {
  Children,
  isValidElement,
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import {
  AlertTriangle,
  Archive,
  Boxes,
  Check,
  Cloud,
  CloudDownload,
  Download,
  ExternalLink,
  File,
  FileJson,
  HardDrive,
  LoaderCircle,
  LockKeyhole,
  Rocket,
  Server,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import { api } from '../api'
import {
  modelCardHeadingId,
  modelCardSanitizeSchema,
  prepareModelCardMarkdown,
  resolveModelCardUrl,
} from '../modelCard'
import { GgufInspector } from './GgufInspector'
import type {
  DownloadMode,
  HubFile,
  HubModelDetails,
  LocalModelDetails,
  RuntimeJob,
  RuntimeTarget,
} from '../types'
import { formatBytes, formatNumber, relativeTime, taskLabel } from '../utils'

interface ModelDrawerProps {
  repoId: string | null
  onClose: () => void
  onQueued: (repoId: string) => void
}

const metadataPatterns = [
  '*.json',
  '*.md',
  '*.txt',
  '*.yaml',
  '*.yml',
  '*.jinja',
  '*.model',
  '*.tiktoken',
  'LICENSE*',
  'tokenizer*',
]

const unsafePatterns = ['*.bin', '*.pt', '*.pth', '*.pkl', '*.pickle', '*.ckpt']

const downloadModes: Array<{
  id: DownloadMode
  label: string
  description: string
  icon: typeof Download
}> = [
  { id: 'full', label: 'Full repository', description: 'Every file in this revision', icon: Archive },
  { id: 'safetensors', label: 'SafeTensors', description: 'Safe weights plus runtime files', icon: ShieldCheck },
  { id: 'gguf', label: 'One GGUF', description: 'Choose one quantization file', icon: Boxes },
  { id: 'metadata', label: 'Metadata only', description: 'Config, tokenizer, card, and license', icon: FileJson },
  { id: 'custom', label: 'Custom', description: 'Use include and exclude patterns', icon: File },
]

function isMetadataFile(file: HubFile): boolean {
  const name = file.path.toLowerCase().split('/').pop() || ''
  return (
    ['.json', '.md', '.txt', '.yaml', '.yml', '.jinja', '.model', '.tiktoken'].some((suffix) =>
      name.endsWith(suffix),
    ) ||
    name.startsWith('license') ||
    name.startsWith('tokenizer')
  )
}

function childText(children: ReactNode): string {
  return Children.toArray(children)
    .map((child) => {
      if (typeof child === 'string' || typeof child === 'number') return String(child)
      if (isValidElement<{ children?: ReactNode }>(child)) return childText(child.props.children)
      return ''
    })
    .join('')
}

function scrollToCardHeading(event: React.MouseEvent<HTMLAnchorElement>, href: string) {
  event.preventDefault()
  let targetId = href.slice(1)
  try {
    targetId = decodeURIComponent(targetId)
  } catch {
    // Keep the literal fragment when a model card contains malformed escaping.
  }
  const documentRoot = event.currentTarget.closest('.model-card-document')
  const target = Array.from(documentRoot?.querySelectorAll<HTMLElement>('[id]') || []).find(
    (element) => element.id === targetId,
  )
  target?.scrollIntoView({ block: 'start' })
}

const modelCardComponents: Components = {
  h1: ({ node: _node, children, ...props }) => (
    <h1 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h1>
  ),
  h2: ({ node: _node, children, ...props }) => (
    <h2 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h2>
  ),
  h3: ({ node: _node, children, ...props }) => (
    <h3 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h3>
  ),
  h4: ({ node: _node, children, ...props }) => (
    <h4 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h4>
  ),
  h5: ({ node: _node, children, ...props }) => (
    <h5 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h5>
  ),
  h6: ({ node: _node, children, ...props }) => (
    <h6 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h6>
  ),
  a: ({ node: _node, href, children, ...props }) => {
    const isHeadingLink = href?.startsWith('#') || false
    const showExternalIcon =
      !isHeadingLink && /^https?:/i.test(href || '') && childText(children).trim().length > 0
    return (
      <a
        {...props}
        href={href}
        target={isHeadingLink ? undefined : '_blank'}
        rel={isHeadingLink ? undefined : 'noreferrer noopener'}
        onClick={isHeadingLink && href ? (event) => scrollToCardHeading(event, href) : undefined}
      >
        {children}
        {showExternalIcon && <ExternalLink size={11} aria-hidden="true" />}
      </a>
    )
  },
  img: ({ node: _node, className, src, alt, ...props }) => {
    const badge =
      typeof src === 'string' &&
      /(?:img\.shields\.io|badge(?:s)?[./_-]|colab-badge)/i.test(src)
    const classes = [className, badge ? 'model-card-badge' : ''].filter(Boolean).join(' ')
    return (
      <img
        {...props}
        className={classes || undefined}
        src={src}
        alt={alt || ''}
        loading="lazy"
        decoding="async"
      />
    )
  },
}

const ModelCardDocument = memo(function ModelCardDocument({
  source,
  sourceUrl,
  revision,
}: {
  source: string
  sourceUrl: string
  revision: string
}) {
  const preparedSource = useMemo(() => prepareModelCardMarkdown(source), [source])
  return (
    <article className="model-card-document">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: false }]]}
        rehypePlugins={[
          rehypeRaw,
          [rehypeSanitize, modelCardSanitizeSchema],
          rehypeKatex,
        ]}
        urlTransform={(url, attribute) => {
          const resolved = resolveModelCardUrl(url, attribute, sourceUrl, revision)
          return resolved === null ? null : defaultUrlTransform(resolved)
        }}
        components={modelCardComponents}
      >
        {preparedSource}
      </ReactMarkdown>
    </article>
  )
})

export function ModelDrawer({ repoId, onClose, onQueued }: ModelDrawerProps) {
  const [model, setModel] = useState<HubModelDetails | null>(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<'card' | 'files' | 'gguf'>('card')
  const [revision, setRevision] = useState('main')
  const [mode, setMode] = useState<DownloadMode>('full')
  const [ggufPath, setGgufPath] = useState('')
  const [excludeUnsafe, setExcludeUnsafe] = useState(false)
  const [include, setInclude] = useState('')
  const [exclude, setExclude] = useState('')
  const [queuing, setQueuing] = useState(false)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let ignore = false
    setModel(null)
    setError('')
    setTab('card')
    setRevision('main')
    setMode('full')
    setGgufPath('')
    setExcludeUnsafe(false)
    setInclude('')
    setExclude('')
    if (!repoId) return
    api.modelDetails(repoId)
      .then((payload) => {
        if (ignore) return
        setModel(payload)
        setGgufPath(payload.files.find((file) => file.path.toLowerCase().endsWith('.gguf'))?.path || '')
      })
      .catch((reason) => {
        if (!ignore) setError(reason.message)
      })
    return () => {
      ignore = true
    }
  }, [repoId])

  useEffect(() => {
    if (!repoId) return
    closeButtonRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose, repoId])
  const unsafeFiles = useMemo(
    () =>
      model?.files.filter((file) =>
        ['.bin', '.pt', '.pth', '.pkl', '.pickle', '.ckpt'].some((suffix) =>
          file.path.toLowerCase().endsWith(suffix),
        ),
      ) || [],
    [model],
  )

  const ggufFiles = useMemo(
    () => model?.files.filter((file) => file.path.toLowerCase().endsWith('.gguf')) || [],
    [model],
  )

  const selectedFiles = useMemo(() => {
    if (!model) return []
    if (mode === 'full') return model.files
    if (mode === 'safetensors') {
      return model.files.filter((file) => file.path.toLowerCase().endsWith('.safetensors') || isMetadataFile(file))
    }
    if (mode === 'gguf') return model.files.filter((file) => file.path === ggufPath || isMetadataFile(file))
    if (mode === 'metadata') return model.files.filter(isMetadataFile)
    return []
  }, [ggufPath, mode, model])

  const estimatedBytes = selectedFiles.reduce((total, file) => total + (file.size || 0), 0)

  if (!repoId) return null

  async function queue() {
    if (!repoId) return
    setQueuing(true)
    setError('')
    try {
      let allowPatterns: string[] = []
      let ignorePatterns = excludeUnsafe ? unsafePatterns : []
      if (mode === 'safetensors') allowPatterns = ['*.safetensors', ...metadataPatterns]
      if (mode === 'gguf') allowPatterns = [ggufPath, ...metadataPatterns].filter(Boolean)
      if (mode === 'metadata') allowPatterns = metadataPatterns
      if (mode === 'custom') {
        allowPatterns = include.split(',').map((value) => value.trim()).filter(Boolean)
        ignorePatterns = [
          ...ignorePatterns,
          ...exclude.split(',').map((value) => value.trim()).filter(Boolean),
        ]
      }
      await api.startDownload({
        repo_id: repoId,
        revision,
        allow_patterns: allowPatterns,
        ignore_patterns: ignorePatterns,
        mode,
      })
      onQueued(repoId)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to queue download')
    } finally {
      setQueuing(false)
    }
  }

  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`Model details for ${repoId}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="drawer-header">
          <div>
            <span className="eyebrow">Hugging Face model</span>
            <h2>{repoId}</h2>
          </div>
          <button ref={closeButtonRef} type="button" className="icon-button" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        </div>

        {!model && !error && (
          <div className="drawer-loading">
            <LoaderCircle size={24} className="spin" /> Reading repository metadata…
          </div>
        )}
        {error && <div className="inline-error">{error}</div>}
        {model && (
          <>
            <div className="drawer-summary">
              <div className="drawer-tags">
                {model.pipeline_tag && <span className="task-tag">{taskLabel(model.pipeline_tag)}</span>}
                {model.library_name && <span>{model.library_name}</span>}
                {model.license && <span>{model.license}</span>}
                {model.gated && (
                  <span>
                    <LockKeyhole size={12} /> Gated
                  </span>
                )}
                {model.local && (
                  <span className="local-badge">
                    <Check size={12} /> On NAS
                  </span>
                )}
              </div>
              <div className="detail-metrics">
                <span>
                  <strong>{formatNumber(model.downloads)}</strong> monthly downloads
                </span>
                <span>
                  <strong>{formatNumber(model.likes)}</strong> likes
                </span>
                <span>
                  <strong>{formatBytes(model.total_bytes)}</strong> repository
                </span>
              </div>
              <a href={model.source_url} target="_blank" rel="noreferrer" className="text-link">
                Open original on Hugging Face <ExternalLink size={13} />
              </a>
            </div>

            <div className="download-box">
              <div className="download-box-title">
                <div>
                  <h3>{model.local ? 'Pull latest revision' : 'Download to your NAS'}</h3>
                  <p>Choose exactly what belongs in /models/{repoId}</p>
                </div>
                <Download size={20} />
              </div>
              <label>
                Revision
                <input value={revision} onChange={(event) => setRevision(event.target.value)} />
              </label>
              <div className="download-mode-grid" role="radiogroup" aria-label="Download contents">
                {downloadModes.map(({ id, label, description, icon: Icon }) => {
                  const unavailable = id === 'gguf' && ggufFiles.length === 0
                  return (
                    <button
                      type="button"
                      key={id}
                      className={mode === id ? 'selected' : ''}
                      onClick={() => setMode(id)}
                      disabled={unavailable}
                      role="radio"
                      aria-checked={mode === id}
                    >
                      <Icon size={16} />
                      <span>
                        <strong>{label}</strong>
                        <small>{unavailable ? 'No GGUF found' : description}</small>
                      </span>
                    </button>
                  )
                })}
              </div>
              {mode === 'gguf' && ggufFiles.length > 0 && (
                <label className="gguf-select-label">
                  Quantization file
                  <select value={ggufPath} onChange={(event) => setGgufPath(event.target.value)}>
                    {ggufFiles.map((file) => (
                      <option value={file.path} key={file.path}>
                        {file.path} · {formatBytes(file.size)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {mode === 'custom' && (
                <div className="field-pair">
                  <label>
                    Include patterns <small>optional, comma separated</small>
                    <input
                      value={include}
                      onChange={(event) => setInclude(event.target.value)}
                      placeholder="*.safetensors, *.json"
                    />
                  </label>
                  <label>
                    Exclude patterns <small>optional</small>
                    <input
                      value={exclude}
                      onChange={(event) => setExclude(event.target.value)}
                      placeholder="original/*, *.onnx"
                    />
                  </label>
                </div>
              )}
              <label className="safety-toggle">
                <input
                  type="checkbox"
                  checked={excludeUnsafe}
                  onChange={(event) => setExcludeUnsafe(event.target.checked)}
                />
                <span>
                  <strong>Exclude pickle-compatible files</strong>
                  <small>Skip .bin, .pt, .pth, .pkl, .pickle, and .ckpt artifacts</small>
                </span>
              </label>
              <div className="download-selection-summary">
                <span>
                  {mode === 'custom'
                    ? 'Pattern-based selection'
                    : `${selectedFiles.length} of ${model.files.length} files`}
                </span>
                <strong>
                  {mode === 'custom' ? 'Size calculated during preparation' : formatBytes(estimatedBytes)}
                </strong>
              </div>
              {unsafeFiles.length > 0 && (
                <div className="security-note warning">
                  <AlertTriangle size={16} />
                  This repository includes {unsafeFiles.length} pickle-compatible file
                  {unsafeFiles.length === 1 ? '' : 's'}. Downloading is passive; only load artifacts
                  from publishers you trust.
                </div>
              )}
              <button type="button" className="download-button wide" onClick={queue} disabled={queuing}>
                {queuing ? <LoaderCircle size={16} className="spin" /> : <Download size={16} />}
                {queuing ? 'Adding to queue…' : model.local ? 'Update local copy' : 'Start download'}
              </button>
            </div>

            <div className="drawer-tabs" role="tablist" aria-label="Repository content">
              <button id="model-card-tab" role="tab" aria-selected={tab === 'card'} aria-controls="model-card-panel" className={tab === 'card' ? 'active' : ''} onClick={() => setTab('card')}>
                Model card
              </button>
              <button id="model-files-tab" role="tab" aria-selected={tab === 'files'} aria-controls="model-files-panel" className={tab === 'files' ? 'active' : ''} onClick={() => setTab('files')}>
                Files <span>{model.files.length}</span>
              </button>
              {ggufFiles.length > 0 && (
                <button id="model-gguf-tab" role="tab" aria-selected={tab === 'gguf'} aria-controls="model-gguf-panel" className={tab === 'gguf' ? 'active' : ''} onClick={() => setTab('gguf')}>
                  GGUF <span>{ggufFiles.length}</span>
                </button>
              )}
            </div>
            {tab === 'card' ? (
              <section id="model-card-panel" role="tabpanel" aria-labelledby="model-card-tab">
                {model.model_card ? (
                  <ModelCardDocument
                    source={model.model_card}
                    sourceUrl={model.source_url}
                    revision={model.revision}
                  />
                ) : (
                  <div className="empty-compact">This repository does not expose a README model card.</div>
                )}
              </section>
            ) : tab === 'files' ? (
              <div id="model-files-panel" role="tabpanel" aria-labelledby="model-files-tab" className="file-list">
                {model.files.map((file) => (
                  <div key={file.path} className="file-row">
                    <File size={15} />
                    <span title={file.path}>{file.path}</span>
                    <small>{file.size ? formatBytes(file.size) : '—'}</small>
                  </div>
                ))}
              </div>
            ) : (
              <div id="model-gguf-panel" role="tabpanel" aria-labelledby="model-gguf-tab">
                <GgufInspector repoId={repoId} revision={revision} files={ggufFiles} />
              </div>
            )}
          </>
        )}
      </aside>
    </div>
  )
}

interface LocalDrawerProps {
  repoId: string | null
  onClose: () => void
  onChanged: () => void
  onToast: (message: string, tone?: 'success' | 'error') => void
  canManageRuntimes: boolean
}

const activeRuntimeStatuses = ['queued', 'preparing', 'transferring', 'loading']

export function LocalDrawer({
  repoId,
  onClose,
  onChanged,
  onToast,
  canManageRuntimes,
}: LocalDrawerProps) {
  const [details, setDetails] = useState<LocalModelDetails | null>(null)
  const [error, setError] = useState('')
  const [changingCache, setChangingCache] = useState(false)
  const [runtimeTargets, setRuntimeTargets] = useState<RuntimeTarget[]>([])
  const [runtimeTargetId, setRuntimeTargetId] = useState('')
  const [runtimeModelName, setRuntimeModelName] = useState('')
  const [runtimeSourceFile, setRuntimeSourceFile] = useState('')
  const [runtimeJob, setRuntimeJob] = useState<RuntimeJob | null>(null)
  const [dispatching, setDispatching] = useState(false)

  useEffect(() => {
    let ignore = false
    setDetails(null)
    setError('')
    setRuntimeJob(null)
    setRuntimeModelName(repoId ? repoId.replace('/', '-').toLowerCase() : '')
    setRuntimeSourceFile('')
    if (!repoId) return
    api
      .localModelDetails(repoId)
      .then((payload) => {
        if (!ignore) setDetails(payload)
      })
      .catch((reason) => {
        if (!ignore) setError(reason.message)
      })
    return () => {
      ignore = true
    }
  }, [repoId])

  useEffect(() => {
    if (!repoId || !canManageRuntimes) {
      setRuntimeTargets([])
      setRuntimeTargetId('')
      return
    }
    api
      .runtimeTargets()
      .then((payload) => {
        setRuntimeTargets(payload.items)
        setRuntimeTargetId((current) =>
          payload.items.some((target) => target.id === current)
            ? current
            : payload.items[0]?.id || '',
        )
      })
      .catch(() => setRuntimeTargets([]))
  }, [canManageRuntimes, repoId])

  useEffect(() => {
    if (!runtimeJob || !activeRuntimeStatuses.includes(runtimeJob.status)) return
    const timer = window.setTimeout(() => {
      api
        .runtimeJob(runtimeJob.id)
        .then((next) => {
          setRuntimeJob(next)
          if (next.status === 'ready') {
            onToast(`${next.runtime_model_name} is ready on ${next.target_name}.`)
          } else if (next.status === 'failed') {
            onToast(next.error || 'Runtime load failed.', 'error')
          }
        })
        .catch(() => undefined)
    }, 1200)
    return () => window.clearTimeout(timer)
  }, [onToast, runtimeJob])

  async function changeCache() {
    if (!details || details.model.storage_backend !== 's3') return
    if (
      details.model.cached
      && !window.confirm(
        `Remove the local cache for ${details.model.repo_id}? The complete S3 copy will remain.`,
      )
    ) return
    setChangingCache(true)
    setError('')
    try {
      if (details.model.cached) {
        await api.evictLocalModelCache(details.model.repo_id)
        const remoteDetails = await api.localModelDetails(details.model.repo_id)
        setDetails(remoteDetails)
        onToast(`${details.model.repo_id} is now stored in S3 only.`)
      } else {
        const restored = await api.restoreLocalModel(details.model.repo_id)
        setDetails(restored)
        onToast(`${details.model.repo_id} was restored to the local cache.`)
      }
      onChanged()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Could not update the model cache.'
      setError(message)
      onToast(message, 'error')
    } finally {
      setChangingCache(false)
    }
  }

  async function dispatchRuntime() {
    if (!details || !runtimeTargetId || !runtimeModelName.trim()) return
    setDispatching(true)
    setError('')
    try {
      const job = await api.loadRuntime(runtimeTargetId, {
        repo_id: details.model.repo_id,
        runtime_model_name: runtimeModelName.trim(),
        ...(runtimeSourceFile ? { source_file: runtimeSourceFile } : {}),
      })
      setRuntimeJob(job)
      onToast(`${details.model.repo_id} was queued for ${job.target_name}.`)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Could not send the model to the runtime.'
      setError(message)
      onToast(message, 'error')
    } finally {
      setDispatching(false)
    }
  }

  if (!repoId) return null
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`Local details for ${repoId}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="drawer-header">
          <div>
            <span className="eyebrow">
              {details?.model.storage_backend === 's3' ? 'S3-backed model' : 'Local model'}
            </span>
            <h2>{repoId}</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        {!details && !error && (
          <div className="drawer-loading">
            <LoaderCircle size={24} className="spin" /> Scanning local files…
          </div>
        )}
        {error && <div className="inline-error">{error}</div>}
        {details && (
          <>
            <div className="local-detail-hero">
              {details.model.cached ? <HardDrive size={24} /> : <Cloud size={24} />}
              <div>
                <code>
                  {details.model.cached
                    ? `/models/${details.model.relative_path}`
                    : details.model.remote_uri}
                </code>
                <p>
                  {formatBytes(details.model.size_bytes)} across {details.model.file_count} files ·
                  updated {relativeTime(details.model.modified_at)}
                  {details.model.storage_backend === 's3'
                    ? details.model.cached ? ' · cached locally' : ' · S3 only'
                    : ''}
                </p>
              </div>
            </div>
            {details.model.storage_backend === 's3' && (
              <div className="local-storage-actions">
                <button
                  type="button"
                  className="secondary-button"
                  disabled={changingCache}
                  onClick={changeCache}
                >
                  {changingCache
                    ? <LoaderCircle size={16} className="spin" />
                    : details.model.cached ? <Trash2 size={16} /> : <CloudDownload size={16} />}
                  {changingCache
                    ? 'Working…'
                    : details.model.cached ? 'Remove local cache' : 'Restore to local cache'}
                </button>
                <p>
                  {details.model.cached
                    ? 'The durable S3 copy stays available.'
                    : 'Restore before loading this model in vLLM, llama.cpp, or another local runtime.'}
                </p>
              </div>
            )}
            {canManageRuntimes && runtimeTargets.length > 0 && (
              <section className="runtime-dispatch">
                <div className="runtime-dispatch-title">
                  <Server size={17} />
                  <div>
                    <strong>Send to runtime</strong>
                    <p>Transfer to Ollama or start this shared path through a vLLM agent.</p>
                  </div>
                </div>
                <div className="runtime-dispatch-form">
                  <label>
                    Destination
                    <select
                      value={runtimeTargetId}
                      onChange={(event) => {
                        setRuntimeTargetId(event.target.value)
                        setRuntimeSourceFile('')
                      }}
                    >
                      {runtimeTargets.map((target) => (
                        <option key={target.id} value={target.id}>
                          {target.name} · {target.kind}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Served model name
                    <input
                      value={runtimeModelName}
                      onChange={(event) => setRuntimeModelName(event.target.value)}
                      maxLength={128}
                    />
                  </label>
                  {runtimeTargets.find((target) => target.id === runtimeTargetId)?.kind === 'ollama'
                    && details.files.filter((file) => file.path.toLowerCase().endsWith('.gguf')).length > 1 && (
                      <label className="runtime-source-field">
                        GGUF quantization
                        <select
                          value={runtimeSourceFile}
                          onChange={(event) => setRuntimeSourceFile(event.target.value)}
                        >
                          <option value="">Choose a GGUF file</option>
                          {details.files
                            .filter((file) => file.path.toLowerCase().endsWith('.gguf'))
                            .map((file) => (
                              <option key={file.path} value={file.path}>
                                {file.path} · {formatBytes(file.size)}
                              </option>
                            ))}
                        </select>
                      </label>
                    )}
                  <button
                    type="button"
                    className="download-button"
                    onClick={dispatchRuntime}
                    disabled={
                      dispatching
                      || !details.model.cached
                      || !runtimeTargetId
                      || !runtimeModelName.trim()
                      || (
                        runtimeTargets.find((target) => target.id === runtimeTargetId)?.kind === 'ollama'
                        && details.files.filter((file) => file.path.toLowerCase().endsWith('.gguf')).length > 1
                        && !runtimeSourceFile
                      )
                      || Boolean(runtimeJob && activeRuntimeStatuses.includes(runtimeJob.status))
                    }
                  >
                    {dispatching || (runtimeJob && activeRuntimeStatuses.includes(runtimeJob.status))
                      ? <LoaderCircle size={16} className="spin" />
                      : <Rocket size={16} />}
                    {runtimeJob && activeRuntimeStatuses.includes(runtimeJob.status)
                      ? runtimeJob.message
                      : dispatching ? 'Queuing…' : 'Load model'}
                  </button>
                </div>
                {runtimeJob && (
                  <div className={`runtime-job-inline ${runtimeJob.status}`}>
                    <div>
                      <span>{runtimeJob.message}</span>
                      <strong>{runtimeJob.progress.toFixed(0)}%</strong>
                    </div>
                    <div className="job-progress">
                      <span style={{ width: `${runtimeJob.progress}%` }} />
                    </div>
                    {runtimeJob.error && <p>{runtimeJob.error}</p>}
                  </div>
                )}
              </section>
            )}
            {details.unsafe_file_count > 0 ? (
              <div className="security-note warning">
                <AlertTriangle size={16} />
                {details.unsafe_file_count} file{details.unsafe_file_count === 1 ? '' : 's'} may use
                pickle serialization. Do not load untrusted artifacts with code execution enabled.
              </div>
            ) : (
              <div className="security-note">
                <ShieldCheck size={16} />
                No common pickle-compatible file extensions found in the indexed file set.
              </div>
            )}
            <div className="file-list local-files">
              {details.files.map((file) => (
                <div key={file.path} className="file-row">
                  {file.unsafe_serialization ? <AlertTriangle size={15} /> : <File size={15} />}
                  <span title={file.path}>{file.path}</span>
                  <small>{formatBytes(file.size)}</small>
                </div>
              ))}
            </div>
          </>
        )}
      </aside>
    </div>
  )
}
