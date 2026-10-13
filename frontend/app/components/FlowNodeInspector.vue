<script setup lang="ts">
// Formulário do nó selecionado. Escreve direto no `data` do nó (objeto do grafo),
// então o canvas reflete a edição enquanto se digita.
import type { FlowNodeData, FlowNodeType, PipedrivePipeline, Tag, TagColor, Template } from '~/types/api'

const props = defineProps<{
  type: FlowNodeType
  companyId: number
  accountId: number
}>()

const data = defineModel<FlowNodeData>({ required: true })

const api = useApi()
const meta = computed(() => FLOW_NODE_META[props.type])

// ---- template ----
const picked = ref<Template | null>(null)
const pickedParams = ref<string[]>([])
const pickedHeaderMediaId = ref('')

watch(picked, (template) => {
  if (!template) return
  data.value.template_name = template.name
  data.value.language = template.language
})
watch([pickedParams, pickedHeaderMediaId], () => {
  if (!picked.value) return
  data.value.body_params = [...pickedParams.value]
  data.value.header_media_id = pickedHeaderMediaId.value
}, { deep: true })

// ---- botões ----
const buttons = computed(() => data.value.buttons ?? [])

function addButton() {
  if (buttons.value.length >= 3) return
  data.value.buttons = [...buttons.value, { id: `b${Math.random().toString(36).slice(2, 8)}`, title: '' }]
}

function removeButton(id: string) {
  data.value.buttons = buttons.value.filter(b => b.id !== id)
}

// ---- requisição HTTP ----
const httpMethodItems = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const httpHeaders = computed(() => data.value.headers ?? [])

function addHeader() {
  data.value.headers = [...httpHeaders.value, { key: '', value: '' }]
}

function removeHeader(index: number) {
  data.value.headers = httpHeaders.value.filter((_, i) => i !== index)
}

// ---- etiqueta ----
const tagItems = ref<{ label: string, value: number }[]>([])
const toast = useToast()

watch(() => props.type, async (type) => {
  if (type !== 'tag' || tagItems.value.length) return
  const tags = await api<Tag[]>(`/companies/${props.companyId}/tags`)
  tagItems.value = tags.map(t => ({ label: t.name, value: t.id }))
}, { immediate: true })

const newTag = reactive({ name: '', color: 'neutral' as TagColor })
const creatingTag = ref(false)

async function createTag(name: string, color: TagColor = 'neutral') {
  if (!name.trim() || creatingTag.value) return
  creatingTag.value = true
  try {
    const tag = await api<Tag>(`/companies/${props.companyId}/tags`, { method: 'POST', body: { name, color } })
    if (!tagItems.value.some(t => t.value === tag.id)) tagItems.value.push({ label: tag.name, value: tag.id })
    data.value.tag_id = tag.id
    newTag.name = ''
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível criar a etiqueta'), color: 'error' })
  } finally {
    creatingTag.value = false
  }
}

// ---- estágios do Pipedrive (nó crm: mover de estágio) ----
const stageItems = ref<{ label: string, value: number }[]>([])
const stagesError = ref('')

