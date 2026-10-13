import json
import re

from django.db import IntegrityError
from django.db.models import Count, QuerySet
from django.utils import timezone

from apps.automations import outbound
from apps.automations.models import Flow, FlowRun, LeadSource
from apps.chats import events, whatsapp
from apps.chats import services as chat_services
from apps.chats.models import Contact, Message
from apps.chats.services import get_account
from apps.companies.models import Company
from core.exceptions import NotFound, ValidationError

MAX_STEPS = 50
LEAD_NOTE_MAX_CHARS = 500  # dado de formulário vai pro prompt da IA: limita o tamanho
TERMINAL = ('ai', 'handoff', 'end')

# * ------ graph helpers -----


def _node(graph: dict, node_id: str) -> dict | None:
    return next((n for n in graph.get('nodes', []) if n['id'] == node_id), None)


def _start_id(graph: dict) -> str:
    return next((n['id'] for n in graph.get('nodes', []) if n['type'] == 'start'), '')


def _next_id(graph: dict, source: str, handle: str = '') -> str:
    for e in graph.get('edges', []):
        if e['source'] == source and (e.get('sourceHandle') or '') == (handle or ''):
            return e['target']
    return ''


VAR_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')


def _render(text: str, run: FlowRun, escape_json: bool = False) -> str:
    """Troca {{nome}}, {{telefone}}, {{botao}}, {{campanha}} e {{<variável do fluxo>}}. Variável desconhecida fica como está."""
    contact, ctx = run.contact, run.context
    values = {k: str(v) for k, v in ctx.items() if v is not None and not isinstance(v, (dict, list))}
    values |= {
        'nome': contact.name or contact.wa_id,
        'telefone': contact.wa_id,
        'botao': str(ctx.get('button', '')),
        'campanha': str(ctx.get('campanha', '')),
    }

    def sub(match: re.Match) -> str:
        if match.group(1) not in values:
            return match.group(0)
        value = values[match.group(1)]
        # dentro de corpo JSON: aspas e quebras de linha do valor não podem quebrar o documento
        return json.dumps(value)[1:-1] if escape_json else value

    return VAR_RE.sub(sub, text or '')


def _run_http(data: dict, run: FlowRun) -> str:
    headers = {
        h['key'].strip(): _render(h.get('value', ''), run)
        for h in data.get('headers') or []
        if (h.get('key') or '').strip()
    }
    body = data.get('body', '') or ''
    if body and not any(k.lower() == 'content-type' for k in headers):
        headers['Content-Type'] = 'application/json'
    is_json = any(k.lower() == 'content-type' and 'json' in v.lower() for k, v in headers.items())
    try:
        status = outbound.request(
            (data.get('method') or 'POST').upper(), _render(data.get('url', ''), run), headers, _render(body, run, escape_json=is_json)
        )
    except Exception:
        # URL recusada, timeout, DNS: não derruba o fluxo, segue pela saída de falha
        status = 0
    run.context['http_status'] = status
    return 'sucesso' if 200 <= status < 300 else 'falha'


def _run_tag(data: dict, run: FlowRun) -> None:
    tag_id = data.get('tag_id')
    if not tag_id:
        return
    try:
        if data.get('action') == 'remove':
            chat_services.remove_contact_tag(run.flow.company, run.contact.pk, tag_id)
        else:
            chat_services.add_contact_tag(run.flow.company, run.contact.pk, tag_id)
    except NotFound:
        pass  # etiqueta apagada depois do fluxo montado: segue sem ela


def _match_condition(subject: str, op: str, value: str) -> bool:
    if op == 'equals':
        return subject.strip().lower() == value.strip().lower()
    if op == 'regex':
        try:
            return bool(re.search(value, subject))
        except re.error:
            return False
    return value.lower() in subject.lower()


