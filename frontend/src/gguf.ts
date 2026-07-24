import type { MetadataValue } from '@huggingface/gguf'
import type { HubFile } from './types'

export interface GgufMetadataEntry {
  key: string
  type: string
  value: string
  itemCount?: number
}

export interface GgufTensorEntry {
  name: string
  shape: string[]
  dtype: string
}

export interface GgufInspection {
  file: HubFile
  version: number
  parameterCount: number
  tensorDataOffset: string
  metadata: GgufMetadataEntry[]
  tensors: GgufTensorEntry[]
  typeCounts: Array<{ dtype: string; count: number }>
  bytesRead: number
  rangeRequests: number
  shard?: {
    index: number
    total: number
  }
}

const inspectionCache = new Map<string, Promise<GgufInspection>>()

function scalarPreview(value: string | number | bigint | boolean): string {
  if (typeof value === 'string') {
    const normalized = value.replace(/\r\n?/g, '\n')
    return normalized.length > 500 ? `${normalized.slice(0, 500)}…` : normalized
  }
  return String(value)
}

export function metadataPreview(value: MetadataValue): {
  value: string
  itemCount?: number
} {
  if (!Array.isArray(value)) return { value: scalarPreview(value) }
  const preview = value.slice(0, 4).map((item) => {
    if (Array.isArray(item)) return `[${item.length} items]`
    const rendered = scalarPreview(item)
    return typeof item === 'string' ? JSON.stringify(rendered) : rendered
  })
  return {
    value: `[${preview.join(', ')}${value.length > preview.length ? ', …' : ''}]`,
    itemCount: value.length,
  }
}

function cacheKey(repoId: string, revision: string, file: HubFile): string {
  return [repoId, revision, file.path, file.blob_id || 'no-blob'].join('\u0000')
}

async function inspect(
  repoId: string,
  revision: string,
  file: HubFile,
): Promise<GgufInspection> {
  const {
    GGMLQuantizationType,
    GGUFValueType,
    gguf,
    parseGgufShardFilename,
  } = await import('@huggingface/gguf')
  const params = new URLSearchParams({
    repo_id: repoId,
    revision,
    filename: file.path,
  })
  const proxyUrl = `/api/hub/gguf-range?${params.toString()}`
  let bytesRead = 0
  let rangeRequests = 0

  const proxyFetch: typeof fetch = async (input, init) => {
    const headers = new Headers(init?.headers)
    const response = await fetch(input, {
      ...init,
      credentials: 'same-origin',
      headers,
    })
    if (!response.ok) {
      if (response.status === 401) {
        window.dispatchEvent(new Event('hugginghack:unauthorized'))
      }
      const payload = await response.clone().json().catch(() => ({}))
      const detail =
        payload && typeof payload === 'object' && 'detail' in payload
          ? String(payload.detail)
          : `GGUF header request failed with status ${response.status}`
      throw new Error(detail)
    }
    rangeRequests += 1
    bytesRead += Number(response.headers.get('Content-Length') || 0)
    return response
  }

  const parsed = await gguf(proxyUrl, {
    fetch: proxyFetch,
    typedMetadata: true,
    computeParametersCount: true,
  })
  const metadata = Object.entries(parsed.typedMetadata).map(([key, entry]) => {
    const typedEntry = entry as {
      value: MetadataValue
      type: number
      subType?: number
    }
    const preview = metadataPreview(typedEntry.value)
    const typeName = GGUFValueType[typedEntry.type] || String(typedEntry.type)
    const subtypeName =
      typedEntry.subType === undefined
        ? ''
        : `<${GGUFValueType[typedEntry.subType] || typedEntry.subType}>`
    return {
      key,
      type: `${typeName}${subtypeName}`,
      value: preview.value,
      itemCount: preview.itemCount,
    }
  })
  const tensors = parsed.tensorInfos.map((tensor) => ({
    name: tensor.name,
    shape: tensor.shape.map(String),
    dtype: GGMLQuantizationType[tensor.dtype] || String(tensor.dtype),
  }))
  const counts = new Map<string, number>()
  for (const tensor of tensors) {
    counts.set(tensor.dtype, (counts.get(tensor.dtype) || 0) + 1)
  }
  const shardInfo = parseGgufShardFilename(file.path)

  return {
    file,
    version: parsed.metadata.version,
    parameterCount: parsed.parameterCount,
    tensorDataOffset: String(parsed.tensorDataOffset),
    metadata,
    tensors,
    typeCounts: [...counts.entries()]
      .map(([dtype, count]) => ({ dtype, count }))
      .sort((left, right) => right.count - left.count || left.dtype.localeCompare(right.dtype)),
    bytesRead,
    rangeRequests,
    shard: shardInfo
      ? {
          index: Number(shardInfo.shard),
          total: Number(shardInfo.total),
        }
      : undefined,
  }
}

export function inspectGguf(
  repoId: string,
  revision: string,
  file: HubFile,
): Promise<GgufInspection> {
  const key = cacheKey(repoId, revision, file)
  const cached = inspectionCache.get(key)
  if (cached) return cached
  const pending = inspect(repoId, revision, file).catch((error) => {
    inspectionCache.delete(key)
    throw error
  })
  inspectionCache.set(key, pending)
  return pending
}
