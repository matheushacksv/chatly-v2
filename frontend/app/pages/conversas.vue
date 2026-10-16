<script setup lang="ts">
import type { Contact, Member, Message, Paginated, QuickReply, Sticker, Template, WhatsAppAccount } from '~/types/api'

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()
const { activeId: companyId, isOwner } = useCompany()
const { user } = useAuth()

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
// fila: todas, aguardando humano (IA pausada sem responsável) ou minhas
const queue = ref<'all' | 'waiting' | 'mine'>('all')
const queueItems = [
  { label: 'Todas', value: 'all' },
  { label: 'Aguardando', value: 'waiting' },
  { label: 'Minhas', value: 'mine' }
]

const { data: members } = await useAsyncData(
  () => `members:${companyId.value}`,
  () => api<Member[]>(`/companies/${companyId.value}/members`),
  { watch: [companyId], default: () => [] as Member[] }
)
const memberName = (id: number | null) => {
  const member = members.value?.find(m => m.id === id)
  return member ? (member.name || member.email) : 'Sem nome'
}
const assigneeItems = computed(() => [
  { label: 'Sem responsável', value: 0 },
  ...(members.value ?? []).map(m => ({ label: m.id === user.value?.id ? `${m.name || m.email} (eu)` : (m.name || m.email), value: m.id }))
])
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
    `/companies/${companyId.value}/whatsapp-accounts/${accountId.value}/contacts?limit=100${queue.value === 'all' ? '' : `&queue=${queue.value}`}`
  )
  const items = [...page.items]
  // contato aberto sem mensagem pode estar fora da página (nulls_last) - preserva ele
  const current = contacts.value.find(c => c.id === selectedId.value)
  if (current && !items.some(c => c.id === current.id)) items.push(current)
  contacts.value = items
}
watch(accountId, () => { selectedId.value = null; loadContacts() }, { immediate: true })
watch(queue, loadContacts)

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

// muda a cada evento da conversa aberta: o painel "Ações da IA" recarrega
const activityKey = ref(0)

