import { expect, test } from "@playwright/test"

// LiteraturePanel (Task 13) is only wired into the chat page by Task 14, which
// has not landed yet at the time this spec was authored. Per
// backend/app/api/routes/litsearch.py::get_search, an assistant chat message
// that belongs to a literature search carries `message_metadata.search_id`
// (+ `litsearch_kind`) — the natural hook for Task 14 to render
// `<LiteraturePanel searchId={message.message_metadata.search_id} />` under
// that message. This spec assumes that wiring. If Task 14 lands with a
// different mounting mechanism (e.g. a dedicated route/query param), update
// the `page.goto` / session-select steps below accordingly — the network
// mocks and the final assertions (spinner label, "добавлено в диалог" badge,
// enabled "Добавить в базу" button) should not need to change.
//
// This test requires a live backend (auth.setup logs in via /login) which is
// not available in every environment — it is meant to run in the e2e phase
// once Task 14 is merged, not as part of this task's verification.

const searchId = "11111111-1111-1111-1111-111111111111"
const sessionId = "22222222-2222-2222-2222-222222222222"

test("literature panel shows download spinner and add-to-database state", async ({
  page,
}) => {
  await page.route("**/api/v1/chat/sessions", async (route) => {
    if (route.request().method() !== "GET") {
      return route.fallback()
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            id: sessionId,
            title: "Литературный поиск",
            created_at: "2026-07-01T00:00:00Z",
          },
        ],
        count: 1,
      }),
    })
  })

  await page.route(`**/api/v1/chat/sessions/${sessionId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "33333333-3333-3333-3333-333333333333",
          session_id: sessionId,
          role: "user",
          content: "Найди статьи про сегрегацию меди",
          message_metadata: null,
          created_at: "2026-07-01T00:00:01Z",
        },
        {
          id: "44444444-4444-4444-4444-444444444444",
          session_id: sessionId,
          role: "assistant",
          content: "Нашёл несколько статей по теме.",
          message_metadata: {
            search_id: searchId,
            litsearch_kind: "abstracts",
          },
          created_at: "2026-07-01T00:00:05Z",
        },
      ]),
    })
  })

  await page.route(`**/api/v1/litsearch/${searchId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: searchId,
        stage: "done",
        round: 1,
        followup_search_id: null,
        papers: [
          {
            id: "paper-downloading",
            doi: null,
            title: "Copper segregation in nickel alloys",
            authors: "A. Smith, B. Jones",
            year: 2023,
            abstract: "A study of copper segregation effects.",
            pdf_url: null,
            citation_count: 5,
            fetch_status: "downloading",
            fulltext_status: "none",
            fulltext_chars: 0,
            ingest_status: "none",
            document_id: null,
          },
          {
            id: "paper-added",
            doi: "10.1000/xyz",
            title: "Grain boundary segregation review",
            authors: "C. Lee",
            year: 2022,
            abstract: "A review of grain boundary segregation.",
            pdf_url: "https://example.com/paper.pdf",
            citation_count: 42,
            fetch_status: "done",
            fulltext_status: "added",
            fulltext_chars: 12000,
            ingest_status: "none",
            document_id: null,
          },
        ],
        answers: [
          {
            message_id: "44444444-4444-4444-4444-444444444444",
            kind: "abstracts",
          },
        ],
      }),
    })
  })

  await page.goto("/chat")
  await page.getByText("Литературный поиск").click()

  await expect(page.getByText("Скачивание…")).toBeVisible()

  await expect(page.getByText("добавлено в диалог")).toBeVisible()
  const addButton = page.getByRole("button", { name: "Добавить в базу" }).last()
  await expect(addButton).toBeEnabled()
})
