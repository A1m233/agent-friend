import { createRequire } from 'node:module';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('playwright');
const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
try {
  const pages = browser.contexts().flatMap(c=>c.pages());
  console.log('TARGETS',pages.map(p=>({url:p.url()})));
  const page=pages.find(p=>new URL(p.url()).pathname==='/pet.html');
  if(!page) throw Error('No pet WebView');
  console.log('STATE',JSON.stringify(await page.evaluate(()=>{
    const canvas=document.querySelector('canvas');
    const root=document.querySelector('#root');
    const host=root?.firstElementChild;
    const fiberKey=host&&Object.keys(host).find(k=>k.startsWith('__reactFiber$'));
    let fiber=fiberKey?host[fiberKey]:null;
    while(fiber?.return)fiber=fiber.return;
    const currentRoot=fiber?.stateNode?.current??fiber;
    const stack=[currentRoot];fiber=null;
    while(stack.length){const n=stack.pop();if(!n)continue;if(n.type?.name==='C_'){fiber=n;break;}stack.push(n.sibling,n.child);}
    const chain=[]; const details=[];
    while(fiber){
      const hooks=[]; let h=fiber.memoizedState; let i=0;
      while(h && i<100){
        const s=h.memoizedState;
        if (s && typeof s==='object' && ['x','y','w','h'].every(k=>typeof s[k]==='number')) details.push({i,rect:s});
        if(i===12) details.push({i,webviewDpr:s});
        if(Array.isArray(s)&&s[0]?.dpr)details.push({i,displayContext:s[0]});
        if(s?.current?.alphaHitTest && s.current.spriteScreen) {
          const b=s.current.spriteScreen;
          const samples=[];
          for(let y=0.1;y<1;y+=0.2) for(let x=0.1;x<1;x+=0.2) {
            const p={x:b.x+b.w*x,y:b.y+b.h*y};samples.push({...p,hit:s.current.alphaHitTest(p)});
          }
          details.push({i,dragging:s.current.isDragging,bounds:b,samples});
        }
        const value=s===null||['string','number','boolean'].includes(typeof s)?s:
          s?.current!==undefined?{ref:true,keys:s.current&&typeof s.current==='object'?Object.keys(s.current):[],value:typeof s.current==='boolean'?s.current:undefined}:
          s&&typeof s==='object'?{keys:Object.keys(s)}:typeof s;
        hooks.push({i,value});h=h.next;i++;
      }
      chain.push({name:typeof fiber.type==='function'?fiber.type.name:fiber.type,hooks});
      fiber=fiber.return;
    }
    return {url:location.href,dpr:devicePixelRatio,viewport:{w:innerWidth,h:innerHeight},canvas:canvas?{width:canvas.width,height:canvas.height,rect:canvas.getBoundingClientRect().toJSON()}:null,bodyPointerEvents:getComputedStyle(document.body).pointerEvents,hits:[...document.querySelectorAll('[data-hit]')].map(e=>({hit:e.dataset.hit,rect:e.getBoundingClientRect().toJSON()})),tauriKeys:Object.getOwnPropertyNames(window).filter(k=>k.includes('TAURI')).map(k=>[k,Object.getOwnPropertyNames(window[k])]),details};
  })));
} finally {await browser.close();}
