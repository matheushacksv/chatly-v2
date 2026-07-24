<script setup lang="ts">
import type { EvolutionAccount, EvolutionConnect } from '~/types/api'

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()
const { activeId: companyId, isOwner } = useCompany()

// conexão + inbox são estado compartilhado (mesmo do widget flutuante)
const {
  base,
  connection,
  contacts,
  selectedId,
  messages,
  selected,
  stickers,
  loadConnection,
  loadContacts,
  resetInbox,
  send: sendMessage,
  sendMedia,
  loadStickers,
  saveSticker,
  sendSticker,
  deleteSticker
} = useEvolutionChat()

// ---- conexão: QR/pair/polling é local à página ----
const qrcode = ref('')
const pairCode = ref('')
const mode = ref<'qr' | 'pair'>('qr')
const phone = ref('')
const connecting = ref(false)

const qrSrc = computed(() => {
  const q = qrcode.value
  if (!q) return ''
  return q.startsWith('data:') ? q : `data:image/png;base64,${q}`
})

// QR só fica em memória logo após conectar; num reload buscamos de novo pelo endpoint
async function fetchQr() {
  if (!connection.value) return
  try {
    const res = await api<{ qrcode: string }>(`${base.value}/${connection.value.id}/qrcode`)
    qrcode.value = res.qrcode
  } catch { /* QR pode não estar pronto ainda */ }
}

async function startConnect() {
  if (mode.value === 'pair' && !phone.value.trim()) {
    toast.add({ title: 'Informe o número com DDI (ex: 5548...)', color: 'error' })
    return
  }
  connecting.value = true
  try {
    const body = mode.value === 'pair' ? { phone: phone.value.trim() } : {}
    const res = await api<EvolutionConnect>(base.value, { method: 'POST', body })
    if (mode.value === 'pair') pairCode.value = res.pair_code
    else qrcode.value = res.qrcode
    await loadConnection()
    startPolling()
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao conectar'), color: 'error' })
  } finally {
    connecting.value = false
  }
}

