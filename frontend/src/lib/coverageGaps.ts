import type { CoverageResponse, RegimeBucket } from "@/client"

// Пробелы «нет данных» выводятся напрямую из матрицы покрытия онтологии:
// комбинация материал × свойство × режим, для которой нет ни одного
// эксперимента, — это реальный пробел (единственный тип, который можно
// честно посчитать из текущего бэкенда). Остальные типы (противоречие,
// один источник, гео-асимметрия, устаревшее) требуют отдельного детектора.

export const regimeLabel: Record<RegimeBucket, string> = {
  low: "низкий режим",
  medium: "средний режим",
  high: "высокий режим",
}

export type NoDataGap = {
  id: string
  material: string
  property: string
  regime: RegimeBucket
  /** эвристика важности: суммарное число экспериментов по этому материалу */
  importance: number
  title: string
}

// Разделитель ключа — управляющий символ (unit separator, 0x1F), которого не
// бывает в текстовых метках материалов/свойств. Без него «Ni»+«CuS» и
// «NiCu»+«S» дали бы один ключ, и пробел бы потерялся либо получил
// дублирующийся id (он же React-key).
const CELL_SEP = String.fromCharCode(31)

function cellKey(material: string, property: string, regime: string): string {
  return [material, property, regime].join(CELL_SEP)
}

export function deriveNoDataGaps(coverage: CoverageResponse): NoDataGap[] {
  const covered = new Set<string>()
  const perMaterialExperiments = new Map<string, number>()

  for (const cell of coverage.cells) {
    if (cell.experiment_count > 0) {
      covered.add(cellKey(cell.material, cell.property, cell.regime_bucket))
      perMaterialExperiments.set(
        cell.material,
        (perMaterialExperiments.get(cell.material) ?? 0) +
          cell.experiment_count,
      )
    }
  }

  const gaps: NoDataGap[] = []
  for (const material of coverage.materials) {
    for (const property of coverage.properties) {
      for (const regime of coverage.regime_buckets) {
        if (covered.has(cellKey(material, property, regime))) continue
        gaps.push({
          id: cellKey(material, property, regime),
          material,
          property,
          regime,
          importance: perMaterialExperiments.get(material) ?? 0,
          title: `${material} × ${property} × ${regimeLabel[regime]}`,
        })
      }
    }
  }

  // Сначала пробелы по хорошо изученным материалам — они заметнее.
  gaps.sort((a, b) => b.importance - a.importance)
  return gaps
}

export function countCoveredCells(coverage: CoverageResponse): number {
  return coverage.cells.filter((c) => c.experiment_count > 0).length
}
