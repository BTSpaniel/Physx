const $=id=>document.getElementById(id);let lastReport=null;
window.runValidation=async(profile=$('profile').value)=>{
  if($('run').disabled)throw new Error('Validation already running');
  $('profile').value=profile;$('run').disabled=true;$('profile').disabled=true;$('save').disabled=true;
  $('status').textContent='RUNNING — verifying staged artifacts';$('results').replaceChildren();$('detail').textContent='';lastReport=null;
  let worker;
  try{worker=new Worker(new URL('./worker.mjs',import.meta.url),{type:'module'});}
  catch(e){const report={status:'BLOCKED_OR_FAILED',error:String(e),physicsExecuted:false,releaseApproved:false};lastReport=report;window.lastReport=report;$('status').textContent='BLOCKED OR FAILED';$('detail').textContent=JSON.stringify(report,null,2);$('run').disabled=false;$('profile').disabled=false;$('save').disabled=false;return report;}
  try{return await new Promise(resolve=>{
    let done=false;
    const finish=report=>{if(done)return;done=true;clearTimeout(timer);worker.terminate();lastReport=report;window.lastReport=report;
      $('status').textContent=report.status.replaceAll('_',' ');$('detail').textContent=JSON.stringify(report,null,2);$('save').disabled=false;resolve(report);};
    const timer=setTimeout(()=>finish({status:'BLOCKED_OR_FAILED',error:'Worker timeout; terminated.',physicsExecuted:false,releaseApproved:false}),90000);
    worker.onmessage=({data})=>{if(data.type==='test'){const li=document.createElement('li');li.textContent=`${data.test.status} — ${data.test.name}${data.test.error?`: ${data.test.error}`:''}`;$('results').append(li);}else if(data.type==='done')finish(data.report);};
    worker.onerror=e=>finish({status:'BLOCKED_OR_FAILED',error:e.message,physicsExecuted:false,releaseApproved:false});
    worker.postMessage({profile});
  });}finally{worker.terminate();$('run').disabled=false;$('profile').disabled=false;}
};
$('run').onclick=()=>window.runValidation();
$('save').onclick=()=>{const u=URL.createObjectURL(new Blob([JSON.stringify(lastReport,null,2)+'\n'],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download=`physx-${lastReport.profile??'error'}-report.json`;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);};
