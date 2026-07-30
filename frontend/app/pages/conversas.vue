<script setup lang="ts">
import type { Contact, Message, Paginated, Sticker, Template, WhatsAppAccount } from '~/types/api'

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()
const { activeId: companyId, isOwner } = useCompany()

// ---- contas WhatsApp ----
const { data: accounts } = await useAsyncData(
  () => `accounts:${companyId.value}`,
  () => api<WhatsAppAccount[]>(`/companies/${companyId.value}/whatsapp-accounts`),
  { watch: [companyId], default: () => [] as WhatsAppAccount[] }
)
const accountId = ref<number | null>(null)
watch(accounts, list => { accountId.value = list?.[0]?.id ?? null }, { immediate: true })

// ---- contatos ----
const contacts = ref<Contact[]>([])
const search = ref('')
const selectedId = ref<number | null>(null)
const selected = computed(() => contacts.value.find(c => c.id === selectedId.value) ?? null)

// conversa = contato com mensagem; mudo (importado/recém-criado) só aparece enquanto selecionado
const filteredContacts = computed(() => {
  const q = search.value.toLowerCase()
  return contacts.value.filter(c =>
    (c.last_message_at || c.id === selectedId.value)
    && (!q || c.name.toLowerCase().includes(q) || c.wa_id.includes(q))
  )
})

async function loadContacts() {
  if (!companyId.value || !accountId.value) {
    contacts.value = []
    return
  }
  const page = await api<Paginated<Contact>>(
    `/companies/${companyId.value}/whatsapp-accounts/${accountId.value}/contacts?limit=100`
  )
  const items = [...page.items]
  // contato aberto sem mensagem pode estar fora da página (nulls_last) - preserva ele
  const current = contacts.value.find(c => c.id === selectedId.value)
  if (current && !items.some(c => c.id === current.id)) items.push(current)
  contacts.value = items
}
watch(accountId, () => { selectedId.value = null; loadContacts() }, { immediate: true })

// ---- mensagens ----
// API devolve desc (recente primeiro); exibimos asc → sempre inverter a página.
// Scroll no topo puxa páginas mais antigas (estilo WhatsApp Web).
const PAGE_SIZE = 20
const messages = ref<Message[]>([])
const totalMessages = ref(0)
const loadingOlder = ref(false)
const chatBody = ref<HTMLElement | null>(null)

const hasOlder = computed(() => messages.value.length < totalMessages.value)

function scrollToBottom() {
  nextTick(() => { chatBody.value?.scrollTo({ top: chatBody.value.scrollHeight }) })
}

function fetchPage(offset: number) {
  return api<Paginated<Message>>(
    `/companies/${companyId.value}/contacts/${selectedId.value}/messages?limit=${PAGE_SIZE}&offset=${offset}`
  )
}

// página 0 = mais recentes; usado na carga inicial e no polling
async function loadMessages() {
  if (!selectedId.value) return
  const page = await fetchPage(0)
  totalMessages.value = page.count
  const fresh = [...page.items].reverse()
  if (!messages.value.length) {
    messages.value = fresh
    scrollToBottom()
    maybeFillViewport()
    return
  }
  const known = new Set(messages.value.map(m => m.id))
  const news = fresh.filter(m => !known.has(m.id))
  if (news.length) {
    // só puxa pro fim se o usuário já está perto dele - lendo histórico, fica onde está
    const el = chatBody.value
    const nearBottom = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 150
    messages.value.push(...news)
    if (nearBottom) scrollToBottom()
  }
}

async function loadOlder() {
  if (loadingOlder.value || !hasOlder.value || !selectedId.value) return
  loadingOlder.value = true
  try {
    // offset = quantas já temos: msgs novas deslocam a janela → overlap, dedupe resolve
    const page = await fetchPage(messages.value.length)
    totalMessages.value = page.count
    const known = new Set(messages.value.map(m => m.id))
    const older = [...page.items].reverse().filter(m => !known.has(m.id))
    if (!older.length) return
    const el = chatBody.value
    const prevHeight = el?.scrollHeight ?? 0
    messages.value.unshift(...older)
    // manter viewport na mesma mensagem após o prepend
    nextTick(() => {
      if (el) el.scrollTop += el.scrollHeight - prevHeight
    })
  } finally {
    loadingOlder.value = false
  }
  maybeFillViewport()
}

