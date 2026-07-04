import type { ReactNode } from "react"

import { PageHeader } from "@/components/Common/PageHeader"

/**
 * Обёртка для «служебных» экранов (админка, загрузка, настройки): панель с
 * заголовком + прокручиваемая область с отступами и ограничением ширины.
 * Заменяет отступы, которые раньше давал _layout.
 */
export function PageContainer({
  title,
  actions,
  children,
}: {
  title: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={title} actions={actions} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-7xl p-6 md:p-8">{children}</div>
      </div>
    </div>
  )
}
