<script setup lang="ts">
import type { TabsItem } from '@nuxt/ui'
import type { Agent, Credential, Invite, Member, PipedriveConfig, PipedrivePipeline, Tag, TagColor, Template, WhatsAppAccount } from '~/types/api'

// ?tab= abre direto numa aba (ex.: link "Gerenciar respostas rápidas" do chat)
const tab = ref(String(useRoute().query.tab || 'credenciais'))
const settingsTabs: TabsItem[] = [
  { label: 'Credenciais de IA', icon: 'i-lucide-key-round', value: 'credenciais', slot: 'credenciais' },
  { label: 'Pipedrive', icon: 'i-lucide-handshake', value: 'pipedrive', slot: 'pipedrive' },
  { label: 'WhatsApp', icon: 'i-lucide-phone', value: 'whatsapp', slot: 'whatsapp' },
  { label: 'Contatos', icon: 'i-lucide-tag', value: 'etiquetas', slot: 'etiquetas' },
  { label: 'Respostas rápidas', icon: 'i-lucide-zap', value: 'respostas', slot: 'respostas' },
  { label: 'Membros', icon: 'i-lucide-users', value: 'membros', slot: 'membros' },
  { label: 'Empresa', icon: 'i-lucide-building-2', value: 'empresa', slot: 'empresa' }
]

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()
const promptModal = usePrompt()
const { activeId: companyId, active, isOwner, load: loadCompanies, setActive } = useCompany()

// ---- dados ----
const credentials = ref<Credential[]>([])
const accounts = ref<WhatsAppAccount[]>([])
const agents = ref<Agent[]>([])

async function loadAll() {
  if (!companyId.value) return
  ;[credentials.value, accounts.value, agents.value] = await Promise.all([
    api<Credential[]>(`/companies/${companyId.value}/credentials`),
    api<WhatsAppAccount[]>(`/companies/${companyId.value}/whatsapp-accounts`),
    api<Agent[]>(`/companies/${companyId.value}/agents`)
  ])
}
watch(companyId, loadAll, { immediate: true })

const agentName = (id: number) => agents.value.find(a => a.id === id)?.name ?? `#${id}`
const agentItems = computed(() => agents.value.map(a => ({ label: a.name, value: a.id })))

// ---- credenciais ----
const providerItems = [
  { label: 'Anthropic', value: 'anthropic' },
  { label: 'OpenAI', value: 'openai' }
]
const newCredential = reactive({ provider: 'anthropic' as 'anthropic' | 'openai', api_key: '' })
const savingCredential = ref(false)