// página curta pode não gerar scrollbar → sem evento de scroll, antigas inacessíveis;
// carrega mais até encher a tela ou acabar o histórico
function maybeFillViewport() {
  nextTick(() => {
    const el = chatBody.value
    if (el && el.scrollHeight <= el.clientHeight && hasOlder.value) loadOlder()
  })
}

function onChatScroll() {
  if ((chatBody.value?.scrollTop ?? Infinity) < 100) loadOlder()
}

watch(selectedId, () => {
  messages.value = []
  totalMessages.value = 0
  loadMessages()
})

// ---- notificações ----
const notifPermission = ref<NotificationPermission>('denied')
onMounted(() => {
  if ('Notification' in window) notifPermission.value = Notification.permission
})
async function enableNotifications() {
  notifPermission.value = await Notification.requestPermission()
}

// tempo real: stream vive no layout (notifica em qualquer página);
// aqui só consumimos os eventos pra atualizar chat e lista
const { lastEvent, openContact, openContactData } = useChatEvents()

watch(lastEvent, (entry) => {
  if (!entry) return
  const event = entry.event
  // eventos de conexão pessoal (Evolution) têm tabela/endpoints próprios - ignora aqui
  if (event.channel === 'evolution' || event.type === 'evolution_status') return
  if (event.type === 'reconnect') {
    // ficou offline: catch-up do que perdeu
    loadMessages()
    loadContacts()
    return
  }
  if (event.type === 'message') {
    if (event.contact_id === selectedId.value) {
      loadMessages()
    } else {
      // só notifica conversa em atendimento humano - IA ativa cuida sozinha
      const contact = contacts.value.find(c => c.id === event.contact_id)
      if (contact?.ai_paused) {
        toast.add({
          title: `Nova mensagem de ${contact.name || contact.wa_id}`,
          icon: 'i-lucide-message-circle',
          actions: [{ label: 'Abrir', onClick: () => { selectedId.value = event.contact_id } }]
        })
      }
    }
  }
  loadContacts() // badges de pausa, contatos novos, ordem
})

// notificação clicada (em qualquer página) ou "Conversar" da página de contatos
watch(openContact, (id) => {
  if (id !== null) {
    // contato sem mensagem não vem na lista de conversas - usa o objeto entregue
    if (openContactData.value?.id === id && !contacts.value.some(c => c.id === id)) {
      contacts.value.unshift(openContactData.value)
    }
    openContactData.value = null
    selectedId.value = id
    openContact.value = null
  }
}, { immediate: true })

// ---- envio manual ----
const draft = ref('')
const sending = ref(false)

// anexo: arquivo escolhido vira chip de preview; campo de texto vira legenda
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
  input.value = '' // permite re-escolher o mesmo arquivo
}

