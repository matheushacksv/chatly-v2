import type { TokenPair, User } from '~/types/api'

// ponytail: tokens em localStorage (risco XSS aceito no MVP; upgrade = cookie httpOnly via BFF)
const ACCESS_KEY = 'chatly_access'
const REFRESH_KEY = 'chatly_refresh'
const USER_KEY = 'chatly_user'

export function useAuth() {
  const config = useRuntimeConfig()
  const accessToken = useState<string | null>('auth:access', () => localStorage.getItem(ACCESS_KEY))
  const user = useState<User | null>('auth:user', () => {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  })

  async function login(email: string, password: string) {
    const tokens = await $fetch<TokenPair>('/auth/login', {
      baseURL: config.public.apiBase,
      method: 'POST',
      body: { email, password }
    })
    accessToken.value = tokens.access
    user.value = tokens.user
    localStorage.setItem(ACCESS_KEY, tokens.access)
    localStorage.setItem(REFRESH_KEY, tokens.refresh)
    localStorage.setItem(USER_KEY, JSON.stringify(tokens.user))
  }

  async function register(email: string, password: string, repeatPassword: string, name: string) {
    await $fetch('/auth/register', {
      baseURL: config.public.apiBase,
      method: 'POST',
      body: { email, password, repeat_password: repeatPassword, name }
    })
    await login(email, password)
  }

  /** Renova o access token. Retorna false se o refresh expirou (→ logout). */
  async function refreshAccess(): Promise<boolean> {
    const refresh = localStorage.getItem(REFRESH_KEY)
    if (!refresh) return false
    try {
      const { access } = await $fetch<{ access: string }>('/auth/refresh', {
        baseURL: config.public.apiBase,
        method: 'POST',
        body: { refresh }
      })
      accessToken.value = access
      localStorage.setItem(ACCESS_KEY, access)
      return true
    } catch {
      return false
    }
  }

  function logout() {
    accessToken.value = null
    user.value = null
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
    localStorage.removeItem(USER_KEY)
    return navigateTo('/login')
  }

  return { accessToken, user, login, register, refreshAccess, logout }
}
