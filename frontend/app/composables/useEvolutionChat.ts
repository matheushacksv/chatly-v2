import type { EvolutionAccount, EvolutionContact, EvolutionDirectoryEntry, EvolutionMessage, Sticker } from '~/types/api'

// Estado compartilhado (useState) entre a página /meu-whatsapp e o widget flutuante,
// pra seleção/mensagens/conexão não divergirem. O "driver" (carga inicial, SSE,
// seleção -> mensagens) vive no layout default.vue, que roda uma vez só.
export function useEvolutionChat() {
  const api = useApi()
  const { activeId: companyId } = useCompany()
  const base = computed(() => `/evolution/${companyId.value}/evolution-accounts`)

  const connection = useState<EvolutionAccount | null>('evo-connection', () => null)
  const contacts = useState<EvolutionContact[]>('evo-contacts', () => [])
  const selectedId = useState<number | null>('evo-selected', () => null)
  const messages = useState<EvolutionMessage[]>('evo-messages', () => [])
  // galeria de figurinhas é company-scoped (compartilhada com o canal oficial)
  const stickers = useState<Sticker[]>('evo-stickers', () => [])
  // destinatários possíveis pra iniciar conversa: agenda do celular + contatos do canal oficial
  const directory = useState<EvolutionDirectoryEntry[]>('evo-directory', () => [])
  // aberto/fechado da bolha flutuante: compartilhado pra outras páginas abrirem o widget
  const widgetOpen = useState<boolean>('evo-widget-open', () => false)

  const selected = computed(() => contacts.value.find(c => c.id === selectedId.value) ?? null)
  const connected = computed(() => connection.value?.status === 'connected')

  async function loadConnection() {
    if (!companyId.value) {
      connection.value = null
      return
    }
    connection.value = await api<EvolutionAccount | null>(`${base.value}/me`)
  }

  async function loadContacts() {
    if (connection.value?.status !== 'connected') {
      contacts.value = []
      return
    }
    contacts.value = await api<EvolutionContact[]>(`${base.value}/${connection.value.id}/contacts`)
  }

  async function loadMessages() {
    if (!connection.value || !selectedId.value) {
      messages.value = []
      return
    }
    messages.value = await api<EvolutionMessage[]>(`${base.value}/${connection.value.id}/contacts/${selectedId.value}/messages`)
  }

  function resetInbox() {
    contacts.value = []
    messages.value = []
    selectedId.value = null
    directory.value = []
  }

  async function loadDirectory() {
    if (!connection.value) return
    directory.value = await api<EvolutionDirectoryEntry[]>(`${base.value}/${connection.value.id}/directory`)
  }

  // cria (ou reaproveita) o contato e abre a conversa; o watcher do layout carrega a thread
  async function startConversation(phone: string, name = '') {
    if (!connection.value) return
    const contact = await api<EvolutionContact>(
      `${base.value}/${connection.value.id}/contacts`,
      { method: 'POST', body: { phone, name } }
    )
    if (!contacts.value.some(c => c.id === contact.id)) contacts.value = [contact, ...contacts.value]
    selectedId.value = contact.id
  }

  // apaga a conversa só no Chatly (nossa DB); não toca no WhatsApp do atendente
  async function deleteConversation(contactId: number) {
    if (!connection.value) return
    try {
      await api(`${base.value}/${connection.value.id}/contacts/${contactId}`, { method: 'DELETE' })
    } catch (error) {
      // 404 = já não existe no servidor (estado local ficou stale): objetivo cumprido, limpa mesmo assim
      if ((error as { status?: number }).status !== 404) throw error
    }
    contacts.value = contacts.value.filter(c => c.id !== contactId)
    if (selectedId.value === contactId) {
      selectedId.value = null
      messages.value = []
    }
  }

  async function send(content: string) {
    const text = content.trim()
    if (!text || !connection.value || !selectedId.value) return
    const msg = await api<EvolutionMessage>(
      `${base.value}/${connection.value.id}/contacts/${selectedId.value}/messages`,
      { method: 'POST', body: { content: text } }
    )
    messages.value = [...messages.value, msg]
  }

  async function sendMedia(file: File, caption: string) {
    if (!connection.value || !selectedId.value) return
    const form = new FormData()
    form.append('file', file)
    if (caption) form.append('caption', caption)
    const msg = await api<EvolutionMessage>(
      `${base.value}/${connection.value.id}/contacts/${selectedId.value}/messages/media`,
      { method: 'POST', body: form }
    )
    messages.value = [...messages.value, msg]
  }

  // ---- figurinhas ---- lista/exclusão reusam a galeria company-scoped;
  // salvar (de uma recebida) e enviar passam pelo canal Evolution.
  async function loadStickers() {
    if (!companyId.value) return
    stickers.value = await api<Sticker[]>(`/companies/${companyId.value}/stickers`)
  }

  async function saveSticker(messageId: number) {
    if (!connection.value) return
    await api(`${base.value}/${connection.value.id}/stickers`, { method: 'POST', body: { message_id: messageId } })
  }

  async function sendSticker(stickerId: number) {
    if (!connection.value || !selectedId.value) return
    const msg = await api<EvolutionMessage>(
      `${base.value}/${connection.value.id}/contacts/${selectedId.value}/messages/sticker`,
      { method: 'POST', body: { sticker_id: stickerId } }
    )
    messages.value = [...messages.value, msg]
  }

  async function deleteSticker(id: number) {
    if (!companyId.value) return
    await api(`/companies/${companyId.value}/stickers/${id}`, { method: 'DELETE' })
    stickers.value = stickers.value.filter(s => s.id !== id)
  }

  return {
    base,
    connection,
    contacts,
    selectedId,
    messages,
    stickers,
    directory,
    widgetOpen,
    selected,
    connected,
    loadConnection,
    loadContacts,
    loadMessages,
    loadDirectory,
    startConversation,
    deleteConversation,
    resetInbox,
    send,
    sendMedia,
    loadStickers,
    saveSticker,
    sendSticker,
    deleteSticker
  }
}
