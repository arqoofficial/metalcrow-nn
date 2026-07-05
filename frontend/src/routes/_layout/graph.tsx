import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Loader2 } from "lucide-react"
import { useTheme } from "next-themes"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ForceGraph2D, {
  type ForceGraphMethods,
  type LinkObject,
  type NodeObject,
} from "react-force-graph-2d"

import {
  ApiError,
  type GraphGap,
  GraphService,
  type SubgraphResponse,
} from "@/client"
import { PageHeader } from "@/components/Common/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/graph")({
  component: GraphPage,
  head: () => ({ meta: [{ title: "Граф — MetalCrow" }] }),
})

type Mode = "overview" | "subgraph" | "template" | "path"

// ── Модель графа (клиентская проекция) ─────────────────────────────────────────
type GNode = {
  id: string
  label: string
  type: string
  count: number
  reason?: string
  properties?: Record<string, unknown>
}
type GLink = { source: string; target: string; type: string; gap: boolean }

// Цвета узлов по типу /overview — подобраны под палитру приложения, но заданы
// фиксированными hex, т.к. рисуются на canvas (CSS-переменные там ненадёжны).
const TYPE_COLOR: Record<string, string> = {
  Material: "#14b8a6",
  Process: "#64748b",
  Equipment: "#8b5cf6",
  Result: "#f59e0b",
  Lab: "#06b6d4",
  Expert: "#ec4899",
  Publication: "#a1a1aa",
  Experiment: "#0ea5e9",
  Other: "#94a3b8",
  Gap: "#ef4444",
}
const TYPE_LABEL_RU: Record<string, string> = {
  Material: "Материалы",
  Process: "Процессы",
  Equipment: "Оборудование",
  Result: "Свойства/результаты",
  Lab: "Лаборатории",
  Expert: "Эксперты",
  Publication: "Публикации",
  Experiment: "Эксперименты",
  Other: "Прочее",
  Gap: "Пробелы",
}
const LEGEND_TYPES = [
  "Material",
  "Process",
  "Equipment",
  "Result",
  "Lab",
  "Expert",
  "Publication",
  "Gap",
]
// Fallback-раскраска для произвольных типов из subgraph/template/path (сырые метки
// бэкенда) — матчим по ключевым словам.
function colorForType(type: string): string {
  if (TYPE_COLOR[type]) return TYPE_COLOR[type]
  const t = type.toLowerCase()
  if (/(material|материал|вещество|электролит|раствор)/.test(t))
    return "#14b8a6"
  if (/(equip|оборудов|ванна|аппарат|печь)/.test(t)) return "#8b5cf6"
  if (/(propert|свойств|результат|value|режим)/.test(t)) return "#f59e0b"
  if (/(lab|лаборатор)/.test(t)) return "#06b6d4"
  if (/(research|эксперт|автор|исследоват)/.test(t)) return "#ec4899"
  if (/(process|процесс|операц|метод|реакц)/.test(t)) return "#64748b"
  return "#94a3b8"
}

const nodeRadius = (n: GNode) =>
  n.type === "Gap" ? 3 : 3 + Math.sqrt(Math.max(n.count, 1)) * 1.1
const truncate = (t: string, n: number) =>
  t.length > n ? `${t.slice(0, n - 1)}…` : t
const linkEndId = (
  end: string | number | { id?: string | number } | undefined,
) => (typeof end === "object" && end !== null ? String(end.id) : String(end))

function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (rect) setSize({ width: rect.width, height: rect.height })
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, size] as const
}

