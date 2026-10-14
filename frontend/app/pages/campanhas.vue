<script setup lang="ts">
import type { Campaign, Contact, CsvRecipient, Flow, Paginated, Tag, Template, WhatsAppAccount } from '~/types/api'

const api = useApi()
const toast = useToast()
const { activeId: companyId } = useCompany()

// ---- contas WhatsApp ----
const { data: accounts } = await useAsyncData(
  () => `accounts:${companyId.value}`,
  () => api<WhatsAppAccount[]>(`/companies/${companyId.value}/whatsapp-accounts`),
  { watch: [companyId], default: () => [] as WhatsAppAccount[] }
)
const accountId = ref<number | null>(null)
watch(accounts, list => { accountId.value = list?.[0]?.id ?? null }, { immediate: true })

const accountItems = computed(() =>
  (accounts.value ?? []).map(a => ({ label: a.phone_number_id, value: a.id }))
)

// ---- campanhas ----
const campaigns = ref<Campaign[]>([])

async function loadCampaigns() {
  if (!companyId.value) return
  const page = await api<Paginated<Campaign>>(`/companies/${companyId.value}/campaigns?limit=100`)
  campaigns.value = page.items
}
watch(companyId, loadCampaigns, { immediate: true })

// campanha enviando = contadores mudando no banco → re-busca até terminar
const poll = setInterval(() => {
  // agendada que venceu: o beat dispara em até 1 min, então segue buscando até virar "enviando"
  const due = (c: Campaign) => c.status === 'scheduled' && !!c.scheduled_at && new Date(c.scheduled_at) <= new Date()
  if (campaigns.value.some(c => c.status === 'sending' || due(c))) loadCampaigns()
}, 5000)
onUnmounted(() => clearInterval(poll))

const statusBadge: Record<Campaign['status'], { label: string, color: 'warning' | 'success' | 'error' | 'info' | 'neutral' }> = {
  scheduled: { label: 'Agendada', color: 'info' },
  canceled: { label: 'Cancelada', color: 'neutral' },
  sending: { label: 'Enviando', color: 'warning' },
  done: { label: 'Concluída', color: 'success' },
  failed: { label: 'Falha', color: 'error' }
}

const dateFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })

// ---- detalhes (delivered/read só vêm no GET individual) ----
const detail = ref<Campaign | null>(null)
const detailOpen = ref(false)

async function openDetail(campaign: Campaign) {
  detail.value = null
  detailOpen.value = true
  detail.value = await api<Campaign>(`/companies/${companyId.value}/campaigns/${campaign.id}`)
}

// ---- nova campanha ----
const createOpen = ref(false)
const saving = ref(false)
const name = ref('')
const template = ref<Template | null>(null)
const params = ref<string[]>([])
const headerMediaId = ref('')

type RecipientMode = 'all' | 'select' | 'csv'
const mode = ref<RecipientMode>('all')
const modeItems = [
  { label: 'Todos os contatos', value: 'all' },
  { label: 'Selecionar contatos', value: 'select' },
  { label: 'Importar CSV', value: 'csv' }
]

// fluxo disparado no clique de um botão do template + filtro por etiqueta
const flowId = ref<number | undefined>(undefined)
const tagGroups = ref<number[][]>([])
const audienceOpen = ref(false)
const audienceCount = ref<number | null>(null)
const tagName = (id: number) => tags.value.find(t => t.id === id)?.name ?? '?'
// "(vip e quente) ou frio, exceto bloqueado"
const audienceSummary = computed(() => {
  const groups = tagGroups.value.map(g => g.length > 1 ? `(${g.map(tagName).join(' e ')})` : tagName(g[0]!))
  const text = groups.length ? groups.join(' ou ') : 'Todos os contatos'
  return excludeTagIds.value.length ? `${text}, exceto ${excludeTagIds.value.map(tagName).join(', ')}` : text
})
const excludeTagIds = ref<number[]>([])
const flows = ref<Flow[]>([])
const tags = ref<Tag[]>([])
const flowItems = computed(() =>
  flows.value.filter(f => f.trigger === 'campaign' && f.account === accountId.value).map(f => ({
    label: f.active ? f.name : `${f.name} (inativo)`,
    value: f.id
  }))
)
const tagItems = computed(() => tags.value.map(t => ({ label: t.name, value: t.id })))

