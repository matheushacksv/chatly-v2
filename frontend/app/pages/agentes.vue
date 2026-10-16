<script setup lang="ts">
import { z } from 'zod'
import type { Agent, AgentDocument, AgentTool, Credential, NativeTool } from '~/types/api'

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()
const { activeId: companyId, isOwner } = useCompany()

// ---- dados ----
const agents = ref<Agent[]>([])
const credentials = ref<Credential[]>([])

async function loadAll() {
  if (!companyId.value) return
  ;[agents.value, credentials.value] = await Promise.all([
    api<Agent[]>(`/companies/${companyId.value}/agents`),
    api<Credential[]>(`/companies/${companyId.value}/credentials`)
  ])
}
watch(companyId, loadAll, { immediate: true })

const credentialItems = computed(() =>
  credentials.value.map(c => ({ label: `${c.provider === 'anthropic' ? 'Anthropic' : 'OpenAI'} #${c.id}`, value: c.id }))
)

// catálogo fixo de modelos por provider - atualizar aqui quando sair modelo novo
const MODELS: Record<Credential['provider'], { label: string, value: string }[]> = {
  anthropic: [
    { label: 'Claude Haiku 4.5', value: 'claude-haiku-4-5' },
    { label: 'Claude Sonnet 4.6', value: 'claude-sonnet-4-6' },
    { label: 'Claude Sonnet 5', value: 'claude-sonnet-5' },
    { label: 'Claude Opus 4.8', value: 'claude-opus-4-8' }
  ],
  openai: [
    { label: 'GPT-5.6 Sol', value: 'gpt-5.6-sol' },
    { label: 'GPT-5.6 Terra', value: 'gpt-5.6-terra' },
    { label: 'GPT-5.6 Luna', value: 'gpt-5.6-luna' },
    { label: 'GPT-5.5', value: 'gpt-5.5' },
    { label: 'GPT-5.5 Pro', value: 'gpt-5.5-pro' },
    { label: 'GPT-5.4', value: 'gpt-5.4' },
    { label: 'GPT-5.4 mini', value: 'gpt-5.4-mini' },
    { label: 'GPT-5.4 nano', value: 'gpt-5.4-nano' },
    { label: 'GPT-5.4 Pro', value: 'gpt-5.4-pro' },
    { label: 'GPT-5.2', value: 'gpt-5.2' },
    { label: 'GPT-5.2 Pro', value: 'gpt-5.2-pro' },
    { label: 'GPT-5.1', value: 'gpt-5.1' },
    { label: 'GPT-5', value: 'gpt-5' },
    { label: 'GPT-5 mini', value: 'gpt-5-mini' },
    { label: 'GPT-5 nano', value: 'gpt-5-nano' },
    { label: 'GPT-5 Pro', value: 'gpt-5-pro' },
    { label: 'GPT-4.1', value: 'gpt-4.1' },
    { label: 'GPT-4.1 mini', value: 'gpt-4.1-mini' },
    { label: 'GPT-4.1 nano', value: 'gpt-4.1-nano' },
    { label: 'GPT-4o', value: 'gpt-4o' },
    { label: 'GPT-4o mini', value: 'gpt-4o-mini' }
  ]
}

// ---- form (criar/editar) ----
const schema = z.object({
  name: z.string().min(1, 'Informe o nome'),
  model: z.string().min(1, 'Informe o modelo'),
  prompt: z.string().min(1, 'Informe o prompt'),
  description: z.string(),
  response_delay: z.coerce.number().min(0, 'Não pode ser negativo'),
  split_messages: z.boolean(),
  handoff_resume_hours: z.coerce.number().int().min(0, 'Não pode ser negativo').max(720, 'Máximo de 720 horas'),
  credential_id: z.number().nullable(),
  ai_hours_on: z.boolean(),
  ai_schedule: z.array(z.array(z.tuple([z.string(), z.string()]))),
  native_tools: z.array(z.string())
}).refine(data => editing.value !== null || data.credential_id !== null, {
  message: 'Escolha uma credencial',
  path: ['credential_id']
}).refine(data => !data.ai_hours_on || data.ai_schedule.flat().every(([start, end]) => start && end && (end > start || end === '00:00')), {
  message: 'Cada faixa precisa de início e fim, com o fim depois do início (00:00 = meia-noite)',
  path: ['ai_schedule']
}).refine(data => !data.ai_hours_on || data.ai_schedule.some(day => day.length), {
  message: 'Coloque ao menos uma faixa',
  path: ['ai_schedule']
})

