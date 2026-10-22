<script setup lang="ts">
// Template salvo (nome, idioma, variáveis) numa linha de resumo; o TemplatePicker abre num modal. Usado na abertura automática do Pipedrive
import type { AutoTemplate, Template } from '~/types/api'

defineProps<{ companyId: number, accountId: number, clearable?: boolean }>()
const model = defineModel<AutoTemplate>({ required: true })

// o picker reconhece o salvo por nome+idioma e troca pelo template completo ao carregar a lista
const picked = ref<Template | null>(
  model.value.template_name ? { name: model.value.template_name, language: model.value.language ?? '' } as Template : null
)
const params = ref<string[]>([...(model.value.body_params ?? [])])
const headerMediaId = ref(model.value.header_media_id ?? '')
watch([picked, params, headerMediaId], () => {
  if (!picked.value) {
    // removido: tira só o template, mantém o resto (ex.: after_hours do follow-up)
    const { template_name: _n, language: _l, body_params: _p, header_media_id: _h, ...rest } = model.value
    model.value = rest
    return
  }
  model.value = {
    ...model.value,
    template_name: picked.value.name,
    language: picked.value.language,
    body_params: [...params.value],
    header_media_id: headerMediaId.value
  }
}, { deep: true })

const open = ref(false)
const paramCount = computed(() => model.value.body_params?.length ?? 0)
</script>

<template>
  <div class="flex items-center gap-2 min-w-0">
    <button
      type="button"
      class="flex flex-1 min-w-0 items-center gap-2 rounded-md ring-1 ring-default px-2.5 py-1.5 text-left text-sm hover:bg-elevated/50"
      @click="open = true"
    >
      <UIcon name="i-lucide-layout-template" class="size-4 shrink-0 text-muted" />
      <template v-if="model.template_name">
        <span class="truncate font-mono">{{ model.template_name }}</span>
        <span class="shrink-0 text-xs text-muted">
          {{ model.language }}<template v-if="paramCount"> · {{ paramCount }} {{ paramCount === 1 ? 'variável' : 'variáveis' }}</template>
        </span>
      </template>
      <span v-else class="text-muted">Escolher template</span>
      <UIcon name="i-lucide-pencil" class="ms-auto size-3.5 shrink-0 text-muted" />
    </button>
    <UButton
      v-if="clearable && model.template_name"
      icon="i-lucide-x"
      variant="ghost"
      color="neutral"
      size="xs"
      aria-label="Remover template"
      @click="() => { picked = null }"
    />

    <UModal v-model:open="open" title="Template">
      <template #body>
        <div class="space-y-4">
          <TemplatePicker
            v-model:template="picked"
            v-model:params="params"
            v-model:header-media-id="headerMediaId"
            :company-id="companyId"
            :account-id="accountId"
          />
          <div class="flex justify-end">
            <UButton label="Pronto" @click="() => { open = false }" />
          </div>
        </div>
      </template>
    </UModal>
  </div>
</template>
