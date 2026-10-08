/**
 * JOBS SCRAPER -> GOOGLE SHEETS -> GMAIL
 * Email-first workflow for Halima Essaouaf.
 *
 * Python writes every discovered job after exhaustive company-level email enrichment.
 * Jobs without a verified public email are kept with Email Status = NO_EMAIL.
 * This script sends from the Gmail account that authorizes the Apps Script.
 * Put the CV PDF in the same Google Drive account.
 */

const CONFIG = {
  SPREADSHEET_ID: "1sCzxP9e_1gjKGBsN3tB_NUsSOnFacrfffuCzH45JuE8",
  NAME: "Halima Essaouaf",
  EMAIL: "essaouafhalima@gmail.com",
  PHONE: "+212 619 968 131",
  LINKEDIN: "",
  CV_FILE_NAME: "CV_Halima_Essaouaf.pdf",

  SHEETS: ["Ausbildung", "Worldwide Remote", "Morocco Remote", "Casablanca Onsite", "Casablanca Spontaneous", "Remote Travel Hospitality"],

  // Gmail/Apps Script consumer accounts currently have a 100-recipient/day quota.
  // Keep a margin instead of trying to consume the entire quota.
  MAX_EMAILS_PER_DAY: 95,
  MAX_EMAILS_PER_RUN: 25,

  // Hours use the Apps Script project timezone.
  SEND_START_HOUR: 7,
  SEND_END_HOUR: 20,

  FOLLOWUP_DAYS: 5,

  // Permanent do-not-contact block. Never send to Fertan, even if the scraper rediscovers the company or its email address later.
  BLOCKED_COMPANIES: ["fertan"],
  BLOCKED_EMAIL_DOMAINS: ["fertan.de"],

  HEADERS: [
    "Date Detection","Status","Role Cible","Intitulé","Entreprise","Lieu","Remote",
    "Source","Lien","ID","Company Site","Emails RH","Deep Status","Fit Score",
    "Fit Reasons","Salary","Description","Last Updated","Posted Age","Posted <=24h",
    "Search Type","Email Status","Email Source","Spontaneous","Date Sent","Date Followup"
  ]
};

function getSS_() {
  return SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
}

function getSheet_(name) {
  const ss = getSS_();
  const n = String(name || "Worldwide Remote").trim() || "Worldwide Remote";
  let sh = ss.getSheetByName(n);
  if (!sh) sh = ss.insertSheet(n);
  if (sh.getMaxColumns() < CONFIG.HEADERS.length) {
    sh.insertColumnsAfter(sh.getMaxColumns(), CONFIG.HEADERS.length - sh.getMaxColumns());
  }
  sh.getRange(1, 1, 1, CONFIG.HEADERS.length).setValues([CONFIG.HEADERS]);
  sh.setFrozenRows(1);
  return sh;
}

function setupRemoteSheet() {
  CONFIG.SHEETS.forEach(getSheet_);
  Logger.log("Sheets ready.");
}

