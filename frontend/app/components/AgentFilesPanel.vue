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

// janela própria: escolher, nomear e enviar num lugar só (o "Salvar" do agente não sobe arquivo)
const formOpen = ref(false)
const form = reactive({ name: '', description: '' })
const picked = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)

function openForm() {
  Object.assign(form, { name: '', description: '' })
  picked.value = null
  formOpen.value = true
}

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
    formOpen.value = false
    toast.add({ title: 'Arquivo salvo', color: 'success' })
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
    <UButton
      v-if="isOwner"
      label="Novo arquivo"
      icon="i-lucide-plus"
      variant="soft"
      @click="openForm"
    />

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

    <UModal v-model:open="formOpen" title="Novo arquivo">
      <template #body>
        <form class="space-y-4" @submit.prevent="upload">
          <input
            ref="fileInput"
            type="file"
            accept="application/pdf,image/jpeg,image/png,video/mp4,video/3gpp,audio/*,.doc,.docx,.xls,.xlsx,.ppt,.pptx"
            class="hidden"
            @change="onPick"
          >
          <UFormField label="Arquivo" required>
            <UButton
              :label="picked ? picked.name : 'Escolher no computador'"
              :icon="picked ? 'i-lucide-file-check' : 'i-lucide-paperclip'"
              variant="outline"
              color="neutral"
              block
              class="justify-start"
              @click="fileInput?.click()"
            />
          </UFormField>
          <UFormField label="Nome" required help="É por esse nome que a IA escolhe o arquivo e que você cita no prompt">
            <UInput v-model="form.name" placeholder="Proposta sinC" class="w-full" />
          </UFormField>
          <UFormField label="Quando enviar">
            <UTextarea v-model="form.description" :rows="2" placeholder="Quando o cliente perguntar valor ou planos" class="w-full" />
          </UFormField>
          <div class="flex justify-end gap-2">
            <UButton label="Cancelar" variant="ghost" color="neutral" @click="() => { formOpen = false }" />
            <UButton type="submit" label="Salvar arquivo" icon="i-lucide-upload" :disabled="!picked || !form.name.trim()" :loading="uploading" />
          </div>
        </form>
      </template>
    </UModal>
  </div>
</template>
