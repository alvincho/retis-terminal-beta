export function filterLedger(rows,{query='',action='',from='',to='',timeZone}={}) {
 const needle=query.trim().toLowerCase();
 return rows.filter(row=>{
  const date=new Date(row.created_at);let localDate='';if(Number.isFinite(+date)){if(timeZone){const parts=new Intl.DateTimeFormat('en-GB',{timeZone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date),get=type=>parts.find(p=>p.type===type).value;localDate=[get('year'),get('month'),get('day')].join('-');}else localDate=[date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('-');}
  return (!action||row.action===action)&&(!from||localDate>=from)&&(!to||localDate<=to)&&(!needle||[row.id,row.request_id,row.reason,row.agent_name,row.agent_id,row.escrowId,row.source,row.counterparty,...(row.payees||[]).flatMap(p=>[p.provider_username,p.provider_user_id,p.agent_name])].join(' ').toLowerCase().includes(needle));
 });
}
export function csvCell(value) {
 let text=String(value??'');
 // Spreadsheet text must not be interpreted as a formula on opening the export.
 if(typeof value!=='number'&&/^[\s]*[=+@-]/.test(text))text="'"+text;
 return '"'+text.replaceAll('"','""')+'"';
}
export function ledgerCSV(rows) {
 const keys=['created_at','label','delta','previous_credit_points','credit_points','agent_name','payee','pulse','source','reason','request_id','escrowId','id'];
 const lines=[keys.map(csvCell).join(',')];
 for(const row of rows){const values={...row,payee:(row.payees||[]).map(p=>p.provider_username||p.provider_user_id).join('; ')};lines.push(keys.map(k=>csvCell(values[k])).join(','));}
 return '\ufeff'+lines.join('\r\n');
}
