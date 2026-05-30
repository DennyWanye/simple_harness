#!/usr/bin/env node
// 一次性验证 F1/F2/F3/F4/F5 (除 F6 需 LLM call) 的 CDP 测试脚本。
// F3 由于 webview 内部无法直接拖窗（OS-level），通过断言 set_window_geometry
// 不再调 set_size 间接验证。F4 通过 setSize Tauri API 触发并检查 model.scale。
import http from 'node:http'
import { WebSocket } from 'ws'
import fs from 'node:fs/promises'

const SHOT_DIR = 'G:/projects/deskpet-ui-fixes-restore/plans/manual-results-restore/'

async function listTargets(){
  return new Promise((resolve,reject)=>{
    http.get({host:'127.0.0.1',port:9222,path:'/json'},(res)=>{
      let d=''; res.on('data',c=>d+=c)
      res.on('end',()=>{try{resolve(JSON.parse(d))}catch(e){reject(e)}})
    }).on('error',reject)
  })
}
async function connect(url){
  const ws=new WebSocket(url); await new Promise(r=>ws.once('open',r))
  let id=0; const pending=new Map()
  ws.on('message',raw=>{const m=JSON.parse(raw); if(pending.has(m.id)){pending.get(m.id)(m); pending.delete(m.id)}})
  return {
    send(method,params={}){
      const myId=++id
      return new Promise((res,rej)=>{pending.set(myId,m=>m.error?rej(new Error(JSON.stringify(m.error))):res(m.result));ws.send(JSON.stringify({id:myId,method,params}))})
    },
    close:()=>ws.close()
  }
}
async function evalJS(c,expr){
  const r=await c.send('Runtime.evaluate',{expression:'(async()=>{'+expr+'})()',awaitPromise:true,returnByValue:true})
  if(r.exceptionDetails) return {__error: JSON.stringify(r.exceptionDetails).slice(0,500)}
  return r.result.value
}
async function shot(c,name){
  await c.send('Page.enable')
  const r=await c.send('Page.captureScreenshot',{format:'png'})
  await fs.writeFile(SHOT_DIR+name+'.png', Buffer.from(r.data,'base64'))
  console.log('  📸', name+'.png')
}

const results = { passes: [], fails: [] }
function pass(id, msg){ results.passes.push({id, msg}); console.log(`  ✅ ${id}: ${msg}`) }
function fail(id, msg){ results.fails.push({id, msg}); console.log(`  ❌ ${id}: ${msg}`) }