const WEEKDAYS = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
type DayRanges = [string, string][]
const WHOLE_DAY: [string, string] = ['00:00', '00:00']
const NIGHT: DayRanges = [['00:00', '09:00'], ['18:00', '00:00']]
// atalhos que preenchem a semana; depois cada dia ajusta à vontade
const SCHEDULE_PRESETS: { label: string, days: DayRanges[] }[] = [
  { label: 'Comercial (seg-sex 9h-18h)', days: [...Array(5).fill([['09:00', '18:00']]), [], []] },
  { label: 'Fora do comercial', days: [...Array(5).fill(NIGHT), [WHOLE_DAY], [WHOLE_DAY]] }
]
const cloneSchedule = (days: DayRanges[]) => days.map(day => day.map(([start, end]) => [start, end] as [string, string]))

function copyDayToAll(day: number) {
  state.ai_schedule = state.ai_schedule.map(() => cloneSchedule([state.ai_schedule[day]!])[0]!)
}

// desligado = null: IA sempre atende
function aiHoursBody() {
  return { ai_schedule: state.ai_hours_on ? state.ai_schedule : null }
}

const rangesLabel = (day: DayRanges) => day.map(([start, end]) => start === end ? 'dia inteiro' : `${start}-${end}`).join(', ')
function hoursLabel(agent: Agent) {
  const days = (agent.ai_schedule ?? []).filter(day => day.length)
  return new Set(days.map(rangesLabel)).size === 1 ? `${days.length}d · ${rangesLabel(days[0]!)}` : 'Horário por dia'
}

const open = ref(false)
const modalTab = ref('geral')
const modalTabs = computed(() => [
  { label: 'Geral', icon: 'i-lucide-settings', value: 'geral', slot: 'geral' as const },
  { label: 'Prompt', icon: 'i-lucide-text', value: 'prompt', slot: 'prompt' as const },
  { label: 'Ferramentas', icon: 'i-lucide-wrench', value: 'ferramentas', slot: 'ferramentas' as const },
  ...(editing.value
    ? [
        { label: 'Documentos', icon: 'i-lucide-file-text', value: 'documentos', slot: 'documentos' as const },
        { label: 'Testar', icon: 'i-lucide-flask-conical', value: 'testar', slot: 'testar' as const }
      ]
    : [])
])

// erro de validação pode estar numa aba escondida: leva até ela
function onFormError(event: { errors: { name?: string }[] }) {
  const field = event.errors[0]?.name
  modalTab.value = field === 'prompt' ? 'prompt' : 'geral'
}

// ---- referências no prompt: dados do contato e ferramentas ----
const { variables: contactVariables, load: loadContactFields } = useContactFields()
onMounted(loadContactFields)
const customTools = ref<AgentTool[]>([])
async function loadCustomTools() {
  customTools.value = editing.value ? await api<AgentTool[]>(`/companies/${companyId.value}/agents/${editing.value.id}/tools`) : []
}
const NATIVE_TOOL_NAMES: Record<NativeTool, string[]> = {
  handoff: ['transferir_para_atendente'],
  followup: ['agendar_retomada'],
  save_field: ['salvar_dado_contato'],
  tag: ['etiquetar_contato'],
  pipedrive: ['criar_negocio', 'anotar_crm']
}
const promptInput = useTemplateRef<{ textareaRef: HTMLTextAreaElement | null }>('promptInput')

// insere no ponto do cursor (ou no fim) e devolve o foco logo depois do texto inserido
function insertAtCursor(text: string) {
  const el = promptInput.value?.textareaRef
  const start = el?.selectionStart ?? state.prompt.length
  const end = el?.selectionEnd ?? start
  state.prompt = state.prompt.slice(0, start) + text + state.prompt.slice(end)
  nextTick(() => {
    el?.focus()
    el?.setSelectionRange(start + text.length, start + text.length)
  })
}

