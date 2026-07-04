import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { FileText, Loader2, Play } from "lucide-react"
import { useEffect, useState } from "react"

import {
  type AdminCoverageResponse,
  AdminService,
  type DocumentFileSummary,
  IngestService,
  type ProcessingLevel,
} from "@/client"
import { PageContainer } from "@/components/Common/PageContainer"
import { TablePagination } from "@/components/Common/TablePagination"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useCustomToast from "@/hooks/useCustomToast"
import { usePagination } from "@/hooks/usePagination"
import {
  listUnparsedRawFiles,
  parseRawDataFile,
  type RawDataFileSummary,
} from "@/lib/adminRawFiles"
import { ensureSuperuser } from "@/lib/session"
import { cn } from "@/lib/utils"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/admin/coverage")({
  component: AdminCoveragePage,
  beforeLoad: ensureSuperuser,
  head: () => ({
    meta: [{ title: "Покрытие — MetalCrow" }],
  }),
})

const LEVEL_RANK: Record<ProcessingLevel, number> = {
  L0: 0,
  L1: 1,
  L2: 2,
  L3: 3,
}

const TERMINAL_TASK_STATUSES = new Set(["done", "error"])

const LEVEL_META: Record<
  Exclude<ProcessingLevel, "L0">,
  { label: string; description: string }
> = {
  L1: { label: "L1 fast parse", description: "Docling → Markdown, searchable" },
  L2: { label: "L2 spaCy NER", description: "Entities, synonyms, constraints" },
  L3: { label: "L3 deep LLM", description: "Ontology, triples, OKF facts" },
}

function isFileProcessing(file: DocumentFileSummary): boolean {
  return (
    !!file.latest_task_status &&
    !TERMINAL_TASK_STATUSES.has(file.latest_task_status)
  )
}

function levelReached(
  current: ProcessingLevel,
  target: ProcessingLevel,
): boolean {
  return LEVEL_RANK[current] >= LEVEL_RANK[target]
}

function formatPercent(count: number, total: number): number {
  if (total === 0) return 0
  return Math.round((count / total) * 1000) / 10
}

function buildCorpusStats(coverage: AdminCoverageResponse) {
  const counts = Object.fromEntries(
    coverage.by_level.map((item) => [item.level, item.count]),
  ) as Partial<Record<ProcessingLevel, number>>

  const total = coverage.total_files
  const l0 = counts.L0 ?? 0
  const l1Plus = (counts.L1 ?? 0) + (counts.L2 ?? 0) + (counts.L3 ?? 0)
  const l2Plus = (counts.L2 ?? 0) + (counts.L3 ?? 0)
  const l3 = counts.L3 ?? 0

  return {
    total,
    l0,
    l1Plus: { count: l1Plus, percent: formatPercent(l1Plus, total) },
    l2Plus: { count: l2Plus, percent: formatPercent(l2Plus, total) },
    l3: { count: l3, percent: formatPercent(l3, total) },
  }
}

function StatCard({
  title,
  description,
  count,
  percent,
  total,
  accentClass,
}: {
  title: string
  description: string
  count: number
  percent: number
  total: number
  accentClass: string
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-3xl tabular-nums">{percent}%</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">{description}</p>
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div
            className={cn("h-full rounded-full transition-all", accentClass)}
            style={{ width: `${Math.min(percent, 100)}%` }}
          />
        </div>
        <p className="text-xs text-muted-foreground tabular-nums">
          {count} of {total} files
        </p>
      </CardContent>
    </Card>
  )
}

function LevelIndicators({ level }: { level: ProcessingLevel }) {
  const steps: Exclude<ProcessingLevel, "L0">[] = ["L1", "L2", "L3"]

  return (
    <div className="flex items-center gap-1.5">
      {steps.map((step) => {
        const done = levelReached(level, step)
        return (
          <div key={step} className="flex flex-col items-center gap-1">
            <div
              className={cn(
                "size-2.5 rounded-full border",
                done
                  ? "border-green-600/50 bg-green-600 dark:bg-green-500"
                  : "border-muted-foreground/30 bg-muted",
              )}
              title={`${step}${done ? " — done" : " — pending"}`}
            />
            <span className="text-[10px] text-muted-foreground">{step}</span>
          </div>
        )
      })}
    </div>
  )
}

