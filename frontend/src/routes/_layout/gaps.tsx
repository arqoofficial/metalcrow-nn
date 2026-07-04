import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { Bell, Loader2 } from "lucide-react"
import { useMemo, useState } from "react"

import { AnalyticsService, ChatService } from "@/client"
import { PageHeader } from "@/components/Common/PageHeader"
import { StubMark } from "@/components/Common/StubMark"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { deriveNoDataGaps, regimeLabel } from "@/lib/coverageGaps"
import { cn, countRu } from "@/lib/utils"

export const Route = createFileRoute("/_layout/gaps")({
  component: GapsPage,
  head: () => ({ meta: [{ title: "Пробелы — MetalCrow" }] }),
})

const MAX_CARDS = 80

type GapType = "no_data" | "contradiction" | "single_source" | "geo" | "stale"

// Только no_data выводится из матрицы покрытия. Остальные типы требуют
// отдельного детектора (противоречия, счётчик источников, баланс языков,
// год) — помечены заглушкой.
const gapTypeMeta: Record<
  GapType,
  {
    label: string
    badge: "contradiction" | "confidenceMedium" | "external" | "stale"
    real: boolean
  }
> = {
  no_data: { label: "Нет данных", badge: "contradiction", real: true },
  contradiction: {
    label: "Противоречие",
    badge: "contradiction",
    real: false,
  },
  single_source: {
    label: "Один источник",
    badge: "confidenceMedium",
    real: false,
  },
  geo: { label: "Только RU / EN", badge: "external", real: false },
  stale: { label: "Данные устарели", badge: "stale", real: false },
}

const typeOrder: GapType[] = [
  "no_data",
  "contradiction",
  "single_source",
  "geo",
  "stale",
]

function GapsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<"all" | GapType>("all")
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { data: coverage, isLoading } = useQuery({
    queryKey: ["analytics-coverage"],
    queryFn: () => AnalyticsService.coverage(),
  })

  const allGaps = useMemo(
    () => (coverage ? deriveNoDataGaps(coverage) : []),
    [coverage],
  )

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

  // все реальные пробелы имеют тип no_data
  const visibleGaps = filter === "all" || filter === "no_data" ? allGaps : []
  const shown = visibleGaps.slice(0, MAX_CARDS)
  // деталь показываем только среди видимых пробелов, иначе на вкладке-заглушке
  // панель справа противоречила бы пустому списку слева.
  const selected =
    visibleGaps.find((g) => g.id === selectedId) ?? shown[0] ?? null

  const nearData = useMemo(() => {
    if (!coverage || !selected) return []
    return coverage.cells
      .filter((c) => c.material === selected.material && c.experiment_count > 0)
      .sort((a, b) => b.experiment_count - a.experiment_count)
      .slice(0, 5)
      .map(
        (c) =>
          `${c.property} · ${regimeLabel[c.regime_bucket]} · ${countRu(
            c.experiment_count,
            ["эксперимент", "эксперимента", "экспериментов"],
          )}`,
      )
  }, [coverage, selected])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Пробелы в знаниях"
        actions={
          <span className="hidden text-xs text-muted-foreground md:inline">
            выводятся из матрицы покрытия графа
          </span>
        }
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_360px]">
        {/* список */}
        <div className="flex min-h-0 min-w-0 flex-col">
          <div className="flex flex-wrap gap-1.5 px-4 pt-4 md:px-7">
            <FilterTab
              label={`Все · ${allGaps.length}`}
              active={filter === "all"}
              onClick={() => setFilter("all")}
            />
            {typeOrder.map((t) => {
              const meta = gapTypeMeta[t]
              const count = t === "no_data" ? allGaps.length : 0
              return (
                <FilterTab
                  key={t}
                  label={`${meta.label} · ${count}`}
                  active={filter === t}
                  stub={!meta.real}
                  onClick={() => setFilter(t)}
                />
              )
            })}
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4 py-4 md:px-7">
            {isLoading && (
              <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Загрузка матрицы
                покрытия…
              </div>
            )}

            {!isLoading && filter !== "all" && filter !== "no_data" && (
              <div className="rounded-xl border border-dashed border-destructive/40 bg-destructive/5 p-6 text-sm text-muted-foreground">
                <StubMark /> Детектор пробелов типа «{gapTypeMeta[filter].label}
                » ещё не реализован в бэкенде. Пока доступны только пробелы «нет
                данных», вычисляемые из матрицы покрытия онтологии.
              </div>
            )}

            {!isLoading &&
              (filter === "all" || filter === "no_data") &&
              shown.length === 0 && (
                <p className="py-10 text-center text-sm text-muted-foreground">
                  Пробелов не найдено — матрица покрытия пуста или полностью
                  покрыта. Наполните граф в разделе «Загрузка».
                </p>
              )}

            {shown.map((gap) => {
              const isSel = selected?.id === gap.id
              return (
                <button
                  key={gap.id}
                  type="button"
                  onClick={() => setSelectedId(gap.id)}
                  className={cn(
                    "flex-none rounded-xl border bg-card p-3.5 text-left transition-shadow md:px-[18px]",
                    isSel
                      ? "border-primary ring-1 ring-primary"
                      : "border-border hover:border-primary/40",
                  )}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="contradiction" className="text-[11px]">
                      Нет данных
                    </Badge>
                    <span className="ml-auto text-[11.5px] text-muted-foreground">
                      материал изучен в{" "}
                      <b className="font-mono text-foreground">
                        {gap.importance}
                      </b>{" "}
                      эксп.
                    </span>
                  </div>
                  <div className="pt-2 text-sm leading-snug font-semibold text-foreground">
                    {gap.title}
                  </div>
                  <div className="pt-1 text-[12.5px] leading-relaxed text-muted-foreground">
                    Нет ни одного эксперимента для комбинации «{gap.material} ×{" "}
                    {gap.property} × {regimeLabel[gap.regime]}».
                  </div>
                </button>
              )
            })}

            {visibleGaps.length > MAX_CARDS && (
              <p className="flex-none pt-1 text-center text-[11.5px] text-muted-foreground">
                Показаны {MAX_CARDS} из {visibleGaps.length} пробелов (по
                убыванию изученности материала).
              </p>
            )}
          </div>
        </div>

        {/* детали */}
        <div className="hidden min-h-0 flex-col gap-3 overflow-y-auto border-l bg-card p-5 lg:flex">
          {!selected ? (
            <p className="text-sm text-muted-foreground">
              Выберите пробел, чтобы увидеть детали.
            </p>
          ) : (
            <GapDetail
              title={selected.title}
              material={selected.material}
              property={selected.property}
              regime={regimeLabel[selected.regime]}
              nearData={nearData}
              onAskAgent={() =>
                askAgent.mutate(
                  `Что известно про «${selected.material}» — свойство «${selected.property}» при режиме «${regimeLabel[selected.regime]}»? Каких данных не хватает?`,
                )
              }
              asking={askAgent.isPending}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function FilterTab({
  label,
  active,
  stub,
  onClick,
}: {
  label: string
  active: boolean
  stub?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
        active
          ? "border-foreground bg-foreground text-background"
          : "border-border bg-card text-foreground hover:bg-muted",
      )}
    >
      {label}
      {stub && <StubMark className="text-[10px]" />}
    </button>
  )
}