def _run_crm(node: dict, run: FlowRun) -> None:
    from apps.crm import pipedrive
    from apps.crm import services as crm_services
    from apps.crm.models import PipedriveAccount

    pd = PipedriveAccount.objects.filter(company=run.flow.company).first()
    if pd is None:
        return
    data = node.get('data', {})
    action = data.get('action', 'deal')
    contact = run.contact
    if action == 'person':
        crm_services.find_or_create_person(pd, contact)
    elif action == 'deal':
        deal = crm_services.create_deal_for_contact(pd, contact, data.get('title') or (contact.name or contact.wa_id))
        run.context['deal_id'] = deal['id']
    elif action == 'note':
        person_id = crm_services.find_or_create_person(pd, contact)
        text = _render(data.get('text', ''), run)
        pipedrive.create_note(pd.api_key, text, person_id=person_id)
    elif action == 'stage':
        deal_id = run.context.get('deal_id')
        if deal_id and data.get('stage_id'):
            pipedrive.update_deal(pd.api_key, deal_id, stage_id=data['stage_id'])


# * ------ executores -----


def _execute(node: dict, run: FlowRun, message: Message | None) -> str | None:
    data = node.get('data', {})
    contact = run.contact
    account = run.flow.account
    t = node['type']

    if t == 'start':
        return ''
    if t == 'template':
        chat_services.send_template_message(
            run.flow.company,
            contact.id,
            data.get('template_name', ''),
            data.get('language', ''),
            data.get('body_params', []),
            data.get('header_media_id', ''),
        )
        return ''
    if t == 'message':
        text = _render(data.get('text', ''), run)
        wamid = whatsapp.send_text(account, contact.wa_id, text)
        chat_services.record_outgoing_message(contact, wamid, text)
        return ''
    if t == 'handoff':
        chat_services.pause_ai(contact)
        events.publish(run.flow.company_id, {'type': 'handoff', 'contact_id': contact.id, 'name': contact.name or contact.wa_id})
        return ''
    if t == 'buttons':
        text = _render(data.get('text', ''), run)
        wamid = whatsapp.send_interactive(account, contact.wa_id, text, data.get('buttons', []))
        chat_services.record_outgoing_message(contact, wamid, text)
        return
    if t == 'condition':
        # janela de 24h aberta: dá pra IA responder direto, sem template
        if data.get('op') == 'window_open':
            return 'sim' if chat_services.service_window_open(contact) else 'nao'
        src = data.get('source', 'last_message')
        subject = run.context.get('last_message', '') if src == 'last_message' else str(run.context.get(src, ''))
        return 'sim' if _match_condition(subject, data.get('op', 'contains'), data.get('value', '')) else 'nao'
    if t == 'delay':
        from apps.automations import tasks

        tasks.resume_flow.apply_async(args=(run.pk,), countdown=int(data.get('minutes', 0)) * 60)
        return None
    if t == 'wait':
        if data.get('timeout_minutes'):
            from apps.automations import tasks

            tasks.resume_flow.apply_async(args=(run.pk,), countdown=int(data['timeout_minutes']) * 60)
        return None
    if t == 'crm':
        _run_crm(node, run)
        return ''
    if t == 'http':
        return _run_http(data, run)
    if t == 'tag':
        _run_tag(data, run)
        return ''
    if t == 'ai':
        chat_services.resume_ai(contact)
        return ''
    if t == 'pause':
        chat_services.pause_ai(contact)
        return ''
    if t in ('start', 'handoff', 'end'):
        return ''
    return ''


# * ------ laços -----


def advance(run: FlowRun, message: Message | None = None) -> None:
    try:
        graph = run.flow.graph
        for _ in range(MAX_STEPS):
            node = _node(graph, run.node_id)
            if node is None:
                run.status = FlowRun.Status.DONE
                run.save(update_fields=['status', 'context', 'updated_at'])
                return

            handle = _execute(node, run, message)
            message = None
            run.steps.append(
                {'node': node['id'], 'type': node['type'], 'at': timezone.now().isoformat(), 'result': 'wait' if handle is None else (handle or 'ok')}
            )

            if handle is None:
                run.status = FlowRun.Status.WAITING
                run.save(update_fields=['status', 'steps', 'context', 'updated_at'])
                return
            if node['type'] in TERMINAL:
                run.status = FlowRun.Status.DONE
                run.save(update_fields=['status', 'steps', 'context', 'updated_at'])
                return
            run.node_id = _next_id(graph, node['id'], handle)
            if not run.node_id:
                run.status = FlowRun.Status.DONE
                run.save(update_fields=['status', 'node_id', 'steps', 'context', 'updated_at'])
                return
            run.save(update_fields=['node_id', 'steps', 'context', 'updated_at'])
        run.status = FlowRun.Status.FAILED
        run.save(update_fields=['status', 'updated_at'])
    except Exception as exc:
        run.status = FlowRun.Status.FAILED
        run.steps.append({'node': run.node_id, 'type': 'error', 'at': timezone.now().isoformat(), 'result': str(exc)[:200]})
        run.save(update_fields=['status', 'steps', 'updated_at'])


