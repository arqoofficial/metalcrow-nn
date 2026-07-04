import { OpenAPI } from "@/client/core/OpenAPI"
import { request } from "@/client/core/request"
import type { IngestStatus, ProcessingLevel } from "@/client/types.gen"

export type RawDataFileSummary = {
  path: string
  filename: string
  stage0_done: boolean
  stage1_done: boolean
  document_id?: string | null
  processing_level?: ProcessingLevel | null
  latest_task_status?: IngestStatus | null
}

export type RawDataFilesResponse = {
  data: RawDataFileSummary[]
  count: number
  offset: number
  limit: number
}

export function listUnparsedRawFiles(params: {
  search?: string
  limit?: number
  offset?: number
}) {
  return request<RawDataFilesResponse>(OpenAPI, {
    method: "GET",
    url: "/api/v1/admin/raw-files",
    query: params,
  })
}

export function parseRawDataFile(path: string) {
  return request<{ task_id: string }>(OpenAPI, {
    method: "POST",
    url: "/api/v1/admin/raw-files/parse",
    body: { path },
    mediaType: "application/json",
  })
}
