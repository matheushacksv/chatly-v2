// Gravação de áudio do microfone via MediaRecorder. Usado pelo widget e pela
// página /meu-whatsapp. start() pede permissão e grava; stop() resolve um File
// pronto pro sendMedia (ou null se curto demais); cancel() descarta. Formato: prefere opus, cai no que o
// browser suportar (Chrome=webm, Firefox=ogg, Safari=mp4).
export function useAudioRecorder() {
  const recording = ref(false)
  const seconds = ref(0)
  const canRecord = ref(false)

  let mediaRecorder: MediaRecorder | null = null
  let chunks: Blob[] = []
  let stream: MediaStream | null = null
  let timer: ReturnType<typeof setInterval> | null = null
  let startedAt = 0

  // nota de voz abaixo de 1s chega no WhatsApp com duração 0:00 e não toca (o app
  // nativo também descarta); o contador em segundos é grosso demais pra decidir isso
  const MIN_MS = 1000

  onMounted(() => {
    canRecord.value = !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder)
  })

  function pickMime(): string {
    // ogg/mp4 primeiro: já aceitos pelo backend (MEDIA_SPECS). webm é fallback
    // do Chrome (só ele grava). Firefox->ogg, Safari->mp4, Chrome->webm.
    const prefs = ['audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm;codecs=opus', 'audio/webm']
    return prefs.find(m => MediaRecorder.isTypeSupported?.(m)) ?? ''
  }

  function cleanup() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    stream?.getTracks().forEach(t => t.stop())
    stream = null
    mediaRecorder = null
    recording.value = false
  }

  async function start() {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const mimeType = pickMime()
    mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    chunks = []
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data)
    }
    mediaRecorder.start()
    startedAt = Date.now()
    recording.value = true
    seconds.value = 0
    timer = setInterval(() => seconds.value++, 1000)
  }

  function stop(): Promise<File | null> {
    return new Promise((resolve, reject) => {
      const rec = mediaRecorder
      if (!rec) {
        reject(new Error('not recording'))
        return
      }
      if (Date.now() - startedAt < MIN_MS) {
        cancel()
        resolve(null)
        return
      }
      rec.onstop = () => {
        const type = (rec.mimeType || 'audio/webm').split(';')[0]!
        const ext = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'm4a' : 'webm'
        const file = new File([new Blob(chunks, { type })], `audio.${ext}`, { type })
        cleanup()
        resolve(file)
      }
      rec.stop()
    })
  }

  function cancel() {
    if (mediaRecorder) mediaRecorder.onstop = null
    mediaRecorder?.stop()
    chunks = []
    cleanup()
  }

  onUnmounted(cancel)

  return { recording, seconds, canRecord, start, stop, cancel }
}
