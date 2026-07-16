<script setup lang="ts">
const { companies, create } = useCompany()
const toast = useToast()

// convite pendente (aberto deslogado) vem antes de tudo - senão convidado cai no "crie sua empresa"
const pendingInvite = localStorage.getItem('chatly_invite')
if (pendingInvite) {
  localStorage.removeItem('chatly_invite')
  await navigateTo(`/convite/${pendingInvite}`, { replace: true })
}

// company carregada pelo layout; com company → direto pras conversas
if (companies.value.length > 0) {
  await navigateTo('/conversas', { replace: true })
}

const name = ref('')
const loading = ref(false)

async function onCreate() {
  if (!name.value.trim()) return
  loading.value = true
  try {
    await create(name.value.trim())
    await navigateTo('/conversas')
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <UDashboardPanel id="onboarding">
    <template #body>
      <div class="flex-1 flex items-center justify-center">
        <UCard class="w-full max-w-md">
          <h2 class="font-display text-lg font-bold mb-1">Bem-vindo ao Chatly 👋</h2>
          <p class="text-sm text-muted mb-4">
            Crie sua empresa pra começar a configurar agentes e conectar o WhatsApp.
          </p>
          <form class="flex gap-2" @submit.prevent="onCreate">
            <UInput v-model="name" placeholder="Nome da empresa" class="flex-1" />
            <UButton type="submit" :loading="loading" :disabled="!name.trim()">Criar</UButton>
          </form>
        </UCard>
      </div>
    </template>
  </UDashboardPanel>
</template>
