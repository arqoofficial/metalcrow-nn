import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Loader2, Search as SearchIcon } from "lucide-react"
import { type ReactNode, useState } from "react"

import { SearchService } from "@/client"
import { PageHeader } from "@/components/Common/PageHeader"
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

// Тип результата → бейдж. Ключи = kind из онтологии (search_passages).
const KIND_LABELS: Record<string, string> = {
  measurement: "ИЗМЕРЕНИЕ",
  finding: "ВЫВОД",
  recommendation: "РЕКОМЕНДАЦИЯ",
  chunk: "ФРАГМЕНТ",
}

const KIND_FILTERS: { value: string; label: string }[] = [
  { value: "measurement", label: "Измерения" },
  { value: "finding", label: "Выводы" },
  { value: "recommendation", label: "Рекомендации" },
  { value: "chunk", label: "Фрагменты текста" },
  { value: "document", label: "Документы" },
]

// параметры числового условия: id = канонические имена величин онтологии,
// подпись — отображаемая единица (temperature вводится в °C, бэкенд→K)
const NUMERIC_PARAMS: { value: string; label: string }[] = [
  { value: "temperature", label: "Температура, °C" },
  { value: "recovery_degree", label: "Степень извлечения, %" },
  { value: "concentration", label: "Концентрация, г/л" },
  { value: "current_density", label: "Плотность тока, А/м²" },
  { value: "particle_size", label: "Крупность частиц, мкм" },
]