watch(lastEvent, (entry) => {
  if (!entry) return
  const event = entry.event
  // eventos de conexão pessoal têm tabela/endpoints próprios - ignora aqui
  if (event.channel === 'personal' || event.type === 'personal_status') return
  if (event.contact_id === selectedId.value) activityKey.value++
  if (event.type === 'reconnect') {
    // ficou offline: catch-up do que perdeu
    loadMessages()
    loadContacts()
    return
  }
  if (event.type === 'message_status') {
    // ✓, ✓✓ ou falha de entrega: só a conversa aberta muda, a lista não
    if (event.contact_id === selectedId.value) loadMessages()
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

// ---- respostas rápidas: "/" no começo ou depois de espaço abre a lista ----
const { replies: quickReplies, load: loadQuickReplies } = useQuickReplies()
onMounted(loadQuickReplies)
const draftInput = useTemplateRef<{ inputRef: HTMLInputElement | null }>('draftInput')
const slashIndex = ref(0)
// null = lista fechada; string = o que foi digitado depois da "/"
const slashQuery = computed(() => {
  const match = draft.value.match(/(?:^|\s)\/([\w-]*)$/)
  return match && !slashDismissed.value ? match[1]! : null
})
const slashDismissed = ref(false)
watch(draft, () => {
  slashDismissed.value = false
  slashIndex.value = 0
})
const slashOptions = computed(() => {
  const q = (slashQuery.value ?? '').toLowerCase()
  return quickReplies.value.filter(r => r.shortcut.includes(q) || r.text.toLowerCase().includes(q)).slice(0, 8)
})

function pickQuickReply(reply: QuickReply) {
  if (!selected.value) return
  // troca o "/atalho" digitado pelo texto já preenchido com os dados do contato
  draft.value = draft.value.replace(/\/[\w-]*$/, fillContactVars(reply.text, selected.value))
  slashDismissed.value = true
  nextTick(() => draftInput.value?.inputRef?.focus())
}

function onDraftKeydown(event: KeyboardEvent) {
  if (slashQuery.value === null || !slashOptions.value.length) {
    if (event.key === 'Escape') slashDismissed.value = true
    return
  }
  const total = slashOptions.value.length
  if (event.key === 'ArrowDown') slashIndex.value = (slashIndex.value + 1) % total
  else if (event.key === 'ArrowUp') slashIndex.value = (slashIndex.value - 1 + total) % total
  else if (event.key === 'Enter' || event.key === 'Tab') pickQuickReply(slashOptions.value[slashIndex.value]!)
  else if (event.key === 'Escape') slashDismissed.value = true
  else return
  event.preventDefault()
}

function closeSlashSoon() {
  setTimeout(() => {
    slashDismissed.value = true
  }, 150)
}

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
    message.sticker_saved = true
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
async function patchSelected(body: Record<string, unknown>) {
  mergeContact(await api<Contact>(`/companies/${companyId.value}/contacts/${selected.value!.id}`, { method: 'PATCH', body }))
}

function mergeContact(updated: Contact) {
  // PATCH não traz os campos da lista (última mensagem): mescla em vez de substituir
  contacts.value = contacts.value.map(c => (c.id === updated.id ? { ...c, ...updated, last_message: c.last_message, last_message_at: c.last_message_at, last_inbound_at: c.last_inbound_at } : c))
}

async function assign(userId: number) {
  if (!selected.value) return
  try {
    await patchSelected({ assigned_to: userId || null })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// "IA volta em 5h" / "IA volta em 20 min"; null quando a pausa não expira
const resumeText = computed(() => {
  const at = selected.value?.ai_paused ? selected.value.ai_resume_at : null
  if (!at) return null
  const minutes = Math.max(1, Math.round((new Date(at).getTime() - Date.now()) / 60000))
  return minutes < 60 ? `IA volta em ${minutes} min` : `IA volta em ${Math.round(minutes / 60)}h`
})

// ponytail: calculado no carregamento do contato; atualiza quando a lista recarrega (SSE), sem relógio próprio
const aiOpensText = computed(() => {
  const at = selected.value?.ai_paused ? null : selected.value?.ai_opens_at
  if (!at || new Date(at).getTime() <= Date.now()) return null
  const opens = new Date(at)
  const time = opens.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  if (opens.toDateString() === new Date().toDateString()) return `IA responde às ${time}`
  return `IA responde ${opens.toLocaleDateString('pt-BR', { weekday: 'short' }).replace('.', '')} ${time}`
})

async function setPaused(paused: boolean) {
  if (!selected.value) return
  try {
    // assumir = pausar e ficar como responsável (se ninguém tiver pego)
    await patchSelected(paused
      ? { ai_paused: true, assigned_to: selected.value.assigned_to ?? user.value?.id ?? null }
      : { ai_paused: false })
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
// seções recolhidas: abre só o que precisa; o estado fica entre um contato e outro
const detailSections = ref<string[]>(['fields'])
const pipedriveConnected = ref(false)
watch(companyId, async (id) => {
  pipedriveConnected.value = !!id && await api(`/companies/${id}/pipedrive`).then(() => true, () => false)
}, { immediate: true })
const detailItems = computed(() => [
  { label: 'Campos', icon: 'i-lucide-list', value: 'fields', slot: 'fields' as const },
  { label: 'Ações da IA', icon: 'i-lucide-wrench', value: 'activity', slot: 'activity' as const },
  { label: 'Memória da IA', icon: 'i-lucide-brain', value: 'memory', slot: 'memory' as const },
  ...(pipedriveConnected.value ? [{ label: 'Pipedrive', icon: 'i-lucide-handshake', value: 'crm', slot: 'crm' as const }] : [])
])

// ações menos usadas do topo da conversa ficam no menu "..."
const headerMenu = computed(() => [
  ...(personalConnected.value ? [{ label: 'Falar pelo meu WhatsApp', icon: 'i-lucide-smartphone', onSelect: openInPersonal }] : []),
  ...(isOwner.value && selected.value ? [{ label: 'Apagar conversa', icon: 'i-lucide-trash-2', color: 'error' as const, onSelect: () => deleteConversation(selected.value!) }] : [])
])

// ---- abrir no meu WhatsApp (canal pessoal) ----
// reusa o estado global do usePersonalChat: startConversation seta o selecionado,
// o watcher do layout carrega a thread, e navegar pra /meu-whatsapp já mostra a conversa.
const { connected: personalConnected, startConversation: startPersonalConversation, widgetOpen: personalWidgetOpen } = usePersonalChat()
const openingPersonal = ref(false)
async function openInPersonal() {
  if (!selected.value || openingPersonal.value) return
  openingPersonal.value = true
  try {
    await startPersonalConversation(selected.value.wa_id, selected.value.name)
    personalWidgetOpen.value = true
  } catch (error) {
    const detail = apiErrorDetail(error, '')
    toast.add({
      title: detail.includes('not on whatsapp') ? 'Esse número não tem WhatsApp' : detail || 'Não foi possível abrir no seu WhatsApp',
      color: 'error'
    })
  } finally {
    openingPersonal.value = false
  }
}

// ---- helpers ----
const timeFmt = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' })
const dateFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
const dayFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: 'long', year: 'numeric' })
const time = (iso: string) => timeFmt.format(new Date(iso))
// enviado = aceito pela Meta; a entrega (ou a falha) chega depois pelo webhook
const STATUS_ICONS: Record<string, { icon: string, class: string, label: string }> = {
  sent: { icon: 'i-lucide-check', class: 'text-muted', label: 'Enviada' },
  delivered: { icon: 'i-lucide-check-check', class: 'text-muted', label: 'Entregue' },
  read: { icon: 'i-lucide-check-check', class: 'text-info', label: 'Lida' },
  failed: { icon: 'i-lucide-circle-alert', class: 'text-error', label: 'Não entregue' }
}
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
      <UDashboardToolbar class="flex-col items-stretch gap-2 py-2">
        <UInput v-model="search" icon="i-lucide-search" placeholder="Buscar contato..." class="w-full" size="sm" />
        <UTabs v-model="queue" :items="queueItems" :content="false" size="xs" class="w-full" />
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
              <UBadge
                v-if="contact.ai_paused && !contact.assigned_to"
                label="Aguardando"
                color="warning"
                variant="subtle"
                size="sm"
                icon="i-lucide-headset"
                class="shrink-0"
              />
              <UAvatar
                v-else-if="contact.assigned_to"
                :alt="memberName(contact.assigned_to)"
                size="3xs"
                class="shrink-0"
                :title="`Com ${memberName(contact.assigned_to)}`"
              />
            </div>
          </div>
        </button>

        <p v-if="!filteredContacts.length" class="p-4 text-sm text-muted">
          {{ queue === 'waiting' ? 'Ninguém aguardando atendimento.' : queue === 'mine' ? 'Nenhuma conversa com você.' : 'Nenhuma conversa ainda.' }}
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
            <AppContactTags
              class="hidden sm:flex"
              :contact="selected"
              :company-id="companyId ?? 0"
              size="xs"
              @updated="mergeContact"
            />
          </div>
        </template>
        <template #right>
          <template v-if="selected">
            <!-- o botão já diz o estado (Assumir = IA ativa, Reativar = pausada); o detalhe fica no tooltip -->
            <UTooltip v-if="aiOpensText" :text="`Fora do horário: ${aiOpensText}, se ninguém responder antes`">
              <UBadge color="neutral" variant="subtle" icon="i-lucide-clock">
                {{ aiOpensText.replace('IA responde ', '') }}
              </UBadge>
            </UTooltip>
            <USelect
              :model-value="selected.assigned_to ?? 0"
              :items="assigneeItems"
              icon="i-lucide-user-round"
              size="sm"
              variant="ghost"
              class="w-44"
              aria-label="Responsável"
              @update:model-value="assign(Number($event))"
            />
            <UTooltip :text="selected.ai_paused ? `IA pausada: ${resumeText ?? 'só volta se reativar'}` : 'IA respondendo. Assumir pausa a IA'">
              <UButton
                :label="selected.ai_paused ? 'Reativar IA' : 'Assumir conversa'"
                :color="selected.ai_paused ? 'success' : 'warning'"
                :icon="selected.ai_paused ? 'i-lucide-play' : 'i-lucide-pause'"
                variant="soft"
                size="sm"
                @click="setPaused(!selected.ai_paused)"
              />
            </UTooltip>
            <UDropdownMenu v-if="headerMenu.length" :items="headerMenu" :content="{ align: 'end' }">
              <UButton icon="i-lucide-ellipsis-vertical" color="neutral" variant="ghost" size="sm" aria-label="Mais ações" :loading="openingPersonal" />
            </UDropdownMenu>
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
                :label="message.sticker_saved ? 'Salva' : 'Salvar'"
                :icon="message.sticker_saved ? 'i-lucide-bookmark-check' : 'i-lucide-bookmark-plus'"
                :disabled="message.sticker_saved"
                variant="ghost"
                color="neutral"
                size="xs"
                :class="message.sticker_saved ? '' : 'opacity-0 group-hover:opacity-100 transition-opacity'"
                @click="saveSticker(message)"
              />
              <p
                v-if="message.message_type === 'text' || !isPlaceholder(message)"
                class="whitespace-pre-wrap break-words"
                :class="message.message_type !== 'text' ? 'mt-1' : ''"
              >{{ message.content }}</p>
              <p
                v-if="message.direction === 'out' && message.status === 'failed'"
                class="text-xs text-error mt-1 flex items-start gap-1"
              >
                <UIcon name="i-lucide-circle-alert" class="size-3.5 shrink-0 mt-0.5" />
                Não entregue{{ message.error_detail ? `: ${message.error_detail}` : '' }}
              </p>
              <p class="text-[10px] text-muted mt-1 font-mono flex items-center justify-end gap-1" :title="fullDate(message.created_at)">
                {{ time(message.created_at) }}
                <UIcon
                  v-if="message.direction === 'out' && STATUS_ICONS[message.status]"
                  :name="STATUS_ICONS[message.status]!.icon"
                  class="size-3.5"
                  :class="STATUS_ICONS[message.status]!.class"
                  :aria-label="STATUS_ICONS[message.status]!.label"
                />
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
          <div class="relative flex-1">
            <UInput
              ref="draftInput"
              v-model="draft"
              :placeholder="attachment
                ? 'Legenda (opcional)...'
                : (selected.ai_paused ? 'Responder como atendente... (/ pra respostas rápidas)' : 'Responder assume a conversa e pausa a IA...')"
              variant="soft"
              size="lg"
              class="w-full"
              :ui="{ base: 'rounded-full px-4' }"
              autocomplete="off"
              @keydown="onDraftKeydown"
              @blur="closeSlashSoon"
            />
            <!-- lista de respostas rápidas: abre com "/" e filtra pelo que vem depois -->
            <div
              v-if="slashQuery !== null"
              class="absolute bottom-full mb-2 left-0 w-full max-w-md rounded-lg bg-default ring-1 ring-default shadow-lg py-1 z-20"
              role="listbox"
            >
              <button
                v-for="(reply, i) in slashOptions"
                :key="reply.id"
                type="button"
                role="option"
                :aria-selected="i === slashIndex"
                class="w-full text-left px-3 py-1.5"
                :class="i === slashIndex ? 'bg-elevated' : ''"
                @mousedown.prevent="pickQuickReply(reply)"
              >
                <p class="text-sm font-mono">
                  /{{ reply.shortcut }}
                </p>
                <p class="text-xs text-muted truncate">
                  {{ fillContactVars(reply.text, selected) }}
                </p>
              </button>
              <p v-if="!slashOptions.length" class="px-3 py-2 text-sm text-muted">
                {{ quickReplies.length ? 'Nenhuma resposta com esse atalho.' : 'Nenhuma resposta rápida ainda.' }}
              </p>
              <button
                type="button"
                class="block w-full text-left px-3 py-1.5 text-xs text-primary border-t border-default"
                @mousedown.prevent="navigateTo('/configuracoes?tab=respostas')"
              >
                Gerenciar respostas rápidas
              </button>
            </div>
          </div>
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
      <div class="space-y-4">
        <div class="flex items-center gap-3">
          <UAvatar :alt="selected.name || selected.wa_id" size="lg" />
          <div class="min-w-0">
            <p class="font-display font-semibold truncate">{{ selected.name || 'Sem nome' }}</p>
            <p class="text-xs text-muted font-mono">{{ selected.wa_id }}</p>
          </div>
        </div>

        <div class="flex items-center gap-2">
          <USwitch
            :model-value="!selected.ai_paused"
            :label="selected.ai_paused ? 'IA pausada' : 'IA ativa'"
            @update:model-value="setPaused(!$event)"
          />
          <AppTip
            :text="selected.ai_paused
              ? (resumeText ? `A IA não responde. ${resumeText}.` : 'A IA não responde até ser reativada.')
              : aiOpensText
                ? `Fora do horário: ${aiOpensText}, se ninguém responder antes.`
                : 'Responder pelo chat também pausa a IA.'"
          />
        </div>

        <UAccordion
          v-if="companyId"
          v-model="detailSections"
          type="multiple"
          :items="detailItems"
          :ui="{ trigger: 'text-xs font-semibold uppercase text-muted py-2.5', body: 'pb-3' }"
        >
          <template #fields>
            <AppContactFields :contact="selected" :company-id="companyId" @updated="mergeContact" />
          </template>
          <template #activity>
            <AppAIActivity :contact-id="selected.id" :company-id="companyId" :refresh-key="activityKey" />
          </template>
          <template #memory>
            <pre v-if="selected.memory" class="text-xs bg-elevated rounded-lg p-3 whitespace-pre-wrap break-words font-sans">{{ selected.memory }}</pre>
            <p v-else class="text-sm text-muted">Nada anotado ainda.</p>
            <UButton
              v-if="selected.memory"
              label="Limpar memória"
              icon="i-lucide-eraser"
              color="error"
              variant="ghost"
              size="xs"
              class="mt-1"
              @click="clearMemory"
            />
          </template>
          <template #crm>
            <PipedrivePanel :company-id="companyId" :contact="selected" />
          </template>
        </UAccordion>
      </div>
    </template>
  </UDashboardPanel>
</template>
