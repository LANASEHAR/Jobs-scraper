/** Jobs webhook for the six target role families + enrichment + diagnostics. */
const CONFIG={SPREADSHEET_ID:"1sCzxP9e_1gjKGBsN3tB_NUsSOnFacrfffuCzH45JuE8",SHEET_NAME:"Worldwide Remote",CONTACTS_SHEET:"Morocco Contacts",LOG_SHEET:"Scraper Logs",SHEETS:["Worldwide Remote","Morocco Remote","Casablanca Onsite"],MAX_DEEP_AGE_DAYS:30};
const COL={DATE:1,STATUS:2,ROLE:3,TITLE:4,COMPANY:5,LOCATION:6,REMOTE:7,SOURCE:8,LINK:9,ID:10,COMPANY_SITE:11,EMAILS:12,DEEP_STATUS:13,FIT_SCORE:14,FIT_REASONS:15,SALARY:16,DESCRIPTION:17,UPDATED:18,POSTED_AGE:19,POSTED_24H:20,SEARCH_TYPE:21,EMAIL_STATUS:22,EMAIL_SOURCE:23,SPONTANEOUS:24};
const HEADERS=["Date Detection","Status","Role Cible","Intitulé","Entreprise","Lieu","Remote","Source","Lien","ID","Company Site","Emails RH","Deep Status","Fit Score","Fit Reasons","Salary","Description","Last Updated","Posted Age","Posted <=24h","Search Type","Email Status","Email Source","Spontaneous"];
const LOG_HEADERS=["Timestamp","Run Finished","Total Unique","Worldwide Added","Morocco Added","Casablanca Added","Source Stats","Status"];
function getSS_(){return SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);}
function getSheet_(name){const ss=getSS_(),n=String(name||CONFIG.SHEET_NAME).trim()||CONFIG.SHEET_NAME;let sh=ss.getSheetByName(n);if(!sh)sh=ss.insertSheet(n);if(sh.getMaxColumns()<HEADERS.length)sh.insertColumnsAfter(sh.getMaxColumns(),HEADERS.length-sh.getMaxColumns());sh.getRange(1,1,1,HEADERS.length).setValues([HEADERS]);sh.setFrozenRows(1);return sh;}
function getContacts_(){const ss=getSS_();let sh=ss.getSheetByName(CONFIG.CONTACTS_SHEET);if(!sh)sh=ss.insertSheet(CONFIG.CONTACTS_SHEET);sh.getRange(1,1,1,7).setValues([["Date Found","Role","Company","Location","Email","Email Type","Company Website"]]);sh.setFrozenRows(1);return sh;}
function getLogs_(){const ss=getSS_();let sh=ss.getSheetByName(CONFIG.LOG_SHEET);if(!sh)sh=ss.insertSheet(CONFIG.LOG_SHEET);sh.getRange(1,1,1,LOG_HEADERS.length).setValues([LOG_HEADERS]);sh.setFrozenRows(1);return sh;}
function doPost(e){try{const raw=(e&&e.postData&&e.postData.contents)||"{}";const b=JSON.parse(raw);if(b.mode==="contacts")return addContacts_(b.contacts||[]);if(b.mode==="jobs")return addJobs_(b.jobs||[],b.sheet||CONFIG.SHEET_NAME);if(b.mode==="enrich")return enrichJobs_(b.updates||[],b.sheet||CONFIG.SHEET_NAME);if(b.mode==="pending")return getPending_(Number(b.limit||5000),b.sheet||"ALL");if(b.mode==="log")return addLog_(b.run||{});return json_({status:"error",message:"Unknown mode"});}catch(err){return json_({status:"error",message:String(err)});}}
function doGet(e){try{const a=(e&&e.parameter&&e.parameter.action)||"";if(a==="contacts")return json_({status:"success",contacts:getContacts_().getDataRange().getValues()});if(a==="pending")return getPending_(Math.min(Number(e.parameter.limit||5000),5000),e.parameter.sheet||"ALL");return json_({status:"ok",service:"jobs-webhook",time:new Date().toISOString()});}catch(err){return json_({status:"error",message:String(err)});}}
function getPending_(limit,sheetName){const names=sheetName&&sheetName!=="ALL"?[sheetName]:CONFIG.SHEETS;const out=[],max=Math.min(Number(limit)||5000,5000),cutoff=Date.now()-CONFIG.MAX_DEEP_AGE_DAYS*86400000;for(const name of names){const sh=getSheet_(name),v=sh.getDataRange().getValues();for(let r=1;r<v.length&&out.length<max;r++){const row=v[r],deep=String(row[COL.DEEP_STATUS-1]||"").toUpperCase(),id=String(row[COL.ID-1]||"").trim();if(!id||deep==="DONE"||deep==="NO_SITE"||deep==="SITE_FOUND_NO_PAGES"||deep==="NOT_FOUND")continue;const d=new Date(row[COL.DATE-1]).getTime();if(d&&d<cutoff)continue;out.push({id,entreprise:row[COL.COMPANY-1],intitule:row[COL.TITLE-1],lien:row[COL.LINK-1],company_site:row[COL.COMPANY_SITE-1],deep_status:deep,sheet:name});}}return json_({status:"success",jobs:out,count:out.length});}
function addJobs_(jobs,sheetName){const sh=getSheet_(sheetName),data=sh.getDataRange().getValues();const ids=new Set(data.slice(1).map(r=>String(r[COL.ID-1]||"").trim()).filter(Boolean)),rows=[];for(const j of jobs){const id=String(j.id||"").trim();if(!id||ids.has(id))continue;const title=String(j.intitule||j.role_cible||"").trim(),company=String(j.entreprise||"").trim();if(!title||!company||company==="Unknown")continue;rows.push([j.date_detection||new Date().toISOString(),"NEW",j.role_cible||title,title,company,j.lieu||"Casablanca",j.remote===true?"YES":"NO",j.source||"",j.lien||"",id,j.company_site||"",j.emails_rh||"",j.deep_status||"PENDING",j.fit_score||"",j.fit_reasons||"",j.salary||"",j.description||"",new Date(),j.posted_age||"",j.posted_within_24h||"UNKNOWN",j.search_type||"",j.email_status||"PENDING",j.email_source||"",j.spontaneous||"NO"]);ids.add(id);}if(rows.length)sh.getRange(sh.getLastRow()+1,1,rows.length,HEADERS.length).setValues(rows);return json_({status:"success",added:rows.length,received:jobs.length,sheet:sheetName});}
function enrichJobs_(updates,sheetName){const bySheet={};for(const u of updates){const n=u.sheet||sheetName||CONFIG.SHEET_NAME;(bySheet[n]||(bySheet[n]=[])).push(u);}let updated=0;for(const n in bySheet){const sh=getSheet_(n),v=sh.getDataRange().getValues(),map=new Map();for(let r=1;r<v.length;r++){const id=String(v[r][COL.ID-1]||"").trim();if(id)map.set(id,r+1);}for(const u of bySheet[n]){const row=map.get(String(u.id||"").trim());if(!row)continue;if(u.company_site!==undefined)sh.getRange(row,COL.COMPANY_SITE).setValue(u.company_site||"");if(u.emails_rh!==undefined)sh.getRange(row,COL.EMAILS).setValue(u.emails_rh||"");if(u.deep_status!==undefined)sh.getRange(row,COL.DEEP_STATUS).setValue(u.deep_status||"");if(u.email_status!==undefined)sh.getRange(row,COL.EMAIL_STATUS).setValue(u.email_status||"");if(u.email_source!==undefined)sh.getRange(row,COL.EMAIL_SOURCE).setValue(u.email_source||"");if(u.deep_error!==undefined)sh.getRange(row,COL.FIT_REASONS).setValue("Deep search error: "+u.deep_error);sh.getRange(row,COL.UPDATED).setValue(new Date());updated++;}}return json_({status:"success",updated,received:updates.length});}
function addContacts_(contacts){const sh=getContacts_(),v=sh.getDataRange().getValues();const seen=new Set(v.slice(1).map(r=>String(r[4]||"").toLowerCase().trim()).filter(Boolean)),rows=[];for(const c of contacts){const email=String(c.email||"").toLowerCase().trim();if(!email||seen.has(email)||!email.includes("@"))continue;rows.push([c.date_found||new Date().toISOString(),c.role||"",c.company||"",c.location||"",email,c.email_type||"Public company contact",c.company_website||""]);seen.add(email);}if(rows.length)sh.getRange(sh.getLastRow()+1,1,rows.length,7).setValues(rows);return json_({status:"success",added:rows.length,received:contacts.length,sheet:CONFIG.CONTACTS_SHEET});}
function addLog_(run){const sh=getLogs_(),g=run.groups_added||{};sh.appendRow([new Date(),run.finished_at||"",run.total_unique||0,g["Worldwide Remote"]||0,g["Morocco Remote"]||0,g["Casablanca Onsite"]||0,JSON.stringify(run.source_stats||{}),"SUCCESS"]);return json_({status:"success",logged:true});}