// ── Canvas на react-force-graph-2d (вид в стиле Obsidian) ───────────────────────
function GraphCanvas({
  nodes,
  links,
  selectedId,
  onSelect,
}: {
  nodes: GNode[]
  links: GLink[]
  selectedId: string | null
  onSelect: (id: string | null) => void
}) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === "dark"
  const textColor = isDark ? "#e5e7eb" : "#1f2937"
  const dimColor = isDark ? "rgba(148,163,184,0.18)" : "rgba(100,116,139,0.18)"

  const [hoverId, setHoverId] = useState<string | null>(null)
  const focusId = hoverId ?? selectedId

  const graphData = useMemo(
    () => ({
      nodes: nodes.map((n) => ({ ...n })),
      links: links.map((l) => ({ ...l })),
    }),
    [nodes, links],
  )

  const neighbours = useMemo(() => {
    const adj = new Map<string, Set<string>>()
    const add = (a: string, b: string) => {
      const s = adj.get(a) ?? new Set<string>()
      s.add(b)
      adj.set(a, s)
    }
    for (const l of links) {
      add(l.source, l.target)
      add(l.target, l.source)
    }
    return adj
  }, [links])

  const highlight = useMemo(() => {
    if (!focusId) return null
    const set = new Set<string>([focusId])
    for (const id of neighbours.get(focusId) ?? []) set.add(id)
    return set
  }, [focusId, neighbours])

  const fgRef = useRef<
    ForceGraphMethods<NodeObject<GNode>, LinkObject<GNode, GLink>> | undefined
  >(undefined)
  const fittedKey = useRef("")
  const [containerRef, size] = useElementSize<HTMLDivElement>()

  // Раздвигаем узлы: сильнее отталкивание и длиннее рёбра, чтобы плотный граф
  // (особенно GraphRAG) не слипался в ком. Масштабируем отталкивание к размеру
  // графа, иначе большие графы всё равно комкаются.
  useEffect(() => {
    const fg = fgRef.current
    if (!fg || graphData.nodes.length === 0) return
    const strength = -50 - Math.min(graphData.nodes.length, 400)
    const charge = fg.d3Force("charge") as unknown as
      | { strength: (n: number) => unknown }
      | undefined
    charge?.strength(strength)
    const link = fg.d3Force("link") as unknown as
      | { distance: (n: number) => unknown }
      | undefined
    link?.distance(46)
    fg.d3ReheatSimulation()
  }, [graphData])

  const handleEngineStop = useCallback(() => {
    const key = `${nodes.length}:${links.length}`
    if (fittedKey.current !== key) {
      fittedKey.current = key
      fgRef.current?.zoomToFit(400, 60)
    }
  }, [nodes.length, links.length])

  const paintNode = useCallback(
    (node: NodeObject<GNode>, ctx: CanvasRenderingContext2D, scale: number) => {
      const x = node.x ?? 0
      const y = node.y ?? 0
      const r = nodeRadius(node)
      const dimmed = focusId != null && !highlight?.has(node.id)
      ctx.globalAlpha = dimmed ? 0.15 : 1

      ctx.beginPath()
      ctx.arc(x, y, r, 0, 2 * Math.PI)
      ctx.fillStyle = colorForType(node.type)
      ctx.fill()

      if (node.id === focusId) {
        ctx.lineWidth = 2 / scale
        ctx.strokeStyle = colorForType(node.type)
        ctx.beginPath()
        ctx.arc(x, y, r + 2.5, 0, 2 * Math.PI)
        ctx.stroke()
      }

      if (scale > 1.1 || node.id === focusId || node.type === "Gap") {
        const label = node.type === "Gap" ? "⚠" : node.label
        const fontSize = Math.max(10 / scale, 2)
        ctx.font = `${fontSize}px Inter, system-ui, sans-serif`
        ctx.textAlign = "center"
        ctx.textBaseline = "top"
        ctx.fillStyle = node.type === "Gap" ? TYPE_COLOR.Gap : textColor
        ctx.fillText(truncate(label, 26), x, y + r + 1)
      }
      ctx.globalAlpha = 1
    },
    [focusId, highlight, textColor],
  )

  const paintPointerArea = useCallback(
    (node: NodeObject<GNode>, color: string, ctx: CanvasRenderingContext2D) => {
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(node.x ?? 0, node.y ?? 0, nodeRadius(node) + 2, 0, 2 * Math.PI)
      ctx.fill()
    },
    [],
  )

  const linkColorFn = useCallback(
    (link: LinkObject<GNode, GLink>) => {
      const s = linkEndId(link.source)
      const t = linkEndId(link.target)
      const active = focusId != null && (s === focusId || t === focusId)
      if (link.gap) return active ? "#ef4444" : "rgba(239,68,68,0.45)"
      if (focusId != null && !active) return dimColor
      return isDark ? "rgba(148,163,184,0.55)" : "rgba(100,116,139,0.5)"
    },
    [focusId, dimColor, isDark],
  )

  return (
    <div ref={containerRef} className="absolute inset-0">
      {size.width > 0 && (
        <ForceGraph2D<GNode, GLink>
          ref={fgRef}
          graphData={graphData}
          width={size.width}
          height={size.height}
          backgroundColor="rgba(0,0,0,0)"
          nodeRelSize={3}
          nodeVal={(n) => Math.max(n.count, 1)}
          nodeLabel={(n) =>
            n.type === "Gap"
              ? (n.reason ?? "пробел")
              : `${TYPE_LABEL_RU[n.type] ?? n.type}: ${n.label}`
          }
          nodeCanvasObject={paintNode}
          nodePointerAreaPaint={paintPointerArea}
          linkColor={linkColorFn}
          linkWidth={(l) => (l.gap ? 0.6 : 0.8)}
          linkLineDash={(l) => (l.gap ? [4, 3] : null)}
          linkDirectionalArrowLength={(l) => (l.gap ? 0 : 2.5)}
          linkDirectionalArrowRelPos={1}
          linkDirectionalArrowColor={linkColorFn}
          d3VelocityDecay={0.3}
          cooldownTicks={140}
          onEngineStop={handleEngineStop}
          onNodeHover={(n) => setHoverId(n ? n.id : null)}
          onNodeClick={(n) => onSelect(n.id)}
          onBackgroundClick={() => onSelect(null)}
        />
      )}
    </div>
  )
}

