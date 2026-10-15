<script setup lang="ts">
// Botão "Variável": escolhe {{nome}}, {{campo}} ou variável do fluxo em vez de digitar entre chaves.
const props = defineProps<{ flowVars?: string[], extra?: { label: string, key: string }[] }>()
const emit = defineEmits<{ insert: [token: string] }>()

const { variables, load } = useContactFields()
onMounted(load)

const items = computed(() => [
  [
    { type: 'label' as const, label: 'Contato' },
    ...variables.value.map(v => ({ label: v.label, suffix: `{{${v.key}}}`, onSelect: () => emit('insert', `{{${v.key}}}`) }))
  ],
  // grupo "Fluxo" só existe no editor de fluxos (respostas rápidas não têm variáveis de fluxo)
  ...(props.extra?.length || props.flowVars?.length
    ? [[
        { type: 'label' as const, label: 'Fluxo' },
        ...(props.extra ?? []).map(v => ({ label: v.label, suffix: `{{${v.key}}}`, onSelect: () => emit('insert', `{{${v.key}}}`) })),
        ...(props.flowVars ?? []).map(key => ({ label: `Resposta guardada: ${key}`, suffix: `{{${key}}}`, onSelect: () => emit('insert', `{{${key}}}`) }))
      ]]
    : [])
])
</script>

<template>
  <UDropdownMenu :items="items" :content="{ align: 'end' }" :ui="{ content: 'max-h-72 overflow-y-auto' }">
    <UButton label="Variável" icon="i-lucide-braces" variant="ghost" size="xs" />
  </UDropdownMenu>
</template>
