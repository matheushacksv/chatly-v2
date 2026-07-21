<script setup lang="ts">
import type { TemplateButton, TemplateButtonType, TemplateCategory, TemplateDetail, TemplateHeaderFormat, TemplateInput, TemplateMediaHandle } from '~/types/api'

const props = defineProps<{ companyId: number, accountId: number, templateId: string | null }>()
const open = defineModel<boolean>('open', { default: false })
const emit = defineEmits<{ saved: [] }>()

const api = useApi()
const toast = useToast()

const editing = computed(() => Boolean(props.templateId))
const loading = ref(false)
const saving = ref(false)
const status = ref<string | null>(null)

const state = reactive({
  name: '',
  language: 'pt_BR',
  category: 'MARKETING' as TemplateCategory,
  headerFormat: 'NONE' as TemplateHeaderFormat,
  headerText: '',
  headerExample: '',
  headerHandle: '',
  headerMediaId: '',
  headerFileName: '',
  bodyText: '',
  bodyExamples: [] as string[],
  footerText: '',
  buttons: [] as TemplateButton[]
})
const hasSavedMedia = ref(false)

function reset() {
  Object.assign(state, {
    name: '',
    language: 'pt_BR',
    category: 'MARKETING',
    headerFormat: 'NONE',
    headerText: '',
    headerExample: '',
    headerHandle: '',
    headerMediaId: '',
    headerFileName: '',
    bodyText: '',
    bodyExamples: [],
    footerText: '',
    buttons: []
  })
  status.value = null
  hasSavedMedia.value = false
  revokeHeaderPreview()
}

watch(open, async (isOpen) => {
  if (!isOpen) return
  reset()
  if (!props.templateId) return
  loading.value = true
  try {
    const detail = await api<TemplateDetail>(
      `/companies/${props.companyId}/accounts/${props.accountId}/templates/${props.templateId}`
    )
    state.name = detail.name
    state.language = detail.language
    state.category = detail.category as TemplateCategory
    state.headerFormat = detail.header?.format ?? 'NONE'
    state.headerText = detail.header?.text ?? ''
    state.headerExample = detail.header?.example ?? ''
    // a Meta não devolve o handle de mídia de exemplo depois de criado - mas o Chatly guarda um media_id
    // reusável desde a criação, então só pede reenvio se não tiver nada salvo (hasSavedMedia)
    state.headerHandle = ''
    state.headerMediaId = ''
    state.headerFileName = ''
    hasSavedMedia.value = detail.has_header_media
    state.bodyText = detail.body_text
    state.bodyExamples = [...detail.body_examples]
    state.footerText = detail.footer_text
    state.buttons = detail.buttons.map(b => ({ ...b }))
    status.value = detail.status
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao carregar template'), color: 'error' })
    open.value = false
  } finally {
    loading.value = false
  }
})

// ---- body: detecta {{n}} e mantém um exemplo por variável ----
const bodyParamCount = computed(() => {
  const matches = [...state.bodyText.matchAll(/\{\{(\d+)\}\}/g)]
  return matches.reduce((max, m) => Math.max(max, Number(m[1])), 0)
})
watch(bodyParamCount, (count) => {
  state.bodyExamples = Array.from({ length: count }, (_, i) => state.bodyExamples[i] ?? '')
})

const bodyPreview = computed(() =>
  state.bodyText.replace(/\{\{(\d+)\}\}/g, (raw, i) => state.bodyExamples[Number(i) - 1]?.trim() || raw)
)

// ---- header: texto (só aceita {{1}}) ou mídia (upload gera um handle) ----
const headerHasParam = computed(() => /\{\{1\}\}/.test(state.headerText))
const headerPreview = computed(() =>
  state.headerText.replace(/\{\{1\}\}/g, () => state.headerExample.trim() || '{{1}}')
)
const mediaHeaderIcon: Record<string, string> = {
  IMAGE: 'i-lucide-image',
  VIDEO: 'i-lucide-video',
  DOCUMENT: 'i-lucide-file-text'
}
const buttonIcon: Record<TemplateButtonType, string> = {
  QUICK_REPLY: 'i-lucide-reply',
  URL: 'i-lucide-external-link',
  PHONE_NUMBER: 'i-lucide-phone'
}
const headerFormatItems: { label: string, value: TemplateHeaderFormat }[] = [
  { label: 'Sem cabeçalho', value: 'NONE' },
  { label: 'Texto', value: 'TEXT' },
  { label: 'Imagem', value: 'IMAGE' },
  { label: 'Vídeo', value: 'VIDEO' },
  { label: 'Documento', value: 'DOCUMENT' }
]

