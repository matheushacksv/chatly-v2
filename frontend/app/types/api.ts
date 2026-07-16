export interface User {
  id: number
  email: string
  is_staff: boolean
}

export interface TokenPair {
  access: string
  refresh: string
  user: User
}

export interface Company {
  id: number
  name: string
  role?: 'owner' | 'member' | null
}

export interface Member {
  id: number
  name: string
  email: string
  role: 'owner' | 'member'
}

export interface Invite {
  id: number
  token: string
  created_at: string
}

export interface WhatsAppAccount {
  id: number
  agent: number
  phone_number_id: string
  waba_id: string
  verify_token: string
}

export interface Contact {
  id: number
  wa_id: string
  name: string
  memory: string
  ai_paused: boolean
  created_at: string
  last_message: string | null
  last_message_at: string | null
  last_inbound_at: string | null
}

export interface Message {
  id: number
  contact: number
  direction: 'in' | 'out'
  content: string
  message_type: 'text' | 'image' | 'audio' | 'video' | 'document' | 'sticker'
  media_url: string | null
  wa_message_id: string | null
  from_human: boolean
  status: string
  created_at: string
}

export interface Sticker {
  id: number
  url: string
  created_at: string
}

export interface Paginated<T> {
  items: T[]
  count: number
}

export interface Template {
  name: string
  language: string
  status: string
  category: string
  body_text: string
  param_count: number
}

export interface Campaign {
  id: number
  name: string
  template_name: string
  template_language: string
  status: 'sending' | 'done' | 'failed'
  total: number
  sent: number
  failed: number
  created_at: string
  delivered: number
  read: number
}

export interface Credential {
  id: number
  provider: 'anthropic' | 'openai'
}

export interface Agent {
  id: number
  name: string
  description: string
  model: string
  prompt: string
  response_delay: number
  split_messages: boolean
  created_at: string
}

export interface AgentDocument {
  id: number
  name: string
  status: 'processing' | 'ready' | 'failed'
  created_at: string
}
