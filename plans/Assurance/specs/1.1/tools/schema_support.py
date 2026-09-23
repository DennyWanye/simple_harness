"""Dependency-free validator for the explicitly used JSON Schema subset.
Not a replacement for Draft 2020-12 meta-validation. Rejects unsupported validation keywords.
"""
from pathlib import Path
import json,re,copy,hashlib

ANNOTATIONS={'$schema','$id','$defs','title','description','examples','$comment'}
SUPPORTED={'$ref','type','properties','required','additionalProperties','const','enum','oneOf','anyOf','allOf','items','minItems','maxItems','uniqueItems','minimum','maximum','minLength','maxLength','pattern','if','then','else'}

def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def pointer(doc,path):
 for x in path.lstrip('/').split('/') if path else []:
  key=x.replace('~1','/').replace('~0','~')
  doc=doc[int(key)] if isinstance(doc,list) else doc[key]
 return doc

def ref_target(root,file,ref):
 target,_,ptr=ref.partition('#');p=(file.parent/target).resolve() if target else file.resolve()
 if not p.is_relative_to(root.resolve()):raise ValueError('SCHEMA_REF_ESCAPE')
 return p,pointer(json.loads(p.read_text()),ptr)

def expanded(root,file,node,seen=()):
 if isinstance(node,list):return [expanded(root,file,x,seen) for x in node]
 if not isinstance(node,dict):return node
 if '$ref' in node:
  ident=(str(file),node['$ref'])
  if ident in seen:return {'recursive_ref':node['$ref']}
  p,n=ref_target(root,file,node['$ref'])
  out={'resolved':expanded(root,p,n,seen+(ident,))}
  out.update({k:expanded(root,file,v,seen) for k,v in node.items() if k!='$ref'})
  return out
 return {k:expanded(root,file,v,seen) for k,v in node.items()}

def validate(root,file,node,value,path='$'):
 unknown=set(node)-ANNOTATIONS-SUPPORTED
 if unknown:raise ValueError('UNSUPPORTED_SCHEMA_KEYWORD:'+str(unknown))
 if '$ref' in node:
  p,n=ref_target(root,file,node['$ref']);validate(root,p,n,value,path)
 for key in ('allOf','oneOf','anyOf'):
  if key in node:
   hits=0
   for x in node[key]:
    try:validate(root,file,x,value,path);hits+=1
    except ValueError:pass
   if (key=='allOf' and hits!=len(node[key])) or (key=='oneOf' and hits!=1) or (key=='anyOf' and hits==0):raise ValueError(path+':'+key)
 if 'if' in node:
  try:validate(root,file,node['if'],value,path);branch='then'
  except ValueError:branch='else'
  if branch in node:validate(root,file,node[branch],value,path)
 if 'const' in node and (value!=node['const'] or type(value)!=type(node['const'])):raise ValueError(path+':const')
 if 'enum' in node and not any(type(value)==type(x) and value==x for x in node['enum']):raise ValueError(path+':enum')
 typ=node.get('type');types={'object':dict,'array':list,'string':str,'integer':int,'boolean':bool,'null':type(None),'number':(int,float)}
 if typ:
  variants=typ if isinstance(typ,list) else [typ]
  ok=False
  for t in variants:
   want=types[t]
   ok=ok or (type(value) in want if isinstance(want,tuple) else type(value) is want)
  if not ok:raise ValueError(path+':type')
 if isinstance(value,dict):
  if not set(node.get('required',[]))<=set(value):raise ValueError(path+':required')
  props=node.get('properties',{})
  if node.get('additionalProperties') is False and set(value)-set(props):raise ValueError(path+':unknown')
  for k,n in props.items():
   if k in value:validate(root,file,n,value[k],path+'/'+k)
 if isinstance(value,list):
  if len(value)<node.get('minItems',0) or len(value)>node.get('maxItems',10**9):raise ValueError(path+':items-bound')
  if node.get('uniqueItems') and len({digest(v) for v in value})!=len(value):raise ValueError(path+':duplicate')
  if 'items' in node:
   for i,v in enumerate(value):validate(root,file,node['items'],v,path+'/'+str(i))
 if isinstance(value,str):
  if len(value)<node.get('minLength',0) or len(value)>node.get('maxLength',10**9):raise ValueError(path+':length')
  if 'pattern' in node and not re.search(node['pattern'],value):raise ValueError(path+':pattern')
 if type(value) in (int,float):
  if value<node.get('minimum',float('-inf')) or value>node.get('maximum',float('inf')):raise ValueError(path+':numeric-bound')

def example(root,file,n,level=0):
 if level>80:raise ValueError('RECURSIVE_EXAMPLE')
 if '$ref' in n:
  p,x=ref_target(root,file,n['$ref']);return example(root,p,x,level+1)
 if 'const' in n:return n['const']
 if 'enum' in n:return n['enum'][0]
 if 'allOf' in n and 'type' not in n:
  v=example(root,file,n['allOf'][0],level+1)
  for a in n['allOf'][1:]:
   for k,x in a.get('properties',{}).items():v[k]=example(root,file,x,level+1)
  return v
 for key in ('oneOf','anyOf'):
  if key in n:return example(root,file,n[key][0],level+1)
 t=n.get('type')
 if isinstance(t,list):t=t[0]
 if t=='object':
  v={k:example(root,file,x,level+1) for k,x in n.get('properties',{}).items()}
  for a in n.get('allOf',[]):
   if 'if' in a:
    try:validate(root,file,a['if'],v);b=a.get('then',{})
    except ValueError:b=a.get('else',{})
    for k,x in b.get('properties',{}).items():v[k]=example(root,file,x,level+1)
  return v
 if t=='array':return [example(root,file,n['items'],level+1) for _ in range(n.get('minItems',0))]
 if t=='string':
  if n.get('pattern')=='^[0-9a-f]{64}$':return 'a'*64
  if n.get('pattern')=='^ev-[0-9a-f]{64}$':return 'ev-'+'a'*64
  return 'x'*max(1,n.get('minLength',1))
 if t in ('integer','number'):return n.get('minimum',0)
 if t=='boolean':return False
 if t=='null':return None
 raise ValueError('NO_EXAMPLE:'+str(n))

def field_rows(root):
 rows=[]
 rules=json.loads((root/'implementation/schema-source-rules.json').read_text())
 for file in sorted((root/'contracts').glob('*.schema.json')):
  def visit(n,ptr):
   if isinstance(n,dict):
    for field,s in n.get('properties',{}).items():
     p=ptr+'/properties/'+field.replace('~','~0').replace('/','~1')
     rows.append({'schema_file':str(file.relative_to(root)),'json_pointer':p,'resolved_subtree_hash':digest(expanded(root,file,s)),'producer_rule':rules['field_specific'].get(field,next((owner for prefix,owner in sorted(rules['schema_owners'].items(),key=lambda x:-len(x[0])) if file.stem.startswith(prefix)),rules['default_field_owner'])),'semantic_contract':'ASSURANCE-EXEC-1.1.zh-CN.md §§3–12'})
    for k,v in n.items():visit(v,ptr+'/'+k.replace('~','~0').replace('/','~1'))
   elif isinstance(n,list):
    for i,x in enumerate(n):visit(x,ptr+'/'+str(i))
  visit(json.loads(file.read_text()),'')
 return rows
