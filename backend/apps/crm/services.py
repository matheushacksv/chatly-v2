import logging

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chats import events
from apps.chats.models import Contact, WhatsAppAccount
from apps.agents.services import next_ai_opening
from apps.chats.services import find_contact, get_template_send_info, normalize_number, send_template_message
from apps.crm import pipedrive
from apps.crm.models import DealOpening, PipedriveAccount
from core.exceptions import DomainError, NotFound, PipedriveError, ValidationError

logger = logging.getLogger(__name__)


def _webhook_url(account: PipedriveAccount) -> str:
    return f'{settings.FRONTEND_URL}/api/companies/pipedrive/webhook/{account.webhook_token}'


def _register_webhooks(account: PipedriveAccount) -> list[int]:
    url = _webhook_url(account)
    ids: list[int] = []
    for action in ('create', 'change'):
        try:
            ids.append(pipedrive.register_webhook(account.api_key, url, action, 'deal'))
        except PipedriveError:
            pass
    return ids


def _delete_webhooks(company) -> None:
    account = PipedriveAccount.objects.filter(company=company).first()
    if not account:
        return
    for webhook_id in account.webhook_ids:
        try:
            pipedrive.delete_webhook(account.api_key, webhook_id)
        except (PipedriveError, NotFound):
            pass


def _first_phone(person: dict) -> str:
    phones = person.get('phones') or []
    if not phones:
        return ''
    primary = next((p for p in phones if p.get('primary')), phones[0])
    return primary.get('value') or ''


def ingest_deal_event(pd: PipedriveAccount, action: str, data: dict, previous: dict | None, change_source: str = '') -> Contact | None:
    if pd.lead_account is None:
        return None

    person_id = data.get('person_id')
    if not person_id:
        person_id = pipedrive.get_deal(pd.api_key, data['id']).get('person_id')
    if not person_id:
        return None

    linked = Contact.objects.filter(account=pd.lead_account, pipedrive_person_id=person_id).first()
    if linked:
        contact = linked
    else:
        person = pipedrive.get_person(pd.api_key, person_id)
        try:
            wa_id = normalize_number(_first_phone(person))
        except ValidationError:
            return None
        contact = find_contact(pd.lead_account, wa_id)
        if contact:
            link_person(contact, person_id)
        else:
            contact = Contact.objects.create(account=pd.lead_account, wa_id=wa_id, name=person.get('name') or '', pipedrive_person_id=person_id)
        events.publish(pd.company.pk, {'type': 'contact_synced', 'contact_id': contact.pk})

    if change_source != 'api':
        _auto_open(pd, contact, action, data, previous)
        from apps.automations import services as flow_services

        flow_services.dispatch_pipedrive_event(contact, action, data, previous)
    return contact


def _auto_open(pd: PipedriveAccount, contact: Contact, action: str, deal: dict, previous: dict | None) -> None:
    """Negócio entrou numa etapa configurada: manda o template de abertura e a IA assume as respostas.
    Mudança feita pelo próprio chatly (change_source 'api') nem chega aqui: a conversa já existe."""
    template = pd.auto_template
    if deal.get('stage_id') not in pd.auto_stage_ids or not template.get('template_name'):
        return
    if deal.get('status', 'open') != 'open':
        return
    if action == 'change' and 'stage_id' not in (previous or {}):
        return  # mudou outra coisa no negócio, não a etapa
    if pd.auto_only_in_hours and next_ai_opening(pd.lead_account.agent, timezone.now()) is not None:
        return  # fora do horário da IA: fica de fora, não espera abrir
    try:
        with transaction.atomic():
            DealOpening.objects.create(pipedrive_account=pd, deal_id=deal['id'], contact=contact)
    except IntegrityError:
        return
    try:
        send_template_message(
            pd.company, contact.pk, template['template_name'], template.get('language', ''),
            template.get('body_params', []), template.get('header_media_id', ''),
        )
    except (DomainError, httpx.HTTPError) as e:
        # sem envio não conta como aberto: a próxima mudança de etapa tenta de novo
        DealOpening.objects.filter(pipedrive_account=pd, deal_id=deal['id']).delete()
        logger.warning('abertura do negócio %s não enviada: %s', deal['id'], e)


def update_auto_open(company, stage_ids: list[int], template: dict, only_in_hours: bool) -> PipedriveAccount:
    account = get_pipedrive_account(company)
    if stage_ids:
        if account.lead_account is None:
            raise ValidationError('escolha a conta de WhatsApp dos leads antes')
        if not template.get('template_name') or not template.get('language'):
            raise ValidationError('escolha o template de abertura')
        get_template_send_info(account.lead_account, template['template_name'], template['language'])  # aprovado?
    account.auto_stage_ids = list(dict.fromkeys(stage_ids))
    account.auto_template = template
    account.auto_only_in_hours = only_in_hours
    account.save(update_fields=['auto_stage_ids', 'auto_template', 'auto_only_in_hours'])
    return account


