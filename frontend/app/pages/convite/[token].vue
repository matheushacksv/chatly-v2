<script setup lang="ts">
import type { Company } from '~/types/api'

// ponytail: sem redirect-back no login - quem cai deslogado loga e reabre o link (veio por WhatsApp, continua na mão)
definePageMeta({ layout: false })

const api = useApi()
const route = useRoute()
const toast = useToast()
const { load, setActive } = useCompany()

const failed = ref(false)

onMounted(async () => {
  try {
    const company = await api<Company>(`/companies/invites/${route.params.token}/accept`, { method: 'POST' })
    await load()
    setActive(company.id)
    toast.add({ title: `Você entrou em ${company.name}`, icon: 'i-lucide-party-popper' })
    await navigateTo('/conversas')
  } catch {
    failed.value = true
  }
})
</script>

<template>
  <div class="min-h-screen flex flex-col items-center justify-center gap-4 p-6 text-center">
    <template v-if="!failed">
      <UIcon name="i-lucide-loader-circle" class="size-8 animate-spin text-primary" />
      <p class="text-sm text-muted">Entrando na empresa...</p>
    </template>
    <template v-else>
      <UIcon name="i-lucide-link-2-off" class="size-8 text-error" />
      <p class="font-display font-semibold text-lg">Convite inválido ou expirado</p>
      <p class="text-sm text-muted">Convites valem 7 dias e só podem ser usados uma vez. Peça um novo link pra quem te convidou.</p>
      <UButton label="Ir para o início" to="/" variant="soft" />
    </template>
  </div>
</template>
