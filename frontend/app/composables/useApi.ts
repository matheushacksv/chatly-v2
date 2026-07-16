import type { FetchOptions } from 'ofetch'

/**
 * $fetch autenticado contra a API do chatly.
 * 401 → tenta refresh uma vez e repete; refresh falhou → logout.
 */
export function useApi() {
  const config = useRuntimeConfig()
  const { accessToken, refreshAccess, logout } = useAuth()

  function call<T>(path: string, opts: FetchOptions<'json'> = {}): Promise<T> {
    return $fetch<T>(path, {
      baseURL: config.public.apiBase,
      ...opts,
      headers: {
        ...(accessToken.value ? { Authorization: `Bearer ${accessToken.value}` } : {}),
        ...(opts.headers as Record<string, string> | undefined)
      }
    } as never)
  }

  return async function api<T>(path: string, opts: FetchOptions<'json'> = {}): Promise<T> {
    try {
      return await call<T>(path, opts)
    } catch (error) {
      const status = (error as { status?: number }).status
      if (status !== 401) throw error
      if (!(await refreshAccess())) {
        await logout()
        throw error
      }
      return call<T>(path, opts)
    }
  }
}

/** Extrai a mensagem de erro da API ({detail: str}) pra exibir em toast. */
export function apiErrorDetail(error: unknown, fallback = 'Algo deu errado'): string {
  const data = (error as { data?: { detail?: unknown } }).data
  return typeof data?.detail === 'string' ? data.detail : fallback
}