function setupAutomation() {
  setupRemoteSheet();

  ScriptApp.getProjectTriggers().forEach(t => {
    const fn = t.getHandlerFunction();
    if (fn === "sendPendingApplications") ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger("sendPendingApplications")
    .timeBased()
    .everyHours(1)
    .create();

  Logger.log("Hourly Gmail trigger installed.");
}

function isValidEmail_(email) {
  return /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i.test(String(email || "").trim());
}

function isBlockedRecipient_(email, company) {
  const e = String(email || "").trim().toLowerCase();
  const c = String(company || "").trim().toLowerCase();

  if (CONFIG.BLOCKED_EMAIL_DOMAINS.some(domain =>
      e === domain || e.endsWith("@" + domain))) return true;

  return CONFIG.BLOCKED_COMPANIES.some(name =>
    c === name || c.includes(name) || e.includes(name));
}

function firstEmail_(value) {
  const parts = String(value || "").split(/\s*\/\s*/);
  for (const p of parts) {
    const e = p.trim();
    if (isValidEmail_(e)) return e;
  }
  return "";
}

function findColumn_(headers, name) {
  const i = headers.indexOf(name);
  return i >= 0 ? i : -1;
}

function rowObject_(headers, row) {
  const o = {};
  headers.forEach((h, i) => o[h] = row[i] === undefined ? "" : row[i]);
  return o;
}

function shouldSendNow_() {
  const hour = Number(Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "H"));
  return hour >= CONFIG.SEND_START_HOUR && hour < CONFIG.SEND_END_HOUR;
}

function sentTodayCount_() {
  const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd");
  let count = 0;
  CONFIG.SHEETS.forEach(function(name) {
    const sh = getSheet_(name);
    const data = sh.getDataRange().getValues();
    if (data.length < 2) return;
    const headers = data[0];
    const statusCol = findColumn_(headers, "Status");
    const sentCol = findColumn_(headers, "Date Sent");
    for (let r = 1; r < data.length; r++) {
      if (statusCol < 0 || sentCol < 0) continue;
      const status = String(data[r][statusCol] || "");
      const sent = data[r][sentCol];
      if (status === "SENT" && sent) {
        const d = Utilities.formatDate(new Date(sent), Session.getScriptTimeZone(), "yyyy-MM-dd");
        if (d === today) count++;
      }
    }
  });
  return count;
}
function getCV_() {
  const files = DriveApp.getFilesByName(CONFIG.CV_FILE_NAME);
  if (files.hasNext()) return files.next();

  const all = DriveApp.getFiles();
  while (all.hasNext()) {
    const f = all.next();
    if (f.getMimeType() === MimeType.PDF &&
        /halima.*essaouaf|essaouaf.*halima/i.test(f.getName())) {
      return f;
    }
  }
  return null;
}

function roleFamily_(job) {
  const t = (String(job["Intitulé"] || "") + " " + String(job["Role Cible"] || "")).toLowerCase();

  if (/hotelfachfrau|hotelfachmann|hotelmanagement|hotelkauf/.test(t)) return "ausbildung_hotel";
  if (/systemgastronomie|restaurants? und veranstaltungsgastronomie|fachkraft für gastronomie|fachfrau für gastronomie|fachmann für gastronomie|restaurantfach/.test(t)) return "ausbildung_gastro";
  if (/einzelhandel|verk[aä]ufer/.test(t)) return "ausbildung_retail";
  if (/spedition|logistikdienstleistung|speditionskauf/.test(t)) return "ausbildung_logistics";
  if (/groß- und au[ßs]enhandel|au[ßs]enhandelsmanagement|handel/.test(t)) return "ausbildung_trade";
  if (/industriekauf/.test(t)) return "ausbildung_industry";
  if (/b[uü]romanagement|b[uü]rokauff|kaufmann.*b[uü]romanagement/.test(t)) return "ausbildung_office";

  if (/customer success|client success|customer experience|onboarding/.test(t)) return "customer_success";
  if (/account manager|account management|key account|strategic account|partner manager/.test(t)) return "account";
  if (/partnership|revenue operations|sales operations|commercial operations|sales enablement/.test(t)) return "partnerships";
  if (/travel|hospitality|hotel tech/.test(t)) return "travel";
  if (/ecommerce|e-commerce|shopify|marketplace|digital operations/.test(t)) return "ecommerce";
  return "operations";
}
function companyMention_(company) {
  const c = String(company || "").trim();
  if (!c || /^(unknown|indeed employer|company)$/i.test(c)) return "";
  return c;
}

function extractOfferDetails_(description, role) {
  const raw = String(description || "")
    .replace(/\{[^}]{0,300}\}/g, " ")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!raw) return [];

  const roleWords = String(role || "")
    .toLowerCase()
    .split(/[^a-zäöüß0-9]+/i)
    .filter(w => w.length >= 4);

  const sentences = raw
    .split(/(?<=[.!?])\s+/)
    .map(s => s.trim())
    .filter(s => s.length >= 45 && s.length <= 240);

  const preferred = sentences
    .map(s => {
      const t = s.toLowerCase();
      let score = 0;
      if (/(aufgaben|verantwort|tätig|betreuung|kunden|gäste|service|organisation|koordination|verkauf|beratung|reservierung|buchung|rezeption|administration|disposition|aufträge|bestellungen|e-commerce|crm|hotellerie|gastronomie)/i.test(t)) score += 4;
      roleWords.forEach(w => { if (t.indexOf(w) >= 0) score += 1; });
      if (/gesucht|wir bieten|benefits|vergütung|gehalt|urlaub|bewerben|kontakt/i.test(t)) score -= 3;
      return { s, score };
    })
    .filter(x => x.score > 0)
    .sort((a, b) => b.score - a.score);

  return preferred.slice(0, 2).map(x => {
    let s = x.s.replace(/\s+/g, " ").trim();
    if (s.length > 180) s = s.slice(0, 177).replace(/\s+\S*$/, "") + "...";
    return s;
  });
}

