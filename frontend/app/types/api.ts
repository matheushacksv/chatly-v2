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
  app_id: string
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
  id: string
  name: string
  language: string
  status: string
  category: string
  header_format: string
  has_header_media: boolean
  body_text: string
  param_count: number
}

export interface MediaUpload {
  media_id: string
}

export type TemplateCategory = 'MARKETING' | 'UTILITY'
export type TemplateHeaderFormat = 'NONE' | 'TEXT' | 'IMAGE' | 'VIDEO' | 'DOCUMENT'
export type TemplateButtonType = 'QUICK_REPLY' | 'URL' | 'PHONE_NUMBER'

export interface TemplateHeader {
  format: TemplateHeaderFormat
  text: string
  example: string
  handle: string
  media_id: string
}

export interface TemplateMediaHandle {
  handle: string
  media_id: string
}

export interface TemplateButton {
  type: TemplateButtonType
  text: string
  url: string
  phone_number: string
}

export interface TemplateDetail {
  id: string
  name: string
  language: string
  category: string
  status: string
  header: TemplateHeader | null
  has_header_media: boolean
  body_text: string
  body_examples: string[]
  footer_text: string
  buttons: TemplateButton[]
}

export interface TemplateInput {
  name: string
  language: string
  category: TemplateCategory
  header: TemplateHeader | null
  body_text: string
  body_examples: string[]
  footer_text: string
  buttons: TemplateButton[]
}

export interface TemplateCreated {
  id: string
  status: string
  category: string
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

export interface PipedriveConfig {
  id: number
  default_pipeline_id: number | null
  default_stage_id: number | null
  created_at: string
}

export interface PipedrivePipeline {
  id: number
  name: string
  stages: { id: number, name: string }[]
}

export interface PipedrivePerson {
  id: number
  name: string
  phones: string[]
}

export interface PipedrivePersonResolve {
  person: PipedrivePerson | null
  suggestions: PipedrivePerson[]
}

export interface PipedriveDeal {
  id: number
  title: string
  value: number | null
  currency: string | null
  pipeline_id: number | null
  stage_id: number | null
  status: string | null
  update_time: string | null
}

export interface PipedriveNote {
  id: number
  content: string
  add_time: string | null
}

export interface PipedriveActivity {
  id: number
  subject: string | null
  type: string | null
  due_date: string | null
  done: boolean
}

// ---- WhatsApp não-oficial (Evolution) ----
// conexão pessoal do atendente: só texto 1:1, sem IA/campanha/template

export interface EvolutionAccount {
  id: number
  instance_name: string
  status: 'connecting' | 'connected' | 'disconnected'
  phone_number: string
  created_at: string
  user_email: string
}

export interface EvolutionContact {
  id: number
  wa_id: string
  name: string
  created_at: string
}

export interface EvolutionMessage {
  id: number
  direction: 'in' | 'out'
  content: string
  message_type: 'text' | 'image' | 'audio' | 'video' | 'document' | 'sticker'
  media_url: string | null
  status: string
  error_detail: string | null
  wa_message_id: string | null
  created_at: string
}

export interface EvolutionConnect {
  qrcode: string
  pair_code: string
}
