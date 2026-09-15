/**
 * ==============================================================================
 * GERER CANDIDATURES AUSBILDUNG KAUFMANN / KAUFFRAU
 * Script Google Apps Script complet pour Google Sheets & Gmail
 * ==============================================================================
 */

// 1. MAPPING STRICT DES CV PDF (Stockés à la racine de ton Google Drive)
const CV_MAPPING = {
  "Büromanagement": "Bewerbung Kauffrau Buromanagemenet Halima Essaouaf.pdf",
  "E-Commerce": "Bewerbung Kauffrau ECommerce Halima Essaouaf.pdf",
  "Groß- und Außenhandel": "Bewerbung Kauffrau GrossAussenhandel Halima Essaouaf.pdf",
  "Spedition & Logistik": "Bewerbung Kauffrau Spedition Logistik Halima Essaouaf.pdf",
  "Tourismus": "Bewerbung Kauffrau Tourismus Freizeit Halima Essaouaf_2.pdf"
};

// 2. REGLES ET QUOTAS D'ENVOI
const BATCH_LIMIT = 25; // 25 envois max par heure (Protection anti-spam Gmail)

/**
 * WEBHOOK POST : Écouteur qui reçoit les offres directement depuis le Scraper Python
 */
function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const jobs = Array.isArray(data) ? data : [data];
    
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
    const existingData = sheet.getDataRange().getValues();
    
    // Set des IDs existants pour éviter tout doublon (Colonne J / Index 9)
    const existingIds = new Set();
    for (let i = 1; i < existingData.length; i++) {
      if (existingData[i][9]) {
        existingIds.add(String(existingData[i][9]).trim());
      }
    }

    let rajoutes = 0;
    jobs.forEach(job => {
      if (job.id && !existingIds.has(String(job.id).trim())) {
        sheet.appendRow([
          job.date_detection || new Date().toISOString(),
          job.statut || "NOUVEAU",
          job.role_cible || "",
          job.intitule || "",
          job.entreprise || "",
          job.lieu || "Deutschland (Allemagne)",
          job.emails_rh || "Non détecté (Postuler via lien)",
          job.source || "Scraper Cloud",
          job.lien || "",
          job.id || ""
        ]);
        existingIds.add(String(job.id).trim());
        rajoutes++;
      }
    });

    return ContentService.createTextOutput(JSON.stringify({
      status: "success",
      added: rajoutes
    })).setMimeType(ContentService.MimeType.JSON);

  } catch (error) {
    return ContentService.createTextOutput(JSON.stringify({
      status: "error",
      message: error.toString()
    })).setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * Fonction Principale : Appelée automatiquement par le déclencheur temporel chaque heure
 */
function traiterCandidaturesEtRelances() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  const data = sheet.getDataRange().getValues();
  
  if (data.length <= 1) {
    Logger.log("Feuille vide ou seule la ligne d'en-tête est présente.");
    return;
  }

  let compteurEnvois = 0;
  const now = new Date();

  for (let i = 1; i < data.length; i++) {
    if (compteurEnvois >= BATCH_LIMIT) {
      Logger.log("Quota d'envoi par lot (25/heure) atteint.");
      break;
    }

    const row = data[i];
    const statut = String(row[1] || "").trim();
    const roleCible = String(row[2] || "").trim();
    const intitule = String(row[3] || "").trim();
    const entreprise = String(row[4] || "").trim() || "Sehr geehrte Damen und Herren";
    const emailsRh = String(row[6] || "").trim();
    const dateEnvoi = row[10] ? new Date(row[10]) : null;

    if (!emailsRh || emailsRh.toLowerCase().includes("non détecté") || !emailsRh.includes("@")) {
      continue;
    }

    let emailCible = emailsRh.split("/")[0].trim();
    emailCible = emailCible.replace(/^[u003e\\'"]+/, "");

    if (statut === "NOUVEAU") {
      const cvFile = recupererCV(intitule, roleCible);
      
      if (!cvFile) {
        Logger.log(`⚠️ CV introuvable pour l'offre: ${intitule}`);
        continue;
      }

      const titrePoste = definirTitreAusbildung(intitule, roleCible);
      const sujet = `Bewerbung um einen Ausbildungsplatz als ${titrePoste} - Halima Essaouaf`;
      const corps = genererCorpsCandidature(entreprise, titrePoste);

      try {
        GmailApp.sendEmail(emailCible, sujet, corps, {
          attachments: [cvFile.getAs(MimeType.PDF)],
          name: "Halima Essaouaf"
        });

        sheet.getRange(i + 1, 2).setValue("CANDIDATURE_ENVOYEE");
        sheet.getRange(i + 1, 11).setValue(new Date());
        compteurEnvois++;
        Logger.log(`✅ CANDIDATURE ENVOYÉE -> Entreprise: ${entreprise} | Email: ${emailCible}`);
      } catch (err) {
        Logger.log(`❌ ERREUR Envoi Candidature (${emailCible}): ` + err.toString());
      }
    }
    else if (statut === "CANDIDATURE_ENVOYEE" && dateEnvoi) {
      const diffHeures = (now - dateEnvoi) / (1000 * 60 * 60);

      if (diffHeures >= 48) {
        const cvFile = recupererCV(intitule, roleCible);
        const titrePoste = definirTitreAusbildung(intitule, roleCible);
        const sujet = `Nachfassaktion / Status meiner Bewerbung als ${titrePoste} - Halima Essaouaf`;
        const corps = genererCorpsRelance(entreprise, titrePoste);

        try {
          GmailApp.sendEmail(emailCible, sujet, corps, {
            attachments: cvFile ? [cvFile.getAs(MimeType.PDF)] : [],
            name: "Halima Essaouaf"
          });

          sheet.getRange(i + 1, 2).setValue("RELANCE_EFFECTUEE");
          sheet.getRange(i + 1, 12).setValue(new Date());
          compteurEnvois++;
          Logger.log(`🔄 RELANCE EFFECTUÉE (48H) -> Entreprise: ${entreprise} | Email: ${emailCible}`);
        } catch (err) {
          Logger.log(`❌ ERREUR Relance (${emailCible}): ` + err.toString());
        }
      }
    }
  }
}

function recupererCV(intitule, roleCible) {
  const text = (intitule + " " + roleCible).toLowerCase();
  let filename = CV_MAPPING["Büromanagement"];

  if (text.includes("e-commerce") || text.includes("ecommerce")) {
    filename = CV_MAPPING["E-Commerce"];
  } else if (text.includes("groß") || text.includes("gross") || text.includes("außenhandel") || text.includes("aussenhandel")) {
    filename = CV_MAPPING["Groß- und Außenhandel"];
  } else if (text.includes("spedition") || text.includes("logistik")) {
    filename = CV_MAPPING["Spedition & Logistik"];
  } else if (text.includes("tourismus") || text.includes("freizeit")) {
    filename = CV_MAPPING["Tourismus"];
  }

  let files = DriveApp.getFilesByName(filename);
  if (files.hasNext()) {
    return files.next();
  }
  
  const fallbackFiles = DriveApp.getFilesByName(CV_MAPPING["Büromanagement"]);
  if (fallbackFiles.hasNext()) {
    return fallbackFiles.next();
  }
  return null;
}

function definirTitreAusbildung(intitule, roleCible) {
  const text = (intitule + " " + roleCible).toLowerCase();
  if (text.includes("e-commerce")) return "Kauffrau im E-Commerce";
  if (text.includes("groß") || text.includes("gross") || text.includes("außenhandel")) return "Kauffrau im Groß- und Außenhandelsmanagement";
  if (text.includes("spedition") || text.includes("logistik")) return "Kauffrau für Spedition und Logistikdienstleistung";
  if (text.includes("tourismus")) return "Kauffrau für Tourismus und Freizeit";
  return "Kaufmann/-frau für Büromanagement";
}

function genererCorpsCandidature(entreprise, titrePoste) {
  let salutation = "Sehr geehrte Damen und Herren,";
  if (entreprise && entreprise !== "Unternehmen Deutschland" && entreprise !== "Sehr geehrte Damen und Herren") {
    salutation = `Sehr geehrte Damen und Herren der ${entreprise},`;
  }

  return `${salutation}

mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als ${titrePoste}.

Ich zeichne mich durch Organisationsfähigkeit, Engagement und eine sehr hohe Lernbereitschaft aus. Gerne möchte ich meine Kenntnisse und mein Wissen aktiv in Ihr Team einbringen und mich in Ihrem Unternehmen beruflich weiterentwickeln.

In der Anlage finden Sie meine vollständigen Bewerbungsunterlagen (CV) als PDF-Datei.

Über die Gelegenheit zu einem persönlichen Vorstellungsgespräch oder einem Online-Interview freue ich mich sehr.

Mit freundlichen Grüßen

Halima Essaouaf
E-Mail: essaouafhalima@gmail.com`;
}

function genererCorpsRelance(entreprise, titrePoste) {
  let salutation = "Sehr geehrte Damen und Herren,";
  if (entreprise && entreprise !== "Unternehmen Deutschland" && entreprise !== "Sehr geehrte Damen und Herren") {
    salutation = `Sehr geehrte Damen und Herren der ${entreprise},`;
  }

  return `${salutation}

vor zwei Tagen habe ich Ihnen meine Bewerbungsunterlagen für den Ausbildungsplatz als ${titrePoste} zukommen lassen.

Da ich nach wie vor sehr an einer Ausbildung in Ihrem Hause interessiert bin, möchte ich mich kurz erkundigen, ob meine Unterlagen gut bei Ihnen eingegangen sind.

Zur Sicherheit habe ich meinen Lebenslauf dieser E-Mail nochmals als PDF-Datei beigefügt.

Ich freue mich sehr über eine kurze Rückmeldung Ihrerseits.

Mit freundlichen Grüßen

Halima Essaouaf
E-Mail: essaouafhalima@gmail.com`;
}
