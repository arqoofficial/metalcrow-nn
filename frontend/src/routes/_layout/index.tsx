import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"

import { AnalyticsService, ChatService } from "@/client"
import { PageHeader } from "@/components/Common/PageHeader"
import { Skeleton } from "@/components/ui/skeleton"
import useAuth from "@/hooks/useAuth"
import { deriveNoDataGaps } from "@/lib/coverageGaps"
import { cn, formatChatTimestamp } from "@/lib/utils"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({ meta: [{ title: "Дашборд — MetalCrow" }] }),
})

const quickQuestions = [
  "Какая плотность тока оптимальна при электроэкстракции Ni?",
  "Куда уходит серебро при конвертировании штейна?",
  "Что ещё не изучено по флотации пентландита?",
]

function greeting(): string {
  const h = new Date().getHours()
  if (h < 5) return "Доброй ночи"
  if (h < 12) return "Доброе утро"
  if (h < 18) return "Добрый день"
  return "Добрый вечер"
}

function nf(value: number | null | undefined): string {
  if (value == null) return "—"
  return value.toLocaleString("ru-RU")
}

function StatCard({
  label,
  value,
  note,
  accent = false,
  to,
}: {
  label: string
  value: string
  note?: string
  accent?: boolean
  to?: string
}) {
  const inner = (
    <>
      <div
        className={cn(
          "text-xs",
          accent ? "text-confidence-medium-fg" : "text-muted-foreground",
        )}
      >
        {label}
      </div>
      <div
        className={cn(
          "pt-1 text-2xl font-bold tabular-nums",
          accent && "text-confidence-medium-fg",
        )}
      >
        {value}
      </div>
      {note && (
        <div
          className={cn(
            "pt-0.5 text-[11.5px]",
            accent ? "text-confidence-medium-fg" : "text-muted-foreground",
          )}
        >
          {note}
        </div>
      )}
    </>
  )

  const className = cn(
    "rounded-xl border p-4 md:p-[18px]",
    accent
      ? "border-confidence-medium/40 bg-confidence-medium-bg"
      : "border-border bg-card",
    to && "transition-colors hover:brightness-[0.99]",
  )

  if (to) {
    return (
      <Link to={to} className={cn(className, "block")}>
        {inner}
      </Link>
    )
  }
  return <div className={className}>{inner}</div>
}

function Dashboard() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: metrics, isLoading: metricsLoading } = useQuery({
    queryKey: ["analytics-metrics"],
    queryFn: () => AnalyticsService.metrics(),
  })

  const { data: coverage } = useQuery({
    queryKey: ["analytics-coverage"],
    queryFn: () => AnalyticsService.coverage(),
  })

  const { data: sessions } = useQuery({
    queryKey: ["chat-sessions"],
    queryFn: () => ChatService.listSessions(),
  })

  const askAgent = useMutation({
    mutationFn: (draft: string) =>
      ChatService.createSession({
        requestBody: { title: draft.slice(0, 60) },
      }),
    onSuccess: (created, draft) => {
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] })
      navigate({ to: "/chat", search: { session: created.id, draft } })
    },
  })

  const gapCount = coverage ? deriveNoDataGaps(coverage).length : null
  const recent = sessions?.data.slice(0, 5) ?? []
  const name = user?.full_name?.split(" ")[0] || user?.email?.split("@")[0]

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="Дашборд" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-6 p-6 md:p-8">
          <div>
            <h1 className="text-xl font-bold tracking-tight">
              {greeting()}
              {name ? `, ${name}` : ""}
            </h1>
            <p className="pt-1 text-sm text-muted-foreground">
              Поисково-аналитическая система над графом знаний R&D
              горно-металлургической отрасли.
            </p>
          </div>

          {/* стат-карточки */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {metricsLoading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-[92px] rounded-xl" />
              ))
            ) : (
              <>
                <StatCard
                  label="Документы"
                  value={nf(metrics?.total_documents)}
                  note={
                    metrics?.provenance_coverage != null
                      ? `провенанс ${Math.round(
                          metrics.provenance_coverage * 100,
                        )}%`
                      : undefined
                  }
                />
                <StatCard
                  label="Эксперименты"
                  value={nf(metrics?.total_experiments)}
                />
                <StatCard
                  label="Материалы"
                  value={nf(metrics?.total_materials)}
                />
                <StatCard
                  label="Пробелы в знаниях"
                  value={gapCount == null ? "…" : nf(gapCount)}
                  note="открыть карту →"
                  accent
                  to="/gaps"
                />
              </>
            )}
          </div>

          {/* панели */}
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded-xl border bg-card p-5">
              <div className="pb-3 text-[11px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
                Недавние сессии
              </div>
              <div className="flex flex-col gap-1">
                {recent.length === 0 && (
                  <p className="px-1 py-2 text-sm text-muted-foreground">
                    Сессий пока нет — задайте вопрос агенту.
                  </p>
                )}
                {recent.map((s) => (
                  <Link
                    key={s.id}
                    to="/chat"
                    search={{ session: s.id }}
                    className="flex items-center justify-between gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-muted/60"
                  >
                    <span className="truncate text-[13.5px] font-medium">
                      {s.title || "Без названия"}
                    </span>
                    <span className="shrink-0 text-[11.5px] text-muted-foreground">
                      {formatChatTimestamp(s.created_at) ?? ""}
                    </span>
                  </Link>
                ))}
              </div>
            </div>

            <div className="rounded-xl border bg-card p-5">
              <div className="pb-3 text-[11px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
                Спросить агента
              </div>
              <div className="flex flex-col gap-2">
                {quickQuestions.map((q) => (
                  <button
                    key={q}
                    type="button"
                    disabled={askAgent.isPending}
                    onClick={() => askAgent.mutate(q)}
                    className="rounded-lg border px-3 py-2 text-left text-[12.5px] text-foreground transition-colors hover:border-primary hover:bg-primary/5 disabled:opacity-60"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
