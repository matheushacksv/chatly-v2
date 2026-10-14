<script setup lang="ts">
import { z } from 'zod'
import type { Agent, AgentDocument, Credential, NativeTool } from '~/types/api'

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
  ai_hours_start: z.string(),
  ai_hours_end: z.string(),
  ai_days: z.array(z.number()),
  native_tools: z.array(z.string())
}).refine(data => editing.value !== null || data.credential_id !== null, {
  message: 'Escolha uma credencial',
  path: ['credential_id']
}).refine(data => !data.ai_hours_on || (data.ai_hours_start && data.ai_hours_end), {
  message: 'Informe início e fim',
  path: ['ai_hours_end']
}).refine(data => !data.ai_hours_on || data.ai_days.length > 0, {
  message: 'Escolha ao menos um dia',
  path: ['ai_days']
})

const WEEKDAYS = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]

function toggleDay(day: number) {
  state.ai_days = state.ai_days.includes(day) ? state.ai_days.filter(d => d !== day) : [...state.ai_days, day].sort((a, b) => a - b)
}

// desligado = null nos dois: IA sempre atende
function aiHoursBody() {
  return {
    ai_hours_start: state.ai_hours_on ? state.ai_hours_start : null,
    ai_hours_end: state.ai_hours_on ? state.ai_hours_end : null,
    ai_days: state.ai_hours_on ? state.ai_days : ALL_DAYS
  }
}

function hoursLabel(agent: Agent) {
  return `${agent.ai_hours_start?.slice(0, 5)}-${agent.ai_hours_end?.slice(0, 5)}`
}

const open = ref(false)
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
  ai_hours_start: '09:00',
  ai_hours_end: '18:00',
  ai_days: [...ALL_DAYS],
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
  Object.assign(state, { name: '', description: '', model: '', prompt: '', response_delay: 0, split_messages: false, handoff_resume_hours: 24, credential_id: null, ai_hours_on: false, ai_hours_start: '09:00', ai_hours_end: '18:00', ai_days: [...ALL_DAYS], native_tools: ['handoff', 'pipedrive'] })
  documents.value = []
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
    ai_hours_on: agent.ai_hours_start !== null,
    ai_hours_start: agent.ai_hours_start?.slice(0, 5) ?? '09:00',
    ai_hours_end: agent.ai_hours_end?.slice(0, 5) ?? '18:00',
    ai_days: [...agent.ai_days],
    native_tools: [...agent.native_tools]
  })
  loadDocuments(agent.id)
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

          <p class="text-sm text-muted line-clamp-2 min-h-10">{{ agent.description || 'Sem descrição.' }}</p>

          <div class="flex items-center gap-2 flex-wrap">
            <UBadge variant="subtle" color="neutral" size="sm" class="font-mono">{{ agent.model }}</UBadge>
            <UBadge v-if="agent.response_delay" variant="subtle" color="neutral" size="sm" icon="i-lucide-timer">
              {{ agent.response_delay }}s
            </UBadge>
            <UBadge v-if="agent.ai_hours_start" variant="subtle" color="neutral" size="sm" icon="i-lucide-clock">
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

  <USlideover
    v-model:open="open"
    :title="editing ? 'Editar agente' : 'Novo agente'"
    :description="editing ? editing.name : 'A IA que vai responder seus clientes'"
    :ui="{ content: 'max-w-xl' }"
  >
    <template #body>
      <UForm :schema="schema" :state="state" class="space-y-5" @submit="save">
        <UFormField name="name" label="Nome" required>
          <UInput v-model="state.name" placeholder="Atendente da loja" class="w-full" />
        </UFormField>

        <UFormField name="description" label="Descrição">
          <UInput v-model="state.description" placeholder="Responde dúvidas sobre produtos e pedidos" class="w-full" />
        </UFormField>

        <div class="grid grid-cols-2 gap-4">
          <UFormField name="credential_id" label="Credencial" :required="!editing" :help="editing ? 'Vazio = manter atual' : undefined">
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

        <UFormField name="response_delay" label="Delay de resposta (segundos)" help="Espera antes de responder - agrupa mensagens seguidas do cliente">
          <UInput v-model.number="state.response_delay" type="number" min="0" class="w-full" />
        </UFormField>

        <UFormField
          name="handoff_resume_hours"
          label="IA volta sozinha depois de (horas)"
          help="Depois de um handoff ou de o atendente responder, a IA fica pausada por esse tempo. 0 = só volta manualmente"
        >
          <UInput v-model.number="state.handoff_resume_hours" type="number" min="0" max="720" class="w-full" />
        </UFormField>

        <UFormField name="ai_hours_on">
          <USwitch
            v-model="state.ai_hours_on"
            label="Horário de atendimento da IA"
            description="Fora do horário a IA fica calada e responde quando abrir, se ninguém tiver respondido antes"
          />
        </UFormField>

        <template v-if="state.ai_hours_on">
          <div class="grid grid-cols-2 gap-4">
            <UFormField name="ai_hours_start" label="Início">
              <UInput v-model="state.ai_hours_start" type="time" class="w-full" />
            </UFormField>
            <UFormField name="ai_hours_end" label="Fim" help="Antes do início vira a meia-noite (18:00-09:00)">
              <UInput v-model="state.ai_hours_end" type="time" class="w-full" />
            </UFormField>
          </div>

          <UFormField name="ai_days" label="Dias" help="Dia em que o horário começa">
            <div class="flex gap-1 flex-wrap">
              <UButton
                v-for="(label, day) in WEEKDAYS"
                :key="day"
                :label="label"
                size="sm"
                :variant="state.ai_days.includes(day) ? 'solid' : 'outline'"
                :color="state.ai_days.includes(day) ? 'primary' : 'neutral'"
                @click="toggleDay(day)"
              />
            </div>
          </UFormField>
        </template>

        <UFormField name="split_messages">
          <USwitch
            v-model="state.split_messages"
            label="Dividir resposta em várias mensagens"
            description="A IA responde em blocos curtos em sequência, como uma pessoa digitando"
          />
        </UFormField>

        <UFormField name="native_tools" label="O que a IA pode fazer" help="Ferramentas nativas. As personalizadas (sistemas externos) ficam abaixo, depois de salvar">
          <UCheckboxGroup v-model="state.native_tools" :items="NATIVE_TOOL_ITEMS" />
        </UFormField>

        <UFormField name="prompt" label="Prompt" required help="Instruções que definem como a IA se comporta">
          <UTextarea v-model="state.prompt" :rows="10" class="w-full" placeholder="Você é o atendente da..." />
        </UFormField>

        <UButton type="submit" block :loading="saving">
          {{ editing ? 'Salvar alterações' : 'Criar agente' }}
        </UButton>
      </UForm>

      <template v-if="editing">
        <USeparator class="my-6" label="Documentos (RAG)" />

        <div class="space-y-3">
          <p class="text-xs text-muted">
            PDF, TXT ou MD até 10MB. A IA usa esses documentos pra responder com base neles.
          </p>

          <input ref="fileInput" type="file" accept=".pdf,.txt,.md" class="hidden" @change="onFilePicked">
          <UButton
            label="Enviar documento"
            icon="i-lucide-upload"
            variant="soft"
            :loading="uploading"
            @click="fileInput?.click()"
          />

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

        <USeparator class="my-6" label="Ferramentas personalizadas" />
        <AgentToolsPanel v-if="companyId" :company-id="companyId" :agent-id="editing.id" :is-owner="isOwner" />
      </template>
    </template>
  </USlideover>
</template>