# * ------ flows -----


def _flows(company: Company) -> QuerySet[Flow]:
    return Flow.objects.filter(company=company).annotate(run_count=Count('runs'))


def list_flows(company: Company) -> QuerySet[Flow]:
    return _flows(company).order_by('-id')


def get_flow(company: Company, flow_id: int) -> Flow:
    flow = _flows(company).filter(pk=flow_id).first()
    if flow is None:
        raise NotFound('flow not found')
    return flow


def _validate_trigger(trigger: str) -> None:
    if trigger not in Flow.Trigger.values:
        raise ValidationError(f'invalid trigger: {trigger}')


def create_flow(company: Company, data: dict) -> Flow:
    _validate_trigger(data['trigger'])
    account = get_account(company, data['account_id'])
    flow = Flow.objects.create(
        company=company,
        account=account,
        name=data['name'],
        trigger=data['trigger'],
        trigger_config=data.get('trigger_config') or {},
        graph=data.get('graph') or {},
    )
    return get_flow(company, flow.pk)


def update_flow(company: Company, flow_id: int, data: dict) -> Flow:
    flow = get_flow(company, flow_id)
    _validate_trigger(data['trigger'])
    flow.account = get_account(company, data['account_id'])
    flow.name = data['name']
    flow.trigger = data['trigger']
    flow.trigger_config = data.get('trigger_config') or {}
    flow.graph = data.get('graph') or {}
    flow.save()
    return get_flow(company, flow.pk)


def delete_flow(company: Company, flow_id: int) -> None:
    get_flow(company, flow_id).delete()


def set_active(company: Company, flow_id: int, active: bool) -> Flow:
    flow = get_flow(company, flow_id)
    if active:
        validate_graph(flow)
    flow.active = active
    flow.save(update_fields=['active', 'updated_at'])
    return get_flow(company, flow.pk)


def list_runs(company: Company, flow_id: int) -> QuerySet[FlowRun]:
    get_flow(company, flow_id)
    return FlowRun.objects.filter(flow_id=flow_id).select_related('contact').order_by('-id')


def start_flow(flow: Flow, contact: Contact, context: dict | None = None) -> FlowRun:
    existing = FlowRun.objects.filter(flow=flow, contact=contact, status__in=FlowRun.ACTIVE_STATUSES).first()
    if existing:
        return existing
    try:
        run = FlowRun.objects.create(flow=flow, contact=contact, node_id=_start_id(flow.graph), status=FlowRun.Status.RUNNING, context=context or {})
    except IntegrityError:
        return FlowRun.objects.get(flow=flow, contact=contact, status__in=FlowRun.ACTIVE_STATUSES)
    advance(run)
    return run


# * ------ lead sources -----


def list_sources(company: Company) -> QuerySet[LeadSource]:
    return LeadSource.objects.filter(company=company).order_by('-id')


def create_source(company: Company, name: str, account_id: int) -> LeadSource:
    account = get_account(company, account_id)
    return LeadSource.objects.create(company=company, account=account, name=name)


def delete_source(company: Company, source_id: int) -> None:
    deleted, _ = LeadSource.objects.filter(company=company, pk=source_id).delete()
    if not deleted:
        raise NotFound('lead source not found')


