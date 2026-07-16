<script setup lang="ts">
import type { Agent, Credential, Invite, Member, WhatsAppAccount } from '~/types/api'

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
  verify_token: ''
})

function openAccountCreate() {
  editingAccount.value = null
  Object.assign(accountState, { agent_id: null, phone_number_id: '', waba_id: '', access_token: '', app_secret: '', verify_token: '' })
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
    verify_token: account.verify_token
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
        verify_token: accountState.verify_token
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

async function copyInvite(token: string) {
  await navigator.clipboard.writeText(`${location.origin}/convite/${token}`)
  toast.add({ title: 'Link de convite copiado', icon: 'i-lucide-clipboard-check' })
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
</script>

<template>
  <UDashboardPanel id="configuracoes">
    <template #header>
      <UDashboardNavbar title="Configurações" />
    </template>

    <template #body>
      <div class="max-w-3xl space-y-8">
        <!-- member acessando por URL direta: backend devolve 404 nas rotas de admin -->
        <UAlert
          v-if="!isOwner"
          icon="i-lucide-lock"
          title="Somente o dono gerencia estas configurações"
          description="Peça ao dono da empresa para alterar credenciais, contas WhatsApp e membros."
          variant="subtle"
          color="neutral"
        />

        <template v-else>
          <!-- Credenciais -->
          <section class="space-y-3">
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

          <USeparator />

          <!-- Contas WhatsApp -->
          <section class="space-y-3">
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
                <UButton icon="i-lucide-pencil" variant="ghost" color="neutral" size="xs" @click="openAccountEdit(account)" />
                <UButton icon="i-lucide-trash-2" variant="ghost" color="error" size="xs" @click="removeAccount(account)" />
              </div>
            </div>
            <p v-else-if="agents.length" class="text-sm text-muted">Nenhum número conectado ainda.</p>
          </section>

          <USeparator />

          <!-- Membros -->
          <section class="space-y-3">
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

          <USeparator />

          <!-- Empresa -->
          <section class="space-y-3">
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

        <UFormField label="Access Token" :required="!editingAccount" :help="editingAccount ? 'Vazio = manter o atual' : 'Token permanente do System User'">
          <UInput v-model="accountState.access_token" type="password" class="w-full font-mono" autocomplete="off" />
        </UFormField>

        <UFormField label="App Secret" :required="!editingAccount" :help="editingAccount ? 'Vazio = manter o atual' : undefined">
          <UInput v-model="accountState.app_secret" type="password" class="w-full font-mono" autocomplete="off" />
        </UFormField>

        <UFormField label="Verify Token" required help="Mesmo valor configurado no webhook da Meta">
          <UInput v-model="accountState.verify_token" class="w-full font-mono" />
        </UFormField>

        <UButton type="submit" block :loading="savingAccount" :disabled="!accountFormValid">
          {{ editingAccount ? 'Salvar alterações' : 'Conectar' }}
        </UButton>
      </form>
    </template>
  </USlideover>
</template>
