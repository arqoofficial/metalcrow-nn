import { expect, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { createUser } from "./utils/privateApi"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser } from "./utils/user"

test("Chat page renders", async ({ page }) => {
  await page.goto("/chat")
  await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible()
})

test("Wiki page renders", async ({ page }) => {
  await page.goto("/wiki")
  await expect(page.getByRole("heading", { name: "Wiki" })).toBeVisible()
})

test("Graph page renders", async ({ page }) => {
  await page.goto("/graph")
  await expect(page.getByRole("heading", { name: "Graph" })).toBeVisible()
})

test.describe("Ingest page access control", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Non-superuser cannot access ingest page", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()

    await createUser({ email, password })
    await logInUser(page, email, password)

    await page.goto("/ingest")

    await expect(
      page.getByRole("heading", { name: "Ingest" }),
    ).not.toBeVisible()
    await expect(page).not.toHaveURL(/\/ingest/)
  })

  test("Superuser can access ingest page", async ({ page }) => {
    await logInUser(page, firstSuperuser, firstSuperuserPassword)

    await page.goto("/ingest")

    await expect(page.getByRole("heading", { name: "Ingest" })).toBeVisible()
  })
})
