import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { ArrowUp, Download, Maximize2, Plus, Share2 } from "lucide-react"
import { Fragment, useEffect, useMemo, useRef, useState } from "react"

import { type ChatMode, ChatService } from "@/client"
import { LiteraturePanel } from "@/components/Chat/LiteraturePanel"
import { MarkdownContent } from "@/components/Common/MarkdownContent"
import { PageHeader } from "@/components/Common/PageHeader"
import { StubMark } from "@/components/Common/StubMark"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  type ChatClaim,
  type ChatClaimConfidence,
  type ChatMessageResponse,
  type ChatModeUsed,
  type ChatSource,
  postChatMessage,
} from "@/lib/postChatMessage"
import { getSearch } from "@/lib/litsearch"
import { cn, countRu } from "@/lib/utils"

// ── режимы композера ──────────────────────────────────────────────────────
const modeOptions: { value: ChatMode; label: string; hint: string }[] = [
  {
    value: "auto",
    label: "Авто",
    hint: "Сам выбирает: сначала онтология, при пустом ответе — граф знаний",
  },
  {
    value: "ontology",
    label: "Онтология",
    hint: "Только типизированная онтология — дословные цитаты источников",
  },
  {
    value: "knowledge_graph",
    label: "Граф знаний",
    hint: "GraphRAG по графу знаний + поиск по экспериментам",
  },
  {
    value: "literature",
    label: "Литература",
    hint: "Поиск статей в открытых источниках: сначала ответ по аннотациям, затем — по полным текстам",
  },
]

const modeUsedLabel: Record<ChatModeUsed, string> = {
  ontology: "Онтология",
  knowledge_graph: "Граф знаний",
  hypothesis: "Гипотеза (gap-анализ)",
  literature: "Литература",
}

const modeUsedVariant: Record<
  ChatModeUsed,
  "confidenceHigh" | "neutral" | "external"
> = {
  ontology: "neutral",
  knowledge_graph: "confidenceHigh",
  hypothesis: "external",
  literature: "neutral",
}

const litsearchKindLabel: Record<string, string> = {
  abstracts: "Ответ по аннотациям",
  fulltext: "Ответ по полным текстам",
}

const confidenceMeta: Record<
  ChatClaimConfidence,
  {
    label: string
    dot: string
    badge: "confidenceHigh" | "confidenceMedium" | "neutral"
  }
> = {
  high: {
    label: "высокая",
    dot: "bg-confidence-high",
    badge: "confidenceHigh",
  },
  medium: {
    label: "средняя",
    dot: "bg-confidence-medium",
    badge: "confidenceMedium",
  },
  low: { label: "низкая", dot: "bg-muted-foreground", badge: "neutral" },
}

const suggestionChips = [
  "Какая плотность тока оптимальна при электроэкстракции Ni?",
  "Сравни отечественную и мировую практику по схемам подачи католита",
  "Как распределяются Au, Ag и МПГ между штейном и шлаком?",
  "Куда уходит серебро при конвертировании штейна?",
  "Из чего получают файнштейн — покажи цепочку переделов",
  "Какими способами подают концентрат в печи взвешенной плавки?",
  "Что ещё не изучено по флотации пентландита?",
  "Какие методы обессоливания шахтных вод применимы при 200–300 мг/л сульфатов?",
  "Сравни отечественную и зарубежную практику по выщелачиванию",
  "Какие комбинации режимов ещё не изучены?",
  "Где пробелы в базе: что покрыто слабо?",
  "Есть ли противоречия между источниками?",
  "Какие лаборатории занимались хлорированием?",
]

// Провенанс вшит в текст claim'а строкой «— источник: «Документ: цитата»».
function splitClaimSource(text: string): {
  body: string
  source: string | null
} {
  const marker = "— источник:"
  const idx = text.indexOf(marker)
  if (idx === -1) return { body: text.trim(), source: null }
  const body = text.slice(0, idx).replace(/\n+$/, "").trim()
  const source = text
    .slice(idx + marker.length)
    .trim()
    .replace(/^«|»$/g, "")
  return { body, source: source || null }
}

