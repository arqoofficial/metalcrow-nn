import type { ReactNode } from "react"

import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { cn } from "@/lib/utils"

/**
 * Верхняя панель экрана в стиле прототипа: триггер сайдбара + заголовок/крошки
 * слева, действия справа, опциональный контент по центру (поиск, легенда).
 */
export function PageHeader({
  title,
  actions,
  children,
  className,
}: {
  title: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
}) {
  return (
    <header
      className={cn(
        "flex h-14 shrink-0 items-center gap-3 border-b bg-card px-4 md:px-6",
        className,
      )}
    >
      <SidebarTrigger className="-ml-1 text-muted-foreground" />
      <Separator
        orientation="vertical"
        className="mr-1 hidden data-[orientation=vertical]:h-5 sm:block"
      />
      <div className="flex min-w-0 items-center gap-2 text-sm font-semibold">
        {title}
      </div>
      {children && (
        <div className="flex min-w-0 flex-1 items-center gap-2">{children}</div>
      )}
      {actions && (
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {actions}
        </div>
      )}
    </header>
  )
}
