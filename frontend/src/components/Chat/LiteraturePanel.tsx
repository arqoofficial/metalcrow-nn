import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  ExternalLink,
  Loader2,
  Quote,
  XCircle,
} from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { LoadingButton } from "@/components/ui/loading-button"
import { Skeleton } from "@/components/ui/skeleton"
import useCustomToast from "@/hooks/useCustomToast"
import {
  addToDatabase,
  getIngestStatus,
  getSearch,
  type LiteraturePaperPublic,
  type LiteratureSearchStage,
} from "@/lib/litsearch"

const stageLabel: Record<LiteratureSearchStage, string> = {
  searching: "Поиск статей…",
  fetching: "Загрузка PDF…",
  reading: "Чтение полного текста…",
  done: "Поиск завершён",
  failed: "Поиск завершился с ошибкой",
}

const ingestPollingStatuses = new Set(["queued", "running"])

const ingestLabel: Record<string, string> = {
  none: "поставлено в очередь",
  queued: "в очереди на обработку",
  running: "обрабатывается",
  done: "добавлено в базу знаний",
  failed: "ошибка обработки",
}

// The server (see `_coarse_ingest_status` in `litsearch.py`) only ever emits
// none/queued/running/done/failed, but we harden against any unexpected
// value here too so a raw/unknown string never leaks into the UI — it falls
// back to the generic "in progress" label and badge instead.
function ingestLabelFor(status: string): string {
  return ingestLabel[status] ?? ingestLabel.running
}

function IngestStatusBadge({ status }: { status: string }) {
  const label = ingestLabelFor(status)

  if (status === "done") {
    return (
      <Badge variant="secondary" className="gap-1">
        <CheckCircle2 className="size-3" />
        {label}
      </Badge>
    )
  }
  if (status === "failed") {
    return (
      <Badge variant="destructive" className="gap-1">
        <XCircle className="size-3" />
        {label}
      </Badge>
    )
  }
  if (status === "none") {
    return (
      <Badge variant="outline" className="gap-1">
        <Clock className="size-3" />
        {label}
      </Badge>
    )
  }
  // "queued", "running", or any other unrecognized value: treat as
  // in-progress so nothing silently stalls on an unmapped status string.
  return (
    <Badge variant="outline" className="gap-1">
      <Loader2 className="size-3 animate-spin" />
      {label}
    </Badge>
  )
}

function PaperCard({ paper }: { paper: LiteraturePaperPublic }) {
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const queryClient = useQueryClient()
  const [addStarted, setAddStarted] = useState(paper.ingest_status !== "none")
  const [expanded, setExpanded] = useState(false)

  const addMutation = useMutation({
    mutationFn: () => addToDatabase(paper.id),
    onSuccess: () => {
      setAddStarted(true)
      showSuccessToast("Статья поставлена в очередь на добавление в базу")
      queryClient.invalidateQueries({ queryKey: ["ingest-status", paper.id] })
    },
    onError: () => showErrorToast("Не удалось добавить статью в базу"),
  })

  // Polls independently of the search-level query above, so ingest progress
  // keeps animating even after the parent search's `stage` reaches "done".
  const ingestQuery = useQuery({
    queryKey: ["ingest-status", paper.id],
    queryFn: () => getIngestStatus(paper.id),
    enabled: addStarted,
    refetchInterval: (query) =>
      ingestPollingStatuses.has(query.state.data?.status ?? "") ? 1500 : false,
  })

  const meta = [paper.authors, paper.year ? String(paper.year) : null]
    .filter(Boolean)
    .join(" · ")

  const ingestStatus = ingestQuery.data?.status ?? "none"

  return (
    <Card>
      {/* Collapsed view shows only the title; click to expand for the details. */}
      <CardHeader
        className="cursor-pointer select-none"
        onClick={() => setExpanded((e) => !e)}
      >
        <CardTitle className="flex items-start gap-2 text-sm font-medium">
          {expanded ? (
            <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          )}
          <span>{paper.title}</span>
        </CardTitle>
      </CardHeader>
      {expanded && (
        <CardContent className="flex flex-col gap-3">
          {meta && <CardDescription>{meta}</CardDescription>}
          <div className="flex flex-wrap items-center gap-2">
            {paper.doi && (
              <a
                href={`https://doi.org/${paper.doi}`}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
              >
                <Badge variant="outline" className="gap-1">
                  <ExternalLink className="size-3" />
                  DOI
                </Badge>
              </a>
            )}
            {paper.document_id && (
              <Link
                to="/wiki"
                search={{ doc: undefined }}
                onClick={(e) => e.stopPropagation()}
              >
                <Badge variant="secondary" className="gap-1">
                  <BookOpen className="size-3" />в базе знаний
                </Badge>
              </Link>
            )}
            {paper.citation_count !== null && (
              <Badge variant="outline" className="gap-1">
                <Quote className="size-3" />
                {paper.citation_count}
              </Badge>
            )}
            {paper.fulltext_status === "added" && (
              <Badge variant="secondary">добавлено в диалог</Badge>
            )}
            {paper.fetch_status === "downloading" && (
              <Badge variant="outline" className="gap-1">
                <Loader2 className="size-3 animate-spin" />
                Скачивание…
              </Badge>
            )}
            {paper.fetch_status === "pending" && (
              <Badge variant="outline" className="gap-1">
                <Clock className="size-3" />В очереди на загрузку
              </Badge>
            )}
            {paper.fetch_status === "failed" && (
              <Badge variant="destructive" className="gap-1">
                <AlertTriangle className="size-3" />
                Не удалось загрузить PDF
              </Badge>
            )}
          </div>
          {paper.abstract && (
            <p className="text-sm text-muted-foreground">{paper.abstract}</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <LoadingButton
              size="sm"
              loading={addMutation.isPending}
              disabled={addStarted}
              onClick={(e) => {
                e.stopPropagation()
                addMutation.mutate()
              }}
            >
              Добавить в базу
            </LoadingButton>
            {addStarted && <IngestStatusBadge status={ingestStatus} />}
          </div>
        </CardContent>
      )}
    </Card>
  )
}

export function LiteraturePanel({ searchId }: { searchId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["litsearch", searchId],
    queryFn: () => getSearch(searchId),
    refetchInterval: (query) => {
      const stage = query.state.data?.stage
      return stage === "done" || stage === "failed" ? false : 2000
    },
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Найденные статьи
          {data && <Badge variant="secondary">{data.papers.length}</Badge>}
        </CardTitle>
        {data && <CardDescription>{stageLabel[data.stage]}</CardDescription>}
        {data && data.queries.length > 0 && (
          <div className="flex flex-col gap-1 pt-1">
            <span className="text-xs font-medium text-muted-foreground">
              Запросы:
            </span>
            <div className="flex flex-wrap gap-1">
              {data.queries.map((query, i) => (
                <Badge key={i} variant="outline" className="font-normal">
                  {query}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {isLoading && (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
        )}
        {!isLoading && data?.papers.length === 0 && (
          <p className="text-sm text-muted-foreground">Статьи ещё не найдены</p>
        )}
        {data?.papers.map((paper) => (
          <PaperCard key={paper.id} paper={paper} />
        ))}
      </CardContent>
    </Card>
  )
}
