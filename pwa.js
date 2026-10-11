(function(){var d=null,L=localStorage,S=sessionStorage,K="phInst";
var ua=navigator.userAgent;
var ios=(/iphone|ipad|ipod/i.test(ua)||(navigator.platform==="MacIntel"&&navigator.maxTouchPoints>1))&&!standalone();
function standalone(){try{return matchMedia("(display-mode: standalone)").matches||matchMedia("(display-mode: fullscreen)").matches||matchMedia("(display-mode: minimal-ui)").matches||navigator.standalone===true||/^android-app:/.test(document.referrer)}catch(e){return false}}
if(standalone())L[K]="1";
if(!S.phV){S.phV=1;L.phVisits=(+L.phVisits||0)+1}
var inst=function(){return standalone()||L[K]=="1"};
function sync(){document.querySelectorAll(".inst").forEach(function(b){b.hidden=!((d||ios)&&!inst())})}
function close(){var e=document.getElementById("ip");if(e)e.remove();S.phC=1}
function installed(){L[K]="1";d=null;sync();var e=document.getElementById("ip");if(e)e.remove()}
function install(){if(!d){if(ios)show(1);return}var p=d;p.prompt();p.userChoice.then(function(r){if(r.outcome=="accepted")installed();else{d=null;sync();close()}})}
 function show(f){if(inst()||(!f&&((+L.phVisits||0)>2||S.phC))||document.getElementById("ip")||(!d&&!ios))return;
 var e=document.createElement("div");e.id="ip";e.style.cssText="position:fixed;z-index:50;left:16px;bottom:calc(16px + env(safe-area-inset-bottom,0px));width:min(440px,calc(100% - 32px));background:#fff;border:1px solid #d9e2df;border-radius:16px;box-shadow:0 8px 32px rgba(13,36,38,.18);padding:20px;display:flex;gap:14px;font:600 17px/1.45 Inter,system-ui,sans-serif;color:#0d2426";
 e.innerHTML='<img src="/icon.svg" width="52" height="52" alt="" style="border-radius:12px;flex:none"><div><div style="font-weight:800;font-size:1.2rem">Install PHCORA</div><p style="margin:4px 0 12px;color:#4d6463;font-weight:500">'+(d?"Open it like an app, with faster loading and the last data available offline.":"On iPhone or iPad, tap Share, then Add to Home Screen.")+'</p><div style="display:flex;gap:10px;flex-wrap:wrap">'+(d?'<button id="ipi" style="min-height:48px;padding:0 20px;border-radius:999px;border:2px solid #0b3b3c;background:#0b3b3c;color:#fff;font:inherit;font-weight:700;cursor:pointer">Install app</button>':"")+'<button id="ipx" style="min-height:48px;padding:0 20px;border-radius:999px;border:2px solid #0b3b3c;background:#fff;color:#0b3b3c;font:inherit;font-weight:700;cursor:pointer">'+(d?"Not now":"Got it")+'</button></div></div>';
 document.body.appendChild(e);var i=document.getElementById("ipi");if(i)i.onclick=install;document.getElementById("ipx").onclick=close}
addEventListener("beforeinstallprompt",function(e){e.preventDefault();if(standalone())return;L.removeItem(K);d=e;sync();setTimeout(show,1500)});
addEventListener("appinstalled",installed);
try{["standalone","fullscreen","minimal-ui"].forEach(function(m){var q=matchMedia("(display-mode: "+m+")");var f=function(){if(q.matches)installed()};q.addEventListener?q.addEventListener("change",f):q.addListener&&q.addListener(f)})}catch(e){}
if(navigator.getInstalledRelatedApps){navigator.getInstalledRelatedApps().then(function(a){if(a&&a.length)installed()}).catch(function(){})}
document.addEventListener("click",function(e){if(e.target.closest&&e.target.closest(".inst"))install()});
if(ios)setTimeout(function(){sync();show()},2500);
if("serviceWorker"in navigator)addEventListener("load",function(){navigator.serviceWorker.register("/sw.js").catch(function(){})});
window.phSync=sync})();
