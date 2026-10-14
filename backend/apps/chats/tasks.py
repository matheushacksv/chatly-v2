import time

import httpx
from celery import shared_task
from django.db.models import F
from django.utils import timezone

from apps import ai
from apps.agents import services as agents_services
from apps.chats import events, services, whatsapp
from apps.chats.models import Campaign, Contact, ContactField, Message
from apps.crm import pipedrive
from apps.crm import services as crm_services
from apps.crm.models import PipedriveAccount
from core.exceptions import ValidationError


@shared_task(bind=True, max_retries=3)
def process_incoming_message(self, message_id: int, deferred: bool = False, pending: list[str] | None = None, handed_off: bool = False):
    message = Message.objects.select_related('contact__account__agent__credential').filter(pk=message_id).first()
    if message is None:
        return
    contact = message.contact
    account = contact.account
    if pending:
        # retry de envio parcial: termina a resposta que já começou a sair, sem gerar outra
        _send_parts(self, message_id, contact, pending, deferred, handed_off)
        update_contact_memory.delay(contact.pk)
        return
    try:
        if message.message_type == 'audio' and not message.content:
            audio_bytes, audio_mime = whatsapp.download_media(account, message.media_id)
            message.content = ai.transcribe_audio(audio_bytes, audio_mime)
            message.save(update_fields=['content'])

        from apps.automations import services as flow_services

        # adiada pelo horário da IA: o fluxo já viu essa mensagem
        if not deferred and flow_services.dispatch_incoming(message):
            return

        if contact.ai_paused and contact.ai_resume_at and contact.ai_resume_at <= timezone.now():
            services.resume_ai(contact)
        if contact.ai_paused:
            return
        if Message.objects.filter(contact=contact, direction='in', pk__gt=message.pk).exists():
            return
        # task entregue de novo depois de responder (o retry de envio parcial vem com `pending`)
        if self.request.retries and Message.objects.filter(contact=contact, direction='out', from_human=False, pk__gt=message.pk).exists():
            return
        # esperou o horário da IA e alguém já respondeu nesse meio-tempo
        if deferred and Message.objects.filter(contact=contact, direction='out', pk__gt=message.pk).exists():
            return
        opens_at = agents_services.next_ai_opening(account.agent, timezone.now())
        if opens_at:
            process_incoming_message.apply_async(args=(message.pk,), kwargs={'deferred': True}, eta=opens_at)
            return
        history = services.build_history(contact, exclude_pk=message.pk)
        image, mime_type = None, ''
        if message.message_type == 'image':
            image, mime_type = whatsapp.download_media(account, message.media_id)

        handed_off = False

        def transferir_para_atendente() -> str:
            """Transfere a conversa para um humano. Use quando o cliente pedir para falar com uma pessoa
            ou quando você não conseguir resolver"""
            nonlocal handed_off
            handed_off = True
            services.pause_ai(contact)
            events.publish(contact.account.company_id, {'type': 'handoff', 'contact_id': contact.pk, 'name': contact.name or contact.wa_id})
            return 'Transferido'

        tools = [transferir_para_atendente]
        pd = PipedriveAccount.objects.filter(company=account.company).first()
        if pd:

            def criar_negocio(titulo: str) -> str:
                """Cria negócio (deal) no CRM para este cliente. Use quando você concluir seu objetivo
                O título deve ser o nome"""
                try:
                    deal = crm_services.create_deal_for_contact(pd, contact, titulo)
                    return f'Negócio criado no CRM (id {deal["id"]})'
                except Exception as exc:
                    return f'Falha ao criar negócio: {exc}'

            def anotar_crm(anotacao: str) -> str:
                """Salva uma anotação sobre este cliente no CRM. Use para registrar fatos importantes para a equipe de vendas:
                orçamento, objeções, quem decide e etc"""
                try:
                    person_id = crm_services.find_or_create_person(pd, contact)
                    pipedrive.create_note(pd.api_key, anotacao, person_id=person_id)
                    return 'Anotação salva no CRM'
                except Exception as exc:
                    return f'Falha ao anotar no CRM: {exc}'

            tools += [criar_negocio, anotar_crm]

        reply = ai.generate_reply(account.agent, history, message.content, image=image, mime_type=mime_type, memory=_memory_with_fields(contact), tools=tools)
        parts = [p.strip() for p in reply.split('\n\n') if p.strip()] if account.agent.split_messages else [reply]
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2**self.request.retries)
    _send_parts(self, message_id, contact, parts, deferred, handed_off)
    update_contact_memory.delay(contact.pk)


