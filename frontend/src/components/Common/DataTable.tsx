import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  type OnChangeFn,
  type PaginationState,
  useReactTable,
} from "@tanstack/react-table"

import { TablePagination } from "@/components/Common/TablePagination"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { DEFAULT_PAGE_SIZE } from "@/hooks/usePagination"

interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[]
  data: TData[]
  manualPagination?: boolean
  pageCount?: number
  totalCount?: number
  pagination?: PaginationState
  onPaginationChange?: OnChangeFn<PaginationState>
}

export function DataTable<TData, TValue>({
  columns,
  data,
  manualPagination = false,
  pageCount,
  totalCount,
  pagination,
  onPaginationChange,
}: DataTableProps<TData, TValue>) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: manualPagination
      ? undefined
      : getPaginationRowModel(),
    manualPagination,
    pageCount: manualPagination ? pageCount : undefined,
    state: pagination ? { pagination } : undefined,
    onPaginationChange,
    initialState: {
      pagination: {
        pageIndex: pagination?.pageIndex ?? 0,
        pageSize: pagination?.pageSize ?? DEFAULT_PAGE_SIZE,
      },
    },
  })

  const resolvedTotalCount = totalCount ?? data.length
  const resolvedPageIndex =
    pagination?.pageIndex ?? table.getState().pagination.pageIndex
  const resolvedPageSize =
    pagination?.pageSize ?? table.getState().pagination.pageSize

  const handlePageIndexChange = (pageIndex: number) => {
    if (onPaginationChange) {
      onPaginationChange({ pageIndex, pageSize: resolvedPageSize })
      return
    }
    table.setPageIndex(pageIndex)
  }

  const handlePageSizeChange = (pageSize: number) => {
    if (onPaginationChange) {
      onPaginationChange({ pageIndex: 0, pageSize })
      return
    }
    table.setPageSize(pageSize)
    table.setPageIndex(0)
  }

  return (
    <div className="flex flex-col gap-4">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id} className="hover:bg-transparent">
              {headerGroup.headers.map((header) => {
                return (
                  <TableHead key={header.id}>
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                  </TableHead>
                )
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow className="hover:bg-transparent">
              <TableCell
                colSpan={columns.length}
                className="h-32 text-center text-muted-foreground"
              >
                No results found.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <TablePagination
        pageIndex={resolvedPageIndex}
        pageSize={resolvedPageSize}
        totalCount={resolvedTotalCount}
        onPageIndexChange={handlePageIndexChange}
        onPageSizeChange={handlePageSizeChange}
      />
    </div>
  )
}
