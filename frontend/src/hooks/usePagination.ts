import { useCallback, useState } from "react"

export const DEFAULT_PAGE_SIZE = 10
export const PAGE_SIZE_OPTIONS = [5, 10, 25, 50] as const

export function getPageCount(totalCount: number, pageSize: number): number {
  if (totalCount === 0) return 0
  return Math.ceil(totalCount / pageSize)
}

export function paginateArray<T>(
  items: readonly T[],
  pageIndex: number,
  pageSize: number,
): T[] {
  const start = pageIndex * pageSize
  return items.slice(start, start + pageSize)
}

export function usePagination(initialPageSize = DEFAULT_PAGE_SIZE) {
  const [pageIndex, setPageIndex] = useState(0)
  const [pageSize, setPageSize] = useState(initialPageSize)

  const skip = pageIndex * pageSize
  const offset = skip

  const setPageSizeAndReset = useCallback((size: number) => {
    setPageSize(size)
    setPageIndex(0)
  }, [])

  const resetPage = useCallback(() => {
    setPageIndex(0)
  }, [])

  return {
    pageIndex,
    pageSize,
    skip,
    offset,
    setPageIndex,
    setPageSize: setPageSizeAndReset,
    resetPage,
  }
}
