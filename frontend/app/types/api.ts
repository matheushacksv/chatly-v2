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
  plan: 'trial' | 'starter' | 'pro' | 'business'
  plan_until: string | null
  plan_active: boolean
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

export type TagColor = 'neutral' | 'primary' | 'success' | 'info' | 'warning' | 'error'

export interface Tag {
  id: number
  name: string
  color: TagColor
}

/** Campo personalizado do contato: o valor vira a variável {{key}} em templates e fluxos */
export interface ContactField {
  id: number
  key: string
  label: string
}

/** Linha de CSV importada (contatos e campanha): fields = {ContactField.key: valor} */
export interface CsvRecipient {
  number: string
  name: string
  fields: Record<string, string>
}

export interface Contact {
  id: number
  wa_id: string
  name: string
  memory: string
  ai_paused: boolean
  ai_resume_at: string | null
  ai_opens_at: string | null // fora do horário da IA: quando ela volta a responder
  custom_fields: Record<string, string>
  assigned_to: number | null
  tags: Tag[]
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
  sticker_saved: boolean
  wa_message_id: string | null
  from_human: boolean
  status: 'sent' | 'delivered' | 'read' | 'failed' | string
  error_detail: string // motivo da falha devolvido pela Meta
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
  quick_replies: string[]
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
  flow: number | null
  status: 'scheduled' | 'sending' | 'done' | 'failed' | 'canceled'
  total: number
  sent: number
  failed: number
  scheduled_at: string | null
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
  handoff_resume_hours: number
  // faixas por dia (0 = segunda), ['HH:MM', 'HH:MM'] com fim '00:00' = meia-noite; null = IA sempre atende
  ai_schedule: [string, string][][] | null
  native_tools: NativeTool[]
  created_at: string
}

export type NativeTool = 'handoff' | 'followup' | 'save_field' | 'tag' | 'pipedrive' | 'send_file'

export interface AgentFile {
  id: number
  name: string
  description: string
  content_type: string
  url: string
  created_at: string
}

export interface AgentToolParam {
  name: string
  type: 'string' | 'number' | 'boolean'
  description: string
  required: boolean
}

/** Ferramenta HTTP criada pelo dono; secret_header_keys traz só os nomes, o valor nunca volta */
export interface AgentTool {
  id: number
  name: string
  description: string
  parameters: AgentToolParam[]
  method: HttpMethod
  url: string
  headers: { key: string, value: string }[]
  secret_header_keys: string[]
  body: string
  requires_confirmation: boolean
  active: boolean
  created_at: string
}

export interface ToolCall {
  id: number
  name: string
  arguments: Record<string, unknown>
  result: string
  ok: boolean
  created_at: string
}

export interface FollowUp {
  id: number
  send_at: string
  text: string
  status: 'pending' | 'sent' | 'canceled' | 'failed'
  error: string
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
  lead_account_id: number | null
  receiving_events: boolean
  auto_stage_ids: number[]
  auto_template: AutoTemplate
  auto_rules: AutoRule[]
  auto_only_in_hours: boolean
  auto_delay_minutes: number
  auto_followup: AutoFollowup | Record<string, never>
  field_map: PipedriveFieldMapItem[]
  created_at: string
}

export interface AutoTemplate { template_name?: string, language?: string, body_params?: string[], header_media_id?: string }

/** Contato com `field` igual a um dos `values` recebe esse template na abertura */
export interface AutoFollowup extends AutoTemplate { after_hours: number }
export interface AutoRule { field: string, values: string[], template: AutoTemplate }

export interface PipedriveField { source: 'deal' | 'person', key: string, name: string }

export interface PipedriveFieldMapItem extends PipedriveField { contact_field: string }

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

// ---- WhatsApp pessoal (OpenWA) ----
// conexão pessoal do atendente: só texto 1:1, sem IA/campanha/template

export interface PersonalAccount {
  id: number
  session_name: string
  status: 'connecting' | 'connected' | 'disconnected'
  phone_number: string
  created_at: string
  user_email: string
}

