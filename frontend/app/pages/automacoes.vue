<script setup lang="ts">
import type { Flow, FlowRun, FlowTrigger, LeadSource, Paginated, PipedrivePipeline, WhatsAppAccount } from '~/types/api'

const api = useApi()
const toast = useToast()
const confirm = useConfirm()
const config = useRuntimeConfig()
const { activeId: companyId } = useCompany()

// ---- contas WhatsApp ----
const { data: accounts } = await useAsyncData(
  () => `accounts:${companyId.value}`,
  () => api<WhatsAppAccount[]>(`/companies/${companyId.value}/whatsapp-accounts`),
  { watch: [companyId], default: () => [] as WhatsAppAccount[] }
)
const accountItems = computed(() =>
  (accounts.value ?? []).map(a => ({ label: a.phone_number_id, value: a.id }))
)

// ---- listas ----
const flows = ref<Flow[]>([])
const sources = ref<LeadSource[]>([])

async function load() {
  if (!companyId.value) return
  try {
    const [flowList, sourceList] = await Promise.all([
      api<Flow[]>(`/automations/${companyId.value}/flows`),
      api<LeadSource[]>(`/automations/${companyId.value}/lead-sources`)
    ])
    flows.value = flowList
    sources.value = sourceList
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao carregar as automações'), color: 'error' })
  }
}
watch(companyId, load, { immediate: true })

const tab = ref('flows')
const tabItems = [
  { label: 'Fluxos', value: 'flows', icon: 'i-lucide-workflow' },
  { label: 'Fontes de lead', value: 'sources', icon: 'i-lucide-inbox' }
]

const triggerLabels: Record<FlowTrigger, string> = {
  lead: 'Lead recebido',
  first_message: 'Primeira mensagem',
  keyword: 'Palavra-chave',
  pipedrive: 'Negócio no Pipedrive',
  campaign: 'Clique em botão de campanha'
}
const triggerItems = Object.entries(triggerLabels).map(([value, label]) => ({ label, value }))

const pdEventItems = [
  { label: 'Negócio criado na etapa', value: 'deal_added' },
  { label: 'Movido para a etapa', value: 'stage_changed' }
]
// stage_id é único por funil no Pipedrive, então basta ele (dispensa pipeline_id no filtro)
const pdStageItems = ref<{ label: string, value: number }[]>([])
const pdStagesError = ref('')
async function loadPipedriveStages() {
  if (pdStageItems.value.length || !companyId.value) return
  try {
    const pipelines = await api<PipedrivePipeline[]>(`/companies/${companyId.value}/pipedrive/pipelines`)
    pdStageItems.value = pipelines.flatMap(p => p.stages.map(s => ({ label: `${p.name} · ${s.name}`, value: s.id })))
  } catch (error) {
    pdStagesError.value = apiErrorDetail(error, 'Conecte o Pipedrive em Configurações')
  }
}

const dateFmt = new Intl.DateTimeFormat('pt-BR', {
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'
})

async function copy(text: string, title: string) {
  await navigator.clipboard.writeText(text)
  toast.add({ title, icon: 'i-lucide-check', color: 'success' })
}

// ---- editor ----
const editing = ref<Flow | null>(null)

function onSaved(saved: Flow) {
  const index = flows.value.findIndex(f => f.id === saved.id)
  if (index >= 0) flows.value[index] = saved
  if (editing.value?.id === saved.id) editing.value = saved
}

function closeEditor() {
  editing.value = null
}

// ---- novo fluxo ----
const createOpen = ref(false)
const creating = ref(false)
const draft = reactive({
  name: '',
  trigger: 'lead' as FlowTrigger,
  accountId: undefined as number | undefined,
  keyword: '',
  sourceId: undefined as number | undefined,
  pipedriveEvent: 'stage_changed',
  pipedriveStageId: undefined as number | undefined
})

function openCreate() {
  draft.name = ''
  draft.trigger = 'lead'
  draft.accountId = accounts.value?.[0]?.id
  draft.keyword = ''
  draft.sourceId = sources.value[0]?.id
  draft.pipedriveEvent = 'stage_changed'
  draft.pipedriveStageId = undefined
  createOpen.value = true
}

// carrega os estágios só quando o gatilho Pipedrive é escolhido
watch(() => draft.trigger, (t) => {
  if (t === 'pipedrive') loadPipedriveStages()
})

const canCreate = computed(() => Boolean(draft.name.trim() && draft.accountId))