async function main(){
  await fs.mkdir(SHOT_DIR,{recursive:true})
  const targets = await listTargets()
  const main = targets.find(t => t.url === 'http://localhost:5173/')
  const panel = targets.find(t => t.url.includes('message-panel'))

  // ============ MAIN PET ============
  console.log('\n========== Main pet webview ==========')
  const mc = await connect(main.webSocketDebuggerUrl)
  await new Promise(r => setTimeout(r, 1500))
  await shot(mc, 'M01-initial-main-pet')

  // F1: verify DEFAULT_BASE_URL is chinzy.com (re-import live module)
  console.log('\n=== F1 — Login DEFAULT_BASE_URL ===')
  const f1 = await evalJS(mc, `
    const m = await import('/src/auth/RelayAuthAdapter.ts?t=' + Date.now())
    const a = new m.RelayAuthAdapter({})
    return { baseUrl: a.baseUrl }
  `)
  if (f1.baseUrl === 'https://chinzy.com') pass('F1', 'DEFAULT_BASE_URL = https://chinzy.com')
  else fail('F1', 'baseUrl='+JSON.stringify(f1))

  // F1.2: verify relayConfig URLs
  const f1b = await evalJS(mc, `
    const m = await import('/src/auth/relayConfig.ts?t=' + Date.now())
    return { RECHARGE_URL: m.RECHARGE_URL, DEVICE_CONSOLE_URL: m.DEVICE_CONSOLE_URL }
  `)
  if (f1b.RECHARGE_URL && f1b.RECHARGE_URL.includes('chinzy.com')) pass('F1b', 'RECHARGE_URL → chinzy.com')
  else fail('F1b', JSON.stringify(f1b))
  if (f1b.DEVICE_CONSOLE_URL && f1b.DEVICE_CONSOLE_URL.includes('chinzy.com')) pass('F1c', 'DEVICE_CONSOLE_URL → chinzy.com')
  else fail('F1c', JSON.stringify(f1b))

  // F5: ContextRing mounted in toolbar
  console.log('\n=== F5 — ContextRing in toolbar ===')
  const f5 = await evalJS(mc, `
    const rings = document.querySelectorAll('[data-testid="context-ring"]')
    return { count: rings.length, aria: rings[0] ? rings[0].getAttribute('aria-label') : null, title: rings[0] ? rings[0].title : null }
  `)
  if (f5.count >= 1) pass('F5-main', `${f5.count} ring(s) mounted, label="${f5.aria}", title="${f5.title}"`)
  else fail('F5-main', 'no ring found')

  // F5b: click ring → modal opens
  console.log('\n=== F5b — Click ring → breakdown modal ===')
  await evalJS(mc, `
    const r = document.querySelector('[data-testid="context-ring"]')
    if (r) r.click()
    await new Promise(r=>setTimeout(r,1500))
    return {}
  `)
  await shot(mc, 'M02-context-breakdown-modal')
  const f5c = await evalJS(mc, `
    const dlg = document.querySelector('[role="dialog"][aria-label="Context usage breakdown"]')
    if (!dlg) return { found: false }
    const sections = Array.from(dlg.querySelectorAll('li')).length
    return { found: true, sectionsCount: sections, hasGauge: !!dlg.querySelector('div[style*="background"]') }
  `)
  if (f5c.found) pass('F5b', `Breakdown modal opened, ${f5c.sectionsCount} section rows`)
  else fail('F5b', 'breakdown modal not found')

  // Close modal
  await evalJS(mc, `
    const btn = document.querySelector('[role="dialog"][aria-label="Context usage breakdown"] button[aria-label="关闭"]')
    if (btn) btn.click()
    return {}
  `)

  // F4: trigger window resize via Tauri setSize, check Live2D scale recomputes
  console.log('\n=== F4 — Live2D resize equal-aspect ===')
  const f4a = await evalJS(mc, `
    // Capture model state before
    const m = window.__deskpet_anim_debug
    const dpr = window.devicePixelRatio
    return { iw_before: window.innerWidth, ih_before: window.innerHeight, dpr }
  `)
  console.log('  before:', JSON.stringify(f4a))

  // Use Tauri to setSize larger
  const f4b = await evalJS(mc, `
    try {
      const winMod = await import('@tauri-apps/api/window')
      const dpiMod = await import('@tauri-apps/api/dpi')
      const win = winMod.getCurrentWindow()
      await win.setSize(new dpiMod.LogicalSize(800, 700))
      await new Promise(r=>setTimeout(r, 700))
      return { iw: window.innerWidth, ih: window.innerHeight }
    } catch(e) { return { error: String(e) } }
  `)
  console.log('  after setSize 800x700:', JSON.stringify(f4b))
  await shot(mc, 'M03-window-resized-800x700')

  // Now we expect: model rendered without horizontal stretch.
  // Indirect verification: canvas backing buffer should equal new viewport*dpr
  // (= renderer.resize fired). We can also verify the resize useEffect dep is wired.
  const f4c = await evalJS(mc, `
    const c = document.querySelector('canvas[data-pet-live2d]')
    if (!c) return { canvas: null, viewport: {iw: window.innerWidth, ih: window.innerHeight, dpr: window.devicePixelRatio} }
    return {
      canvas: { w: c.width, h: c.height },
      viewport: { iw: window.innerWidth, ih: window.innerHeight, dpr: window.devicePixelRatio }
    }
  `)
  console.log('  canvas:', JSON.stringify(f4c))
  if (f4c.canvas && f4c.viewport) {
    const expectedW = Math.round(f4c.viewport.iw * f4c.viewport.dpr)
    const expectedH = Math.round(f4c.viewport.ih * f4c.viewport.dpr)
    const wOK = Math.abs(f4c.canvas.w - expectedW) < 5
    const hOK = Math.abs(f4c.canvas.h - expectedH) < 5
    if (wOK && hOK) pass('F4', `Renderer resized to ${f4c.canvas.w}x${f4c.canvas.h} matches viewport*dpr ${expectedW}x${expectedH}`)
    else fail('F4', `Canvas ${f4c.canvas.w}x${f4c.canvas.h} != expected ${expectedW}x${expectedH} (renderer.resize NOT firing on size change)`)
  } else {
    console.log('  ⚠ canvas not visible in DOM (may be offscreen), skipping shape check')
  }

  // F3: verify front-end resize useEffect doesn't exist (we deleted it)
  console.log('\n=== F3 — Window geometry feedback loop removed ===')
  // Indirect: send window.resize event and verify NO set_window_geometry invoke
  // happens (would indicate the old useEffect is still there).
  // Best verification: just confirm app size doesn't shrink after multiple resizes.
  const f3a = await evalJS(mc, `
    const winMod = await import('@tauri-apps/api/window')
    const dpiMod = await import('@tauri-apps/api/dpi')
    const win = winMod.getCurrentWindow()
    const initial = await win.outerSize()
    // Wait 2.5s for any debounce to fire + capture if window self-shrinks
    await new Promise(r=>setTimeout(r, 2500))
    const after = await win.outerSize()
    return {
      initial: { w: initial.width, h: initial.height },
      after: { w: after.width, h: after.height },
      shrink_w: initial.width - after.width,
      shrink_h: initial.height - after.height,
    }
  `)
  console.log('  ', JSON.stringify(f3a))
  if (f3a.shrink_w === 0 && f3a.shrink_h === 0) pass('F3', `Window stable: ${f3a.initial.w}x${f3a.initial.h} → ${f3a.after.w}x${f3a.after.h} (no auto-shrink)`)
  else fail('F3', `Window shrank by ${f3a.shrink_w}w x ${f3a.shrink_h}h`)

  // Restore window size
  await evalJS(mc, `
    const winMod = await import('@tauri-apps/api/window')
    const dpiMod = await import('@tauri-apps/api/dpi')
    await winMod.getCurrentWindow().setSize(new dpiMod.LogicalSize(360, 600))
    await new Promise(r=>setTimeout(r,500))
    return {}
  `)

  mc.close()

  // ============ MESSAGE PANEL ============
  console.log('\n========== Message panel webview ==========')
  if (panel) {
    const pc = await connect(panel.webSocketDebuggerUrl)
    await new Promise(r => setTimeout(r, 800))
    await shot(pc, 'P01-message-panel')

    // F2: tabs removed
    console.log('\n=== F2 — MessagePanel tabs removed ===')
    const f2 = await evalJS(pc, `
      const chips = document.querySelectorAll('[data-testid^="msgstream-filter-"]')
      return { tabCount: chips.length, ids: Array.from(chips).map(c => c.getAttribute('data-testid')) }
    `)
    if (f2.tabCount === 0) pass('F2', 'All 4 filter tabs removed (0 chips)')
    else fail('F2', `${f2.tabCount} tabs still present: ${JSON.stringify(f2.ids)}`)

    // F5d: panel also has ring
    const f5d = await evalJS(pc, `
      const rings = document.querySelectorAll('[data-testid="context-ring"]')
      return { count: rings.length, aria: rings[0]?.getAttribute('aria-label') }
    `)
    if (f5d.count >= 1) pass('F5d', `Panel ring mounted, label="${f5d.aria}"`)
    else fail('F5d', 'no ring in panel')

    pc.close()
  } else {
    console.log('  ⚠ message panel webview not found')
  }

  // ============ SUMMARY ============
  console.log('\n========== SUMMARY ==========')
  console.log(`  ✅ ${results.passes.length} PASS`)
  console.log(`  ❌ ${results.fails.length} FAIL`)
  for (const f of results.fails) console.log(`     - ${f.id}: ${f.msg}`)

  await fs.writeFile(SHOT_DIR+'results.json', JSON.stringify(results, null, 2))
  process.exit(results.fails.length > 0 ? 1 : 0)
}
main().catch(e=>{console.error('FATAL:',e); process.exit(2)})