function naturalFit_(family, details) {
  if (details.length) {
    const first = details[0]
      .replace(/^aufgaben[:\-]?\s*/i, "")
      .replace(/^ihre aufgaben[:\-]?\s*/i, "")
      .trim();
    return "Besonders angesprochen hat mich, dass bei dieser Ausbildung " + first.charAt(0).toLowerCase() + first.slice(1);
  }

  const fallback = {
    ausbildung_hotel: "Besonders passend finde ich die Verbindung aus Gästekontakt, Organisation und den internationalen Abläufen der Hotellerie.",
    ausbildung_gastro: "Besonders passend finde ich die Verbindung aus Service, Gästekontakt und einem professionell organisierten Arbeitsalltag.",
    ausbildung_retail: "Besonders passend finde ich die Verbindung aus Kundenberatung, Service und kaufmännischem Arbeiten.",
    ausbildung_logistics: "Besonders passend finde ich die Verbindung aus Koordination, Kundenkontakt und strukturierten internationalen Abläufen.",
    ausbildung_trade: "Besonders passend finde ich die Verbindung aus B2B-Kommunikation, kaufmännischer Arbeit und internationalem Geschäft.",
    ausbildung_industry: "Besonders passend finde ich die Verbindung aus kaufmännischen Prozessen, Organisation und einem strukturierten Unternehmensumfeld.",
    ausbildung_office: "Besonders passend finde ich die Verbindung aus Organisation, Kommunikation und kaufmännischen Aufgaben."
  };
  return fallback[family] || "Besonders angesprochen hat mich die Verbindung aus praktischer Verantwortung, Kundenorientierung und strukturiertem Arbeiten.";
}

function generateRemoteEmail_(job) {
  const company = companyMention_(job["Entreprise"]);
  const role = String(job["Intitulé"] || job["Role Cible"] || "the opportunity").trim();
  const access = String(job["acces_depuis_maroc"] || job["Access From Morocco"] || "").trim();

  const subject = company
    ? "Application – " + role + " – " + company + " | Halima Essaouaf"
    : "Application – " + role + " | Halima Essaouaf";

  const eligibility = /CONFIRMED_MOROCCO/i.test(access)
    ? "I am based in Casablanca, Morocco and the role indicates eligibility for Morocco."
    : "I am based in Casablanca, Morocco and am specifically interested in remote roles that can be performed from Morocco.";

  const body =
    "Dear Hiring Team" + (company ? " at " + company : "") + ",\n\n" +
    "I am reaching out regarding the " + role + " opportunity." + "\n\n" +
    "I bring 5+ years of experience across B2B account management, customer success, sales, customer support and business operations. At HBX Group (Hotelbeds / Bedsonline), I managed a portfolio of 600+ B2B travel-agency accounts across the Middle East, supporting onboarding, training, relationship management, retention and growth in Arabic, French and English." + "\n\n" +
    "My current experience also combines commercial administration, supplier communication, logistics/order coordination, e-commerce and customer operations. This gives me a practical profile at the intersection of client relationships, travel, commercial work and structured operations." + "\n\n" +
    eligibility + " I am particularly interested in international travel, hospitality and travel-tech teams where I can contribute in Customer Success, Account Management, Sales, Partnerships, Operations or Customer Experience." + "\n\n" +
    "I would be happy to discuss whether my profile could be relevant to your team. My CV is attached for your consideration." + "\n\n" +
    "Kind regards,\n" +
    CONFIG.NAME + "\n" +
    CONFIG.EMAIL + "\n" +
    CONFIG.PHONE;

  return {subject: subject, body: body};
}