def _note_lead(contact: Contact, source: LeadSource, ctx: dict) -> None:
    # a IA lê contact.memory: assim ela sabe de onde o lead veio e o que ele preencheu
    info = ', '.join(f'{k}: {v}' for k, v in ctx.items() if v not in ('', None))
    note = f'Chegou como lead pela fonte "{source.name}" em {timezone.localdate():%d/%m/%Y}' + (f' ({info})' if info else '')
    contact.memory = '\n'.join(filter(None, [contact.memory, note[:LEAD_NOTE_MAX_CHARS]]))
    contact.save(update_fields=['memory'])


def create_lead(source: LeadSource, payload: dict) -> tuple[Contact, bool]:
    wa_id = chat_services.normalize_number(payload.get('telefone', ''))
    contact = chat_services.get_or_create_contact(source.account, wa_id, payload.get('nome', ''))
    ctx = {'origem': payload.get('origem', ''), 'email': payload.get('email', ''), **(payload.get('extras') or {})}
    _note_lead(contact, source, ctx)
    flow = Flow.objects.filter(account=source.account, trigger=Flow.Trigger.LEAD, active=True).order_by('-id').first()
    if not flow:
        return contact, False
    start_flow(flow, contact, ctx)
    return contact, True


def _cancel_active_runs(contact: Contact, reason: str) -> None:
    for run in FlowRun.objects.filter(contact=contact, status__in=FlowRun.ACTIVE_STATUSES):
        run.status = FlowRun.Status.DONE
        run.steps.append({'node': run.node_id, 'type': 'cancel', 'at': timezone.now().isoformat(), 'result': reason})
        run.save(update_fields=['status', 'steps', 'updated_at'])


def _dispatch_campaign_click(message: Message) -> bool:
    """Clique num botão do template de uma campanha com fluxo: roda o fluxo uma vez por botão."""
    if not (message.campaign_id and message.button_id):
        return False
    campaign = message.campaign
    flow = campaign.flow
    if flow is None or not flow.active:
        return False
    contact = message.contact
    clicked = FlowRun.objects.filter(flow=flow, contact=contact, context__campaign_id=campaign.pk, context__button_id=message.button_id)
    if clicked.exists():
        return True  # mesmo botão de novo: já tratado, e a IA não precisa responder "Confirmar" outra vez
    # o clique é a intenção mais recente do contato: encerra o que estava rodando pra ele
    _cancel_active_runs(contact, 'cancelada pelo clique na campanha')
    button = message.content or message.button_id
    ctx = {'campaign_id': campaign.pk, 'campanha': campaign.name, 'button': button, 'button_id': message.button_id, 'last_message': button}
    start_flow(flow, contact, ctx)
    return True


def dispatch_incoming(message: Message) -> bool:
    if _dispatch_campaign_click(message):
        return True
    contact = message.contact
    run = FlowRun.objects.filter(contact=contact, status__in=FlowRun.ACTIVE_STATUSES).select_related('flow').first()
    if run:
        if run.status == FlowRun.Status.WAITING:
            node = _node(run.flow.graph, run.node_id)
            if node and node['type'] == 'buttons' and message.button_id:
                _resume(run, _next_id(run.flow.graph, run.node_id, message.button_id), message)
            elif node and node['type'] == 'wait':
                run.context['last_message'] = message.content or ''
                var = node.get('data', {}).get('var')
                if var:
                    run.context[var] = message.content or ''
                _resume(run, _next_id(run.flow.graph, run.node_id), message)
        return True
    flow = _match_trigger(contact, message)
    if flow:
        start_flow(flow, contact)
        return True
    return False


def _pipedrive_match(cfg: dict, action: str, deal: dict, previous: dict | None) -> bool:
    stage_id, pipeline_id = cfg.get('stage_id'), cfg.get('pipeline_id')
    if pipeline_id and deal.get('pipeline_id') != pipeline_id:
        return False
    if cfg.get('event') == 'deal_added':
        return action == 'create' and (not stage_id or deal.get('stage_id') == stage_id)
    if cfg.get('event') == 'stage_changed':
        if action != 'change' or not previous or 'stage_id' not in previous:
            return False
        if not stage_id:
            return True
        return deal.get('stage_id') == stage_id and previous.get('stage_id') != stage_id
    return False


