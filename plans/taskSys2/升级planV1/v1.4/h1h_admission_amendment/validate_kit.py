"""Validate this specification kit only; never reads or mutates the user's repo."""
from pathlib import Path
import ast
import json
import re


def main() -> None:
    root = Path(__file__).resolve().parent
    required = [
        'AMENDMENT.zh-CN.md','README.zh-CN.md','sources.json',
        'sdk-test-cases.json','reference/rules.py','reference/schema.sql',
        'tests/test_reference.py','VALIDATION.md',
    ]
    for name in required:
        if not (root / name).is_file():
            raise AssertionError(f'missing required file: {name}')
    document = (root / 'AMENDMENT.zh-CN.md').read_text(encoding='utf-8')
    fences = [line for line in document.splitlines() if line.startswith('```')]
    assert len(fences) % 2 == 0, 'unpaired Markdown fence'
    sources = json.loads((root / 'sources.json').read_text(encoding='utf-8'))
    source_ids = {s['id'] for s in sources['sources']}
    for ref in re.findall(r'\[((?:S|W|H)\d+)\]', document):
        assert ref in source_ids, f'missing source identity: {ref}'
    data = json.loads((root / 'sdk-test-cases.json').read_text(encoding='utf-8'))
    cases = data['cases']
    expected = {f'{prefix}{n:02}' for prefix, end in [('A',8),('O',10),('P',10),('I',8)] for n in range(1,end+1)}
    assert {c['id'] for c in cases} == expected and len(cases) == len(expected)
    assert all(c['status'] == 'NOT_RUN' for c in cases), 'SDK cases were not executed here'
    assert all(c['required_assertions'] for c in cases)
    for name in ('reference/rules.py','tests/test_reference.py','validate_kit.py'):
        ast.parse((root / name).read_text(encoding='utf-8'), filename=name)
    ddl = (root / 'reference/schema.sql').read_text(encoding='utf-8')
    tables = re.findall(r'CREATE TABLE (\w+)', ddl)
    assert tables == ['planning_lane_grants','planning_request_authority_bindings',
                      'planning_operation_action_links','planning_admission_checks']
    assert not re.search(r'\b(?:DROP|DELETE|UPDATE)\b', ddl, re.I), 'reference DDL must be additive'
    print(json.dumps({'kit':'H1H-ADM-1.0','validation':'PASS',
                      'sdk_case_groups':len(cases),'sdk_cases_run':0,
                      'source_entries':len(source_ids),'proposed_new_tables':len(tables),
                      'production_changes':False},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
