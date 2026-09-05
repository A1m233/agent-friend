import { createRequire } from 'node:module';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium}=require('playwright');
const browser=await chromium.connectOverCDP('http://127.0.0.1:9222');
try {
 const page=browser.contexts().flatMap(c=>c.pages()).find(p=>new URL(p.url()).pathname==='/pet.html');
 console.log(JSON.stringify(await page.evaluate(async()=>{
  const host=document.querySelector('#root').firstElementChild;
  let f=host[Object.keys(host).find(k=>k.startsWith('__reactFiber$'))];
  let ref;
  while(f&&!ref){let h=f.memoizedState;while(h){if(h.memoizedState?.current?.alphaHitTest){ref=h.memoizedState;break;}h=h.next;}f=f.return;}
  if(!ref)throw Error('Missing passthrough ref');
  const api=window.__TAURI_INTERNALS__;
  const samples=[];let count=0;let insideCount=0;let hitCount=0;let last='';let lastPayload;
  const handler=api.transformCallback(e=>{
   count++;
   lastPayload=e.payload;
   const b=ref.current.spriteScreen,p=e.payload;
   const inside=b&&p.x>=b.x&&p.x<=b.x+b.w&&p.y>=b.y&&p.y<=b.y+b.h;
   const alpha=inside?ref.current.alphaHitTest(p):null;
   if(inside)insideCount++;if(alpha)hitCount++;
   const record={p,b,inside,alpha,dragging:ref.current.isDragging,dpr:devicePixelRatio,dom:document.elementFromPoint(p.x,p.y)?.closest('[data-hit]')?.dataset.hit??null,toolbar:!!document.querySelector('[data-hit]')};
   const key=JSON.stringify(record);
   if(key!==last){samples.push(record);if(samples.length>8)samples.shift();last=key;}
  });
  const id=await api.invoke('plugin:event|listen',{event:'pet://cursor',target:{kind:'Any'},handler});
  let native;
  try {await new Promise(r=>setTimeout(r,1500)); native={position:await api.invoke('plugin:window|outer_position',{label:'pet'}),size:await api.invoke('plugin:window|outer_size',{label:'pet'}),cursor:await api.invoke('plugin:window|cursor_position')};}
  finally {window.__TAURI_EVENT_PLUGIN_INTERNALS__.unregisterListener('pet://cursor',id);await api.invoke('plugin:event|unlisten',{event:'pet://cursor',eventId:id});api.unregisterCallback(handler);}
  const corrected={x:(native.cursor.x-native.position.x)/devicePixelRatio,y:(native.cursor.y-native.position.y)/devicePixelRatio};
  const recordedCorrection={x:(2402-(-2560))/devicePixelRatio,y:999/devicePixelRatio};
  return {count,insideCount,hitCount,native,lastPayload,actualDpr:devicePixelRatio,inferredNativeDpr:(native.cursor.x-native.position.x)/lastPayload.x,corrected,correctedAlphaHit:ref.current.alphaHitTest(corrected),earlierStationarySample:{physical:{x:2402,y:999},observed:{x:2835.4285714285716,y:570.8571428571429},corrected:recordedCorrection,correctedAlphaHit:ref.current.alphaHitTest(recordedCorrection)},samples};
 }),null,2));
} finally {await browser.close();}
