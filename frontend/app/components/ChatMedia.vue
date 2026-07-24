<script setup lang="ts">
import type { Message, EvolutionMessage } from '~/types/api'

// canal oficial (Message) e pessoal (EvolutionMessage) compartilham o mesmo render.
// proxy=true (oficial): sem media_url busca os bytes na Meta via rota /messages/{id}/media.
// proxy=false (Evolution): mídia é local; sem media_url = download falhou -> mostra indisponível
// (não tentar o proxy oficial, que serviria a mídia errada por colisão de PK entre canais).
const props = withDefaults(defineProps<{ message: Message | EvolutionMessage, companyId: number, proxy?: boolean }>(), { proxy: true })

const config = useRuntimeConfig()
const api = useApi()

// enviada (media_url): arquivo local do backend, servido direto (URL com uuid);
// recebida (sem media_url): proxy autenticado baixa da Meta → blob em memória
const src = ref<string | null>(null)
const failed = ref(false)

onMounted(async () => {
  if (props.message.media_url) {
    // storage S3/MinIO devolve URL absoluta; relativa só se storage local
    const url = props.message.media_url
    src.value = url.startsWith('http')
      ? url
      : `${config.public.apiBase.replace(/\/api\/?$/, '')}/${url.replace(/^\//, '')}`
    return
  }
  if (!props.proxy) {
    failed.value = true // canal pessoal sem media_url = download da mídia falhou
    return
  }
  try {
    const blob = await api<Blob>(
      `/companies/${props.companyId}/messages/${props.message.id}/media`,
      { responseType: 'blob' } as never
    )
    blobType.value = blob.type
    src.value = URL.createObjectURL(blob)
  } catch {
    failed.value = true // media_id expirado na Meta (~30 dias) ou download falhou
  }
})

onUnmounted(() => {
  if (src.value?.startsWith('blob:')) URL.revokeObjectURL(src.value)
})

const documentLabel = computed(() => {
  const match = props.message.content.match(/^\[documento: (.+)\]$/)
  return match?.[1] || 'Documento'
})

// ponytail: preview inline só pra PDF (render nativo do browser via iframe);
// outros formatos (docx etc.) precisariam de conversão no backend - segue link
const blobType = ref('')
const isPdf = computed(() =>
  blobType.value === 'application/pdf' || /\.pdf($|\?)/i.test(props.message.media_url ?? '')
)
</script>

<template>
  <p v-if="failed" class="text-xs text-muted italic flex items-center gap-1 py-1">
    <UIcon name="i-lucide-image-off" class="size-4 shrink-0" />
    Mídia indisponível (expirada)
  </p>
  <USkeleton v-else-if="!src" class="h-32 w-48 rounded-lg" />
  <img
    v-else-if="message.message_type === 'sticker'"
    :src="src"
    class="size-32 object-contain"
    alt="Figurinha"
  >
  <UModal v-else-if="message.message_type === 'image'">
    <button type="button" class="block cursor-zoom-in">
      <img :src="src" class="rounded-lg max-h-64 max-w-full" :alt="message.content">
    </button>
    <template #content>
      <img :src="src" class="max-h-[85vh] w-full object-contain" :alt="message.content">
    </template>
  </UModal>
  <audio v-else-if="message.message_type === 'audio'" :src="src" controls class="max-w-full" />
  <video v-else-if="message.message_type === 'video'" :src="src" controls class="rounded-lg max-h-64 max-w-full" />
  <UModal v-else-if="isPdf" :ui="{ content: 'max-w-4xl' }">
    <UButton icon="i-lucide-file-text" variant="soft" color="neutral" size="sm" :label="documentLabel" />
    <template #content>
      <iframe :src="src" class="w-full h-[85vh] rounded-lg" :title="documentLabel" />
    </template>
  </UModal>
  <UButton
    v-else
    :href="src"
    target="_blank"
    icon="i-lucide-file-text"
    variant="soft"
    color="neutral"
    size="sm"
    :label="documentLabel"
  />
</template>
