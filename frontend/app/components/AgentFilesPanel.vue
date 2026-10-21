<script setup lang="ts">
// Arquivos que a IA pode mandar na conversa (ferramenta nativa "Enviar arquivo"): ela escolhe pelo nome e pela descrição.
import type { AgentFile } from '~/types/api'

const props = defineProps<{ companyId: number, agentId: number, isOwner: boolean }>()

const api = useApi()
const toast = useToast()

const base = computed(() => `/companies/${props.companyId}/agents/${props.agentId}/files`)
const files = ref<AgentFile[]>([])
watch(base, async () => {
  files.value = await api<AgentFile[]>(base.value)
}, { immediate: true })

const form = reactive({ name: '', description: '' })
const picked = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)

function onPick(event: Event) {
  picked.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (picked.value && !form.name) form.name = picked.value.name.replace(/\.[^.]+$/, '')
}

async function upload() {
  if (!picked.value) return
  uploading.value = true
  try {
    const body = new FormData()
    body.append('file', picked.value)
    body.append('name', form.name)
    body.append('description', form.description)
    files.value.push(await api<AgentFile>(base.value, { method: 'POST', body }))
    Object.assign(form, { name: '', description: '' })
    picked.value = null
    if (fileInput.value) fileInput.value.value = ''
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha no upload'), color: 'error' })
  } finally {
    uploading.value = false
  }
}

async function remove(file: AgentFile) {
  try {
    await api(`${base.value}/${file.id}`, { method: 'DELETE' })
    files.value = files.value.filter(f => f.id !== file.id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

function icon(contentType: string) {
  if (contentType.startsWith('image/')) return 'i-lucide-image'
  if (contentType.startsWith('video/')) return 'i-lucide-video'
  if (contentType.startsWith('audio/')) return 'i-lucide-music'
  return 'i-lucide-file'
}
</script>

<template>
  <div class="space-y-3">
    <div v-if="files.length" class="space-y-2">
      <div v-for="file in files" :key="file.id" class="flex items-center gap-3 rounded-lg ring-1 ring-default px-3 py-2">
        <UIcon :name="icon(file.content_type)" class="size-4 text-muted shrink-0" />
        <a :href="file.url" target="_blank" class="min-w-0 flex-1">
          <p class="text-sm truncate">{{ file.name }}</p>
          <p v-if="file.description" class="text-xs text-muted truncate">{{ file.description }}</p>
        </a>
        <UButton
          v-if="isOwner"
          icon="i-lucide-x"
          variant="ghost"
          color="error"
          size="xs"
          :aria-label="`Remover ${file.name}`"
          @click="remove(file)"
        />
      </div>
    </div>
    <p v-else class="text-sm text-muted">
      Nenhum arquivo ainda.
    </p>

    <div v-if="isOwner" class="space-y-2 rounded-lg ring-1 ring-default p-3">
      <input
        ref="fileInput"
        type="file"
        accept="application/pdf,image/jpeg,image/png,video/mp4,video/3gpp,audio/*,.doc,.docx,.xls,.xlsx,.ppt,.pptx"
        class="hidden"
        @change="onPick"
      >
      <UButton
        :label="picked ? picked.name : 'Escolher arquivo'"
        icon="i-lucide-paperclip"
        variant="soft"
        color="neutral"
        block
        class="justify-start"
        @click="fileInput?.click()"
      />
      <UInput v-model="form.name" placeholder="Nome (ex.: Catálogo 2026)" class="w-full" />
      <UInput v-model="form.description" placeholder="Quando enviar (ex.: quando o cliente pedir os planos)" class="w-full" />
      <UButton
        label="Adicionar arquivo"
        icon="i-lucide-upload"
        :disabled="!picked"
        :loading="uploading"
        @click="upload"
      />
    </div>
  </div>
</template>
