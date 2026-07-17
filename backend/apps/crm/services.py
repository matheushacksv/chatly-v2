from apps.chats.models import Contact
from apps.crm import pipedrive
from apps.crm.models import PipedriveAccount
from core.exceptions import NotFound, ValidationError

def get_pipedrive_account(company) -> PipedriveAccount:
    account = PipedriveAccount.objects.filter(company=company).first()
    if not account:
        raise NotFound('pipedrive not connected')
    return account

def connect(company, api_key: str) -> PipedriveAccount:
    pipedrive.validate(api_key)
    account, _ = PipedriveAccount.objects.update_or_create(company=company, defaults={'api_key': api_key})
    return account

def update_defaults(company, pipeline_id: int | None, stage_id: int | None) -> PipedriveAccount:
    account = get_pipedrive_account(company)
    account.default_pipeline_id = pipeline_id
    account.default_stage_id = stage_id
    account.save(update_fields=['default_pipeline_id', 'default_stage_id'])
    return account

def disconnect(company):
    PipedriveAccount.objects.filter(company=company).delete()

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

def create_deal_for_contact(pd: PipedriveAccount, contact: Contact, title: str, value: float | None = None, pipeline_id: int | None = None, stage_id: int | None = None) -> dict:
    pipeline_id = pipeline_id or pd.default_pipeline_id
    stage_id = stage_id or pd.default_stage_id
    if not pipeline_id or not stage_id:
        raise ValidationError('default pipeline and stage not set')
    person_id = find_or_create_person(pd, contact)
    return pipedrive.create_deal(pd.api_key, title, person_id, pipeline_id, stage_id, value)