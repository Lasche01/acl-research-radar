'use strict';
// Exercise the shipped UI filtering, with no real server or user records.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function element(id){if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',addEventListener(){},classList:{toggle(){},add(){},remove(){}}});return nodes.get(id);}
const context={document:{querySelector:element,querySelectorAll:()=>[],body:element('body')},
 fetch:()=>new Promise(()=>{}),setTimeout(){},clearTimeout(){},URL,Intl,Date,console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('aclbrief/static/app.js','utf8'),context);
vm.runInContext(`
 const make=(id,decision,stage)=>({id,title:id,is_demo:false,screening_stage:stage,screening:{decision,score:0,reason:'test',related_candidate:id==='detail'},analysis:{category:'review',mappings:[],mode:'rule_screening'},payload:{},version:1,first_seen:'2026-11-07',source_id:'test'});
 state={notices:[make('title','review','title'),make('period','review','period'),make('detail','review','detailed'),make('match','recommended','detailed')],excluded:[],
 screening_summary:{review:2,title_pending:1,period_pending:1,detailed:2,related_candidates:1},source_summary:{},profile:{nodes:[]}};
 decision='review';globalThis.reviewIds=filteredNotices().map(n=>n.id);
 decision='preselection';globalThis.titleIds=filteredNotices().map(n=>n.id);
 decision='recommended';globalThis.recommendedIds=filteredNotices().map(n=>n.id);
 decision='related';globalThis.relatedIds=filteredNotices().map(n=>n.id);
 decision='opportunities';globalThis.opportunityIds=filteredNotices().map(n=>n.id);
 decision='all';globalThis.allIds=filteredNotices().map(n=>n.id);
 renderInbox();
`,context);
assert.deepEqual(Array.from(context.reviewIds),['period','detail']);
assert.deepEqual(Array.from(context.titleIds),['title']);
assert.deepEqual(Array.from(context.recommendedIds),['match']);
assert.deepEqual(Array.from(context.relatedIds),['detail']);
assert.deepEqual(Array.from(context.opportunityIds),['detail','match']);
assert.equal(context.allIds.length,4);
assert.match(nodes.get('#app').innerHTML,/제목 미선별 1건/);
assert.match(nodes.get('#app').innerHTML,/검토 대기 2건 보기/);
assert.match(nodes.get('#app').innerHTML,/기간 확인 대기 1건/);
assert.match(nodes.get('#app').innerHTML,/관련 후보 1건 보기/);
console.log('UI filter contract passed: title, period, detailed review, recommendation and preserved all-record view.');
vm.runInContext(`state.ai={provider:'openai',configured:true,model:'mock<model>',calls:2,call_limit:20};renderInbox();`,context);
assert.match(nodes.get('#app').innerHTML,/GPT API로 원문과 연구 실적의 의미를 심사/);
assert.match(nodes.get('#app').innerHTML,/mock&lt;model&gt;/);
assert.match(nodes.get('#app').innerHTML,/이번 작업 요청 2\/20회/);
assert.doesNotMatch(nodes.get('#app').innerHTML,/API·로컬 AI 없이 원문 근거 규칙으로 심사합니다/);
console.log('GPT mode UI contract passed: actual mode, escaped model, request counts and external transmission notice.');