const formatSize = (bytes: number) =>
  bytes < 2 ** 20 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 2 ** 20).toFixed(1)} MB`

async function send() {
  const content = draft.value.trim()
  if ((!content && !attachment.value) || !selected.value || sending.value) return
  sending.value = true
  try {
    let message: Message
    if (attachment.value) {
      const form = new FormData()
      form.append('file', attachment.value)
      if (content) form.append('caption', content)
      message = await api<Message>(
        `/companies/${companyId.value}/contacts/${selected.value.id}/messages/media`,
        { method: 'POST', body: form }
      )
      attachment.value = null
    } else {
      message = await api<Message>(
        `/companies/${companyId.value}/contacts/${selected.value.id}/messages`,
        { method: 'POST', body: { content } }
      )
    }
    messages.value.push(message)
    draft.value = ''
    scrollToBottom()
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao enviar'), color: 'error' })
  } finally {
    sending.value = false
  }
}

// ---- figurinhas ----
const stickers = ref<Sticker[]>([])
const stickerPickerOpen = ref(false)

watch(stickerPickerOpen, (open) => {
  if (open) loadStickers()
})

async function loadStickers() {
  stickers.value = await api<Sticker[]>(`/companies/${companyId.value}/stickers`)
}

async function saveSticker(message: Message) {
  try {
    await api(`/companies/${companyId.value}/stickers`, { method: 'POST', body: { message_id: message.id } })
    toast.add({ title: 'Figurinha salva', icon: 'i-lucide-sticker' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao salvar figurinha'), color: 'error' })
  }
}

async function sendSticker(sticker: Sticker) {
  if (!selected.value || sending.value) return
  sending.value = true
  stickerPickerOpen.value = false
  try {
    const message = await api<Message>(
      `/companies/${companyId.value}/contacts/${selected.value.id}/messages/sticker`,
      { method: 'POST', body: { sticker_id: sticker.id } }
    )
    messages.value.push(message)
    scrollToBottom()
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao enviar figurinha'), color: 'error' })
  } finally {
    sending.value = false
  }
}

async function deleteSticker(id: number) {
  try {
    await api(`/companies/${companyId.value}/stickers/${id}`, { method: 'DELETE' })
    stickers.value = stickers.value.filter(s => s.id !== id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// ---- template (janela de 24h) ----
// Meta só aceita mensagem livre até 24h após a última msg DO CLIENTE; depois, só template.
// Janela fechada = composer some (backend também bloqueia - a Meta aceita o POST e falha silenciosa depois).
const WINDOW_MS = 24 * 3600 * 1000
const windowClosed = computed(() =>
  Boolean(selected.value
    && (!selected.value.last_inbound_at
      || Date.now() - new Date(selected.value.last_inbound_at).getTime() > WINDOW_MS))
)

const templateModalOpen = ref(false)
const chosenTemplate = ref<Template | null>(null)
const templateParams = ref<string[]>([])
const templateHeaderMediaId = ref('')

const templateMissingReasons = computed(() => {
  if (!chosenTemplate.value) return []
  const reasons: string[] = []
  if (!templateParams.value.every(p => p.trim())) reasons.push('preencha todas as variáveis do corpo')
  if (
    ['IMAGE', 'VIDEO', 'DOCUMENT'].includes(chosenTemplate.value.header_format)
    && !chosenTemplate.value.has_header_media
    && !templateHeaderMediaId.value
  ) {
    reasons.push('envie o arquivo do cabeçalho')
  }
  return reasons
})
const canSendTemplate = computed(() => Boolean(chosenTemplate.value) && templateMissingReasons.value.length === 0)

async function sendTemplate() {
  if (!canSendTemplate.value || !chosenTemplate.value || !selected.value || sending.value) return
  sending.value = true
  try {
    const message = await api<Message>(
      `/companies/${companyId.value}/contacts/${selected.value.id}/messages/template`,
      {
        method: 'POST',
        body: {
          template_name: chosenTemplate.value.name,
          language: chosenTemplate.value.language,
          params: templateParams.value,
          header_media_id: templateHeaderMediaId.value
        }
      }
    )
    messages.value.push(message)
    templateModalOpen.value = false
    scrollToBottom()
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao enviar template'), color: 'error' })
  } finally {
    sending.value = false
  }
}

// ---- nova conversa ----
const newChatOpen = ref(false)
const newChatNumber = ref('')
const newChatName = ref('')
const creatingContact = ref(false)

async function createContact() {
  if (!newChatNumber.value.trim() || !accountId.value || creatingContact.value) return
  creatingContact.value = true
  try {
    const contact = await api<Contact>(
      `/companies/${companyId.value}/whatsapp-accounts/${accountId.value}/contacts`,
      { method: 'POST', body: { number: newChatNumber.value, name: newChatName.value.trim() } }
    )
    // número já existente devolve o contato existente - só abre a conversa
    if (!contacts.value.some(c => c.id === contact.id)) contacts.value.unshift(contact)
    selectedId.value = contact.id
    newChatOpen.value = false
    newChatNumber.value = ''
    newChatName.value = ''
    // contato novo = janela fechada: só template chega; fechar o modal é opcional
    templateModalOpen.value = true
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Número inválido'), color: 'error' })
  } finally {
    creatingContact.value = false
  }
}

// ---- handoff / memória ----
async function setPaused(paused: boolean) {
  if (!selected.value) return
  try {
    const updated = await api<Contact>(
      `/companies/${companyId.value}/contacts/${selected.value.id}`,
      { method: 'PATCH', body: { ai_paused: paused } }
    )
    contacts.value = contacts.value.map(c => (c.id === updated.id ? updated : c))
    toast.add({
      title: paused ? 'IA pausada - você assume a conversa' : 'IA reativada',
      color: paused ? 'warning' : 'success',
      icon: paused ? 'i-lucide-pause' : 'i-lucide-play'
    })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

async function clearMemory() {
  if (!selected.value) return
  try {
    await api(`/companies/${companyId.value}/contacts/${selected.value.id}/memory`, { method: 'DELETE' })
    contacts.value = contacts.value.map(c => (c.id === selectedId.value ? { ...c, memory: '' } : c))
    toast.add({ title: 'Memória apagada', icon: 'i-lucide-eraser' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// ---- apagar conversa (só dono) ----
// remove contato + histórico do Chatly; não mexe no WhatsApp do cliente (é só o registro local)
async function deleteConversation(contact: Contact) {
  const ok = await confirmModal({
    title: 'Apagar conversa',
    description: `A conversa com ${contact.name || contact.wa_id} e todo o histórico somem do Chatly. Não afeta o WhatsApp do cliente.`,
    confirmLabel: 'Apagar'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}/contacts/${contact.id}`, { method: 'DELETE' })
    contacts.value = contacts.value.filter(c => c.id !== contact.id)
    if (selectedId.value === contact.id) selectedId.value = null
    toast.add({ title: 'Conversa apagada', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao apagar'), color: 'error' })
  }
}

