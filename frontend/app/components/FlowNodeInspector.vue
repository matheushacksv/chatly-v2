<script setup lang="ts">
// Formulário do nó selecionado. Escreve direto no `data` do nó (objeto do grafo),
// então o canvas reflete a edição enquanto se digita.
import type { FlowNodeData, FlowNodeType, PipedrivePipeline, Template } from '~/types/api'

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
        label="Comparar"
        help="last_message = a última resposta do contato"
      >
        <UInput
          v-model="data.source"
          class="w-full"
          placeholder="last_message"
        />
      </UFormField>
      <UFormField label="Operador">
        <USelect
          v-model="data.op"
          :items="FLOW_CONDITION_OP_ITEMS"
          class="w-full"
        />
      </UFormField>
      <UFormField
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
