import { createFileRoute, Link } from "@tanstack/react-router"
import { ArrowUp, Download, Plus } from "lucide-react"

import { PageHeader } from "@/components/Common/PageHeader"
import { StubBanner, StubMark } from "@/components/Common/StubMark"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

export const Route = createFileRoute("/_layout/reports")({
  component: ReportsPage,
  head: () => ({ meta: [{ title: "Отчёты — MetalCrow" }] }),
})

// Раздел «Отчёты» (литобзор/метаанализ) — макет по дизайну. Бэкенда генерации
// обзоров, refine-цикла и экспорта ещё нет, поэтому весь контент —
// демонстрационный и помечен заглушками.

const toc = [
  "Сводка",
  "Схемы циркуляции",
  "Сравнение схем",
  "Зоны разногласий",
  "Пробелы",
  "Источники и эксперты",
]

const verification: [string, string, string?][] = [
  ["Источников", "14"],
  ["— внутренних", "6"],
  ["— внешних", "8", "external"],
  ["Консенсус", "4 / 6", "high"],
  ["Противоречий", "1", "contra"],
  ["Пробелов", "2", "medium"],
  ["Актуализация", "06.2026"],
]

function Sup({ children }: { children: React.ReactNode }) {
  return (
    <sup className="font-sans text-[11px] font-semibold text-confidence-high-fg">
      {children}
    </sup>
  )
}

