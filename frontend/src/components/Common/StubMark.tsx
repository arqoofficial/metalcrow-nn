import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

const DEFAULT_REASON = "Заглушка: не подключено к бэкенду — реализовать позже"

/**
 * Маркеры заглушек. Всё, что помечено ими, — демо-данные, ещё не подключённые
 * к реальному API. Красный «@» и пунктирное подчёркивание намеренно заметны,
 * чтобы незакрытые места не потерялись.
 */

/** Инлайновый красный «@» с подсказкой. */
export function StubMark({
  reason = DEFAULT_REASON,
  className,
}: {
  reason?: string
  className?: string
}) {
  return (
    <span
      role="img"
      title={reason}
      aria-label={`Заглушка: ${reason}`}
      className={cn(
        "inline-flex select-none items-center font-mono font-bold text-destructive",
        className,
      )}
    >
      @
    </span>
  )
}

/** Оборачивает текст-плейсхолдер пунктирным красным подчёркиванием. */
export function StubText({
  children,
  reason = DEFAULT_REASON,
  className,
}: {
  children: ReactNode
  reason?: string
  className?: string
}) {
  return (
    <span
      title={reason}
      className={cn(
        "underline decoration-destructive decoration-dashed underline-offset-4",
        className,
      )}
    >
      {children}
    </span>
  )
}

/** Плашка-предупреждение для экранов/секций, целиком построенных на демо-данных. */
export function StubBanner({
  children,
  className,
}: {
  children?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border border-dashed border-destructive/50 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive",
        className,
      )}
    >
      <StubMark reason="Раздел на демо-данных" className="mt-px text-sm" />
      <span>
        {children ?? (
          <>
            Раздел показан на демонстрационных данных — соответствующий бэкенд
            ещё не реализован.
          </>
        )}
      </span>
    </div>
  )
}
