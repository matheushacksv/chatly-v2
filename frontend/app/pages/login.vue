<script setup lang="ts">
import { z } from 'zod'

definePageMeta({ layout: false })

const { login } = useAuth()
const toast = useToast()

const schema = z.object({
  email: z.string().email('Email inválido'),
  password: z.string().min(1, 'Informe a senha')
})

const state = reactive({ email: '', password: '' })
const loading = ref(false)
const hasInvite = !!localStorage.getItem('chatly_invite')

async function onSubmit() {
  loading.value = true
  try {
    await login(state.email, state.password)
    await navigateTo('/')
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Email ou senha inválidos'), color: 'error' })
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen grid lg:grid-cols-2">
    <BrandPanel />

    <div class="flex items-center justify-center p-6">
      <div class="w-full max-w-sm">
        <div class="lg:hidden flex items-center gap-2 mb-8">
          <UIcon name="i-lucide-message-circle-heart" class="size-7 text-primary" />
          <span class="font-display font-bold text-xl tracking-tight">Chatly</span>
        </div>

        <h2 class="font-display font-bold text-2xl tracking-tight mb-1">Bem-vindo de volta</h2>
        <p class="text-sm text-muted mb-8">Entre pra acompanhar suas conversas.</p>

        <UAlert
          v-if="hasInvite"
          icon="i-lucide-mail-open"
          color="primary"
          variant="subtle"
          title="Você foi convidado pra uma empresa"
          description="Entre (ou crie sua conta) e o convite será aceito automaticamente."
          class="mb-6"
        />

        <UForm :schema="schema" :state="state" class="space-y-5" @submit="onSubmit">
          <UFormField name="email" label="Email" required>
            <UInput v-model="state.email" type="email" placeholder="voce@empresa.com" size="lg" class="w-full" />
          </UFormField>

          <UFormField name="password" label="Senha" required>
            <UInput v-model="state.password" type="password" size="lg" class="w-full" />
          </UFormField>

          <UButton type="submit" block size="lg" :loading="loading">Entrar</UButton>
        </UForm>

        <p class="text-sm text-muted text-center mt-6">
          Não tem conta?
          <NuxtLink to="/register" class="text-primary font-medium">Criar conta</NuxtLink>
        </p>
      </div>
    </div>
  </div>
</template>
