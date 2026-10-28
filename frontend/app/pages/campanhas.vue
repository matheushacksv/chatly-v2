<script setup lang="ts">
import type { Campaign, CampaignRecipient, Contact, CsvRecipient, Flow, Paginated, Tag, Template, WhatsAppAccount } from '~/types/api'

const api = useApi()
const toast = useToast()
const { activeId: companyId, isOwner } = useCompany()
const confirmModal = useConfirm()

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

type RecipientMode = 'all' | 'select' | 'csv' | 'reuse'
const mode = ref<RecipientMode>('all')
const modeItems = [
  { label: 'Todos os contatos', value: 'all' },
  { label: 'Selecionar contatos', value: 'select' },
  { label: 'Importar CSV', value: 'csv' },
  { label: 'Público de outra campanha', value: 'reuse' }
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

async function removeCampaign() {
  const campaign = detail.value
  if (!campaign) return
  const ok = await confirmModal({
    title: `Excluir "${campaign.name}"?`,
    description: 'Some da lista. As mensagens enviadas continuam nas conversas, mas clique em botão dela não roda mais o fluxo.',
    confirmLabel: 'Excluir'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}/campaigns/${campaign.id}`, { method: 'DELETE' })
    campaigns.value = campaigns.value.filter(c => c.id !== campaign.id)
    detailOpen.value = false
    toast.add({ title: 'Campanha excluída', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível excluir'), color: 'error' })
  }
}

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
  csvTags.value = []
  reuseCampaignId.value = undefined
  reuseSearch.value = ''
  pastedNumbers.value = ''
  createOpen.value = true
}

// "Reutilizar público" no detalhe: abre a nova campanha com o público, o template e o fluxo da escolhida
function reuseAudience(campaign: Campaign) {
  openCreate()
  accountId.value = campaign.account
  name.value = `${campaign.name} (nova)`
  // o picker reconhece por nome+idioma e troca pelo template completo
  template.value = { name: campaign.template_name, language: campaign.template_language } as Template
  params.value = [...campaign.body_params]
  flowId.value = campaign.flow ?? undefined
  mode.value = 'reuse'
  reuseCampaignId.value = campaign.id
  detailOpen.value = false
}

// público de outra campanha: vem todo marcado, desmarque quem não deve receber
// ponytail: lista inteira renderizada; paginar se público de milhares ficar lento
const reuseCampaignId = ref<number | undefined>(undefined)
const reuseContacts = ref<CampaignRecipient[]>([])
const reuseSelected = ref(new Set<number>())
const reuseSearch = ref('')
const reuseLoading = ref(false)
const reuseCampaignItems = computed(() =>
  campaigns.value.filter(c => c.account === accountId.value).map(c => ({ label: `${c.name} (${c.total} contato(s))`, value: c.id }))
)
watch(reuseCampaignId, async (id) => {
  reuseContacts.value = []
  reuseSelected.value = new Set()
  if (!id) return
  reuseLoading.value = true
  try {
    reuseContacts.value = await api<CampaignRecipient[]>(`/companies/${companyId.value}/campaigns/${id}/recipients`)
    reuseSelected.value = new Set(reuseContacts.value.map(c => c.id))
  } finally {
    reuseLoading.value = false
  }
})
const reuseVisible = computed(() => {
  const q = reuseSearch.value.trim().toLowerCase()
  return q ? reuseContacts.value.filter(c => `${c.name} ${c.wa_id}`.toLowerCase().includes(q)) : reuseContacts.value
})
// etiqueta em "Nunca enviar": o backend tira no envio, aqui só mostra
const excludedTag = (contact: CampaignRecipient) => contact.tags.find(t => excludeTagIds.value.includes(t.id))?.name
const reuseFinalCount = computed(() => reuseContacts.value.filter(c => reuseSelected.value.has(c.id) && !excludedTag(c)).length)

function toggleReuse(id: number) {
  if (reuseSelected.value.has(id)) reuseSelected.value.delete(id)
  else reuseSelected.value.add(id)
}

function setReuseVisible(checked: boolean) {
  for (const c of reuseVisible.value) {
    if (checked) reuseSelected.value.add(c.id)
    else reuseSelected.value.delete(c.id)
  }
}

// colar números (um por linha, vírgula, com ou sem DDI/máscara) e desmarcar os que estão na lista
const pastedNumbers = ref('')
// chave de comparação: DDI 55 + DDD + 8 últimos dígitos, pra casar com e sem o 9 do celular
function numberKey(raw: string): string {
  let digits = raw.replace(/\D/g, '')
  if (digits.length === 10 || digits.length === 11) digits = `55${digits}`
  return digits.startsWith('55') && digits.length >= 12 ? `55${digits.slice(2, 4)}${digits.slice(-8)}` : digits
}

function unselectPasted() {
  const keys = new Set(pastedNumbers.value.split(/[\n,;]+/).map(numberKey).filter(k => k.length >= 8))
  if (!keys.size) return
  const hits = reuseContacts.value.filter(c => keys.has(numberKey(c.wa_id)))
  for (const c of hits) reuseSelected.value.delete(c.id)
  const missing = keys.size - new Set(hits.map(c => numberKey(c.wa_id))).size
  toast.add({
    title: `${hits.length} desmarcado(s)`,
    description: missing ? `${missing} número(s) não estavam nesse público` : undefined,
    icon: 'i-lucide-user-minus'
  })
  pastedNumbers.value = ''
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
  const tagNames = csvTags.value.map(t => t.name)
  return [labels.length ? `Campos: ${labels.join(', ')}` : '', tagNames.length ? `Etiquetas: ${tagNames.join(', ')}` : '']
    .filter(Boolean).join(' · ') || undefined
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

const csvTags = ref<Tag[]>([])
function onCsvMapped({ rows, invalid, tags: chosen }: { rows: CsvRecipient[], invalid: string[], tags: Tag[] }) {
  csvNumbers.value = rows
  csvInvalid.value = invalid
  csvTags.value = chosen
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
  if (mode.value === 'reuse' && !reuseFinalCount.value) reasons.push('escolha uma campanha e mantenha ao menos um contato')
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
    if (mode.value === 'reuse') body.contact_ids = [...reuseSelected.value]
    if (mode.value === 'csv') {
      body.contact_ids = []
      body.numbers = csvNumbers.value
      body.import_tag_ids = csvTags.value.map(t => t.id)
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
    <template v-if="detail" #footer>
      <div class="flex w-full items-center justify-between gap-2">
        <UButton label="Reutilizar público" icon="i-lucide-users" variant="soft" size="sm" @click="reuseAudience(detail)" />
        <UButton
          v-if="isOwner && detail.status !== 'sending'"
          label="Excluir campanha"
          icon="i-lucide-trash-2"
          color="error"
          variant="ghost"
          size="sm"
          @click="removeCampaign"
        />
      </div>
    </template>
  </UModal>

  <USlideover v-model:open="createOpen" title="Nova campanha" description="Dispara um template aprovado pra vários contatos" :ui="{ content: 'max-w-xl' }">
    <template #body>
      <div class="space-y-5">
        <AppField label="Nome" required tip="Só pra você identificar depois">
          <UInput v-model="name" placeholder="Promo de julho" class="w-full" />
        </AppField>

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

        <AppField v-if="mode !== 'all'" label="Nunca enviar pra quem tem" tip="Vale pra qualquer modo, inclusive CSV (ex.: bloqueado)">
          <USelectMenu v-model="excludeTagIds" :items="tagItems" value-key="value" multiple placeholder="Nenhuma" class="w-full" />
        </AppField>

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

        <div v-if="mode === 'reuse'" class="space-y-2">
          <USelectMenu
            v-model="reuseCampaignId"
            :items="reuseCampaignItems"
            value-key="value"
            placeholder="Escolha a campanha"
            icon="i-lucide-megaphone"
            class="w-full"
          />
          <template v-if="reuseCampaignId">
            <div class="flex items-center gap-2">
              <UInput v-model="reuseSearch" icon="i-lucide-search" placeholder="Buscar nome ou número" size="sm" class="flex-1" />
              <UButton label="Marcar" variant="ghost" size="xs" @click="setReuseVisible(true)" />
              <UButton label="Desmarcar" variant="ghost" color="neutral" size="xs" @click="setReuseVisible(false)" />
            </div>
            <div class="rounded-lg ring-1 ring-default max-h-56 overflow-y-auto divide-y divide-default">
              <p v-if="reuseLoading" class="p-3 text-sm text-muted">Carregando...</p>
              <p v-else-if="!reuseVisible.length" class="p-3 text-sm text-muted">Nenhum contato.</p>
              <label
                v-for="contact in reuseVisible"
                :key="contact.id"
                class="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-elevated/60"
                :class="{ 'opacity-50': excludedTag(contact) }"
              >
                <UCheckbox :model-value="reuseSelected.has(contact.id)" @update:model-value="toggleReuse(contact.id)" />
                <span class="text-sm truncate">{{ contact.name || contact.wa_id }}</span>
                <span v-if="excludedTag(contact)" class="text-xs text-muted shrink-0">sai por "{{ excludedTag(contact) }}"</span>
                <span class="text-xs text-muted font-mono ml-auto shrink-0">{{ contact.wa_id }}</span>
              </label>
            </div>
            <div class="flex items-start gap-2">
              <UTextarea
                v-model="pastedNumbers"
                :rows="2"
                autoresize
                :maxrows="6"
                placeholder="Cole números pra tirar (um por linha ou separados por vírgula)"
                class="flex-1"
              />
              <UButton
                label="Desmarcar estes"
                icon="i-lucide-user-minus"
                variant="soft"
                size="sm"
                :disabled="!pastedNumbers.trim()"
                @click="unselectPasted"
              />
            </div>
            <p class="text-xs text-muted">
              {{ reuseFinalCount }} de {{ reuseContacts.length }} vão receber
            </p>
          </template>
        </div>

        <div v-if="mode === 'csv'" class="space-y-2">
          <input ref="csvInput" type="file" accept=".csv,.txt" class="hidden" @change="onCsvPicked">
          <UTooltip text="CSV com uma linha por contato. Depois de escolher, você diz o que é cada coluna (telefone, nome, campos)">
            <UButton label="Escolher arquivo" icon="i-lucide-upload" variant="soft" @click="csvInput?.click()" />
          </UTooltip>
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

        <!-- depois dos destinatários: campo criado ao mapear o CSV já aparece no menu de variáveis -->
        <TemplatePicker
          v-if="accountId"
          v-model:template="template"
          v-model:params="params"
          v-model:header-media-id="headerMediaId"
          :company-id="companyId ?? 0"
          :account-id="accountId"
        />

        <AppField
          label="Fluxo ao clicar"
          tip="Opcional. Roda quando o contato clica num botão de resposta rápida do template (gatilho 'Clique em botão de campanha' em Automações)"
        >
          <USelect
            v-model="flowId"
            :items="flowItems"
            :placeholder="flowItems.length ? 'Nenhum' : 'Nenhum fluxo de campanha nessa conta'"
            :disabled="!flowItems.length"
            class="w-full"
          />
        </AppField>
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
        <AppField v-if="when === 'later'" label="Data e hora do disparo" required tip="Horário de Brasília">
          <UInput v-model="scheduledLocal" type="datetime-local" :min="minScheduleLocal" class="w-full" />
        </AppField>

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
