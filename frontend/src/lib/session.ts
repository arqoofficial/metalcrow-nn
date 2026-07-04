import { redirect } from "@tanstack/react-router"

import { ApiError, UsersService } from "@/client"

const ACCESS_TOKEN_KEY = "access_token"

export function isLoggedIn(): boolean {
  return localStorage.getItem(ACCESS_TOKEN_KEY) !== null
}

export function clearSession(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
}

export function isSessionInvalidError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if ([401, 403].includes(error.status)) return true
  if (error.status === 404 && error.url.includes("/users/me")) return true
  return false
}

export async function ensureAuthenticated(): Promise<void> {
  if (!isLoggedIn()) {
    throw redirect({ to: "/login" })
  }
  try {
    await UsersService.readUserMe()
  } catch (error) {
    if (isSessionInvalidError(error)) {
      clearSession()
      throw redirect({ to: "/login" })
    }
    throw error
  }
}

export async function ensureSuperuser() {
  try {
    const user = await UsersService.readUserMe()
    if (!user.is_superuser) {
      throw redirect({ to: "/" })
    }
    return user
  } catch (error) {
    if (isSessionInvalidError(error)) {
      clearSession()
      throw redirect({ to: "/login" })
    }
    throw error
  }
}

export async function redirectIfAuthenticated(): Promise<void> {
  if (!isLoggedIn()) return
  try {
    await UsersService.readUserMe()
    throw redirect({ to: "/" })
  } catch (error) {
    if (isSessionInvalidError(error)) {
      clearSession()
      return
    }
    throw error
  }
}
