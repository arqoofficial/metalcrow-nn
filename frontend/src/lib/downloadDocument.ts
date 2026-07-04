import { OpenAPI } from "@/client"

function apiUrl(path: string): string {
  const base = (OpenAPI.BASE ?? "").replace(/\/$/, "")
  return `${base}${path}`
}

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null
  const match = header.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i)
  return match?.[1] ? decodeURIComponent(match[1].replace(/"/g, "")) : null
}

export async function downloadDocument(documentId: string): Promise<void> {
  const token = localStorage.getItem("access_token")
  const response = await fetch(
    apiUrl(`/api/v1/sources/${documentId}/content`),
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    },
  )

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = body.detail
    if (typeof detail === "string") {
      throw new Error(detail)
    }
    throw new Error(`Download failed (${response.status})`)
  }

  const blob = await response.blob()
  const filename =
    filenameFromDisposition(response.headers.get("Content-Disposition")) ??
    "document"

  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
