/**
 * REMOTE JOBS — GOOGLE SHEETS WEBHOOK
 * Discovery is separated from deep enrichment.
 * POST {mode:"jobs", jobs:[...]} -> inserts new LinkedIn jobs immediately.
 * POST {mode:"enrich", updates:[...]} -> updates company site/email/deep status by ID.
 * GET ?action=pending&limit=100 -> returns jobs waiting for deep search.
 */
const CONFIG = {
  SHEET_NAME: "Remote Jobs",
  MAX_DEEP_AGE_DAYS: 30,
};

const COL = {
  DATE: 1, STATUS: 2, ROLE: 3, TITLE: 4, COMPANY: 5, LOCATION: 6,
  REMOTE: 7, SOURCE: 8, LINK: 9, ID: 10, COMPANY_SITE: 11, EMAILS: 12,
  DEEP_STATUS: 13, FIT_SCORE: 14, FIT_REASONS: 15, SALARY: 16, DESCRIPTION: 17,
  UPDATED: 18,
};

function getSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(CONFIG.SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(CONFIG.SHEET_NAME);
    sh.getRange(1,1,1,18).setValues([[
      "Date Detection","Status","Role Cible","Intitulé","Entreprise","Lieu","Remote",
      "Source","Lien","ID","Company Site","Emails RH","Deep Status","Fit Score",
      "Fit Reasons","Salary","Description","Last Updated"
    ]]);
    sh.setFrozenRows(1);
  }
  return sh;
}

function isValidEmail_(email) {
  if (!email) return false;
  return /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/.test(String(email).trim().split(/\s*\/\s*/)[0]);
}

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents || "{}");
    if (body.mode === "jobs") return addJobs_(body.jobs || []);
    if (body.mode === "enrich") return enrichJobs_(body.updates || []);
    return json_({status:"error", message:"Unknown mode"});
  } catch (err) {
    return json_({status:"error", message:String(err)});
  }
}

function doGet(e) {
  try {
    if ((e.parameter.action || "") !== "pending") return json_({status:"ok", service:"remote-jobs-webhook"});
    const limit = Math.min(Number(e.parameter.limit || 100), 500);
    const sh = getSheet_();
    const values = sh.getDataRange().getValues();
    const out = [];
    const cutoff = Date.now() - CONFIG.MAX_DEEP_AGE_DAYS * 86400000;
    for (let r = 1; r < values.length && out.length < limit; r++) {
      const row = values[r];
      const deep = String(row[COL.DEEP_STATUS-1] || "").toUpperCase();
      const id = String(row[COL.ID-1] || "").trim();
      if (!id || (deep && deep !== "PENDING" && deep !== "ERROR")) continue;
      const d = new Date(row[COL.DATE-1]).getTime();
      if (d && d < cutoff) continue;
      out.push({
        id:id, entreprise:row[COL.COMPANY-1], intitule:row[COL.TITLE-1],
        lien:row[COL.LINK-1], company_site:row[COL.COMPANY_SITE-1], deep_status:deep
      });
    }
    return json_({status:"success", jobs:out});
  } catch (err) { return json_({status:"error", message:String(err)}); }
}

function addJobs_(jobs) {
  const sh = getSheet_();
  const data = sh.getDataRange().getValues();
  const ids = new Set(data.slice(1).map(r => String(r[COL.ID-1] || "").trim()).filter(Boolean));
  const rows = [];
  for (const j of jobs) {
    const id = String(j.id || "").trim();
    if (!id || ids.has(id)) continue;
    const title = String(j.intitule || j.role_cible || "").trim();
    const company = String(j.entreprise || "").trim();
    if (!title || !company) continue;
    rows.push([
      j.date_detection || new Date().toISOString(), "NEW", j.role_cible || title, title,
      company, j.lieu || "Remote / Worldwide", j.remote === true ? "YES" : "UNKNOWN",
      j.source || "LinkedIn", j.lien || "", id, j.company_site || "", j.emails_rh || "",
      j.deep_status || "PENDING", j.fit_score || "", j.fit_reasons || "", j.salary || "",
      j.description || "", new Date()
    ]);
    ids.add(id);
  }
  if (rows.length) sh.getRange(sh.getLastRow()+1,1,rows.length,18).setValues(rows);
  return json_({status:"success", added:rows.length, received:jobs.length});
}

function enrichJobs_(updates) {
  const sh = getSheet_();
  const values = sh.getDataRange().getValues();
  const rowById = new Map();
  for (let r=1; r<values.length; r++) {
    const id = String(values[r][COL.ID-1] || "").trim();
    if (id) rowById.set(id, r+1);
  }
  let updated = 0;
  for (const u of updates) {
    const row = rowById.get(String(u.id || "").trim());
    if (!row) continue;
    if (u.company_site !== undefined) sh.getRange(row,COL.COMPANY_SITE).setValue(u.company_site || "");
    if (u.emails_rh !== undefined) sh.getRange(row,COL.EMAILS).setValue(u.emails_rh || "");
    if (u.deep_status !== undefined) sh.getRange(row,COL.DEEP_STATUS).setValue(u.deep_status || "");
    if (u.deep_error !== undefined) sh.getRange(row,COL.FIT_REASONS).setValue("Deep search error: " + u.deep_error);
    sh.getRange(row,COL.UPDATED).setValue(new Date());
    updated++;
  }
  return json_({status:"success", updated:updated, received:updates.length});
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function setupRemoteSheet() { getSheet_(); }
