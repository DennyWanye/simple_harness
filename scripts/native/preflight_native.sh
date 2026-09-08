#!/bin/bash
# Pre-flight for a native journey (2026-09-08 lesson: three A6 aborts were all
# catchable before launch). Prints PASS/WARN/FAIL lines; exit 1 on any FAIL.
# Usage: preflight_native.sh <env-file> <model> [port=18120] [expect-window=32000] [userdata-dir]
# Never prints the API key.
set -u
ENVF="${1:?env file}"; MODEL="${2:?model}"; PORT="${3:-18120}"; WANT_WIN="${4:-32000}"; USERDATA="${5:-}"
fail=0
say() { printf '%-5s %s\n' "$1" "$2"; [ "$1" = FAIL ] && fail=1; return 0; }

# 1. port / leftovers
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then say FAIL "port $PORT busy: $(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | awk 'NR>1{print $1"/"$2}' | tr '\n' ' ')"; else say PASS "port $PORT free"; fi
n=$(pgrep -f 'launch_native_candidate.py|a6_driver.sh|run_corpus_batch.py' | wc -l | tr -d ' '); [ "$n" = 0 ] && say PASS "no launcher/driver/batch running" || say FAIL "$n launcher/driver/batch processes running"

# 2. screen unlocked (System Events must enumerate a window of a GUI app)
if python3 - <<'PY'
import subprocess,sys
out=subprocess.run(["osascript","-e",'tell application "System Events" to get name of every process whose frontmost is true'],capture_output=True,text=True)
sys.exit(0 if out.returncode==0 and out.stdout.strip() else 1)
PY
then say PASS "screen unlocked (frontmost process visible)"; else say FAIL "screen locked or GUI scripting unavailable"; fi

# 3. provider smoke: primary chat + analysis-shaped tool call (arguments must parse)
python3 - "$ENVF" "$MODEL" <<'PY' || fail=1
import json,sys,urllib.request,urllib.error
envf,model=sys.argv[1:3]; env={}
for line in open(envf):
    line=line.strip()
    if '=' in line and not line.startswith('#'):
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
base=env['BASEURL'].rstrip('/'); url=base+'/chat/completions'
def call(body):
    q=urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+env['APIKEY'],'Content-Type':'application/json'})
    return json.loads(urllib.request.urlopen(q,timeout=120).read())
try:
    r=call({'model':model,'messages':[{'role':'user','content':'只回复“收到”。'}],'max_tokens':20})
    print('PASS  provider chat ok: finish=%s' % r['choices'][0].get('finish_reason'))
except Exception as e:
    print('FAIL  provider chat: %s' % type(e).__name__); sys.exit(1)
tool={'type':'function','function':{'name':'submit','description':'提交结构化结果','parameters':{'type':'object','properties':{'facts':{'type':'array','items':{'type':'object','properties':{'subject':{'type':'string'},'value':{'type':'string'}},'required':['subject','value']}},'closure_reason':{'type':'string'}},'required':['facts','closure_reason']}}}
bad=0
for i in range(3):
    try:
        r=call({'model':model,'messages':[{'role':'user','content':'从下面文本抽取事实并用 submit 提交：我做资料校对时统一用 Python 3.12；校对结果存到「外接硬盘 / 校对归档」。'}],'tools':[tool],'tool_choice':'auto','max_tokens':600})
        tcs=r['choices'][0]['message'].get('tool_calls') or []
        if not tcs: print('WARN  attempt %d: model answered without a tool call' % (i+1)); continue
        a=tcs[0]['function']['arguments']; json.loads(a)
    except json.JSONDecodeError: bad+=1
    except urllib.error.HTTPError as e:
        body=e.read()[:300].decode('utf-8','replace')
        print('FAIL  provider tool call: HTTP %s %s' % (e.code, body.replace('\n',' '))); sys.exit(1)
    except Exception as e:
        print('FAIL  provider tool call: %s' % type(e).__name__); sys.exit(1)
print(('WARN ' if bad else 'PASS ') + ' analysis-shaped tool calls: %d/3 arguments unparseable (Host repair handles trailing brace)' % bad)
PY

# 4. window override present when a userdata dir is given
if [ -n "$USERDATA" ]; then
  f="$USERDATA/model_overrides.toml"
  if [ -f "$f" ] && grep -q "\"$MODEL\"" "$f" && grep -q "context_window = $WANT_WIN" "$f"; then say PASS "model_overrides.toml pins $MODEL to $WANT_WIN"; else say FAIL "model_overrides.toml missing/incorrect at $f (want [models.\"$MODEL\"] context_window = $WANT_WIN)"; fi
fi

# 5. disk / memory headroom
free_gb=$(df -g / | awk 'NR==2{print $4}'); [ "$free_gb" -ge 10 ] && say PASS "disk free ${free_gb}G" || say WARN "disk free only ${free_gb}G"
exit $fail