async function createFlow() {
  if (!canCreate.value || creating.value) return
  creating.value = true
  try {
    const triggerConfig: Record<string, unknown> = {}
    if (draft.trigger === 'keyword') triggerConfig.keyword = draft.keyword.trim()
    if (draft.trigger === 'lead' && draft.sourceId) triggerConfig.source_id = draft.sourceId
    if (draft.trigger === 'pipedrive') {
      triggerConfig.event = draft.pipedriveEvent
      if (draft.pipedriveStageId) triggerConfig.stage_id = draft.pipedriveStageId
    }

    const created = await api<Flow>(`/automations/${companyId.value}/flows`, {
      method: 'POST',
      body: {
        name: draft.name.trim(),
        account_id: draft.accountId,
        trigger: draft.trigger,
        trigger_config: triggerConfig,
        graph: { nodes: [], edges: [] }
      }
    })
    flows.value.unshift(created)
    createOpen.value = false
    editing.value = created
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao criar o fluxo'), color: 'error' })
  } finally {
    creating.value = false
  }
}

// cópia pausada logo abaixo do original, pra adaptar sem mexer nele
async function duplicateFlow(flow: Flow) {
  try {
    const copy = await api<Flow>(`/automations/${companyId.value}/flows/${flow.id}/duplicate`, { method: 'POST' })
    flows.value.splice(flows.value.findIndex(f => f.id === flow.id) + 1, 0, copy)
    toast.add({ title: `"${copy.name}" criado, pausado`, icon: 'i-lucide-copy', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível duplicar'), color: 'error' })
  }
}

async function removeFlow(flow: Flow) {
  const ok = await confirm({
    title: `Excluir "${flow.name}"?`,
    description: 'As execuções em andamento param e o histórico some.'
  })
  if (!ok) return
  await api(`/automations/${companyId.value}/flows/${flow.id}`, { method: 'DELETE' })
  flows.value = flows.value.filter(f => f.id !== flow.id)
}

// ---- execuções ----
const runsOpen = ref(false)
const runsFlow = ref<Flow | null>(null)
const runs = ref<FlowRun[]>([])
const runsLoading = ref(false)
const openRunId = ref<number | null>(null)

const runStatus: Record<FlowRun['status'], { label: string, color: 'info' | 'warning' | 'success' | 'error' }> = {
  running: { label: 'Rodando', color: 'info' },
  waiting: { label: 'Esperando', color: 'warning' },
  done: { label: 'Concluída', color: 'success' },
  failed: { label: 'Falhou', color: 'error' }
}

async function openRuns(flow: Flow) {
  runsFlow.value = flow
  runs.value = []
  openRunId.value = null
  runsOpen.value = true
  runsLoading.value = true
  try {
    const page = await api<Paginated<FlowRun>>(`/automations/${companyId.value}/flows/${flow.id}/runs?limit=50`)
    runs.value = page.items
  } finally {
    runsLoading.value = false
  }
}

// ---- fontes de lead ----
const sourceOpen = ref(false)
const savingSource = ref(false)
const sourceDraft = reactive({ name: '', accountId: undefined as number | undefined })

function openSource() {
  sourceDraft.name = ''
  sourceDraft.accountId = accounts.value?.[0]?.id
  sourceOpen.value = true
}

async function createSource() {
  if (!sourceDraft.name.trim() || !sourceDraft.accountId || savingSource.value) return
  savingSource.value = true
  try {
    const created = await api<LeadSource>(`/automations/${companyId.value}/lead-sources`, {
      method: 'POST',
      body: { name: sourceDraft.name.trim(), account_id: sourceDraft.accountId }
    })
    sources.value.unshift(created)
    sourceOpen.value = false
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao criar a fonte'), color: 'error' })
  } finally {
    savingSource.value = false
  }
}

async function removeSource(source: LeadSource) {
  const ok = await confirm({
    title: `Excluir "${source.name}"?`,
    description: 'A URL para de aceitar leads na hora.'
  })
  if (!ok) return
  await api(`/automations/${companyId.value}/lead-sources/${source.id}`, { method: 'DELETE' })
  sources.value = sources.value.filter(s => s.id !== source.id)
}

const sourceUrl = (source: LeadSource) => `${config.public.apiBase}/automations/leads/${source.token}`

// campos personalizados cadastrados entram no exemplo pela chave
const { fields: contactFields, load: loadContactFields } = useContactFields()
watch(companyId, loadContactFields, { immediate: true })  // no F5 a empresa ativa chega depois do mount
const samplePayload = computed(() => JSON.stringify(
  {
    nome: 'Maria Silva',
    telefone: '5511999999999',
    email: 'maria@exemplo.com',
    origem: 'site',
    ...(contactFields.value.length ? { campos: Object.fromEntries(contactFields.value.map(f => [f.key, '...'])) } : {})
  },
  null,
  2
))

// ---- link wa.me ----
// o número real não fica no banco (a conta guarda só o phone_number_id), então vem daqui
const waNumber = ref('')
const waText = ref('Oi! Quero saber mais.')
const waLink = computed(() => {
  const digits = waNumber.value.replace(/\D/g, '')
  return digits ? `https://wa.me/${digits}?text=${encodeURIComponent(waText.value)}` : ''
})
</script>

<template>
  <UDashboardPanel id="automacoes">
    <template #header>
      <UDashboardNavbar :title="editing ? editing.name : 'Automações'">
        <template #right>
          <UButton
            v-if="editing"
            label="Voltar"
            icon="i-lucide-arrow-left"
            variant="ghost"
            @click="closeEditor()"
          />
          <template v-else>
            <UButton
              v-if="tab === 'flows'"
              label="Novo fluxo"
              icon="i-lucide-plus"
              :disabled="!accountItems.length"
              @click="openCreate()"
            />
            <UButton
              v-else
              label="Nova fonte"
              icon="i-lucide-plus"
              :disabled="!accountItems.length"
              @click="openSource()"
            />
          </template>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <FlowEditor
        v-if="editing"
        :key="editing.id"
        :flow="editing"
        :company-id="companyId ?? 0"
        @saved="onSaved"
        @close="closeEditor()"
      />

      <template v-else>
        <div
          v-if="!accountItems.length"
          class="p-4 text-sm text-muted"
        >
          Nenhuma conta WhatsApp conectada. Configure uma em
          <NuxtLink
            to="/configuracoes"
            class="text-primary"
          >Configurações</NuxtLink>.
        </div>

        <template v-else>
          <UTabs
            v-model="tab"
            :items="tabItems"
            class="mb-4"
          />

          <!-- fluxos -->
          <div
            v-if="tab === 'flows'"
            class="space-y-3"
          >
            <div
              v-for="flow in flows"
              :key="flow.id"
              class="rounded-xl ring-1 ring-default bg-elevated/40 p-4 flex items-center gap-4"
            >
              <div class="size-9 rounded-lg bg-primary/10 ring-1 ring-primary/20 flex items-center justify-center shrink-0">
                <UIcon
                  name="i-lucide-workflow"
                  class="size-5 text-primary"
                />
              </div>
              <button
                class="flex-1 min-w-0 text-left"
                @click="editing = flow"
              >
                <div class="flex items-center gap-2">
                  <p class="font-display font-semibold truncate">
                    {{ flow.name }}
                  </p>
                  <UBadge
                    :color="flow.active ? 'success' : 'neutral'"
                    variant="subtle"
                    size="sm"
                  >
                    {{ flow.active ? 'Ativo' : 'Pausado' }}
                  </UBadge>
                </div>
                <p class="text-xs text-muted truncate">
                  {{ triggerLabels[flow.trigger] }}
                  <span v-if="flow.trigger_config.keyword"> · "{{ flow.trigger_config.keyword }}"</span>
                  <span v-if="flow.trigger === 'pipedrive'"> · {{ flow.trigger_config.event === 'deal_added' ? 'criado' : 'movido' }}</span>
                  · {{ flow.graph.nodes.length }} bloco(s)
                  · editado {{ dateFmt.format(new Date(flow.updated_at)) }}
                </p>
              </button>
              <UButton
                :label="`${flow.run_count} execuções`"
                icon="i-lucide-history"
                variant="ghost"
                size="sm"
                class="shrink-0 hidden sm:flex"
                @click="openRuns(flow)"
              />
              <UButton
                icon="i-lucide-copy"
                color="neutral"
                variant="ghost"
                size="sm"
                class="shrink-0"
                aria-label="Duplicar fluxo"
                title="Duplicar"
                @click="duplicateFlow(flow)"
              />
              <UButton
                icon="i-lucide-trash-2"
                color="neutral"
                variant="ghost"
                size="sm"
                class="shrink-0"
                @click="removeFlow(flow)"
              />
            </div>

            <div
              v-if="!flows.length"
              class="flex-1 flex flex-col items-center justify-center gap-3 text-muted py-16"
            >
              <div class="size-16 rounded-2xl bg-elevated ring-1 ring-default flex items-center justify-center">
                <UIcon
                  name="i-lucide-workflow"
                  class="size-8"
                />
              </div>
              <p class="text-sm">
                Nenhum fluxo ainda. Desenhe o primeiro.
              </p>
              <UButton
                label="Novo fluxo"
                icon="i-lucide-plus"
                variant="soft"
                @click="openCreate()"
              />
            </div>
          </div>

          <!-- fontes de lead -->
          <div
            v-else
            class="space-y-4"
          >
            <div
              v-for="source in sources"
              :key="source.id"
              class="rounded-xl ring-1 ring-default bg-elevated/40 p-4 space-y-3"
            >
              <div class="flex items-center gap-3">
                <UIcon
                  name="i-lucide-inbox"
                  class="size-5 text-primary shrink-0"
                />
                <p class="font-display font-semibold truncate flex-1">
                  {{ source.name }}
                </p>
                <UBadge
                  v-if="!source.active"
                  color="neutral"
                  variant="subtle"
                  size="sm"
                >
                  Inativa
                </UBadge>
                <UButton
                  icon="i-lucide-trash-2"
                  color="neutral"
                  variant="ghost"
                  size="sm"
                  @click="removeSource(source)"
                />
              </div>
              <div class="flex items-center gap-2">
                <code class="flex-1 min-w-0 truncate rounded-lg bg-default ring-1 ring-default px-3 py-2 text-xs">
                  POST {{ sourceUrl(source) }}
                </code>
                <UButton
                  icon="i-lucide-copy"
                  variant="soft"
                  size="sm"
                  @click="copy(sourceUrl(source), 'URL copiada')"
                />
              </div>
            </div>

            <div
              v-if="!sources.length"
              class="flex flex-col items-center justify-center gap-3 text-muted py-10"
            >
              <div class="size-16 rounded-2xl bg-elevated ring-1 ring-default flex items-center justify-center">
                <UIcon
                  name="i-lucide-inbox"
                  class="size-8"
                />
              </div>
              <p class="text-sm">
                Nenhuma fonte ainda. Crie uma pra receber leads.
              </p>
              <UButton
                label="Nova fonte"
                icon="i-lucide-plus"
                variant="soft"
                @click="openSource()"
              />
            </div>

            <div class="rounded-xl ring-1 ring-default p-4 space-y-2">
              <p class="text-sm font-semibold flex items-center gap-1.5">
                Corpo esperado
                <AppTip text="Só telefone é obrigatório. O resto vira variável do fluxo. Em campos, use a chave de um campo personalizado já cadastrado (Contatos, Campos): o valor fica salvo no contato. Chave desconhecida é ignorada e volta em ignored_fields na resposta" />
              </p>
              <pre class="rounded-lg bg-elevated ring-1 ring-default p-3 text-xs overflow-x-auto">{{ samplePayload }}</pre>
            </div>

            <div class="rounded-xl ring-1 ring-default p-4 space-y-3">
              <p class="text-sm font-semibold flex items-center gap-1.5">
                Link de conversa (wa.me)
                <AppTip text="O lead manda a primeira mensagem e abre a janela de 24h sem custo de template" />
              </p>
              <div class="grid gap-2 sm:grid-cols-2">
                <UInput
                  v-model="waNumber"
                  placeholder="5511999999999"
                  icon="i-lucide-phone"
                />
                <UInput
                  v-model="waText"
                  placeholder="Mensagem pré-pronta"
                />
              </div>
              <div
                v-if="waLink"
                class="flex items-center gap-2"
              >
                <code class="flex-1 min-w-0 truncate rounded-lg bg-elevated ring-1 ring-default px-3 py-2 text-xs">
                  {{ waLink }}
                </code>
                <UButton
                  icon="i-lucide-copy"
                  variant="soft"
                  size="sm"
                  @click="copy(waLink, 'Link copiado')"
                />
              </div>
            </div>
          </div>
        </template>
      </template>
    </template>
  </UDashboardPanel>

  <USlideover
    v-model:open="createOpen"
    title="Novo fluxo"
    description="Escolha o gatilho, o desenho vem depois"
  >
    <template #body>
      <div class="space-y-4">
        <UFormField
          label="Nome"
          required
        >
          <UInput
            v-model="draft.name"
            placeholder="Boas-vindas do site"
            class="w-full"
          />
        </UFormField>
        <UFormField
          label="Conta WhatsApp"
          required
        >
          <USelect
            v-model="draft.accountId"
            :items="accountItems"
            icon="i-lucide-phone"
            class="w-full"
          />
        </UFormField>
        <UFormField
          label="Gatilho"
          required
        >
          <USelect
            v-model="draft.trigger"
            :items="triggerItems"
            class="w-full"
          />
        </UFormField>
        <AppField
          v-if="draft.trigger === 'keyword'"
          label="Palavra-chave"
          required
          tip="Dispara quando a mensagem do contato contém esse termo"
        >
          <UInput
            v-model="draft.keyword"
            placeholder="orçamento"
            class="w-full"
          />
        </AppField>
        <AppField
          v-if="draft.trigger === 'lead' && sources.length"
          label="Fonte"
          tip="Deixe vazio pra valer pra qualquer fonte"
        >
          <USelect
            v-model="draft.sourceId"
            :items="sources.map(s => ({ label: s.name, value: s.id }))"
            class="w-full"
          />
        </AppField>
        <UAlert
          v-if="draft.trigger === 'campaign'"
          color="info"
          variant="subtle"
          icon="i-lucide-megaphone"
          description="Escolha este fluxo ao criar a campanha. Quando o contato clicar num botão do template, o fluxo roda com {{botao}} = texto do botão. Use uma Condição em 'Botão clicado' pra separar cada botão."
        />
        <template v-if="draft.trigger === 'pipedrive'">
          <UFormField
            label="Quando"
            required
          >
            <USelect
              v-model="draft.pipedriveEvent"
              :items="pdEventItems"
              class="w-full"
            />
          </UFormField>
          <AppField
            label="Etapa"
            tip="Deixe vazio pra valer pra qualquer etapa. Use o mesmo número configurado em 'recebe leads do Pipedrive'."
          >
            <USelect
              v-model="draft.pipedriveStageId"
              :items="pdStageItems"
              :placeholder="pdStagesError || 'Escolha a etapa'"
              :disabled="!pdStageItems.length"
              class="w-full"
            />
          </AppField>
        </template>
        <UButton
          block
          icon="i-lucide-pencil-ruler"
          label="Criar e desenhar"
          :loading="creating"
          :disabled="!canCreate"
          @click="createFlow()"
        />
      </div>
    </template>
  </USlideover>

  <USlideover
    v-model:open="sourceOpen"
    title="Nova fonte de lead"
    description="Gera uma URL pública pra receber leads"
  >
    <template #body>
      <div class="space-y-4">
        <AppField
          label="Nome"
          required
          tip="Site, anúncio, planilha... só pra você identificar"
        >
          <UInput
            v-model="sourceDraft.name"
            placeholder="Formulário do site"
            class="w-full"
          />
        </AppField>
        <AppField
          label="Conta WhatsApp"
          required
          tip="Número que vai iniciar a conversa"
        >
          <USelect
            v-model="sourceDraft.accountId"
            :items="accountItems"
            icon="i-lucide-phone"
            class="w-full"
          />
        </AppField>
        <UButton
          block
          icon="i-lucide-plus"
          label="Criar fonte"
          :loading="savingSource"
          :disabled="!sourceDraft.name.trim() || !sourceDraft.accountId"
          @click="createSource()"
        />
      </div>
    </template>
  </USlideover>

  <UModal
    v-model:open="runsOpen"
    :title="runsFlow?.name ?? 'Execuções'"
    description="Quem entrou no fluxo e por onde passou"
  >
    <template #body>
      <div
        v-if="runsLoading"
        class="flex justify-center py-8"
      >
        <UIcon
          name="i-lucide-loader-circle"
          class="size-5 animate-spin text-muted"
        />
      </div>
      <p
        v-else-if="!runs.length"
        class="text-sm text-muted"
      >
        Ninguém entrou nesse fluxo ainda.
      </p>
      <div
        v-else
        class="divide-y divide-default"
      >
        <div
          v-for="run in runs"
          :key="run.id"
          class="py-2"
        >
          <button
            class="w-full flex items-center gap-3 text-left"
            @click="openRunId = openRunId === run.id ? null : run.id"
          >
            <UBadge
              :color="runStatus[run.status].color"
              variant="subtle"
              size="sm"
            >
              {{ runStatus[run.status].label }}
            </UBadge>
            <span class="text-sm truncate flex-1">{{ run.contact_name || 'Sem nome' }}</span>
            <span class="text-xs text-muted shrink-0">{{ dateFmt.format(new Date(run.created_at)) }}</span>
            <UIcon
              name="i-lucide-chevron-down"
              class="size-4 text-dimmed transition"
              :class="openRunId === run.id ? 'rotate-180' : ''"
            />
          </button>
          <div
            v-if="openRunId === run.id"
            class="pt-3 pl-1"
          >
            <FlowRunTimeline :run="run" />
          </div>
        </div>
      </div>
    </template>
  </UModal>
</template>
