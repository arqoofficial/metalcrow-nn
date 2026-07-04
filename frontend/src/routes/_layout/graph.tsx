import { useMutation } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Loader2 } from "lucide-react"
import { useMemo, useState } from "react"

import {
  ApiError,
  type GraphNode,
  GraphService,
  type SubgraphResponse,
} from "@/client"
import { PageHeader } from "@/components/Common/PageHeader"
import { StubMark } from "@/components/Common/StubMark"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/graph")({
  component: GraphPage,
  head: () => ({ meta: [{ title: "Граф — MetalCrow" }] }),
})

type Mode = "subgraph" | "template" | "path"

// Тип узла → цвет (материалы teal, процессы серый, свойства янтарный,
// оборудование фиолетовый). Метки типов из бэкенда произвольные — матчим по
// ключевым словам.
type Kind = "material" | "process" | "property" | "equipment" | "other"

function nodeKind(type: string | undefined): Kind {
  const t = (type ?? "").toLowerCase()
  if (/(material|материал|вещество|электролит|раствор|catholyte)/.test(t))
    return "material"
  if (/(process|процесс|операц|метод|реакц)/.test(t)) return "process"
  if (/(propert|свойств|параметр|режим|param|value)/.test(t)) return "property"
  if (/(equip|оборудов|ванна|апарат|аппарат|печь|устройств)/.test(t))
    return "equipment"
  return "other"
}

const KIND_STYLE: Record<
  Kind,
  { fill: string; stroke: string; text: string; label: string }
> = {
  material: {
    fill: "var(--confidence-high-bg)",
    stroke: "var(--confidence-high)",
    text: "var(--confidence-high-fg)",
    label: "Материалы",
  },
  process: {
    fill: "var(--muted)",
    stroke: "var(--muted-foreground)",
    text: "var(--foreground)",
    label: "Процессы",
  },
  property: {
    fill: "var(--confidence-medium-bg)",
    stroke: "var(--confidence-medium)",
    text: "var(--confidence-medium-fg)",
    label: "Свойства",
  },
  equipment: {
    fill: "var(--equipment-bg)",
    stroke: "var(--equipment)",
    text: "var(--equipment-fg)",
    label: "Оборудование",
  },
  other: {
    fill: "var(--card)",
    stroke: "var(--border)",
    text: "var(--foreground)",
    label: "Прочее",
  },
}

const legendKinds: Kind[] = ["material", "process", "property", "equipment"]

function truncate(text: string, n: number): string {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text
}