// códigos de idioma aceitos pela Meta pra templates
const languageItems = [
  { label: 'Africâner (af)', value: 'af' },
  { label: 'Albanês (sq)', value: 'sq' },
  { label: 'Alemão (de)', value: 'de' },
  { label: 'Árabe (ar)', value: 'ar' },
  { label: 'Azerbaijano (az)', value: 'az' },
  { label: 'Bengali (bn)', value: 'bn' },
  { label: 'Búlgaro (bg)', value: 'bg' },
  { label: 'Catalão (ca)', value: 'ca' },
  { label: 'Chinês (CHN) (zh_CN)', value: 'zh_CN' },
  { label: 'Chinês (HKG) (zh_HK)', value: 'zh_HK' },
  { label: 'Chinês (TAI) (zh_TW)', value: 'zh_TW' },
  { label: 'Coreano (ko)', value: 'ko' },
  { label: 'Croata (hr)', value: 'hr' },
  { label: 'Dinamarquês (da)', value: 'da' },
  { label: 'Eslovaco (sk)', value: 'sk' },
  { label: 'Esloveno (sl)', value: 'sl' },
  { label: 'Espanhol (es)', value: 'es' },
  { label: 'Espanhol (ARG) (es_AR)', value: 'es_AR' },
  { label: 'Espanhol (ESP) (es_ES)', value: 'es_ES' },
  { label: 'Espanhol (MEX) (es_MX)', value: 'es_MX' },
  { label: 'Estoniano (et)', value: 'et' },
  { label: 'Filipino (fil)', value: 'fil' },
  { label: 'Finlandês (fi)', value: 'fi' },
  { label: 'Francês (fr)', value: 'fr' },
  { label: 'Georgiano (ka)', value: 'ka' },
  { label: 'Grego (el)', value: 'el' },
  { label: 'Gujarati (gu)', value: 'gu' },
  { label: 'Hebraico (he)', value: 'he' },
  { label: 'Híndi (hi)', value: 'hi' },
  { label: 'Húngaro (hu)', value: 'hu' },
  { label: 'Indonésio (id)', value: 'id' },
  { label: 'Inglês (en)', value: 'en' },
  { label: 'Inglês (Reino Unido) (en_GB)', value: 'en_GB' },
  { label: 'Inglês (EUA) (en_US)', value: 'en_US' },
  { label: 'Irlandês (ga)', value: 'ga' },
  { label: 'Italiano (it)', value: 'it' },
  { label: 'Japonês (ja)', value: 'ja' },
  { label: 'Cazaque (kk)', value: 'kk' },
  { label: 'Canarês (kn)', value: 'kn' },
  { label: 'Laosiano (lo)', value: 'lo' },
  { label: 'Letão (lv)', value: 'lv' },
  { label: 'Lituano (lt)', value: 'lt' },
  { label: 'Macedônio (mk)', value: 'mk' },
  { label: 'Malaio (ms)', value: 'ms' },
  { label: 'Malaiala (ml)', value: 'ml' },
  { label: 'Marata (mr)', value: 'mr' },
  { label: 'Norueguês (nb)', value: 'nb' },
  { label: 'Holandês (nl)', value: 'nl' },
  { label: 'Persa (fa)', value: 'fa' },
  { label: 'Polonês (pl)', value: 'pl' },
  { label: 'Português (BR) (pt_BR)', value: 'pt_BR' },
  { label: 'Português (PT) (pt_PT)', value: 'pt_PT' },
  { label: 'Punjabi (pa)', value: 'pa' },
  { label: 'Romeno (ro)', value: 'ro' },
  { label: 'Russo (ru)', value: 'ru' },
  { label: 'Sérvio (sr)', value: 'sr' },
  { label: 'Suaíli (sw)', value: 'sw' },
  { label: 'Sueco (sv)', value: 'sv' },
  { label: 'Tâmil (ta)', value: 'ta' },
  { label: 'Telugu (te)', value: 'te' },
  { label: 'Tailandês (th)', value: 'th' },
  { label: 'Turco (tr)', value: 'tr' },
  { label: 'Ucraniano (uk)', value: 'uk' },
  { label: 'Urdu (ur)', value: 'ur' },
  { label: 'Usbeque (uz)', value: 'uz' },
  { label: 'Vietnamita (vi)', value: 'vi' },
  { label: 'Zulu (zu)', value: 'zu' }
]

const headerPreviewUrl = ref<string | null>(null)
function revokeHeaderPreview() {
  if (headerPreviewUrl.value) URL.revokeObjectURL(headerPreviewUrl.value)
  headerPreviewUrl.value = null
}
onUnmounted(revokeHeaderPreview)

