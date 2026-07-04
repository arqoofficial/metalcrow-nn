import { type ChatMessageMetadata, OpenAPI } from "@/client"

// backend/app/api/routes/chat.py:66-85 отдаёт StreamingResponse с одним
// SSE-event `data: {...}\n\n`; сгенерированный клиент типизирует ответ как
// `unknown` и не парсит SSE, поэтому здесь ручной fetch + разбор одного event.
// Формы claim/response продублированы из backend/app/schemas/chat.py, так как
// роут не объявляет `response_model` и типы не попадают в openapi-ts клиент.
export type ChatClaimConfidence = "high" | "medium" | "low"
export type ChatClaimKind = "fact" | "hypothesis"
export type ChatClaimRisk = "low" | "medium" | "high"

export type ChatClaimGapCell = {
  material?: string | null
  property?: string | null
  regime_bucket?: string | null
}

// GraphRAG source article (backend/app/schemas/chat.py::ChatSource) —
// rendered as a clickable chip in chat that deep-links to the wiki document
// view (/wiki?doc=<okf_path>), which shows the markdown + inline PDF.
export type ChatSource = {
  doc_id: string
  filename?: string | null
  source_path?: string | null
  okf_path?: string | null
}

export type ChatClaim = {
  text: string
  experiment_ids: string[]
  confidence: ChatClaimConfidence
  kind: ChatClaimKind
  gap_cell?: ChatClaimGapCell | null
  novelty?: number | null
  risk?: ChatClaimRisk | null
  value?: number | null
  score_rationale?: string | null
  sources: ChatSource[]
}

export type ChatModeUsed = "ontology" | "knowledge_graph" | "hypothesis"

export type ChatMessageResponse = {
  claims: ChatClaim[]
  summary: string
  tools_used: string[]
  subgraph?: unknown | null
  session_id: string
  mode_used: ChatModeUsed
}

export async function postChatMessage(
  sessionId: string,
  content: string,
  metadata?: ChatMessageMetadata | null,
): Promise<ChatMessageResponse> {
  const token = localStorage.getItem("access_token")

  const response = await fetch(
    `${OpenAPI.BASE}/api/v1/chat/sessions/${sessionId}/messages`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ content, metadata: metadata ?? null }),
    },
  )

  if (!response.ok) {
    throw new Error(`Chat request failed with status ${response.status}`)
  }

  const text = await response.text()
  const payload = text.replace(/^data:\s*/, "").trim()
  return JSON.parse(payload) as ChatMessageResponse
}
