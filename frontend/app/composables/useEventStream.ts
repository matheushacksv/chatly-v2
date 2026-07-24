interface StreamHandlers {
  onEvent: (event: { type: string, contact_id: number, name?: string, channel?: string, account_id?: number, status?: string }) => void
  /** Chamado quando a conexão REabre - hora de re-buscar o que se perdeu offline. */
  onReconnect?: () => void
}

/**
 * SSE via fetch streaming (EventSource nativo não manda header Authorization).
 * Reconecta sozinho com backoff exponencial; 401 → refresh do token → reconecta.
 * Retorna função de disconnect.
 */
export function useEventStream() {
  const config = useRuntimeConfig()
  const { accessToken, refreshAccess, logout } = useAuth()

  function connect(path: string, handlers: StreamHandlers) {
    let stopped = false
    let controller: AbortController | null = null
    let attempt = 0

    async function run() {
      while (!stopped) {
        try {
          controller = new AbortController()
          const response = await fetch(`${config.public.apiBase}${path}`, {
            headers: { Authorization: `Bearer ${accessToken.value}` },
            signal: controller.signal
          })

          if (response.status === 401) {
            if (await refreshAccess()) continue
            await logout()
            return
          }
          if (!response.ok || !response.body) throw new Error(`stream ${response.status}`)

          if (attempt > 0) handlers.onReconnect?.()
          attempt = 0

          const reader = response.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''
          for (;;) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            // eventos SSE terminam em linha em branco; linhas ":..." são keepalive
            let separator
            while ((separator = buffer.indexOf('\n\n')) !== -1) {
              const rawEvent = buffer.slice(0, separator)
              buffer = buffer.slice(separator + 2)
              const data = rawEvent
                .split('\n')
                .filter(line => line.startsWith('data:'))
                .map(line => line.slice(5).trim())
                .join('')
              if (data) {
                try {
                  handlers.onEvent(JSON.parse(data))
                } catch { /* payload inválido: ignora, evento é só dica */ }
              }
            }
          }
          throw new Error('stream encerrado pelo servidor')
        } catch {
          if (stopped) return
          attempt++
          await new Promise(resolve => setTimeout(resolve, Math.min(1000 * 2 ** attempt, 15000)))
        }
      }
    }

    run()

    return () => {
      stopped = true
      controller?.abort()
    }
  }

  return { connect }
}