// GraphRAG source chips — deep-link to wiki document view (markdown + inline PDF).
function SourceChips({ sources }: { sources: ChatSource[] }) {
  if (!sources.length) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {sources.map((source) =>
        source.okf_path ? (
          <Link
            key={source.doc_id}
            to="/wiki"
            search={{ doc: source.okf_path }}
            className="rounded-md border bg-background px-2 py-0.5 text-[11.5px] text-foreground hover:border-primary hover:text-primary"
          >
            {source.filename ?? source.doc_id}
          </Link>
        ) : (
          <span
            key={source.doc_id}
            className="text-[11.5px] text-muted-foreground"
          >
            {source.filename ?? source.doc_id}
          </span>
        ),
      )}
    </div>
  )
}

function graphSourcesFromClaims(claims: ChatClaim[]): ChatSource[] {
  const seen = new Set<string>()
  const out: ChatSource[] = []
  for (const claim of claims) {
    for (const source of claim.sources ?? []) {
      if (seen.has(source.doc_id)) continue
      seen.add(source.doc_id)
      out.push(source)
    }
  }
  return out
}

function structuredResponseFromMetadata(
  metadata: Record<string, unknown> | null | undefined,
  sessionId: string,
  fallbackContent: string,
): ChatMessageResponse | null {
  const claims = metadata?.claims
  if (!Array.isArray(claims) || claims.length === 0) return null
  return {
    claims: claims as ChatClaim[],
    summary: (metadata?.summary as string) ?? fallbackContent,
    tools_used: (metadata?.tools_used as string[]) ?? [],
    subgraph: metadata?.subgraph ?? null,
    session_id: sessionId,
    mode_used: (metadata?.mode_used as ChatModeUsed) ?? "ontology",
  }
}

