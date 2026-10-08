"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

import re
from copy import deepcopy
from hashlib import sha256
from typing import Any


def retry_messages(messages: list[dict[str, Any]], error: Exception) -> list[dict[str, Any]] | None:
    detail = str(error)
    if not (
        'invalid_id_prefix' in detail
        and re.search(r"Expected an ID that begins with\s+(['\"])fc_?\1", detail)
    ):
        return None
    ids = {
        call['id']
        for message in messages if message.get('role') == 'assistant'
        for call in message.get('tool_calls', [])
        if isinstance(call.get('id'), str)
    }
    mapping: dict[str, str] = {}
    for call_id in sorted(ids):
        if call_id.startswith('fc_'):
            continue
        candidate = 'fc_' + sha256(call_id.encode()).hexdigest()[:32]
        while candidate in ids or candidate in mapping.values():
            candidate = 'fc_' + sha256(candidate.encode()).hexdigest()[:32]
        mapping[call_id] = candidate
    if not mapping:
        return None
    result = deepcopy(messages)
    for message in result:
        if message.get('role') == 'assistant':
            for call in message.get('tool_calls', []):
                if call.get('id') in mapping:
                    call['id'] = mapping[call['id']]
        elif message.get('role') == 'tool':
            call_id = message.get('tool_call_id')
            if call_id in mapping:
                message['tool_call_id'] = mapping[call_id]
    return result