function generateEmail_(job) {
  if (String(job["Search Type"] || "").toUpperCase() === "REMOTE_TRAVEL_HOSPITALITY") return generateRemoteEmail_(job);
  if (String(job["Search Type"] || "").toUpperCase() === "REMOTE_TRAVEL_HOSPITALITY") return generateRemoteEmail_(job);

  const company = companyMention_(job["Entreprise"]);
  const role = String(job["Intitulé"] || job["Role Cible"] || "Ausbildungsplatz").trim();
  const family = roleFamily_(job);
  const isAusbildung = /^ausbildung_/.test(family);
  const description = String(job["Description"] || "").trim();
  const fitReasons = String(job["Fit Reasons"] || "").trim();
  const location = String(job["Lieu"] || "").trim();

  if (isAusbildung) {
    const details = extractOfferDetails_(description, role);
    const companyIntro = company
      ? "Ihre Ausschreibung für den Ausbildungsplatz als " + role + " bei " + company + " hat mich angesprochen."
      : "Ihre Ausschreibung für den Ausbildungsplatz als " + role + " hat mich angesprochen.";

    let motivation = "";
    if (family === "ausbildung_hotel") {
      motivation = "Durch meine Erfahrung bei der HBX Group / Hotelbeds kenne ich die internationale Hotellerie, die Zusammenarbeit mit Hotelpartnern und den Umgang mit internationalen Kunden bereits aus der Praxis. Genau diese Erfahrung möchte ich jetzt mit einer fundierten Ausbildung im Hotelbereich weiterentwickeln.";
    } else if (family === "ausbildung_gastro") {
      motivation = "Meine Erfahrung in der internationalen Hotellerie hat mir gezeigt, wie wichtig Service, Organisation und ein sicherer Umgang mit unterschiedlichen Gästen sind. Diese Stärken möchte ich nun gezielt in einer Ausbildung im gastronomischen Bereich ausbauen.";
    } else if (family === "ausbildung_retail") {
      motivation = "Ich bringe praktische Erfahrung im Kundenkontakt, in der Beratung und in der Bearbeitung von Kundenanliegen mit. Eine Ausbildung im Einzelhandel ist für mich deshalb ein sehr konkreter Schritt, um diese Erfahrung mit einer anerkannten kaufmännischen Qualifikation zu verbinden.";
    } else if (family === "ausbildung_logistics") {
      motivation = "Aus meiner bisherigen Arbeit kenne ich Kundenbetreuung, Auftragskoordination und internationale Abläufe. Diese Erfahrung möchte ich nun systematisch durch eine Ausbildung im Speditions- und Logistikbereich vertiefen.";
    } else if (family === "ausbildung_trade") {
      motivation = "Mein bisheriger Weg verbindet internationale Kundenbetreuung, B2B-Kommunikation und kaufmännische Koordination. Deshalb passt eine Ausbildung im Handelsbereich sehr gut zu meiner bisherigen Erfahrung und meinem nächsten beruflichen Schritt.";
    } else if (family === "ausbildung_industry") {
      motivation = "Ich bringe eine kaufmännische Arbeitsweise, Erfahrung im Kundenmanagement und in der Koordination von Geschäftsvorgängen mit. Diese praktische Grundlage möchte ich in einer strukturierten kaufmännischen Ausbildung im industriellen Umfeld weiter ausbauen.";
    } else {
      motivation = "Ich bringe Erfahrung in Kundenbetreuung, B2B-Kommunikation, Administration und operativer Koordination mit. Eine Ausbildung ist für mich der bewusste nächste Schritt, um diese praktische Erfahrung mit einer soliden beruflichen Qualifikation in Deutschland zu verbinden.";
    }

    const context = (fitReasons + " " + description).toLowerCase();
    const proof = [];
    if (/hotel|hospitality|tourism|reise/.test(context)) proof.push("meine Erfahrung in der internationalen Hotellerie und Reisebranche");
    if (/b2b/.test(context)) proof.push("meine B2B-Erfahrung");
    if (/customer|kunden|service|gast/.test(context)) proof.push("meine Kunden- und Serviceorientierung");
    if (/logistik|spedition|liefer|transport|disposition/.test(context)) proof.push("meine Erfahrung mit Koordination und internationalen Abläufen");
    if (/kaufm|commercial|sales|handel|e-commerce/.test(context)) proof.push("mein kaufmännisches und vertriebliches Verständnis");

    const proofSentence = proof.length
      ? "Für die Aufgaben bringe ich insbesondere " + proof.slice(0, 2).join(" und ") + " mit."
      : "Ich bin überzeugt, dass meine praktische Erfahrung und meine schnelle Auffassungsgabe eine gute Grundlage für diese Ausbildung bilden.";

    const fitSentence = naturalFit_(family, details);
    const locationSentence = location
      ? "Auch der Ausbildungsstandort " + location + " kommt für mich sehr gut infrage."
      : "";

    const relocation = "Da ich mich derzeit aus Marokko bewerbe, organisiere ich Visum, Einreise, Unterlagen und meine persönliche Anreise selbstständig.";

    const body =
      "Sehr geehrte Damen und Herren,\n\n" +
      companyIntro + "\n\n" +
      motivation + "\n\n" +
      fitSentence + ". " + proofSentence + (locationSentence ? " " + locationSentence : "") + "\n\n" +
      relocation + " Für Sie entsteht dadurch kein zusätzlicher organisatorischer Aufwand.\n\n" +
      "Ich freue mich, mich persönlich vorzustellen und Sie von meiner Motivation zu überzeugen. Meine Unterlagen finden Sie im Anhang.\n\n" +
      "Mit freundlichen Grüßen\n" +
      CONFIG.NAME + "\n" +
      CONFIG.EMAIL + "\n" +
      CONFIG.PHONE;

    return {
      subject: "Bewerbung um einen Ausbildungsplatz als " + role + " – " + CONFIG.NAME,
      body: body
    };
  }

  const opening = company
    ? "I’m reaching out regarding the " + role + " opportunity at " + company + "."
    : "I’m reaching out regarding the " + role + " opportunity.";

  let proof = "";
  if (String(job["Spontaneous"] || "").toUpperCase() === "YES") {
    proof = "My background combines B2B account management, customer success, commercial administration, operations and international client support. At HBX Group, I managed 600+ B2B travel-agency accounts, while my more recent experience also includes logistics, e-commerce and administrative coordination.";
  } else if (family === "customer_success" || family === "travel") {
    proof = "At HBX Group (Hotelbeds / Bedsonline), I managed a portfolio of 600+ B2B travel-agency accounts, handling onboarding, training, customer support, retention and growth in Arabic, French and English.";
  } else if (family === "account") {
    proof = "At HBX Group (Hotelbeds / Bedsonline), I managed 600+ B2B travel-agency accounts, developed client relationships, supported onboarding and identified retention, upselling and growth opportunities across an international portfolio.";
  } else if (family === "partnerships") {
    proof = "My background combines B2B account management, digital acquisition, CRM, customer onboarding and commercial coordination. At HBX Group, I worked with 600+ B2B travel accounts and supported growth and activation across an international market.";
  } else if (family === "ecommerce") {
    proof = "Alongside my B2B and customer-success experience, I have hands-on experience with Shopify, e-commerce operations, Canva and Meta Ads, as well as customer support and commercial coordination.";
  } else {
    proof = "My background combines B2B/B2C customer management, commercial administration, logistics coordination, CRM and international client support. In my current role I coordinate suppliers, orders, deliveries and customer operations.";
  }

  const fit = company
    ? "The combination of international client management, CRM, multilingual communication and operational coordination is why I believe my background could be relevant to your team."
    : "The combination of international client management, CRM, multilingual communication and operational coordination is why I believe my background could be relevant to this position.";

  const body =
    "Dear Hiring Team,\n\n" +
    opening + "\n\n" +
    proof + "\n\n" +
    fit + "\n\n" +
    "I have attached my CV and would be happy to discuss the role and how I could contribute. I am particularly interested in international, customer-focused environments where I can combine relationship management with structured operations and digital tools.\n\n" +
    "Thank you for your time. I look forward to hearing from you.\n\n" +
    "Kind regards,\n" +
    CONFIG.NAME + "\n" +
    CONFIG.EMAIL + "\n" +
    CONFIG.PHONE;

  return {
    subject: "Application – " + role + " | " + CONFIG.NAME,
    body: body
  };
}

