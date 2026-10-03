const fs=require('fs'),vm=require('vm');
const results=[];
for(const [target,file] of [['original','original/outputs/btc_copilot.html'],['study002','outputs/copilot_study002_fee_review/btc_copilot.html']]){
 const elements=new Map();function element(){return {style:{},dataset:{},textContent:'',hidden:false,clientWidth:1000,replaceChildren(){},append(){},getContext(){return new Proxy({},{get:()=>()=>{}})}}}
 const document={getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement:element,querySelectorAll(){return []}};
 const sandbox={document,window:{devicePixelRatio:1},Date,Number,Math,Set,sessionStorage:{getItem(){return null},setItem(){}},setInterval(){},fetch:async()=>{throw Error('offline')}};
 vm.createContext(sandbox);
 const html=fs.readFileSync(__dirname+'/'+file,'utf8');const source=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('window.onresize=draw;update();','window.onresize=draw;');
 vm.runInContext(source,sandbox);
 const checks=[['expired','latest={epoch:Date.now()/1000-30,valid_until_epoch:Date.now()/1000-1,decision:"UP"};disconnected=false;freshness();'],
 ['future','latest={epoch:Date.now()/1000+100,valid_until_epoch:Date.now()/1000+110,decision:"UP"};disconnected=false;freshness();'],
 ['disconnected','latest={epoch:Date.now()/1000,valid_until_epoch:Date.now()/1000+10,decision:"UP"};disconnected=true;freshness();'],
 ['missing_epoch','latest={valid_until_epoch:Date.now()/1000+10,decision:"UP"};disconnected=false;freshness();'],
 ['missing_expiry','latest={epoch:Date.now()/1000,decision:"UP"};disconnected=false;freshness();'],
 ['nonfinite_epoch','latest={epoch:NaN,valid_until_epoch:Date.now()/1000+10,decision:"UP"};disconnected=false;freshness();']];
 for(const [name,code] of checks){vm.runInContext(code,sandbox);const actual=elements.get('decision').textContent;results.push({target,scenario:name,expected:'NO TRADE',actual,passed:actual==='NO TRADE'});}
}
fs.writeFileSync(__dirname+'/ui_results.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results,null,2));process.exitCode=results.every(x=>x.passed)?0:1;