/* === Hourly recruitment email sender ===
   Sends up to 30 valid, unsent recruitment emails per hour during the morning.
   It never sends rows without an email and marks successful sends as SENT. */
const EMAIL_CONFIG={MAX_PER_HOUR:30,MORNING_START:8,MORNING_END:13,FROM_NAME:"Halima Essaouaf",REPLY_TO:""};

function parseFirstEmail_(value){
  const s=String(value||"").trim();
  if(!s)return "";
  const parts=s.split(/\\s*[/;,|]+\\s*/);
  for(const p of parts){
    const m=p.match(/[A-Z0-9._%+\\-]+@[A-Z0-9.\\-]+\\.[A-Z]{2,}/i);
    if(m)return m[0].toLowerCase();
  }
  return "";
}

function buildApplicationEmail_(row){
  const title=String(row[COL.TITLE-1]||row[COL.ROLE-1]||"the position").trim();
  const company=String(row[COL.COMPANY-1]||"your company").trim();
  const link=String(row[COL.LINK-1]||"").trim();
  return {
    subject:"Application – "+title+" – "+company,
    body:"Hello,\\n\\nI am writing to apply for the "+title+" position at "+company+".\\n\\nI would be pleased to discuss how my experience in customer success, account management, sales, administration, and digital design could contribute to your team.\\n\\nJob posting: "+(link||"available upon request")+"\\n\\nPlease find my CV attached if applicable. I would be happy to provide any additional information.\\n\\nBest regards,\\nHalima Essaouaf"
  };
}