function SearchPage() {
  const [query, setQuery] = useState("")
  const [submitted, setSubmitted] = useState("")
  const [kinds, setKinds] = useState<string[]>([])
  const [yearFrom, setYearFrom] = useState("")
  const [yearTo, setYearTo] = useState("")
  const [geoRu, setGeoRu] = useState(false)
  const [geoEn, setGeoEn] = useState(false)
  const [numQuantity, setNumQuantity] = useState("")
  const [numFrom, setNumFrom] = useState("")
  const [numTo, setNumTo] = useState("")

  // обе галки или ни одной = без фильтра географии
  const geo = geoRu !== geoEn ? (geoRu ? "ru" : "en") : null
  const numericActive = Boolean(numQuantity && (numFrom || numTo))
  // поиск активен по тексту ИЛИ по числовому условию (без текста)
  const active = submitted.trim().length > 0 || numericActive

  const { data, isFetching, isError } = useQuery({
    queryKey: [
      "corpus-search",
      submitted,
      kinds,
      yearFrom,
      yearTo,
      geo,
      numQuantity,
      numFrom,
      numTo,
    ],
    queryFn: () =>
      SearchService.corpusSearch({
        requestBody: {
          query: submitted,
          limit: 20,
          kinds: kinds.length ? kinds : null,
          year_from: yearFrom ? Number(yearFrom) : null,
          year_to: yearTo ? Number(yearTo) : null,
          geo,
          numeric: numericActive
            ? {
                quantity: numQuantity,
                value_from: numFrom ? Number(numFrom) : null,
                value_to: numTo ? Number(numTo) : null,
              }
            : null,
        },
      }),
    enabled: active,
  })

  const passages = data?.passages ?? []
  const documents = data?.documents ?? []
  const entities = data?.entities
  const expanded = data?.expanded_terms ?? []
  const hasEntities = Boolean(
    entities?.process ||
      entities?.quantity_kind ||
      (entities?.materials?.length ?? 0) > 0,
  )

  const toggleKind = (value: string) =>
    setKinds((prev) =>
      prev.includes(value) ? prev.filter((k) => k !== value) : [...prev, value],
    )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="Поиск по базе знаний" />

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[256px_1fr]">
        {/* фильтры */}
        <aside className="hidden min-h-0 flex-col gap-5 overflow-y-auto border-r bg-card p-5 lg:flex">
          <FilterGroup title="Тип результата">
            {KIND_FILTERS.map(({ value, label }) => (
              <label
                key={value}
                className="flex cursor-pointer items-center gap-2 text-[13px]"
              >
                <input
                  type="checkbox"
                  checked={kinds.includes(value)}
                  onChange={() => toggleKind(value)}
                  className="size-3.5 accent-primary"
                />
                {label}
              </label>
            ))}
            <p className="pt-1 text-[11px] text-muted-foreground">
              Ничего не выбрано — показываются все типы.
            </p>
          </FilterGroup>

          <FilterGroup title="Годы">
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={yearFrom}
                onChange={(e) => setYearFrom(e.target.value)}
                placeholder="от"
                className="w-[74px] rounded-md border bg-background px-2 py-1.5 text-[12.5px]"
              />
              <span className="text-muted-foreground">—</span>
              <input
                type="number"
                value={yearTo}
                onChange={(e) => setYearTo(e.target.value)}
                placeholder="до"
                className="w-[74px] rounded-md border bg-background px-2 py-1.5 text-[12.5px]"
              />
            </div>
            <p className="pt-1 text-[11px] text-muted-foreground">
              Результаты без года не скрываются.
            </p>
          </FilterGroup>

          <FilterGroup title="География / язык">
            <label className="flex cursor-pointer items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={geoRu}
                onChange={() => setGeoRu((v) => !v)}
                className="size-3.5 accent-primary"
              />
              RU — отечественные
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={geoEn}
                onChange={() => setGeoEn((v) => !v)}
                className="size-3.5 accent-primary"
              />
              EN — мировые
            </label>
            <p className="pt-1 text-[11px] text-muted-foreground">
              Источники без метаданных языка не скрываются.
            </p>
          </FilterGroup>

          <FilterGroup title="Числовое условие">
            <select
              value={numQuantity}
              onChange={(e) => setNumQuantity(e.target.value)}
              className="w-full rounded-md border bg-background px-2 py-1.5 text-[12.5px]"
            >
              <option value="">— параметр не задан —</option>
              {NUMERIC_PARAMS.map(({ value, label }) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <div className="flex items-center gap-2 pt-1">
              <input
                type="number"
                value={numFrom}
                onChange={(e) => setNumFrom(e.target.value)}
                placeholder="от"
                disabled={!numQuantity}
                className="w-[74px] rounded-md border bg-background px-2 py-1.5 text-[12.5px] disabled:opacity-50"
              />
              <span className="text-muted-foreground">—</span>
              <input
                type="number"
                value={numTo}
                onChange={(e) => setNumTo(e.target.value)}
                placeholder="до"
                disabled={!numQuantity}
                className="w-[74px] rounded-md border bg-background px-2 py-1.5 text-[12.5px] disabled:opacity-50"
              />
            </div>
            <p className="pt-1 text-[11px] text-muted-foreground">
              Ищет измерения величины в диапазоне; текст запроса сужает выдачу.
              Работает и без текста.
            </p>
          </FilterGroup>
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
                placeholder="Поиск по выводам, измерениям и документам корпуса…"
                className="flex-1 border-none bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
              <kbd className="shrink-0 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                ⏎
              </kbd>
            </form>

            {/* распознанные сущности — канон из реестров онтологии */}
            {submitted.trim() && !isFetching && (
              <div className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
                <span className="text-muted-foreground">
                  Распознанные сущности:
                </span>
                {hasEntities ? (
                  <>
                    {entities?.process && (
                      <Badge variant="neutral" className="text-[10.5px]">
                        процесс · {entities.process}
                      </Badge>
                    )}
                    {entities?.quantity_kind && (
                      <Badge variant="neutral" className="text-[10.5px]">
                        величина · {entities.quantity_kind}
                      </Badge>
                    )}
                    {(entities?.materials ?? []).map((m) => (
                      <Badge
                        key={m}
                        variant="neutral"
                        className="text-[10.5px]"
                      >
                        материал · {m}
                      </Badge>
                    ))}
                  </>
                ) : (
                  <span className="text-muted-foreground">
                    не распознаны в реестрах онтологии
                  </span>
                )}
                {expanded.length > 0 && (
                  <span className="text-muted-foreground">
                    · синонимы: {expanded.slice(0, 5).join(", ")}
                  </span>
                )}
              </div>
            )}

            {active && (
              <div className="text-[12.5px] text-muted-foreground">
                {isFetching ? (
                  <span className="flex items-center gap-2">
                    <Loader2 className="size-3.5 animate-spin" /> Поиск…
                  </span>
                ) : (
                  <>
                    Найдено{" "}
                    <b className="text-foreground">
                      {countRu(passages.length, [
                        "фрагмент",
                        "фрагмента",
                        "фрагментов",
                      ])}
                    </b>
                    {" · "}
                    <b className="text-foreground">
                      {countRu(documents.length, [
                        "документ",
                        "документа",
                        "документов",
                      ])}
                    </b>
                  </>
                )}
              </div>
            )}

            {!active && (
              <p className="pt-6 text-center text-sm text-muted-foreground">
                Введите запрос — поиск по выводам и измерениям онтологии (с
                источником и цитатой) и по обработанным документам корпуса. Либо
                задайте числовое условие в панели слева.
              </p>
            )}

            {active && !isFetching && isError && (
              <p className="pt-6 text-center text-sm text-muted-foreground">
                Поиск временно недоступен — попробуйте повторить запрос.
              </p>
            )}

            {active &&
              !isFetching &&
              !isError &&
              passages.length === 0 &&
              documents.length === 0 && (
                <p className="pt-6 text-center text-sm text-muted-foreground">
                  {data?.note ??
                    (submitted.trim()
                      ? `Ничего не найдено по запросу «${submitted}».`
                      : "Ничего не найдено по заданному условию.")}
                </p>
              )}

            {passages.map((p, i) => (
              <div
                key={`${p.doc}-${i}`}
                className="flex flex-col gap-2 rounded-xl border bg-card p-4 md:px-[18px]"
              >
                <div className="flex items-center gap-2">
                  <Badge variant="neutral" className="text-[10.5px]">
                    {KIND_LABELS[p.kind] ?? p.kind.toUpperCase()}
                  </Badge>
                  <span className="truncate text-sm font-semibold text-foreground">
                    {p.doc}
                  </span>
                  {p.year && (
                    <span className="shrink-0 text-[11.5px] text-muted-foreground">
                      {p.year}
                    </span>
                  )}
                  {p.country && (
                    <span className="shrink-0 text-[11.5px] text-muted-foreground">
                      {p.country}
                    </span>
                  )}
                </div>
                {p.kind === "measurement" && p.text && (
                  <div className="text-[13px] font-medium">
                    {highlightNumbers(p.text)}
                  </div>
                )}
                {p.snippet && (
                  <div className="text-[12.5px] leading-relaxed text-muted-foreground">
                    …{highlightNumbers(p.snippet)}…
                  </div>
                )}
                {p.kind !== "measurement" && !p.snippet && p.text && (
                  <div className="text-[12.5px] leading-relaxed text-muted-foreground">
                    {highlightNumbers(p.text)}
                  </div>
                )}
                <div className="flex items-center gap-3 text-[11.5px] text-muted-foreground">
                  {p.locator && <span className="font-mono">{p.locator}</span>}
                  {p.okf_path && (
                    <Link
                      to="/wiki"
                      search={{ doc: p.okf_path }}
                      className="ml-auto shrink-0 font-medium text-primary hover:underline"
                    >
                      Открыть источник
                    </Link>
                  )}
                </div>
              </div>
            ))}

            {documents.length > 0 && (
              <div className="pt-2 text-[11px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
                Документы корпуса
              </div>
            )}
            {documents.map((r) => (
              <div
                key={r.okf_path}
                className="flex flex-col gap-2 rounded-xl border bg-card p-4 md:px-[18px]"
              >
                <div className="flex items-center gap-2">
                  <Badge variant="neutral" className="text-[10.5px]">
                    ДОКУМЕНТ
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
