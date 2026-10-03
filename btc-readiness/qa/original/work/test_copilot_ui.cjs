const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map();
function element(){return {style:{},dataset:{},textContent:'',hidden:false,clientWidth:1000,replaceChildren(){},append(){},getContext(){return new Proxy({},{get:()=>()=>{}})}}}
const document={getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement:element,querySelectorAll(){return []}};
const sandbox={document,window:{devicePixelRatio:1},Date,Number,Math,Set,sessionStorage:{getItem(){return null},setItem(){}},setInterval(){},fetch:async()=>{throw Error('offline')}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('work/btc_copilot_ui.js','utf8').replace('window.onresize=draw;update();','window.onresize=draw;'),sandbox);
vm.runInContext(`latest={epoch:Date.now()/1000,valid_until_epoch:Date.now()/1000+20,decision:'UP'};freshness();`,sandbox);
assert.equal(elements.get('decision').textContent,'UP');
vm.runInContext('disconnected=true;freshness();',sandbox);
assert.equal(elements.get('decision').textContent,'NO TRADE');
assert.equal(elements.get('previewCard').hidden,true);
vm.runInContext('freshness();',sandbox);assert.equal(elements.get('decision').textContent,'NO TRADE');
vm.runInContext('disconnected=false;latest.epoch=Date.now()/1000+100;freshness();',sandbox);
assert.equal(elements.get('decision').textContent,'NO TRADE');
vm.runInContext('latest.epoch=Date.now()/1000;latest.valid_until_epoch=Date.now()/1000-1;freshness();',sandbox);
assert.equal(elements.get('signalAlert').textContent,'');assert.equal(elements.get('decision').textContent,'NO TRADE');
console.log('UI checks passed: disconnect stays latched, clock reversal and expiry suppress guidance and previews.');