export interface PersonalContact {
  id: number
  wa_id: string
  name: string
  avatar_url: string
  created_at: string
}

export interface PersonalDirectoryEntry {
  wa_id: string
  name: string
  source: 'agenda' | 'oficial'
}

export interface PersonalMessage {
  id: number
  direction: 'in' | 'out'
  content: string
  message_type: 'text' | 'image' | 'audio' | 'video' | 'document' | 'sticker'
  media_url: string | null
  sticker_saved: boolean
  status: string
  error_detail: string | null
  wa_message_id: string | null
  created_at: string
}

export interface PersonalConnect {
  qrcode: string
  pair_code: string
}

// ---- Automações (fluxos) ----
// Rotas que o front consome (router montado em /automations):
//   GET|POST    /automations/{company_id}/flows
//   GET|PUT|DELETE /automations/{company_id}/flows/{id}
//   POST        /automations/{company_id}/flows/{id}/activate   (body: {active: bool})
//   GET         /automations/{company_id}/flows/{id}/runs       (paginado)
//   GET|POST    /automations/{company_id}/lead-sources
//   DELETE      /automations/{company_id}/lead-sources/{id}
//   POST        /automations/leads/{token}                      (público, auth=None)

export type FlowNodeType
  = 'start' | 'template' | 'message' | 'buttons' | 'wait' | 'condition' | 'delay' | 'crm' | 'http' | 'tag' | 'pause' | 'handoff' | 'ai' | 'end'

export type FlowTrigger = 'lead' | 'first_message' | 'keyword' | 'pipedrive' | 'campaign'

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export interface FlowButton {
  id: string
  title: string
}

/** Config do nó. Cada tipo usa um subconjunto, por isso tudo opcional. */
export interface FlowNodeData {
  template_name?: string
  language?: string
  body_params?: string[]
  header_media_id?: string
  text?: string
  buttons?: FlowButton[]
  var?: string
  timeout_minutes?: number
  source?: string
  op?: 'contains' | 'equals' | 'regex' | 'window_open' | 'has_tag' | 'filled'
  value?: string
  values?: string[] // condição contém/é igual: basta um casar
  minutes?: number
  action?: 'person' | 'deal' | 'note' | 'stage' | 'label' | 'add' | 'remove'
  title?: string
  stage_id?: number
  label_id?: number
  label_mode?: 'add' | 'remove'
  // requisição HTTP
  method?: HttpMethod
  url?: string
  headers?: { key: string, value: string }[]
  body?: string
  // etiqueta
  tag_id?: number
}

export interface FlowNode {
  id: string
  type: FlowNodeType
  position: { x: number, y: number }
  data: FlowNodeData
}

export interface FlowEdge {
  id: string
  source: string
  target: string
  // saída de origem: id do botão, 'sim'/'nao' na condição, null quando o nó tem saída única
  sourceHandle?: string | null
}

export interface FlowGraph {
  nodes: FlowNode[]
  edges: FlowEdge[]
}

export interface Flow {
  id: number
  name: string
  account: number
  trigger: FlowTrigger
  trigger_config: { source_id?: number, keyword?: string, event?: string, stage_id?: number }
  graph: FlowGraph
  active: boolean
  updated_at: string
  run_count: number
}

export interface FlowRunStep {
  node: string
  type: string
  at: string
  result: string
}

export interface FlowRun {
  id: number
  contact_id: number
  contact_name: string
  status: 'running' | 'waiting' | 'done' | 'failed'
  node_id: string
  steps: FlowRunStep[]
  created_at: string
  updated_at: string
}

export interface LeadSource {
  id: number
  name: string
  account: number
  token: string
  active: boolean
  created_at: string
}

/** Resposta pronta do atendente: /atalho no chat; o texto aceita {{nome}}, {{telefone}} e campos */
export interface QuickReply {
  id: number
  shortcut: string
  text: string
}
