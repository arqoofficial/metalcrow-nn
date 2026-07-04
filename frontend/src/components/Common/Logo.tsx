import { Link } from "@tanstack/react-router"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

const TILE_SIZE = {
  full: "size-9 text-sm",
  icon: "size-8 text-[13px]",
  responsive: "size-8 text-[13px]",
} as const

const WORD_SIZE = {
  full: "text-xl",
  icon: "text-lg",
  responsive: "text-[15px]",
} as const

/**
 * Логотип MetalCrow: плитка «MC» на teal-фоне + словесный знак.
 * responsive — в свёрнутом сайдбаре остаётся только плитка.
 */
export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const content = (
    <span className={cn("flex items-center gap-2.5", className)}>
      <span
        className={cn(
          "grid shrink-0 place-items-center rounded-lg bg-primary font-mono font-bold tracking-tight text-primary-foreground",
          TILE_SIZE[variant],
        )}
        aria-hidden
      >
        MC
      </span>
      <span
        className={cn(
          "font-bold tracking-tight text-foreground",
          WORD_SIZE[variant],
          variant === "icon" && "hidden",
          variant === "responsive" && "group-data-[collapsible=icon]:hidden",
        )}
      >
        MetalCrow
      </span>
    </span>
  )

  if (!asLink) {
    return content
  }

  return (
    <Link to="/" aria-label="MetalCrow — на дашборд">
      {content}
    </Link>
  )
}
