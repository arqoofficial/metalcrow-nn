import type { DocumentFileSummary, IngestUploadBatchResponse } from "@/client"
import { OpenAPI } from "@/client"

function apiUrl(path: string): string {
  const base = (OpenAPI.BASE ?? "").replace(/\/$/, "")
  return `${base}${path}`
}

async function parseUploadError(response: Response): Promise<Error> {
  if (response.status === 413) {
    return new Error("File exceeds the 50MB limit")
  }

  const body = await response.json().catch(() => ({}))
  const detail = body.detail
  if (typeof detail === "string") {
    return new Error(detail)
  }
  if (Array.isArray(detail) && detail[0]?.msg) {
    return new Error(String(detail[0].msg))
  }
  return new Error(`Upload failed (${response.status})`)
}

async function uploadSingleFile(
  file: File,
  token: string | null,
): Promise<DocumentFileSummary> {
  const formData = new FormData()
  formData.append("file", file)

  const response = await fetch(apiUrl("/api/v1/ingest/upload"), {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  })

  if (!response.ok) {
    throw await parseUploadError(response)
  }

  const result = (await response.json()) as IngestUploadBatchResponse
  const uploaded = result.data[0]
  if (!uploaded) {
    throw new Error(`Upload failed for ${file.name}`)
  }
  return uploaded
}

export type UploadProgress = {
  completed: number
  total: number
  currentFile: string
}

export async function uploadDocuments(
  files: File[],
  onProgress?: (progress: UploadProgress) => void,
): Promise<IngestUploadBatchResponse> {
  if (files.length === 0) {
    throw new Error("No files selected")
  }

  const token = localStorage.getItem("access_token")
  const uploaded: DocumentFileSummary[] = []

  for (const [index, file] of files.entries()) {
    onProgress?.({
      completed: index,
      total: files.length,
      currentFile: file.name,
    })
    uploaded.push(await uploadSingleFile(file, token))
  }

  onProgress?.({
    completed: files.length,
    total: files.length,
    currentFile: "",
  })

  return { data: uploaded, count: uploaded.length }
}
