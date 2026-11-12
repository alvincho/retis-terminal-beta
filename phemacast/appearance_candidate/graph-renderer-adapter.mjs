import{graphHTML,mountGraph}from'./graph.js';
export function installGraphRenderer(registerRenderer){registerRenderer({id:'workspace:display.graph',version:'1.0.0',render:value=>graphHTML(value),mount:(host,value)=>{
 const send=(inputs,observe=false)=>new Promise((resolve,reject)=>host.dispatchEvent(new CustomEvent('phemacast:graph-position',{bubbles:true,detail:{reference:value.positionReference,inputs,observe,resolve,reject}})));
 return mountGraph(host,value,{savePosition:inputs=>send(inputs),checkStatus:()=>send(null,true)});
}});}
