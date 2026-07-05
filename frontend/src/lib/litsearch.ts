import { OpenAPI } from "@/client"

// backend/app/api/routes/litsearch.py + backend/app/schemas/litsearch.py:
// these three endpoints are new (Task 12) and not covered by the generated
// OpenAPI client, so — mirroring postChatMessage.ts — we hand-roll an auth'd
// fetch and hand-declare the response shapes here.

export type LitPaperFetchStatus =
  | "pending"
  | "downloading"
  | "done"
  | "failed"
  | "skipped"

export type LitPaperFulltextStatus = "none" | "added" | "failed"

export type LitPaperIngestStatus =
  | "none"
  | "queued"
  | "running"
  | "done"
  | "failed"

export type LiteratureSearchStage =
  | "searching"
  | "fetching"
  | "reading"
  | "done"
  | "failed"

export type LitAnswerKind = "abstracts" | "fulltext"

export interface LitAnswerRef {
  message_id: string
  kind: LitAnswerKind
}

export interface LiteraturePaperPublic {
  id: string
  doi: string | null
  title: string
  authors: string
  year: number | null
  abstract: string
  pdf_url: string | null
  citation_count: number | null
  fetch_status: LitPaperFetchStatus
  fulltext_status: LitPaperFulltextStatus
  fulltext_chars: number
  ingest_status: LitPaperIngestStatus
  document_id: string | null
}

export interface LiteratureSearchPublic {
  id: string
  stage: LiteratureSearchStage
  round: number
  followup_search_id: string | null
  papers: LiteraturePaperPublic[]
  answers: LitAnswerRef[]
  queries: string[]
}

export interface PaperIngestStatusPublic {
  status: string
  progress: number
  stage_name: string | null
  error: string | null
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("access_token")
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function getSearch(
  searchId: string,
): Promise<LiteratureSearchPublic> {
  const response = await fetch(`${OpenAPI.BASE}/api/v1/litsearch/${searchId}`, {
    method: "GET",
    headers: { ...authHeaders() },
  })

  if (!response.ok) {
    throw new Error(`Litsearch request failed with status ${response.status}`)
  }

  return (await response.json()) as LiteratureSearchPublic
}

export async function addToDatabase(
  paperId: string,
): Promise<LiteraturePaperPublic> {
  const response = await fetch(
    `${OpenAPI.BASE}/api/v1/litsearch/papers/${paperId}/add-to-database`,
    {
      method: "POST",
      headers: { ...authHeaders() },
    },
  )

  if (!response.ok) {
    throw new Error(
      `Add-to-database request failed with status ${response.status}`,
    )
  }

  return (await response.json()) as LiteraturePaperPublic
}

export async function getIngestStatus(
  paperId: string,
): Promise<PaperIngestStatusPublic> {
  const response = await fetch(
    `${OpenAPI.BASE}/api/v1/litsearch/papers/${paperId}/ingest-status`,
    {
      method: "GET",
      headers: { ...authHeaders() },
    },
  )

  if (!response.ok) {
    throw new Error(
      `Ingest-status request failed with status ${response.status}`,
    )
  }

  return (await response.json()) as PaperIngestStatusPublic
}
