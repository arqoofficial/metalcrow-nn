import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Download, Loader2, Trash2, UploadCloud } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { type DocumentFileSummary, IngestService } from "@/client"
import { PageContainer } from "@/components/Common/PageContainer"
import { TablePagination } from "@/components/Common/TablePagination"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useCustomToast from "@/hooks/useCustomToast"
import { getPageCount, usePagination } from "@/hooks/usePagination"
import { downloadDocument } from "@/lib/downloadDocument"
import { ensureSuperuser } from "@/lib/session"
import { type UploadProgress, uploadDocuments } from "@/lib/uploadDocuments"
import { cn } from "@/lib/utils"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/ingest")({
  component: IngestPage,
  beforeLoad: ensureSuperuser,
  head: () => ({
    meta: [{ title: "Загрузка — MetalCrow" }],
  }),
})

const ACCEPT = ".pdf,.docx,.pptx,.xlsx,.csv"

// Статусы IngestTask, которые ещё не завершены — сигнал держать polling включённым.
const TERMINAL_TASK_STATUSES = new Set(["done", "error"])

function isFileProcessing(file: DocumentFileSummary): boolean {
  return (
    !!file.latest_task_status &&
    !TERMINAL_TASK_STATUSES.has(file.latest_task_status)
  )
}

function formatUploadedAt(value: string | null | undefined): string {
  if (!value) return "—"
  return new Date(value).toLocaleString()
}

function StatusBadge({ file }: { file: DocumentFileSummary }) {
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
        Готово
      </Badge>
    )
  }
  if (status === "error") {
    return (
      <Badge
        variant="destructive"
        title={file.latest_task_error ?? "Ошибка обработки"}
      >
        Ошибка
      </Badge>
    )
  }
  const pct =
    typeof file.latest_task_progress === "number"
      ? Math.round(file.latest_task_progress * 100)
      : null
  return (
    <Badge variant="secondary" className="gap-1">
      <Loader2 className="size-3 animate-spin" />
      {status === "queued"
        ? "В очереди"
        : `Обработка${pct !== null ? ` ${pct}%` : "…"}`}
    </Badge>
  )
}

function DropZone({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void
  disabled: boolean
}) {
  const [dragOver, setDragOver] = useState(false)

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      setDragOver(false)
      if (disabled) return
      const dropped = Array.from(event.dataTransfer.files)
      if (dropped.length > 0) onFiles(dropped)
    },
    [disabled, onFiles],
  )

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: drag-and-drop upload zone
    <div
      onDragOver={(event) => {
        event.preventDefault()
        if (!disabled) setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-10 text-center transition-colors",
        dragOver && "border-primary bg-primary/5",
        disabled && "pointer-events-none opacity-50",
      )}
    >
      <UploadCloud className="size-10 text-muted-foreground" />
      <div>
        <p className="font-medium">Перетащите файлы сюда</p>
        <p className="text-sm text-muted-foreground">
          PDF, DOCX, PPTX, XLSX или CSV — до 50 МБ каждый
        </p>
      </div>
      <label>
        <input
          type="file"
          accept={ACCEPT}
          multiple
          className="sr-only"
          disabled={disabled}
          onChange={(event) => {
            const picked = Array.from(event.target.files ?? [])
            if (picked.length > 0) onFiles(picked)
            event.target.value = ""
          }}
        />
        <Button type="button" variant="secondary" disabled={disabled} asChild>
          <span>Выбрать файлы</span>
        </Button>
      </label>
    </div>
  )
}