function sendOne_(sheet, rowNumber, headers, job, cvFile) {
  const email = firstEmail_(job["Emails RH"]);
  if (!email) return false;
  if (isBlockedRecipient_(email, job["Entreprise"])) {
    Logger.log("BLOCKED recipient — no email sent: " + email + " / " + job["Entreprise"]);
    return false;
  }

  const mail = generateEmail_(job);

  GmailApp.sendEmail(email, mail.subject, mail.body, {
    attachments: [cvFile.getAs(MimeType.PDF)],
    name: CONFIG.NAME,
    replyTo: CONFIG.EMAIL
  });

  const now = new Date();
  const sentCol = findColumn_(headers, "Date Sent");
  const statusCol = findColumn_(headers, "Status");
  const updatedCol = findColumn_(headers, "Last Updated");
  const followupCol = findColumn_(headers, "Date Followup");

  if (statusCol >= 0) sheet.getRange(rowNumber, statusCol + 1).setValue("SENT");
  if (sentCol >= 0) sheet.getRange(rowNumber, sentCol + 1).setValue(now);
  if (updatedCol >= 0) sheet.getRange(rowNumber, updatedCol + 1).setValue(now);

  if (followupCol >= 0) {
    const follow = new Date(now.getTime() + CONFIG.FOLLOWUP_DAYS * 24 * 60 * 60 * 1000);
    sheet.getRange(rowNumber, followupCol + 1).setValue(follow);
  }

  return true;
}

