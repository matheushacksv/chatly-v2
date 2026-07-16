<template>
  <div class="brand-panel hidden lg:flex flex-col justify-between p-12 text-white">
    <div class="flex items-center gap-2.5">
      <UIcon name="i-lucide-message-circle-heart" class="size-7 text-emerald-400" />
      <span class="font-display font-bold text-2xl tracking-tight">Chatly</span>
    </div>

    <div class="space-y-8 max-w-md">
      <h1 class="font-display font-bold text-4xl leading-tight tracking-tight">
        Seu WhatsApp responde sozinho.<br>
        <span class="text-emerald-400">Você assume quando importa.</span>
      </h1>

      <!-- conversa demo: cliente → IA → handoff -->
      <div class="space-y-3 text-sm">
        <div class="flex">
          <div class="bg-white/10 backdrop-blur rounded-xl rounded-bl-sm px-3.5 py-2.5 max-w-[80%]">
            Oi, quero falar com uma pessoa
          </div>
        </div>
        <div class="flex justify-end">
          <div class="bg-emerald-500/25 backdrop-blur rounded-xl rounded-br-sm px-3.5 py-2.5 max-w-[80%]">
            <p class="text-[10px] font-semibold text-emerald-300 mb-0.5 flex items-center gap-1">
              <UIcon name="i-lucide-sparkles" class="size-3" /> IA
            </p>
            Claro! Transferindo você para um atendente 🤝
          </div>
        </div>
        <div class="flex justify-end">
          <div class="bg-sky-500/25 backdrop-blur rounded-xl rounded-br-sm px-3.5 py-2.5 max-w-[80%]">
            <p class="text-[10px] font-semibold text-sky-300 mb-0.5 flex items-center gap-1">
              <UIcon name="i-lucide-headset" class="size-3" /> Atendente
            </p>
            Olá! Pode deixar comigo 👋
          </div>
        </div>
      </div>
    </div>

    <p class="text-xs text-white/40">Atendimento com IA no WhatsApp, com handoff humano.</p>
  </div>
</template>
