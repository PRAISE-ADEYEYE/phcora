const V="phcora-v1",SHELL=["/","/manifest.json","/icon.svg","/icon-192.png"],LIB=["cdn.jsdelivr.net","www.gstatic.com","fonts.googleapis.com","fonts.gstatic.com"];
self.addEventListener("install",e=>{e.waitUntil(caches.open(V).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()))});
self.addEventListener("activate",e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==V).map(x=>caches.delete(x)))).then(()=>self.clients.claim()))});
const swr=async(req)=>{const c=await caches.open(V),hit=await c.match(req),net=fetch(req).then(r=>{if(r&&(r.ok||r.type==="opaque"))c.put(req,r.clone());return r}).catch(()=>hit);return hit||net};
self.addEventListener("fetch",e=>{const r=e.request;if(r.method!=="GET")return;const u=new URL(r.url);
 if(u.origin!==location.origin){if(LIB.includes(u.hostname))e.respondWith(swr(r));return}
 if(u.pathname.startsWith("/api/"))return;
 if(r.mode==="navigate"){e.respondWith(fetch(r).then(x=>{caches.open(V).then(c=>c.put("/",x.clone()));return x}).catch(()=>caches.match("/")));return}
 e.respondWith(swr(r))});