function sendMorningEmails(){
  const hour=Number(Utilities.formatDate(new Date(),Session.getScriptTimeZone(),"H"));
  if(hour<EMAIL_CONFIG.MORNING_START || hour>=EMAIL_CONFIG.MORNING_END)return;
  const lock=LockService.getScriptLock();
  if(!lock.tryLock(5000))return;
  try{
    const props=PropertiesService.getScriptProperties();
    const hourKey=Utilities.formatDate(new Date(),Session.getScriptTimeZone(),"yyyy-MM-dd-HH");
    if(props.getProperty("EMAIL_BATCH_"+hourKey)==="DONE")return;

    let remaining=EMAIL_CONFIG.MAX_PER_HOUR, sent=0;
    const ss=getSS_();
    for(const name of CONFIG.SHEETS){
      if(remaining<=0)break;
      const sh=getSheet_(name), values=sh.getDataRange().getValues();
      for(let r=1;r<values.length && remaining>0;r++){
        const row=values[r];
        const status=String(row[COL.EMAIL_STATUS-1]||"").trim().toUpperCase();
        const email=parseFirstEmail_(row[COL.EMAILS-1]);
        if(!email || status==="SENT" || status==="SKIPPED")continue;

        const mail=buildApplicationEmail_(row);
        try{
          GmailApp.sendEmail(email,mail.subject,mail.body,{name:EMAIL_CONFIG.FROM_NAME});
          sh.getRange(r+1,COL.EMAIL_STATUS).setValue("SENT");
          sh.getRange(r+1,COL.EMAIL_SOURCE).setValue("Gmail - hourly morning batch");
          sh.getRange(r+1,COL.UPDATED).setValue(new Date());
          sent++;remaining--;
        }catch(err){
          sh.getRange(r+1,COL.EMAIL_STATUS).setValue("ERROR");
          sh.getRange(r+1,COL.EMAIL_SOURCE).setValue("Gmail error: "+String(err).slice(0,180));
        }
      }
    }
    props.setProperty("EMAIL_BATCH_"+hourKey,"DONE");
    getLogs_().appendRow([new Date(),"EMAIL_BATCH",sent,"","","","hour="+hourKey+"; limit="+EMAIL_CONFIG.MAX_PER_HOUR,"EMAIL_SENT"]);
  }finally{
    lock.releaseLock();
  }
}

/* Run this ONCE manually in Apps Script to create the hourly trigger.
   The function itself only sends during the configured morning window. */
function setupHourlyMorningEmailSender(){
  ScriptApp.getProjectTriggers().forEach(t=>{
    if(t.getHandlerFunction()==="sendMorningEmails")ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger("sendMorningEmails").timeBased().everyHours(1).create();
}
function json_(obj){return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);}
function setupRemoteSheet(){CONFIG.SHEETS.forEach(getSheet_);getLogs_();}function setupMoroccoSheet(){getSheet_("Morocco Remote");}function setupMoroccoContacts(){getContacts_();}