async function createCredential() {
  if (!newCredential.api_key.trim()) return
  savingCredential.value = true
  try {
    const created = await api<Credential>(`/companies/${companyId.value}/credentials`, {
      method: 'POST', body: { ...newCredential }
    })
    credentials.value.push(created)
    newCredential.api_key = ''
    toast.add({ title: 'Credencial salva', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao salvar credencial'), color: 'error' })
  } finally {
    savingCredential.value = false
  }
}

async function rotateCredential(credential: Credential) {
  const key = await promptModal({
    title: `Trocar chave ${credential.provider === 'anthropic' ? 'Anthropic' : 'OpenAI'}`,
    description: 'A chave atual será substituída na hora. Ela nunca é exibida depois de salva.',
    placeholder: 'sk-...',
    confirmLabel: 'Trocar chave',
    inputType: 'password'
  })
  if (!key) return
  try {
    await api(`/companies/${companyId.value}/credentials/${credential.id}`, {
      method: 'PATCH', body: { api_key: key }
    })
    toast.add({ title: 'Chave atualizada', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

async function removeCredential(credential: Credential) {
  const ok = await confirmModal({
    title: `Excluir credencial ${credential.provider} #${credential.id}?`,
    description: 'Agentes que usam essa chave param de responder.'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}/credentials/${credential.id}`, { method: 'DELETE' })
    credentials.value = credentials.value.filter(c => c.id !== credential.id)
    toast.add({ title: 'Credencial excluída', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// ---- contas WhatsApp ----
const accountOpen = ref(false)
const editingAccount = ref<WhatsAppAccount | null>(null)
const savingAccount = ref(false)
const accountState = reactive({
  agent_id: null as number | null,
  phone_number_id: '',
  waba_id: '',
  access_token: '',
  app_secret: '',
  verify_token: '',
  app_id: ''
})

function openAccountCreate() {
  editingAccount.value = null
  Object.assign(accountState, { agent_id: null, phone_number_id: '', waba_id: '', access_token: '', app_secret: '', verify_token: '', app_id: '' })
  accountOpen.value = true
}

function openAccountEdit(account: WhatsAppAccount) {
  editingAccount.value = account
  Object.assign(accountState, {
    agent_id: account.agent,
    phone_number_id: account.phone_number_id,
    waba_id: account.waba_id,
    access_token: '', // write-only: vazio = manter
    app_secret: '',
    verify_token: account.verify_token,
    app_id: account.app_id
  })
  accountOpen.value = true
}

async function saveAccount() {
  savingAccount.value = true
  try {
    if (editingAccount.value) {
      const body: Record<string, unknown> = {
        agent_id: accountState.agent_id,
        waba_id: accountState.waba_id,
        verify_token: accountState.verify_token,
        app_id: accountState.app_id
      }
      if (accountState.access_token.trim()) body.access_token = accountState.access_token.trim()
      if (accountState.app_secret.trim()) body.app_secret = accountState.app_secret.trim()
      const updated = await api<WhatsAppAccount>(
        `/companies/${companyId.value}/whatsapp-accounts/${editingAccount.value.id}`,
        { method: 'PATCH', body }
      )
      accounts.value = accounts.value.map(a => (a.id === updated.id ? updated : a))
      toast.add({ title: 'Conta atualizada', color: 'success' })
    } else {
      const created = await api<WhatsAppAccount>(`/companies/${companyId.value}/whatsapp-accounts`, {
        method: 'POST', body: { ...accountState }
      })
      accounts.value.push(created)
      toast.add({ title: 'Conta conectada', color: 'success' })
    }
    accountOpen.value = false
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao salvar conta'), color: 'error' })
  } finally {
    savingAccount.value = false
  }
}

async function removeAccount(account: WhatsAppAccount) {
  const ok = await confirmModal({
    title: `Desconectar o número ${account.phone_number_id}?`,
    description: 'Conversas e contatos dele serão apagados.',
    confirmLabel: 'Desconectar'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}/whatsapp-accounts/${account.id}`, { method: 'DELETE' })
    accounts.value = accounts.value.filter(a => a.id !== account.id)
    toast.add({ title: 'Conta desconectada', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// ---- etiquetas ----
const tags = ref<Tag[]>([])
const newTag = reactive({ name: '', color: 'neutral' as TagColor })
watch(companyId, async (id) => {
  tags.value = id ? await api<Tag[]>(`/companies/${id}/tags`) : []
}, { immediate: true })

async function createTag() {
  if (!newTag.name.trim()) return
  try {
    const tag = await api<Tag>(`/companies/${companyId.value}/tags`, { method: 'POST', body: { ...newTag } })
    if (!tags.value.some(t => t.id === tag.id)) tags.value = [...tags.value, tag].sort((a, b) => a.name.localeCompare(b.name))
    newTag.name = ''
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível criar a etiqueta'), color: 'error' })
  }
}

async function removeTag(tag: Tag) {
  const ok = await confirmModal({
    title: `Excluir a etiqueta "${tag.name}"?`,
    description: 'Ela sai de todos os contatos. Fluxos que usam essa etiqueta seguem sem ela.',
    confirmLabel: 'Excluir'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}/tags/${tag.id}`, { method: 'DELETE' })
    tags.value = tags.value.filter(t => t.id !== tag.id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

const registeringId = ref<number | null>(null)

async function registerAccount(account: WhatsAppAccount) {
  const pin = await promptModal({
    title: `Registrar ${account.phone_number_id} no Cloud API`,
    description: 'PIN de verificação em duas etapas do número. Se nunca definiu um, este vira o PIN.',
    placeholder: '6 dígitos',
    confirmLabel: 'Registrar'
  })
  if (!pin) return
  if (!/^\d{6}$/.test(pin)) {
    toast.add({ title: 'O PIN precisa ter 6 dígitos', color: 'error' })
    return
  }
  registeringId.value = account.id
  try {
    const res = await api<{ detail: string }>(
      `/companies/${companyId.value}/whatsapp-accounts/${account.id}/register`,
      { method: 'POST', body: { pin } }
    )
    toast.add({ title: res.detail, icon: 'i-lucide-badge-check', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao registrar número'), color: 'error' })
  } finally {
    registeringId.value = null
  }
}

const accountFormValid = computed(() => {
  if (editingAccount.value) return accountState.agent_id !== null && accountState.waba_id.trim() !== '' && accountState.verify_token.trim() !== ''
  return accountState.agent_id !== null
    && accountState.phone_number_id.trim() !== ''
    && accountState.waba_id.trim() !== ''
    && accountState.access_token.trim() !== ''
    && accountState.app_secret.trim() !== ''
    && accountState.verify_token.trim() !== ''
})

// ---- membros + convites (owner only - GET /invites é 404 pra member) ----
const members = ref<Member[]>([])
const invites = ref<Invite[]>([])
const generatingInvite = ref(false)

async function loadMembers() {
  if (!companyId.value || !isOwner.value) return
  ;[members.value, invites.value] = await Promise.all([
    api<Member[]>(`/companies/${companyId.value}/members`),
    api<Invite[]>(`/companies/${companyId.value}/invites`)
  ])
}
watch([companyId, isOwner], loadMembers, { immediate: true })

// URL que vai no painel da Meta (WhatsApp → Configuração → Webhook). apiBase é relativo em prod ('/api')
const webhookUrl = new URL(`${useRuntimeConfig().public.apiBase}/whatsapp/webhook`, location.origin).href

async function copyWebhookUrl() {
  try {
    await navigator.clipboard.writeText(webhookUrl)
    toast.add({ title: 'URL de callback copiada', icon: 'i-lucide-clipboard-check' })
  } catch {
    toast.add({ title: 'Não foi possível copiar', description: 'Selecione a URL e copie manualmente.', color: 'error' })
  }
}

async function copyInvite(token: string) {
  try {
    await navigator.clipboard.writeText(`${location.origin}/convite/${token}`)
    toast.add({ title: 'Link de convite copiado', icon: 'i-lucide-clipboard-check' })
  } catch {
    // navegador sem permissão de área de transferência: o convite existe, só não foi copiado
    toast.add({ title: 'Convite criado', description: 'Copie o link pela lista de convites.', icon: 'i-lucide-link' })
  }
}

async function generateInvite() {
  generatingInvite.value = true
  try {
    const invite = await api<Invite>(`/companies/${companyId.value}/invites`, { method: 'POST' })
    invites.value.unshift(invite)
    await copyInvite(invite.token)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao gerar convite'), color: 'error' })
  } finally {
    generatingInvite.value = false
  }
}

async function revokeInvite(invite: Invite) {
  try {
    await api(`/companies/${companyId.value}/invites/${invite.id}`, { method: 'DELETE' })
    invites.value = invites.value.filter(i => i.id !== invite.id)
    toast.add({ title: 'Convite revogado', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

async function removeMember(member: Member) {
  const ok = await confirmModal({
    title: `Remover ${member.name || member.email}?`,
    description: 'A pessoa perde o acesso à empresa na hora.',
    confirmLabel: 'Remover'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}/members/${member.id}`, { method: 'DELETE' })
    members.value = members.value.filter(m => m.id !== member.id)
    toast.add({ title: 'Membro removido', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

const inviteDateFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' })
const inviteDate = (iso: string) => inviteDateFmt.format(new Date(iso))

// ---- empresa ----
const companyName = ref('')
watch(active, c => { companyName.value = c?.name ?? '' }, { immediate: true })
const savingCompany = ref(false)

async function renameCompany() {
  if (!companyName.value.trim() || !companyId.value) return
  savingCompany.value = true
  try {
    await api(`/companies/${companyId.value}`, { method: 'PATCH', body: { name: companyName.value.trim() } })
    await loadCompanies()
    toast.add({ title: 'Empresa renomeada', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  } finally {
    savingCompany.value = false
  }
}

async function removeCompany() {
  if (!active.value) return
  const ok = await confirmModal({
    title: `Excluir a empresa "${active.value.name}"?`,
    description: 'TODOS os agentes, contas e conversas serão apagados. Isso não tem volta.',
    confirmLabel: 'Excluir tudo'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}`, { method: 'DELETE' })
    setActive(null)
    await loadCompanies()
    toast.add({ title: 'Empresa excluída', icon: 'i-lucide-trash-2' })
    await navigateTo('/')
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// ---- Pipedrive ----
const pipedrive = ref<PipedriveConfig | null>(null)
const pipelines = ref<PipedrivePipeline[]>([])
const pipedriveKey = ref('')
const connectingPipedrive = ref(false)
const pipelineId = ref<number | undefined>()
const stageId = ref<number | undefined>()
const leadAccountId = ref<number | undefined>()
const savingDefaults = ref(false)

// selector só faz sentido com mais de um número; com um só o backend já auto-vincula
const accountItems = computed(() => accounts.value.map(a => ({ label: a.phone_number_id, value: a.id })))
const pipelineItems = computed(() => pipelines.value.map(p => ({ label: p.name, value: p.id })))
const stageItems = computed(() =>
  pipelines.value.find(p => p.id === pipelineId.value)?.stages.map(s => ({ label: s.name, value: s.id })) ?? []
)

async function loadPipedrive() {
  if (!companyId.value || !isOwner.value) return
  try {
    pipedrive.value = await api<PipedriveConfig>(`/companies/${companyId.value}/pipedrive`)
  } catch {
    pipedrive.value = null // 404 = não conectado
    return
  }
  pipelineId.value = pipedrive.value.default_pipeline_id ?? undefined
  stageId.value = pipedrive.value.default_stage_id ?? undefined
  leadAccountId.value = pipedrive.value.lead_account_id ?? undefined
  try {
    pipelines.value = await api<PipedrivePipeline[]>(`/companies/${companyId.value}/pipedrive/pipelines`)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao carregar funis'), color: 'error' })
  }
}
watch([companyId, isOwner], loadPipedrive, { immediate: true })

async function connectPipedrive() {
  if (!pipedriveKey.value.trim()) return
  connectingPipedrive.value = true
  try {
    await api(`/companies/${companyId.value}/pipedrive`, { method: 'POST', body: { api_key: pipedriveKey.value.trim() } })
    pipedriveKey.value = ''
    toast.add({ title: 'Pipedrive conectado', color: 'success' })
    await loadPipedrive()
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao conectar Pipedrive'), color: 'error' })
  } finally {
    connectingPipedrive.value = false
  }
}

async function savePipedriveDefaults() {
  savingDefaults.value = true
  try {
    pipedrive.value = await api<PipedriveConfig>(`/companies/${companyId.value}/pipedrive`, {
      method: 'PATCH', body: { pipeline_id: pipelineId.value ?? null, stage_id: stageId.value ?? null, lead_account_id: leadAccountId.value ?? null }
    })
    toast.add({ title: 'Funil padrão salvo', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  } finally {
    savingDefaults.value = false
  }
}

// ---- IA atende negócio novo: template de abertura quando entra numa das etapas ----
const autoStageIds = ref<number[]>([])
const autoOnlyInHours = ref(true)
const autoTemplate = ref<PipedriveConfig['auto_template']>({})
const autoPicked = ref<Template | null>(null)
const autoParams = ref<string[]>([])
const autoHeaderMediaId = ref('')
const savingAuto = ref(false)
const allStageItems = computed(() => pipelines.value.flatMap(p => p.stages.map(s => ({ label: `${p.name} > ${s.name}`, value: s.id }))))
watch(pipedrive, (config) => {
  autoStageIds.value = [...(config?.auto_stage_ids ?? [])]
  autoOnlyInHours.value = config?.auto_only_in_hours ?? true
  autoTemplate.value = { ...(config?.auto_template ?? {}) }
  autoPicked.value = null
})
watch([autoPicked, autoParams, autoHeaderMediaId], () => {
  if (!autoPicked.value) return
  autoTemplate.value = {
    template_name: autoPicked.value.name,
    language: autoPicked.value.language,
    body_params: [...autoParams.value],
    header_media_id: autoHeaderMediaId.value
  }
}, { deep: true })

async function saveAutoOpen() {
  savingAuto.value = true
  try {
    pipedrive.value = await api<PipedriveConfig>(`/companies/${companyId.value}/pipedrive/auto-open`, {
      method: 'PUT', body: { stage_ids: autoStageIds.value, template: autoTemplate.value, only_in_hours: autoOnlyInHours.value }
    })
    toast.add({ title: autoStageIds.value.length ? 'IA vai atender os negócios novos' : 'Atendimento automático desligado', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  } finally {
    savingAuto.value = false
  }
}

async function disconnectPipedrive() {
  const ok = await confirmModal({
    title: 'Desconectar o Pipedrive?',
    description: 'A IA para de criar negócios e o painel do CRM some das conversas.',
    confirmLabel: 'Desconectar'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}/pipedrive`, { method: 'DELETE' })
    pipedrive.value = null
    pipelines.value = []
    pipelineId.value = undefined
    stageId.value = undefined
    leadAccountId.value = undefined
    toast.add({ title: 'Pipedrive desconectado', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}
</script>

<template>
  <UDashboardPanel id="configuracoes">
    <template #header>
      <UDashboardNavbar title="Configurações" />
    </template>

    <template #body>
      <div class="max-w-5xl space-y-8">
        <!-- member acessando por URL direta: backend devolve 404 nas rotas de admin -->
        <UAlert
          v-if="!isOwner"
          icon="i-lucide-lock"
          title="Somente o dono gerencia estas configurações"
          description="Peça ao dono da empresa para alterar credenciais, contas WhatsApp e membros."
          variant="subtle"
          color="neutral"
        />

        <!-- aba não encolhe (sem "Pipe..."); a rolagem só aparece em tela estreita (celular) -->
        <UTabs
          v-else
          v-model="tab"
          :items="settingsTabs"
          variant="link"
          class="w-full"
          :ui="{ list: 'overflow-x-auto overflow-y-hidden', trigger: 'shrink-0', label: 'overflow-visible text-clip' }"
        >
          <template #credenciais>
          <!-- Credenciais -->
          <section class="space-y-3 pt-4">
            <div>
              <h2 class="font-display font-semibold text-lg">Credenciais de IA</h2>
              <p class="text-sm text-muted">Chaves de API usadas pelos seus agentes. A chave nunca é exibida depois de salva.</p>
            </div>

            <div v-if="credentials.length" class="space-y-2">
              <div
                v-for="credential in credentials"
                :key="credential.id"
                class="flex items-center gap-3 rounded-lg ring-1 ring-default px-4 py-3"
              >
                <UIcon name="i-lucide-key-round" class="size-4 text-muted" />
                <span class="font-medium capitalize flex-1">{{ credential.provider }} <span class="text-muted font-normal">#{{ credential.id }}</span></span>
                <UButton label="Trocar chave" variant="ghost" color="neutral" size="xs" @click="rotateCredential(credential)" />
                <UButton icon="i-lucide-trash-2" variant="ghost" color="error" size="xs" @click="removeCredential(credential)" />
              </div>
            </div>

            <form class="flex gap-2" @submit.prevent="createCredential">
              <USelect v-model="newCredential.provider" :items="providerItems" class="w-36" />
              <UInput
                v-model="newCredential.api_key"
                type="password"
                placeholder="sk-..."
                class="flex-1 font-mono"
                autocomplete="off"
              />
              <UButton type="submit" label="Adicionar" :loading="savingCredential" :disabled="!newCredential.api_key.trim()" />
            </form>
          </section>
          </template>

          <template #pipedrive>
          <!-- Pipedrive -->
          <section class="space-y-3 pt-4">
            <div class="flex items-start justify-between gap-4">
              <div>
                <h2 class="font-display font-semibold text-lg">Pipedrive</h2>
                <p class="text-sm text-muted">CRM integrado: a IA e os atendentes criam negócios e anotações. A chave nunca é exibida depois de salva.</p>
              </div>
              <div v-if="pipedrive" class="flex items-center gap-2">
                <UBadge color="success" variant="subtle">Conectado</UBadge>
                <UBadge v-if="pipedrive.receiving_events" color="primary" variant="subtle" icon="i-lucide-webhook">Recebendo leads</UBadge>
              </div>
            </div>

            <form v-if="!pipedrive" class="flex gap-2" @submit.prevent="connectPipedrive">
              <UInput
                v-model="pipedriveKey"
                type="password"
                placeholder="Api token do Pipedrive"
                class="flex-1 font-mono"
                autocomplete="off"
              />
              <UButton type="submit" label="Conectar" :loading="connectingPipedrive" :disabled="!pipedriveKey.trim()" />
            </form>

            <template v-else>
              <div class="flex gap-2 items-start">
                <UFormField label="Funil padrão" class="flex-1">
                  <USelect v-model="pipelineId" :items="pipelineItems" placeholder="Escolha" class="w-full" @update:model-value="stageId = undefined" />
                </UFormField>
                <UFormField label="Etapa inicial" class="flex-1">
                  <USelect v-model="stageId" :items="stageItems" placeholder="Escolha" class="w-full" :disabled="!pipelineId" />
                </UFormField>
              </div>
              <UFormField
                v-if="accounts.length > 1"
                label="Número que recebe leads do Pipedrive"
                help="Negócios novos ou movidos no Pipedrive viram/atualizam contatos neste número."
              >
                <USelect v-model="leadAccountId" :items="accountItems" placeholder="Escolha o número" class="w-full" />
              </UFormField>
              <div class="flex items-center justify-between gap-4">
                <p class="text-xs text-muted">Usados quando a IA cria um negócio.</p>
                <div class="flex items-center gap-2">
                  <UButton label="Desconectar" color="error" variant="ghost" size="xs" @click="disconnectPipedrive()" />
                  <UButton label="Salvar" :loading="savingDefaults" :disabled="!pipelineId || !stageId" @click="savePipedriveDefaults()" />
                </div>
              </div>

              <div class="rounded-lg ring-1 ring-default p-4 space-y-3">
                <div>
                  <h3 class="font-semibold">
                    IA atende negócios novos
                  </h3>
                  <p class="text-sm text-muted">
                    Quando um negócio é criado ou movido para uma dessas etapas, o lead recebe o template de abertura e a IA cuida das respostas. Uma vez por negócio.
                  </p>
                </div>
                <UFormField label="Etapas" help="Vazio = desligado">
                  <USelectMenu
                    v-model="autoStageIds"
                    :items="allStageItems"
                    value-key="value"
                    multiple
                    placeholder="Nenhuma"
                    icon="i-lucide-kanban"
                    class="w-full"
                  />
                </UFormField>
                <template v-if="autoStageIds.length && pipedrive.lead_account_id">
                  <UFormField label="Template de abertura" help="A Meta só deixa iniciar conversa com template aprovado">
                    <p v-if="autoTemplate.template_name" class="text-xs text-muted mb-2">
                      Atual: <span class="font-mono">{{ autoTemplate.template_name }} ({{ autoTemplate.language }})</span>
                    </p>
                    <TemplatePicker
                      v-model:template="autoPicked"
                      v-model:params="autoParams"
                      v-model:header-media-id="autoHeaderMediaId"
                      :company-id="companyId!"
                      :account-id="pipedrive.lead_account_id"
                    />
                  </UFormField>
                  <USwitch
                    v-model="autoOnlyInHours"
                    label="Só no horário da IA"
                    description="Negócio que entra fora do horário do agente fica de fora: não recebe nada, nem depois"
                  />
                  <p class="text-xs text-muted">
                    Tem fluxo de automação com gatilho do Pipedrive na mesma etapa? O lead recebe as duas mensagens.
                  </p>
                </template>
                <UAlert
                  v-else-if="autoStageIds.length"
                  color="warning"
                  variant="subtle"
                  icon="i-lucide-triangle-alert"
                  title="Escolha o número que recebe leads do Pipedrive e salve antes"
                />
                <div class="flex justify-end">
                  <UButton
                    label="Salvar"
                    :loading="savingAuto"
                    :disabled="autoStageIds.length > 0 && !autoTemplate.template_name"
                    @click="saveAutoOpen()"
                  />
                </div>
              </div>
            </template>
          </section>
          </template>

          <template #whatsapp>
          <!-- Contas WhatsApp -->
          <section class="space-y-3 pt-4">
            <div class="flex items-start justify-between gap-4">
              <div>
                <h2 class="font-display font-semibold text-lg">Contas WhatsApp</h2>
                <p class="text-sm text-muted">Números conectados via Meta Cloud API. Tokens nunca são exibidos depois de salvos.</p>
              </div>
              <UButton label="Conectar número" icon="i-lucide-plus" variant="soft" :disabled="!agents.length" @click="openAccountCreate()" />
            </div>

            <UAlert
              v-if="!agents.length"
              icon="i-lucide-bot"
              title="Crie um agente primeiro"
              description="Toda conta WhatsApp precisa de um agente respondendo por ela."
              variant="subtle"
              color="warning"
              :actions="[{ label: 'Ir para Agentes', to: '/agentes', color: 'warning', variant: 'soft' }]"
            />

            <div v-if="accounts.length" class="space-y-2">
              <div
                v-for="account in accounts"
                :key="account.id"
                class="flex items-center gap-3 rounded-lg ring-1 ring-default px-4 py-3"
              >
                <UIcon name="i-lucide-phone" class="size-4 text-muted" />
                <div class="flex-1 min-w-0">
                  <p class="font-mono text-sm truncate">{{ account.phone_number_id }}</p>
                  <p class="text-xs text-muted truncate">Agente: {{ agentName(account.agent) }}</p>
                </div>
                <UButton icon="i-lucide-badge-check" variant="ghost" color="neutral" size="xs" title="Registrar no Cloud API" :loading="registeringId === account.id" @click="registerAccount(account)" />
                <UButton icon="i-lucide-pencil" variant="ghost" color="neutral" size="xs" @click="openAccountEdit(account)" />
                <UButton icon="i-lucide-trash-2" variant="ghost" color="error" size="xs" @click="removeAccount(account)" />
              </div>
            </div>
            <p v-else-if="agents.length" class="text-sm text-muted">Nenhum número conectado ainda.</p>
          </section>
          </template>

          <template #etiquetas>
          <section class="space-y-3 pt-4">
            <div>
              <h2 class="font-display font-semibold text-lg">Etiquetas</h2>
              <p class="text-sm text-muted">Marcam contatos no painel e nos fluxos. Use em campanhas pra incluir ou excluir (ex.: bloqueado).</p>
            </div>
            <form class="flex gap-2" @submit.prevent="createTag">
              <UInput v-model="newTag.name" placeholder="Nova etiqueta" maxlength="40" class="flex-1" />
              <USelect v-model="newTag.color" :items="TAG_COLOR_ITEMS" class="w-32" />
              <UButton type="submit" label="Criar" icon="i-lucide-plus" variant="soft" :disabled="!newTag.name.trim()" />
            </form>
            <div v-if="tags.length" class="flex flex-wrap gap-2">
              <UBadge v-for="tag in tags" :key="tag.id" :color="tag.color" variant="subtle" class="gap-1">
                {{ tag.name }}
                <UButton
                  v-if="isOwner"
                  icon="i-lucide-x"
                  variant="link"
                  :color="tag.color"
                  size="xs"
                  class="p-0"
                  :aria-label="`Excluir etiqueta ${tag.name}`"
                  @click="removeTag(tag)"
                />
              </UBadge>
            </div>
            <p v-else class="text-sm text-muted">Nenhuma etiqueta ainda.</p>
          </section>

          <section class="space-y-3 pt-6">
            <h2 class="font-display font-semibold text-lg">
              Campos personalizados
            </h2>
            <ContactFieldsManager v-if="companyId" :company-id="companyId" />
          </section>
          </template>

          <template #respostas>
          <section class="space-y-3 pt-4">
            <h2 class="font-display font-semibold text-lg">
              Respostas rápidas
            </h2>
            <QuickRepliesManager v-if="companyId" :company-id="companyId" />
          </section>
          </template>

          <template #membros>
          <!-- Membros -->
          <section class="space-y-3 pt-4">
            <div class="flex items-start justify-between gap-4">
              <div>
                <h2 class="font-display font-semibold text-lg">Membros</h2>
                <p class="text-sm text-muted">Quem tem acesso a esta empresa. Membros operam conversas, contatos, campanhas e agentes - só o dono mexe aqui.</p>
              </div>
              <UButton
                label="Gerar link de convite"
                icon="i-lucide-link"
                variant="soft"
                :loading="generatingInvite"
                @click="generateInvite()"
              />
            </div>

            <div class="space-y-2">
              <div
                v-for="member in members"
                :key="member.id"
                class="flex items-center gap-3 rounded-lg ring-1 ring-default px-4 py-3"
              >
                <UAvatar :alt="member.name || member.email" size="sm" />
                <div class="flex-1 min-w-0">
                  <p class="font-medium text-sm truncate">{{ member.name || 'Sem nome' }}</p>
                  <p class="text-xs text-muted truncate">{{ member.email }}</p>
                </div>
                <UBadge :color="member.role === 'owner' ? 'primary' : 'neutral'" variant="subtle" size="sm">
                  {{ member.role === 'owner' ? 'Dono' : 'Membro' }}
                </UBadge>
                <UButton
                  v-if="member.role !== 'owner'"
                  icon="i-lucide-user-x"
                  variant="ghost"
                  color="error"
                  size="xs"
                  aria-label="Remover membro"
                  @click="removeMember(member)"
                />
              </div>
            </div>

            <div v-if="invites.length" class="space-y-2">
              <p class="text-xs font-medium text-muted uppercase tracking-wide">Convites pendentes</p>
              <div
                v-for="invite in invites"
                :key="invite.id"
                class="flex items-center gap-3 rounded-lg ring-1 ring-default px-4 py-2"
              >
                <UIcon name="i-lucide-link" class="size-4 text-muted" />
                <span class="text-sm text-muted flex-1">Criado em {{ inviteDate(invite.created_at) }} · vale 7 dias, uso único</span>
                <UButton label="Copiar link" variant="ghost" color="neutral" size="xs" @click="copyInvite(invite.token)" />
                <UButton icon="i-lucide-trash-2" variant="ghost" color="error" size="xs" aria-label="Revogar convite" @click="revokeInvite(invite)" />
              </div>
            </div>
          </section>
          </template>

          <template #empresa>
          <!-- Empresa -->
          <section class="space-y-3 pt-4">
            <div>
              <h2 class="font-display font-semibold text-lg">Empresa</h2>
              <p class="text-sm text-muted">Dados da empresa ativa.</p>
            </div>

            <form class="flex gap-2" @submit.prevent="renameCompany">
              <UInput v-model="companyName" class="flex-1" />
              <UButton type="submit" label="Salvar" :loading="savingCompany" :disabled="!companyName.trim() || companyName === active?.name" />
            </form>

            <div class="rounded-lg ring-1 ring-error/30 bg-error/5 px-4 py-3 flex items-center justify-between gap-3">
              <div>
                <p class="text-sm font-medium">Excluir empresa</p>
                <p class="text-xs text-muted">Apaga agentes, contas, contatos e conversas. Irreversível.</p>
              </div>
              <UButton label="Excluir" color="error" variant="soft" icon="i-lucide-trash-2" @click="removeCompany()" />
            </div>
          </section>
          </template>
        </UTabs>
      </div>
    </template>
  </UDashboardPanel>

  <USlideover
    v-model:open="accountOpen"
    :title="editingAccount ? 'Editar conta WhatsApp' : 'Conectar número'"
    :description="editingAccount ? editingAccount.phone_number_id : 'Dados do app Meta + número na Cloud API'"
  >
    <template #body>
      <form class="space-y-5" @submit.prevent="saveAccount">
        <UFormField label="Agente" required help="Quem responde as mensagens desse número">
          <USelect v-model="accountState.agent_id" :items="agentItems" placeholder="Escolha" class="w-full" />
        </UFormField>

        <UFormField label="Phone Number ID" :required="!editingAccount" :help="editingAccount ? 'Não editável' : undefined">
          <UInput v-model="accountState.phone_number_id" :disabled="!!editingAccount" class="w-full font-mono" />
        </UFormField>

        <UFormField label="WABA ID" required>
          <UInput v-model="accountState.waba_id" class="w-full font-mono" />
        </UFormField>

        <UFormField label="App ID" help="ID do app da Meta - só necessário pra cabeçalho de imagem/vídeo/PDF em templates">
          <UInput v-model="accountState.app_id" class="w-full font-mono" />
        </UFormField>

        <UFormField label="Access Token" :required="!editingAccount" :help="editingAccount ? 'Vazio = manter o atual' : 'Token permanente do System User'">
          <UInput v-model="accountState.access_token" type="password" class="w-full font-mono" autocomplete="off" />
        </UFormField>

        <UFormField label="App Secret" :required="!editingAccount" :help="editingAccount ? 'Vazio = manter o atual' : undefined">
          <UInput v-model="accountState.app_secret" type="password" class="w-full font-mono" autocomplete="off" />
        </UFormField>

        <UFormField label="Verify Token" required help="Invente um valor e use o mesmo no webhook da Meta">
          <UInput v-model="accountState.verify_token" class="w-full font-mono" />
        </UFormField>

        <UFormField
          label="URL de callback"
          help="Cole no app da Meta em WhatsApp → Configuração → Webhook, com o Verify Token acima, e assine o campo messages"
        >
          <UInput :model-value="webhookUrl" readonly class="w-full font-mono" @focus="($event.target as HTMLInputElement).select()">
            <template #trailing>
              <UButton icon="i-lucide-copy" variant="link" color="neutral" size="sm" aria-label="Copiar URL de callback" @click="copyWebhookUrl" />
            </template>
          </UInput>
        </UFormField>

        <UButton type="submit" block :loading="savingAccount" :disabled="!accountFormValid">
          {{ editingAccount ? 'Salvar alterações' : 'Conectar' }}
        </UButton>
      </form>
    </template>
  </USlideover>
</template>
