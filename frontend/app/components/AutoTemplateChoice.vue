<script setup lang="ts">
// Template salvo (nome, idioma, variáveis) com troca pelo TemplatePicker: usado na abertura automática do Pipedrive
import type { AutoTemplate, Template } from '~/types/api'

defineProps<{ companyId: number, accountId: number }>()
const model = defineModel<AutoTemplate>({ required: true })

const picked = ref<Template | null>(null)
const params = ref<string[]>([])
const headerMediaId = ref('')
watch([picked, params, headerMediaId], () => {
  if (!picked.value) return
  model.value = {
    template_name: picked.value.name,
    language: picked.value.language,
    body_params: [...params.value],
    header_media_id: headerMediaId.value
  }
}, { deep: true })
</script>

<template>
  <div>
    <p v-if="model.template_name" class="text-xs text-muted mb-2">
      Atual: <span class="font-mono">{{ model.template_name }} ({{ model.language }})</span>
    </p>
    <TemplatePicker
      v-model:template="picked"
      v-model:params="params"
      v-model:header-media-id="headerMediaId"
      :company-id="companyId"
      :account-id="accountId"
    />
  </div>
</template>
