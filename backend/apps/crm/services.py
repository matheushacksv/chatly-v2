from django.conf import settings

from apps.chats import events
from apps.chats.models import Contact, WhatsAppAccount
from apps.chats.services import normalize_number
from apps.crm import pipedrive
from apps.crm.models import PipedriveAccount
from core.exceptions import NotFound, PipedriveError, ValidationError


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
        contact = Contact.objects.filter(account=pd.lead_account, wa_id=wa_id).first()
        if contact:
            link_person(contact, person_id)
        else:
            contact = Contact.objects.create(account=pd.lead_account, wa_id=wa_id, name=person.get('name') or '', pipedrive_person_id=person_id)
        events.publish(pd.company.pk, {'type': 'contact_synced', 'contact_id': contact.pk})

    if change_source != 'api':
        from apps.automations import services as flow_services

        flow_services.dispatch_pipedrive_event(contact, action, data, previous)
    return contact

    person = pipedrive.get_person(pd.api_key, person_id)
    try:
        wa_id = normalize_number(_first_phone(person))
    except ValidationError:
        return None

    contact = Contact.objects.filter(account=pd.lead_account, wa_id=wa_id).first()
    if contact:
        link_person(contact, person_id)
    else:
        contact = Contact.objects.create(account=pd.lead_account, wa_id=wa_id, name=person.get('name') or '', pipedrive_person_id=person_id)

    events.publish(pd.company.pk, {'type': 'contact_synced', 'contact_id': contact.pk})
    return contact


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


def create_deal_for_contact(
    pd: PipedriveAccount, contact: Contact, title: str, value: float | None = None, pipeline_id: int | None = None, stage_id: int | None = None
) -> dict:
    pipeline_id = pipeline_id or pd.default_pipeline_id
    stage_id = stage_id or pd.default_stage_id
    if not pipeline_id or not stage_id:
        raise ValidationError('default pipeline and stage not set')
    person_id = find_or_create_person(pd, contact)
    return pipedrive.create_deal(pd.api_key, title, person_id, pipeline_id, stage_id, value)