def _send_parts(task, message_id: int, contact: Contact, parts: list[str], deferred: bool, handed_off: bool) -> None:
    for i, part in enumerate(parts):
        if i:
            time.sleep(min(1 + len(part) / 25, 5))
        # atendente (ou handoff de fluxo) pode ter pausado durante a geração; a pausa da própria
        # ferramenta de transferência não conta, a despedida dela precisa sair
        contact.refresh_from_db(fields=['ai_paused'])
        if contact.ai_paused and not handed_off:
            return
        try:
            wamid = whatsapp.send_text(contact.account, contact.wa_id, part)
        except Exception as exc:
            # o retry leva só o que falta: o que já saiu não repete e o resto não se perde
            pending = {'deferred': deferred, 'pending': parts[i:], 'handed_off': handed_off}
            raise task.retry(exc=exc, countdown=2**task.request.retries, args=(message_id,), kwargs=pending)
        services.record_outgoing_message(contact, wamid, part)


@shared_task(bind=True, max_retries=3)
def update_contact_memory(self, contact_id: int):
    contact = Contact.objects.select_related('account__agent__credential').filter(pk=contact_id).first()
    if contact is None:
        return
    try:
        history = services.build_history(contact)
        contact.memory = ai.update_memory(contact.account.agent, contact.memory, history)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2**self.request.retries)
    contact.save(update_fields=['memory'])


def _memory_with_fields(contact: Contact) -> str:
    """Memória da IA + campos personalizados preenchidos, pra IA saber o que já está cadastrado."""
    labels = dict(ContactField.objects.filter(company_id=contact.account.company_id).values_list('key', 'label'))
    data = '\n'.join(f'{labels[k]}: {v}' for k, v in contact.custom_fields.items() if k in labels)
    return '\n\n'.join(filter(None, [contact.memory, data and f'Dados cadastrados:\n{data}']))


@shared_task
def run_campaign(campaign_id: int, contact_ids: list[int]):
    campaign = Campaign.objects.select_related('account').filter(pk=campaign_id).first()
    if campaign is None:
        return
    try:
        body, _ = services.get_template_send_info(campaign.account, campaign.template_name, campaign.template_language)
    except (ValidationError, httpx.HTTPStatusError):
        Campaign.objects.filter(pk=campaign_id).update(status='failed')
        return
    for contact in Contact.objects.filter(pk__in=contact_ids):
        try:
            params = services.resolve_params(campaign.body_params, contact)
            wamid = whatsapp.send_template(
                campaign.account,
                contact.wa_id,
                campaign.template_name,
                campaign.template_language,
                params,
                campaign.header_media_id,
                campaign.header_media_type,
            )
        except (httpx.HTTPStatusError, ValidationError):  # ValidationError: contato sem valor num campo usado
            Campaign.objects.filter(pk=campaign_id).update(failed=F('failed') + 1)
            continue
        services.record_outgoing_message(
            contact, wamid, services.render_template(body, campaign.body_params, contact), from_human=True, campaign=campaign
        )
        Campaign.objects.filter(pk=campaign_id).update(sent=F('sent') + 1)
        time.sleep(0.1)
    Campaign.objects.filter(pk=campaign_id).update(status='done')


@shared_task
def dispatch_scheduled_campaigns():
    """Beat, a cada minuto: dispara as campanhas agendadas que venceram. O update condicional garante um disparo só."""
    due = Campaign.objects.filter(status=Campaign.Status.SCHEDULED, scheduled_at__lte=timezone.now())
    for campaign in due:
        if Campaign.objects.filter(pk=campaign.pk, status=Campaign.Status.SCHEDULED).update(status=Campaign.Status.SENDING):
            run_campaign.delay(campaign.pk, campaign.recipient_ids)
