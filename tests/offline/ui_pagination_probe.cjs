const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/subtranslate/static/app.js', 'utf8');
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a, `Missing source section ${start}`);
  return source.slice(a, b);
}
function context() {
  const ctx = vm.createContext({console, Date, Set, JSON, encodeURIComponent, URL});
  vm.runInContext(`
    var selectedFolder='fixture',selectionFolder='fixture',seasonLangValue='français',globalSourceLang='français';
    var episodes=Array.from({length:80},(_,i)=>({id:i+1})),selectedEpisodeKeys=new Set(['60']);
    var episodeHasMore=true,episodeLoading=false,episodeOffset=0,lastEpisodesRefreshAt=0,episodeRenderFingerprint='',episodeLoadPromise=null;
    globalThis.__subtranslateDetectedSourceFolder='fixture';
    function $(id){return null} function episodeKey(ep){return String(ep.id)}
    function renderEpisodes(){} function syncSelectionUi(){} function t(key){return key} function esc(s){return String(s)}
    function recordPublished(){return false} function recordValidated(){return false} function recordLabel(){return 'v3'} function recordStateBadge(){return ''}
    var apiCalls=0;
    var api=async url=>{apiCalls++;const q=new URL(url,'http://fake').searchParams,offset=Number(q.get('offset')),limit=Number(q.get('limit'));
      return {episodes:Array.from({length:Math.min(limit,120-offset)},(_,i)=>({id:offset+i+1})),has_more:offset+limit<120}};
    ${section('function episodePageUrl(', '\n let currentArchiveSeriesId=')}
    ${section('function sourceBadge(ep){', '\nfunction auditBadge(ep){')}
    ${section('function versionDetailsHtml(record){', '\nasync function showVersionDetails(')}
  `, ctx);
  return ctx;
}
(async () => {
  const c=context();
  await vm.runInContext('loadEpisodes()',c);
  assert.equal(vm.runInContext('episodes.length',c),80);
  assert(vm.runInContext("selectedEpisodeKeys.has('60')",c));
  await vm.runInContext('loadAllEpisodes()',c);
  assert.equal(vm.runInContext('episodes.length',c),120);
  assert.equal(vm.runInContext('episodeHasMore',c),false);
  assert(vm.runInContext('sourceBadge({available:true,status:"SOURCE_AVAILABLE_INTERNAL_TEXT"})',c).includes('status.sourceInternal'));
  assert(vm.runInContext('versionDetailsHtml({id:2,source_language:"français",lineage:[{source_record_id:2,parent_record_id:1}]})',c).includes('français · registro 1'));

  const failed=context();
  vm.runInContext('api=async()=>{apiCalls++;throw new Error("fixture failure")}',failed);
  await assert.rejects(vm.runInContext('loadAllEpisodes()',failed), /fixture failure/);
  assert.equal(vm.runInContext('apiCalls',failed),1);
  assert.equal(vm.runInContext('episodeLoading',failed),false);
  assert.equal(vm.runInContext('episodes.length',failed),80);

  const stuck=context();
  vm.runInContext('api=async()=>({episodes:[],has_more:true})',stuck);
  await assert.rejects(vm.runInContext('loadAllEpisodes()',stuck), /sem avanço/);

  const concurrent=context();
  vm.runInContext('var resolvePage;api=()=>{apiCalls++;return new Promise(resolve=>{resolvePage=resolve})};var p1=loadMoreEpisodes();var p2=loadMoreEpisodes();',concurrent);
  vm.runInContext('resolvePage({episodes:[{id:81}],has_more:false})',concurrent);
  await vm.runInContext('Promise.all([p1,p2])',concurrent);
  assert.equal(vm.runInContext('apiCalls',concurrent),1);
  assert.equal(vm.runInContext('episodes.length',concurrent),81);

  const stale=context();
  vm.runInContext('var resolvePage;api=()=>new Promise(r=>{resolvePage=r});var p=loadEpisodes();selectedFolder="different";',stale);
  vm.runInContext('resolvePage({episodes:[{id:1}],has_more:false})',stale);
  await vm.runInContext('p',stale);
  assert.equal(vm.runInContext('episodes.length',stale),80);
  console.log('UI_PAGINATION_OK refresh,selection,season,failure,no-progress,concurrency,stale-response,badges,lineage');
})().catch(error=>{console.error(error);process.exitCode=1});