watch(() => [props.type, data.value.action], async ([type, action]) => {
  if (type !== 'crm' || action !== 'stage' || stageItems.value.length) return
  try {
    const pipelines = await api<PipedrivePipeline[]>(`/companies/${props.companyId}/pipedrive/pipelines`)
    stageItems.value = pipelines.flatMap(p => p.stages.map(s => ({ label: `${p.name} · ${s.name}`, value: s.id })))
  } catch (error) {
    stagesError.value = apiErrorDetail(error, 'Conecte o Pipedrive em Configurações')
  }
}, { immediate: true })
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center gap-2">
      <UIcon
        :name="meta.icon"
        class="size-4"
        :class="meta.color"
      />
      <p class="text-sm font-semibold">
        {{ meta.label }}
      </p>
    </div>
    <p class="text-xs text-muted">
      {{ meta.hint }}
    </p>

    <template v-if="type === 'template'">
      <p
        v-if="data.template_name"
        class="text-xs text-muted"
      >
        Atual: <span class="font-mono">{{ data.template_name }} ({{ data.language }})</span>
      </p>
      <TemplatePicker
        v-model:template="picked"
        v-model:params="pickedParams"
        v-model:header-media-id="pickedHeaderMediaId"
        :company-id="companyId"
        :account-id="accountId"
      />
    </template>

    <UFormField
      v-if="type === 'message' || type === 'buttons'"
      label="Mensagem"
      required
    >
      <UTextarea
        v-model="data.text"
        :rows="3"
        class="w-full"
        placeholder="Olá {{nome}}, tudo bem?"
      />
    </UFormField>

    <template v-if="type === 'buttons'">
      <UFormField
        label="Botões"
        help="Até 3, com no máximo 20 caracteres cada"
      >
        <div class="space-y-2">
          <div
            v-for="button in buttons"
            :key="button.id"
            class="flex items-center gap-2"
          >
            <UInput
              v-model="button.title"
              :maxlength="20"
              class="flex-1"
              placeholder="Texto do botão"
            />
            <UButton
              icon="i-lucide-trash-2"
              color="neutral"
              variant="ghost"
              size="sm"
              :disabled="buttons.length <= 1"
              @click="removeButton(button.id)"
            />
          </div>
          <UButton
            label="Adicionar botão"
            icon="i-lucide-plus"
            variant="soft"
            size="sm"
            :disabled="buttons.length >= 3"
            @click="addButton()"
          />
        </div>
      </UFormField>
    </template>

    <template v-if="type === 'wait'">
      <UFormField
        label="Guardar resposta em"
        help="Use depois numa condição"
      >
        <UInput
          v-model="data.var"
          class="w-full"
          placeholder="resposta"
        />
      </UFormField>
      <UFormField
        label="Desistir depois de (minutos)"
        help="0 = espera pra sempre"
      >
        <UInput
          v-model.number="data.timeout_minutes"
          type="number"
          min="0"
          class="w-full"
        />
      </UFormField>
    </template>

    <template v-if="type === 'condition'">
      <UFormField
        v-if="data.op !== 'window_open'"
        label="Comparar"
        help="last_message = última resposta, button = botão clicado na campanha, ou o nome de uma variável"
      >
        <UInput
          v-model="data.source"
          class="w-full"
          placeholder="last_message"
        />
      </UFormField>
      <UFormField
        label="Operador"
        :help="data.op === 'window_open' ? 'Sim se o contato escreveu nas últimas 24h: a IA pode responder sem template' : undefined"
      >
        <USelect
          v-model="data.op"
          :items="FLOW_CONDITION_OP_ITEMS"
          class="w-full"
        />
      </UFormField>
      <UFormField
        v-if="data.op !== 'window_open'"
        label="Valor"
        required
      >
        <UInput
          v-model="data.value"
          class="w-full"
          placeholder="orçamento"
        />
      </UFormField>
    </template>

    <UFormField
      v-if="type === 'delay'"
      label="Esperar (minutos)"
      :help="formatMinutes(data.minutes ?? 0)"
    >
      <UInput
        v-model.number="data.minutes"
        type="number"
        min="1"
        class="w-full"
      />
    </UFormField>

    <template v-if="type === 'http'">
      <div class="flex gap-2">
        <USelect
          v-model="data.method"
          :items="httpMethodItems"
          class="w-28"
        />
        <UInput
          v-model="data.url"
          class="flex-1"
          placeholder="https://sistema.com/webhook"
        />
      </div>
      <UFormField
        label="Headers"
        help="Ex.: Authorization: Bearer seu-token"
      >
        <div class="space-y-2">
          <div
            v-for="(header, index) in httpHeaders"
            :key="index"
            class="flex gap-2"
          >
            <UInput
              v-model="header.key"
              placeholder="Nome"
              class="w-2/5"
            />
            <UInput
              v-model="header.value"
              placeholder="Valor"
              class="flex-1"
            />
            <UButton
              icon="i-lucide-x"
              variant="ghost"
              color="neutral"
              aria-label="Remover header"
              @click="removeHeader(index)"
            />
          </div>
          <UButton
            label="Adicionar header"
            icon="i-lucide-plus"
            variant="soft"
            size="xs"
            @click="addHeader"
          />
        </div>
      </UFormField>
      <UFormField
        v-if="data.method !== 'GET'"
        label="Corpo"
        help="Sem Content-Type nos headers, vai como application/json"
      >
        <UTextarea
          v-model="data.body"
          :rows="6"
          class="w-full font-mono text-xs"
        />
      </UFormField>
      <p class="text-xs text-muted">
        Variáveis: <code v-pre>{{nome}}</code>, <code v-pre>{{telefone}}</code>, <code v-pre>{{botao}}</code>, <code v-pre>{{campanha}}</code> e as guardadas no fluxo (<code v-pre>{{resposta}}</code>...). Só https, endereço interno é recusado. Timeout de 10s.
      </p>
    </template>

    <template v-if="type === 'tag'">
      <UFormField label="Ação">
        <USelect
          v-model="data.action"
          :items="[{ label: 'Adicionar', value: 'add' }, { label: 'Remover', value: 'remove' }]"
          class="w-full"
        />
      </UFormField>
      <UFormField
        label="Etiqueta"
        required
        help="Digite pra buscar ou criar uma nova"
      >
        <USelectMenu
          v-model="data.tag_id"
          :items="tagItems"
          value-key="value"
          create-item
          placeholder="Escolha"
          class="w-full"
          @create="name => createTag(name)"
        />
      </UFormField>
      <UFormField
        label="Nova etiqueta"
        help="Cria e já seleciona neste bloco"
      >
        <form
          class="flex gap-2"
          @submit.prevent="createTag(newTag.name, newTag.color)"
        >
          <UInput
            v-model="newTag.name"
            placeholder="bloqueado"
            maxlength="40"
            class="flex-1"
          />
          <USelect
            v-model="newTag.color"
            :items="TAG_COLOR_ITEMS"
            class="w-28"
          />
          <UButton
            type="submit"
            icon="i-lucide-plus"
            variant="soft"
            aria-label="Criar etiqueta"
            :loading="creatingTag"
            :disabled="!newTag.name.trim()"
          />
        </form>
      </UFormField>
    </template>

    <template v-if="type === 'crm'">
      <UFormField label="Ação">
        <USelect
          v-model="data.action"
          :items="FLOW_CRM_ACTION_ITEMS"
          class="w-full"
        />
      </UFormField>
      <UFormField
        v-if="data.action === 'deal'"
        label="Título do negócio"
        required
      >
        <UInput
          v-model="data.title"
          class="w-full"
          placeholder="Lead {{nome}}"
        />
      </UFormField>
      <UFormField
        v-if="data.action === 'note'"
        label="Anotação"
        required
      >
        <UTextarea
          v-model="data.text"
          :rows="3"
          class="w-full"
          placeholder="Veio do formulário do site"
        />
      </UFormField>
      <UFormField
        v-if="data.action === 'stage'"
        label="Estágio de destino"
        required
      >
        <USelect
          v-model="data.stage_id"
          :items="stageItems"
          :placeholder="stagesError || 'Escolha o estágio'"
          :disabled="!stageItems.length"
          class="w-full"
        />
      </UFormField>
    </template>
  </div>
</template>