function sendPendingApplications() {
  if (!shouldSendNow_()) return;

  const cvFile = getCV_();
  if (!cvFile) {
    Logger.log("STOP: CV PDF not found in Google Drive: " + CONFIG.CV_FILE_NAME);
    return;
  }

  const remaining = MailApp.getRemainingDailyQuota();
  const alreadySent = sentTodayCount_();
  const dailyRemaining = Math.min(CONFIG.MAX_EMAILS_PER_DAY - alreadySent, remaining);
  const limit = Math.min(CONFIG.MAX_EMAILS_PER_RUN, dailyRemaining);

  if (limit <= 0) return;

  let sent = 0;

  for (const name of CONFIG.SHEETS) {
    if (sent >= limit) break;

    const sh = getSheet_(name);
    const data = sh.getDataRange().getValues();
    if (data.length < 2) continue;

    const headers = data[0];
    const statusCol = findColumn_(headers, "Status");
    const emailCol = findColumn_(headers, "Emails RH");

    if (statusCol < 0 || emailCol < 0) continue;

    for (let r = 1; r < data.length && sent < limit; r++) {
      const status = String(data[r][statusCol] || "").trim();
      if (status !== "NEW" && status !== "NOUVEAU") continue;

      const job = rowObject_(headers, data[r]);
      const recipient = firstEmail_(job["Emails RH"]);
      if (!recipient) continue;
      if (isBlockedRecipient_(recipient, job["Entreprise"])) {
        Logger.log("BLOCKED pending application — no email sent: " + recipient + " / " + job["Entreprise"]);
        continue;
      }

      try {
        if (sendOne_(sh, r + 1, headers, job, cvFile)) {
          sent++;
          Utilities.sleep(1200);
        }
      } catch (err) {
        Logger.log("Send error row " + (r + 1) + ": " + err);
      }
    }
  }

  Logger.log("Sent this run: " + sent);
}