// ---- painel de detalhes ----
const detailOpen = ref(false)

// ---- abrir no meu WhatsApp (canal pessoal / evolution) ----
// reusa o estado global do useEvolutionChat: startConversation seta o selecionado,
// o watcher do layout carrega a thread, e navegar pra /meu-whatsapp já mostra a conversa.
const { connected: evoConnected, startConversation: startEvoConversation, widgetOpen: evoWidgetOpen } = useEvolutionChat()
const openingEvo = ref(false)
async function openInEvolution() {
  if (!selected.value || openingEvo.value) return
  openingEvo.value = true
  try {
    await startEvoConversation(selected.value.wa_id, selected.value.name)
    evoWidgetOpen.value = true
  } catch (error) {
    const detail = apiErrorDetail(error, '')
    toast.add({
      title: detail.includes('not on whatsapp') ? 'Esse número não tem WhatsApp' : detail || 'Não foi possível abrir no seu WhatsApp',
      color: 'error'
    })
  } finally {
    openingEvo.value = false
  }
}

// ---- helpers ----
const timeFmt = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' })
const dateFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
const dayFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: 'long', year: 'numeric' })
const time = (iso: string) => timeFmt.format(new Date(iso))
const fullDate = (iso: string) => dateFmt.format(new Date(iso))

// caption real aparece sob a mídia; placeholder ([imagem], [vídeo]...) não repete
const isPlaceholder = (message: Message) => /^\[.+\]$/.test(message.content)

// separadores de dia entre bolhas
const dayKey = (iso: string) => new Date(iso).toDateString()
const showDay = (i: number) =>
  i === 0 || dayKey(messages.value[i]!.created_at) !== dayKey(messages.value[i - 1]!.created_at)
function dayLabel(iso: string) {
  const key = dayKey(iso)
  if (key === new Date().toDateString()) return 'Hoje'
  if (key === new Date(Date.now() - 864e5).toDateString()) return 'Ontem'
  return dayFmt.format(new Date(iso))
}