function FilesTable({
  files,
  onDelete,
  onDownload,
  deletingId,
  downloadingId,
}: {
  files: DocumentFileSummary[]
  onDelete: (id: string) => void
  onDownload: (id: string) => void
  deletingId: string | null
  downloadingId: string | null
}) {
  if (files.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Документы ещё не загружены.
      </p>
    )
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Имя файла</TableHead>
          <TableHead>Тип</TableHead>
          <TableHead>Уровень</TableHead>
          <TableHead>Статус</TableHead>
          <TableHead>Загружен</TableHead>
          <TableHead className="text-right">Действия</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {files.map((file) => (
          <TableRow key={file.id}>
            <TableCell className="max-w-xs truncate font-medium">
              {file.filename}
            </TableCell>
            <TableCell className="text-muted-foreground">
              {file.mime_type ?? "—"}
            </TableCell>
            <TableCell>
              <Badge variant="outline">{file.processing_level}</Badge>
            </TableCell>
            <TableCell>
              <StatusBadge file={file} />
            </TableCell>
            <TableCell className="text-muted-foreground">
              {formatUploadedAt(file.uploaded_at)}
            </TableCell>
            <TableCell className="text-right">
              <div className="flex justify-end gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  title="Скачать"
                  disabled={downloadingId === file.id}
                  onClick={() => onDownload(file.id)}
                >
                  <Download className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  title="Удалить"
                  disabled={deletingId === file.id}
                  onClick={() => onDelete(file.id)}
                >
                  <Trash2 className="size-4 text-destructive" />
                </Button>
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function IngestPage() {
  const queryClient = useQueryClient()
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const { pageIndex, pageSize, offset, setPageIndex, setPageSize, resetPage } =
    usePagination()
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(
    null,
  )

  const { data: filesResponse, isLoading } = useQuery({
    queryKey: ["ingest-files", pageIndex, pageSize],
    queryFn: () => IngestService.listFiles({ limit: pageSize, offset }),
    // Пока хотя бы один файл на странице ещё парсится — обновляем каждые 3с,
    // чтобы прогресс/уровень в таблице менялись без ручного refresh.
    refetchInterval: (query) => {
      const files = query.state.data?.data ?? []
      return files.some(isFileProcessing) ? 3000 : false
    },
  })

  const totalCount = filesResponse?.count ?? 0
  const processingFiles = (filesResponse?.data ?? []).filter(isFileProcessing)

  useEffect(() => {
    if (totalCount === 0) {
      return
    }
    const maxPageIndex = Math.max(0, getPageCount(totalCount, pageSize) - 1)
    if (pageIndex > maxPageIndex) {
      setPageIndex(maxPageIndex)
    }
  }, [pageIndex, pageSize, setPageIndex, totalCount])

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => uploadDocuments(files, setUploadProgress),
    onSuccess: (result) => {
      showSuccessToast(
        `Uploaded ${result.count} file${result.count === 1 ? "" : "s"}`,
      )
      resetPage()
      queryClient.invalidateQueries({ queryKey: ["ingest-files"] })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => setUploadProgress(null),
  })

  const deleteMutation = useMutation({
    mutationFn: (documentId: string) =>
      IngestService.deleteFile({ documentId }),
    onMutate: (documentId) => setDeletingId(documentId),
    onSettled: () => setDeletingId(null),
    onSuccess: () => {
      showSuccessToast("Document deleted")
      queryClient.invalidateQueries({ queryKey: ["ingest-files"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const handleDownload = async (documentId: string) => {
    setDownloadingId(documentId)
    try {
      await downloadDocument(documentId)
    } catch (error) {
      showErrorToast(error instanceof Error ? error.message : "Download failed")
    } finally {
      setDownloadingId(null)
    }
  }

  return (
    <PageContainer title="Загрузка документов">
      <div className="flex flex-col gap-6">
        <p className="text-sm text-muted-foreground">
          Загрузите документы — парсинг (L1) стартует автоматически в фоне.
        </p>

        {processingFiles.length > 0 && (
          <div className="flex items-center gap-2 rounded-lg border border-dashed bg-muted/30 px-4 py-3 text-sm">
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
            <span>
              Обрабатывается файлов: {processingFiles.length} — парсер работает,
              страница обновляется автоматически.
            </span>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Загрузить документы</CardTitle>
            <CardDescription>
              Файлы сохраняются в SHARED через парсер и сразу ставятся в очередь
              на разбор L1.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DropZone
              disabled={uploadMutation.isPending}
              onFiles={(files) => uploadMutation.mutate(files)}
            />
            {uploadProgress && (
              <p className="mt-3 text-sm text-muted-foreground">
                Загрузка {uploadProgress.completed + 1} из{" "}
                {uploadProgress.total}
                {uploadProgress.currentFile
                  ? `: ${uploadProgress.currentFile}`
                  : ""}
                …
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Загруженные файлы
              {filesResponse && (
                <Badge variant="secondary">{filesResponse.count}</Badge>
              )}
            </CardTitle>
            <CardDescription>
              Все документы корпуса с текущим уровнем обработки и статусом
              разбора в реальном времени.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Загрузка…</p>
            ) : (
              <div className="flex flex-col gap-4">
                <FilesTable
                  files={filesResponse?.data ?? []}
                  deletingId={deletingId}
                  downloadingId={downloadingId}
                  onDelete={(id) => deleteMutation.mutate(id)}
                  onDownload={handleDownload}
                />
                <TablePagination
                  pageIndex={pageIndex}
                  pageSize={pageSize}
                  totalCount={totalCount}
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
