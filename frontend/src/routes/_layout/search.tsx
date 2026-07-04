import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Loader2, Search as SearchIcon } from "lucide-react"
import { type ReactNode, useState } from "react"

import { WikiService } from "@/client"
import { PageHeader } from "@/components/Common/PageHeader"
import { StubBanner, StubMark } from "@/components/Common/StubMark"
import { Badge } from "@/components/ui/badge"
import { countRu } from "@/lib/utils"

export const Route = createFileRoute("/_layout/search")({
  component: SearchPage,
  head: () => ({ meta: [{ title: "Поиск — MetalCrow" }] }),
})

// Подсветка числовых значений в сниппете (числа, диапазоны, единицы).
const NUMBER_RE =
  /(\d[\d.,]*(?:\s?[–—-]\s?\d[\d.,]*)?\s?(?:°C|%|А\/м²|л\/мин|г\/л|мг\/л|мкм|нм|кА|кВт·ч\/т)?)/g

function highlightNumbers(text: string): ReactNode[] {
  const parts = text.split(NUMBER_RE)
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="mc-num">
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}

function SearchPage() {
  const [query, setQuery] = useState("")
  const [submitted, setSubmitted] = useState("")

  const { data, isFetching } = useQuery({
    queryKey: ["doc-search", submitted],
    queryFn: () => WikiService.search({ q: submitted }),
    enabled: submitted.trim().length > 0,
  })

  const results = data?.results ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="Поиск по базе знаний" />

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[256px_1fr]">
        {/* фильтры (заглушка) */}
        <aside className="hidden min-h-0 flex-col gap-5 overflow-y-auto border-r bg-card p-5 lg:flex">
          <StubBanner>
            Фильтры по типу, языку, годам и числовым условиям — макет. Активен
            только полнотекстовый поиск справа.
          </StubBanner>

          <FilterGroup title="Тип источника">
            {["Статьи", "Отчёты", "Патенты", "Эксперименты"].map((t) => (
              <StubCheckbox key={t} label={t} />
            ))}
          </FilterGroup>

          <FilterGroup title="География / язык">
            {["RU — отечественные", "EN — мировые"].map((t) => (
              <StubCheckbox key={t} label={t} />
            ))}
          </FilterGroup>

          <FilterGroup title="Годы">
            <div className="flex items-center gap-2">
              <StubNumber value="2010" />
              <span className="text-muted-foreground">—</span>
              <StubNumber value="2026" />
            </div>
          </FilterGroup>

          <div className="border-t pt-4">
            <div className="flex items-center gap-1 pb-1 text-[11px] font-bold tracking-[0.07em] text-confidence-high-fg uppercase">
              Числовые условия <StubMark />
            </div>
            <p className="pb-3 text-[11.5px] leading-relaxed text-muted-foreground">
              Поиск по значениям параметров в документах и экспериментах.
            </p>
            <div className="flex flex-col gap-3">
              <NumericRange label="Температура, °C" from="40" to="70" />
              <NumericRange label="Скорость потока, л/мин" from="20" to="35" />
              <NumericRange label="Концентрация Ni²⁺, г/л" from="70" to="90" />
            </div>
          </div>
        </aside>

        {/* результаты */}
        <div className="flex min-h-0 min-w-0 flex-col overflow-y-auto">
          <div className="mx-auto flex w-full max-w-[900px] flex-col gap-3 p-5 md:p-7">
            <form
              onSubmit={(e) => {
                e.preventDefault()
                setSubmitted(query)
              }}
              className="flex items-center gap-2.5 rounded-xl border bg-card px-3.5 py-2.5 shadow-sm focus-within:border-ring"
            >
              <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Поиск по обработанным документам…"
                className="flex-1 border-none bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
              <kbd className="shrink-0 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                ⏎
              </kbd>
            </form>

            <div className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
              <span className="text-muted-foreground">
                Распознанные сущности
              </span>
              <StubMark />
              <span className="text-muted-foreground">
                — извлечение материалов/процессов/параметров из запроса ещё не
                подключено.
              </span>
            </div>

            {submitted.trim() && (
              <div className="text-[12.5px] text-muted-foreground">
                {isFetching ? (
                  <span className="flex items-center gap-2">
                    <Loader2 className="size-3.5 animate-spin" /> Поиск…
                  </span>
                ) : (
                  <>
                    Найдено{" "}
                    <b className="text-foreground">
                      {countRu(results.length, [
                        "документ",
                        "документа",
                        "документов",
                      ])}
                    </b>
                    {" · эксперименты "}
                    <StubMark />
                  </>
                )}
              </div>
            )}

            {!submitted.trim() && (
              <p className="pt-6 text-center text-sm text-muted-foreground">
                Введите запрос — полнотекстовый поиск по обработанным
                markdown-документам корпуса.
              </p>
            )}

            {submitted.trim() && !isFetching && results.length === 0 && (
              <p className="pt-6 text-center text-sm text-muted-foreground">
                Ничего не найдено по запросу «{submitted}».
              </p>
            )}

            {results.map((r) => (
              <div
                key={r.okf_path}
                className="flex flex-col gap-2 rounded-xl border bg-card p-4 md:px-[18px]"
              >
                <div className="flex items-center gap-2">
                  <Badge variant="neutral" className="text-[10.5px]">
                    ДОКУМЕНТ <StubMark className="ml-0.5" />
                  </Badge>
                  <span className="text-sm font-semibold text-foreground">
                    {r.title}
                  </span>
                </div>
                {r.snippet && (
                  <div className="text-[12.5px] leading-relaxed text-muted-foreground">
                    …{highlightNumbers(r.snippet)}…
                  </div>
                )}
                <div className="flex items-center gap-3 text-[11.5px] text-muted-foreground">
                  <span className="truncate font-mono">{r.okf_path}</span>
                  <Link
                    to="/wiki"
                    search={{ doc: r.okf_path }}
                    className="ml-auto shrink-0 font-medium text-primary hover:underline"
                  >
                    Открыть
                  </Link>
                  <Link
                    to="/graph"
                    className="shrink-0 font-medium text-primary hover:underline"
                  >
                    В граф →
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function FilterGroup({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <div>
      <div className="pb-2.5 text-[11px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
        {title}
      </div>
      <div className="flex flex-col gap-1.5">{children}</div>
    </div>
  )
}

function StubCheckbox({ label }: { label: string }) {
  return (
    <label className="flex cursor-not-allowed items-center gap-2 text-[13px] text-muted-foreground">
      <input type="checkbox" disabled className="size-3.5 accent-primary" />
      {label}
    </label>
  )
}

function StubNumber({ value }: { value: string }) {
  return (
    <input
      type="text"
      value={value}
      disabled
      readOnly
      className="w-[74px] rounded-md border bg-muted/40 px-2 py-1.5 text-[12.5px] text-muted-foreground"
    />
  )
}

function NumericRange({
  label,
  from,
  to,
}: {
  label: string
  from: string
  to: string
}) {
  return (
    <div>
      <div className="pb-1.5 text-[12.5px] font-medium text-muted-foreground">
        {label}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={from}
          disabled
          readOnly
          className="w-[74px] rounded-md border border-confidence-high/30 bg-confidence-high-bg/40 px-2 py-1.5 text-[12.5px] text-muted-foreground"
        />
        <span className="text-muted-foreground">—</span>
        <input
          type="text"
          value={to}
          disabled
          readOnly
          className="w-[74px] rounded-md border border-confidence-high/30 bg-confidence-high-bg/40 px-2 py-1.5 text-[12.5px] text-muted-foreground"
        />
      </div>
    </div>
  )
}