function FileStatusBadge({ file }: { file: DocumentFileSummary }) {
  const status = file.latest_task_status
  if (!status) {
    return <span className="text-sm text-muted-foreground">—</span>
  }
  if (status === "done") {
    return (
      <Badge
        variant="outline"
        className="border-green-600/50 text-green-700 dark:text-green-400"
      >
        Done
      </Badge>
    )
  }
  if (status === "error") {
    return (
      <Badge variant="destructive" title={file.latest_task_error ?? undefined}>
        Failed
      </Badge>
    )
  }
  return (
    <Badge variant="secondary" className="gap-1">
      <Loader2 className="size-3 animate-spin" />
      {status === "queued" ? "Queued" : "Processing"}
    </Badge>
  )
}

function RawFileTaskBadge({ file }: { file: RawDataFileSummary }) {
  const status = file.latest_task_status
  if (!status) {
    return <span className="text-sm text-muted-foreground">Not queued</span>
  }
  if (status === "done") {
    return (
      <Badge
        variant="outline"
        className="border-green-600/50 text-green-700 dark:text-green-400"
      >
        Done
      </Badge>
    )
  }
  if (status === "error") {
    return <Badge variant="destructive">Failed</Badge>
  }
  return (
    <Badge variant="secondary" className="gap-1">
      <Loader2 className="size-3 animate-spin" />
      {status === "queued" ? "Queued" : "Processing"}
    </Badge>
  )
}

function isRawFileProcessing(file: RawDataFileSummary): boolean {
  return (
    !!file.latest_task_status &&
    !TERMINAL_TASK_STATUSES.has(file.latest_task_status)
  )
}