const insertItems = computed(() => [
  [
    { type: 'label' as const, label: 'Dados do contato' },
    ...contactVariables.value.map(v => ({ label: v.label, suffix: `{{${v.key}}}`, onSelect: () => insertAtCursor(`{{${v.key}}}`) }))
  ],
  [
    { type: 'label' as const, label: 'Ferramentas' },
    ...state.native_tools.flatMap(t => NATIVE_TOOL_NAMES[t]).map(name => ({ label: name, icon: 'i-lucide-wrench', onSelect: () => insertAtCursor(name) })),
    ...customTools.value.filter(t => t.active).map(t => ({ label: t.name, icon: 'i-lucide-plug', onSelect: () => insertAtCursor(t.name) }))
  ]
])

// ---- autocomplete: "{{" ou "@" abre a lista de referências no ponto do cursor ----
type PromptRef = { label: string, insert: string, icon: string, group: string }
const allRefs = computed<PromptRef[]>(() => [
  ...contactVariables.value.map(v => ({ label: v.label, insert: `{{${v.key}}}`, icon: 'i-lucide-user', group: 'Dados do contato' })),
  ...state.native_tools.flatMap(t => NATIVE_TOOL_NAMES[t]).map(name => ({ label: name, insert: name, icon: 'i-lucide-wrench', group: 'Ferramenta' })),
  ...customTools.value.filter(t => t.active).map(t => ({ label: t.name, insert: t.name, icon: 'i-lucide-plug', group: 'Ferramenta' }))
])
const mention = ref<{ start: number, query: string, top: number, left: number } | null>(null)
const mentionIndex = ref(0)
const mentionOptions = computed(() => {
  if (!mention.value) return []
  const q = mention.value.query.toLowerCase()
  return allRefs.value.filter(r => `${r.label} ${r.insert}`.toLowerCase().includes(q)).slice(0, 8)
})

// posição do cursor em pixels: espelha o textarea numa div invisível com o mesmo estilo
function caretCoords(el: HTMLTextAreaElement, pos: number) {
  const mirror = document.createElement('div')
  const style = getComputedStyle(el)
  for (const prop of ['font', 'letterSpacing', 'lineHeight', 'padding', 'border', 'boxSizing', 'whiteSpace', 'wordWrap', 'width'] as const) {
    mirror.style[prop] = style[prop]
  }
  Object.assign(mirror.style, { position: 'absolute', visibility: 'hidden', whiteSpace: 'pre-wrap', overflowWrap: 'break-word' })
  mirror.textContent = el.value.slice(0, pos)
  const marker = document.createElement('span')
  marker.textContent = '\u200b'
  mirror.appendChild(marker)
  document.body.appendChild(mirror)
  const coords = { top: marker.offsetTop - el.scrollTop + parseFloat(style.lineHeight || '20'), left: marker.offsetLeft }
  mirror.remove()
  return coords
}

