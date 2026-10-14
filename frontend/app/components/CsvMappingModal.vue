<script setup lang="ts">
// Mapeia cada coluna do CSV pra telefone, nome, campo personalizado (existente ou novo) ou ignorar.
import type { ContactField, CsvRecipient, Tag } from '~/types/api'
import type { CsvTarget } from '~/composables/useContactFields'

const props = defineProps<{ text: string, companyId: number, confirmLabel: string }>()
const open = defineModel<boolean>('open', { default: false })
const emit = defineEmits<{ confirm: [result: { rows: CsvRecipient[], invalid: string[], tagIds: number[] }] }>()

const api = useApi()
const toast = useToast()
const { isOwner } = useCompany()
const { fields, load } = useContactFields()

const rows = computed(() => readCsv(props.text))
const hasHeader = ref(false)
const targets = ref<CsvTarget[]>([])
const saving = ref(false)

// etiquetas opcionais pra marcar todos os importados (ex.: lista_outubro)
const tags = ref<Tag[]>([])
const tagIds = ref<number[]>([])
const tagItems = computed(() => tags.value.map(t => ({ label: t.name, value: t.id })))
async function createTag(name: string) {
  try {
    const tag = await api<Tag>(`/companies/${props.companyId}/tags`, { method: 'POST', body: { name } })
    if (!tags.value.some(t => t.id === tag.id)) tags.value = [...tags.value, tag]
    if (!tagIds.value.includes(tag.id)) tagIds.value = [...tagIds.value, tag.id]
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível criar a etiqueta'), color: 'error' })
  }
}

watch(() => [open.value, props.text], async () => {
  if (!open.value) return
  await load()
  tagIds.value = []
  tags.value = await api<Tag[]>(`/companies/${props.companyId}/tags`)
  hasHeader.value = looksLikeHeader(rows.value[0])
  targets.value = suggestMapping(rows.value, hasHeader.value, fields.value)
}, { immediate: true })
// ligar/desligar o cabeçalho muda os nomes das colunas: refaz a sugestão
watch(hasHeader, (value, old) => {
  if (old !== undefined) targets.value = suggestMapping(rows.value, value, fields.value)
})

const columnName = (i: number) => (hasHeader.value ? rows.value[0]?.[i] : '') || `Coluna ${i + 1}`
const samples = (i: number) => rows.value.slice(hasHeader.value ? 1 : 0, hasHeader.value ? 3 : 2).map(r => r[i] ?? '').filter(Boolean)

function itemsFor(i: number) {
  const name = columnName(i)
  return [
    { label: 'Telefone', value: 'phone' },
    { label: 'Nome', value: 'name' },
    ...fields.value.map((f: ContactField) => ({ label: `Campo: ${f.label}`, value: `field:${f.key}` })),
    ...(isOwner.value && hasHeader.value ? [{ label: `Criar campo "${name}"`, value: `new:${i}` }] : []),
    { label: 'Ignorar', value: 'skip' }
  ]
}

const phoneCount = computed(() => targets.value.filter(t => t === 'phone').length)
// prévia: campo novo ainda não tem variável, usa uma provisória só pra contar
const preview = computed(() => applyMapping(rows.value, hasHeader.value, targets.value))

async function confirm() {
  if (phoneCount.value !== 1 || saving.value) return
  saving.value = true
  try {
    const newKeys: Record<string, string> = {}
    for (const target of targets.value) {
      if (!target.startsWith('new:') || newKeys[target]) continue
      const label = columnName(Number(target.slice(4)))
      const existing = fields.value.find(f => f.label.toLowerCase() === label.toLowerCase())
      const field = existing ?? await api<ContactField>(`/companies/${props.companyId}/contact-fields`, { method: 'POST', body: { label } })
      if (!existing) fields.value = [...fields.value, field].sort((a, b) => a.label.localeCompare(b.label))
      newKeys[target] = field.key
    }
    emit('confirm', { ...applyMapping(rows.value, hasHeader.value, targets.value, newKeys), tagIds: tagIds.value })
    open.value = false
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível criar o campo'), color: 'error' })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <UModal v-model:open="open" title="Mapear colunas" description="Diga o que é cada coluna da planilha" :ui="{ content: 'sm:max-w-3xl' }">
    <template #body>
      <div class="space-y-4">
        <UCheckbox v-model="hasHeader" label="A primeira linha é o cabeçalho (nomes das colunas)" />

        <div class="rounded-lg ring-1 ring-default divide-y divide-default">
          <div class="grid grid-cols-[1fr_1fr] gap-3 px-3 py-2 text-xs font-semibold text-muted uppercase">
            <span>Coluna da planilha</span>
            <span>Vai para</span>
          </div>
          <div v-for="(target, i) in targets" :key="i" class="grid grid-cols-[1fr_1fr] gap-3 px-3 py-2 items-center">
            <div class="min-w-0">
              <p class="text-sm font-medium truncate">
                {{ columnName(i) }}
              </p>
              <p class="text-xs text-muted truncate">
                {{ samples(i).join(' · ') || 'vazia' }}
              </p>
            </div>
            <USelect v-model="targets[i]" :items="itemsFor(i)" class="w-full" :aria-label="`Destino da coluna ${columnName(i)}`" />
          </div>
        </div>

        <UFormField label="Etiquetar todos os importados" help="Opcional. Digite pra criar uma etiqueta nova">
          <USelectMenu
            v-model="tagIds"
            :items="tagItems"
            value-key="value"
            multiple
            create-item
            placeholder="Nenhuma"
            icon="i-lucide-tag"
            class="w-full"
            :search-input="{ placeholder: 'Buscar ou criar...' }"
            @create="createTag"
          />
        </UFormField>

        <UAlert
          v-if="phoneCount !== 1"
          icon="i-lucide-triangle-alert"
          color="warning"
          variant="subtle"
          :title="phoneCount ? 'Escolha só uma coluna de telefone' : 'Escolha a coluna do telefone'"
        />
        <template v-else>
          <UAlert
            icon="i-lucide-check"
            color="success"
            variant="subtle"
            :title="`${preview.rows.length} contato(s) com telefone válido`"
          />
          <UAlert
            v-if="preview.invalid.length"
            icon="i-lucide-triangle-alert"
            color="warning"
            variant="subtle"
            :title="`${preview.invalid.length} linha(s) ignorada(s) - telefone inválido`"
            :description="preview.invalid.slice(0, 3).join(' · ')"
          />
        </template>
      </div>
    </template>
    <template #footer>
      <div class="flex justify-end gap-2 w-full">
        <UButton label="Cancelar" variant="ghost" color="neutral" @click="() => { open = false }" />
        <UButton
          :label="confirmLabel.replace('{n}', String(preview.rows.length))"
          icon="i-lucide-check"
          :loading="saving"
          :disabled="phoneCount !== 1 || !preview.rows.length"
          @click="confirm"
        />
      </div>
    </template>
  </UModal>
</template>
