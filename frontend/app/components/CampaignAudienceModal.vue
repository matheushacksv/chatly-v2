<script setup lang="ts">
// Público da campanha por etiquetas: OU entre grupos, E dentro do grupo, com contagem ao vivo.
import type { Tag } from '~/types/api'

const props = defineProps<{ companyId: number, accountId: number, tags: Tag[] }>()
const open = defineModel<boolean>('open', { default: false })
const groups = defineModel<number[][]>('groups', { default: () => [] })
const exclude = defineModel<number[]>('exclude', { default: () => [] })
const emit = defineEmits<{ applied: [count: number] }>()

const api = useApi()
const tagItems = computed(() => props.tags.map(t => ({ label: t.name, value: t.id })))

// rascunho: só vale ao clicar em Aplicar
const draftGroups = ref<number[][]>([])
const draftExclude = ref<number[]>([])
watch(open, (isOpen) => {
  if (!isOpen) return
  draftGroups.value = groups.value.length ? groups.value.map(g => [...g]) : [[]]
  draftExclude.value = [...exclude.value]
})

const preview = ref<{ count: number, sample: string[] } | null>(null)
const counting = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
watch([draftGroups, draftExclude, open], () => {
  if (!open.value) return
  clearTimeout(timer)
  timer = setTimeout(async () => {
    counting.value = true
    try {
      preview.value = await api(`/companies/${props.companyId}/campaigns/audience`, {
        method: 'POST',
        body: { account_id: props.accountId, tag_groups: draftGroups.value.filter(g => g.length), exclude_tag_ids: draftExclude.value }
      })
    } finally {
      counting.value = false
    }
  }, 300)
}, { deep: true })

function apply() {
  groups.value = draftGroups.value.filter(g => g.length)
  exclude.value = draftExclude.value
  emit('applied', preview.value?.count ?? 0)
  open.value = false
}
</script>

<template>
  <UModal v-model:open="open" title="Filtrar destinatários" description="Contatos com todas as etiquetas de um grupo. Vários grupos: basta bater com um deles.">
    <template #body>
      <div class="space-y-4">
        <template v-for="(group, i) in draftGroups" :key="i">
          <div v-if="i > 0" class="flex items-center gap-2 text-xs font-semibold text-muted">
            <USeparator class="flex-1" />OU<USeparator class="flex-1" />
          </div>
          <div class="flex items-start gap-2">
            <UFormField :label="`Grupo ${i + 1}: tem todas estas`" class="flex-1">
              <USelectMenu
                v-model="draftGroups[i]"
                :items="tagItems"
                value-key="value"
                multiple
                placeholder="Escolha etiquetas"
                class="w-full"
              />
            </UFormField>
            <UButton
              v-if="draftGroups.length > 1"
              icon="i-lucide-x"
              variant="ghost"
              color="neutral"
              class="mt-6"
              :aria-label="`Remover grupo ${i + 1}`"
              @click="() => { draftGroups.splice(i, 1) }"
            />
          </div>
        </template>
        <UButton label="Adicionar grupo (OU)" icon="i-lucide-plus" variant="soft" size="sm" @click="() => { draftGroups.push([]) }" />

        <UFormField label="Nunca enviar pra quem tem" help="Ex.: bloqueado. Vale também pra seleção manual e CSV">
          <USelectMenu v-model="draftExclude" :items="tagItems" value-key="value" multiple placeholder="Nenhuma" class="w-full" />
        </UFormField>

        <UAlert
          :icon="counting ? 'i-lucide-loader-circle' : 'i-lucide-users'"
          color="neutral"
          variant="subtle"
          :title="preview ? `${preview.count} contato(s) recebem` : 'Calculando...'"
          :description="preview?.sample.length ? `${preview.sample.join(', ')}${preview.count > preview.sample.length ? '...' : ''}` : undefined"
        />
      </div>
    </template>
    <template #footer>
      <div class="flex justify-end gap-2 w-full">
        <UButton label="Cancelar" variant="ghost" color="neutral" @click="() => { open = false }" />
        <UButton label="Aplicar" icon="i-lucide-check" @click="apply" />
      </div>
    </template>
  </UModal>
</template>