function UnparsedRawDataCard() {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [searchInput, setSearchInput] = useState("")
  const [search, setSearch] = useState("")
  const { pageIndex, pageSize, offset, setPageIndex, setPageSize } =
    usePagination()

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim())
      setPageIndex(0)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [searchInput, setPageIndex])

  const { data: rawResponse, isLoading } = useQuery({
    queryKey: ["admin-raw-files", search, pageIndex, pageSize],
    queryFn: () => listUnparsedRawFiles({ search, limit: pageSize, offset }),
    refetchInterval: (query) => {
      const files = query.state.data?.data ?? []
      return files.some(isRawFileProcessing) ? 3000 : false
    },
  })

  const parseMutation = useMutation({
    mutationFn: (path: string) => parseRawDataFile(path),
    onSuccess: () => {
      showSuccessToast("Parsing queued")
      queryClient.invalidateQueries({ queryKey: ["admin-raw-files"] })
      queryClient.invalidateQueries({ queryKey: ["admin-coverage-files"] })
      queryClient.invalidateQueries({ queryKey: ["admin-coverage"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const rawFiles = rawResponse?.data ?? []
  const processingCount = rawFiles.filter(isRawFileProcessing).length

  return (
    <Card>
      <CardHeader>
        <CardTitle>Unparsed RAW_DATA</CardTitle>
        <CardDescription>
          PDFs on disk under SHARED/RAW_DATA without Docling output yet — queue
          them manually; once parsed they appear in Per-file progress below
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Input
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Search by filename or path…"
          className="max-w-md"
        />

        {processingCount > 0 && (
          <p className="text-sm text-muted-foreground">
            {processingCount} file{processingCount === 1 ? "" : "s"} on this
            page still processing — list refreshes every 3 seconds.
          </p>
        )}

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : rawFiles.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {search
              ? "No unparsed PDFs match your search."
              : "All RAW_DATA PDFs are already parsed."}
          </p>
        ) : (
          <div className="flex flex-col gap-4">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Filename</TableHead>
                  <TableHead>Path</TableHead>
                  <TableHead>Task</TableHead>
                  <TableHead className="w-28 text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rawFiles.map((file) => {
                  const busy =
                    parseMutation.isPending &&
                    parseMutation.variables === file.path
                  const processing = isRawFileProcessing(file)
                  return (
                    <TableRow key={file.path}>
                      <TableCell className="max-w-xs truncate font-medium">
                        {file.filename}
                      </TableCell>
                      <TableCell
                        className="max-w-sm truncate text-muted-foreground"
                        title={file.path}
                      >
                        {file.path}
                      </TableCell>
                      <TableCell>
                        <RawFileTaskBadge file={file} />
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy || processing}
                          onClick={() => parseMutation.mutate(file.path)}
                        >
                          {busy || processing ? (
                            <Loader2 className="size-4 animate-spin" />
                          ) : (
                            <Play className="size-4" />
                          )}
                          Parse
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
            <TablePagination
              pageIndex={pageIndex}
              pageSize={pageSize}
              totalCount={rawResponse?.count ?? 0}
              onPageIndexChange={setPageIndex}
              onPageSizeChange={setPageSize}
            />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function AdminCoveragePage() {
  const { pageIndex, pageSize, offset, setPageIndex, setPageSize } =
    usePagination()

  const { data: filesResponse, isLoading: filesLoading } = useQuery({
    queryKey: ["admin-coverage-files", pageIndex, pageSize],
    queryFn: () => IngestService.listFiles({ limit: pageSize, offset }),
    refetchInterval: (query) => {
      const files = query.state.data?.data ?? []
      return files.some(isFileProcessing) ? 3000 : false
    },
  })

  const hasProcessingFiles = (filesResponse?.data ?? []).some(isFileProcessing)

  const { data: coverage, isLoading: coverageLoading } = useQuery({
    queryKey: ["admin-coverage"],
    queryFn: () => AdminService.adminCoverage(),
    refetchInterval: hasProcessingFiles ? 3000 : false,
  })

  const stats = coverage ? buildCorpusStats(coverage) : null
  const files = filesResponse?.data ?? []
  const processingCount = files.filter(isFileProcessing).length

  return (
    <PageContainer
      title="Покрытие корпуса"
      actions={
        <Button variant="outline" asChild>
          <Link to="/ingest">
            <FileText className="size-4" />
            Управление загрузкой
          </Link>
        </Button>
      }
    >
      <div className="flex flex-col gap-6">
        {processingCount > 0 && (
          <Alert>
            <Loader2 className="animate-spin" />
            <AlertTitle>Processing in progress</AlertTitle>
            <AlertDescription>
              {processingCount} file{processingCount === 1 ? "" : "s"} on this
              page still running — stats refresh automatically every 3 seconds.
            </AlertDescription>
          </Alert>
        )}

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {coverageLoading || !stats ? (
            Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-40 rounded-xl" />
            ))
          ) : (
            <>
              <StatCard
                title={LEVEL_META.L1.label}
                description={LEVEL_META.L1.description}
                count={stats.l1Plus.count}
                percent={stats.l1Plus.percent}
                total={stats.total}
                accentClass="bg-blue-500"
              />
              <StatCard
                title={LEVEL_META.L2.label}
                description={LEVEL_META.L2.description}
                count={stats.l2Plus.count}
                percent={stats.l2Plus.percent}
                total={stats.total}
                accentClass="bg-violet-500"
              />
              <StatCard
                title={LEVEL_META.L3.label}
                description={LEVEL_META.L3.description}
                count={stats.l3.count}
                percent={stats.l3.percent}
                total={stats.total}
                accentClass="bg-amber-500"
              />
              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>Total files</CardDescription>
                  <CardTitle className="text-3xl tabular-nums">
                    {stats.total}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm text-muted-foreground">
                  <p>
                    <span className="font-medium text-foreground">
                      {stats.l0}
                    </span>{" "}
                    at L0 (upload only)
                  </p>
                  <p>
                    <span className="font-medium text-foreground">
                      {stats.l1Plus.count}
                    </span>{" "}
                    searchable (L1+)
                  </p>
                </CardContent>
              </Card>
            </>
          )}
        </div>

        <UnparsedRawDataCard />

        <Card>
          <CardHeader>
            <CardTitle>Per-file progress</CardTitle>
            <CardDescription>
              Each document&apos;s highest completed processing level
            </CardDescription>
          </CardHeader>
          <CardContent>
            {filesLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : files.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No documents in the corpus yet.{" "}
                <Link to="/ingest" className="underline underline-offset-4">
                  Upload files
                </Link>
              </p>
            ) : (
              <div className="flex flex-col gap-4">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Filename</TableHead>
                      <TableHead>Level</TableHead>
                      <TableHead>L1 / L2 / L3</TableHead>
                      <TableHead>Task</TableHead>
                      <TableHead>Uploaded</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {files.map((file) => (
                      <TableRow key={file.id}>
                        <TableCell className="max-w-xs truncate font-medium">
                          {file.filename}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">
                            {file.processing_level}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <LevelIndicators level={file.processing_level} />
                        </TableCell>
                        <TableCell>
                          <FileStatusBadge file={file} />
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {file.uploaded_at
                            ? new Date(file.uploaded_at).toLocaleString()
                            : "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                <TablePagination
                  pageIndex={pageIndex}
                  pageSize={pageSize}
                  totalCount={filesResponse?.count ?? 0}
                  onPageIndexChange={setPageIndex}
                  onPageSizeChange={setPageSize}
                />
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  )
}