function formatMessageSentAt(value: string | null | undefined): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const yyyy = date.getFullYear()
  const mm = String(date.getMonth() + 1).padStart(2, "0")
  const dd = String(date.getDate()).padStart(2, "0")
  const hh = String(date.getHours()).padStart(2, "0")
  const min = String(date.getMinutes()).padStart(2, "0")
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`
}

function ModeUsedBadge({ mode }: { mode: string }) {
  const known = mode in modeUsedLabel ? (mode as ChatModeUsed) : null
  return (
    <Badge variant={known ? modeUsedVariant[known] : "neutral"}>
      {known ? modeUsedLabel[known] : mode}
    </Badge>
  )
}

export const Route = createFileRoute("/_layout/chat")({
  component: ChatPage,
  validateSearch: (
    search: Record<string, unknown>,
  ): { session?: string; draft?: string } => ({
    session: typeof search.session === "string" ? search.session : undefined,
    draft: typeof search.draft === "string" ? search.draft : undefined,
  }),
  head: () => ({ meta: [{ title: "Агент — MetalCrow" }] }),
})

// ── пузырь вопроса пользователя ────────────────────────────────────────────
function QuestionBubble({
  text,
  time,
}: {
  text: string
  time: string | null
}) {
  return (
    <div className="flex w-full max-w-[840px] flex-none flex-col items-end gap-1">
      <div className="max-w-[620px] rounded-[14px_14px_4px_14px] bg-primary px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap text-primary-foreground">
        {text}
      </div>
      {time && (
        <span className="pr-1 text-[11.5px] text-muted-foreground">{time}</span>
      )}
    </div>
  )
}

// ── одно утверждение (accordion через <details>) ───────────────────────────
function ClaimItem({
  claim,
  defaultOpen,
}: {
  claim: ChatClaim
  defaultOpen: boolean
}) {
  const meta = confidenceMeta[claim.confidence] ?? confidenceMeta.low
  const { body, source } = splitClaimSource(claim.text)
  return (
    <details
      open={defaultOpen}
      className="group overflow-hidden rounded-[10px] border bg-card"
    >
      <summary className="flex cursor-pointer list-none items-center gap-2.5 px-3.5 py-3 hover:bg-muted/50">
        <span className={cn("size-2 shrink-0 rounded-full", meta.dot)} />
        <span className="text-[13.5px] leading-snug text-foreground">
          {body}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {claim.kind === "hypothesis" && (
            <Badge variant="external" className="text-[10px]">
              гипотеза
            </Badge>
          )}
          <Badge variant={meta.badge} className="text-[11px]">
            {meta.label}
          </Badge>
        </span>
      </summary>
      <div className="flex flex-col gap-2.5 border-t bg-muted/30 px-3.5 py-3">
        {source ? (
          <div className="flex gap-2.5">
            <span className={cn("w-[3px] shrink-0 rounded-full", meta.dot)} />
            <p className="text-[12.5px] leading-relaxed text-muted-foreground">
              {source}
            </p>
          </div>
        ) : (
          <p className="text-[12.5px] text-muted-foreground">
            Источник в ответе не выделен отдельной строкой.
          </p>
        )}
        {claim.score_rationale && (
          <p className="text-[12px] leading-relaxed text-muted-foreground italic">
            {claim.score_rationale}
          </p>
        )}
        {claim.experiment_ids.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 text-[11.5px] text-muted-foreground">
            <span>Эксперименты:</span>
            {claim.experiment_ids.map((id) => (
              <span
                key={id}
                className="rounded bg-muted px-2 py-0.5 font-mono text-[11px] text-foreground"
              >
                {id}
              </span>
            ))}
            <Link
              to="/graph"
              className="ml-auto font-medium text-primary hover:underline"
            >
              Показать в графе →
            </Link>
          </div>
        )}
        {claim.sources.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <span className="text-[11.5px] text-muted-foreground">
              Документы:
            </span>
            <SourceChips sources={claim.sources} />
          </div>
        )}
      </div>
    </details>
  )
}

// ── карточка структурированного ответа агента ──────────────────────────────
function ReportCard({
  data,
  time,
}: {
  data: ChatMessageResponse
  time: string | null
}) {
  const claims = data.claims ?? []
  const expCount = useMemo(
    () => new Set(claims.flatMap((c) => c.experiment_ids)).size,
    [claims],
  )
  const graphSources = useMemo(
    () => graphSourcesFromClaims(claims),
    [claims],
  )
  const textSources = useMemo(() => {
    const seen = new Set<string>()
    for (const c of claims) {
      const { source } = splitClaimSource(c.text)
      if (source) seen.add(source)
    }
    return [...seen]
  }, [claims])
  const gapClaim = claims.find((c) => c.gap_cell)
  const gap = gapClaim?.gap_cell

  return (
    <div className="w-full max-w-[840px] flex-none overflow-hidden rounded-[14px] border bg-card">
      {/* шапка */}
      <div className="flex flex-wrap items-center gap-2.5 border-b px-5 py-3.5">
        <ModeUsedBadge mode={data.mode_used} />
        <span className="text-xs text-muted-foreground">
          {countRu(claims.length, [
            "утверждение",
            "утверждения",
            "утверждений",
          ])}
          {" · "}
          {countRu(expCount, ["эксперимент", "эксперимента", "экспериментов"])}
        </span>
        {time && (
          <span className="ml-auto text-[11.5px] text-muted-foreground">
            {time}
          </span>
        )}
      </div>

      {/* сводка */}
      {data.summary && (
        <div className="px-5 pt-4 pb-1.5">
          <div className="pb-2 text-[11px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
            Сводка
          </div>
          <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground">
            {data.summary}
          </p>
        </div>
      )}

      {/* утверждения */}
      {claims.length > 0 && (
        <div className="px-5 pt-4 pb-2">
          <div className="flex items-baseline gap-2 pb-2">
            <span className="text-[11px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
              Утверждения
            </span>
            <span className="text-[11.5px] text-muted-foreground/70">
              нажмите, чтобы раскрыть доказательства
            </span>
          </div>
          <div className="flex flex-col gap-2">
            {claims.map((claim, index) => (
              <ClaimItem key={index} claim={claim} defaultOpen={index === 0} />
            ))}
          </div>
        </div>
      )}

      {/* источники + эксперты */}
      <div className="grid gap-4 px-5 pt-4 pb-5 sm:grid-cols-2">
        <div>
          <div className="pb-2 text-[11px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
            Источники
          </div>
          {graphSources.length > 0 ? (
            <SourceChips sources={graphSources} />
          ) : textSources.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              {textSources.slice(0, 5).map((s, i) => (
                <Link
                  key={i}
                  to="/search"
                  className="line-clamp-2 text-[12.5px] text-foreground hover:text-primary"
                >
                  {s}
                </Link>
              ))}
              <Link
                to="/search"
                className="text-[12.5px] font-medium text-primary hover:underline"
              >
                Искать в базе →
              </Link>
            </div>
          ) : (
            <p className="text-[12.5px] text-muted-foreground">
              Структурированный список источников <StubMark /> появится после
              миграции провенанса.
            </p>
          )}
        </div>
        <div>
          <div className="pb-2 text-[11px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
            Эксперты по теме
          </div>
          <p className="text-[12.5px] text-muted-foreground">
            Привязка экспертов к теме <StubMark /> — ещё не реализована в
            бэкенде.
          </p>
          {gap && (
            <Link
              to="/gaps"
              className="mt-3 block rounded-[9px] border border-confidence-medium/40 bg-confidence-medium-bg px-3 py-2.5 text-[12px] leading-relaxed text-confidence-medium-fg hover:brightness-[0.98]"
            >
              <b>Пробел:</b> нет данных для комбинации «
              {[gap.material, gap.property, gap.regime_bucket]
                .filter(Boolean)
                .join(" × ")}
              » → карта пробелов →
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}

// ── обычная (историческая) реплика ассистента без структуры ────────────────
function AssistantPlainCard({
  content,
  mode,
  litsearchKind,
  time,
}: {
  content: string
  mode?: string
  litsearchKind?: string
  time: string | null
}) {
  const litsearchKindText = litsearchKind
    ? litsearchKindLabel[litsearchKind]
    : undefined
  return (
    <div className="w-full max-w-[840px] flex-none overflow-hidden rounded-[14px] border bg-card">
      <div className="flex flex-wrap items-center gap-2.5 border-b px-5 py-3">
        {mode ? (
          <ModeUsedBadge mode={mode} />
        ) : (
          <Badge variant="neutral">Ответ</Badge>
        )}
        {litsearchKindText && (
          <Badge variant="outline" className="text-[11px]">
            {litsearchKindText}
          </Badge>
        )}
        {time && (
          <span className="ml-auto text-[11.5px] text-muted-foreground">
            {time}
          </span>
        )}
      </div>
      <div className="px-5 py-4">
        {litsearchKind || mode === "literature" ? (
          <MarkdownContent content={content} />
        ) : (
          <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground">
            {content}
          </p>
        )}
      </div>
    </div>
  )
}

// Стадии «мышления» по режиму. Бэкенд отдаёт ответ одним событием (без
// стриминга шагов), поэтому прогресс сменяется по таймеру и отражает реальные
// этапы пайплайна: разбор вопроса → ретрив → отбор доказательств → синтез.
const thinkingStages: Record<ChatMode, string[]> = {
  ontology: [
    "Разбираю вопрос, определяю интент…",
    "Ищу пассажи в онтологии: плотный + лексический поиск…",
    "Отбираю доказательства и дословные цитаты источников…",
    "Синтезирую ответ и оцениваю достоверность…",
  ],
  knowledge_graph: [
    "Разбираю вопрос…",
    "Обхожу граф знаний: сущности → связи…",
    "Достаю релевантные эксперименты (hybrid search)…",
    "Синтезирую ответ по графовому контексту…",
  ],
  auto: [
    "Разбираю вопрос, определяю интент…",
    "Ищу в онтологии: плотный + лексический поиск…",
    "Отбираю доказательства с цитатами; при пустом — граф знаний…",
    "Синтезирую структурированный ответ…",
  ],
  literature: [
    "Формулирую поисковые запросы…",
    "Ищу статьи в OpenAlex и Cyberleninka…",
    "Синтезирую ответ по аннотациям…",
    "Загружаю полные тексты для подробного ответа…",
  ],
}

function ThinkingIndicator({ mode }: { mode: ChatMode }) {
  const stages = thinkingStages[mode] ?? thinkingStages.auto
  const [stage, setStage] = useState(0)
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    setStage(0)
    setElapsed(0)
    const started = Date.now()
    const timer = setInterval(() => {
      const secs = Math.floor((Date.now() - started) / 1000)
      setElapsed(secs)
      // ~2.4 с на стадию, на последней задерживаемся до прихода ответа
      setStage(Math.min(stages.length - 1, Math.floor(secs / 2.4)))
    }, 300)
    return () => clearInterval(timer)
  }, [stages.length])

  return (
    <div className="flex w-full max-w-[840px] flex-none items-center gap-2.5 px-1 py-1.5">
      <span className="flex shrink-0 items-center gap-1">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="mc-pulse size-1.5 rounded-full bg-primary"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </span>
      <span className="text-[12.5px] text-muted-foreground transition-opacity">
        {stages[stage]}
      </span>
      <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground/60">
        {elapsed}s
      </span>
    </div>
  )
}

// ── страница ────────────────────────────────────────────────────────────────
function ChatPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const search = Route.useSearch()
  const selectedSessionId = search.session ?? null

  const [mode, setMode] = useState<ChatMode>("auto")
  const [messageContent, setMessageContent] = useState(search.draft ?? "")
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null)
  const [pendingSentAt, setPendingSentAt] = useState<string | null>(null)
  const [newTitle, setNewTitle] = useState("")
  const [baseHistoryLen, setBaseHistoryLen] = useState(0)
  const [activeSearchId, setActiveSearchId] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data: sessions } = useQuery({
    queryKey: ["chat-sessions"],
    queryFn: () => ChatService.listSessions(),
  })

  const { data: history } = useQuery({
    queryKey: ["chat-history", selectedSessionId],
    queryFn: () =>
      ChatService.getSessionHistory({ sessionId: selectedSessionId ?? "" }),
    enabled: !!selectedSessionId,
  })

  const latestHistorySearchId = useMemo(() => {
    if (!history) return null
    for (let index = history.length - 1; index >= 0; index -= 1) {
      const metadata = history[index].message_metadata as
        | Record<string, unknown>
        | null
        | undefined
      const searchId = metadata?.search_id
      if (typeof searchId === "string") {
        return searchId
      }
    }
    return null
  }, [history])

  const effectiveSearchId = activeSearchId ?? latestHistorySearchId

  const { data: literatureSearch } = useQuery({
    queryKey: ["litsearch", effectiveSearchId],
    queryFn: () => getSearch(effectiveSearchId as string),
    enabled: !!effectiveSearchId,
    refetchInterval: (query) => {
      const stage = query.state.data?.stage
      return stage === "done" || stage === "failed" ? false : 2000
    },
  })

  const previousAnswerCountRef = useRef(0)

  useEffect(() => {
    const answerCount = literatureSearch?.answers.length ?? 0
    if (answerCount > previousAnswerCountRef.current) {
      queryClient.invalidateQueries({
        queryKey: ["chat-history", selectedSessionId],
      })
    }
    previousAnswerCountRef.current = answerCount
  }, [literatureSearch?.answers.length, queryClient, selectedSessionId])

  useEffect(() => {
    if (literatureSearch?.followup_search_id) {
      setActiveSearchId(literatureSearch.followup_search_id)
    }
  }, [literatureSearch?.followup_search_id])

  const createSession = useMutation({
    mutationFn: (title?: string) =>
      ChatService.createSession({
        requestBody: { title: title?.trim() || null },
      }),
    onSuccess: (created) => {
      setNewTitle("")
      setActiveSearchId(null)
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] })
      navigate({ to: "/chat", search: { session: created.id } })
    },
  })

  const sendMessage = useMutation({
    mutationFn: (content: string) =>
      postChatMessage(selectedSessionId as string, content, { mode }),
    onMutate: (content) => {
      setPendingQuestion(content)
      setPendingSentAt(new Date().toISOString())
      setMessageContent("")
      setBaseHistoryLen(history?.length ?? 0)
    },
    onSuccess: async (response) => {
      if (response.literature?.search_id) {
        setActiveSearchId(response.literature.search_id)
      }
      try {
        await queryClient.invalidateQueries({
          queryKey: ["chat-history", selectedSessionId],
        })
      } finally {
        queryClient.invalidateQueries({ queryKey: ["chat-sessions"] })
        setPendingQuestion(null)
        setPendingSentAt(null)
      }
    },
    onError: () => {
      setPendingQuestion(null)
      setPendingSentAt(null)
    },
  })

  // Сброс состояния и префилл черновика при смене сессии (или draft в URL).
  // biome-ignore lint/correctness/useExhaustiveDependencies: намеренно перезапускаем при смене сессии
  useEffect(() => {
    setPendingQuestion(null)
    setPendingSentAt(null)
    setBaseHistoryLen(0)
    setActiveSearchId(null)
    setMessageContent(search.draft ?? "")
  }, [selectedSessionId, search.draft])

  // Автоскролл вниз при изменении ленты сообщений.
  // biome-ignore lint/correctness/useExhaustiveDependencies: скролл зависит от объёма ленты
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [history, pendingQuestion, sendMessage.data])

  const sessionTitle =
    sessions?.data.find((s) => s.id === selectedSessionId)?.title ||
    "Без названия"

  const handleSend = () => {
    const content = messageContent.trim()
    if (!selectedSessionId || !content || sendMessage.isPending) return
    sendMessage.mutate(content)
  }

  const lastAssistantIndex = useMemo(() => {
    if (!history) return -1
    for (let i = history.length - 1; i >= 0; i--) {
      if (history[i].role === "assistant") return i
    }
    return -1
  }, [history])

  // Показываем структурированный ответ, только когда рефетч истории уже
  // подхватил новый ход (длина выросла) — иначе он на мгновение привязался бы
  // к предыдущей реплике ассистента.
  const liveResponse =
    sendMessage.data &&
    sendMessage.data.session_id === selectedSessionId &&
    (history?.length ?? 0) > baseHistoryLen
      ? sendMessage.data
      : null

  const isEmpty = !history || history.length === 0
  const showChips =
    !!selectedSessionId && isEmpty && !pendingQuestion && !sendMessage.isPending

  // ── нет активной сессии ──
  if (!selectedSessionId) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <PageHeader title="Агент" />
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
          <div className="max-w-md">
            <h2 className="text-lg font-semibold">Задайте вопрос агенту</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Каждый ответ — структурированный мини-отчёт: утверждения с
              достоверностью, цитаты источников и связанные эксперименты.
              Выберите сессию слева или создайте новую.
            </p>
          </div>
          <div className="flex w-full max-w-sm items-center gap-2">
            <input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") createSession.mutate(newTitle)
              }}
              placeholder="Название сессии (необязательно)"
              className="h-9 flex-1 rounded-lg border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus:border-ring"
            />
            <Button
              onClick={() => createSession.mutate(newTitle)}
              disabled={createSession.isPending}
            >
              <Plus className="size-4" />
              Создать
            </Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={
          <span className="flex items-baseline gap-2">
            <span className="truncate">{sessionTitle}</span>
          </span>
        }
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link to="/reports">
                <Maximize2 className="size-3.5" />
                Развернуть в обзор
                <StubMark className="ml-0.5" />
              </Link>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => window.print()}
              title="Печать / сохранение ответа в PDF браузером"
            >
              <Download className="size-3.5" />
              Экспорт PDF
            </Button>
            <Button variant="outline" size="sm" asChild>
              <Link to="/graph">
                <Share2 className="size-3.5" />
                <span className="hidden sm:inline">Открыть в графе</span>
              </Link>
            </Button>
          </>
        }
      />

      <div
        className={cn(
          "grid min-h-0 flex-1",
          effectiveSearchId && "lg:grid-cols-[minmax(0,1fr)_360px]",
        )}
      >
        <div
          ref={scrollRef}
          className="flex min-h-0 flex-col items-center gap-5 overflow-y-auto bg-muted/20 px-4 py-6"
        >
        {isEmpty && !pendingQuestion && (
          <div className="flex w-full max-w-[840px] flex-none flex-col items-center gap-1 pt-6 text-center text-muted-foreground">
            <p className="text-sm">
              Новая сессия. Задайте вопрос: материал + процесс + условия +
              география.
            </p>
          </div>
        )}

        {history?.map((message, index) => {
          const time = formatMessageSentAt(message.created_at)
          const metadata = message.message_metadata as
            | Record<string, unknown>
            | null
            | undefined

          if (message.role === "user") {
            return (
              <QuestionBubble
                key={message.id ?? index}
                text={message.content}
                time={time}
              />
            )
          }

          // последняя реплика ассистента + есть живой структурированный ответ
          if (index === lastAssistantIndex && liveResponse) {
            return (
              <ReportCard
                key={message.id ?? index}
                data={liveResponse}
                time={time}
              />
            )
          }

          const structured = structuredResponseFromMetadata(
            metadata,
            selectedSessionId,
            message.content,
          )
          if (structured) {
            return (
              <ReportCard
                key={message.id ?? index}
                data={structured}
                time={time}
              />
            )
          }

          return (
            <AssistantPlainCard
              key={message.id ?? index}
              content={message.content}
              mode={metadata?.mode_used as string | undefined}
              litsearchKind={metadata?.litsearch_kind as string | undefined}
              time={time}
            />
          )
        })}

        {pendingQuestion && (history?.length ?? 0) <= baseHistoryLen && (
          <Fragment>
            <QuestionBubble
              text={pendingQuestion}
              time={formatMessageSentAt(pendingSentAt)}
            />
            <ThinkingIndicator mode={mode} />
          </Fragment>
        )}

        {showChips && (
          <div className="flex w-full max-w-[840px] flex-none flex-wrap justify-center gap-2.5 pt-2">
            {suggestionChips.map((chip) => (
              <button
                key={chip}
                type="button"
                onClick={() => setMessageContent(chip)}
                className="rounded-full border px-3 py-1.5 text-[12px] text-foreground transition-colors hover:border-primary hover:bg-primary/5"
              >
                {chip}
              </button>
            ))}
          </div>
        )}
        </div>

        {effectiveSearchId && (
          <div className="hidden min-h-0 overflow-y-auto border-l bg-card lg:block">
            <LiteraturePanel searchId={effectiveSearchId} />
          </div>
        )}
      </div>

      {/* композер */}
      <div className="flex shrink-0 justify-center border-t bg-card px-4 py-3.5">
        <div className="flex w-full max-w-[840px] flex-col gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {modeOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setMode(option.value)}
                className={cn(
                  "rounded-lg px-3 py-1 text-xs transition-colors",
                  mode === option.value
                    ? "bg-foreground font-semibold text-background"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {option.label}
              </button>
            ))}
            <span className="pl-1 text-[11.5px] text-muted-foreground">
              {modeOptions.find((o) => o.value === mode)?.hint}
            </span>
          </div>
          <div className="flex items-center gap-2.5 rounded-xl border bg-background py-2 pr-2 pl-3.5 shadow-sm focus-within:border-ring">
            <input
              value={messageContent}
              onChange={(e) => setMessageContent(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  handleSend()
                }
              }}
              placeholder="Задайте вопрос: материал + процесс + условия + география…"
              className="flex-1 border-none bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={sendMessage.isPending || !messageContent.trim()}
              aria-label="Отправить"
              className="grid size-9 shrink-0 place-items-center rounded-[9px] bg-primary text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
            >
              <ArrowUp className="size-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
