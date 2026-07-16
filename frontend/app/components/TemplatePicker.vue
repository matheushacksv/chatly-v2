<script setup lang="ts">
import type { Template } from '~/types/api'

const props = defineProps<{ companyId: number, accountId: number }>()
const template = defineModel<Template | null>('template', { default: null })
const params = defineModel<string[]>('params', { default: () => [] })

const api = useApi()
const templates = ref<Template[]>([])
const loading = ref(false)
const selectedKey = ref<string | null>(null)

watch(() => props.accountId, load, { immediate: true })
async function load() {
  loading.value = true
  selectedKey.value = null
  try {
    templates.value = await api<Template[]>(
      `/companies/${props.companyId}/accounts/${props.accountId}/templates`
    )
  } finally {
    loading.value = false
  }
}

// nome+idioma identificam o template (mesmo nome pode existir em vários idiomas)
const key = (t: Template) => `${t.name}|${t.language}`
const items = computed(() =>
  templates.value
    .filter(t => t.status === 'APPROVED')
    .map(t => ({ label: `${t.name} (${t.language})`, value: key(t) }))
)

watch(selectedKey, (k) => {
  template.value = templates.value.find(t => key(t) === k) ?? null
  params.value = Array.from({ length: template.value?.param_count ?? 0 }, () => '')
})

// preview ao vivo: variáveis preenchidas substituem os {{n}} do corpo
const preview = computed(() =>
  template.value?.body_text.replace(/\{\{(\d+)\}\}/g, (raw, i) => params.value[+i - 1]?.trim() || raw) ?? ''
)
</script>

<template>
  <div class="space-y-3">
    <UFormField label="Template" required help="Aprovados pela Meta - crie novos no WhatsApp Manager">
      <USelect
        v-model="selectedKey"
        :items="items"
        :loading="loading"
        :placeholder="items.length ? 'Escolha um template' : 'Nenhum template aprovado'"
        :disabled="!items.length && !loading"
        icon="i-lucide-layout-template"
        class="w-full"
      />
    </UFormField>

    <template v-if="template">
      <div class="rounded-lg bg-elevated ring-1 ring-default p-3 text-sm whitespace-pre-wrap break-words">
        {{ preview }}
      </div>

      <UFormField
        v-for="i in template.param_count"
        :key="i"
        :label="`Variável {{${i}}}`"
        required
        :help="i === 1 ? 'Use {{nome}} pra inserir o nome de cada contato' : undefined"
      >
        <UInput v-model="params[i - 1]" class="w-full" :placeholder="`Valor de {{${i}}}`" />
      </UFormField>
    </template>
  </div>
</template>