function ReportsPage() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={
          <span className="flex items-center gap-2 text-muted-foreground">
            Отчёты /{" "}
            <b className="text-foreground">
              Циркуляция католита — литературный обзор
            </b>
            <Badge variant="neutral" className="ml-1">
              черновик · v3
            </Badge>
            <Badge variant="external">внешние источники: вкл</Badge>
          </span>
        }
        actions={
          <>
            <Button variant="outline" size="sm" disabled>
              <Download className="size-3.5" />
              Экспорт: PDF · MD · JSON-LD <StubMark />
            </Button>
            <Button variant="outline" size="sm" disabled>
              <Plus className="size-3.5" />
              Новый обзор <StubMark />
            </Button>
          </>
        }
      />

      <div className="shrink-0 px-4 pt-3 md:px-6">
        <StubBanner>
          Раздел обзоров — макет по дизайну. Генерация литобзора из сессии,
          дополнение обзора уточняющими запросами и экспорт (PDF / Markdown /
          JSON-LD) ещё не реализованы в бэкенде. Содержимое ниже —
          демонстрационное.
        </StubBanner>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[250px_1fr]">
        {/* оглавление + верификация */}
        <aside className="hidden min-h-0 flex-col gap-4 overflow-y-auto border-r bg-card p-5 lg:flex">
          <div>
            <div className="pb-2.5 text-[10.5px] font-bold tracking-[0.08em] text-muted-foreground uppercase">
              Структура
            </div>
            <div className="flex flex-col gap-0.5 text-[12.5px]">
              {toc.map((item, i) => (
                <div key={item} className={cnToc(i === 0)}>
                  <span className="text-muted-foreground/60">
                    {i < 5 ? i + 1 : "·"}
                  </span>
                  {item}
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[10px] border p-3">
            <div className="flex items-center gap-1 pb-2 text-[10.5px] font-bold tracking-[0.08em] text-muted-foreground uppercase">
              Верификация <StubMark />
            </div>
            <div className="flex flex-col gap-1.5 text-[12px]">
              {verification.map(([label, value, tone]) => (
                <div key={label} className="flex justify-between">
                  <span className="text-muted-foreground">{label}</span>
                  <b
                    className={
                      tone === "external"
                        ? "font-mono text-external-fg"
                        : tone === "high"
                          ? "font-mono text-confidence-high-fg"
                          : tone === "contra"
                            ? "font-mono text-contradiction-fg"
                            : tone === "medium"
                              ? "font-mono text-confidence-medium-fg"
                              : "font-mono"
                    }
                  >
                    {value}
                  </b>
                </div>
              ))}
            </div>
          </div>

          <div className="text-[12px] leading-relaxed text-muted-foreground">
            История версий:
            <br />
            <b className="text-foreground">v3</b> — внешние источники (8)
            <br />
            v2 — таблица сравнения схем
            <br />
            v1 — из сессии чата
          </div>

          <Button variant="outline" size="sm" className="w-full" asChild>
            <Link to="/chat">← Открыть исходную сессию</Link>
          </Button>
        </aside>

        {/* документ + командная строка */}
        <div className="flex min-h-0 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <article className="mx-auto w-full max-w-[760px] px-6 py-8 md:px-8">
              <div className="pb-2.5 text-[11px] font-bold tracking-[0.1em] text-muted-foreground uppercase">
                Литературный обзор · граф знаний + внешние источники
              </div>
              <h1 className="font-serif text-[28px] leading-tight font-bold tracking-tight text-foreground">
                Циркуляция католита при электроэкстракции никеля: мировая
                практика и оптимальные режимы
              </h1>
              <div className="flex flex-wrap gap-2 py-4">
                {["электроэкстракция", "католит", "RU + мировая практика"].map(
                  (t) => (
                    <Badge key={t} variant="neutral">
                      {t}
                    </Badge>
                  ),
                )}
                {["OpenAlex", "Espacenet"].map((t) => (
                  <Badge key={t} variant="external">
                    {t}
                  </Badge>
                ))}
              </div>

              <div className="font-serif text-[15.5px] leading-[1.75] text-foreground">
                <p className="mb-3.5">
                  В мировой практике описаны три принципиальные схемы
                  организации циркуляции католита:{" "}
                  <b>донная подача через перфорированный коллектор</b>
                  <Sup>[1,3]</Sup>, <b>боковая каскадная подача</b>
                  <Sup>[2]</Sup> и{" "}
                  <b>комбинированная схема с диафрагменной ячейкой</b>
                  <Sup>[1,4]</Sup>. Консенсус четырёх из шести профильных
                  источников: оптимальная скорость потока —{" "}
                  <b>20–35 л/мин на ванну</b>.
                </p>
              </div>

              {/* консенсус */}
              <details
                open
                className="my-5 overflow-hidden rounded-xl border border-confidence-high/30 bg-confidence-high-bg/40"
              >
                <summary className="flex list-none items-center gap-3 px-4 py-3">
                  <span className="grid size-10 shrink-0 place-items-center rounded-[9px] bg-primary font-mono text-[13px] font-bold text-primary-foreground">
                    4/6
                  </span>
                  <span className="text-[13.5px] leading-snug text-foreground">
                    <b>Консенсусный вывод.</b> Оптимальная скорость циркуляции —
                    20–35 л/мин на ванну; подтверждено экспериментами EXP-0142,
                    EXP-0187.
                  </span>
                </summary>
                <div className="flex flex-col gap-2 px-4 pt-0 pb-3.5 pl-[68px]">
                  <div className="text-[12.5px] leading-relaxed text-muted-foreground">
                    «При скорости циркуляции 25–30 л/мин достигается равномерное
                    распределение ионов Ni²⁺…» —{" "}
                    <b>Гипроникель, Г-2019-114, стр. 47</b>
                  </div>
                </div>
              </details>

              {/* таблица сравнения */}
              <div className="my-5 overflow-hidden rounded-xl border bg-card">
                <div className="px-4 pt-3 pb-2.5 text-[10.5px] font-bold tracking-[0.08em] text-muted-foreground uppercase">
                  Табл. 1 — Сравнение схем подачи католита
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[420px] text-[12.5px]">
                    <thead>
                      <tr className="border-t text-left">
                        <th className="px-4 py-2 font-medium" />
                        <th className="px-2.5 py-2 font-semibold">Донная</th>
                        <th className="px-2.5 py-2 font-semibold">Каскадная</th>
                        <th className="px-2.5 py-2 font-semibold">Комбинир.</th>
                      </tr>
                    </thead>
                    <tbody className="[&_td]:border-t [&_td]:px-2.5 [&_td]:py-2 [&_th]:border-t [&_th]:px-4 [&_th]:py-2">
                      <tr>
                        <th className="text-left font-normal text-muted-foreground">
                          Снижение расслоения
                        </th>
                        <td className="font-mono font-semibold text-confidence-high-fg">
                          15–20 %
                        </td>
                        <td className="font-mono">5–8 %</td>
                        <td className="font-mono">10–12 %</td>
                      </tr>
                      <tr>
                        <th className="text-left font-normal text-muted-foreground">
                          CAPEX
                        </th>
                        <td>высокий</td>
                        <td className="font-semibold text-confidence-high-fg">
                          низкий
                        </td>
                        <td>средний</td>
                      </tr>
                      <tr>
                        <th className="text-left font-normal text-muted-foreground">
                          TRL
                        </th>
                        <td className="font-mono">9</td>
                        <td className="font-mono">9</td>
                        <td className="font-mono">7</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              {/* разногласия */}
              <div className="my-3.5 flex gap-3 rounded-xl border border-contradiction/30 bg-contradiction-bg px-4 py-3.5">
                <span className="grid size-10 shrink-0 place-items-center rounded-[9px] bg-contradiction font-serif text-[15px] font-bold text-white">
                  ≠
                </span>
                <div className="text-[13px] leading-relaxed text-contradiction-fg">
                  <b>Зона разногласий.</b> Hydrometallurgy (2020) сообщает рост
                  выхода по току на 2–3 % при 70 °C; внутренний отчёт Г-2021-032
                  фиксирует деградацию диафрагмы выше 65 °C. Требуется
                  верифицирующий эксперимент.{" "}
                  <b>
                    Отправить эксперту → <StubMark />
                  </b>
                </div>
              </div>

              {/* пробелы */}
              <Link
                to="/gaps"
                className="my-3.5 flex gap-3 rounded-xl border border-confidence-medium/30 bg-confidence-medium-bg px-4 py-3.5 hover:brightness-[0.98]"
              >
                <span className="grid size-10 shrink-0 place-items-center rounded-[9px] bg-confidence-medium font-serif text-[15px] font-bold text-white">
                  ?
                </span>
                <div className="text-[13px] leading-relaxed text-confidence-medium-fg">
                  <b>Пробелы в знаниях.</b> Не найдено экспериментов для
                  «скорость &gt; 40 л/мин × T &lt; 50 °C»; эффект донной подачи
                  не верифицирован за рубежом. <b>Открыть в карте пробелов →</b>
                </div>
              </Link>

              {/* источники */}
              <div className="mt-4 border-t pt-4">
                <div className="pb-2.5 text-[10.5px] font-bold tracking-[0.08em] text-muted-foreground uppercase">
                  Источники · 6 внутренних + 8 внешних
                </div>
                <div className="grid gap-1.5 text-[12.5px] leading-snug text-muted-foreground sm:grid-cols-2">
                  {[
                    ["[1]", "Гипроникель, отчёт Г-2019-114, 2019", "внутр."],
                    [
                      "[2]",
                      "Nickel Institute, Tankhouse survey, 2020",
                      "внешн.",
                    ],
                    ["[3]", "Патент RU 2 645 987, 2018", "внутр."],
                    ["[4]", "J. Appl. Electrochem., Vol. 51, 2021", "внешн."],
                  ].map(([n, text, kind]) => (
                    <div key={n}>
                      <b className="text-confidence-high-fg">{n}</b> {text}{" "}
                      <Badge
                        variant={
                          kind === "внешн." ? "external" : "confidenceHigh"
                        }
                        className="text-[10px]"
                      >
                        {kind}
                      </Badge>
                    </div>
                  ))}
                </div>
              </div>
            </article>
          </div>

          {/* командная строка (заглушка) */}
          <div className="shrink-0 border-t bg-card px-4 py-3 md:px-6">
            <div className="mx-auto flex w-full max-w-[760px] flex-col gap-2">
              <div className="flex flex-wrap justify-center gap-2">
                {[
                  "Добавить экономические показатели",
                  "Сравнить с зарубежными tankhouse",
                  "Раздел по материалам диафрагм",
                ].map((c) => (
                  <span
                    key={c}
                    className="rounded-full border px-3 py-1 text-xs text-muted-foreground"
                  >
                    {c}
                  </span>
                ))}
              </div>
              <div className="flex items-center gap-2.5 rounded-xl border bg-background py-2 pr-2 pl-3.5 opacity-70 shadow-sm">
                <input
                  disabled
                  placeholder="Уточните обзор: «добавь экономические показатели»…"
                  className="flex-1 border-none bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                />
                <StubMark />
                <span className="grid size-9 shrink-0 place-items-center rounded-[9px] bg-primary text-primary-foreground">
                  <ArrowUp className="size-4" />
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function cnToc(active: boolean): string {
  return active
    ? "flex items-center gap-2.5 rounded-md bg-confidence-high-bg px-2.5 py-1.5 font-semibold text-confidence-high-fg"
    : "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-muted-foreground"
}