def dispatch_pipedrive_event(contact: Contact, action: str, deal: dict, previous: dict | None) -> None:
    flows = Flow.objects.filter(
        company=contact.account.company,
        account=contact.account,
        trigger=Flow.Trigger.PIPEDRIVE,
        active=True,
    )
    for flow in flows:
        if not _pipedrive_match(flow.trigger_config, action, deal, previous):
            continue
        if FlowRun.objects.filter(flow=flow, contact=contact).exists():
            continue
        ctx = {
            'deal_id': deal.get('id'),
            'deal_title': deal.get('title', ''),
            'pipeline_id': deal.get('pipeline_id'),
            'stage_id': deal.get('stage_id'),
        }
        start_flow(flow, contact, ctx)


def _resume(run: FlowRun, next_id: str, message: Message | None = None) -> None:
    if not next_id:
        return
    run.node_id = next_id
    run.status = FlowRun.Status.RUNNING
    run.save(update_fields=['node_id', 'status', 'context', 'updated_at'])
    advance(run, message)


def _match_trigger(contact: Contact, message: Message) -> Flow | None:
    account = contact.account
    text = (message.content or '').lower()
    for f in Flow.objects.filter(account=account, trigger=Flow.Trigger.KEYWORD, active=True):
        kw = (f.trigger_config.get('keyword') or '').lower()
        if kw and kw in text:
            return f
    # nós escrevemos antes (template de lead, campanha): a resposta já não é primeiro contato
    first_contact = not Message.objects.filter(contact=contact).exclude(pk=message.pk).exists()
    if first_contact:
        return Flow.objects.filter(account=account, trigger=Flow.Trigger.FIRST_MESSAGE, active=True).order_by('-id').first()
    return None


def receive_lead(token: str, payload: dict) -> tuple[Contact, bool]:
    source = LeadSource.objects.filter(token=token, active=True).select_related('account').first()
    if source is None:
        raise NotFound('fonte de lead não encontrada')
    return create_lead(source, payload)


def resume(run_id: int) -> None:
    run = FlowRun.objects.filter(pk=run_id, status=FlowRun.Status.WAITING).select_related('flow').first()
    if run is None:
        return
    node = _node(run.flow.graph, run.node_id)
    if node is None:
        return
    if node['type'] == 'delay':
        nxt = _next_id(run.flow.graph, run.node_id)
    elif node['type'] == 'wait':
        nxt = _next_id(run.flow.graph, run.node_id, 'timeout') or _next_id(run.flow.graph, run.node_id)
    else:
        return
    if nxt:
        run.node_id = nxt
        run.status = FlowRun.Status.RUNNING
        run.save(update_fields=['node_id', 'status', 'updated_at'])
        advance(run)
    else:
        run.status = FlowRun.Status.DONE
        run.save(update_fields=['status', 'updated_at'])


def validate_graph(flow: Flow) -> None:
    graph = flow.graph or {}
    start = _start_id(graph)
    if not start:
        raise ValidationError('fluxo sem bloco de início')
    first_id = _next_id(graph, start)
    if not first_id:
        raise ValidationError('ligue o início ao primeiro passo')
    if flow.trigger in (Flow.Trigger.LEAD, Flow.Trigger.PIPEDRIVE):
        first = _node(graph, first_id)
        # condição de janela na frente: o "sim" já pode falar livre, só o "não" precisa do template
        if first and first['type'] == 'condition' and first.get('data', {}).get('op') == 'window_open':
            first = _node(graph, _next_id(graph, first_id, 'nao'))
        if first is None or first['type'] != 'template':
            raise ValidationError('fluxo de lead precisa começar com um template, ou com a condição de janela de 24h e um template no "Não"')
    sources = {e['source'] for e in graph.get('edges', [])}
    for n in graph.get('nodes', []):
        # ação no fim do fluxo (pausar, etiquetar, avisar outro sistema) pode ser o último bloco
        if n['type'] not in TERMINAL and n['type'] not in ('start', 'pause', 'tag', 'http') and n['id'] not in sources:
            raise ValidationError(f'bloco "{n.get("data", {}).get("text") or n["type"]}" está sem saída ligada')
