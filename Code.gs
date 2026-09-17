/** Google Sheets webhook for Remote Jobs and Morocco Jobs. */
const CONFIG = { SHEET_NAME: "Remote Jobs", MAX_DEEP_AGE_DAYS: 30 };
const COL = { DATE:1, STATUS:2, ROLE:3, TITLE:4, COMPANY:5, LOCATION:6, REMOTE:7, SOURCE:8, LINK:9, ID:10, COMPANY_SITE:11, EMAILS:12, DEEP_STATUS:13, FIT_SCORE:14, FIT_REASONS:15, SALARY:16, DESCRIPTION:17, UPDATED:18 };

function getSheet_(name) {
  const ss=SpreadsheetApp.getActiveSpreadsheet();
  const sheetName=String(name || CONFIG.SHEET_NAME).trim() || CONFIG.SHEET_NAME;
  let sh=ss.getSheetByName(sheetName);
  if(!sh){
    sh=ss.insertSheet(sheetName);
    sh.getRange(1,1,1,18).setValues([["Date Detection","Status","Role Cible","Intitulé","Entreprise","Lieu","Remote","Source","Lien","ID","Company Site","Emails RH","Deep Status","Fit Score","Fit Reasons","Salary","Description","Last Updated"]]);
    sh.setFrozenRows(1);
  }
  return sh;
}

function doPost(e){
  try{
    const body=JSON.parse((e.postData && e.postData.contents) || "{}");
    const sheet=body.sheet || CONFIG.SHEET_NAME;
    if(body.mode==="jobs") return addJobs_(body.jobs || [], sheet);
    if(body.mode==="enrich") return enrichJobs_(body.updates || [], sheet);
    if(body.mode==="pending") return getPending_(body.limit || 100, sheet);
    return json_({status:"error", message:"Unknown mode"});
  }catch(err){ return json_({status:"error", message:String(err)}); }
}

function doGet(e){
  try{
    if((e.parameter.action || "") !== "pending") return json_({status:"ok", service:"jobs-webhook"});
    return getPending_(Math.min(Number(e.parameter.limit || 100),500), e.parameter.sheet || CONFIG.SHEET_NAME);
  }catch(err){ return json_({status:"error", message:String(err)}); }
}

function getPending_(limit, sheetName){
  const sh=getSheet_(sheetName), values=sh.getDataRange().getValues(), out=[];
  const cutoff=Date.now()-CONFIG.MAX_DEEP_AGE_DAYS*86400000;
  for(let r=1;r<values.length && out.length<Math.min(Number(limit)||100,500);r++){
    const row=values[r], deep=String(row[COL.DEEP_STATUS-1]||"").toUpperCase(), id=String(row[COL.ID-1]||"").trim();
    if(!id || (deep && deep!=="PENDING" && deep!=="ERROR")) continue;
    const d=new Date(row[COL.DATE-1]).getTime(); if(d && d<cutoff) continue;
    out.push({id:id,entreprise:row[COL.COMPANY-1],intitule:row[COL.TITLE-1],lien:row[COL.LINK-1],company_site:row[COL.COMPANY_SITE-1],deep_status:deep});
  }
  return json_({status:"success",jobs:out,sheet:sheetName});
}

function addJobs_(jobs,sheetName){
  const sh=getSheet_(sheetName), data=sh.getDataRange().getValues();
  const ids=new Set(data.slice(1).map(r=>String(r[COL.ID-1]||"").trim()).filter(Boolean)), rows=[];
  for(const j of jobs){
    const id=String(j.id||"").trim(); if(!id || ids.has(id)) continue;
    const title=String(j.intitule||j.role_cible||"").trim(), company=String(j.entreprise||"").trim();
    if(!title || !company) continue;
    rows.push([j.date_detection||new Date().toISOString(),"NEW",j.role_cible||title,title,company,j.lieu||"Morocco / Casablanca",j.remote===true?"YES":"NO",j.source||"LinkedIn",j.lien||"",id,j.company_site||"",j.emails_rh||"",j.deep_status||"PENDING",j.fit_score||"",j.fit_reasons||"",j.salary||"",j.description||"",new Date()]);
    ids.add(id);
  }
  if(rows.length) sh.getRange(sh.getLastRow()+1,1,rows.length,18).setValues(rows);
  return json_({status:"success",added:rows.length,received:jobs.length,sheet:sheetName});
}

function enrichJobs_(updates,sheetName){
  const sh=getSheet_(sheetName), values=sh.getDataRange().getValues(), rowById=new Map();
  for(let r=1;r<values.length;r++){ const id=String(values[r][COL.ID-1]||"").trim(); if(id) rowById.set(id,r+1); }
  let updated=0;
  for(const u of updates){
    const row=rowById.get(String(u.id||"").trim()); if(!row) continue;
    if(u.company_site!==undefined) sh.getRange(row,COL.COMPANY_SITE).setValue(u.company_site||"");
    if(u.emails_rh!==undefined) sh.getRange(row,COL.EMAILS).setValue(u.emails_rh||"");
    if(u.deep_status!==undefined) sh.getRange(row,COL.DEEP_STATUS).setValue(u.deep_status||"");
    if(u.deep_error!==undefined) sh.getRange(row,COL.FIT_REASONS).setValue("Deep search error: "+u.deep_error);
    sh.getRange(row,COL.UPDATED).setValue(new Date()); updated++;
  }
  return json_({status:"success",updated:updated,received:updates.length,sheet:sheetName});
}

function json_(obj){ return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON); }
function setupRemoteSheet(){ getSheet_("Remote Jobs"); }
function setupMoroccoSheet(){ getSheet_("Morocco Jobs"); }
