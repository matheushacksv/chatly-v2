<script setup lang="ts">
// Template salvo (nome, idioma, variáveis) editado pelo TemplatePicker: usado na abertura automática do Pipedrive
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
</script>

<template>
  <TemplatePicker
    v-model:template="picked"
    v-model:params="params"
    v-model:header-media-id="headerMediaId"
    :company-id="companyId"
    :account-id="accountId"
    :clearable="clearable"
  />
</template>
