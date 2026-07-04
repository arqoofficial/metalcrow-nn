import { useQuery } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import {
  ChevronDown,
  ChevronRight,
  ChevronsDown,
  ChevronsUp,
  Download,
  FileText,
  Folder,
  Loader2,
  Maximize2,
  Search,
  TriangleAlert,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react"

import {
  type WikiFileTreeNode,
  type WikiSearchResult,
  WikiService,
} from "@/client"
import { MarkdownContent } from "@/components/Common/MarkdownContent"
import { PageHeader } from "@/components/Common/PageHeader"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  downloadWikiDocument,
  fetchWikiRawBlobUrl,
} from "@/lib/downloadWikiDocument"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/wiki")({
  component: WikiPage,
  // `doc` = an okf_path to open directly, e.g. deep-linked from a chat source
  // chip (/wiki?doc=01_docling_clean00/.../file.pdf.md).
  validateSearch: (search: Record<string, unknown>) => ({
    doc: typeof search.doc === "string" ? search.doc : undefined,
  }),
  head: () => ({
    meta: [{ title: "Вики — MetalCrow" }],
  }),
})

function collectDirPaths(
  node: WikiFileTreeNode,
  parentPath: string,
  depth: number,
  maxDepth: number,
  acc: Set<string>,
): void {
  if (node.type !== "dir") return
  const currentPath = parentPath ? `${parentPath}/${node.name}` : node.name
  if (depth <= maxDepth) {
    acc.add(currentPath)
  }
  for (const child of node.children ?? []) {
    collectDirPaths(child, currentPath, depth + 1, maxDepth, acc)
  }
}

function collectAllDirPaths(
  node: WikiFileTreeNode,
  parentPath: string,
  acc: Set<string>,
): void {
  if (node.type !== "dir") return
  const currentPath = parentPath ? `${parentPath}/${node.name}` : node.name
  acc.add(currentPath)
  for (const child of node.children ?? []) {
    collectAllDirPaths(child, currentPath, acc)
  }
}

function WikiTreeNode({
  node,
  parentPath,
  depth,
  expanded,
  selectedPath,
  onToggle,
  onSelectFile,
}: {
  node: WikiFileTreeNode
  parentPath: string
  depth: number
  expanded: Set<string>
  selectedPath: string | null
  onToggle: (path: string) => void
  onSelectFile: (path: string) => void
}) {
  const currentPath = parentPath ? `${parentPath}/${node.name}` : node.name

  if (node.type === "file") {
    if (!node.path?.endsWith(".md")) return null
    const isSelected = selectedPath === node.path
    return (
      <button
        type="button"
        onClick={() => node.path && onSelectFile(node.path)}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
          isSelected
            ? "bg-accent text-accent-foreground"
            : "hover:bg-accent/50 text-muted-foreground hover:text-foreground",
        )}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        <FileText className="size-4 shrink-0 opacity-70" />
        <span className="truncate">{node.name}</span>
      </button>
    )
  }

  const isExpanded = expanded.has(currentPath)
  const hasChildren = (node.children?.length ?? 0) > 0

  return (
    <div>
      <button
        type="button"
        onClick={() => onToggle(currentPath)}
        className="hover:bg-accent/50 flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-left text-sm font-medium"
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {hasChildren ? (
          isExpanded ? (
            <ChevronDown className="size-4 shrink-0 opacity-70" />
          ) : (
            <ChevronRight className="size-4 shrink-0 opacity-70" />
          )
        ) : (
          <span className="size-4 shrink-0" />
        )}
        <Folder className="size-4 shrink-0 opacity-70" />
        <span className="truncate">{node.name}</span>
      </button>
      {isExpanded &&
        node.children?.map((child) => (
          <WikiTreeNode
            key={`${currentPath}/${child.name}`}
            node={child}
            parentPath={currentPath}
            depth={depth + 1}
            expanded={expanded}
            selectedPath={selectedPath}
            onToggle={onToggle}
            onSelectFile={onSelectFile}
          />
        ))}
    </div>
  )
}

