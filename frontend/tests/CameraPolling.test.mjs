import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import React from 'react';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const uiSource = readFileSync(new URL('../src/ui.tsx',import.meta.url),'utf8');
const uiOutput = ts.transpileModule(uiSource,{ compilerOptions:{ module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022 } }).outputText;
const uiModule = { exports: {} };
new Function('require','module','exports',uiOutput)(require,uiModule,uiModule.exports);
const memberApiOutput = ts.transpileModule(readFileSync(new URL('../src/memberApi.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const memberApiModule = {exports:{}};
new Function('require','module','exports',memberApiOutput)(require,memberApiModule,memberApiModule.exports);
const source = readFileSync(new URL('../src/LiveCameraPanel.tsx',import.meta.url),'utf8');
const { outputText } = ts.transpileModule(source,{ compilerOptions:{ module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022 } });
const evaluate = new Function('require','module','exports','fetch','window','document','URL','Image','performance',outputText);
const flush = async () => { for(let i=0;i<20;i++) await Promise.resolve(); };

// Virtual time and synthetic JPEGs only: no browser, camera, model, or real network.
function harness(respond,decode = () => Promise.resolve()) {
  let now=0, nextTimer=1, stateIndex=0, refIndex=0;
  const state=[],refs=[],effects=[],timers=new Map(),calls=[],revoked=[],draws=[],listeners=new Map();
  const schedule=(fn,delay,interval=false) => { const id=nextTimer++;timers.set(id,{fn,at:now+Math.max(1,delay),delay,interval});return id; };
  const events={addEventListener(name,fn){listeners.set(name,fn);},removeEventListener(name,fn){if(listeners.get(name)===fn)listeners.delete(name);}};
  const window={...events,setTimeout:(fn,ms)=>schedule(fn,ms),setInterval:(fn,ms)=>schedule(fn,ms,true),clearTimeout:id=>timers.delete(id),clearInterval:id=>timers.delete(id)};
  const hooks={...React,useState(initial){const i=stateIndex++;if(!(i in state))state[i]=initial;return [state[i],value=>{state[i]=typeof value==='function'?value(state[i]):value;}];},useRef(initial){return refs[refIndex++] ||= {current:initial};},useEffect(fn){effects.push(fn);}};
  const fetch=async(path,init)=>{calls.push({path,init,at:now});return respond(path,init);};
  const module={exports:{}};
  let nextUrl=0;
  evaluate(id=>id==='react'?hooks:id==='./ui'?uiModule.exports:id==='./memberApi'?memberApiModule.exports:id.endsWith('.css')?{}:require(id),module,module.exports,fetch,window,events,{createObjectURL:()=>`blob:fixture-${++nextUrl}`,revokeObjectURL:url=>revoked.push(url)},class{naturalWidth=640;naturalHeight=360;decode(){return decode();}},{now:()=>now});
  function render(){stateIndex=0;refIndex=0;effects.length=0;return module.exports.LiveCameraPanel({});}
  const canvas = find(render(),node=>node.type==='canvas');
  const context = { drawImage: image=>draws.push(image.src), clearRect(){} };
  canvas.props.ref.current = { width:300,height:150,getContext:()=>context };
  const cleanup=effects[0]();
  async function advance(ms){const end=now+ms;await flush();for(;;){let selected;for(const [id,t]of timers)if(t.at<=end&&(!selected||t.at<selected[1].at))selected=[id,t];if(!selected)break;const[id,t]=selected;now=t.at;if(t.interval)t.at+=t.delay;else timers.delete(id);t.fn();await flush();}now=end;await flush();}
  function find(node,predicate){if(!React.isValidElement(node))return null;if(predicate(node))return node;for(const child of React.Children.toArray(node.props.children)){const found=find(child,predicate);if(found)return found;}return null;}
  return {state,calls,revoked,draws,listeners,timers,advance,cleanup,canvasHidden:()=>find(render(),node=>node.type==='canvas').props.hidden,command:()=>find(render(),node=>node.type==='button').props.onClick()};
}
const status=(state='running')=>({state,frames:1,recording:false,events:[]});
const response=data=>({ok:true,json:async()=>data,blob:async()=>new Blob(['synthetic fixture'])});
const hang=signal=>new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));

test('A hung frame cannot block heartbeat; timeouts retry across the 30-second worker lease',async()=>{
  const h=harness((path,init)=>path.endsWith('/status')?response(status()):hang(init.signal));
  await h.advance(45000);
  const heartbeat=h.calls.filter(c=>c.path.endsWith('/status'));
  assert.ok(heartbeat.length>=45);
  for(let i=1;i<heartbeat.length;i++)assert.ok(heartbeat[i].at-heartbeat[i-1].at<=1000);
  const frames=h.calls.filter(c=>c.path.endsWith('/frame.jpg'));
  assert.ok(frames.length>=8,'stalled image requests must retry');
  assert.ok(frames.slice(0,-1).every(c=>c.init.signal.aborted));
  assert.match(h.state[5],/超时/);
  h.cleanup();
});

test('Preview reaches the 20 FPS budget independently of status frame counters',async()=>{
  const h=harness(path=>response(path.endsWith('/status')?status():{}));
  await h.advance(2050);
  const frames=h.calls.filter(c=>c.path.endsWith('/frame.jpg'));
  assert.ok(frames.length>=39&&frames.length<=42,`${frames.length} frames in two seconds`);
  assert.equal(h.calls.filter(c=>c.path.endsWith('/status')).length,3);
  assert.equal(h.canvasHidden(),false);
  assert.equal(h.draws.length,frames.length);
  assert.equal(h.state[5],'');
  h.cleanup();
  assert.equal(h.timers.size,0);
  assert.equal(h.listeners.size,0);
  assert.equal(h.revoked.length,frames.length);
});

test('A matching frame ETag skips downloading and decoding a duplicate image',async()=>{
  let version=1, decodes=0;
  const h=harness((path,init)=>{
    if(path.endsWith('/status')) return response(status());
    const tag=`"${version}"`;
    if(init.headers?.['If-None-Match']===tag) return {ok:false,status:304};
    return {...response({}),headers:new Headers({'ETag':tag})};
  },()=>{decodes++;return Promise.resolve();});
  await h.advance(500);
  assert.equal(decodes,1);
  assert.equal(h.draws.length,1);
  version++;
  await h.advance(500);
  assert.equal(decodes,2);
  assert.equal(h.draws.length,2);
  assert.equal(h.state[5],'');
  h.cleanup();
});

test('Hung status requests keep the last frame briefly, then expire it and recover',async()=>{
  let blocked=false;
  const h=harness((path,init)=>path.endsWith('/status')?(blocked?hang(init.signal):response(status())):response({}));
  await h.advance(100);assert.equal(h.canvasHidden(),false);
  blocked=true;await h.advance(6000);
  assert.equal(h.canvasHidden(),false);assert.match(h.state[4],/超时/);
  await h.advance(3100);
  assert.equal(h.canvasHidden(),true);
  blocked=false;await h.advance(5100);
  assert.equal(h.state[4],'');assert.equal(h.canvasHidden(),false);
  h.cleanup();
});

test('A delayed JPEG cannot restore the picture after stop',async()=>{
  let resolveFrame;
  const h=harness(path=>path.endsWith('/status')?response(status()):path.endsWith('/stop')?response(status('stopped')):new Promise(resolve=>{resolveFrame=resolve;}));
  await h.advance(100);h.command();await flush();
  resolveFrame(response({}));await flush();
  assert.equal(h.state[0].state,'stopped');assert.equal(h.canvasHidden(),true);
  assert.equal(h.draws.length,0);
  h.cleanup();
});

test('Invalid images are retried and never replace the preview; unmount aborts pending requests',async()=>{
  let broken=true;
  const h=harness(path=>response(path.endsWith('/status')?status():{}),()=>broken?Promise.reject(new Error('fixture decode failure')):Promise.resolve());
  await h.advance(120);assert.equal(h.canvasHidden(),true);assert.match(h.state[5],/decode failure/);
  broken=false;await h.advance(100);assert.equal(h.canvasHidden(),false);assert.equal(h.state[5],'');
  h.cleanup();
  const stalled=harness((_path,init)=>hang(init.signal));
  await stalled.advance(20);stalled.cleanup();await flush();
  assert.ok(stalled.calls.every(c=>c.init.signal.aborted));
  assert.equal(stalled.timers.size,0);
});

test('Intermittent 503 and decode failures hold the last good image until a new image is ready',async()=>{
  let unavailable=false, invalid=false;
  const h=harness(path=>path.endsWith('/status')?response(status()):unavailable?{ok:false,status:503}:response({}),
    ()=>invalid?Promise.reject(new Error('corrupt JPEG')):Promise.resolve());
  await h.advance(100);
  const lastDraw=h.draws.at(-1);
  unavailable=true;
  await h.advance(300);
  assert.equal(h.canvasHidden(),false);
  assert.equal(h.draws.at(-1),lastDraw);
  unavailable=false;
  invalid=true;
  await h.advance(300);
  assert.equal(h.canvasHidden(),false);
  assert.equal(h.draws.at(-1),lastDraw);
  invalid=false;
  await h.advance(100);
  assert.notEqual(h.draws.at(-1),lastDraw);
  assert.equal(h.canvasHidden(),false);
  h.cleanup();
});

test('A brief reconnect keeps the last frame, and stale or stopped state clears it',async()=>{
  let state='running';
  const h=harness(path=>response(path.endsWith('/status')?status(state):{}));
  await h.advance(250);
  state='reconnecting';
  await h.advance(1000);
  assert.equal(h.canvasHidden(),false);
  state='connecting';
  await h.advance(1000);
  assert.equal(h.canvasHidden(),false);
  state='stale';
  await h.advance(1000);
  assert.equal(h.canvasHidden(),true);
  state='running';
  await h.advance(1000);
  assert.equal(h.canvasHidden(),false);
  state='stopped';
  await h.advance(1000);
  assert.equal(h.canvasHidden(),true);
  h.cleanup();
});
