<script setup lang="ts">
// Ferramentas personalizadas (HTTP) do agente: o dono cria, a IA chama quando a descrição bate com a conversa.
import type { AgentTool, AgentToolParam, HttpMethod } from '~/types/api'

const props = defineProps<{ companyId: number, agentId: number, isOwner: boolean }>()
// lista mudou: o editor de prompt atualiza as referências de ferramentas
const emit = defineEmits<{ changed: [] }>()

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()
const { variables, load: loadFields } = useContactFields()
onMounted(loadFields)

const base = computed(() => `/companies/${props.companyId}/agents/${props.agentId}/tools`)
const tools = ref<AgentTool[]>([])
watch(base, async () => {
  tools.value = await api<AgentTool[]>(base.value)
}, { immediate: true })

const METHODS: HttpMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const PARAM_TYPES = [
  { label: 'Texto', value: 'string' },
  { label: 'Número', value: 'number' },
  { label: 'Sim/Não', value: 'boolean' }
]

// ---- formulário (criar/editar) ----
const formOpen = ref(false)
const editing = ref<AgentTool | null>(null)
const saving = ref(false)
const emptyForm = () => ({
  name: '',
  description: '',
  method: 'POST' as HttpMethod,
  url: '',
  parameters: [] as AgentToolParam[],
  headers: [] as { key: string, value: string }[],
  secret_headers: [] as { key: string, value: string }[],
  body: '',
  requires_confirmation: false,
  active: true
})
const form = reactive(emptyForm())

function openForm(tool: AgentTool | null) {
  editing.value = tool
  Object.assign(form, emptyForm(), tool
    ? {
        ...tool,
        parameters: tool.parameters.map(p => ({ ...p })),
        headers: tool.headers.map(h => ({ ...h })),
        // segredo salvo aparece só pelo nome; valor vazio = manter
        secret_headers: tool.secret_header_keys.map(key => ({ key, value: '' }))
      }
    : {})
  testResult.value = null
  formOpen.value = true
}

// nome da função vem do que a pessoa digita: "Consultar pagamento" vira consultar_pagamento
const toKey = (s: string) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 64)

const variableHint = computed(() => [
  ...form.parameters.filter(p => p.name).map(p => `{{${p.name}}}`),
  ...variables.value.map(v => `{{${v.key}}}`)
].join(' '))

