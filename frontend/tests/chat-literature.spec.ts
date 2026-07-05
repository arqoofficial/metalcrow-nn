import { expect, test } from "@playwright/test"

// Task 14: wires LiteraturePanel (Task 13) into the chat page, adds the
// «Литература» chat mode, and threads the two litsearch assistant answers
// (abstracts, then fulltext) into one coherent conversation. This spec mocks
// the network end-to-end — postChatMessage's raw fetch (backend/app/api/routes/
// chat.py:66-85 streams a single SSE `data: {...}\n\n` event, see
// src/lib/postChatMessage.ts), the chat history/session endpoints and the
// litsearch poll (backend/app/api/routes/litsearch.py::get_search) — so it can
// run without a live backend. It is NOT run green as part of Task 14
// (`bunx playwright test --list` only, per the task brief); it's meant for the
// e2e phase once the full litsearch pipeline is available.

const sessionId = "22222222-2222-2222-2222-222222222222"
const searchId = "11111111-1111-1111-1111-111111111111"
const followupSearchId = "55555555-5555-5555-5555-555555555555"
const userMessageId = "33333333-3333-3333-3333-333333333333"
const abstractsMessageId = "44444444-4444-4444-4444-444444444444"
const fulltextMessageId = "66666666-6666-6666-6666-666666666666"

function paper(overrides: Record<string, unknown> = {}) {
  return {
    id: "paper-1",
    doi: "10.1000/xyz",
    title: "Copper segregation in nickel alloys",
    authors: "A. Smith, B. Jones",
    year: 2023,
    abstract: "A study of copper segregation effects.",
    pdf_url: "https://example.com/paper.pdf",
    citation_count: 5,
    fetch_status: "done",
    fulltext_status: "added",
    fulltext_chars: 12000,
    ingest_status: "none",
    document_id: null,
    ...overrides,
  }
}

test("selecting literature mode shows the panel, mode badge, and threads both answers", async ({
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

  // History: starts with just the user message + the "abstracts" answer;
  // once the litsearch poll reports the fulltext answer landed, chat.tsx
  // invalidates this query and we serve the fulltext message too.
  let fulltextReady = false
  await page.route(`**/api/v1/chat/sessions/${sessionId}`, async (route) => {
    if (route.request().method() !== "GET") {
      return route.fallback()
    }
    const messages = [
      {
        id: userMessageId,
        session_id: sessionId,
        role: "user",
        content: "Найди статьи про сегрегацию меди",
        message_metadata: { mode: "literature" },
        created_at: "2026-07-01T00:00:01Z",
      },
      {
        id: abstractsMessageId,
        session_id: sessionId,
        role: "assistant",
        content: "Нашёл несколько статей по теме (по аннотациям).",
        message_metadata: {
          search_id: searchId,
          litsearch_kind: "abstracts",
          mode_used: "literature",
        },
        created_at: "2026-07-01T00:00:05Z",
      },
    ]
    if (fulltextReady) {
      messages.push({
        id: fulltextMessageId,
        session_id: sessionId,
        role: "assistant",
        content: "Уточнённый ответ по полным текстам статей.",
        message_metadata: {
          search_id: searchId,
          litsearch_kind: "fulltext",
          mode_used: "literature",
        },
        created_at: "2026-07-01T00:01:00Z",
      })
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(messages),
    })
  })

  await page.route(
    `**/api/v1/chat/sessions/${sessionId}/messages`,
    async (route) => {
      if (route.request().method() !== "POST") {
        return route.fallback()
      }
      const payload = {
        claims: [],
        summary: "Нашёл несколько статей по теме (по аннотациям).",
        tools_used: ["litsearch"],
        subgraph: null,
        literature: { search_id: searchId, paper_count: 2 },
        session_id: sessionId,
        mode_used: "literature",
      }
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `data: ${JSON.stringify(payload)}\n\n`,
      })
    },
  )

  // Litsearch poll: first call still mid-flight (only the abstracts answer
  // exists yet); subsequent calls report the fulltext answer landed, which
  // flips `fulltextReady` above so the next history refetch includes it.
  let searchPollCount = 0
  await page.route(`**/api/v1/litsearch/${searchId}`, async (route) => {
    searchPollCount += 1
    const done = searchPollCount > 1
    if (done) {
      fulltextReady = true
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: searchId,
        stage: done ? "done" : "reading",
        round: 1,
        followup_search_id: null,
        papers: [paper()],
        answers: done
          ? [
              { message_id: abstractsMessageId, kind: "abstracts" },
              { message_id: fulltextMessageId, kind: "fulltext" },
            ]
          : [{ message_id: abstractsMessageId, kind: "abstracts" }],
      }),
    })
  })

  await page.goto("/chat")
  await page.getByText("Литературный поиск").click()

  await page.getByRole("tab", { name: "Литература" }).click()

  await page
    .getByPlaceholder("Type a message...")
    .fill("Найди статьи про сегрегацию меди")
  await page.getByRole("button", { name: "Send" }).click()

  // Third-column panel appears with the mocked papers.
  await expect(
    page.getByRole("heading", { name: "Найденные статьи" }),
  ).toBeVisible()

  // Mode-used badge reads the Russian label, not the raw "literature" string.
  await expect(page.getByText("Литература").first()).toBeVisible()
  await expect(page.getByText("literature", { exact: true })).toHaveCount(0)

  // Once the poll reports the fulltext answer, the thread shows it labeled.
  await expect(page.getByText("Ответ по полным текстам")).toBeVisible()
  await expect(page.getByText("Ответ по аннотациям")).toBeVisible()
})

test("switching to the follow-up round's search_id keeps the panel on the newest round", async ({
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
    if (route.request().method() !== "GET") {
      return route.fallback()
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: abstractsMessageId,
          session_id: sessionId,
          role: "assistant",
          content: "Нашёл несколько статей по теме.",
          message_metadata: {
            search_id: searchId,
            litsearch_kind: "abstracts",
            mode_used: "literature",
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
        followup_search_id: followupSearchId,
        papers: [paper()],
        answers: [{ message_id: abstractsMessageId, kind: "abstracts" }],
      }),
    })
  })

  await page.route(`**/api/v1/litsearch/${followupSearchId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: followupSearchId,
        stage: "done",
        round: 2,
        followup_search_id: null,
        papers: [paper({ id: "paper-2", title: "Follow-up round paper" })],
        answers: [],
      }),
    })
  })

  await page.goto("/chat")
  await page.getByText("Литературный поиск").click()

  // The panel should switch to the follow-up round's papers.
  await expect(page.getByText("Follow-up round paper")).toBeVisible()
})
