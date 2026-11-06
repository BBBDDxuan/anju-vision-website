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
const source = readFileSync(new URL('../src/LiveCameraPanel.tsx',import.meta.url),'utf8');
const { outputText } = ts.transpileModule(source,{ compilerOptions:{ module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022 } });
const evaluate = new Function('require','module','exports','fetch','window','document','URL','Image','performance',outputText);
const flush = async () => { for(let i=0;i<20;i++) await Promise.resolve(); };

// Virtual time and synthetic JPEGs only: no browser, camera, model, or real network.
function harness(respond,decode = () => Promise.resolve()) {
  let now=0, nextTimer=1, stateIndex=0, refIndex=0;
  const state=[],refs=[],effects=[],timers=new Map(),calls=[],revoked=[],listeners=new Map();
  const schedule=(fn,delay,interval=false) => { const id=nextTimer++;timers.set(id,{fn,at:now+Math.max(1,delay),delay,interval});return id; };
  const events={addEventListener(name,fn){listeners.set(name,fn);},removeEventListener(name,fn){if(listeners.get(name)===fn)listeners.delete(name);}};
  const window={...events,setTimeout:(fn,ms)=>schedule(fn,ms),setInterval:(fn,ms)=>schedule(fn,ms,true),clearTimeout:id=>timers.delete(id),clearInterval:id=>timers.delete(id)};
  const hooks={...React,useState(initial){const i=stateIndex++;if(!(i in state))state[i]=initial;return [state[i],value=>{state[i]=typeof value==='function'?value(state[i]):value;}];},useRef(initial){return refs[refIndex++] ||= {current:initial};},useEffect(fn){effects.push(fn);}};
  const fetch=async(path,init)=>{calls.push({path,init,at:now});return respond(path,init);};
  const module={exports:{}};
  let nextUrl=0;
  evaluate(id=>id==='react'?hooks:id==='./ui'?uiModule.exports:id.endsWith('.css')?{}:require(id),module,module.exports,fetch,window,events,{createObjectURL:()=>`blob:fixture-${++nextUrl}`,revokeObjectURL:url=>revoked.push(url)},class{decode(){return decode();}},{now:()=>now});
  function render(){stateIndex=0;refIndex=0;effects.length=0;return module.exports.LiveCameraPanel({});}
  render();const cleanup=effects[0]();
  async function advance(ms){const end=now+ms;await flush();for(;;){let selected;for(const [id,t]of timers)if(t.at<=end&&(!selected||t.at<selected[1].at))selected=[id,t];if(!selected)break;const[id,t]=selected;now=t.at;if(t.interval)t.at+=t.delay;else timers.delete(id);t.fn();await flush();}now=end;await flush();}
  function find(node,predicate){if(!React.isValidElement(node))return null;if(predicate(node))return node;for(const child of React.Children.toArray(node.props.children)){const found=find(child,predicate);if(found)return found;}return null;}
  return {state,calls,revoked,listeners,timers,advance,cleanup,command:()=>find(render(),node=>node.type==='button').props.onClick()};
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
  assert.match(h.state[3],/^blob:fixture-/);
  assert.equal(h.state[5],'');
  h.cleanup();
  assert.equal(h.timers.size,0);
  assert.equal(h.listeners.size,0);
  assert.equal(h.revoked.length,frames.length);
});

test('Hung status requests recover and hide old frames instead of displaying them as live',async()=>{
  let blocked=false;
  const h=harness((path,init)=>path.endsWith('/status')?(blocked?hang(init.signal):response(status())):response({}));
  await h.advance(100);assert.ok(h.state[3]);
  blocked=true;await h.advance(6000);
  assert.equal(h.state[3],'');assert.match(h.state[4],/超时/);
  blocked=false;await h.advance(2100);
  assert.equal(h.state[4],'');assert.ok(h.state[3]);
  h.cleanup();
});

test('A delayed JPEG cannot restore the picture after stop',async()=>{
  let resolveFrame;
  const h=harness(path=>path.endsWith('/status')?response(status()):path.endsWith('/stop')?response(status('stopped')):new Promise(resolve=>{resolveFrame=resolve;}));
  await h.advance(100);h.command();await flush();
  resolveFrame(response({}));await flush();
  assert.equal(h.state[0].state,'stopped');assert.equal(h.state[3],'');
  h.cleanup();
});

test('Invalid images are retried and never replace the preview; unmount aborts pending requests',async()=>{
  let broken=true;
  const h=harness(path=>response(path.endsWith('/status')?status():{}),()=>broken?Promise.reject(new Error('fixture decode failure')):Promise.resolve());
  await h.advance(120);assert.equal(h.state[3],'');assert.match(h.state[5],/decode failure/);
  broken=false;await h.advance(100);assert.ok(h.state[3]);assert.equal(h.state[5],'');
  h.cleanup();
  const stalled=harness((_path,init)=>hang(init.signal));
  await stalled.advance(20);stalled.cleanup();await flush();
  assert.ok(stalled.calls.every(c=>c.init.signal.aborted));
  assert.equal(stalled.timers.size,0);
});