// horário estilo WhatsApp na lista: hoje = HH:mm, ontem = "Ontem", antes = dd/mm
const shortDateFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit' })
function listTime(iso: string | null) {
  if (!iso) return ''
  const key = dayKey(iso)
  if (key === new Date().toDateString()) return time(iso)
  if (key === new Date(Date.now() - 864e5).toDateString()) return 'Ontem'
  return shortDateFmt.format(new Date(iso))
}

const accountItems = computed(() =>
  (accounts.value ?? []).map(a => ({ label: a.phone_number_id, value: a.id }))
)
</script>

<template>
  <UDashboardPanel id="contacts" resizable :default-size="25" :min-size="15" :max-size="50">
    <template #header>
      <UDashboardNavbar title="Conversas">
        <template #right>
          <UButton
            v-if="accountId"
            icon="i-lucide-message-square-plus"
            variant="ghost"
            color="neutral"
            size="sm"
            aria-label="Nova conversa"
            title="Nova conversa"
            @click="newChatOpen = true"
          />
          <UButton
            v-if="notifPermission === 'default'"
            icon="i-lucide-bell"
            variant="ghost"
            color="neutral"
            size="sm"
            aria-label="Ativar notificações"
            title="Ativar notificações"
            @click="enableNotifications()"
          />
          <USelect
            v-if="accountItems.length > 1"
            v-model="accountId"
            :items="accountItems"
            icon="i-lucide-phone"
            size="sm"
          />
        </template>
      </UDashboardNavbar>
      <UDashboardToolbar>
        <UInput v-model="search" icon="i-lucide-search" placeholder="Buscar contato..." class="w-full" size="sm" />
      </UDashboardToolbar>
    </template>

    <template #body>
      <div v-if="!accountId" class="p-4 text-sm text-muted">
        Nenhuma conta WhatsApp conectada. Configure uma em
        <NuxtLink to="/configuracoes" class="text-primary">Configurações</NuxtLink>.
      </div>

      <div v-else class="-mx-4 -my-2">
        <button
          v-for="contact in filteredContacts"
          :key="contact.id"
          class="w-full text-left px-3 py-2.5 flex items-center gap-3 border-l-2 transition-colors"
          :class="contact.id === selectedId
            ? 'border-primary bg-primary/5'
            : 'border-transparent hover:bg-elevated/60'"
          @click="selectedId = contact.id"
        >
          <UAvatar :alt="contact.name || contact.wa_id" size="md" />
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between gap-2">
              <span class="font-medium truncate">{{ contact.name || contact.wa_id }}</span>
              <span class="text-[11px] text-muted shrink-0 tabular-nums">
                {{ listTime(contact.last_message_at) }}
              </span>
            </div>
            <div class="flex items-center justify-between gap-2">
              <p
                v-if="contact.last_message"
                class="text-xs text-muted truncate"
              >{{ contact.last_message }}</p>
              <p v-else class="text-[11px] text-muted font-mono truncate">{{ contact.wa_id }}</p>
              <UIcon
                v-if="contact.ai_paused"
                name="i-lucide-headset"
                class="size-4 text-warning shrink-0"
                title="Atendimento humano"
              />
            </div>
          </div>
        </button>

        <p v-if="!filteredContacts.length" class="p-4 text-sm text-muted">
          Nenhuma conversa ainda.
        </p>
      </div>

      <UModal
        v-model:open="newChatOpen"
        title="Nova conversa"
        description="Fora da janela de 24h, o primeiro contato precisa ser um template"
      >
        <template #body>
          <form class="space-y-4" @submit.prevent="createContact">
            <UFormField label="Número" required help="Com código do país, ex.: +55 11 99999-9999">
              <UInput
                v-model="newChatNumber"
                type="tel"
                placeholder="+55 11 99999-9999"
                icon="i-lucide-phone"
                class="w-full"
                autofocus
              />
            </UFormField>
            <UFormField label="Nome" help="Opcional">
              <UInput v-model="newChatName" placeholder="Nome do contato" class="w-full" />
            </UFormField>
            <UButton
              type="submit"
              block
              icon="i-lucide-message-square-plus"
              label="Iniciar conversa"
              :loading="creatingContact"
              :disabled="!newChatNumber.trim()"
            />
          </form>
        </template>
      </UModal>
    </template>
  </UDashboardPanel>

  <UDashboardPanel id="chat">
    <template #header>
      <UDashboardNavbar :title="selected ? undefined : 'Chatly'">
        <template v-if="selected" #title>
          <div class="flex items-center gap-2.5 min-w-0">
            <UAvatar :alt="selected.name || selected.wa_id" size="sm" />
            <div class="min-w-0 leading-tight">
              <p class="font-medium text-sm truncate">{{ selected.name || selected.wa_id }}</p>
              <p class="text-[11px] text-muted font-mono truncate">{{ selected.wa_id }}</p>
            </div>
          </div>
        </template>
        <template #right>
          <template v-if="selected">
            <UButton
              v-if="evoConnected"
              icon="i-lucide-smartphone"
              color="neutral"
              variant="ghost"
              size="sm"
              :loading="openingEvo"
              aria-label="Falar pelo meu WhatsApp"
              title="Falar pelo meu WhatsApp"
              @click="openInEvolution"
            />
            <UBadge
              :color="selected.ai_paused ? 'warning' : 'success'"
              variant="subtle"
              :icon="selected.ai_paused ? 'i-lucide-user' : 'i-lucide-bot'"
            >
              {{ selected.ai_paused ? 'Atendimento humano' : 'IA respondendo' }}
            </UBadge>
            <UButton
              :label="selected.ai_paused ? 'Reativar IA' : 'Assumir conversa'"
              :color="selected.ai_paused ? 'success' : 'warning'"
              :icon="selected.ai_paused ? 'i-lucide-play' : 'i-lucide-pause'"
              variant="soft"
              size="sm"
              @click="setPaused(!selected.ai_paused)"
            />
            <UButton
              v-if="isOwner"
              icon="i-lucide-trash-2"
              color="error"
              variant="ghost"
              size="sm"
              aria-label="Apagar conversa"
              title="Apagar conversa"
              @click="deleteConversation(selected)"
            />
            <UButton
              :icon="detailOpen ? 'i-lucide-panel-right-close' : 'i-lucide-panel-right-open'"
              color="neutral"
              variant="ghost"
              size="sm"
              class="hidden lg:inline-flex"
              :aria-label="detailOpen ? 'Ocultar detalhes' : 'Mostrar detalhes'"
              @click="detailOpen = !detailOpen"
            />
          </template>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div v-if="!selected" class="chat-wallpaper flex-1 flex flex-col items-center justify-center gap-3 text-muted">
        <div class="size-16 rounded-2xl bg-elevated ring-1 ring-default flex items-center justify-center">
          <UIcon name="i-lucide-messages-square" class="size-8" />
        </div>
        <p class="text-sm">Selecione uma conversa pra começar</p>
      </div>

      <!-- overflow-anchor:none - browser também compensa prepend (scroll anchoring); com os dois ativos a correção dobra e a visão pula -->
      <div v-else ref="chatBody" class="chat-wallpaper flex-1 overflow-y-auto space-y-2 pb-2 [overflow-anchor:none]" @scroll.passive="onChatScroll">
        <div v-if="loadingOlder" class="flex justify-center py-2">
          <UIcon name="i-lucide-loader-circle" class="size-4 animate-spin text-muted" />
        </div>
        <template v-for="(message, i) in messages" :key="message.id">
          <div v-if="showDay(i)" class="flex justify-center py-2">
            <span class="text-[11px] text-muted bg-elevated/90 px-3 py-1 rounded-full ring-1 ring-default">
              {{ dayLabel(message.created_at) }}
            </span>
          </div>
          <div class="flex" :class="message.direction === 'out' ? 'justify-end' : 'justify-start'">
            <div
              class="max-w-[75%] rounded-xl px-3 py-2 text-sm ring-1 group"
              :class="message.direction === 'out'
                ? (message.from_human
                  ? 'bg-info/10 ring-info/20 rounded-br-sm'
                  : 'bg-primary/10 ring-primary/20 rounded-br-sm')
                : 'bg-elevated ring-default rounded-bl-sm'"
            >
              <p
                v-if="message.direction === 'out'"
                class="text-[10px] font-semibold mb-0.5 flex items-center gap-1"
                :class="message.from_human ? 'text-info' : 'text-primary'"
              >
                <UIcon :name="message.from_human ? 'i-lucide-headset' : 'i-lucide-sparkles'" class="size-3" />
                {{ message.from_human ? 'Atendente' : 'IA' }}
              </p>
              <ChatMedia
                v-if="message.message_type !== 'text'"
                :message="message"
                :company-id="companyId ?? 0"
              />
              <UButton
                v-if="message.message_type === 'sticker' && message.direction === 'in'"
                label="Salvar"
                icon="i-lucide-bookmark-plus"
                variant="ghost"
                color="neutral"
                size="xs"
                class="opacity-0 group-hover:opacity-100 transition-opacity"
                @click="saveSticker(message)"
              />
              <p
                v-if="message.message_type === 'text' || !isPlaceholder(message)"
                class="whitespace-pre-wrap break-words"
                :class="message.message_type !== 'text' ? 'mt-1' : ''"
              >{{ message.content }}</p>
              <p class="text-[10px] text-muted text-right mt-1 font-mono" :title="fullDate(message.created_at)">
                {{ time(message.created_at) }}
              </p>
            </div>
          </div>
        </template>
      </div>
    </template>

    <template #footer>
      <div v-if="selected" class="w-full border-t border-default">
        <div v-if="windowClosed" class="flex items-center gap-2 px-4 py-2 bg-warning/5 border-b border-warning/20 text-xs">
          <UIcon name="i-lucide-clock-alert" class="size-4 text-warning shrink-0" />
          <span class="text-muted flex-1">
            Janela de 24h fechada - a Meta só aceita mensagem livre até 24h após a última resposta do cliente.
          </span>
          <UButton
            label="Enviar template"
            icon="i-lucide-layout-template"
            color="warning"
            variant="soft"
            size="xs"
            @click="templateModalOpen = true"
          />
        </div>
        <div v-if="attachment && !windowClosed" class="flex items-center gap-2 px-4 pt-2">
          <img v-if="attachmentPreview" :src="attachmentPreview" class="size-9 rounded object-cover ring-1 ring-default">
          <UIcon v-else name="i-lucide-file" class="size-5 text-muted shrink-0" />
          <div class="min-w-0 text-xs">
            <p class="truncate font-medium">{{ attachment.name }}</p>
            <p class="text-muted">{{ formatSize(attachment.size) }}</p>
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
        <form v-if="!windowClosed" class="flex items-center gap-2 w-full p-3" @submit.prevent="send">
          <input ref="fileInput" type="file" class="hidden" @change="onFilePicked">
          <UPopover v-model:open="stickerPickerOpen">
            <UButton
              icon="i-lucide-sticker"
              variant="ghost"
              color="neutral"
              size="lg"
              class="rounded-full"
              aria-label="Figurinhas"
            />
            <template #content>
              <div class="p-3 w-72">
                <p v-if="!stickers.length" class="text-sm text-muted">
                  Nenhuma figurinha salva. Passe o mouse numa figurinha recebida e clique em "Salvar".
                </p>
                <div v-else class="grid grid-cols-4 gap-2">
                  <div v-for="sticker in stickers" :key="sticker.id" class="relative group">
                    <button type="button" class="cursor-pointer" @click="sendSticker(sticker)">
                      <img :src="sticker.url" class="size-14 object-contain" alt="Figurinha">
                    </button>
                    <UButton
                      icon="i-lucide-x"
                      size="xs"
                      color="error"
                      variant="solid"
                      class="absolute -top-1 -right-1 rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
                      aria-label="Excluir figurinha"
                      @click="deleteSticker(sticker.id)"
                    />
                  </div>
                </div>
              </div>
            </template>
          </UPopover>
          <UButton
            icon="i-lucide-paperclip"
            variant="ghost"
            color="neutral"
            size="lg"
            class="rounded-full"
            aria-label="Anexar arquivo"
            @click="fileInput?.click()"
          />
          <UButton
            icon="i-lucide-layout-template"
            variant="ghost"
            color="neutral"
            size="lg"
            class="rounded-full"
            aria-label="Enviar template"
            title="Enviar template"
            @click="templateModalOpen = true"
          />
          <UInput
            v-model="draft"
            :placeholder="attachment
              ? 'Legenda (opcional)...'
              : (selected.ai_paused ? 'Responder como atendente...' : 'IA está ativa - enviar mesmo assim?')"
            variant="soft"
            size="lg"
            class="flex-1"
            :ui="{ base: 'rounded-full px-4' }"
            autocomplete="off"
          />
          <UButton
            type="submit"
            icon="i-lucide-arrow-up"
            size="lg"
            class="rounded-full"
            :loading="sending"
            :disabled="!draft.trim() && !attachment"
          />
        </form>

        <UModal
          v-model:open="templateModalOpen"
          title="Enviar template"
          description="Mensagem pré-aprovada pela Meta - funciona com a janela aberta ou fechada"
        >
          <template #body>
            <div class="space-y-4">
              <TemplatePicker
                v-if="accountId"
                v-model:template="chosenTemplate"
                v-model:params="templateParams"
                v-model:header-media-id="templateHeaderMediaId"
                :company-id="companyId ?? 0"
                :account-id="accountId"
              />
              <p v-if="templateMissingReasons.length" class="text-xs text-warning">
                Falta: {{ templateMissingReasons.join(', ') }}
              </p>
              <UButton
                block
                icon="i-lucide-send"
                label="Enviar"
                :loading="sending"
                :disabled="!canSendTemplate"
                @click="sendTemplate()"
              />
            </div>
          </template>
        </UModal>
      </div>
    </template>
  </UDashboardPanel>

  <UDashboardPanel v-if="selected && detailOpen" id="detail" resizable :default-size="22" :min-size="18" :max-size="30" class="hidden lg:flex">
    <template #header>
      <UDashboardNavbar title="Detalhes" />
    </template>

    <template #body>
      <div class="space-y-6">
        <div class="flex flex-col items-center text-center gap-2 py-2">
          <UAvatar :alt="selected.name || selected.wa_id" size="3xl" />
          <div>
            <p class="font-display font-semibold text-lg">{{ selected.name || 'Sem nome' }}</p>
            <p class="text-xs text-muted font-mono">{{ selected.wa_id }}</p>
          </div>
        </div>

        <div>
          <p class="text-xs font-semibold text-muted uppercase mb-2">Atendimento</p>
          <USwitch
            :model-value="selected.ai_paused"
            label="IA pausada"
            description="Pausada, a IA não responde - você assume."
            @update:model-value="setPaused($event)"
          />
        </div>

        <div>
          <div class="flex items-center justify-between mb-2">
            <p class="text-xs font-semibold text-muted uppercase">Memória da IA</p>
            <UButton
              v-if="selected.memory"
              label="Limpar"
              icon="i-lucide-eraser"
              color="error"
              variant="ghost"
              size="xs"
              @click="clearMemory"
            />
          </div>
          <pre v-if="selected.memory" class="text-xs bg-elevated rounded-lg p-3 whitespace-pre-wrap break-words font-sans">{{ selected.memory }}</pre>
          <p v-else class="text-sm text-muted">Nada anotado ainda.</p>
        </div>

        <USeparator v-if="companyId" icon="i-lucide-handshake" />

        <PipedrivePanel v-if="companyId" :company-id="companyId" :contact="selected" />
      </div>
    </template>
  </UDashboardPanel>
</template>
