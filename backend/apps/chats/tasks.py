import time

import httpx
from celery import shared_task
from django.db.models import F
from django.utils import timezone

from apps import ai
from apps.agents import services as agents_services
from apps.chats import ai_tools, events, services, whatsapp
from apps.chats.models import Campaign, Contact, ContactField, FollowUp, Message
from apps.companies import plans
from core.exceptions import ValidationError


@shared_task(bind=True, max_retries=3)
def process_incoming_message(self, message_id: int, deferred: bool = False, pending: list[str] | None = None, handed_off: bool = False):
    message = Message.objects.select_related('contact__account__agent__credential', 'contact__account__company').filter(pk=message_id).first()
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

        if not plans.is_active(account.company):
            return  # plano vencido: a mensagem fica gravada, mas a IA não responde
        if not account.ai_enabled:
            return  # IA desligada no número: só a equipe responde, sem resposta atrasada quando religar
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
        # agendada antes de o contato virar lead de campanha: também não sai
        if deferred and contact.ai_no_catchup:
            return
        opens_at = agents_services.next_ai_opening(account.agent, timezone.now())
        if opens_at and contact.ai_no_catchup:
            return  # lead de campanha fora do horário da IA: fica pra equipe, sem resposta atrasada
        if opens_at:
            process_incoming_message.apply_async(args=(message.pk,), kwargs={'deferred': True}, eta=opens_at)
            return
        history = services.build_history(contact, exclude_pk=message.pk)
        image, mime_type = None, ''
        if message.message_type == 'image':
            image, mime_type = whatsapp.download_media(account, message.media_id)

        tools, tool_state = ai_tools.build_tools(contact)
        reply = ai.generate_reply(account.agent, history, message.content, image=image, mime_type=mime_type, memory=_memory_with_fields(contact), tools=tools, variables=_prompt_variables(contact))
        parts = [p.strip() for p in reply.split('\n\n') if p.strip()] if account.agent.split_messages else [reply]
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2**self.request.retries)
    _send_parts(self, message_id, contact, parts, deferred, tool_state['handed_off'])
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
    # a IA respondeu: o lead de campanha volta ao normal
    Contact.objects.filter(pk=contact.pk, ai_no_catchup=True).update(ai_no_catchup=False)


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


def _prompt_variables(contact: Contact) -> dict[str, str]:
    """Variáveis do prompt do agente: todo campo cadastrado entra (vazio = "não informado"), mais nome e telefone."""
    keys = ContactField.objects.filter(company_id=contact.account.company_id).values_list('key', flat=True)
    return {key: '' for key in keys} | services.contact_variables(contact)


def _memory_with_fields(contact: Contact) -> str:
    """Memória da IA + campos personalizados preenchidos, pra IA saber o que já está cadastrado."""
    labels = dict(ContactField.objects.filter(company_id=contact.account.company_id).values_list('key', 'label'))
    data = '\n'.join(f'{labels[k]}: {v}' for k, v in contact.custom_fields.items() if k in labels)
    return '\n\n'.join(filter(None, [contact.memory, data and f'Dados cadastrados:\n{data}']))


@shared_task
def run_campaign(campaign_id: int, contact_ids: list[int]):
    campaign = Campaign.objects.select_related('account__company').filter(pk=campaign_id).first()
    if campaign is None:
        return
    if not plans.allows(campaign.account.company, 'campaigns'):  # agendada antes de o plano vencer
        Campaign.objects.filter(pk=campaign_id).update(status='failed')
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
        Contact.objects.filter(pk=contact.pk).update(ai_no_catchup=True)
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


@shared_task
def dispatch_followups():
    """Beat, a cada minuto: envia as retomadas agendadas pela IA. Atendente no controle (IA pausada) cancela."""
    due = FollowUp.objects.filter(status=FollowUp.Status.PENDING, send_at__lte=timezone.now()).select_related('contact__account__company')
    for followup in due:
        # update condicional: dois beats (ou um retry) não mandam a mesma retomada duas vezes
        if not FollowUp.objects.filter(pk=followup.pk, status=FollowUp.Status.PENDING).update(status=FollowUp.Status.SENT):
            continue
        contact = followup.contact
        if not plans.is_active(contact.account.company):
            FollowUp.objects.filter(pk=followup.pk).update(status=FollowUp.Status.CANCELED, error='plano expirado')
            continue
        if contact.ai_paused:
            FollowUp.objects.filter(pk=followup.pk).update(status=FollowUp.Status.CANCELED, error='atendente assumiu a conversa')
            continue
        if not contact.account.ai_enabled:
            FollowUp.objects.filter(pk=followup.pk).update(status=FollowUp.Status.CANCELED, error='IA desligada no número')
            continue
        try:
            wamid = whatsapp.send_text(contact.account, contact.wa_id, followup.text)
        except httpx.HTTPError as exc:
            detail = whatsapp.graph_error_message(exc.response) if isinstance(exc, httpx.HTTPStatusError) else str(exc)
            FollowUp.objects.filter(pk=followup.pk).update(status=FollowUp.Status.FAILED, error=detail[:255])
            continue
        services.record_outgoing_message(contact, wamid, followup.text)
