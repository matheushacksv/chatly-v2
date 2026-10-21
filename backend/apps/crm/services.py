import logging
from datetime import timedelta
import re

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chats import events
from apps.chats.models import Contact, WhatsAppAccount
from apps.agents.services import next_ai_opening
from apps.chats.services import (
    _field_keys, _merge_import_fields, find_contact, get_template_send_info, normalize_number, send_template_message,
)
from apps.companies import plans
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

    if not (change_source == 'api' and pipedrive.is_own_change(data['id'])):
        _sync_fields(pd, contact, data, person_id)
        _auto_open(pd, contact, action, data, previous)
        from apps.automations import services as flow_services

        flow_services.dispatch_pipedrive_event(contact, action, data, previous)
    return contact


CUSTOM_KEY = re.compile(r'[0-9a-f]{40}')  # campo personalizado do Pipedrive tem chave hash; os nativos, nome


def _field_value(raw, options: dict) -> str:
    """Valor do Pipedrive em texto: opção vira o nome dela, várias opções viram lista, moeda/endereço o `value`."""
    if raw is None:
        return ''
    if isinstance(raw, dict):
        raw = raw.get('value', '')
    if isinstance(raw, list):
        return ', '.join(options.get(str(v), str(v)) for v in raw)
    return options.get(str(raw), str(raw))


def _sync_fields(pd: PipedriveAccount, contact: Contact, deal: dict, person_id: int) -> None:
    """Copia os campos mapeados (negócio e pessoa) pro contato antes da abertura: o template e a IA já usam."""
    if not pd.field_map:
        return
    try:
        custom = {}
        if any(m['source'] == 'deal' for m in pd.field_map):
            source = deal if 'custom_fields' in deal else pipedrive.get_deal(pd.api_key, deal['id'])
            custom['deal'] = source.get('custom_fields') or {}
        if any(m['source'] == 'person' for m in pd.field_map):
            custom['person'] = pipedrive.get_person(pd.api_key, person_id).get('custom_fields') or {}
    except DomainError as e:
        logger.warning('campos do negócio %s não sincronizados: %s', deal.get('id'), e)
        return
    values = {m['contact_field']: _field_value(custom[m['source']].get(m['key']), m['options']) for m in pd.field_map}
    _merge_import_fields(contact, values, _field_keys(pd.company))


def list_custom_fields(company) -> list[dict]:
    account = get_pipedrive_account(company)
    return [
        {'source': source, 'key': f['key'], 'name': f.get('name') or f['key'],
         'options': {str(o['id']): o['label'] for o in f.get('options') or []}}
        for source in ('deal', 'person')
        for f in pipedrive.list_fields(account.api_key, source)
        if CUSTOM_KEY.fullmatch(f.get('key') or '')
    ]


def update_field_map(company, items: list[dict]) -> PipedriveAccount:
    account = get_pipedrive_account(company)
    available = {(f['source'], f['key']): f for f in list_custom_fields(company)} if items else {}
    known = _field_keys(company)
    field_map = []
    for item in items:
        field = available.get((item['source'], item['key']))
        if field is None:
            raise ValidationError('campo do Pipedrive não encontrado')
        if item['contact_field'] not in known:
            raise ValidationError('campo do contato não encontrado')
        # ponytail: opções copiadas ao salvar; opção criada depois no Pipedrive chega como id até salvar de novo
        field_map.append({**field, 'contact_field': item['contact_field']})
    account.field_map = field_map
    account.save(update_fields=['field_map'])
    return account


def _pick_template(pd: PipedriveAccount, contact: Contact) -> dict:
    """Primeira regra cujo campo do contato está entre os valores; nenhuma: o template padrão."""
    for rule in pd.auto_rules:
        value = str(contact.custom_fields.get(rule['field'], '')).strip().lower()
        if value and value in {v.strip().lower() for v in rule['values']}:
            return rule['template']
    return pd.auto_template


def _auto_open(pd: PipedriveAccount, contact: Contact, action: str, deal: dict, previous: dict | None) -> None:
    """Negócio entrou numa etapa configurada: manda o template de abertura e a IA assume as respostas.
    Mudança feita pelo próprio chatly nem chega aqui: a conversa já existe."""
    template = _pick_template(pd, contact)
    if deal.get('stage_id') not in pd.auto_stage_ids or not template.get('template_name'):
        return
    if deal.get('status', 'open') != 'open':
        return
    if action == 'change' and 'stage_id' not in (previous or {}):
        return  # mudou outra coisa no negócio, não a etapa
    if pd.auto_only_in_hours and next_ai_opening(pd.lead_account.agent, timezone.now()) is not None:
        return  # fora do horário da IA: fica de fora, não espera abrir
    delay = pd.auto_delay_minutes
    try:
        with transaction.atomic():
            opening = DealOpening.objects.create(
                pipedrive_account=pd, deal_id=deal['id'], contact=contact,
                send_at=timezone.now() + timedelta(minutes=delay) if delay else None,
            )
    except IntegrityError:
        return
    if not delay:
        _send_opening(pd, opening)  # com atraso quem manda é o dispatch_deal_openings


def _send_template(pd: PipedriveAccount, contact: Contact, template: dict, deal_id: int) -> bool:
    if not plans.is_active(pd.company):
        return False  # plano vencido: abertura automática e follow-up param
    try:
        send_template_message(
            pd.company, contact.pk, template['template_name'], template.get('language', ''),
            template.get('body_params', []), template.get('header_media_id', ''),
        )
    except (DomainError, httpx.HTTPError) as e:
        logger.warning('template %s do negócio %s não enviado: %s', template['template_name'], deal_id, e)
        return False
    return True


