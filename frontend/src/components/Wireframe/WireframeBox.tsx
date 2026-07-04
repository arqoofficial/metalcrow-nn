import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

type WireframeBoxProps = {
  label: string
  children?: ReactNode
  className?: string
  height?: string
}

/** Low-fi wireframe placeholder block (Phase 0). */
export function WireframeBox({
  label,
  children,
  className,
  height = "h-24",
}: WireframeBoxProps) {
  return (
    <div
      className={cn(
        "rounded-lg border-2 border-dashed border-muted-foreground/40 bg-muted/20 p-4",
        height,
        className,
      )}
    >
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      {children ?? (
        <div className="h-full rounded bg-muted/40 text-xs text-muted-foreground">
          placeholder
        </div>
      )}
    </div>
  )
}

type WireframeScreenProps = {
  title: string
  subtitle: string
  role: string
  children: ReactNode
}

export function WireframeScreen({
  title,
  subtitle,
  role,
  children,
}: WireframeScreenProps) {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Wireframe · {role}
          </p>
          <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
          <p className="text-muted-foreground">{subtitle}</p>
        </div>
        <span className="rounded border border-dashed px-2 py-1 text-xs text-muted-foreground">
          Phase 0 mock
        </span>
      </div>
      {children}
    </div>
  )
}
