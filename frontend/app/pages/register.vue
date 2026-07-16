<script setup lang="ts">
import { z } from 'zod'

definePageMeta({ layout: false })

const { register } = useAuth()
const toast = useToast()

const schema = z.object({
  name: z.string().min(1, 'Informe seu nome'),
  email: z.string().email('Email inválido'),
  password: z.string().min(8, 'Mínimo 8 caracteres'),
  repeatPassword: z.string()
}).refine(data => data.password === data.repeatPassword, {
  message: 'As senhas não conferem',
  path: ['repeatPassword']
})

const state = reactive({ name: '', email: '', password: '', repeatPassword: '' })
const loading = ref(false)
const hasInvite = !!localStorage.getItem('chatly_invite')

async function onSubmit() {
  loading.value = true
  try {
    await register(state.email, state.password, state.repeatPassword, state.name)
    await navigateTo('/')
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível criar a conta'), color: 'error' })
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

        <h2 class="font-display font-bold text-2xl tracking-tight mb-1">Criar conta</h2>
        <p class="text-sm text-muted mb-8">Comece grátis em menos de um minuto.</p>

        <UAlert
          v-if="hasInvite"
          icon="i-lucide-mail-open"
          color="primary"
          variant="subtle"
          title="Você foi convidado pra uma empresa"
          description="Crie sua conta e o convite será aceito automaticamente."
          class="mb-6"
        />

        <UForm :schema="schema" :state="state" class="space-y-5" @submit="onSubmit">
          <UFormField name="name" label="Nome" required>
            <UInput v-model="state.name" size="lg" class="w-full" />
          </UFormField>

          <UFormField name="email" label="Email" required>
            <UInput v-model="state.email" type="email" size="lg" class="w-full" />
          </UFormField>

          <UFormField name="password" label="Senha" required>
            <UInput v-model="state.password" type="password" size="lg" class="w-full" />
          </UFormField>

          <UFormField name="repeatPassword" label="Repetir senha" required>
            <UInput v-model="state.repeatPassword" type="password" size="lg" class="w-full" />
          </UFormField>

          <UButton type="submit" block size="lg" :loading="loading">Criar conta</UButton>
        </UForm>

        <p class="text-sm text-muted text-center mt-6">
          Já tem conta?
          <NuxtLink to="/login" class="text-primary font-medium">Entrar</NuxtLink>
        </p>
      </div>
    </div>
  </div>
</template>
