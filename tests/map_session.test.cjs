const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const elements = new Map();
const element=id=>{if(!elements.has(id)) elements.set(id,{value:'',textContent:'',style:{},addEventListener(){},classList:{add(){},remove(){},contains(){return false;}}});return elements.get(id);};
const context=vm.createContext({document:{getElementById:element,querySelectorAll:()=>[],addEventListener(){}},localStorage:{getItem:()=>null,setItem(){}},navigator:{},setInterval(){},setTimeout(){},console});
context.window=context;
vm.runInContext(fs.readFileSync('static/script.js','utf8'),context);
vm.runInContext(fs.readFileSync('static/map.js','utf8'),context);
test('authenticated requests load the newest saved tokens', async()=>{
 const calls=[];
 context.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({access_token:'new',refresh_token:'new-refresh',cookie:'new-cookie'})};};
 await vm.runInContext(`accountFetch('/api/order/create', {method:'POST',body:JSON.stringify({item_id:'fake',access_token:'old'})})`,context);
 const body=JSON.parse(calls[1].options.body);
 assert.equal(body.access_token,'new'); assert.equal(body.item_id,'fake');
 assert.equal(calls[0].options.cache,'no-store');
});
test('map fallback works without Leaflet and rejects invalid coordinates',()=>{
 vm.runInContext('initMap()',context);
 assert.equal(element('location-coordinates').open,true);
 assert.equal(vm.runInContext('setLocation(91,0)',context),false);
 assert.equal(vm.runInContext('setLocation(40,-70)',context),true);
 assert.equal(element('coord-display').textContent,'40.0000, -70.0000');
});
test('OSM tiles have attribution and click/drag updates selected coordinates',()=>{
 let source,options;const events={},pinEvents={};
 context.L={map:()=>({on:(name,fn)=>events[name]=fn,setView(){},invalidateSize(){}}),tileLayer:(url,opts)=>{source=url;options=opts;return {addTo(){return this;},on(){}};},control:{scale:()=>({addTo(){}})},marker:()=>({addTo(){return this;},setLatLng(){},on:(name,fn)=>pinEvents[name]=fn,getLatLng:()=>({wrap:()=>({lat:45,lng:3})})})};
 vm.runInContext('initMap()',context);
 assert.equal(source,'https://tile.openstreetmap.org/{z}/{x}/{y}.png');
 assert.match(options.attribution,/openstreetmap.org\/copyright/);
 events.click({latlng:{wrap:()=>({lat:44,lng:2})}});
 assert.equal(vm.runInContext('selectedLat',context),44);
 pinEvents.dragend(); assert.equal(vm.runInContext('selectedLat',context),45);
});
test('refresh session button submits current credentials and restores its state',async()=>{
 const calls=[];
 context.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=> url==='/api/credentials'?{access_token:'new',refresh_token:'new-refresh'}:url==='/api/auth/status'?{can_refresh:true,expires_at:2000000000}:{status:'success'}};};
 await vm.runInContext('refreshSession()',context);
 const refresh=calls.find(c=>c.url==='/api/auth/refresh');
 assert.equal(JSON.parse(refresh.options.body).access_token,'new');
 assert.equal(element('btn-refresh-session').disabled,false);
 assert.equal(element('btn-refresh-session').textContent,'Refresh session');
 assert.match(element('auth-session-info').textContent,/expires/);
});
test('Telegram abort sync removes the local order using its real ID field',async()=>{
 vm.runInContext(`credentials={access_token:'fake'};localOrders=[{id:'order-1',timestamp:Date.now()}];`,context);
 context.fetch=async()=>({json:async()=>({status:'success',snipers:[{item_id:'item',order_id:'order-1',status:'ABORTED_REMOTELY',logs:[]}]})});
 await vm.runInContext('fetchSniperStatus()',context);
 assert.equal(vm.runInContext('localOrders.length',context),0);
});