// ── Инспектор: детали узла / связанные лаборатории и эксперты / пробелы ─────────
function Inspector({
  node,
  neighbours,
  nodeById,
  gaps,
  notes,
  onPick,
}: {
  node: GNode | null
  neighbours: { otherId: string; type: string }[]
  nodeById: Map<string, GNode>
  gaps: GraphGap[]
  notes: string[]
  onPick: (id: string) => void
}) {
  if (node?.type === "Gap") {
    return (
      <div className="flex flex-col gap-2">
        <div className="text-[17px] font-bold">Пробел в данных</div>
        <div className="rounded-[10px] border border-destructive/30 bg-destructive/5 p-3 text-[13px] text-muted-foreground">
          {node.reason}
        </div>
        <p className="text-[12px] text-muted-foreground">
          Комбинация присутствует в корпусе по отдельности, но ни одного
          эксперимента для неё нет — кандидат на постановку опыта.
        </p>
      </div>
    )
  }

  if (node) {
    // Группируем соседей по типу — лаборатории и эксперты в приоритете.
    const grouped = new Map<string, GNode[]>()
    for (const nb of neighbours) {
      const other = nodeById.get(nb.otherId)
      if (!other || other.type === "Gap") continue
      const list = grouped.get(other.type) ?? []
      if (!list.some((x) => x.id === other.id)) list.push(other)
      grouped.set(other.type, list)
    }
    const order = [
      "Lab",
      "Expert",
      "Material",
      "Process",
      "Equipment",
      "Result",
    ]
    const known = order.filter((t) => grouped.has(t))
    const extra = [...grouped.keys()].filter((t) => !order.includes(t))
    const groups = [...known, ...extra]
    const rawSources = (node.properties?.sources ?? []) as unknown
    const sources = Array.isArray(rawSources) ? (rawSources as string[]) : []
    // Внутренние/составные ключи не показываем в таблице свойств.
    const props = Object.entries(node.properties ?? {}).filter(
      ([k, v]) =>
        !["count", "sources", "kg_type", "reason"].includes(k) &&
        typeof v !== "object",
    )

    return (
      <div className="flex flex-col gap-3.5">
        <div>
          <div className="flex items-center gap-2 text-[17px] font-bold">
            <span
              className="inline-block size-3 rounded-full"
              style={{ backgroundColor: colorForType(node.type) }}
            />
            {node.label || node.id}
          </div>
          <div className="pt-1 text-[12px] text-muted-foreground">
            {TYPE_LABEL_RU[node.type] ?? node.type}
            {node.count > 1 ? ` · ${node.count} эксп.` : ""}
          </div>
        </div>

        {groups.length === 0 && props.length === 0 && (
          <p className="text-[13px] text-muted-foreground">Нет связей</p>
        )}

        {groups.map((type) => (
          <div key={type} className="flex flex-col gap-1.5">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted-foreground">
              {TYPE_LABEL_RU[type] ?? type} ({grouped.get(type)?.length})
            </p>
            <div className="flex flex-wrap gap-1.5">
              {grouped.get(type)?.map((n) => (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => onPick(n.id)}
                  className="cursor-pointer"
                >
                  <Badge variant="neutral" className="font-normal">
                    {truncate(n.label || n.id, 24)}
                  </Badge>
                </button>
              ))}
            </div>
          </div>
        ))}

        {props.length > 0 && (
          <div className="overflow-hidden rounded-[10px] border">
            <div className="bg-muted/50 px-3.5 py-2 text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted-foreground">
              Свойства
            </div>
            {props.map(([k, v]) => (
              <div
                key={k}
                className="flex justify-between gap-3 border-t px-3.5 py-2 text-[12.5px]"
              >
                <span className="text-muted-foreground">{k}</span>
                <span className="text-right font-mono font-medium">
                  {truncate(String(v), 24)}
                </span>
              </div>
            ))}
          </div>
        )}

        {sources.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted-foreground">
              Источники ({sources.length})
            </p>
            {sources.map((src) => (
              <div
                key={src}
                className="truncate rounded-md border bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground"
                title={src}
              >
                {src.split("/").pop()}
              </div>
            ))}
          </div>
        )}

        {node.type === "Material" && grouped.has("Lab") && (
          <Button variant="outline" className="w-full" asChild>
            <Link to="/wiki" search={{ doc: undefined }}>
              Открыть тему в Вики
            </Link>
          </Button>
        )}
      </div>
    )
  }

  // Ничего не выбрано — сводка пробелов (SQL-coverage + текстовые из GraphRAG).
  const reasons = [...gaps.map((g) => g.reason), ...notes]
  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="text-[15px] font-bold">
        Пробелы в данных ({reasons.length})
      </div>
      <p className="text-[12px] text-muted-foreground">
        Комбинации без экспериментов / темы без данных. Нажмите узел графа,
        чтобы увидеть связанные лаборатории и экспертов.
      </p>
      <div className="flex min-h-0 flex-col gap-1.5 overflow-y-auto">
        {reasons.length === 0 && (
          <p className="text-[13px] text-muted-foreground">
            Пробелов не найдено
          </p>
        )}
        {reasons.slice(0, 40).map((reason) => (
          <div
            key={reason}
            className="rounded-md border border-destructive/25 bg-destructive/5 px-2 py-1.5 text-[11.5px] text-muted-foreground"
          >
            {reason}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Страница ───────────────────────────────────────────────────────────────────
function GraphPage() {
  const [mode, setMode] = useState<Mode>("overview")
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // overview — GraphRAG (реальные сущности, извлечённые из документов)
  const [kgQ, setKgQ] = useState("")
  const [kgDepth, setKgDepth] = useState("2")
  const [appliedKg, setAppliedKg] = useState({ q: "", depth: "2" })

  // advanced inputs
  const [entityId, setEntityId] = useState("")
  const [depth, setDepth] = useState("1")
  const [templateId, setTemplateId] = useState("")
  const [paramsText, setParamsText] = useState("{}")
  const [paramsError, setParamsError] = useState<string | null>(null)
  const [from, setFrom] = useState("")
  const [to, setTo] = useState("")
  const [maxDepth, setMaxDepth] = useState("4")
  const [advResult, setAdvResult] = useState<SubgraphResponse | null>(null)

  const kgQuery = useQuery({
    queryKey: ["graph", "kg", appliedKg],
    enabled: mode === "overview",
    queryFn: () =>
      GraphService.kg({
        q: appliedKg.q || undefined,
        depth: Number(appliedKg.depth) || 2,
        limit: 400,
      }),
  })

  const onAdvResult = (data: SubgraphResponse) => {
    setAdvResult(data)
    setSelectedId(data.nodes?.[0]?.id ?? null)
  }
  const subgraph = useMutation({
    mutationFn: () =>
      GraphService.subgraph({ entityId, depth: Number(depth) || undefined }),
    onSuccess: onAdvResult,
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
    onSuccess: onAdvResult,
  })
  const path = useMutation({
    mutationFn: () =>
      GraphService.path({
        _from: from,
        to,
        maxDepth: Number(maxDepth) || undefined,
      }),
    onSuccess: onAdvResult,
  })

  const runTemplate = () => {
    try {
      const params = JSON.parse(paramsText || "{}")
      setParamsError(null)
      template.mutate(params)
    } catch {
      setParamsError("Параметры должны быть корректным JSON")
    }
  }

  const pathIs503 =
    mode === "path" &&
    path.error instanceof ApiError &&
    path.error.status === 503

  // Данные для канваса: overview (GraphRAG) либо результат advanced-режима.
  const { nodes, links, gaps, notes } = useMemo(() => {
    if (mode === "overview") {
      const data = kgQuery.data
      const rawNodes = data?.nodes ?? []
      const ids = new Set(rawNodes.map((n) => n.id))
      const rawEdges = (data?.edges ?? []).filter(
        (e) => ids.has(e.source) && ids.has(e.target),
      )
      return {
        // GraphRAG: узлы одного размера (count=1), чтобы граф не «жирнел» от
        // числа источников — оно доступно в инспекторе.
        nodes: rawNodes.map((n) => {
          const p = (n.properties ?? {}) as Record<string, unknown>
          return {
            id: n.id,
            label: n.label,
            type: n.type,
            count: 1,
            properties: p,
          } satisfies GNode
        }),
        links: rawEdges.map((e) => ({
          source: e.source,
          target: e.target,
          type: e.type,
          gap: false,
        })),
        gaps: [] as GraphGap[],
        notes: data?.notes ?? [],
      }
    }
    const data = advResult
    return {
      nodes: (data?.nodes ?? []).map((n) => ({
        id: n.id,
        label: n.label,
        type: n.type,
        count: 1,
        properties: n.properties as Record<string, unknown> | undefined,
      })),
      links: (data?.edges ?? []).map((e) => ({
        source: e.source,
        target: e.target,
        type: e.type,
        gap: false,
      })),
      gaps: [] as GraphGap[],
      notes: [] as string[],
    }
  }, [mode, kgQuery.data, advResult])

  const { nodeById, neighbourList } = useMemo(() => {
    const byId = new Map<string, GNode>()
    for (const n of nodes) byId.set(n.id, n)
    const adj = new Map<string, { otherId: string; type: string }[]>()
    const push = (a: string, b: string, type: string) => {
      const list = adj.get(a) ?? []
      list.push({ otherId: b, type })
      adj.set(a, list)
    }
    for (const l of links) {
      push(l.source, l.target, l.type)
      push(l.target, l.source, l.type)
    }
    return { nodeById: byId, neighbourList: adj }
  }, [nodes, links])

  const selectedNode = selectedId ? (nodeById.get(selectedId) ?? null) : null
  const isLoading =
    mode === "overview"
      ? kgQuery.isFetching
      : subgraph.isPending || template.isPending || path.isPending
  const activeError =
    mode === "overview"
      ? kgQuery.error
      : mode === "subgraph"
        ? subgraph.error
        : mode === "template"
          ? template.error
          : path.error

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Граф знаний"
        actions={
          <div className="hidden flex-wrap gap-1.5 text-[11.5px] lg:flex">
            {LEGEND_TYPES.map((t) => (
              <span key={t} className="flex items-center gap-1.5">
                <span
                  className="size-2.5 rounded-full"
                  style={{ backgroundColor: TYPE_COLOR[t] }}
                />
                {TYPE_LABEL_RU[t]}
              </span>
            ))}
          </div>
        }
      />

      {/* панель управления */}
      <div className="flex flex-col gap-2.5 border-b bg-card px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center gap-1.5">
          {(
            [
              ["overview", "Обзор"],
              ["subgraph", "Подграф по сущности"],
              ["template", "Шаблон Cypher"],
              ["path", "Путь (Neo4j)"],
            ] as [Mode, string][]
          ).map(([m, label]) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMode(m)
                setSelectedId(null)
              }}
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

        {mode === "overview" && (
          <div className="flex flex-wrap items-center gap-2">
            <Input
              placeholder="тема / сущность (напр. As, Cu, медь) — пусто = обзор"
              value={kgQ}
              onChange={(e) => setKgQ(e.target.value)}
              onKeyDown={(e) =>
                e.key === "Enter" && setAppliedKg({ q: kgQ, depth: kgDepth })
              }
              className="max-w-[320px]"
            />
            <Select value={kgDepth} onValueChange={setKgDepth}>
              <SelectTrigger className="w-[130px]">
                <SelectValue placeholder="глубина" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="1">глубина 1</SelectItem>
                <SelectItem value="2">глубина 2</SelectItem>
                <SelectItem value="3">глубина 3</SelectItem>
                <SelectItem value="4">глубина 4</SelectItem>
              </SelectContent>
            </Select>
            <Button
              onClick={() => {
                setSelectedId(null)
                setAppliedKg({ q: kgQ, depth: kgDepth })
              }}
              disabled={kgQuery.isFetching}
            >
              {kgQuery.isFetching && (
                <Loader2 className="size-4 animate-spin" />
              )}
              Показать
            </Button>
            <span className="text-[11.5px] text-muted-foreground">
              Реальные сущности из документов (spaCy + GraphRAG)
            </span>
          </div>
        )}

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

        {activeError && (
          <p className="text-xs text-destructive">
            {pathIs503
              ? "Neo4j недоступен (503)."
              : activeError instanceof Error
                ? activeError.message
                : "Запрос не выполнен"}
          </p>
        )}
      </div>

      {/* канвас + инспектор */}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_330px]">
        <div className="relative min-h-[320px] overflow-hidden bg-muted/20">
          {nodes.length > 0 ? (
            <GraphCanvas
              nodes={nodes}
              links={links}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          ) : (
            <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
              {isLoading
                ? "Загрузка графа…"
                : mode === "overview"
                  ? "Нет данных по заданному фильтру."
                  : "Постройте подграф, шаблон или путь, чтобы увидеть визуализацию."}
            </div>
          )}
          {nodes.length > 0 && (
            <div className="absolute bottom-4 left-4 rounded-lg border bg-card px-3 py-1.5 text-[11.5px] text-muted-foreground">
              {nodes.length} узлов · {links.length} связей
              {mode === "overview"
                ? ` · ${gaps.length + notes.length} пробелов`
                : ""}
            </div>
          )}
        </div>
        <div className="flex min-h-0 flex-col gap-3.5 overflow-y-auto border-l bg-card p-5">
          <Inspector
            node={selectedNode}
            neighbours={selectedId ? (neighbourList.get(selectedId) ?? []) : []}
            nodeById={nodeById}
            gaps={gaps}
            notes={notes}
            onPick={setSelectedId}
          />
        </div>
      </div>
    </div>
  )
}
