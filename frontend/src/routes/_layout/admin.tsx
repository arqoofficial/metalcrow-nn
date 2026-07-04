import { createFileRoute, Outlet } from "@tanstack/react-router"

import { ensureSuperuser } from "@/lib/session"

export const Route = createFileRoute("/_layout/admin")({
  component: AdminLayout,
  beforeLoad: ensureSuperuser,
})

function AdminLayout() {
  return <Outlet />
}
