import {runSuite} from './suite.mjs';
self.onmessage=async({data})=>{
  try{self.postMessage({type:'done',report:await runSuite(data.profile,t=>self.postMessage({type:'test',test:{name:t.name,status:t.status,error:t.error}}))});}
  catch(e){self.postMessage({type:'done',report:{status:'BLOCKED_OR_FAILED',physicsExecuted:false,releaseApproved:false,error:String(e.stack??e)}});}
};
