# -*- coding: utf-8 -*-
"""AC-2③: provider_invocations.request_json must not expose workflow_spawn nor the catalog prompt."""
import sqlite3, json, sys, collections
d = sys.argv[1]  # <E>/userdata/data
c = sqlite3.connect(f'{d}/simple-harness-sdk/execution-v6.sqlite3')
rows = c.execute('select request_json from provider_invocations').fetchall()
names = collections.Counter(); ws = 0; prompt = 0
for (j,) in rows:
    o = json.loads(j)
    for t in o.get('tools') or []:
        names[t['function']['name'] if 'function' in t else t.get('name')] += 1
    ws += j.count('workflow_spawn')
    prompt += j.count('Execution profiles are model-selected')
delivered = {'context_page_in','context_route','file_write','procedure_discover','procedure_use','prospective_ack','task_scope_search','task_scope_update','todo_complete','todo_write','tool_activate','tool_describe','tool_search','write_file'}
print(json.dumps({'calls': len(rows), 'workflow_spawn': ws, 'catalog_prompt': prompt, 'tool_names': sorted(names), 'subset_of_delivered': set(names) <= delivered}, ensure_ascii=False))
assert len(rows) >= 1 and ws == 0 and prompt == 0 and 'workflow_spawn' not in names
print('AC-2(3) PASS')
