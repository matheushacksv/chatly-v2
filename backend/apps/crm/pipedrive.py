import httpx

from core.exceptions import NotFound, PipedriveError

V1_URL = 'https://api.pipedrive.com/v1'
V2_URL = 'https://api.pipedrive.com/api/v2'


def _req(api_key: str, method: str, url: str, *, params: dict | None = None, json: dict | None = None) -> dict:
    try:
        response = httpx.request(method, url, headers={'x-api-token': api_key}, params=params, json=json, timeout=10)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 401:
            raise PipedriveError('invalid Pipedrive api key')
        if code == 404:
            raise NotFound('not found on Pipedrive')
        if code == 429:
            raise PipedriveError('Pipedrive rate limit reached, try again soon')
        raise PipedriveError(f'Pipedrive returned {code}')
    except httpx.RequestError:
        raise PipedriveError('could not reach Pipedrive')
    return response.json()


def _paginate(api_key: str, url: str, params: dict | None = None) -> list[dict]:
    items: list[dict] = []
    cursor = None
    while True:
        page_params = {'limit': 100, **(params or {})}
        if cursor:
            page_params['cursor'] = cursor
        body = _req(api_key, 'GET', url, params=page_params)
        items.extend(body.get('data') or [])
        cursor = (body.get('additional_data') or {}).get('next_cursor')
        if not cursor:
            return items


def validate(api_key: str) -> dict:
    return _req(api_key, 'GET', f'{V1_URL}/users/me')['data']


def list_pipelines(api_key: str) -> list[dict]:
    return _paginate(api_key, f'{V2_URL}/pipelines')


def list_stages(api_key: str) -> list[dict]:
    return [s for s in _paginate(api_key, f'{V2_URL}/stages') if not s.get('is_deleted')]


def search_persons(api_key: str, term: str, limit: int = 10) -> list[dict]:
    body = _req(api_key, 'GET', f'{V2_URL}/persons/search', params={'term': term, 'fields': 'name,phone', 'limit': limit})
    return [item['item'] for item in (body.get('data') or {}).get('items') or []]


def get_person(api_key: str, person_id: int) -> dict:
    return _req(api_key, 'GET', f'{V2_URL}/persons/{person_id}')['data']


def create_person(api_key: str, name: str, phone: str) -> dict:
    payload: dict = {'name': name}
    if phone:
        payload['phones'] = [{'value': phone, 'primary': True}]
    return _req(api_key, 'POST', f'{V2_URL}/persons', json=payload)['data']


def list_open_deals(api_key: str, person_id: int) -> list[dict]:
    return _paginate(api_key, f'{V2_URL}/deals', {'person_id': person_id, 'status': 'open'})


def create_deal(api_key: str, title: str, person_id: int, pipeline_id: int, stage_id: int, value: float | None = None) -> dict:
    payload: dict = {'title': title, 'person_id': person_id, 'pipeline_id': pipeline_id, 'stage_id': stage_id}
    if value:
        payload['value'] = value
    return _req(api_key, 'POST', f'{V2_URL}/deals', json=payload)['data']


def update_deal(api_key: str, deal_id: int, *, stage_id: int | None = None, status: str | None = None) -> dict:
    payload: dict = {}
    if stage_id:
        payload['stage_id'] = stage_id
    if status:
        payload['status'] = status
    return _req(api_key, 'PATCH', f'{V2_URL}/deals/{deal_id}', json=payload)['data']


def list_notes(api_key: str, deal_id: int) -> list[dict]:
    body = _req(api_key, 'GET', f'{V1_URL}/notes', params={'deal_id': deal_id, 'sort': 'add_time DESC'})
    return body.get('data') or []


def create_note(api_key: str, content: str, *, deal_id: int | None = None, person_id: int | None = None) -> dict:
    payload: dict = {'content': content}
    if deal_id:
        payload['deal_id'] = deal_id
    if person_id:
        payload['person_id'] = person_id
    return _req(api_key, 'POST', f'{V1_URL}/notes', json=payload)['data']


def list_open_activities(api_key: str, deal_id: int) -> list[dict]:
    body = _req(api_key, 'GET', f'{V1_URL}/deals/{deal_id}/activities', params={'done': 0, 'limit': 20})
    return body.get('data') or []


def mark_activity_done(api_key: str, activity_id: int) -> dict:
    return _req(api_key, 'PATCH', f'{V2_URL}/activities/{activity_id}', json={'done': True})['data']