def _send_opening(pd: PipedriveAccount, opening: DealOpening) -> None:
    template = _pick_template(pd, opening.contact)  # na hora do envio: os campos podem ter chegado durante a espera
    if not template.get('template_name'):
        return
    if not _send_template(pd, opening.contact, template, opening.deal_id):
        opening.delete()  # sem envio não conta como aberto: a próxima mudança de etapa tenta de novo
        return
    opening.sent_at = timezone.now()
    hours = pd.auto_followup.get('after_hours') if pd.auto_followup.get('template_name') else None
    opening.followup_at = opening.sent_at + timedelta(hours=hours) if hours else None
    opening.save(update_fields=['sent_at', 'followup_at'])


def _deal_open(pd: PipedriveAccount, deal_id: int, stage_ids: list[int] | None = None) -> bool | None:
    """Negócio ainda aberto (e numa das etapas, se pedir)? None: Pipedrive fora do ar, tenta no próximo minuto."""
    try:
        deal = pipedrive.get_deal(pd.api_key, deal_id)
    except NotFound:
        return False
    except PipedriveError:
        return None
    return deal.get('status') == 'open' and (stage_ids is None or deal.get('stage_id') in stage_ids)


def dispatch_deal_openings() -> None:
    """Beat, a cada minuto: aberturas que esperavam o atraso e follow-ups de quem não respondeu.
    Lead que já conversou, atendente no controle ou negócio fechado/fora da etapa: descarta.
    Fora do horário da IA (com "só no horário"): adia pra quando abrir."""
    now = timezone.now()
    related = ('pipedrive_account__company', 'pipedrive_account__lead_account__agent', 'contact__account')
    for opening in DealOpening.objects.filter(send_at__lte=now).select_related(*related):
        # update condicional: dois beats não mandam a mesma abertura duas vezes
        if not DealOpening.objects.filter(pk=opening.pk, send_at=opening.send_at).update(send_at=None):
            continue
        pd, contact = opening.pipedrive_account, opening.contact
        if contact.ai_paused or contact.messages.filter(created_at__gte=opening.created_at).exists():
            continue  # a conversa começou durante a espera: não abre por cima
        if later := _outside_hours(pd, now):
            DealOpening.objects.filter(pk=opening.pk).update(send_at=later)
            continue
        is_open = _deal_open(pd, opening.deal_id, pd.auto_stage_ids)
        if is_open is None:
            DealOpening.objects.filter(pk=opening.pk).update(send_at=now + timedelta(minutes=1))
        elif is_open:
            _send_opening(pd, opening)

    for opening in DealOpening.objects.filter(followup_at__lte=now).select_related(*related):
        if not DealOpening.objects.filter(pk=opening.pk, followup_at=opening.followup_at).update(followup_at=None):
            continue
        pd, contact = opening.pipedrive_account, opening.contact
        template = pd.auto_followup
        if not template.get('template_name') or contact.ai_paused:
            continue
        if contact.messages.filter(direction='in', created_at__gt=opening.sent_at).exists():
            continue  # respondeu a abertura: sem follow-up
        if later := _outside_hours(pd, now):
            DealOpening.objects.filter(pk=opening.pk).update(followup_at=later)
            continue
        is_open = _deal_open(pd, opening.deal_id)
        if is_open is None:
            DealOpening.objects.filter(pk=opening.pk).update(followup_at=now + timedelta(minutes=1))
        elif is_open:
            _send_template(pd, contact, template, opening.deal_id)


def _outside_hours(pd: PipedriveAccount, now):
    if pd.auto_only_in_hours and pd.lead_account and pd.lead_account.agent:
        return next_ai_opening(pd.lead_account.agent, now)
    return None


def update_auto_open(
    company, stage_ids: list[int], template: dict, only_in_hours: bool, rules: list[dict] = (),
    delay_minutes: int = 0, followup: dict | None = None,
) -> PipedriveAccount:
    account = get_pipedrive_account(company)
    rules = [{**r, 'values': [v.strip() for v in r['values'] if v.strip()]} for r in rules]
    if stage_ids:
        if account.lead_account is None:
            raise ValidationError('escolha a conta de WhatsApp dos leads antes')
        # sem padrão vale, desde que haja regra: quem não casa com nenhuma fica sem mensagem
        if not rules and not template.get('template_name'):
            raise ValidationError('escolha o template de abertura')
        for rule in rules:
            if not rule['field'] or not rule['values'] or not rule['template'].get('template_name'):
                raise ValidationError('cada regra precisa de campo, valores e template')
        if followup and followup.get('template_name') and not followup.get('after_hours'):
            raise ValidationError('diga depois de quantas horas sai o follow-up')
        for t in [template, *(r['template'] for r in rules), followup or {}]:
            if t.get('template_name'):
                get_template_send_info(account.lead_account, t['template_name'], t.get('language', ''))  # aprovado?
    account.auto_stage_ids = list(dict.fromkeys(stage_ids))
    account.auto_template = template
    account.auto_rules = rules
    account.auto_only_in_hours = only_in_hours
    account.auto_delay_minutes = delay_minutes
    account.auto_followup = followup if followup and followup.get('template_name') else {}
    account.save(update_fields=['auto_stage_ids', 'auto_template', 'auto_rules', 'auto_only_in_hours', 'auto_delay_minutes', 'auto_followup'])
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