function GraphCanvas({
  data,
  selectedId,
  onSelect,
}: {
  data: SubgraphResponse
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const nodes = data.nodes ?? []
  const edges = data.edges ?? []

  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>()
    const cx = 450
    const cy = 310
    if (nodes.length === 1) {
      map.set(nodes[0].id, { x: cx, y: cy })
    } else {
      const r = Math.min(250, 90 + nodes.length * 8)
      nodes.forEach((n, i) => {
        const a = (2 * Math.PI * i) / nodes.length - Math.PI / 2
        map.set(n.id, { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) })
      })
    }
    return map
  }, [nodes])

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
        Граф пуст. Загрузите подграф по сущности, шаблону или найдите путь —
        узлы появятся здесь.
      </div>
    )
  }

  return (
    <svg
      viewBox="0 0 900 620"
      className="h-full w-full"
      role="img"
      aria-label="Визуализация подграфа"
    >
      <title>Подграф</title>
      {edges.map((e, i) => {
        const a = positions.get(e.source)
        const b = positions.get(e.target)
        if (!a || !b) return null
        return (
          <g key={i}>
            <line
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke="var(--border)"
              strokeWidth={1.5}
            />
            {e.type && (
              <text
                x={(a.x + b.x) / 2}
                y={(a.y + b.y) / 2 - 4}
                textAnchor="middle"
                fill="var(--muted-foreground)"
                fontSize={9}
              >
                {truncate(e.type, 16)}
              </text>
            )}
          </g>
        )
      })}
      {nodes.map((n) => {
        const p = positions.get(n.id)
        if (!p) return null
        const style = KIND_STYLE[nodeKind(n.type)]
        const isSel = selectedId === n.id
        return (
          // biome-ignore lint/a11y/useSemanticElements: SVG <g> node cannot be a native <button>
          <g
            key={n.id}
            role="button"
            tabIndex={0}
            aria-label={n.label || n.id}
            className="cursor-pointer focus:outline-none"
            onClick={() => onSelect(n.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") onSelect(n.id)
            }}
          >
            <circle
              cx={p.x}
              cy={p.y}
              r={38}
              fill={style.fill}
              stroke={isSel ? "var(--foreground)" : style.stroke}
              strokeWidth={isSel ? 3 : 1.5}
            />
            <text
              x={p.x}
              y={p.y - 2}
              textAnchor="middle"
              fill={style.text}
              fontSize={10.5}
              fontWeight={600}
            >
              {truncate(n.label || n.id, 14)}
            </text>
            <text
              x={p.x}
              y={p.y + 11}
              textAnchor="middle"
              fill="var(--muted-foreground)"
              fontSize={8.5}
            >
              {truncate(n.type || "", 16)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

function Inspector({ node }: { node: GraphNode | null }) {
  if (!node) {
    return (
      <p className="text-sm text-muted-foreground">
        Нажмите на узел, чтобы посмотреть детали.
      </p>
    )
  }
  const props = Object.entries(node.properties ?? {}).filter(
    ([k]) => k !== "id" && k !== "label",
  )
  return (
    <>
      <div>
        <div className="text-[17px] font-bold">{node.label || node.id}</div>
        <div className="pt-1">
          <Badge variant="neutral">{node.type || "узел"}</Badge>
        </div>
      </div>

      <div className="overflow-hidden rounded-[10px] border">
        <div className="bg-muted/50 px-3.5 py-2 text-[10.5px] font-bold tracking-[0.07em] text-muted-foreground uppercase">
          Свойства
        </div>
        {props.length === 0 ? (
          <p className="px-3.5 py-2.5 text-[12.5px] text-muted-foreground">
            У узла нет дополнительных свойств.
          </p>
        ) : (
          props.map(([k, v]) => (
            <div
              key={k}
              className="flex justify-between gap-3 border-t px-3.5 py-2 text-[12.5px]"
            >
              <span className="text-muted-foreground">{k}</span>
              <span className="text-right font-mono font-medium">
                {truncate(String(v), 28)}
              </span>
            </div>
          ))
        )}
      </div>

      <div className="flex gap-2 text-center text-[12px]">
        <div className="flex-1 rounded-[10px] border p-2.5">
          <div className="text-lg font-bold">
            <StubMark />
          </div>
          <div className="text-[11px] text-muted-foreground">документов</div>
        </div>
        <div className="flex-1 rounded-[10px] border p-2.5">
          <div className="text-lg font-bold">
            <StubMark />
          </div>
          <div className="text-[11px] text-muted-foreground">экспериментов</div>
        </div>
      </div>

      <Button className="w-full" asChild>
        <Link to="/wiki" search={{ doc: undefined }}>
          Открыть страницу в Вики
        </Link>
      </Button>
      <Button variant="outline" className="w-full" asChild>
        <Link to="/search">Документы в поиске</Link>
      </Button>
    </>
  )
}

function GraphPage() {
  const [mode, setMode] = useState<Mode>("subgraph")
  const [result, setResult] = useState<SubgraphResponse | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // subgraph
  const [entityId, setEntityId] = useState("")
  const [depth, setDepth] = useState("1")
  // template
  const [templateId, setTemplateId] = useState("")
  const [paramsText, setParamsText] = useState("{}")
  const [paramsError, setParamsError] = useState<string | null>(null)
  // path
  const [from, setFrom] = useState("")
  const [to, setTo] = useState("")
  const [maxDepth, setMaxDepth] = useState("4")

  const onResult = (data: SubgraphResponse) => {
    setResult(data)
    setSelectedId(data.nodes?.[0]?.id ?? null)
  }

  const subgraph = useMutation({
    mutationFn: () =>
      GraphService.subgraph({ entityId, depth: Number(depth) || undefined }),
    onSuccess: onResult,
  })
  const template = useMutation({
    mutationFn: (params: Record<string, unknown>) =>
      GraphService.query({
        requestBody: {
          template_id: templateId,
          params,
          max_depth: Number(maxDepth) || undefined,
        },
      }),
    onSuccess: onResult,
  })
  const path = useMutation({
    mutationFn: () =>
      GraphService.path({
        _from: from,
        to,
        maxDepth: Number(maxDepth) || undefined,
      }),
    onSuccess: onResult,
  })

  const active =
    mode === "subgraph" ? subgraph : mode === "template" ? template : path
  // только для активного режима «Путь», иначе ошибка подграфа/шаблона была бы
  // ошибочно подписана как «Neo4j 503» из-за оставшейся ошибки прошлого запроса.
  const pathIs503 =
    mode === "path" &&
    path.error instanceof ApiError &&
    path.error.status === 503

  const runTemplate = () => {
    try {
      const params = JSON.parse(paramsText || "{}")
      setParamsError(null)
      template.mutate(params)
    } catch {
      setParamsError("Параметры должны быть корректным JSON")
    }
  }

  const selectedNode = result?.nodes?.find((n) => n.id === selectedId) ?? null

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Граф знаний"
        actions={
          <div className="hidden flex-wrap gap-1.5 text-[11.5px] md:flex">
            {legendKinds.map((k) => (
              <span
                key={k}
                className="flex items-center gap-1.5 rounded-full px-2.5 py-1"
                style={{
                  background: KIND_STYLE[k].fill,
                  color: KIND_STYLE[k].text,
                }}
              >
                <span
                  className="size-1.5 rounded-full"
                  style={{ background: KIND_STYLE[k].stroke }}
                />
                {KIND_STYLE[k].label}
              </span>
            ))}
          </div>
        }
      />

      {/* панель запроса */}
      <div className="flex flex-col gap-2.5 border-b bg-card px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center gap-1.5">
          {(
            [
              ["subgraph", "Подграф по сущности"],
              ["template", "Шаблон Cypher"],
              ["path", "Путь (Neo4j)"],
            ] as [Mode, string][]
          ).map(([m, label]) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={cn(
                "rounded-lg px-3 py-1 text-xs transition-colors",
                mode === m
                  ? "bg-foreground font-semibold text-background"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === "subgraph" && (
          <div className="flex flex-wrap items-center gap-2">
            <Input
              placeholder="entity_id (сущность / эксперимент)"
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              className="max-w-xs"
            />
            <Input
              type="number"
              value={depth}
              onChange={(e) => setDepth(e.target.value)}
              className="w-24"
              placeholder="глубина"
            />
            <Button
              onClick={() => subgraph.mutate()}
              disabled={!entityId || subgraph.isPending}
            >
              {subgraph.isPending && (
                <Loader2 className="size-4 animate-spin" />
              )}
              Построить
            </Button>
            <span className="text-[11.5px] text-muted-foreground">
              SQL-фолбэк вокруг сущности эксперимента
            </span>
          </div>
        )}

        {mode === "template" && (
          <div className="flex flex-wrap items-start gap-2">
            <Input
              placeholder="template_id"
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
              className="max-w-xs"
            />
            <Textarea
              placeholder="params (JSON)"
              value={paramsText}
              onChange={(e) => setParamsText(e.target.value)}
              rows={1}
              className="max-w-xs"
            />
            <Input
              type="number"
              value={maxDepth}
              onChange={(e) => setMaxDepth(e.target.value)}
              className="w-24"
              placeholder="max_depth"
            />
            <Button
              onClick={runTemplate}
              disabled={!templateId || template.isPending}
            >
              {template.isPending && (
                <Loader2 className="size-4 animate-spin" />
              )}
              Выполнить
            </Button>
            <span className="text-[11.5px] text-muted-foreground">
              Только предопределённые Cypher-шаблоны (сырой Cypher запрещён)
            </span>
            {paramsError && (
              <span className="text-xs text-destructive">{paramsError}</span>
            )}
          </div>
        )}

        {mode === "path" && (
          <div className="flex flex-wrap items-center gap-2">
            <Input
              placeholder="от entity_id"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="max-w-[200px]"
            />
            <Input
              placeholder="до entity_id"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="max-w-[200px]"
            />
            <Input
              type="number"
              value={maxDepth}
              onChange={(e) => setMaxDepth(e.target.value)}
              className="w-24"
            />
            <Button
              onClick={() => path.mutate()}
              disabled={!from || !to || path.isPending}
            >
              {path.isPending && <Loader2 className="size-4 animate-spin" />}
              Найти путь
            </Button>
            <span className="text-[11.5px] text-muted-foreground">
              Кратчайший путь в Neo4j (503, если Neo4j недоступен)
            </span>
          </div>
        )}

        {active.isError && (
          <p className="text-xs text-destructive">
            {pathIs503
              ? "Neo4j недоступен (503)."
              : active.error instanceof Error
                ? active.error.message
                : "Запрос не выполнен"}
          </p>
        )}
      </div>

      {/* канвас + инспектор */}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_330px]">
        <div className="relative min-h-[320px] overflow-hidden bg-muted/20">
          {result ? (
            <GraphCanvas
              data={result}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          ) : (
            <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
              Постройте подграф, чтобы увидеть визуализацию. Силовая раскладка
              (d3-force) — <StubMark />; пока узлы размещаются по окружности.
            </div>
          )}
          {result && (
            <div className="absolute bottom-4 left-4 rounded-lg border bg-card px-3 py-1.5 text-[11.5px] text-muted-foreground">
              {result.nodes?.length ?? 0} узлов · {result.edges?.length ?? 0}{" "}
              связей
            </div>
          )}
        </div>
        <div className="flex min-h-0 flex-col gap-3.5 overflow-y-auto border-l bg-card p-5">
          <Inspector node={selectedNode} />
        </div>
      </div>
    </div>
  )
}
