<script setup lang="ts">
import type { MediaUpload, Template } from '~/types/api'

const props = defineProps<{ companyId: number, accountId: number }>()
const template = defineModel<Template | null>('template', { default: null })
const params = defineModel<string[]>('params', { default: () => [] })
const headerMediaId = defineModel<string>('headerMediaId', { default: '' })

const api = useApi()
const toast = useToast()
const templates = ref<Template[]>([])
const loading = ref(false)
const selectedKey = ref<string | null>(null)

// nome+idioma identificam o template (mesmo nome pode existir em vários idiomas)
const key = (t: Pick<Template, 'name' | 'language'>) => `${t.name}|${t.language}`

// template já vindo do pai (salvo antes) aparece selecionado, mantendo as variáveis preenchidas
let restoring = false
watch(() => props.accountId, load, { immediate: true })
async function load() {
  const wanted = template.value ? key(template.value) : null
  loading.value = true
  selectedKey.value = null
  try {
    templates.value = await api<Template[]>(
      `/companies/${props.companyId}/accounts/${props.accountId}/templates`
    )
    if (wanted && items.value.some(i => i.value === wanted)) {
      restoring = true
      selectedKey.value = wanted
    }
  } finally {
    loading.value = false
  }
}
const items = computed(() =>
  templates.value
    .filter(t => t.status === 'APPROVED')
    .map(t => ({ label: `${t.name} (${t.language})`, value: key(t) }))
)

watch(selectedKey, (k) => {
  template.value = templates.value.find(t => key(t) === k) ?? null
  if (restoring) {
    restoring = false
    return
  }
  params.value = Array.from({ length: template.value?.param_count ?? 0 }, () => '')
  headerMediaId.value = ''
  headerMediaFileName.value = ''
})

// menu de dados do contato: insere {{nome}}, {{telefone}} ou {{campo}} na variável
const { variables, load: loadFields } = useContactFields()
onMounted(loadFields)
const variableItems = (index: number) => [
  ...variables.value.map(v => ({
    label: v.label,
    suffix: `{{${v.key}}}`,
    onSelect: () => {
      const current = params.value[index]?.trim()
      params.value[index] = current ? `${current} {{${v.key}}}` : `{{${v.key}}}`
    }
  })),
  // só os do sistema: diz de onde vêm os campos personalizados
  ...(variables.value.length <= 3 ? [{ label: 'Campos personalizados: crie em Contatos > Campos ou ao mapear o CSV', disabled: true, class: 'whitespace-normal max-w-60' }] : [])
]

// preview ao vivo: variáveis preenchidas substituem os {{n}} do corpo
const preview = computed(() =>
  template.value?.body_text.replace(/\{\{(\d+)\}\}/g, (raw, i) => params.value[+i - 1]?.trim() || raw) ?? ''
)

// ---- cabeçalho de mídia: template exige uma imagem/vídeo/PDF a cada envio ----
const needsHeaderMedia = computed(() =>
  Boolean(template.value && ['IMAGE', 'VIDEO', 'DOCUMENT'].includes(template.value.header_format))
)
const headerMediaAccept: Record<string, string> = {
  IMAGE: 'image/jpeg,image/png',
  VIDEO: 'video/mp4,video/3gpp',
  DOCUMENT: '.pdf'
}
const headerMediaFileName = ref('')
const headerMediaFileInput = ref<HTMLInputElement | null>(null)
const uploadingHeaderMedia = ref(false)

async function onHeaderMediaPicked(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  uploadingHeaderMedia.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const { media_id } = await api<MediaUpload>(
      `/companies/${props.companyId}/accounts/${props.accountId}/media`,
      { method: 'POST', body: form }
    )
    headerMediaId.value = media_id
    headerMediaFileName.value = file.name
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha no upload do arquivo'), color: 'error' })
  } finally {
    uploadingHeaderMedia.value = false
    if (headerMediaFileInput.value) headerMediaFileInput.value.value = ''
  }
}
</script>

<template>
  <div class="space-y-3">
    <UFormField label="Template" required help="Aprovados pela Meta - gerencie em Templates">
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
        v-if="needsHeaderMedia"
        label="Arquivo do cabeçalho"
        :required="!template.has_header_media"
        :help="template.has_header_media ? 'Já tem uma imagem salva pra esse template - troque só se quiser usar outra' : 'Esse template exige uma imagem, vídeo ou PDF'"
      >
        <input
          ref="headerMediaFileInput"
          type="file"
          :accept="headerMediaAccept[template.header_format]"
          class="hidden"
          @change="onHeaderMediaPicked"
        >
        <div class="flex items-center gap-2">
          <UButton
            :label="headerMediaFileName ? 'Trocar arquivo' : (template.has_header_media ? 'Trocar arquivo (opcional)' : 'Enviar arquivo')"
            icon="i-lucide-upload"
            variant="soft"
            size="sm"
            :loading="uploadingHeaderMedia"
            @click="headerMediaFileInput?.click()"
          />
          <span v-if="headerMediaFileName" class="text-sm text-muted truncate">{{ headerMediaFileName }}</span>
        </div>
      </UFormField>

      <UFormField
        v-for="i in template.param_count"
        :key="i"
        :label="`Variável {{${i}}}`"
        required
        :help="i === 1 ? 'Texto fixo ou um dado do contato (botão ao lado), preenchido em cada envio' : undefined"
      >
        <UInput v-model="params[i - 1]" class="w-full" :placeholder="`Valor de {{${i}}}`" :ui="{ trailing: 'pe-1' }">
          <template #trailing>
            <UDropdownMenu :items="variableItems(i - 1)" :content="{ align: 'end' }">
              <UButton icon="i-lucide-braces" variant="link" color="neutral" size="xs" aria-label="Inserir dado do contato" />
            </UDropdownMenu>
          </template>
        </UInput>
      </UFormField>
    </template>
  </div>
</template>
