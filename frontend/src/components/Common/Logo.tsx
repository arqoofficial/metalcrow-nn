import { Link } from "@tanstack/react-router"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

const TILE_SIZE = {
  full: "size-9",
  icon: "size-8",
  responsive: "size-8",
} as const

const WORD_SIZE = {
  full: "text-xl",
  icon: "text-lg",
  responsive: "text-[15px]",
} as const

/**
 * Логотип MetalCrow: плитка-знак вороны на teal-фоне + словесный знак.
 * responsive — в свёрнутом сайдбаре остаётся только плитка.
 */
export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const content = (
    <span className={cn("flex items-center gap-2.5", className)}>
      <img
        src="/assets/images/metalcrow-tile.svg"
        alt=""
        aria-hidden
        className={cn("shrink-0 rounded-lg", TILE_SIZE[variant])}
      />
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