type WikiViewMode = "original" | "markdown"

const WIKI_ORIGINAL_SUPPORTED_FORMAT = "PDF"

function isPdfRawPath(rawPath: string | null | undefined): boolean {
  return rawPath?.toLowerCase().endsWith(".pdf") ?? false
}

function rawFileLabel(rawPath: string): string {
  const name = rawPath.split("/").pop() ?? rawPath
  const dot = name.lastIndexOf(".")
  return dot >= 0 ? name.slice(dot + 1).toUpperCase() : name
}

type WikiRawPdfState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; url: string; isPdf: boolean }

function useWikiRawPdf(okfPath: string | null, enabled: boolean): WikiRawPdfState {
  const [state, setState] = useState<WikiRawPdfState>({ status: "idle" })

  useEffect(() => {
    if (!okfPath || !enabled) {
      setState({ status: "idle" })
      return
    }

    let objectUrl: string | null = null
    let cancelled = false
    setState({ status: "loading" })

    fetchWikiRawBlobUrl(okfPath)
      .then(({ url, contentType }) => {
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
        objectUrl = url
        setState({
          status: "ready",
          url,
          isPdf: contentType.startsWith("application/pdf"),
        })
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ status: "error", message: err.message })
      })

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [enabled, okfPath])

  return state
}

function WikiPdfEmbed({
  pdfUrl,
  className,
  fill = false,
}: {
  pdfUrl: string
  className?: string
  fill?: boolean
}) {
  const embed = (
    <embed
      src={pdfUrl}
      type="application/pdf"
      className={cn(
        "w-full rounded-md border",
        fill ? "h-full min-h-0" : className,
      )}
    />
  )

  if (fill) {
    return <div className="min-h-0 flex-1">{embed}</div>
  }

  return embed
}

function WikiOriginalView({
  rawPdf,
  rawPath,
  fill = false,
}: {
  rawPdf: WikiRawPdfState
  rawPath: string | null | undefined
  fill?: boolean
}) {
  if (!rawPath) {
    return (
      <p className="text-muted-foreground text-sm">
        Исходный файл для этого документа недоступен.
      </p>
    )
  }

  if (rawPdf.status === "loading" || rawPdf.status === "idle") {
    return (
      <div className="text-muted-foreground flex items-center gap-2 text-sm">
        <Loader2 className="size-4 animate-spin" />
        Загрузка оригинала…
      </div>
    )
  }

  if (rawPdf.status === "error") {
    return <p className="text-destructive text-sm">{rawPdf.message}</p>
  }

  if (rawPdf.isPdf) {
    return (
      <WikiPdfEmbed pdfUrl={rawPdf.url} fill={fill} className="h-[75vh]" />
    )
  }

  return null
}

function WikiOriginalUnsupportedNotice({
  rawPath,
}: {
  rawPath: string | null | undefined
}) {
  return (
    <Alert className="border-amber-200/80 bg-amber-50 text-amber-950 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-50 [&>svg]:text-amber-600 dark:[&>svg]:text-amber-400">
      <TriangleAlert />
      <AlertTitle>Показан Markdown вместо оригинала</AlertTitle>
      <AlertDescription className="text-amber-900/80 dark:text-amber-100/80">
        {rawPath ? (
          <>
            Просмотр оригинала в браузере поддерживается только для{" "}
            {WIKI_ORIGINAL_SUPPORTED_FORMAT}. Этот документ —{" "}
            {rawFileLabel(rawPath)}. Исходный файл можно скачать через «Скачать
            → Исходный файл».
          </>
        ) : (
          <>
            Просмотр оригинала в браузере поддерживается только для{" "}
            {WIKI_ORIGINAL_SUPPORTED_FORMAT}. Исходный файл для этого документа
            недоступен.
          </>
        )}
      </AlertDescription>
    </Alert>
  )
}