function sendDueFollowups() {
  if (!shouldSendNow_()) return;

  const cvFile = getCV_();
  if (!cvFile) return;

  let sent = 0;
  const remaining = Math.min(CONFIG.MAX_EMAILS_PER_RUN, MailApp.getRemainingDailyQuota());

  for (const name of CONFIG.SHEETS) {
    if (sent >= remaining) break;

    const sh = getSheet_(name);
    const data = sh.getDataRange().getValues();
    if (data.length < 2) continue;

    const headers = data[0];
    const statusCol = findColumn_(headers, "Status");
    const emailCol = findColumn_(headers, "Emails RH");
    const followupCol = findColumn_(headers, "Date Followup");

    if (statusCol < 0 || emailCol < 0 || followupCol < 0) continue;

    for (let r = 1; r < data.length && sent < remaining; r++) {
      const status = String(data[r][statusCol] || "").trim();
      const due = data[r][followupCol];

      if (status !== "SENT" || !due || new Date(due) > new Date()) continue;

      const job = rowObject_(headers, data[r]);
      const email = firstEmail_(job["Emails RH"]);
      if (!email) continue;
      if (isBlockedRecipient_(email, job["Entreprise"])) {
        Logger.log("BLOCKED follow-up — no email sent: " + email + " / " + job["Entreprise"]);
        continue;
      }

      const role = String(job["Intitulé"] || job["Role Cible"] || "the position");
      const company = companyMention_(job["Entreprise"]);
      const greeting = company ? "Dear Hiring Team at " + company + "," : "Dear Hiring Team,";

      const body =
        greeting + "\n\n" +
        "I wanted to briefly follow up on my application for the " + role + ". " +
        "I remain very interested in the opportunity and would be glad to provide any additional information you may need." + "\n\n" +
        "Thank you again for your time." + "\n\n" +
        "Kind regards,\n" + CONFIG.NAME + "\n" + CONFIG.EMAIL + "\n" + CONFIG.PHONE;

      try {
        GmailApp.sendEmail(
          email,
          "Following up – " + role + " | " + CONFIG.NAME,
          body,
          {
            attachments: [cvFile.getAs(MimeType.PDF)],
            name: CONFIG.NAME,
            replyTo: CONFIG.EMAIL
          }
        );

        sh.getRange(r + 1, statusCol + 1).setValue("FOLLOWUP_SENT");
        sent++;
        Utilities.sleep(1200);
      } catch (err) {
        Logger.log("Follow-up error: " + err);
      }
    }
  }
}