function updateMention() {
  const el = promptInput.value?.textareaRef
  if (!el) return
  const pos = el.selectionStart
  const match = el.value.slice(0, pos).match(/(\{\{|@)(\w*)$/)
  if (!match) {
    mention.value = null
    return
  }
  mention.value = { start: pos - match[0].length, query: match[2] ?? '', ...caretCoords(el, pos) }
  mentionIndex.value = 0
}

function pickMention(option: PromptRef) {
  const el = promptInput.value?.textareaRef
  if (!el || !mention.value) return
  const start = mention.value.start
  state.prompt = state.prompt.slice(0, start) + option.insert + state.prompt.slice(el.selectionStart)
  mention.value = null
  nextTick(() => {
    el.focus()
    el.setSelectionRange(start + option.insert.length, start + option.insert.length)
  })
}

// blur fecha a lista, mas depois do clique numa opção (mousedown.prevent já segura o foco)
function closeMentionSoon() {
  setTimeout(() => {
    mention.value = null
  }, 150)
}

function onPromptKeydown(event: KeyboardEvent) {
  if (!mention.value || !mentionOptions.value.length) return
  const total = mentionOptions.value.length
  if (event.key === 'ArrowDown') mentionIndex.value = (mentionIndex.value + 1) % total
  else if (event.key === 'ArrowUp') mentionIndex.value = (mentionIndex.value - 1 + total) % total
  else if (event.key === 'Enter' || event.key === 'Tab') pickMention(mentionOptions.value[mentionIndex.value]!)
  else if (event.key === 'Escape') mention.value = null
  else return
  event.preventDefault()
}

const unknownRefs = computed(() => {
  const known = new Set(contactVariables.value.map(v => v.key))
  return [...new Set([...state.prompt.matchAll(/\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}/gi)].map(m => m[1]!))]
    .filter(key => !known.has(key)).map(key => `{{${key}}}`)
})
const editing = ref<Agent | null>(null)
const saving = ref(false)
const state = reactive({
  name: '',
  description: '',
  model: '',
  prompt: '',
  response_delay: 0,
  split_messages: false,
  handoff_resume_hours: 24,
  credential_id: null as number | null,
  ai_hours_on: false,
  ai_schedule: cloneSchedule(SCHEDULE_PRESETS[0]!.days),
  native_tools: ['handoff', 'pipedrive'] as NativeTool[]
})

// ferramentas embutidas: o dono escolhe o que a IA pode fazer sozinha
const NATIVE_TOOL_ITEMS: { label: string, description: string, value: NativeTool }[] = [
  { label: 'Transferir para atendente', description: 'Pausa a IA e passa a conversa pra equipe, com o motivo', value: 'handoff' },
  { label: 'Agendar retomada', description: 'Programa uma mensagem pra data combinada ("me chama segunda")', value: 'followup' },
  { label: 'Salvar dado do contato', description: 'Preenche os campos personalizados com o que o cliente informou', value: 'save_field' },
  { label: 'Etiquetar contato', description: 'Adiciona ou remove etiquetas existentes', value: 'tag' },
  { label: 'Pipedrive', description: 'Cria negócio e anota no CRM (precisa do Pipedrive conectado)', value: 'pipedrive' }
]

const modelItems = computed(() => {
  const cred = credentials.value.find(c => c.id === state.credential_id)
  // edição sem credencial escolhida ("manter atual"): API não expõe o provider, mostra tudo
  const items = cred ? MODELS[cred.provider] : editing.value ? [...MODELS.anthropic, ...MODELS.openai] : []
  if (state.model && !items.some(i => i.value === state.model)) {
    return [...items, { label: state.model, value: state.model }]
  }
  return items
})

watch(() => state.credential_id, (id) => {
  const cred = credentials.value.find(c => c.id === id)
  if (cred && !MODELS[cred.provider].some(m => m.value === state.model)) state.model = ''
})

function openCreate() {
  editing.value = null
  Object.assign(state, { name: '', description: '', model: '', prompt: '', response_delay: 0, split_messages: false, handoff_resume_hours: 24, credential_id: null, ai_hours_on: false, ai_schedule: cloneSchedule(SCHEDULE_PRESETS[0]!.days), native_tools: ['handoff', 'pipedrive'] })
  documents.value = []
  customTools.value = []
  modalTab.value = 'geral'
  open.value = true
}

function openEdit(agent: Agent) {
  editing.value = agent
  Object.assign(state, {
    name: agent.name,
    description: agent.description,
    model: agent.model,
    prompt: agent.prompt,
    response_delay: agent.response_delay,
    split_messages: agent.split_messages,
    handoff_resume_hours: agent.handoff_resume_hours,
    credential_id: null, // API não expõe a credencial atual; null = manter
    ai_hours_on: agent.ai_schedule !== null,
    ai_schedule: cloneSchedule(agent.ai_schedule ?? SCHEDULE_PRESETS[0]!.days),
    native_tools: [...agent.native_tools]
  })
  loadDocuments(agent.id)
  loadCustomTools()
  modalTab.value = 'geral'
  open.value = true
}

async function save() {
  saving.value = true
  try {
    if (editing.value) {
      const body: Record<string, unknown> = {
        name: state.name,
        description: state.description,
        model: state.model,
        prompt: state.prompt,
        response_delay: Number(state.response_delay),
        split_messages: state.split_messages,
        handoff_resume_hours: Number(state.handoff_resume_hours),
        native_tools: state.native_tools,
        ...aiHoursBody()
      }
      if (state.credential_id !== null) body.credential_id = state.credential_id
      const updated = await api<Agent>(
        `/companies/${companyId.value}/agents/${editing.value.id}`,
        { method: 'PATCH', body }
      )
      agents.value = agents.value.map(a => (a.id === updated.id ? updated : a))
      toast.add({ title: 'Agente atualizado', color: 'success' })
    } else {
      const created = await api<Agent>(`/companies/${companyId.value}/agents`, {
        method: 'POST',
        body: { ...state, response_delay: Number(state.response_delay), handoff_resume_hours: Number(state.handoff_resume_hours), ...aiHoursBody() }
      })
      agents.value.push(created)
      toast.add({ title: 'Agente criado', color: 'success' })
    }
    open.value = false
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao salvar'), color: 'error' })
  } finally {
    saving.value = false
  }
}

async function remove(agent: Agent) {
  const ok = await confirmModal({
    title: `Excluir o agente "${agent.name}"?`,
    description: 'Contas WhatsApp ligadas a ele param de responder.'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}/agents/${agent.id}`, { method: 'DELETE' })
    agents.value = agents.value.filter(a => a.id !== agent.id)
    toast.add({ title: 'Agente excluído', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// ---- documentos (RAG) ----
const documents = ref<AgentDocument[]>([])
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

async function loadDocuments(agentId: number) {
  documents.value = await api<AgentDocument[]>(`/companies/${companyId.value}/agents/${agentId}/documents`)
}

async function onFilePicked(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file || !editing.value) return
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const doc = await api<AgentDocument>(
      `/companies/${companyId.value}/agents/${editing.value.id}/documents`,
      { method: 'POST', body: form }
    )
    documents.value.push(doc)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha no upload'), color: 'error' })
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function removeDocument(doc: AgentDocument) {
  if (!editing.value) return
  try {
    await api(`/companies/${companyId.value}/agents/${editing.value.id}/documents/${doc.id}`, { method: 'DELETE' })
    documents.value = documents.value.filter(d => d.id !== doc.id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// enquanto o slideover está aberto com doc processando, re-busca até estabilizar
const docPoll = setInterval(() => {
  if (open.value && editing.value && documents.value.some(d => d.status === 'processing')) {
    loadDocuments(editing.value.id)
  }
}, 4000)
onUnmounted(() => clearInterval(docPoll))

const statusBadge: Record<AgentDocument['status'], { label: string, color: 'warning' | 'success' | 'error' }> = {
  processing: { label: 'Processando', color: 'warning' },
  ready: { label: 'Pronto', color: 'success' },
  failed: { label: 'Falhou', color: 'error' }
}
</script>

<template>
  <UDashboardPanel id="agentes">
    <template #header>
      <UDashboardNavbar title="Agentes">
        <template #right>
          <UButton label="Novo agente" icon="i-lucide-plus" @click="openCreate()" />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UAlert
        v-if="!credentials.length"
        icon="i-lucide-key-round"
        title="Nenhuma credencial cadastrada"
        description="Um agente precisa de uma chave de API (Anthropic ou OpenAI) pra funcionar."
        variant="subtle"
        color="warning"
        class="mb-4"
        :actions="[{ label: 'Ir para Configurações', to: '/configuracoes', color: 'warning', variant: 'soft' }]"
      />

      <div v-if="agents.length" class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div
          v-for="agent in agents"
          :key="agent.id"
          class="rounded-xl ring-1 ring-default bg-elevated/40 p-5 flex flex-col gap-3 hover:ring-primary/40 transition"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="flex items-center gap-2.5 min-w-0">
              <div class="size-9 rounded-lg bg-primary/10 ring-1 ring-primary/20 flex items-center justify-center shrink-0">
                <UIcon name="i-lucide-bot" class="size-5 text-primary" />
              </div>
              <p class="font-display font-semibold truncate">{{ agent.name }}</p>
            </div>
            <div class="flex gap-1 shrink-0">
              <UButton icon="i-lucide-pencil" variant="ghost" color="neutral" size="xs" @click="openEdit(agent)" />
              <UButton icon="i-lucide-trash-2" variant="ghost" color="error" size="xs" @click="remove(agent)" />
            </div>
          </div>

          <p v-if="agent.description" class="text-sm text-muted line-clamp-2">{{ agent.description }}</p>

          <div class="flex items-center gap-2 flex-wrap">
            <UBadge variant="subtle" color="neutral" size="sm" class="font-mono">{{ agent.model }}</UBadge>
            <UBadge v-if="agent.ai_schedule" variant="subtle" color="neutral" size="sm" icon="i-lucide-clock">
              {{ hoursLabel(agent) }}
            </UBadge>
          </div>
        </div>
      </div>

      <div v-else class="flex-1 flex flex-col items-center justify-center gap-3 text-muted py-16">
        <div class="size-16 rounded-2xl bg-elevated ring-1 ring-default flex items-center justify-center">
          <UIcon name="i-lucide-bot" class="size-8" />
        </div>
        <p class="text-sm">Nenhum agente ainda. Crie o primeiro.</p>
        <UButton label="Novo agente" icon="i-lucide-plus" variant="soft" @click="openCreate()" />
      </div>
    </template>
  </UDashboardPanel>

  <UModal
    v-model:open="open"
    :title="editing ? `Editar ${editing.name}` : 'Novo agente'"
    description="A IA que vai responder seus clientes"
    :ui="{ content: 'sm:max-w-5xl', body: 'sm:p-0' }"
  >
    <template #body>
      <UForm id="agent-form" :schema="schema" :state="state" @submit="save" @error="onFormError">
        <UTabs v-model="modalTab" :items="modalTabs" variant="link" class="w-full" :ui="{ list: 'px-4 sm:px-6', content: 'px-4 sm:px-6 py-5' }">
          <template #geral>
            <div class="space-y-5">
            <UFormField name="name" label="Nome" required>
              <UInput v-model="state.name" placeholder="Atendente da loja" class="w-full" />
            </UFormField>

            <UFormField name="description" label="Descrição">
              <UInput v-model="state.description" placeholder="Responde dúvidas sobre produtos e pedidos" class="w-full" />
            </UFormField>

            <div class="grid grid-cols-2 gap-4">
              <UFormField name="credential_id" label="Credencial" :required="!editing">
                <USelect
                  v-model="state.credential_id"
                  :items="credentialItems"
                  :placeholder="editing ? 'Manter atual' : 'Escolha'"
                  class="w-full"
                />
              </UFormField>

              <UFormField name="model" label="Modelo" required>
                <USelect
                  v-model="state.model"
                  :items="modelItems"
                  :disabled="!state.credential_id && !editing"
                  :placeholder="!state.credential_id && !editing ? 'Escolha a credencial' : 'Escolha o modelo'"
                  class="w-full"
                />
              </UFormField>
            </div>

            <UFormField name="ai_hours_on">
              <div class="flex items-center gap-2">
                <USwitch v-model="state.ai_hours_on" label="Horário de atendimento da IA" />
                <AppTip text="Fora do horário a IA fica calada e responde quando abrir, se ninguém tiver respondido antes. Fim 00:00 = meia-noite: pra virar a noite, use uma faixa até 00:00 e outra a partir de 00:00 no dia seguinte" />
              </div>
            </UFormField>

            <UFormField v-if="state.ai_hours_on" name="ai_schedule">
              <div class="space-y-2">
                <div class="flex gap-1 flex-wrap">
                  <UButton
                    v-for="preset in SCHEDULE_PRESETS"
                    :key="preset.label"
                    :label="preset.label"
                    size="xs"
                    variant="soft"
                    color="neutral"
                    @click="() => { state.ai_schedule = cloneSchedule(preset.days) }"
                  />
                </div>
                <div class="rounded-lg ring-1 ring-default divide-y divide-default">
                  <div v-for="(label, day) in WEEKDAYS" :key="day" class="flex items-start gap-3 px-3 py-2">
                    <span class="w-9 pt-1.5 text-sm font-medium">{{ label }}</span>
                    <div class="flex-1 space-y-1.5">
                      <p v-if="!state.ai_schedule[day]!.length" class="pt-1.5 text-sm text-muted">
                        IA não atende
                      </p>
                      <div v-for="(range, i) in state.ai_schedule[day]" :key="i" class="flex items-center gap-1.5">
                        <UInput v-model="range[0]" type="time" size="sm" class="w-28" :aria-label="`${label}: início`" />
                        <span class="text-muted">-</span>
                        <UInput v-model="range[1]" type="time" size="sm" class="w-28" :aria-label="`${label}: fim`" />
                        <UBadge v-if="range[0] === '00:00' && range[1] === '00:00'" label="dia inteiro" variant="subtle" size="sm" />
                        <UButton
                          icon="i-lucide-x"
                          variant="ghost"
                          color="neutral"
                          size="xs"
                          :aria-label="`Remover faixa de ${label}`"
                          @click="() => { state.ai_schedule[day]!.splice(i, 1) }"
                        />
                      </div>
                    </div>
                    <div class="flex gap-0.5 shrink-0">
                      <UTooltip text="Adicionar faixa">
                        <UButton
                          icon="i-lucide-plus"
                          variant="ghost"
                          size="xs"
                          :aria-label="`Adicionar faixa em ${label}`"
                          @click="() => { state.ai_schedule[day]!.push(['18:00', '00:00']) }"
                        />
                      </UTooltip>
                      <UTooltip text="Dia inteiro">
                        <UButton
                          icon="i-lucide-sun-moon"
                          variant="ghost"
                          size="xs"
                          :aria-label="`${label} o dia inteiro`"
                          @click="() => { state.ai_schedule[day] = [[...WHOLE_DAY]] }"
                        />
                      </UTooltip>
                      <UTooltip text="Copiar pra todos os dias">
                        <UButton
                          icon="i-lucide-copy"
                          variant="ghost"
                          color="neutral"
                          size="xs"
                          :aria-label="`Copiar ${label} pra todos os dias`"
                          @click="copyDayToAll(day)"
                        />
                      </UTooltip>
                    </div>
                  </div>
                </div>
              </div>
            </UFormField>

            <UCollapsible class="flex flex-col gap-4">
              <UButton label="Avançado" variant="link" color="neutral" size="sm" trailing-icon="i-lucide-chevron-down" class="self-start px-0" />
              <template #content>
                <div class="space-y-4">
                  <AppField name="response_delay" label="Delay de resposta (segundos)" tip="Espera antes de responder: agrupa mensagens seguidas do cliente">
                    <UInput v-model.number="state.response_delay" type="number" min="0" class="w-full" />
                  </AppField>
                  <AppField
                    name="handoff_resume_hours"
                    label="IA volta sozinha depois de (horas)"
                    tip="Depois de um handoff ou de o atendente responder, a IA fica pausada por esse tempo. 0 = só volta manualmente"
                  >
                    <UInput v-model.number="state.handoff_resume_hours" type="number" min="0" max="720" class="w-full" />
                  </AppField>
                  <UFormField name="split_messages">
                    <div class="flex items-center gap-2">
                      <USwitch v-model="state.split_messages" label="Dividir resposta em várias mensagens" />
                      <AppTip text="A IA responde em blocos curtos em sequência, como uma pessoa digitando" />
                    </div>
                  </UFormField>
                </div>
              </template>
            </UCollapsible>
            </div>
          </template>

          <template #prompt>
            <div class="space-y-3">
              <div class="flex items-center justify-between gap-2">
                <p class="text-sm text-muted flex items-center gap-1.5">
                  Como a IA se comporta
                  <AppTip text="Referências entre chaves viram o dado do contato na hora de responder. Digite {{ ou @ pra escolher um dado do contato ou uma ferramenta" />
                </p>
                <UDropdownMenu :items="insertItems" :content="{ align: 'end' }" :ui="{ content: 'max-h-80 overflow-y-auto' }">
                  <UButton label="Inserir" icon="i-lucide-braces" variant="soft" size="sm" trailing-icon="i-lucide-chevron-down" />
                </UDropdownMenu>
              </div>
              <UFormField name="prompt">
                <div class="relative">
                  <UTextarea
                    ref="promptInput"
                    v-model="state.prompt"
                    :rows="22"
                    class="w-full"
                    :ui="{ base: 'font-mono text-sm' }"
                    placeholder="Você é o atendente da... O cliente usa {{cad}}. Quando ele pedir um humano, use transferir_para_atendente."
                    @input="updateMention"
                    @click="updateMention"
                    @keydown="onPromptKeydown"
                    @keyup="(e: KeyboardEvent) => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) updateMention() }"
                    @blur="closeMentionSoon"
                  />
                  <div
                    v-if="mention && mentionOptions.length"
                    class="absolute z-10 w-72 max-w-full rounded-md bg-default ring-1 ring-default shadow-lg py-1"
                    :style="{ top: `${mention.top + 4}px`, left: `${Math.min(mention.left, 400)}px` }"
                    role="listbox"
                  >
                    <button
                      v-for="(option, i) in mentionOptions"
                      :key="option.insert"
                      type="button"
                      role="option"
                      :aria-selected="i === mentionIndex"
                      class="w-full flex items-center gap-2 px-2.5 py-1.5 text-left text-sm"
                      :class="i === mentionIndex ? 'bg-elevated' : ''"
                      @mousedown.prevent="pickMention(option)"
                    >
                      <UIcon :name="option.icon" class="size-4 text-muted shrink-0" />
                      <span class="truncate flex-1">{{ option.label }}</span>
                      <span class="text-xs text-muted font-mono truncate">{{ option.group === 'Ferramenta' ? 'ferramenta' : option.insert }}</span>
                    </button>
                  </div>
                </div>
              </UFormField>
              <UAlert
                v-if="unknownRefs.length"
                icon="i-lucide-triangle-alert"
                color="warning"
                variant="subtle"
                :title="`Referência desconhecida: ${unknownRefs.join(', ')}`"
                description="Não é dado do contato nem campo personalizado: vai pra IA do jeito que está escrito."
              />
            </div>
          </template>

          <template #ferramentas>
            <div class="space-y-6">
              <UFormField name="native_tools" label="Ferramentas nativas">
                <UCheckboxGroup v-model="state.native_tools" :items="NATIVE_TOOL_ITEMS" />
              </UFormField>
              <div>
                <p class="text-sm font-medium mb-2 flex items-center gap-1.5">
                  Ferramentas personalizadas
                  <AppTip text="Chamadas HTTP pra sistemas externos (pagamento, agenda, licença...). A IA decide quando usar pela descrição e preenche os parâmetros com o que o cliente disse" />
                </p>
                <AgentToolsPanel
                  v-if="editing && companyId"
                  :company-id="companyId"
                  :agent-id="editing.id"
                  :is-owner="isOwner"
                  @changed="loadCustomTools"
                />
                <p v-else class="text-sm text-muted">
                  Crie o agente primeiro; depois as ferramentas personalizadas aparecem aqui.
                </p>
              </div>
            </div>
          </template>

          <template #testar>
            <AgentPlayground
              v-if="editing && companyId"
              :company-id="companyId"
              :agent-id="editing.id"
              :prompt="state.prompt"
              :native-tools="state.native_tools"
            />
          </template>

          <template #documentos>
        <div class="space-y-3">

          <input ref="fileInput" type="file" accept=".pdf,.txt,.md" class="hidden" @change="onFilePicked">
          <UTooltip text="PDF, TXT ou MD até 10MB. A IA responde com base nesses documentos">
          <UButton
            label="Enviar documento"
            icon="i-lucide-upload"
            variant="soft"
            :loading="uploading"
            @click="fileInput?.click()"
          />
          </UTooltip>

          <div v-if="documents.length" class="space-y-2">
            <div
              v-for="doc in documents"
              :key="doc.id"
              class="flex items-center gap-3 rounded-lg ring-1 ring-default px-3 py-2"
            >
              <UIcon name="i-lucide-file-text" class="size-4 text-muted shrink-0" />
              <span class="text-sm truncate flex-1">{{ doc.name }}</span>
              <UBadge
                :color="statusBadge[doc.status].color"
                variant="subtle"
                size="sm"
                :icon="doc.status === 'processing' ? 'i-lucide-loader-circle' : undefined"
                :ui="doc.status === 'processing' ? { leadingIcon: 'animate-spin' } : undefined"
              >
                {{ statusBadge[doc.status].label }}
              </UBadge>
              <UButton icon="i-lucide-x" variant="ghost" color="error" size="xs" @click="removeDocument(doc)" />
            </div>
          </div>
          <p v-else class="text-sm text-muted">Nenhum documento ainda.</p>
        </div>
          </template>
        </UTabs>
      </UForm>
    </template>
    <template #footer>
      <div class="flex justify-end gap-2 w-full">
        <UButton label="Cancelar" variant="ghost" color="neutral" @click="() => { open = false }" />
        <UButton type="submit" form="agent-form" :loading="saving" :label="editing ? 'Salvar alterações' : 'Criar agente'" />
      </div>
    </template>
  </UModal>
</template>