def get_pipedrive_account(company) -> PipedriveAccount:
    account = PipedriveAccount.objects.filter(company=company).first()
    if not account:
        raise NotFound('pipedrive not connected')
    return account


def get_account_by_webhook_token(token: str) -> PipedriveAccount | None:
    return PipedriveAccount.objects.filter(webhook_token=token).first()


def connect(company, api_key: str) -> PipedriveAccount:
    pipedrive.validate(api_key)
    _delete_webhooks(company)
    account, _ = PipedriveAccount.objects.update_or_create(company=company, defaults={'api_key': api_key})
    if account.lead_account is None:
        account.lead_account = WhatsAppAccount.objects.filter(company=company).first()
    account.webhook_ids = _register_webhooks(account)
    account.save(update_fields=['lead_account', 'webhook_ids'])
    return account


def disconnect(company):
    _delete_webhooks(company)
    PipedriveAccount.objects.filter(company=company).delete()


def update_defaults(company, pipeline_id: int | None, stage_id: int | None, lead_account_id: int | None = None) -> PipedriveAccount:
    account = get_pipedrive_account(company)
    account.default_pipeline_id = pipeline_id
    account.default_stage_id = stage_id
    fields = ['default_pipeline_id', 'default_stage_id']
    if lead_account_id is not None:
        lead = WhatsAppAccount.objects.filter(id=lead_account_id, company=company).first()
        if lead is None:
            raise ValidationError('invalid lead account')
        account.lead_account = lead
        fields.append('lead_account')
    account.save(update_fields=fields)
    return account


def get_contact(company, contact_id: int) -> Contact:
    contact = Contact.objects.filter(id=contact_id, account__company=company).first()
    if not contact:
        raise NotFound('contact not found')
    return contact


def pipeline_with_stages(pd: PipedriveAccount) -> list[dict]:
    pipelines = pipedrive.list_pipelines(pd.api_key)
    stages = pipedrive.list_stages(pd.api_key)
    for pipeline in pipelines:
        pipeline['stages'] = [s for s in stages if s['pipeline_id'] == pipeline['id']]
    return pipelines


def resolve_person(pd: PipedriveAccount, contact: Contact) -> dict:
    if contact.pipedrive_person_id:
        try:
            person = pipedrive.get_person(pd.api_key, contact.pipedrive_person_id)
            return {'person': person, 'suggestions': []}
        except NotFound:
            contact.pipedrive_person_id = None
            contact.save(update_fields=['pipedrive_person_id'])
    suggestions = pipedrive.search_persons(pd.api_key, contact.wa_id)
    return {'person': None, 'suggestions': suggestions}


def link_person(contact: Contact, person_id: int):
    contact.pipedrive_person_id = person_id
    contact.save(update_fields=['pipedrive_person_id'])


def create_person_for_contact(pd: PipedriveAccount, contact: Contact, name: str) -> dict:
    person = pipedrive.create_person(pd.api_key, name, contact.wa_id)
    link_person(contact, person['id'])
    return person


def find_or_create_person(pd: PipedriveAccount, contact: Contact) -> int:
    if contact.pipedrive_person_id:
        return contact.pipedrive_person_id
    matches = pipedrive.search_persons(pd.api_key, contact.wa_id)
    if matches:
        person_id = matches[0]['id']
    else:
        person = pipedrive.create_person(pd.api_key, contact.name or contact.wa_id, contact.wa_id)
        person_id = person['id']
    link_person(contact, person_id)
    return person_id


def get_or_create_open_deal(pd: PipedriveAccount, contact: Contact, title: str) -> tuple[dict, bool]:
    """Pra IA e fluxos: se a pessoa já tem negócio aberto, usa ele em vez de duplicar. Ganho/perdido não conta."""
    if not pd.default_pipeline_id or not pd.default_stage_id:
        raise ValidationError('default pipeline and stage not set')  # antes de tocar no Pipedrive
    person_id = find_or_create_person(pd, contact)
    open_deals = pipedrive.list_open_deals(pd.api_key, person_id)
    if open_deals:
        return open_deals[0], False
    return create_deal_for_contact(pd, contact, title), True


def create_deal_for_contact(
    pd: PipedriveAccount, contact: Contact, title: str, value: float | None = None, pipeline_id: int | None = None, stage_id: int | None = None
) -> dict:
    pipeline_id = pipeline_id or pd.default_pipeline_id
    stage_id = stage_id or pd.default_stage_id
    if not pipeline_id or not stage_id:
        raise ValidationError('default pipeline and stage not set')
    person_id = find_or_create_person(pd, contact)
    return pipedrive.create_deal(pd.api_key, title, person_id, pipeline_id, stage_id, value)
