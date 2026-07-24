import { useEffect, useMemo, useState } from 'react'
import { Boxes, Database, FileSearch, LoaderCircle, Search } from 'lucide-react'
import { inspectGguf, type GgufInspection } from '../gguf'
import type { HubFile } from '../types'
import { formatBytes, formatNumber } from '../utils'

interface GgufInspectorProps {
  repoId: string
  revision: string
  files: HubFile[]
}

const TENSOR_PAGE_SIZE = 200

export function GgufInspector({ repoId, revision, files }: GgufInspectorProps) {
  const [selectedPath, setSelectedPath] = useState(files[0]?.path || '')
  const [inspection, setInspection] = useState<GgufInspection | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [visibleCount, setVisibleCount] = useState(TENSOR_PAGE_SIZE)

  const selectedFile = useMemo(
    () => files.find((file) => file.path === selectedPath) || files[0],
    [files, selectedPath],
  )

  useEffect(() => {
    if (!selectedFile) return
    let ignore = false
    setInspection(null)
    setError('')
    setLoading(true)
    setQuery('')
    setVisibleCount(TENSOR_PAGE_SIZE)
    inspectGguf(repoId, revision, selectedFile)
      .then((result) => {
        if (!ignore) setInspection(result)
      })
      .catch((reason) => {
        if (!ignore) {
          setError(reason instanceof Error ? reason.message : 'Unable to inspect this GGUF file.')
        }
      })
      .finally(() => {
        if (!ignore) setLoading(false)
      })
    return () => {
      ignore = true
    }
  }, [repoId, revision, selectedFile])

  const filteredTensors = useMemo(() => {
    if (!inspection) return []
    const normalized = query.trim().toLowerCase()
    if (!normalized) return inspection.tensors
    return inspection.tensors.filter(
      (tensor) =>
        tensor.name.toLowerCase().includes(normalized) ||
        tensor.dtype.toLowerCase().includes(normalized) ||
        tensor.shape.join(' × ').includes(normalized),
    )
  }, [inspection, query])

  if (!selectedFile) {
    return <div className="empty-compact">This repository does not contain a GGUF file.</div>
  }

  return (
    <section className="gguf-inspector" aria-label="GGUF metadata and tensors">
      <div className="gguf-picker">
        <div>
          <span className="eyebrow">Header-only inspection</span>
          <h3>GGUF metadata &amp; tensors</h3>
          <p>Loaded on demand through bounded range requests. Model weights are not downloaded.</p>
        </div>
        <label>
          GGUF file or shard
          <select value={selectedFile.path} onChange={(event) => setSelectedPath(event.target.value)}>
            {files.map((file) => (
              <option value={file.path} key={file.path}>
                {file.path} · {formatBytes(file.size)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && (
        <div className="gguf-loading">
          <LoaderCircle size={20} className="spin" />
          <div>
            <strong>Reading the GGUF header…</strong>
            <span>Large tokenizer metadata may require several small range requests.</span>
          </div>
        </div>
      )}
      {error && (
        <div className="inline-error gguf-error">
          <strong>Couldn’t inspect this file.</strong>
          <span>{error}</span>
        </div>
      )}

      {inspection && (
        <>
          <div className="gguf-summary">
            <div>
              <Boxes size={17} />
              <span>
                <small>Tensors</small>
                <strong>{formatNumber(inspection.tensors.length)}</strong>
              </span>
            </div>
            <div>
              <Database size={17} />
              <span>
                <small>Parameters</small>
                <strong>{formatNumber(inspection.parameterCount)}</strong>
              </span>
            </div>
            <div>
              <FileSearch size={17} />
              <span>
                <small>Header read</small>
                <strong>{formatBytes(inspection.bytesRead)}</strong>
              </span>
            </div>
          </div>

          <div className="gguf-context">
            <span>GGUF v{inspection.version}</span>
            {inspection.shard && (
              <span>
                Shard {inspection.shard.index} of {inspection.shard.total}
              </span>
            )}
            <span>
              {inspection.rangeRequests} range request{inspection.rangeRequests === 1 ? '' : 's'}
            </span>
            <span>{formatBytes(Number(inspection.tensorDataOffset))} header</span>
          </div>

          {inspection.shard && (
            <p className="gguf-shard-note">
              Only this shard’s header was read. Choose another shard to inspect it; parsed results
              are cached for the current app session.
            </p>
          )}

          <section className="gguf-section">
            <div className="gguf-section-heading">
              <div>
                <h3>Metadata</h3>
                <p>{inspection.metadata.length} key-value entries</p>
              </div>
            </div>
            <div className="gguf-metadata-list">
              {inspection.metadata.map((entry) => (
                <div className="gguf-metadata-row" key={entry.key}>
                  <code>{entry.key}</code>
                  <span>
                    <small>
                      {entry.type}
                      {entry.itemCount === undefined ? '' : ` · ${formatNumber(entry.itemCount)} items`}
                    </small>
                    <code>{entry.value}</code>
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="gguf-section">
            <div className="gguf-section-heading tensor-heading">
              <div>
                <h3>Tensors</h3>
                <p>
                  {formatNumber(filteredTensors.length)} of {formatNumber(inspection.tensors.length)}
                </p>
              </div>
              <label className="gguf-search">
                <Search size={14} />
                <input
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value)
                    setVisibleCount(TENSOR_PAGE_SIZE)
                  }}
                  placeholder="Filter name, shape, or type"
                  aria-label="Filter tensors"
                />
              </label>
            </div>
            <div className="gguf-type-summary" aria-label="Tensor data type counts">
              {inspection.typeCounts.map((item) => (
                <span key={item.dtype}>
                  <code>{item.dtype}</code> {formatNumber(item.count)}
                </span>
              ))}
            </div>
            <div className="gguf-tensor-table">
              <div className="gguf-tensor-row header" aria-hidden="true">
                <span>Name</span>
                <span>Shape</span>
                <span>Type</span>
              </div>
              {filteredTensors.slice(0, visibleCount).map((tensor) => (
                <div className="gguf-tensor-row" key={tensor.name}>
                  <code title={tensor.name}>{tensor.name}</code>
                  <code>{tensor.shape.join(' × ')}</code>
                  <strong>{tensor.dtype}</strong>
                </div>
              ))}
            </div>
            {filteredTensors.length === 0 && (
              <div className="empty-compact">No tensors match that filter.</div>
            )}
            {visibleCount < filteredTensors.length && (
              <button
                type="button"
                className="secondary-button compact gguf-load-more"
                onClick={() => setVisibleCount((current) => current + TENSOR_PAGE_SIZE)}
              >
                Show {formatNumber(Math.min(TENSOR_PAGE_SIZE, filteredTensors.length - visibleCount))}{' '}
                more
              </button>
            )}
          </section>
        </>
      )}
    </section>
  )
}
