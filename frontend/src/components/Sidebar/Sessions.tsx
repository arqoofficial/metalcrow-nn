import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Link as RouterLink,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router"
import { Plus, Trash2 } from "lucide-react"
import { useState } from "react"

import { ChatService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  useSidebar,
} from "@/components/ui/sidebar"
import useCustomToast from "@/hooks/useCustomToast"
import { cn } from "@/lib/utils"

/**
 * Секция «Сессии» в сайдбаре: список чат-сессий, выбор активной через
 * search-параметр /chat?session=…, создание и удаление. Обёрнута в реальный
 * ChatService (сессии — настоящий бэкенд).
 */
export function Sessions() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { isMobile, setOpenMobile } = useSidebar()
  const { showErrorToast } = useCustomToast()

  const location = useRouterState({ select: (s) => s.location })
  const activeSessionId =
    location.pathname === "/chat"
      ? ((location.search as { session?: string }).session ?? null)
      : null

  const [pendingDelete, setPendingDelete] = useState<{
    id: string
    title: string | null
  } | null>(null)

  const { data: sessions } = useQuery({
    queryKey: ["chat-sessions"],
    queryFn: () => ChatService.listSessions(),
  })

  const createSession = useMutation({
    mutationFn: () =>
      ChatService.createSession({ requestBody: { title: null } }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] })
      if (isMobile) setOpenMobile(false)
      navigate({ to: "/chat", search: { session: created.id } })
    },
    onError: () => showErrorToast("Не удалось создать сессию"),
  })

  const deleteSession = useMutation({
    mutationFn: (id: string) => ChatService.deleteSession({ sessionId: id }),
    onSuccess: (_data, id) => {
      setPendingDelete(null)
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] })
      if (activeSessionId === id) {
        navigate({ to: "/chat", search: {} })
      }
    },
    onError: () => showErrorToast("Не удалось удалить сессию"),
  })

  const items = sessions?.data ?? []

  const handleSelect = () => {
    if (isMobile) setOpenMobile(false)
  }

  return (
    <SidebarGroup className="min-h-0 group-data-[collapsible=icon]:hidden">
      <SidebarGroupLabel>Сессии</SidebarGroupLabel>
      <SidebarGroupContent className="flex min-h-0 flex-col gap-1">
        <button
          type="button"
          onClick={() => createSession.mutate()}
          disabled={createSession.isPending}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm font-medium text-primary transition-colors hover:bg-primary/10 disabled:opacity-60"
        >
          <Plus className="size-4 shrink-0" />
          Новая сессия
        </button>

        <div className="flex max-h-56 min-h-0 flex-col gap-0.5 overflow-y-auto">
          {items.length === 0 && (
            <p className="px-2 py-1 text-xs text-muted-foreground">
              Сессий пока нет
            </p>
          )}
          {items.map((session) => {
            const isActive = activeSessionId === session.id
            return (
              <div
                key={session.id}
                className={cn(
                  "group/session flex items-center gap-1 rounded-md pr-1 text-sm transition-colors",
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "hover:bg-sidebar-accent/60",
                )}
              >
                <RouterLink
                  to="/chat"
                  search={{ session: session.id }}
                  onClick={handleSelect}
                  className="min-w-0 flex-1 truncate px-2 py-1.5"
                  title={session.title || "Без названия"}
                >
                  {session.title || "Без названия"}
                </RouterLink>
                <button
                  type="button"
                  aria-label="Удалить сессию"
                  onClick={(event) => {
                    event.preventDefault()
                    event.stopPropagation()
                    setPendingDelete({
                      id: session.id,
                      title: session.title ?? null,
                    })
                  }}
                  className="size-6 shrink-0 rounded-md text-muted-foreground opacity-0 transition-opacity group-hover/session:opacity-100 hover:text-destructive"
                >
                  <Trash2 className="mx-auto size-3.5" />
                </button>
              </div>
            )
          })}
        </div>
      </SidebarGroupContent>

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить сессию?</DialogTitle>
            <DialogDescription>
              {pendingDelete?.title
                ? `«${pendingDelete.title}» и все её сообщения будут`
                : "Эта сессия и все её сообщения будут"}{" "}
              <strong>удалены безвозвратно.</strong> Действие нельзя отменить.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={deleteSession.isPending}>
                Отмена
              </Button>
            </DialogClose>
            <LoadingButton
              variant="destructive"
              loading={deleteSession.isPending}
              onClick={() => {
                if (pendingDelete) deleteSession.mutate(pendingDelete.id)
              }}
            >
              Удалить
            </LoadingButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SidebarGroup>
  )
}
