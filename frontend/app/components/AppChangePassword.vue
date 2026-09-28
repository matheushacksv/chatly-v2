<script setup lang="ts">
import { z } from 'zod'

// Troca de senha do próprio usuário (qualquer papel). Aberto pelo menu do usuário no layout.
const open = defineModel<boolean>('open', { default: false })

const api = useApi()
const toast = useToast()

const schema = z.object({
  current_password: z.string().min(1, 'Informe a senha atual'),
  new_password: z.string().min(8, 'Mínimo de 8 caracteres'),
  repeat_password: z.string()
}).refine(data => data.new_password === data.repeat_password, {
  message: 'As senhas não conferem',
  path: ['repeat_password']
})

const state = reactive({ current_password: '', new_password: '', repeat_password: '' })
const saving = ref(false)
const show = ref(false)

// fechou: não deixa senha digitada pra trás no estado
watch(open, (value) => {
  if (!value) Object.assign(state, { current_password: '', new_password: '', repeat_password: '' })
})

async function save() {
  saving.value = true
  try {
    await api('/auth/change-password', { method: 'POST', body: { ...state } })
    toast.add({ title: 'Senha alterada', icon: 'i-lucide-check', color: 'success' })
    open.value = false
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível alterar a senha'), color: 'error' })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <UModal
    v-model:open="open"
    title="Trocar senha"
    description="Depois de trocar, use a nova senha no próximo login."
  >
    <template #body>
      <UForm
        :schema="schema"
        :state="state"
        class="space-y-4"
        @submit="save"
      >
        <UFormField
          name="current_password"
          label="Senha atual"
          required
        >
          <UInput
            v-model="state.current_password"
            :type="show ? 'text' : 'password'"
            autocomplete="current-password"
            class="w-full"
            autofocus
          />
        </UFormField>
        <UFormField
          name="new_password"
          label="Nova senha"
          required
          help="Mínimo de 8 caracteres, não pode ser só números nem muito comum"
        >
          <UInput
            v-model="state.new_password"
            :type="show ? 'text' : 'password'"
            autocomplete="new-password"
            class="w-full"
          />
        </UFormField>
        <UFormField
          name="repeat_password"
          label="Repita a nova senha"
          required
        >
          <UInput
            v-model="state.repeat_password"
            :type="show ? 'text' : 'password'"
            autocomplete="new-password"
            class="w-full"
          />
        </UFormField>
        <UCheckbox
          v-model="show"
          label="Mostrar senhas"
        />
        <UButton
          type="submit"
          block
          label="Salvar nova senha"
          icon="i-lucide-key-round"
          :loading="saving"
        />
      </UForm>
    </template>
  </UModal>
</template>
