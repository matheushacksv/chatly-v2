import time

import httpx
from celery import shared_task
from django.db.models import F

from apps import ai
from apps.chats import events, services, whatsapp
from apps.chats.models import Campaign, Contact, Message
from apps.crm import pipedrive
from apps.crm import services as crm_services
from apps.crm.models import PipedriveAccount
from core.exceptions import ValidationError


@shared_task(bind=True, max_retries=3)
def process_incoming_message(self, message_id: int):
    message = Message.objects.select_related('contact__account__agent__credential').filter(pk=message_id).first()
    if message is None:
        return
    contact = message.contact
    account = contact.account
    try:
        if message.message_type == 'audio' and not message.content:
            audio_bytes, audio_mime = whatsapp.download_media(account, message.media_id)
            message.content = ai.transcribe_audio(audio_bytes, audio_mime)
            message.save(update_fields=['content'])
        if contact.ai_paused:
            return
        if Message.objects.filter(contact=contact, direction='in', pk__gt=message.pk).exists():
            return
        history = services.build_history(contact, exclude_pk=message.pk)
        image, mime_type = None, ''
        if message.message_type == 'image':
            image, mime_type = whatsapp.download_media(account, message.media_id)

        def transferir_para_atendente() -> str:
            """Transfere a conversa para um humano. Use quando o cliente pedir para falar com uma pessoa
            ou quando você não conseguir resolver"""
            contact.ai_paused = True
            contact.save(update_fields=['ai_paused'])
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

        reply = ai.generate_reply(account.agent, history, message.content, image=image, mime_type=mime_type, memory=contact.memory, tools=tools)
        parts = [p.strip() for p in reply.split('\n\n') if p.strip()] if account.agent.split_messages else [reply]
        wamids = []
        for i, part in enumerate(parts):
            if i:
                time.sleep(min(1 + len(part) / 25, 5))
            wamids.append(whatsapp.send_text(account, contact.wa_id, part))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2**self.request.retries)
    for wamid, part in zip(wamids, parts):
        services.record_outgoing_message(contact, wamid, part)
    update_contact_memory.delay(contact.pk)


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
        params = services.resolve_params(campaign.body_params, contact)
        try:
            wamid = whatsapp.send_template(
                campaign.account,
                contact.wa_id,
                campaign.template_name,
                campaign.template_language,
                params,
                campaign.header_media_id,
                campaign.header_media_type,
            )
        except httpx.HTTPStatusError:
            Campaign.objects.filter(pk=campaign_id).update(failed=F('failed') + 1)
            continue
        services.record_outgoing_message(
            contact, wamid, services.render_template(body, campaign.body_params, contact), from_human=True, campaign=campaign
        )
        Campaign.objects.filter(pk=campaign_id).update(sent=F('sent') + 1)
        time.sleep(0.1)
    Campaign.objects.filter(pk=campaign_id).update(status='done')
