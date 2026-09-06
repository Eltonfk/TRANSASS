(function(){
  const $=id=>document.getElementById(id);
  async function api(url,opt){const r=await fetch(url,opt);let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw new Error(d.message||d.error||`HTTP ${r.status}`);return d}
  function show(dialog){if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','')}
  function close(dialog){dialog.close?.();dialog.removeAttribute('open')}
  function paint(d){
    const m=d.media||{};
    $('onboardingMedia').textContent=m.available?`✓ Biblioteca pronta · ${m.folders||0} pasta(s) encontrada(s)`:"⚠ Biblioteca não acessível. Verifique MEDIA_ROOT/STATE_DIR ou escolha a pasta no Desktop.";
    $('onboardingMedia').className='note '+(m.available?'onboarding-ok':'onboarding-warn');
    $('onboardingProvider').textContent=d.provider_configured?`Motor configurado · ${d.provider} · ${d.model}`:'⚠ Configure um motor e um modelo para traduzir.';
    $('onboardingProvider').className='note '+(d.provider_configured?'onboarding-ok':'onboarding-warn');
    return d;
  }
  async function refresh(){try{return paint(await api('/onboarding/status'))}catch(e){$('onboardingStatus').textContent='Não foi possível verificar a configuração inicial: '+e.message;return null}}
  async function open(){const d=await refresh();if(d&&!d.completed)show($('onboardingDialog'))}
  $('onboardingConfigure').onclick=()=>{close($('onboardingDialog'));$('openTransportConfig')?.click()};
  $('onboardingTest').onclick=async()=>{const status=$('onboardingStatus');status.textContent='Testando sem chamar o modelo…';try{const d=await api('/onboarding/provider-test',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});status.textContent=(d.ok?'✓ ':'⚠ ')+(d.message||'Teste concluído');status.className='muted '+(d.ok?'onboarding-ok':'onboarding-warn')}catch(e){status.textContent='⚠ '+e.message}};
  $('onboardingFinish').onclick=async()=>{const d=await refresh();if(!d)return;if(!d.media?.available||!d.provider_configured){$('onboardingStatus').textContent='Conclua a pasta de mídia e o motor antes de finalizar.';return}try{await api('/onboarding/complete',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});close($('onboardingDialog'))}catch(e){$('onboardingStatus').textContent='Não foi possível salvar: '+e.message}};
  window.addEventListener('load',open);
})();