watch(createOpen, async (open) => {
  if (!open) return
  ;[flows.value, tags.value] = await Promise.all([
    api<Flow[]>(`/automations/${companyId.value}/flows`),
    api<Tag[]>(`/companies/${companyId.value}/tags`)
  ])
})

// agendamento: "agora" ou data/hora futura (input nativo datetime-local)
const when = ref<'now' | 'later'>('now')
const whenItems = [{ label: 'Agora', value: 'now' }, { label: 'Agendar', value: 'later' }]
const scheduledLocal = ref('')
const toLocalInput = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
const minScheduleLocal = computed(() => (createOpen.value ? toLocalInput(new Date()) : ''))

const canceling = ref(false)
async function cancelScheduled() {
  if (!detail.value || canceling.value) return
  canceling.value = true
  try {
    detail.value = await api<Campaign>(`/companies/${companyId.value}/campaigns/${detail.value.id}/cancel`, { method: 'POST' })
    campaigns.value = campaigns.value.map(c => c.id === detail.value!.id ? { ...c, status: detail.value!.status } : c)
    toast.add({ title: 'Agendamento cancelado', icon: 'i-lucide-calendar-x' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível cancelar'), color: 'error' })
  } finally {
    canceling.value = false
  }
}

function openCreate() {
  when.value = 'now'
  scheduledLocal.value = ''
  flowId.value = undefined
  tagGroups.value = []
  audienceCount.value = null
  excludeTagIds.value = []
  name.value = ''
  template.value = null
  params.value = []
  headerMediaId.value = ''
  mode.value = 'all'
  selectedContactIds.value = []
  csvNumbers.value = []
  csvInvalid.value = []
  createOpen.value = true
}

// seleção manual: contatos da conta escolhida
const contacts = ref<Contact[]>([])
const selectedContactIds = ref<number[]>([])

watch([createOpen, accountId], async ([open]) => {
  if (!open || !accountId.value) return
  const page = await api<Paginated<Contact>>(
    `/companies/${companyId.value}/whatsapp-accounts/${accountId.value}/contacts?limit=100`
  )
  contacts.value = page.items
})

function toggleContact(id: number) {
  selectedContactIds.value = selectedContactIds.value.includes(id)
    ? selectedContactIds.value.filter(c => c !== id)
    : [...selectedContactIds.value, id]
}

// CSV: o modal de mapeamento diz o que é cada coluna; backend revalida os números
const csvInput = ref<HTMLInputElement | null>(null)
const csvNumbers = ref<CsvRecipient[]>([])
const csvInvalid = ref<string[]>([])
const csvText = ref('')
const mappingOpen = ref(false)
const { fields: contactFields } = useContactFields()
// resumo dos campos que o CSV vai preencher nos contatos
const csvColumnsText = computed(() => {
  const keys = new Set(csvNumbers.value.flatMap(r => Object.keys(r.fields)))
  const labels = contactFields.value.filter(f => keys.has(f.key)).map(f => f.label)
  return labels.length ? `Campos: ${labels.join(', ')}` : undefined
})

function onCsvPicked(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    csvText.value = String(reader.result)
    mappingOpen.value = true
  }
  reader.readAsText(file)
  ;(event.target as HTMLInputElement).value = ''
}

function onCsvMapped({ rows, invalid }: { rows: CsvRecipient[], invalid: string[] }) {
  csvNumbers.value = rows
  csvInvalid.value = invalid
}

const campaignMissingReasons = computed(() => {
  const reasons: string[] = []
  if (!name.value.trim()) reasons.push('dê um nome pra campanha')
  if (!template.value) reasons.push('escolha um template')
  else if (!params.value.every(p => p.trim())) reasons.push('preencha todas as variáveis do corpo')
  if (template.value && ['IMAGE', 'VIDEO', 'DOCUMENT'].includes(template.value.header_format) && !template.value.has_header_media && !headerMediaId.value) {
    reasons.push('envie o arquivo do cabeçalho')
  }
  if (when.value === 'later' && !(scheduledLocal.value && new Date(scheduledLocal.value) > new Date())) reasons.push('escolha uma data e hora futura')
  if (mode.value === 'select' && !selectedContactIds.value.length) reasons.push('selecione ao menos um contato')
  if (mode.value === 'csv' && !csvNumbers.value.length) reasons.push('importe um CSV com números')
  return reasons
})
const canSubmit = computed(() => campaignMissingReasons.value.length === 0)

async function create() {
  if (!canSubmit.value || !template.value || saving.value) return
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      account_id: accountId.value,
      name: name.value.trim(),
      template_name: template.value.name,
      template_language: template.value.language,
      body_params: params.value,
      header_media_id: headerMediaId.value,
      flow_id: flowId.value ?? null,
      exclude_tag_ids: excludeTagIds.value,
      // datetime-local vem sem fuso: o Date do navegador interpreta no fuso local e o ISO leva o offset
      scheduled_at: when.value === 'later' ? new Date(scheduledLocal.value).toISOString() : null
    }
    if (mode.value === 'all') body.tag_groups = tagGroups.value
    if (mode.value === 'select') body.contact_ids = selectedContactIds.value
    if (mode.value === 'csv') {
      body.contact_ids = []
      body.numbers = csvNumbers.value
    }
    const created = await api<Campaign>(`/companies/${companyId.value}/campaigns`, { method: 'POST', body })
    campaigns.value.unshift(created)
    createOpen.value = false
    toast.add({
      title: created.status === 'scheduled' ? `Campanha agendada para ${dateFmt.format(new Date(created.scheduled_at!))}` : 'Campanha disparada',
      icon: created.status === 'scheduled' ? 'i-lucide-calendar-clock' : 'i-lucide-megaphone',
      color: 'success'
    })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao criar campanha'), color: 'error' })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <UDashboardPanel id="campanhas">
    <template #header>
      <UDashboardNavbar title="Campanhas">
        <template #right>
          <USelect
            v-if="accountItems.length > 1"
            v-model="accountId"
            :items="accountItems"
            icon="i-lucide-phone"
            size="sm"
          />
          <UButton label="Nova campanha" icon="i-lucide-plus" :disabled="!accountId" @click="openCreate()" />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div v-if="!accountId" class="p-4 text-sm text-muted">
        Nenhuma conta WhatsApp conectada. Configure uma em
        <NuxtLink to="/configuracoes" class="text-primary">Configurações</NuxtLink>.
      </div>

      <div v-else-if="campaigns.length" class="space-y-3">
        <button
          v-for="campaign in campaigns"
          :key="campaign.id"
          class="w-full text-left rounded-xl ring-1 ring-default bg-elevated/40 p-4 hover:ring-primary/40 transition flex items-center gap-4"
          @click="openDetail(campaign)"
        >
          <div class="size-9 rounded-lg bg-primary/10 ring-1 ring-primary/20 flex items-center justify-center shrink-0">
            <UIcon name="i-lucide-megaphone" class="size-5 text-primary" />
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <p class="font-display font-semibold truncate">{{ campaign.name }}</p>
              <UBadge :color="statusBadge[campaign.status].color" variant="subtle" size="sm">
                {{ statusBadge[campaign.status].label }}
              </UBadge>
            </div>
            <p class="text-xs text-muted truncate">
              <span class="font-mono">{{ campaign.template_name }}</span>
              · <template v-if="campaign.scheduled_at && ['scheduled', 'canceled'].includes(campaign.status)">
                agendada para {{ dateFmt.format(new Date(campaign.scheduled_at)) }}
              </template>
              <template v-else>
                {{ dateFmt.format(new Date(campaign.created_at)) }}
              </template>
            </p>
          </div>
          <div class="text-right shrink-0">
            <p class="text-sm font-medium tabular-nums">{{ campaign.sent }}/{{ campaign.total }} enviadas</p>
            <p v-if="campaign.failed" class="text-xs text-error tabular-nums">{{ campaign.failed }} falhas</p>
          </div>
          <UProgress
            :model-value="campaign.sent + campaign.failed"
            :max="campaign.total"
            class="w-28 shrink-0 hidden sm:block"
            size="sm"
          />
        </button>
      </div>

      <div v-else class="flex-1 flex flex-col items-center justify-center gap-3 text-muted py-16">
        <div class="size-16 rounded-2xl bg-elevated ring-1 ring-default flex items-center justify-center">
          <UIcon name="i-lucide-megaphone" class="size-8" />
        </div>
        <p class="text-sm">Nenhuma campanha ainda. Dispare a primeira.</p>
        <UButton label="Nova campanha" icon="i-lucide-plus" variant="soft" :disabled="!accountId" @click="openCreate()" />
      </div>
    </template>
  </UDashboardPanel>

  <UModal v-model:open="detailOpen" :title="detail?.name ?? 'Campanha'" :description="detail ? `Template ${detail.template_name}` : undefined">
    <template #body>
      <div v-if="!detail" class="flex justify-center py-8">
        <UIcon name="i-lucide-loader-circle" class="size-5 animate-spin text-muted" />
      </div>
      <UAlert
        v-if="detail?.status === 'scheduled' && detail.scheduled_at"
        icon="i-lucide-calendar-clock"
        color="info"
        variant="subtle"
        class="mb-3"
        :title="`Agendada para ${dateFmt.format(new Date(detail.scheduled_at))}`"
        description="Dispara sozinha nesse horário (até 1 minuto depois)."
        :actions="[{ label: 'Cancelar agendamento', color: 'error', variant: 'soft', loading: canceling, onClick: cancelScheduled }]"
      />
      <div v-if="detail" class="grid grid-cols-2 gap-3">
        <div class="rounded-lg ring-1 ring-default p-3">
          <p class="text-xs text-muted">Destinatários</p>
          <p class="text-xl font-semibold tabular-nums">{{ detail.total }}</p>
        </div>
        <div class="rounded-lg ring-1 ring-default p-3">
          <p class="text-xs text-muted">Enviadas</p>
          <p class="text-xl font-semibold tabular-nums">{{ detail.sent }}</p>
        </div>
        <div class="rounded-lg ring-1 ring-default p-3">
          <p class="text-xs text-muted">Entregues</p>
          <!-- lida implica entregue: status só guarda o mais recente -->
          <p class="text-xl font-semibold tabular-nums">{{ detail.delivered + detail.read }}</p>
        </div>
        <div class="rounded-lg ring-1 ring-default p-3">
          <p class="text-xs text-muted">Lidas</p>
          <p class="text-xl font-semibold tabular-nums">{{ detail.read }}</p>
        </div>
        <div v-if="detail.failed" class="rounded-lg ring-1 ring-error/30 bg-error/5 p-3 col-span-2">
          <p class="text-xs text-error">Falhas de envio</p>
          <p class="text-xl font-semibold tabular-nums text-error">{{ detail.failed }}</p>
        </div>
      </div>
    </template>
  </UModal>

  <USlideover v-model:open="createOpen" title="Nova campanha" description="Dispara um template aprovado pra vários contatos" :ui="{ content: 'max-w-xl' }">
    <template #body>
      <div class="space-y-5">
        <UFormField label="Nome" required help="Só pra você identificar depois">
          <UInput v-model="name" placeholder="Promo de julho" class="w-full" />
        </UFormField>

        <TemplatePicker
          v-if="accountId"
          v-model:template="template"
          v-model:params="params"
          v-model:header-media-id="headerMediaId"
          :company-id="companyId ?? 0"
          :account-id="accountId"
        />

        <UFormField label="Destinatários" required>
          <URadioGroup v-model="mode" :items="modeItems" />
        </UFormField>

        <div v-if="mode === 'all'" class="flex items-center justify-between gap-3 rounded-lg ring-1 ring-default p-3">
          <div class="min-w-0">
            <p class="text-sm truncate">
              {{ audienceSummary }}
            </p>
            <p v-if="audienceCount !== null" class="text-xs text-muted">
              {{ audienceCount }} contato(s)
            </p>
          </div>
          <UButton label="Filtrar por etiquetas" icon="i-lucide-filter" variant="soft" size="sm" @click="() => { audienceOpen = true }" />
        </div>
        <CampaignAudienceModal
          v-if="accountId && companyId"
          v-model:open="audienceOpen"
          v-model:groups="tagGroups"
          v-model:exclude="excludeTagIds"
          :company-id="companyId"
          :account-id="accountId"
          :tags="tags"
          @applied="audienceCount = $event"
        />

        <UFormField v-if="mode !== 'all'" label="Nunca enviar pra quem tem" help="Vale pra qualquer modo, inclusive CSV (ex.: bloqueado)">
          <USelectMenu v-model="excludeTagIds" :items="tagItems" value-key="value" multiple placeholder="Nenhuma" class="w-full" />
        </UFormField>

        <div v-if="mode === 'select'" class="rounded-lg ring-1 ring-default max-h-56 overflow-y-auto divide-y divide-default">
          <p v-if="!contacts.length" class="p-3 text-sm text-muted">Nenhum contato nessa conta.</p>
          <label
            v-for="contact in contacts"
            :key="contact.id"
            class="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-elevated/60"
          >
            <UCheckbox
              :model-value="selectedContactIds.includes(contact.id)"
              @update:model-value="toggleContact(contact.id)"
            />
            <span class="text-sm truncate">{{ contact.name || contact.wa_id }}</span>
            <span class="text-xs text-muted font-mono ml-auto shrink-0">{{ contact.wa_id }}</span>
          </label>
        </div>

        <div v-if="mode === 'csv'" class="space-y-2">
          <input ref="csvInput" type="file" accept=".csv,.txt" class="hidden" @change="onCsvPicked">
          <UButton label="Escolher arquivo" icon="i-lucide-upload" variant="soft" @click="csvInput?.click()" />
          <p class="text-xs text-muted">
            CSV com uma linha por contato. Depois de escolher, você diz o que é cada coluna (telefone, nome, campos).
          </p>
          <CsvMappingModal
            v-if="companyId"
            v-model:open="mappingOpen"
            :text="csvText"
            :company-id="companyId"
            confirm-label="Usar {n} contato(s)"
            @confirm="onCsvMapped"
          />
          <UAlert
            v-if="csvNumbers.length"
            icon="i-lucide-check"
            color="success"
            variant="subtle"
            :title="`${csvNumbers.length} número(s) pronto(s) pra envio`"
            :description="csvColumnsText"
          />
          <UAlert
            v-if="csvInvalid.length"
            icon="i-lucide-triangle-alert"
            color="warning"
            variant="subtle"
            :title="`${csvInvalid.length} linha(s) ignorada(s) - número inválido`"
            :description="csvInvalid.slice(0, 3).join(' · ')"
          />
        </div>

        <UFormField
          label="Fluxo ao clicar"
          help="Opcional. Roda quando o contato clica num botão de resposta rápida do template (gatilho 'Clique em botão de campanha' em Automações)"
        >
          <USelect
            v-model="flowId"
            :items="flowItems"
            :placeholder="flowItems.length ? 'Nenhum' : 'Nenhum fluxo de campanha nessa conta'"
            :disabled="!flowItems.length"
            class="w-full"
          />
        </UFormField>
        <UButton
          v-if="flowId"
          label="Não usar fluxo"
          variant="link"
          size="xs"
          color="neutral"
          class="-mt-4"
          @click="flowId = undefined"
        />

        <UFormField label="Quando">
          <URadioGroup v-model="when" :items="whenItems" orientation="horizontal" />
        </UFormField>
        <UFormField v-if="when === 'later'" label="Data e hora do disparo" required help="Horário de Brasília">
          <UInput v-model="scheduledLocal" type="datetime-local" :min="minScheduleLocal" class="w-full" />
        </UFormField>

        <p v-if="campaignMissingReasons.length" class="text-xs text-warning">
          Falta: {{ campaignMissingReasons.join(', ') }}
        </p>
        <UButton
          block
          icon="i-lucide-send"
          :loading="saving"
          :disabled="!canSubmit"
          :label="when === 'later' ? 'Agendar campanha' : 'Disparar campanha'"
          @click="create()"
        />
      </div>
    </template>
  </USlideover>
</template>