async function save() {
  saving.value = true
  try {
    const body = { ...form, name: toKey(form.name), parameters: form.parameters.map(p => ({ ...p, name: toKey(p.name) })) }
    const saved = editing.value
      ? await api<AgentTool>(`${base.value}/${editing.value.id}`, { method: 'PATCH', body })
      : await api<AgentTool>(base.value, { method: 'POST', body })
    tools.value = editing.value ? tools.value.map(t => t.id === saved.id ? saved : t) : [...tools.value, saved]
    editing.value = saved
    emit('changed')
    toast.add({ title: 'Ferramenta salva', icon: 'i-lucide-check', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível salvar'), color: 'error' })
  } finally {
    saving.value = false
  }
}

async function toggleActive(tool: AgentTool, active: boolean) {
  try {
    const saved = await api<AgentTool>(`${base.value}/${tool.id}`, { method: 'PATCH', body: { active } })
    tools.value = tools.value.map(t => t.id === saved.id ? saved : t)
    emit('changed')
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

async function remove(tool: AgentTool) {
  const ok = await confirmModal({ title: `Excluir a ferramenta ${tool.name}?`, description: 'A IA deixa de usar na hora.', confirmLabel: 'Excluir' })
  if (!ok) return
  try {
    await api(`${base.value}/${tool.id}`, { method: 'DELETE' })
    tools.value = tools.value.filter(t => t.id !== tool.id)
    emit('changed')
    formOpen.value = false
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// ---- testar com valores de exemplo (usa a versão salva) ----
const testArgs = ref<Record<string, string>>({})
const testing = ref(false)
const testResult = ref<{ ok: boolean, result: string } | null>(null)

async function runTest() {
  if (!editing.value) return
  testing.value = true
  try {
    testResult.value = await api(`${base.value}/${editing.value.id}/test`, { method: 'POST', body: { arguments: testArgs.value } })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha no teste'), color: 'error' })
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="space-y-3">
    <p class="text-xs text-muted">
      Chamadas HTTP pra sistemas externos (pagamento, agenda, licença...). A IA decide quando usar pela descrição e preenche os parâmetros com o que o cliente disse.
    </p>
    <UButton
      v-if="isOwner"
      label="Nova ferramenta"
      icon="i-lucide-plus"
      variant="soft"
      @click="openForm(null)"
    />

    <div v-if="tools.length" class="space-y-2">
      <div v-for="tool in tools" :key="tool.id" class="flex items-center gap-3 rounded-lg ring-1 ring-default px-3 py-2">
        <UIcon name="i-lucide-plug" class="size-4 text-muted shrink-0" />
        <button type="button" class="min-w-0 flex-1 text-left" :disabled="!isOwner" @click="openForm(tool)">
          <p class="text-sm font-mono truncate">
            {{ tool.name }}
          </p>
          <p class="text-xs text-muted truncate">
            {{ tool.description }}
          </p>
        </button>
        <USwitch
          :model-value="tool.active"
          :disabled="!isOwner"
          :aria-label="`Ativar ${tool.name}`"
          @update:model-value="toggleActive(tool, $event)"
        />
      </div>
    </div>
    <p v-else class="text-sm text-muted">
      Nenhuma ferramenta ainda.
    </p>

    <UModal v-model:open="formOpen" :title="editing ? `Editar ${editing.name}` : 'Nova ferramenta'" :ui="{ content: 'max-w-2xl' }">
      <template #body>
        <form class="space-y-4" @submit.prevent="save">
          <UFormField label="Nome" required :help="form.name ? `A IA vê como ${toKey(form.name)}` : 'Ex.: consultar pagamento'">
            <UInput v-model="form.name" class="w-full" placeholder="consultar_pagamento" />
          </UFormField>
          <UFormField label="Quando a IA deve usar" required help="Seja específico: é por esse texto que a IA decide chamar">
            <UTextarea v-model="form.description" :rows="3" class="w-full" placeholder="Use quando o cliente disser que pagou, para conferir se o pagamento foi aprovado." />
          </UFormField>

          <UFormField label="Parâmetros que a IA preenche" help="Viram variáveis na URL, cabeçalhos e corpo">
            <div class="space-y-2">
              <div v-for="(param, i) in form.parameters" :key="i" class="flex flex-wrap items-start gap-2 rounded-md ring-1 ring-default p-2">
                <UInput v-model="param.name" placeholder="email" class="w-32" aria-label="Nome do parâmetro" />
                <USelect v-model="param.type" :items="PARAM_TYPES" class="w-28" aria-label="Tipo" />
                <UInput v-model="param.description" placeholder="Email do cliente" class="flex-1 min-w-40" aria-label="Descrição do parâmetro" />
                <UCheckbox v-model="param.required" label="Obrigatório" class="mt-1.5" />
                <UButton icon="i-lucide-x" variant="ghost" color="neutral" size="sm" aria-label="Remover parâmetro" @click="() => { form.parameters.splice(i, 1) }" />
              </div>
              <UButton
                label="Adicionar parâmetro"
                icon="i-lucide-plus"
                variant="ghost"
                size="sm"
                @click="() => { form.parameters.push({ name: '', type: 'string', description: '', required: true }) }"
              />
            </div>
          </UFormField>

          <div class="flex gap-2">
            <UFormField label="Método" class="w-28">
              <USelect v-model="form.method" :items="METHODS" class="w-full" />
            </UFormField>
            <UFormField label="URL" required class="flex-1">
              <UInput v-model="form.url" class="w-full" placeholder="https://api.exemplo.com/pagamentos?email={{email}}" />
            </UFormField>
          </div>

          <UFormField label="Cabeçalhos">
            <div class="space-y-2">
              <div v-for="(header, i) in form.headers" :key="i" class="flex gap-2">
                <UInput v-model="header.key" placeholder="X-Origem" class="w-40" aria-label="Nome do cabeçalho" />
                <UInput v-model="header.value" placeholder="valor" class="flex-1" aria-label="Valor do cabeçalho" />
                <UButton icon="i-lucide-x" variant="ghost" color="neutral" size="sm" aria-label="Remover cabeçalho" @click="() => { form.headers.splice(i, 1) }" />
              </div>
              <UButton label="Adicionar cabeçalho" icon="i-lucide-plus" variant="ghost" size="sm" @click="() => { form.headers.push({ key: '', value: '' }) }" />
            </div>
          </UFormField>

          <UFormField label="Cabeçalhos secretos" help="Tokens de API. Ficam criptografados e não aparecem de novo; deixe vazio pra manter o salvo">
            <div class="space-y-2">
              <div v-for="(header, i) in form.secret_headers" :key="i" class="flex gap-2">
                <UInput v-model="header.key" placeholder="Authorization" class="w-40" aria-label="Nome do cabeçalho secreto" />
                <UInput
                  v-model="header.value"
                  type="password"
                  :placeholder="editing?.secret_header_keys.includes(header.key) ? 'Salvo (vazio mantém)' : 'Bearer seu-token'"
                  class="flex-1"
                  aria-label="Valor secreto"
                />
                <UButton icon="i-lucide-x" variant="ghost" color="neutral" size="sm" aria-label="Remover cabeçalho secreto" @click="() => { form.secret_headers.splice(i, 1) }" />
              </div>
              <UButton
                label="Adicionar segredo"
                icon="i-lucide-key-round"
                variant="ghost"
                size="sm"
                @click="() => { form.secret_headers.push({ key: 'Authorization', value: '' }) }"
              />
            </div>
          </UFormField>

          <UFormField v-if="form.method !== 'GET'" label="Corpo (JSON)">
            <UTextarea v-model="form.body" :rows="5" class="w-full font-mono text-xs" placeholder="{&quot;email&quot;: &quot;{{email}}&quot;, &quot;nome&quot;: &quot;{{nome}}&quot;}" />
          </UFormField>
          <p class="text-xs text-muted">
            Variáveis: <span class="font-mono">{{ variableHint }}</span>. Só https; endereço interno é recusado. Timeout de 10s.
          </p>

          <USwitch
            v-model="form.requires_confirmation"
            label="Pedir confirmação ao cliente antes"
            description="Pra ações com dinheiro ou acesso: a IA confirma os dados e espera um sim"
          />
          <USwitch v-model="form.active" label="Ativa" />

          <div class="flex justify-between gap-2">
            <UButton v-if="editing" label="Excluir" icon="i-lucide-trash-2" color="error" variant="ghost" @click="remove(editing)" />
            <UButton type="submit" label="Salvar" icon="i-lucide-check" :loading="saving" class="ml-auto" />
          </div>
        </form>

        <template v-if="editing">
          <USeparator class="my-5" label="Testar" />
          <div class="space-y-3">
            <p class="text-xs text-muted">
              Faz a chamada de verdade com a versão salva. O nome do contato vira "Contato de teste".
            </p>
            <UFormField v-for="param in editing.parameters" :key="param.name" :label="param.name">
              <UInput v-model="testArgs[param.name]" class="w-full" :placeholder="param.description" />
            </UFormField>
            <UButton label="Testar chamada" icon="i-lucide-play" variant="soft" :loading="testing" @click="runTest" />
            <pre
              v-if="testResult"
              class="text-xs rounded-lg p-3 whitespace-pre-wrap break-words max-h-64 overflow-y-auto"
              :class="testResult.ok ? 'bg-success/10' : 'bg-error/10'"
            >{{ testResult.result }}</pre>
          </div>
        </template>
      </template>
    </UModal>
  </div>
</template>