function doPost(e) {
  try {
    const payload = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    const mode = payload.mode || "jobs";

    if (mode === "jobs") {
      const sheet = getSheet_(payload.sheet || "Worldwide Remote");
      const jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
      const data = sheet.getDataRange().getValues();
      const headers = data[0] || CONFIG.HEADERS;
      const idCol = findColumn_(headers, "ID");
      const existing = new Set();

      for (let r = 1; r < data.length; r++) {
        const id = String(data[r][idCol] || "").trim();
        if (id) existing.add(id);
      }

      let added = 0;

      for (const j of jobs) {
        const id = String(j.id || "").trim();
        const email = firstEmail_(j.emails_rh);

        // Keep the offer even when exhaustive enrichment found no public email.
        // Email is preferred, never fabricated, but it is no longer a condition for storing the job.
        if (!id || existing.has(id)) continue;

        const obj = {
          "Date Detection": j.date_detection || new Date().toISOString(),
          "Status": "NEW",
          "Role Cible": j.role_cible || "",
          "Intitulé": j.intitule || "",
          "Entreprise": j.entreprise || "",
          "Lieu": j.lieu || "",
          "Remote": j.remote ? "YES" : "NO",
          "Source": j.source || "",
          "Lien": j.lien || "",
          "ID": id,
          "Company Site": j.company_site || "",
          "Emails RH": j.emails_rh || "",
          "Deep Status": j.deep_status || "DONE",
          "Fit Score": j.fit_score || "",
          "Fit Reasons": j.fit_reasons || "",
          "Salary": j.salary || "",
          "Description": j.description || "",
          "Last Updated": new Date(),
          "Posted Age": j.posted_age || "",
          "Posted <=24h": j.posted_within_24h || "",
          "Search Type": j.search_type || "",
          "Email Status": email ? "FOUND" : (j.email_status || "NO_EMAIL"),
          "Email Source": j.email_source || "",
          "Spontaneous": j.spontaneous || "NO",
          "Date Sent": "",
          "Date Followup": ""
        };

        const row = CONFIG.HEADERS.map(h => obj[h] === undefined ? "" : obj[h]);
        sheet.getRange(sheet.getLastRow() + 1, 1, 1, CONFIG.HEADERS.length).setValues([row]);

        existing.add(id);
        added++;
      }

      return json_({status:"success", added:added, received:jobs.length});
    }

    if (mode === "enrich") {
      const updates = Array.isArray(payload.updates) ? payload.updates : [];
      let updated = 0;

      // Update existing rows by stable job ID. This is intentionally separate
      // from mode="jobs": discovery creates rows first, enrichment fills in
      // verified company/email data afterwards.
      for (const u of updates) {
        const id = String(u.id || "").trim();
        if (!id) continue;

        const sheetNames = u.sheet && CONFIG.SHEETS.indexOf(u.sheet) >= 0
          ? [u.sheet]
          : CONFIG.SHEETS;

        let found = false;
        for (const name of sheetNames) {
          if (found) break;
          const sh = getSheet_(name);
          const data = sh.getDataRange().getValues();
          if (data.length < 2) continue;

          const headers = data[0];
          const idCol = findColumn_(headers, "ID");
          if (idCol < 0) continue;

          for (let r = 1; r < data.length; r++) {
            if (String(data[r][idCol] || "").trim() !== id) continue;

            const values = {
              "Company Site": u.company_site || "",
              "Emails RH": u.emails_rh || "",
              "Deep Status": u.deep_status || "",
              "Email Status": u.email_status || "",
              "Email Source": u.email_source || "",
              "Salary": u.salary || "",
              "Fit Score": u.fit_score || "",
              "Fit Reasons": u.fit_reasons || "",
              "Last Updated": new Date()
            };

            for (const key in values) {
              const col = findColumn_(headers, key);
              if (col >= 0) sh.getRange(r + 1, col + 1).setValue(values[key]);
            }

            updated++;
            found = true;
            break;
          }
        }
      }

      return json_({status:"success", updated:updated, received:updates.length});
    }

    if (mode === "pending") {
      const jobs = [];
      const names = payload.sheet && payload.sheet !== "ALL" ? [payload.sheet] : CONFIG.SHEETS;

      for (const name of names) {
        const sh = getSheet_(name);
        const data = sh.getDataRange().getValues();
        if (data.length < 2) continue;

        const headers = data[0];
        const statusCol = findColumn_(headers, "Status");

        for (let r = 1; r < data.length; r++) {
          if (statusCol >= 0 && String(data[r][statusCol] || "") === "NEW") {
            jobs.push(rowObject_(headers, data[r]));
          }
        }
      }

      return json_({status:"success", jobs:jobs.slice(0, Number(payload.limit || 5000))});
    }

    if (mode === "log") {
      PropertiesService.getScriptProperties().setProperty("LAST_RUN", JSON.stringify(payload.run || {}));
      return json_({status:"success"});
    }

    return json_({status:"error",message:"Unknown mode"});
  } catch (err) {
    return json_({status:"error",message:String(err)});
  }
}

function json_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function testEmailGeneration() {
  const sample = {
    "Intitulé":"Customer Success Manager",
    "Role Cible":"Customer Success",
    "Entreprise":"Example Travel Tech",
    "Emails RH":"careers@example.com"
  };
  Logger.log(generateEmail_(sample).body);
}
