import { OpenAPI } from "@/client"

function apiUrl(path: string): string {
  const base = (OpenAPI.BASE ?? "").replace(/\/$/, "")
  return `${base}${path}`
}

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null
  const rfc5987 = header.match(/filename\*=UTF-8''([^;]+)/i)
  if (rfc5987?.[1]) return decodeURIComponent(rfc5987[1])
  const quoted = header.match(/filename="([^"]+)"/i)
  if (quoted?.[1]) return quoted[1]
  const plain = header.match(/filename=([^;]+)/i)
  if (plain?.[1]) return plain[1].trim().replace(/"/g, "")
  return null
}

// Fetch the original raw document as an object URL for inline viewing (e.g.
// <embed> a PDF). The raw endpoint requires a bearer token, so a plain
// <embed src="…"> can't authenticate — fetch → blob → object URL instead.
// Caller must URL.revokeObjectURL when done.
export async function fetchWikiRawBlobUrl(
  okfPath: string,
): Promise<{ url: string; contentType: string }> {
  const token = localStorage.getItem("access_token")
  const response = await fetch(
    apiUrl(
      `/api/v1/wiki/documents/download/raw?okf_path=${encodeURIComponent(okfPath)}`,
    ),
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    },
  )
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = body.detail
    if (typeof detail === "string") throw new Error(detail)
    throw new Error(`Raw fetch failed (${response.status})`)
  }
  const contentType =
    response.headers.get("Content-Type") ?? "application/octet-stream"
  const blob = await response.blob()
  return { url: URL.createObjectURL(blob), contentType }
}

export async function downloadWikiDocument(
  okfPath: string,
  variant: "markdown" | "raw",
): Promise<void> {
  const token = localStorage.getItem("access_token")
  const endpoint =
    variant === "markdown"
      ? "/api/v1/wiki/documents/download/markdown"
      : "/api/v1/wiki/documents/download/raw"
  const response = await fetch(
    apiUrl(`${endpoint}?okf_path=${encodeURIComponent(okfPath)}`),
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
  const fallback = variant === "markdown" ? "document.md" : "document"
  const filename =
    filenameFromDisposition(response.headers.get("Content-Disposition")) ??
    fallback

  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