// troca deliberada do usuário - não um watcher, pra não zerar os campos ao carregar um template existente
function onHeaderFormatChange(format: TemplateHeaderFormat) {
  state.headerFormat = format
  state.headerText = ''
  state.headerExample = ''
  state.headerHandle = ''
  state.headerMediaId = ''
  state.headerFileName = ''
  hasSavedMedia.value = false
  revokeHeaderPreview()
}

const headerAccept: Record<string, string> = {
  IMAGE: 'image/jpeg,image/png',
  VIDEO: 'video/mp4,video/3gpp',
  DOCUMENT: '.pdf'
}

const uploadingHeader = ref(false)
const headerFileInput = ref<HTMLInputElement | null>(null)

async function onHeaderFilePicked(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  revokeHeaderPreview()
  headerPreviewUrl.value = URL.createObjectURL(file)
  uploadingHeader.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const { handle, media_id } = await api<TemplateMediaHandle>(
      `/companies/${props.companyId}/accounts/${props.accountId}/templates/media`,
      { method: 'POST', body: form }
    )
    state.headerHandle = handle
    state.headerMediaId = media_id
    state.headerFileName = file.name
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha no upload do arquivo'), color: 'error' })
  } finally {
    uploadingHeader.value = false
    if (headerFileInput.value) headerFileInput.value.value = ''
  }
}

// ---- botões ----
const buttonTypeItems: { label: string, value: TemplateButtonType }[] = [
  { label: 'Resposta rápida', value: 'QUICK_REPLY' },
  { label: 'Link (URL)', value: 'URL' },
  { label: 'Telefone', value: 'PHONE_NUMBER' }
]

function addButton() {
  if (state.buttons.length >= 3) return
  state.buttons.push({ type: 'QUICK_REPLY', text: '', url: '', phone_number: '' })
}
function removeButton(index: number) {
  state.buttons.splice(index, 1)
}

// ---- validação ----
const nameValid = computed(() => /^[a-z0-9_]+$/.test(state.name))
const footerValid = computed(() => state.footerText.length <= 60)
const buttonsValid = computed(() =>
  state.buttons.every(b =>
    b.text.trim() && (b.type !== 'URL' || b.url.trim()) && (b.type !== 'PHONE_NUMBER' || b.phone_number.trim())
  )
)
const isMediaHeader = computed(() => ['IMAGE', 'VIDEO', 'DOCUMENT'].includes(state.headerFormat))
const headerValid = computed(() => {
  if (state.headerFormat === 'TEXT') return state.headerText.trim().length > 0
  if (isMediaHeader.value) return Boolean(state.headerHandle)
  return true
})
const canSubmit = computed(() =>
  nameValid.value
  && state.language.trim().length > 0
  && state.bodyText.trim().length > 0
  && state.bodyExamples.every(e => e.trim())
  && footerValid.value
  && buttonsValid.value
  && headerValid.value
)

const statusBadge: Record<string, { label: string, color: 'success' | 'warning' | 'error' | 'neutral' }> = {
  APPROVED: { label: 'Aprovado', color: 'success' },
  PENDING: { label: 'Em análise', color: 'warning' },
  REJECTED: { label: 'Rejeitado', color: 'error' },
  PAUSED: { label: 'Pausado', color: 'neutral' }
}

