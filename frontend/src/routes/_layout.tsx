import { createFileRoute, Outlet } from "@tanstack/react-router"

import AppSidebar from "@/components/Sidebar/AppSidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { ensureAuthenticated } from "@/lib/session"

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: ensureAuthenticated,
})

function Layout() {
  return (
    // h-svh даёт оболочке фиксированную высоту вьюпорта, чтобы внутренние
    // области (лента чата, инспектор графа, таблицы) прокручивались сами, а не
    // растягивали body и не выталкивали шапку/композер за экран.
    <SidebarProvider className="h-svh overflow-hidden">
      <AppSidebar />
      <SidebarInset className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <Outlet />
      </SidebarInset>
    </SidebarProvider>
  )
}

export default Layout
