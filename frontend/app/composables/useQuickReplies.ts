import type { Contact, QuickReply } from '~/types/api'

/** Respostas rápidas da empresa ativa, compartilhadas entre o chat e Configurações. */
export function useQuickReplies() {
  const api = useApi()
  const { activeId } = useCompany()
  const replies = useState<QuickReply[]>('quick-replies', () => [])

  async function load() {
    replies.value = activeId.value ? await api<QuickReply[]>(`/companies/${activeId.value}/quick-replies`) : []
  }

  return { replies, load }
}

/** Preenche {{nome}}, {{telefone}} e campos com os dados do contato; o que não existir fica vazio pro atendente completar. */
export function fillContactVars(text: string, contact: Contact) {
  const values: Record<string, string> = {
    ...contact.custom_fields,
    nome: contact.name || contact.wa_id,
    telefone: contact.wa_id
  }
  return text.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, key: string) => values[key] ?? '')
}