function WikiMarkdownView({ markdown }: { markdown: string }) {
  return <MarkdownContent content={markdown} />
}

function WikiFullscreenDialog({
  open,
  onOpenChange,
  title,
  padded = true,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  padded?: boolean
  children: ReactNode
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="fixed inset-0 top-0 left-0 flex h-screen w-screen max-w-none translate-x-0 translate-y-0 flex-col gap-0 rounded-none border-0 p-0 shadow-none sm:max-w-none"
        showCloseButton
        closeButtonClassName={
          padded
            ? undefined
            : "z-10 text-white opacity-90 hover:bg-white/15 hover:opacity-100 data-[state=open]:bg-transparent data-[state=open]:text-white focus:ring-white/40 ring-offset-transparent [&_svg]:text-white"
        }
      >
        <DialogTitle className="sr-only">{title}</DialogTitle>
        <div
          className={cn(
            "min-h-0 flex-1",
            padded ? "overflow-y-auto p-4 md:p-6" : "flex flex-col overflow-hidden",
          )}
        >
          {children}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function WikiPage() {
  const { doc } = Route.useSearch()
  const navigate = useNavigate()
  const selectedPath = doc ?? null
  const [query, setQuery] = useState("")
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [downloading, setDownloading] = useState<"markdown" | "raw" | null>(
    null,
  )
  const [viewMode, setViewMode] = useState<WikiViewMode>("original")
  const [fullscreenOpen, setFullscreenOpen] = useState(false)

  const selectDocument = useCallback(
    (path: string) => {
      navigate({ to: "/wiki", search: { doc: path } })
    },
    [navigate],
  )

  // Deep-link: when navigated to /wiki?doc=<okf_path> (e.g. from a chat source
  // chip), expand the tree to reveal the document.
  useEffect(() => {
    if (!doc) return
    const parts = doc.split("/")
    if (parts.length < 2) return
    const dirs = new Set<string>()
    for (let i = 1; i < parts.length; i++) {
      dirs.add(parts.slice(0, i).join("/"))
    }
    setExpanded((current) => new Set([...current, ...dirs]))
  }, [doc])

  const { data: treeData, isLoading: treeLoading } = useQuery({
    queryKey: ["wiki-tree"],
    queryFn: () => WikiService.getTree({ maxDepth: 10 }),
  })

  const trimmedQuery = query.trim()
  const { data: searchResults, isFetching: searchLoading } = useQuery({
    queryKey: ["wiki-search", trimmedQuery],
    queryFn: () => WikiService.search({ q: trimmedQuery }),
    enabled: trimmedQuery.length > 0,
  })

  const { data: document, isLoading: documentLoading } = useQuery({
    queryKey: ["wiki-document", selectedPath],
    queryFn: () =>
      WikiService.getDocumentContent({ okfPath: selectedPath as string }),
    enabled: !!selectedPath,
  })

  const hasPdfOriginal = isPdfRawPath(document?.raw_path)
  const rawPdf = useWikiRawPdf(
    selectedPath,
    !!selectedPath && !!document && hasPdfOriginal,
  )

  useEffect(() => {
    setFullscreenOpen(false)
  }, [selectedPath])

  useEffect(() => {
    if (!document || documentLoading) return
    setViewMode(hasPdfOriginal ? "original" : "markdown")
  }, [selectedPath, document, documentLoading, hasPdfOriginal])

  const canFullscreen =
    !!document &&
    (viewMode === "markdown" ||
      (hasPdfOriginal && rawPdf.status === "ready" && rawPdf.isPdf))

  const isPdfOriginalView =
    viewMode === "original" &&
    hasPdfOriginal &&
    rawPdf.status === "ready" &&
    rawPdf.isPdf

  const fullscreenTitle =
    viewMode === "original" ? "Оригинал — полный экран" : "Markdown — полный экран"

  useEffect(() => {
    if (!treeData?.children?.length) return
    const next = new Set<string>()
    for (const child of treeData.children) {
      collectDirPaths(child, "", 0, 2, next)
    }
    setExpanded(next)
  }, [treeData?.children])

  const toggleDir = useCallback((path: string) => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
      }
      return next
    })
  }, [])

  const handleDownload = async (variant: "markdown" | "raw") => {
    if (!selectedPath) return
    setDownloading(variant)
    try {
      await downloadWikiDocument(selectedPath, variant)
    } finally {
      setDownloading(null)
    }
  }

  const searchItems = useMemo<WikiSearchResult[]>(
    () => searchResults?.results ?? [],
    [searchResults],
  )

  const allDirPaths = useMemo(() => {
    const paths = new Set<string>()
    for (const child of treeData?.children ?? []) {
      collectAllDirPaths(child, "", paths)
    }
    return paths
  }, [treeData?.children])

  const allExpanded =
    allDirPaths.size > 0 && [...allDirPaths].every((path) => expanded.has(path))

  const toggleExpandAll = useCallback(() => {
    setExpanded(allExpanded ? new Set() : new Set(allDirPaths))
  }, [allDirPaths, allExpanded])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="Вики" />
      <div className="grid min-h-0 flex-1 gap-4 overflow-hidden p-4 md:p-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card className="flex min-h-0 flex-col">
          <CardHeader className="gap-3 space-y-0 pb-3">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-base">Документы</CardTitle>
              {!trimmedQuery && allDirPaths.size > 0 && (
                <button
                  type="button"
                  onClick={toggleExpandAll}
                  className="text-muted-foreground hover:text-foreground rounded-md p-1 transition-colors"
                  aria-label={allExpanded ? "Свернуть всё" : "Развернуть всё"}
                  title={allExpanded ? "Свернуть всё" : "Развернуть всё"}
                >
                  {allExpanded ? (
                    <ChevronsUp className="size-4" />
                  ) : (
                    <ChevronsDown className="size-4" />
                  )}
                </button>
              )}
            </div>
            <div className="relative">
              <Search className="text-muted-foreground absolute top-2.5 left-2.5 size-4" />
              <Input
                placeholder="Поиск по документам…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="pl-9"
              />
            </div>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 overflow-y-auto pt-0">
            {trimmedQuery ? (
              <div className="flex flex-col gap-1">
                {searchLoading && (
                  <div className="text-muted-foreground flex items-center gap-2 px-2 py-2 text-sm">
                    <Loader2 className="size-4 animate-spin" />
                    Поиск…
                  </div>
                )}
                {!searchLoading && searchItems.length === 0 && (
                  <p className="text-muted-foreground px-2 py-2 text-sm">
                    Ничего не найдено
                  </p>
                )}
                {searchItems.map((result) => (
                  <button
                    key={result.okf_path}
                    type="button"
                    onClick={() => selectDocument(result.okf_path)}
                    className={cn(
                      "flex flex-col gap-0.5 rounded-md border px-3 py-2 text-left transition-colors",
                      selectedPath === result.okf_path
                        ? "bg-accent text-accent-foreground"
                        : "hover:bg-accent/50",
                    )}
                  >
                    <span className="text-sm font-medium">{result.title}</span>
                    {result.snippet && (
                      <span className="text-muted-foreground truncate text-xs">
                        {result.snippet}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            ) : treeLoading ? (
              <div className="flex flex-col gap-2 px-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-4/5" />
                <Skeleton className="h-8 w-3/5" />
              </div>
            ) : treeData?.children?.length ? (
              treeData.children.map((node) => (
                <WikiTreeNode
                  key={node.name}
                  node={node}
                  parentPath=""
                  depth={0}
                  expanded={expanded}
                  selectedPath={selectedPath}
                  onToggle={toggleDir}
                  onSelectFile={selectDocument}
                />
              ))
            ) : (
              <p className="text-muted-foreground px-2 text-sm">
                Документов нет
              </p>
            )}
          </CardContent>
        </Card>

        <Card className="flex h-full min-h-0 flex-col gap-2">
          <CardHeader className="flex-row items-start justify-between gap-4 space-y-0 pb-0">
            <div className="min-w-0 flex-1">
              <CardTitle className="truncate text-base">
                {document?.title ?? "Выберите документ"}
              </CardTitle>
              {document?.display_path && (
                <p className="text-muted-foreground mt-1 truncate font-mono text-xs">
                  {document.display_path}
                </p>
              )}
            </div>
            {selectedPath && document && !documentLoading && (
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground hidden text-sm sm:inline">
                    Режим просмотра
                  </span>
                  <Select
                    value={viewMode}
                    onValueChange={(value) =>
                      setViewMode(value as WikiViewMode)
                    }
                  >
                    <SelectTrigger
                      size="sm"
                      className="w-[140px]"
                      aria-label="Режим просмотра"
                    >
                      <SelectValue placeholder="Режим просмотра" />
                    </SelectTrigger>
                    <SelectContent align="end">
                      <SelectItem value="original" disabled={!hasPdfOriginal}>
                        Оригинал
                      </SelectItem>
                      <SelectItem value="markdown">Markdown</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={downloading !== null}
                    >
                      {downloading ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Download className="size-4" />
                      )}
                      Скачать
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onClick={() => handleDownload("markdown")}
                    >
                      Очищенный markdown
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => handleDownload("raw")}
                      disabled={!document.raw_path}
                    >
                      Исходный файл
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!canFullscreen}
                  onClick={() => setFullscreenOpen(true)}
                >
                  <Maximize2 className="size-4" />
                  Полный экран
                </Button>
              </div>
            )}
          </CardHeader>
          <CardContent
            className={cn(
              "flex min-h-0 flex-1 flex-col pt-0",
              isPdfOriginalView ? "overflow-hidden" : "overflow-y-auto",
            )}
          >
            {!selectedPath && (
              <div className="text-muted-foreground flex h-full min-h-[240px] items-center justify-center text-sm">
                Выберите файл из дерева или результатов поиска
              </div>
            )}
            {selectedPath && documentLoading && (
              <div className="flex flex-col gap-3">
                <Skeleton className="h-6 w-2/3" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-5/6" />
              </div>
            )}
            {document && !documentLoading && (
              <div
                className={cn(
                  "flex flex-col gap-2",
                  isPdfOriginalView && "min-h-0 flex-1",
                )}
              >
                {document.raw_path && (
                  <Badge variant="outline" className="w-fit shrink-0 font-normal">
                    Raw: {document.raw_path}
                  </Badge>
                )}
                {!hasPdfOriginal && (
                  <WikiOriginalUnsupportedNotice rawPath={document.raw_path} />
                )}
                {viewMode === "original" ? (
                  <WikiOriginalView
                    rawPdf={rawPdf}
                    rawPath={document.raw_path}
                    fill={isPdfOriginalView}
                  />
                ) : (
                  <WikiMarkdownView markdown={document.markdown} />
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {document && (
        <WikiFullscreenDialog
          open={fullscreenOpen}
          onOpenChange={setFullscreenOpen}
          title={fullscreenTitle}
          padded={
            !(
              viewMode === "original" &&
              rawPdf.status === "ready" &&
              rawPdf.isPdf
            )
          }
        >
          {viewMode === "original" &&
          rawPdf.status === "ready" &&
          rawPdf.isPdf ? (
            <WikiPdfEmbed
              pdfUrl={rawPdf.url}
              fill
              className="rounded-none border-0"
            />
          ) : (
            <WikiMarkdownView markdown={document.markdown} />
          )}
        </WikiFullscreenDialog>
      )}
    </div>
  )
}