async function submit() {
  if (!canSubmit.value || saving.value) return
  saving.value = true
  try {
    const payload: TemplateInput = {
      name: state.name,
      language: state.language,
      category: state.category,
      header: state.headerFormat === 'NONE'
        ? null
        : state.headerFormat === 'TEXT'
          ? { format: 'TEXT', text: state.headerText, example: headerHasParam.value ? state.headerExample : '', handle: '', media_id: '' }
          : { format: state.headerFormat, text: '', example: '', handle: state.headerHandle, media_id: state.headerMediaId },
      body_text: state.bodyText,
      body_examples: state.bodyExamples,
      footer_text: state.footerText,
      buttons: state.buttons
    }
    if (editing.value) {
      await api(`/companies/${props.companyId}/accounts/${props.accountId}/templates/${props.templateId}`, {
        method: 'PATCH',
        body: payload
      })
      toast.add({ title: 'Template atualizado', color: 'success' })
    } else {
      await api(`/companies/${props.companyId}/accounts/${props.accountId}/templates`, {
        method: 'POST',
        body: payload
      })
      toast.add({ title: 'Template criado', description: 'Enviado pra aprovação da Meta.', color: 'success' })
    }
    open.value = false
    emit('saved')
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao salvar template'), color: 'error' })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <USlideover
    v-model:open="open"
    :title="editing ? 'Editar template' : 'Novo template'"
    :description="editing ? state.name : 'Fica pendente de aprovação da Meta após criar'"
    :ui="{ content: 'max-w-4xl' }"
  >
    <template #body>
      <div v-if="loading" class="flex justify-center py-8">
        <UIcon name="i-lucide-loader-circle" class="size-5 animate-spin text-muted" />
      </div>

      <div v-else class="flex flex-col md:flex-row gap-6">
        <!-- preview: bolha de chat, como o cliente vê no WhatsApp -->
        <div class="w-full md:w-64 shrink-0 md:sticky md:top-0 self-start">
          <p class="text-xs font-medium text-muted mb-2">Pré-visualização</p>
          <div class="rounded-2xl bg-elevated ring-1 ring-default p-3 min-h-48 flex flex-col justify-end">
            <div class="rounded-xl rounded-br-sm bg-primary/10 ring-1 ring-primary/20 px-3 py-2 text-sm">
              <div
                v-if="isMediaHeader"
                class="rounded-lg bg-default ring-1 ring-default aspect-video overflow-hidden flex items-center justify-center mb-1.5"
              >
                <img
                  v-if="headerPreviewUrl && state.headerFormat === 'IMAGE'"
                  :src="headerPreviewUrl"
                  class="w-full h-full object-cover"
                >
                <video
                  v-else-if="headerPreviewUrl && state.headerFormat === 'VIDEO'"
                  :src="headerPreviewUrl"
                  class="w-full h-full object-cover"
                  muted
                />
                <div v-else-if="headerPreviewUrl && state.headerFormat === 'DOCUMENT'" class="flex flex-col items-center gap-1 text-muted">
                  <UIcon name="i-lucide-file-text" class="size-6" />
                  <span class="text-[10px] truncate max-w-full px-2">{{ state.headerFileName }}</span>
                </div>
                <UIcon v-else :name="mediaHeaderIcon[state.headerFormat]" class="size-6 text-muted" />
              </div>
              <p v-if="state.headerFormat === 'TEXT' && headerPreview" class="font-semibold mb-1 break-words">
                {{ headerPreview }}
              </p>
              <p v-if="bodyPreview" class="whitespace-pre-wrap break-words">{{ bodyPreview }}</p>
              <p v-else class="text-muted italic">Corpo da mensagem</p>
              <p v-if="state.footerText" class="text-xs text-muted mt-1 break-words">{{ state.footerText }}</p>
              <p class="text-[10px] text-muted text-right mt-1">12:00</p>
            </div>
            <div v-if="state.buttons.length" class="mt-1 space-y-1">
              <div
                v-for="(button, index) in state.buttons"
                :key="index"
                class="rounded-lg bg-primary/10 ring-1 ring-primary/20 py-1.5 text-center text-xs text-primary flex items-center justify-center gap-1.5"
              >
                <UIcon :name="buttonIcon[button.type]" class="size-3.5" />
                {{ button.text || 'Botão' }}
              </div>
            </div>
          </div>
        </div>

        <div class="flex-1 min-w-0 space-y-5">
          <UBadge v-if="status" :color="statusBadge[status]?.color ?? 'neutral'" variant="subtle">
            {{ statusBadge[status]?.label ?? status }}
          </UBadge>
          <UAlert
            v-if="editing"
            icon="i-lucide-info"
            variant="subtle"
            color="neutral"
            description="A Meta limita edições (rate limit) e nome/idioma não podem mudar depois de criado."
          />

        <div class="grid grid-cols-2 gap-4">
          <UFormField label="Nome" required help="minúsculas, números e _">
            <UInput v-model="state.name" :disabled="editing" placeholder="boas_vindas" class="w-full" />
          </UFormField>
<<<<<<< HEAD
          <UFormField label="Idioma" required>
            <USelectMenu
              v-model="state.language"
              :disabled="editing"
              :items="languageItems"
              value-key="value"
              placeholder="Escolha o idioma"
              class="w-full"
            />
=======
          <UFormField label="Idioma" required help="código da Meta">
            <UInput v-model="state.language" :disabled="editing" placeholder="pt_BR" class="w-full" />
>>>>>>> origin/main
          </UFormField>
        </div>
        <p v-if="state.name && !nameValid" class="text-xs text-error -mt-3">
          Só letras minúsculas, números e underscore.
        </p>

<<<<<<< HEAD
        <UFormField label="Categoria" required :help="state.category === 'MARKETING' ? 'Fora da janela de 24h, entrega pra contatos sem interação recente pode não acontecer. Pra conteúdo transacional prefira Utilidade.' : undefined">
=======
        <UFormField label="Categoria" required>
>>>>>>> origin/main
          <URadioGroup
            v-model="state.category"
            orientation="horizontal"
            :items="[{ label: 'Marketing', value: 'MARKETING' }, { label: 'Utilidade', value: 'UTILITY' }]"
          />
        </UFormField>

        <USeparator label="Cabeçalho" />
        <UFormField>
          <URadioGroup
            :model-value="state.headerFormat"
            orientation="horizontal"
            :items="headerFormatItems"
            @update:model-value="onHeaderFormatChange($event as TemplateHeaderFormat)"
          />
        </UFormField>
        <template v-if="state.headerFormat === 'TEXT'">
          <UFormField label="Texto do cabeçalho" required help="aceita no máximo uma variável {{1}}">
            <UInput v-model="state.headerText" placeholder="Olá {{1}}!" class="w-full" />
          </UFormField>
          <UFormField v-if="headerHasParam" label="Exemplo de {{1}}" required>
            <UInput v-model="state.headerExample" placeholder="Cliente" class="w-full" />
          </UFormField>
        </template>
        <template v-if="isMediaHeader">
          <UAlert
            v-if="editing && !state.headerHandle && hasSavedMedia"
            icon="i-lucide-info"
            variant="subtle"
            color="neutral"
            description="Já existe uma imagem salva sendo usada nos envios. A Meta exige reenviar o arquivo pra editar o template - se reenviar, a nova imagem passa a valer pros envios também."
          />
          <UAlert
            v-else-if="editing && !state.headerHandle"
            icon="i-lucide-triangle-alert"
            variant="subtle"
            color="warning"
            description="A Meta exige reenviar o arquivo de exemplo a cada edição. Essa mesma imagem passa a ser usada automaticamente nos envios, sem precisar escolher de novo."
          />
          <input ref="headerFileInput" type="file" :accept="headerAccept[state.headerFormat]" class="hidden" @change="onHeaderFilePicked">
          <div class="flex items-center gap-2">
            <UButton
              :label="state.headerFileName ? 'Trocar arquivo' : 'Enviar arquivo de exemplo'"
              icon="i-lucide-upload"
              variant="soft"
              size="sm"
              :loading="uploadingHeader"
              @click="headerFileInput?.click()"
            />
            <span v-if="state.headerFileName" class="text-sm text-muted truncate">{{ state.headerFileName }}</span>
          </div>
        </template>

        <USeparator label="Corpo" />
        <UFormField label="Mensagem" required help="use {{1}}, {{2}}... pra variáveis">
          <UTextarea v-model="state.bodyText" :rows="5" class="w-full" placeholder="Oi {{1}}, temos uma oferta pra você." />
        </UFormField>
        <UFormField
          v-for="i in bodyParamCount"
          :key="i"
          :label="`Exemplo de {{${i}}}`"
          required
        >
          <UInput v-model="state.bodyExamples[i - 1]" class="w-full" :placeholder="`Valor de {{${i}}}`" />
        </UFormField>

        <USeparator label="Rodapé" />
        <UFormField :help="`${state.footerText.length}/60`">
          <UInput v-model="state.footerText" placeholder="Chatly" class="w-full" />
        </UFormField>

        <USeparator label="Botões" />
        <div v-if="state.buttons.length" class="space-y-3">
          <div v-for="(button, index) in state.buttons" :key="index" class="rounded-lg ring-1 ring-default p-3 space-y-2">
            <div class="flex items-center gap-2">
              <USelect v-model="button.type" :items="buttonTypeItems" class="flex-1" />
              <UButton icon="i-lucide-x" variant="ghost" color="error" size="xs" @click="removeButton(index)" />
            </div>
            <UInput v-model="button.text" placeholder="Texto do botão" class="w-full" />
            <UInput v-if="button.type === 'URL'" v-model="button.url" placeholder="https://..." class="w-full" />
            <UInput v-if="button.type === 'PHONE_NUMBER'" v-model="button.phone_number" placeholder="+5511999999999" class="w-full" />
          </div>
        </div>
        <UButton
          v-if="state.buttons.length < 3"
          label="Adicionar botão"
          icon="i-lucide-plus"
          variant="soft"
          size="sm"
          @click="addButton()"
        />

          <UButton block :loading="saving" :disabled="!canSubmit" @click="submit()">
            {{ editing ? 'Salvar alterações' : 'Criar template' }}
          </UButton>
        </div>
      </div>
    </template>
  </USlideover>
</template>
