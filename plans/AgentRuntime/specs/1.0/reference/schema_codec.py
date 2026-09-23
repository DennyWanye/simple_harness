"""Strict executable validator for this bundle's deliberately small Schema subset.
Production reuses the SDK codecs; this is not a general JSON Schema implementation.
"""
import json,math,re
from pathlib import Path
from .runtime_rules import RuleError,parse_strict
ROOT=Path(__file__).resolve().parents[1]
SCHEMA=json.loads((ROOT/'contracts/runtime-plane.schema.json').read_text())
def validate(value,spec):
    if '$ref' in spec:return validate(value,SCHEMA['$defs'][spec['$ref'].split('/')[-1]])
    for keyword in ('oneOf','anyOf'):
        if keyword in spec:
            passed=0
            for sub in spec[keyword]:
                try:validate(value,sub);passed+=1
                except RuleError:pass
            if passed<1 or (keyword=='oneOf' and passed!=1):raise RuleError('UNION_MISMATCH')
            return
    if 'const' in spec and (type(value)!=type(spec['const']) or value!=spec['const']):raise RuleError('CONST')
    if 'enum' in spec and not any(type(value)==type(v) and value==v for v in spec['enum']):raise RuleError('ENUM')
    types=spec.get('type',[]);types=[types] if isinstance(types,str) else types
    pred={'null':lambda:type(value) is type(None),'boolean':lambda:type(value) is bool,'integer':lambda:type(value) is int,'number':lambda:type(value) in (int,float) and math.isfinite(value),'string':lambda:type(value) is str,'array':lambda:type(value) is list,'object':lambda:type(value) is dict}
    if types and not any(pred[t]() for t in types):raise RuleError('TYPE')
    if type(value) is dict:
        p=spec.get('properties',{})
        if not set(spec.get('required',()))<=value.keys():raise RuleError('MISSING_FIELD')
        for k,v in value.items():
            if k in p:validate(v,p[k])
            elif spec.get('additionalProperties',True) is False:raise RuleError('UNKNOWN_FIELD')
            elif isinstance(spec.get('additionalProperties'),dict):validate(v,spec['additionalProperties'])
    elif type(value) is list:
        if len(value)>spec.get('maxItems',10**9) or len(value)<spec.get('minItems',0):raise RuleError('ARRAY_LIMIT')
        if spec.get('uniqueItems') and len({json.dumps(v,sort_keys=True) for v in value})!=len(value):raise RuleError('DUPLICATE_ITEM')
        for v in value:validate(v,spec.get('items',{}))
    elif type(value) is str:
        if len(value)>spec.get('maxLength',10**9) or len(value)<spec.get('minLength',0):raise RuleError('STRING_LIMIT')
        if 'pattern' in spec and not re.search(spec['pattern'],value):raise RuleError('STRING_PATTERN')
    elif type(value) in (int,float):
        if not math.isfinite(value) or value<spec.get('minimum',float('-inf')) or value>spec.get('maximum',float('inf')):raise RuleError('NUMBER_LIMIT')
def decode(name,raw):
    value=parse_strict(raw,max_bytes=8*1024*1024 if name=='ContextManifest' else 262144)
    validate(value,SCHEMA['$defs'][name]);return value
