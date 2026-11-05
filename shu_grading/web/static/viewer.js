/* Progressive viewer: preview now, reading image next, original on detailed zoom. */
let imageViewer=null;
const imageAssets=new Map();
function releaseAsset(key,entry){entry.controller.abort();if(entry.url)URL.revokeObjectURL(entry.url);imageAssets.delete(key);}
function trimImageCache(){
  let bytes=0,pixels=0;for(const e of imageAssets.values()){bytes+=e.bytes||0;pixels+=e.pixels||0;}
  for(const [key,e] of imageAssets){if(imageAssets.size<=4&&bytes<=32*1024*1024&&pixels<=32000000)break;
    if(e.url&&e.url===imageViewer?.objectURL)continue;bytes-=e.bytes||0;pixels-=e.pixels||0;releaseAsset(key,e);}
}
function clearImageCache(){for(const [key,e] of imageAssets)releaseAsset(key,e);$('#image-viewer')?.close();if($('#viewer-image'))$('#viewer-image').removeAttribute('src');}
function loadImageAsset(src){
  if(imageAssets.has(src)){const e=imageAssets.get(src);imageAssets.delete(src);imageAssets.set(src,e);return e.promise;}
  const e={controller:new AbortController()};imageAssets.set(src,e);
  e.promise=(async()=>{const timer=setTimeout(()=>e.controller.abort(),60000);try{
    const response=await fetch(src,{signal:e.controller.signal});if(!response.ok)throw Error('图片请求失败');
    const blob=await response.blob();if(e.controller.signal.aborted)throw Error('加载已取消');
    e.bytes=blob.size;e.url=URL.createObjectURL(blob);const image=new Image();image.src=e.url;await image.decode();
    if(e.controller.signal.aborted)throw Error('加载已取消');
    e.width=image.naturalWidth;e.height=image.naturalHeight;e.pixels=e.width*e.height;e.ready=true;return e;
  }catch(error){if(imageAssets.get(src)===e)releaseAsset(src,e);throw error;}finally{clearTimeout(timer);}})();
  return e.promise;
}
function warmSubmissionImages(id){
  const base='/api/submissions/'+id+'/image',rows=state.task?.submissions||[],index=rows.findIndex(s=>s.id===id);
  const next=rows.slice(index+1).find(s=>!s.confirmed),reading=base+'?reading=true';
  const allowed=new Set([reading,next?'/api/submissions/'+next.id+'/image?preview=true':'',imageViewer?.original]);
  for(const [key,e] of imageAssets)if(!e.ready&&!allowed.has(key)&&!(e.url&&e.url===imageViewer?.objectURL))releaseAsset(key,e);
  loadImageAsset(reading).then(()=>trimImageCache()).catch(()=>{});
  if(next)loadImageAsset('/api/submissions/'+next.id+'/image?preview=true').then(()=>trimImageCache()).catch(()=>{});
}
function viewerQuality(asset,source,quality){
  const v=imageViewer;if(!v)return;const image=$('#viewer-image'),hadImage=v.width>0;
  if(hadImage)v.scale*=v.width/asset.width;
  v.width=asset.width;v.height=asset.height;v.objectURL=asset.url;v.quality=quality;
  image.src=source;image.style.visibility='visible';if(hadImage)paintImage();else fitImage();trimImageCache();
}
function requestOriginal(){
  const v=imageViewer;if(!v||!v.reading||v.quality==='original'||v.originalPending)return;
  v.originalPending=true;$('#viewer-retry').hidden=true;
  loadImageAsset(v.original).then(asset=>{if(imageViewer===v)viewerQuality(asset,asset.url,'original');}).catch(()=>{
    if(imageViewer===v){v.originalPending=false;$('#viewer-retry').hidden=false;}
  });
}
function showFullImage(src,preview=null,reading=null){
  if(!src)return;let modal=$('#image-viewer');
  if(!modal){modal=document.createElement('dialog');modal.id='image-viewer';modal.setAttribute('aria-label','原图查看');
    modal.innerHTML='<button type="button" class="viewer-close" aria-label="关闭原图"><svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="1.5"/></svg></button><button type="button" id="viewer-retry" hidden>清晰图片加载失败，点击重试</button><div id="viewer-stage"><img id="viewer-image" alt="作文原图" draggable="false"></div>';
    document.body.append(modal);modal.addEventListener('close',()=>{imageViewer=null;document.body.classList.remove('viewing-image');});
    modal.querySelector('.viewer-close').onclick=()=>modal.close();$('#viewer-retry').onclick=()=>{if(imageViewer?.quality==='preview')loadReading();else requestOriginal();};
    $('#viewer-stage').onclick=e=>{if(e.target===$('#viewer-stage'))modal.close();};
    modal.addEventListener('wheel',e=>{e.preventDefault();if(e.ctrlKey&&imageViewer){const b=$('#viewer-stage').getBoundingClientRect();zoomImage(Math.exp(-e.deltaY*.002),e.clientX-b.left,e.clientY-b.top);}},{passive:false});
  }
  imageViewer={x:0,y:0,scale:1,width:0,height:0,original:src,reading,quality:'preview',objectURL:null,angle:Number(preview?.dataset.angle||0)};
  $('#viewer-retry').hidden=true;$('#viewer-image').style.visibility='hidden';
  if(!modal.open)modal.showModal();document.body.classList.add('viewing-image');
  if(preview?.complete&&preview.naturalWidth)viewerQuality({width:preview.naturalWidth,height:preview.naturalHeight},preview.currentSrc||preview.src,'preview');
  if(reading)loadReading();else{const v=imageViewer;const image=new Image();image.onload=()=>{if(imageViewer===v)viewerQuality({width:image.naturalWidth,height:image.naturalHeight},src,'original');};image.onerror=()=>{if(imageViewer===v){modal.close();toast('图片加载失败，请重新打开');}};image.src=src;}
}
function loadReading(){const v=imageViewer;if(!v?.reading)return;$('#viewer-retry').hidden=true;
  loadImageAsset(v.reading).then(asset=>{if(imageViewer!==v)return;viewerQuality(asset,asset.url,'reading');if(v.scale*devicePixelRatio>1)requestOriginal();}).catch(()=>{if(imageViewer===v)$('#viewer-retry').hidden=false;});
}
function paintImage(){if(!imageViewer)return;const v=imageViewer;$('#viewer-image').style.transform=`translate(${v.x}px,${v.y}px) scale(${v.scale}) translate(${v.angle===90?v.height:v.angle===180?v.width:0}px,${v.angle===180?v.height:v.angle===270?v.width:0}px) rotate(${v.angle||0}deg)`;}
function fitImage(){if(!imageViewer?.width)return;const v=imageViewer,stage=$('#viewer-stage');const w=v.angle%180?v.height:v.width,h=v.angle%180?v.width:v.height;v.scale=Math.min(1,(stage.clientWidth-40)/w,(stage.clientHeight-40)/h);v.x=(stage.clientWidth-w*v.scale)/2;v.y=(stage.clientHeight-h*v.scale)/2;paintImage();}
function zoomImage(factor,x,y){const v=imageViewer;if(!v?.width)return;const scale=Math.max(.02,Math.min(16,v.scale*factor));const px=(x-v.x)/v.scale,py=(y-v.y)/v.scale;v.x=x-px*scale;v.y=y-py*scale;v.scale=scale;paintImage();if(v.quality==='reading'&&v.scale*devicePixelRatio>1)requestOriginal();}
window.addEventListener('resize',()=>{if(imageViewer)fitImage();});

function orientPaper(img){if(!img.naturalWidth)return;const angle=Number(img.dataset.angle||0),odd=angle%180!==0,w=img.parentElement.clientWidth,h=w*(odd?img.naturalWidth/img.naturalHeight:img.naturalHeight/img.naturalWidth);img.parentElement.style.height=h+"px";img.style.width=(odd?h:w)+"px";img.style.transformOrigin="0 0";img.style.transform=`translate(${angle===90||angle===180?w:0}px,${angle===180||angle===270?h:0}px) rotate(${angle}deg)`;}
window.addEventListener("resize",()=>{const img=document.querySelector("#original-image");if(img)orientPaper(img);});
