import {
  BarChart3,
  BookOpen,
  Bot,
  FileText,
  LayoutDashboard,
  LayoutGrid,
  Search,
  Share2,
  UploadCloud,
  Users,
} from "lucide-react"

import { SidebarAppearance } from "@/components/Common/Appearance"
import { Logo } from "@/components/Common/Logo"
import { StubMark } from "@/components/Common/StubMark"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { type Item, Main } from "./Main"
import { Sessions } from "./Sessions"
import { User } from "./User"

const mainItems: Item[] = [
  { icon: LayoutDashboard, title: "Дашборд", path: "/" },
  { icon: Bot, title: "Агент", path: "/chat" },
  { icon: BookOpen, title: "Вики", path: "/wiki" },
  { icon: Share2, title: "Граф", path: "/graph" },
  { icon: Search, title: "Поиск", path: "/search" },
]

const inDevelopmentItems: Item[] = [
  { icon: FileText, title: "Отчёты", path: "/reports" },
  { icon: LayoutGrid, title: "Пробелы", path: "/gaps" },
]

const adminItems: Item[] = [
  { icon: Users, title: "Пользователи", path: "/admin" },
  { icon: BarChart3, title: "Покрытие", path: "/admin/coverage" },
  { icon: UploadCloud, title: "Загрузка", path: "/ingest" },
]

export function AppSidebar() {
  const { user: currentUser } = useAuth()

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 py-5 group-data-[collapsible=icon]:items-center group-data-[collapsible=icon]:px-0">
        <Logo variant="responsive" />
      </SidebarHeader>
      <SidebarContent>
        <Main items={mainItems} />
        <Main
          items={inDevelopmentItems}
          label={
            <span className="inline-flex items-center gap-1">
              В разработке <StubMark reason="Раздел на демо-данных" />
            </span>
          }
        />
        {currentUser?.is_superuser && (
          <Main items={adminItems} label="Администрирование" />
        )}
        <Sessions />
      </SidebarContent>
      <SidebarFooter>
        <SidebarAppearance />
        <User user={currentUser} />
      </SidebarFooter>
    </Sidebar>
  )
}

export default AppSidebar