async function disconnect() {
  if (!connection.value) return
  const ok = await confirmModal({
    title: 'Desconectar seu WhatsApp?',
    description: 'A sessão encerra e você precisa escanear o QR de novo pra reconectar.',
    confirmLabel: 'Desconectar'
  })
  if (!ok) return
  stopPolling()
  try {
    await api(`${base.value}/${connection.value.id}`, { method: 'DELETE' })
    resetInbox()
    connection.value = null
    qrcode.value = ''
    pairCode.value = ''
    toast.add({ title: 'Desconectado', icon: 'i-lucide-power' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

async function reconnect() {
  if (!connection.value) return
  try {
    await api(`${base.value}/${connection.value.id}`, { method: 'DELETE' })
  } catch { /* pode já estar deslogado no Evolution: segue */ }
  connection.value = null
  await startConnect()
}

// ---- polling enquanto conecta (QR expira; SSE também avisa, isto é o fallback) ----
let pollTimer: ReturnType<typeof setInterval> | null = null
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
const MAX_POLLS = 20 // ~60s: QR expira nesse tempo, não faz sentido pollar além disso
function startPolling() {
  stopPolling()
  let attempts = 0
  pollTimer = setInterval(async () => {
    attempts++
    try {
      await loadConnection()
      // QR fica pronto ~1s após o connect; se ainda não veio, busca até aparecer
      if (connection.value?.status === 'connecting' && !qrcode.value && !pairCode.value) {
        await fetchQr()
      }
    } catch { /* /me falhou: conta pro cap e segue */ }
    const status = connection.value?.status
    if (status === 'connected') {
      stopPolling()
      qrcode.value = ''
      pairCode.value = ''
      toast.add({ title: 'WhatsApp conectado', color: 'success', icon: 'i-lucide-check' })
      loadContacts()
    } else if (status === 'disconnected' || attempts >= MAX_POLLS) {
      // morta ou QR expirou: para de pollar (evita loop infinito martelando /me)
      stopPolling()
      qrcode.value = ''
      pairCode.value = ''
      toast.add({ title: 'Conexão expirou, gere o QR de novo', color: 'warning' })
    }
  }, 3000)
}

// ---- envio (thread da página; contatos/mensagens vêm do composable) ----
const draft = ref('')
const sending = ref(false)

// anexo: arquivo vira chip de preview, campo de texto vira legenda (mesmo padrão de conversas.vue)
const attachment = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const attachmentPreview = ref<string | null>(null)
watch(attachment, (file, _, onCleanup) => {
  attachmentPreview.value = file?.type.startsWith('image/') ? URL.createObjectURL(file) : null
  const url = attachmentPreview.value
  if (url) onCleanup(() => URL.revokeObjectURL(url))
})
function onFilePicked(event: Event) {
  const input = event.target as HTMLInputElement
  attachment.value = input.files?.[0] ?? null
  input.value = ''
}
const formatSize = (bytes: number) =>
  bytes < 2 ** 20 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 2 ** 20).toFixed(1)} MB`
const isPlaceholder = (content: string) => /^\[.+\]$/.test(content)

// nova conversa (modal compartilhado com o widget)
const newChatOpen = ref(false)

// figurinhas
const stickerPickerOpen = ref(false)
watch(stickerPickerOpen, open => open && loadStickers())
async function onSaveSticker(id: number) {
  try {
    await saveSticker(id)
    toast.add({ title: 'Figurinha salva', icon: 'i-lucide-sticker' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao salvar figurinha'), color: 'error' })
  }
}
async function onSendSticker(id: number) {
  if (sending.value) return
  sending.value = true
  stickerPickerOpen.value = false
  try {
    await sendSticker(id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao enviar figurinha'), color: 'error' })
  } finally {
    sending.value = false
  }
}
async function onDeleteSticker(id: number) {
  try {
    await deleteSticker(id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// gravação de áudio
const { recording, seconds, canRecord, start: startRec, stop: stopRec, cancel: cancelRec } = useAudioRecorder()
const recLabel = computed(() => `${Math.floor(seconds.value / 60)}:${String(seconds.value % 60).padStart(2, '0')}`)
async function startRecording() {
  try {
    await startRec()
  } catch {
    toast.add({ title: 'Não foi possível acessar o microfone', color: 'error' })
  }
}
async function stopAndSend() {
  if (sending.value) return
  sending.value = true
  try {
    await sendMedia(await stopRec(), '')
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao enviar áudio'), color: 'error' })
  } finally {
    sending.value = false
  }
}

const chatBody = ref<HTMLElement | null>(null)
watch(messages, () => nextTick(() => {
  const el = chatBody.value
  if (el) el.scrollTop = el.scrollHeight
}))

async function send() {
  const content = draft.value.trim()
  if ((!content && !attachment.value) || sending.value) return
  sending.value = true
  try {
    if (attachment.value) {
      await sendMedia(attachment.value, content)
      attachment.value = null
    } else {
      await sendMessage(content)
    }
    draft.value = ''
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao enviar'), color: 'error' })
  } finally {
    sending.value = false
  }
}

// ---- equipe (owner) ----
const team = ref<EvolutionAccount[]>([])
async function loadTeam() {
  if (!companyId.value || !isOwner.value) {
    team.value = []
    return
  }
  try {
    team.value = await api<EvolutionAccount[]>(base.value)
  } catch { team.value = [] }
}
async function disconnectTeam(acc: EvolutionAccount) {
  const ok = await confirmModal({
    title: `Desconectar ${acc.user_email}?`,
    description: 'A conexão pessoal desse atendente será encerrada.',
    confirmLabel: 'Desconectar'
  })
  if (!ok) return
  try {
    await api(`${base.value}/${acc.id}`, { method: 'DELETE' })
    team.value = team.value.filter(a => a.id !== acc.id)
    toast.add({ title: 'Conexão encerrada', icon: 'i-lucide-power' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// carga de conexão/inbox é do layout; aqui só limpa estado local + equipe
watch(companyId, () => {
  stopPolling()
  qrcode.value = ''
  pairCode.value = ''
  loadTeam()
}, { immediate: true })

watch(isOwner, loadTeam)

// reload no meio de uma conexão: retoma polling/QR
watch(() => connection.value?.status, (s) => {
  if (s === 'connecting' && !qrcode.value && !pairCode.value && !pollTimer) {
    fetchQr()
    startPolling()
  }
}, { immediate: true })

onUnmounted(stopPolling)

const statusColor = (s: string) => (s === 'connected' ? 'success' : s === 'connecting' ? 'warning' : 'error')
const statusLabel = (s: string) => (s === 'connected' ? 'Conectado' : s === 'connecting' ? 'Conectando' : 'Desconectado')
const time = (iso: string) => new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
</script>

<template>
  <UDashboardPanel
    id="meu-whatsapp"
    :resizable="connection?.status === 'connected'"
    :default-size="28"
    :min-size="20"
    :max-size="40"
  >
    <template #header>
      <UDashboardNavbar title="Meu WhatsApp">
        <template
          v-if="connection?.status === 'connected'"
          #right
        >
          <!-- equipe (owner): status das conexões, sem mensagens -->
          <UPopover v-if="isOwner && team.length">
            <UButton
              icon="i-lucide-users"
              color="neutral"
              variant="ghost"
              size="sm"
              aria-label="Conexões da equipe"
            />
            <template #content>
              <div class="p-2 w-80 space-y-1">
                <p class="text-xs font-semibold text-muted uppercase px-2 pb-1">
                  Conexões da equipe
                </p>
                <div
                  v-for="acc in team"
                  :key="acc.id"
                  class="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-elevated/60"
                >
                  <UIcon
                    name="i-lucide-user"
                    class="size-4 text-muted shrink-0"
                  />
                  <span class="flex-1 text-sm truncate">{{ acc.user_email }}</span>
                  <UBadge
                    :color="statusColor(acc.status)"
                    variant="subtle"
                    size="sm"
                  >
                    {{ statusLabel(acc.status) }}
                  </UBadge>
                  <UButton
                    icon="i-lucide-power"
                    variant="ghost"
                    color="error"
                    size="xs"
                    @click="disconnectTeam(acc)"
                  />
                </div>
              </div>
            </template>
          </UPopover>
          <UButton
            icon="i-lucide-power"
            color="error"
            variant="ghost"
            size="sm"
            aria-label="Desconectar"
            @click="disconnect"
          />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <!-- ===== CONECTADO: lista de contatos (estilo messenger) ===== -->
      <template v-if="connection?.status === 'connected'">
        <!-- número conectado -->
        <div class="-mx-4 -mt-2 mb-1 px-4 py-2 flex items-center gap-2 border-b border-default">
          <UIcon
            name="i-lucide-smartphone"
            class="size-4 text-success shrink-0"
          />
          <span class="text-sm font-medium truncate">{{ connection.phone_number || 'Sem número' }}</span>
          <UBadge
            :color="statusColor(connection.status)"
            variant="subtle"
            size="sm"
            class="ml-auto"
          >
            {{ statusLabel(connection.status) }}
          </UBadge>
          <UButton
            icon="i-lucide-message-square-plus"
            color="neutral"
            variant="ghost"
            size="xs"
            aria-label="Nova conversa"
            @click="newChatOpen = true"
          />
        </div>

        <p
          v-if="!contacts.length"
          class="p-4 text-sm text-muted text-center"
        >
          Nenhuma conversa ainda. Comece uma em "Nova conversa", ou espere alguém te mandar mensagem.
        </p>

        <div
          v-else
          class="-mx-4"
        >
          <button
            v-for="contact in contacts"
            :key="contact.id"
            class="w-full text-left px-3 py-2.5 flex items-center gap-3 border-l-2 transition-colors"
            :class="contact.id === selectedId
              ? 'border-primary bg-primary/5'
              : 'border-transparent hover:bg-elevated/60'"
            @click="selectedId = contact.id"
          >
            <UAvatar
              :src="contact.avatar_url || undefined"
              :alt="contact.name || contact.wa_id"
              size="md"
            />
            <div class="flex-1 min-w-0">
              <span class="font-medium truncate block">{{ contact.name || contact.wa_id }}</span>
              <span class="text-[11px] text-muted font-mono truncate block">{{ contact.wa_id }}</span>
            </div>
          </button>
        </div>
      </template>

      <!-- ===== NÃO CONECTADO: gestão da conexão ===== -->
      <div
        v-else
        class="max-w-3xl space-y-8"
      >
        <!-- risco de banimento: número pessoal via WhatsApp Web não-oficial -->
        <UAlert
          icon="i-lucide-triangle-alert"
          title="Conexão pessoal, use com moderação"
          description="É o seu número pessoal via WhatsApp Web. Só conversa manual 1:1, sem IA e sem disparo em massa - automação aumenta o risco de bloqueio pela Meta."
          variant="subtle"
          color="warning"
        />

        <section class="space-y-3">
          <h2 class="font-display font-semibold text-lg">
            Minha conexão
          </h2>

          <!-- sem conexão -->
          <div
            v-if="!connection"
            class="rounded-lg ring-1 ring-default p-6 flex flex-col items-center gap-3 text-center"
          >
            <UIcon
              name="i-lucide-smartphone"
              class="size-8 text-muted"
            />
            <p class="text-sm text-muted">
              Conecte seu WhatsApp pra atender pelo seu número pessoal.
            </p>
            <div class="flex gap-1 rounded-lg bg-elevated p-1">
              <UButton
                label="QR Code"
                :variant="mode === 'qr' ? 'solid' : 'ghost'"
                color="neutral"
                size="xs"
                @click="() => { mode = 'qr' }"
              />
              <UButton
                label="Código"
                :variant="mode === 'pair' ? 'solid' : 'ghost'"
                color="neutral"
                size="xs"
                @click="() => { mode = 'pair' }"
              />
            </div>
            <UInput
              v-if="mode === 'pair'"
              v-model="phone"
              placeholder="Número com DDI (ex: 5548999999999)"
              class="w-full max-w-xs"
            />
            <UButton
              :label="mode === 'pair' ? 'Gerar código' : 'Conectar'"
              :icon="mode === 'pair' ? 'i-lucide-key-round' : 'i-lucide-qr-code'"
              :loading="connecting"
              @click="startConnect"
            />
          </div>

          <!-- conectando: mostra QR/código -->
          <div
            v-else-if="connection.status === 'connecting'"
            class="rounded-lg ring-1 ring-default p-6 flex flex-col items-center gap-3 text-center"
          >
            <template v-if="pairCode">
              <p class="text-sm text-muted">
                No WhatsApp &gt; Aparelhos conectados &gt; Conectar com número de telefone, digite:
              </p>
              <p class="text-2xl font-mono font-bold tracking-widest">
                {{ pairCode }}
              </p>
            </template>
            <template v-else-if="qrSrc">
              <img
                :src="qrSrc"
                alt="QR Code"
                class="size-56 rounded bg-white p-2"
              >
              <p class="text-sm text-muted">
                Abra o WhatsApp &gt; Aparelhos conectados &gt; Conectar aparelho e escaneie.
              </p>
            </template>
            <template v-else>
              <UIcon
                name="i-lucide-loader-circle"
                class="size-8 text-muted animate-spin"
              />
              <p class="text-sm text-muted">
                Conectando...
              </p>
            </template>
            <UButton
              label="Cancelar"
              variant="ghost"
              color="neutral"
              size="xs"
              @click="disconnect"
            />
          </div>

          <!-- desconectado -->
          <div
            v-else
            class="rounded-lg ring-1 ring-default px-4 py-3 flex items-center gap-3"
          >
            <UIcon
              name="i-lucide-smartphone"
              class="size-5 text-muted"
            />
            <div class="flex-1">
              <div class="flex items-center gap-2">
                <span class="font-medium">{{ connection.phone_number || 'Sem número' }}</span>
                <UBadge
                  :color="statusColor(connection.status)"
                  variant="subtle"
                  size="sm"
                >
                  {{ statusLabel(connection.status) }}
                </UBadge>
              </div>
            </div>
            <UButton
              label="Reconectar"
              icon="i-lucide-refresh-cw"
              size="xs"
              @click="reconnect"
            />
            <UButton
              label="Desconectar"
              variant="ghost"
              color="error"
              size="xs"
              @click="disconnect"
            />
          </div>
        </section>

        <!-- conexões da equipe (owner) -->
        <template v-if="isOwner && team.length">
          <USeparator />
          <section class="space-y-3">
            <div>
              <h2 class="font-display font-semibold text-lg">
                Conexões da equipe
              </h2>
              <p class="text-sm text-muted">
                Você vê o status das conexões pessoais da equipe, mas não as mensagens.
              </p>
            </div>
            <div class="space-y-2">
              <div
                v-for="acc in team"
                :key="acc.id"
                class="flex items-center gap-3 rounded-lg ring-1 ring-default px-4 py-3"
              >
                <UIcon
                  name="i-lucide-user"
                  class="size-4 text-muted"
                />
                <span class="flex-1 text-sm font-medium truncate">{{ acc.user_email }}</span>
                <span class="text-sm text-muted">{{ acc.phone_number || '-' }}</span>
                <UBadge
                  :color="statusColor(acc.status)"
                  variant="subtle"
                  size="sm"
                >
                  {{ statusLabel(acc.status) }}
                </UBadge>
                <UButton
                  icon="i-lucide-power"
                  variant="ghost"
                  color="error"
                  size="xs"
                  @click="disconnectTeam(acc)"
                />
              </div>
            </div>
          </section>
        </template>
      </div>

      <AppEvolutionNewChat v-model:open="newChatOpen" />
    </template>
  </UDashboardPanel>

  <UDashboardPanel
    v-if="connection?.status === 'connected'"
    id="mw-chat"
  >
    <template #header>
      <UDashboardNavbar :title="selected ? undefined : 'Conversa'">
        <template
          v-if="selected"
          #title
        >
          <div class="flex items-center gap-2.5 min-w-0">
            <UAvatar
              :src="selected.avatar_url || undefined"
              :alt="selected.name || selected.wa_id"
              size="sm"
            />
            <div class="min-w-0 leading-tight">
              <p class="font-medium text-sm truncate">
                {{ selected.name || selected.wa_id }}
              </p>
              <p class="text-[11px] text-muted font-mono truncate">
                {{ selected.wa_id }}
              </p>
            </div>
          </div>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div
        v-if="!selected"
        class="flex-1 flex flex-col items-center justify-center gap-2 text-muted"
      >
        <UIcon
          name="i-lucide-messages-square"
          class="size-10"
        />
        <p class="text-sm">
          Selecione uma conversa
        </p>
      </div>

      <template v-else>
        <div
          ref="chatBody"
          class="flex-1 overflow-y-auto space-y-1.5 py-2"
        >
          <div
            v-for="message in messages"
            :key="message.id"
            class="flex"
            :class="message.direction === 'out' ? 'justify-end' : 'justify-start'"
          >
            <UAvatar
              v-if="message.direction === 'in'"
              :src="selected.avatar_url || undefined"
              :alt="selected.name || selected.wa_id"
              size="3xs"
              class="self-end mr-1.5 shrink-0"
            />
            <div
              class="max-w-[75%] rounded-2xl px-3.5 py-2 text-sm shadow-sm"
              :class="message.direction === 'out'
                ? 'bg-primary text-inverted rounded-br-sm'
                : 'bg-elevated rounded-bl-sm'"
            >
              <ChatMedia
                v-if="message.message_type !== 'text'"
                :message="message"
                :company-id="companyId ?? 0"
                :proxy="false"
              />
              <UButton
                v-if="message.message_type === 'sticker' && message.direction === 'in'"
                label="Salvar"
                icon="i-lucide-bookmark-plus"
                variant="ghost"
                color="neutral"
                size="xs"
                @click="onSaveSticker(message.id)"
              />
              <p
                v-if="message.message_type === 'text' || !isPlaceholder(message.content)"
                class="whitespace-pre-wrap break-words"
                :class="message.message_type !== 'text' ? 'mt-1' : ''"
              >
                {{ message.content }}
              </p>
              <span class="block text-[10px] opacity-60 mt-0.5 text-right">{{ time(message.created_at) }}</span>
            </div>
          </div>
        </div>

        <div
          v-if="attachment"
          class="flex items-center gap-2 px-1 pt-2"
        >
          <img
            v-if="attachmentPreview"
            :src="attachmentPreview"
            class="size-9 rounded object-cover ring-1 ring-default"
          >
          <UIcon
            v-else
            name="i-lucide-file"
            class="size-5 text-muted shrink-0"
          />
          <div class="min-w-0 text-xs flex-1">
            <p class="truncate font-medium">
              {{ attachment.name }}
            </p>
            <p class="text-muted">
              {{ formatSize(attachment.size) }}
            </p>
          </div>
          <UButton
            icon="i-lucide-x"
            size="xs"
            variant="ghost"
            color="neutral"
            aria-label="Remover anexo"
            @click="attachment = null"
          />
        </div>
        <form
          class="flex items-center gap-2 pt-2 border-t border-default"
          @submit.prevent="send"
        >
          <template v-if="recording">
            <UButton
              icon="i-lucide-trash-2"
              color="error"
              variant="ghost"
              size="lg"
              class="rounded-full shrink-0"
              aria-label="Cancelar gravação"
              @click="cancelRec"
            />
            <div class="flex-1 flex items-center gap-2 text-sm">
              <span class="size-2.5 rounded-full bg-error animate-pulse" />
              <span class="font-mono tabular-nums">{{ recLabel }}</span>
            </div>
            <UButton
              icon="i-lucide-send"
              size="lg"
              class="rounded-full shrink-0"
              :loading="sending"
              aria-label="Enviar áudio"
              @click="stopAndSend"
            />
          </template>
          <template v-else>
            <input
              ref="fileInput"
              type="file"
              class="hidden"
              @change="onFilePicked"
            >
            <UButton
              icon="i-lucide-paperclip"
              variant="ghost"
              color="neutral"
              size="lg"
              class="rounded-full shrink-0"
              aria-label="Anexar arquivo"
              @click="fileInput?.click()"
            />
            <UPopover v-model:open="stickerPickerOpen">
              <UButton
                icon="i-lucide-sticker"
                variant="ghost"
                color="neutral"
                size="lg"
                class="rounded-full shrink-0"
                aria-label="Figurinhas"
              />
              <template #content>
                <div class="p-3 w-72">
                  <p
                    v-if="!stickers.length"
                    class="text-sm text-muted"
                  >
                    Nenhuma figurinha salva. Passe o mouse numa figurinha recebida e clique em "Salvar".
                  </p>
                  <div
                    v-else
                    class="grid grid-cols-4 gap-2"
                  >
                    <div
                      v-for="sticker in stickers"
                      :key="sticker.id"
                      class="relative group"
                    >
                      <button
                        type="button"
                        class="cursor-pointer"
                        @click="onSendSticker(sticker.id)"
                      >
                        <img
                          :src="sticker.url"
                          class="size-14 object-contain"
                          alt="Figurinha"
                        >
                      </button>
                      <UButton
                        icon="i-lucide-x"
                        size="xs"
                        color="error"
                        variant="solid"
                        class="absolute -top-1 -right-1 rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
                        aria-label="Excluir figurinha"
                        @click="onDeleteSticker(sticker.id)"
                      />
                    </div>
                  </div>
                </div>
              </template>
            </UPopover>
            <UButton
              v-if="canRecord && !attachment && !draft.trim()"
              icon="i-lucide-mic"
              variant="ghost"
              color="neutral"
              size="lg"
              class="rounded-full shrink-0"
              aria-label="Gravar áudio"
              @click="startRecording"
            />
            <UInput
              v-model="draft"
              :placeholder="attachment ? 'Legenda (opcional)...' : 'Mensagem...'"
              class="flex-1"
              :ui="{ base: 'rounded-full' }"
              autocomplete="off"
            />
            <UButton
              type="submit"
              icon="i-lucide-send"
              :loading="sending"
              :disabled="!draft.trim() && !attachment"
            />
          </template>
        </form>
      </template>
    </template>
  </UDashboardPanel>
</template>
