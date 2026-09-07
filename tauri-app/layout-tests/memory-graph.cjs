// Actual browser canvas, clipping, wheel and pixel selection. No Cytoscape mock.
// Start Vite with layout-tests/vite.config.mjs; set LAYOUT_URL / PLAYWRIGHT_MODULE.
const {webkit}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const out=path.resolve(process.env.LAYOUT_EVIDENCE || '../.local-test-evidence/2026-09-06/canvas-layout');
if(!out.split(path.sep).includes('.local-test-evidence')) throw Error('Evidence must be root ignored');
fs.mkdirSync(out,{recursive:true});
(async()=>{
  const browser=await webkit.launch({headless:true,timeout:15000});
  const results=[];
  try {
    for(const [width,height] of [[1000,700],[800,560]]) {
      const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:2});
      try {
        await page.goto(process.env.LAYOUT_URL || 'http://127.0.0.1:18175');
        await page.getByRole('button',{name:'关系图',exact:true}).click();
        await page.getByRole('img').waitFor();
        await page.waitForTimeout(200);
        async function pixels(){return page.getByRole('img').evaluate(el=>{
          const host=el.getBoundingClientRect(), clip=document.getElementById('memory-scroll').getBoundingClientRect();
          let visible=0,point=null,total=0;
          for(const c of el.querySelectorAll('canvas')) {
            const r=c.getBoundingClientRect(), data=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
            for(let i=0;i<data.length;i+=4) if(data[i]>70&&data[i]<200&&data[i+1]>data[i]&&data[i+2]>180&&data[i+3]>200){
              total++;
              const x=r.x+(i/4%c.width+.5)*r.width/c.width,y=r.y+(Math.floor(i/4/c.width)+.5)*r.height/c.height;
              if(x>clip.left+2&&x<clip.right-2&&y>clip.top+2&&y<clip.bottom-2){visible++;if(!point) point={x,y};}
            }
          }
          return {total,visible,point,host:host.toJSON(),clip:clip.toJSON(),contained:host.top>=clip.top-1&&host.bottom<=clip.bottom+1};
        });}
        const initial=await pixels();
        results.push({width,height,check:'initial visible graph',pass:initial.contained&&initial.visible>20,initial});
        await page.screenshot({path:path.join(out,`${width}-initial.png`)});
        await page.locator('#memory-scroll').evaluate(el=>el.scrollTop=240);
        // Cytoscape intentionally ignores wheel during a 250ms page-scroll grace period.
        await page.waitForTimeout(350);
        const wheelBox=await pixels();
        await page.mouse.move(wheelBox.host.right-30,Math.max(wheelBox.host.top,wheelBox.clip.top)+25);
        const before=await page.locator('#memory-scroll').evaluate(el=>el.scrollTop);
        const wheelArea=(await pixels()).total;
        await page.mouse.wheel(0,-100);await page.waitForTimeout(300);
        const after=await page.locator('#memory-scroll').evaluate(el=>el.scrollTop);
        results.push({width,height,check:'wheel scrolls pane',pass:after<before&&(await pixels()).total===wheelArea,before,after});
        const filter=page.getByPlaceholder('按记忆内容筛选');
        await filter.fill('favorite_season');await page.waitForTimeout(200);
        results.push({width,height,check:'filter retains focus',pass:await filter.evaluate(el=>el===document.activeElement)});
        await page.getByRole('button',{name:'显示全图',exact:true}).click();await page.waitForTimeout(200);
        const fitted=await pixels();
        results.push({width,height,check:'fit reveals graph',pass:fitted.contained&&fitted.visible>20,fitted});
        if(fitted.point){await page.mouse.click(fitted.point.x+3,fitted.point.y+3);}
        results.push({width,height,check:'canvas pixel selection',pass:await page.getByLabel('选中记忆详情').count()===1});
        await page.screenshot({path:path.join(out,`${width}-selected.png`)});
        const panBefore=await pixels();
        const panX=panBefore.host.right-35, panY=panBefore.host.top+30;
        await page.mouse.move(panX,panY);await page.mouse.down();
        await page.mouse.move(panX-40,panY+25,{steps:8});await page.mouse.up();await page.waitForTimeout(200);
        const panAfter=await pixels();
        results.push({width,height,check:'drag pans graph',pass:!!panBefore.point&&!!panAfter.point&&Math.abs(panAfter.point.x-panBefore.point.x)>20});
        const area=(await pixels()).total;
        await page.getByRole('button',{name:'缩小',exact:true}).click();await page.waitForTimeout(200);
        const reduced=(await pixels()).total;
        results.push({width,height,check:'zoom out button works',pass:reduced<area,area,reduced});
        await page.getByRole('button',{name:'放大',exact:true}).click();await page.waitForTimeout(200);
        results.push({width,height,check:'zoom in button works',pass:(await pixels()).total>reduced});
        for(const trigger of ['Fixture producer','Fixture rebind']) {
          await page.locator('#memory-scroll').evaluate(el=>el.scrollTop=0);
          await page.getByRole('button',{name:trigger,exact:true}).click();await page.waitForTimeout(250);
          const scroll=await page.locator('#memory-scroll').evaluate(el=>el.scrollTop);
          results.push({width,height,check:trigger+' does not auto-reveal',pass:scroll===0,scroll});
        }
        await page.getByRole('button',{name:'记忆列表',exact:true}).click();
        await page.getByRole('button',{name:'关系图',exact:true}).click();await page.waitForTimeout(200);
        const reopened=await pixels();
        results.push({width,height,check:'explicit tab reopen reveals graph',pass:reopened.contained&&reopened.visible>20});
      } finally {await page.close();}
    }
  } finally {await browser.close();fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(results,null,2));}
  console.log(JSON.stringify(results.map(({width,height,check,pass})=>({width,height,check,pass})),null,2));
  assert(results.every(r=>r.pass),'Layout regression failed; see ignored results.json');
})();
