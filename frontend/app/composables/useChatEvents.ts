import type { Contact } from '~/types/api'

export interface ChatEvent {
  type: string
  contact_id: number
  name?: string
  // conexões do WhatsApp pessoal marcam o canal pra não colidir com o inbox oficial
  channel?: string
  account_id?: number
  status?: string
}

/**
 * Ponte layout ↔ páginas: o layout mantém a conexão SSE (notifica de qualquer página)
 * e publica cada evento aqui; a página de conversas consome pra atualizar chat/lista.
 */
export function useChatEvents() {
  // seq muda a cada evento - watch dispara mesmo com payload igual ao anterior
  const lastEvent = useState<{ seq: number, event: ChatEvent } | null>('chat:last-event', () => null)
  // contato a abrir quando a página de conversas montar (setado pelas notificações)
  const openContact = useState<number | null>('chat:open-contact', () => null)
  // objeto completo (setado pela página de contatos): contato sem mensagem não está
  // na lista de conversas - a página faz unshift dele pra conseguir abrir o chat
  const openContactData = useState<Contact | null>('chat:open-contact-data', () => null)

  return { lastEvent, openContact, openContactData }
}