function GapDetail({
  title,
  material,
  property,
  regime,
  nearData,
  onAskAgent,
  asking,
}: {
  title: string
  material: string
  property: string
  regime: string
  nearData: string[]
  onAskAgent: () => void
  asking: boolean
}) {
  return (
    <>
      <div>
        <Badge variant="contradiction">Нет данных</Badge>
        <div className="pt-2 text-[15px] leading-snug font-bold">{title}</div>
      </div>

      <Section label="Почему это пробел">
        В графе нет ни одного эксперимента для комбинации «{material} ×{" "}
        {property} × {regime}». Утверждения по этой комбинации нельзя подкрепить
        измерениями.
      </Section>

      <div className="rounded-[10px] border p-3.5">
        <div className="pb-1 text-[10.5px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
          Ближайшие данные в графе
        </div>
        {nearData.length === 0 ? (
          <p className="pt-1.5 text-[12.5px] text-muted-foreground">
            По материалу «{material}» экспериментов пока нет.
          </p>
        ) : (
          nearData.map((n) => (
            <div
              key={n}
              className="flex gap-2 pt-1.5 text-[12.5px] leading-relaxed text-foreground"
            >
              <span className="shrink-0 text-primary">→</span>
              <span>{n}</span>
            </div>
          ))
        )}
      </div>

      <div className="rounded-[10px] border bg-muted/40 p-3.5">
        <div className="pb-1 text-[10.5px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
          Похожий кейс из смежной области
        </div>
        <p className="text-[12.5px] text-muted-foreground">
          Рекомендация переносимой методики <StubMark /> — требует детектора
          аналогий по графу.
        </p>
      </div>

      <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
        <span className="grid size-5 shrink-0 place-items-center rounded-full bg-primary/15 text-[9px] font-bold text-primary">
          @
        </span>
        Профильный эксперт <StubMark />
      </div>

      <Button className="w-full" asChild>
        <Link to="/reports">Заказать обзор по этой теме</Link>
      </Button>
      <div className="flex gap-2">
        <Button
          variant="outline"
          className="flex-1"
          onClick={onAskAgent}
          disabled={asking}
        >
          {asking && <Loader2 className="size-4 animate-spin" />}
          Спросить агента
        </Button>
        <Button variant="outline" className="flex-1" disabled>
          Программа испытаний <StubMark />
        </Button>
      </div>
      <button
        type="button"
        disabled
        className="flex items-center justify-center gap-2 rounded-[9px] border border-dashed px-3 py-2 text-xs font-medium text-muted-foreground"
      >
        <Bell className="size-3.5" /> Следить за темой <StubMark />
      </button>
    </>
  )
}

function Section({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-[10px] border p-3.5">
      <div className="pb-1.5 text-[10.5px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
        {label}
      </div>
      <div className="text-[12.5px] leading-relaxed text-foreground">
        {children}
      </div>
    </div>
  )
}
