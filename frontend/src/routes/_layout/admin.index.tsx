import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Suspense } from "react"

import { type UserPublic, UsersService } from "@/client"
import AddUser from "@/components/Admin/AddUser"
import { columns, type UserTableData } from "@/components/Admin/columns"
import { DataTable } from "@/components/Common/DataTable"
import { PageContainer } from "@/components/Common/PageContainer"
import PendingUsers from "@/components/Pending/PendingUsers"
import useAuth from "@/hooks/useAuth"
import { getPageCount, usePagination } from "@/hooks/usePagination"

export const Route = createFileRoute("/_layout/admin/")({
  component: AdminUsersPage,
  head: () => ({
    meta: [{ title: "Пользователи — MetalCrow" }],
  }),
})

function UsersTableContent() {
  const { user: currentUser } = useAuth()
  const { pageIndex, pageSize, skip, setPageIndex, setPageSize } =
    usePagination()

  const { data: users } = useSuspenseQuery({
    queryKey: ["users", pageIndex, pageSize],
    queryFn: () => UsersService.readUsers({ skip, limit: pageSize }),
  })

  const tableData: UserTableData[] = users.data.map((user: UserPublic) => ({
    ...user,
    isCurrentUser: currentUser?.id === user.id,
  }))

  return (
    <DataTable
      columns={columns}
      data={tableData}
      manualPagination
      pageCount={getPageCount(users.count, pageSize)}
      totalCount={users.count}
      pagination={{ pageIndex, pageSize }}
      onPaginationChange={(updater) => {
        const next =
          typeof updater === "function"
            ? updater({ pageIndex, pageSize })
            : updater
        if (next.pageIndex !== pageIndex) {
          setPageIndex(next.pageIndex)
        }
        if (next.pageSize !== pageSize) {
          setPageSize(next.pageSize)
        }
      }}
    />
  )
}

function UsersTable() {
  return (
    <Suspense fallback={<PendingUsers />}>
      <UsersTableContent />
    </Suspense>
  )
}

function AdminUsersPage() {
  return (
    <PageContainer title="Пользователи" actions={<AddUser />}>
      <UsersTable />
    </PageContainer>
  )
}
