// Run real loadPosts code in a minimal DOM harness; catch skipped pages and append regressions.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('public/app.js','utf8');
const start=source.indexOf('async function loadPosts(');
const end=source.indexOf("$('archive-more').onclick",start);
const elements=Object.fromEntries(['archive-search','archive-posts','archive-more'].map(id=>[id,{value:'',innerHTML:'',classList:{toggle(){}}}]));
const requests=[];
const context={elements,requests,console,URLSearchParams,AbortSignal};
vm.createContext(context);
vm.runInContext(`let archiveLoaded=true,archiveLoading=false,archiveNext=20,archiveItems=[{post_id:'old'}],archiveQuery='';
const $=id=>elements[id]; const escape=x=>String(x??''); const fmt=x=>x; const extraContent=()=>'';const safeLink=x=>x;
const fetch=async url=>{requests.push(url);return {ok:true,json:async()=>({posts:[{post_id:'new',text:'reset'}],next_offset:40})}};
${source.slice(start,end)};this.loadPosts=loadPosts;`,context);
(async()=>{
 elements['archive-search'].value='reset';
 await context.loadPosts(true);
 assert.match(requests[0],/offset=0/,'changed query starts at first page');
 await context.loadPosts(true);
 assert.match(requests[1],/offset=40/,'same query continues next page');
 assert.equal(vm.runInContext('archiveItems.length',context),2);
 console.log('Archive UI pagination regression: passed');
})().catch(e=>{console.error(e);process.exitCode=1});
